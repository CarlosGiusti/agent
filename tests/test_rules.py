from pathlib import Path

import pytest

from lib.rules.rules import resolve_rules, rule_messages


def _write(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


def test_resolve_rules_puts_global_before_workspace_sorted_by_name(tmp_path: Path):
    global_dir = tmp_path / "home"
    workspace_dir = tmp_path / "project" / ".agent"
    _write(global_dir / "rules", "b.md", "global-b\n")
    _write(global_dir / "rules", "a.md", "global-a\n")
    _write(workspace_dir / "rules", "c.md", "local-c\n")
    _write(workspace_dir / "rules", "a.md", "local-a\n")
    _write(global_dir, "skip.md", "not-a-rule\n")

    rules = resolve_rules(global_dir, workspace_dir)

    assert [rule.role for rule in rules] == ["rule", "rule", "rule", "rule"]
    assert [rule.content for rule in rules] == [
        "global-a\n",
        "global-b\n",
        "local-a\n",
        "local-c\n",
    ]


def test_resolve_rules_keeps_same_name_in_both_layers(tmp_path: Path):
    global_dir = tmp_path / "home"
    workspace_dir = tmp_path / "project"
    _write(global_dir / "rules", "style.md", "from-home\n")
    _write(workspace_dir / "rules", "style.md", "from-project\n")

    rules = resolve_rules(global_dir, workspace_dir)

    assert [rule.content for rule in rules] == ["from-home\n", "from-project\n"]


def test_resolve_rules_skips_missing_dirs_and_non_markdown(tmp_path: Path):
    workspace_dir = tmp_path / "project"
    _write(workspace_dir, "notes.txt", "ignore\n")
    nested = workspace_dir / "nested"
    _write(nested, "deep.md", "ignore\n")

    rules = resolve_rules(tmp_path / "missing", workspace_dir)

    assert rules == []


def test_rule_messages_reads_root_then_workspace_agent_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    root = tmp_path / "home"
    workspace = tmp_path / "project"
    _write(root / "rules", "a.md", "global-a\n")
    _write(workspace / ".agent" / "rules", "b.md", "local-b\n")

    messages = rule_messages(workspace, root)
    printed = capsys.readouterr().out

    assert [message.role for message in messages] == ["rule", "rule"]
    assert [message.content for message in messages] == ["global-a\n", "local-b\n"]
    assert "rules:" in printed
    assert str(root / "rules" / "a.md") in printed
