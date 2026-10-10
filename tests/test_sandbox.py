from pathlib import Path

from lib.sandbox.sandbox import SandboxResult, classify_constraint, outside_paths


def test_classify_success_is_not_a_constraint():
    result = SandboxResult(exit_code=0, stdout="ok", stderr="")

    assert classify_constraint(result, "curl https://example.com", Path("/tmp/ws")) == []


def test_classify_network_unreachable():
    result = SandboxResult(
        exit_code=7,
        stdout="",
        stderr="curl: (7) Failed to connect to example.com: Network is unreachable",
    )

    assert classify_constraint(result, "curl https://example.com", Path("/tmp/ws")) == [
        "network"
    ]


def test_classify_filesystem_when_command_names_a_path_outside_workspace(
    tmp_path: Path,
):
    result = SandboxResult(
        exit_code=1,
        stdout="",
        stderr="cat: /home/me/.ssh/id_rsa: No such file or directory",
    )

    reasons = classify_constraint(result, "cat /home/me/.ssh/id_rsa", tmp_path)

    assert reasons[0].startswith("filesystem:")
    assert "/home/me/.ssh/id_rsa" in reasons[0]


def test_classify_does_not_elevate_grep_miss_inside_workspace(tmp_path: Path):
    result = SandboxResult(exit_code=1, stdout="", stderr="")

    assert classify_constraint(result, "grep nowhere src/app.py", tmp_path) == []


def test_outside_paths_ignores_urls_and_workspace_files(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x", encoding="utf-8")

    found = outside_paths(
        "curl https://pypi.org/simple cat src/app.py /etc/passwd ~/.ssh/id_rsa",
        tmp_path,
    )

    assert any(item.endswith(".ssh/id_rsa") for item in found)
    assert not any("app.py" in item for item in found)
    assert not any(item.startswith("/etc/") for item in found)
