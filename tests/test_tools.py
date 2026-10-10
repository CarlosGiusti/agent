from pathlib import Path

from lib.authorization.authorization import Authorization
from lib.authorization.protocol import AuthorizationProtocol, DecisionType
from lib.sandbox.sandbox import SandboxResult
from lib.tools.file_io import make_read_file, make_write_file
from lib.tools.load_skill import make_load_skill
from lib.tools.shell import Shell
from lib.tools.tool_registry import ToolRegistry, make_local_tools


class UnusedSandbox:
    def run(self, command: str, *, elevated: bool) -> SandboxResult:
        raise AssertionError("sandbox should not run")


class FailProtocol(AuthorizationProtocol):
    async def check(self, action: str, args: dict) -> DecisionType:
        raise AssertionError(f"should not prompt for {action}")


def test_registry_includes_local_tools(tmp_path: Path):
    shell = Shell(
        sandbox=UnusedSandbox(),
        authorization=Authorization(FailProtocol()),
        workspace=tmp_path,
    )
    registry = ToolRegistry(make_local_tools(tmp_path, shell))
    names = {tool.name for tool in registry.tools}

    assert names == {"read_file", "write_file", "run_command"}


def test_registry_appends_extra_tools(tmp_path: Path):
    shell = Shell(
        sandbox=UnusedSandbox(),
        authorization=Authorization(FailProtocol()),
        workspace=tmp_path,
    )
    extra = make_load_skill({"alpha": "BODY\n"})
    registry = ToolRegistry(make_local_tools(tmp_path, shell, extra))
    names = {tool.name for tool in registry.tools}

    assert names == {"read_file", "write_file", "run_command", "load_skill"}


async def test_read_and_write_stay_inside_workspace(tmp_path: Path):
    read = make_read_file(tmp_path)
    write = make_write_file(tmp_path)

    written = await write.call(path="a.txt", content="hi")
    text = await read.call(path="a.txt")

    assert "Wrote" in written
    assert text == "hi"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hi"


async def test_read_file_rejects_path_outside_workspace(tmp_path: Path):
    read = make_read_file(tmp_path)

    result = await read.call(path="../secret.txt")

    assert "outside the workspace" in result


async def test_write_file_rejects_path_outside_workspace(tmp_path: Path):
    write = make_write_file(tmp_path)

    result = await write.call(path="../secret.txt", content="no")

    assert "outside the workspace" in result
    assert not (tmp_path.parent / "secret.txt").exists()
