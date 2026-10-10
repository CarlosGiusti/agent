from pathlib import Path

import pytest

from lib.agents.ollama import OllamaProvider
from lib.agents.types import Message
from lib.skills.skills import resolve_skills, skill_message, skill_messages
from lib.tools.load_skill import make_load_skill


def _write(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(body, encoding="utf-8")


def _skill(name: str, description: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}"


def test_resolve_skills_lists_root_then_workspace_and_workspace_overwrites(
    tmp_path: Path,
):
    root = tmp_path / "home"
    workspace = tmp_path / "project" / ".agent"
    _write(root / "skills", "b.md", _skill("beta", "from-root", "ROOT_BETA\n"))
    _write(root / "skills", "a.md", _skill("alpha", "from-root", "ROOT_ALPHA\n"))
    _write(workspace / "skills", "z.md", _skill("alpha", "from-workspace", "WORK_ALPHA\n"))
    _write(workspace / "skills", "m.md", _skill("gamma", "from-workspace", "WORK_GAMMA\n"))
    _write(root, "skip.md", _skill("skip", "no", "NO\n"))

    catalog = resolve_skills(root, workspace)
    message = skill_message(catalog)

    assert list(catalog.index) == ["alpha", "beta", "gamma"]
    assert catalog.index["alpha"] == "from-workspace"
    assert catalog.bodies["alpha"] == "WORK_ALPHA\n"
    assert catalog.bodies["beta"] == "ROOT_BETA\n"
    assert message is not None
    assert message.role == "rule"
    assert message.content == (
        "skills:\n"
        "alpha: from-workspace\n"
        "beta: from-root\n"
        "gamma: from-workspace"
    )
    assert "WORK_ALPHA" not in message.content


def test_resolve_skills_later_file_in_the_same_layer_replaces_the_name(tmp_path: Path):
    root = tmp_path / "home"
    _write(root / "skills", "a.md", _skill("alpha", "first", "FIRST\n"))
    _write(root / "skills", "b.md", _skill("alpha", "second", "SECOND\n"))

    catalog = resolve_skills(root, tmp_path / "missing")

    assert catalog.index == {"alpha": "second"}
    assert catalog.bodies == {"alpha": "SECOND\n"}


def test_resolve_skills_skips_missing_dirs_non_markdown_and_incomplete_frontmatter(
    tmp_path: Path,
):
    workspace = tmp_path / "project"
    _write(workspace / "skills", "notes.txt", "ignore\n")
    _write(workspace / "skills" / "nested", "deep.md", _skill("deep", "no", "NO\n"))
    _write(workspace / "skills", "plain.md", "no frontmatter\n")
    _write(workspace / "skills", "noname.md", "---\ndescription: only\n---\nbody\n")

    catalog = resolve_skills(tmp_path / "missing", workspace)

    assert catalog.index == {}
    assert catalog.bodies == {}
    assert skill_message(catalog) is None


def test_skill_message_folds_into_the_system_with_rules(tmp_path: Path):
    root = tmp_path / "home"
    _write(root / "skills", "a.md", _skill("alpha", "from-root", "BODY\n"))
    catalog = resolve_skills(root, tmp_path / "missing")
    message = skill_message(catalog)
    assert message is not None

    messages = OllamaProvider(model="qwen3:8b").to_provider_messages(
        [
            Message(role="rule", content="GLOBAL_OK"),
            message,
            Message(role="user", content="hi"),
        ]
    )

    assert [item["role"] for item in messages] == ["system", "user"]
    assert messages[0]["content"] == "GLOBAL_OK\n\nskills:\nalpha: from-root"
    assert "BODY" not in messages[0]["content"]


async def test_load_skill_returns_the_winning_body_and_unknown_name_is_an_error(
    tmp_path: Path,
):
    root = tmp_path / "home"
    workspace = tmp_path / "project"
    _write(root / "skills", "a.md", _skill("alpha", "from-root", "ROOT_ALPHA\n"))
    _write(workspace / "skills", "a.md", _skill("alpha", "from-workspace", "WORK_ALPHA\n"))
    catalog = resolve_skills(root, workspace)
    tool = make_load_skill(catalog.bodies)

    assert await tool.call(name="alpha") == "WORK_ALPHA\n"
    missing = await tool.call(name="nope")
    assert missing == "Error: unknown skill: nope"


async def test_skill_messages_returns_the_index_and_a_tool_for_the_winning_body(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    root = tmp_path / "home"
    workspace = tmp_path / "project"
    _write(root / "skills", "a.md", _skill("alpha", "from-root", "ROOT\n"))
    _write(
        workspace / ".agent" / "skills",
        "b.md",
        _skill("beta", "from-workspace", "WORK\n"),
    )

    messages, tool = skill_messages(workspace, root)
    printed = capsys.readouterr().out

    assert [message.role for message in messages] == ["rule"]
    assert messages[0].content == "skills:\nalpha: from-root\nbeta: from-workspace"
    assert "WORK" not in messages[0].content
    assert await tool.call(name="beta") == "WORK\n"
    assert "skills: alpha, beta" in printed
