from pathlib import Path

from lib.authorization.authorization import Authorization
from lib.authorization.protocol import AuthorizationProtocol, DecisionType
from lib.events.event_system import Event, EventType
from lib.sandbox.sandbox import SandboxResult
from lib.tools.shell import Shell


class ScriptedSandbox:
    def __init__(self, results: list[SandboxResult]) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, bool]] = []

    def run(self, command: str, *, elevated: bool) -> SandboxResult:
        self.calls.append((command, elevated))
        if not self._results:
            raise AssertionError("Sandbox.run called more times than expected")
        return self._results.pop(0)


class ScriptedProtocol(AuthorizationProtocol):
    def __init__(self, decisions: list[DecisionType]) -> None:
        self._decisions = list(decisions)
        self.calls: list[tuple[str, dict]] = []

    async def check(self, action: str, args: dict) -> DecisionType:
        self.calls.append((action, args))
        if not self._decisions:
            raise AssertionError(f"unexpected prompt for {action}")
        return self._decisions.pop(0)


class FailProtocol(AuthorizationProtocol):
    async def check(self, action: str, args: dict) -> DecisionType:
        raise AssertionError(f"should not prompt for {action}")


class RecordingEvents:
    def __init__(self) -> None:
        self.emits: list[Event] = []

    def emit(
        self,
        event_type: str,
        data: dict | None = None,
        *,
        ok: bool = True,
        error: str | None = None,
    ) -> None:
        self.emits.append(
            Event(event_type=event_type, data=data or {}, ok=ok, error=error)
        )


def _shell(
    sandbox: ScriptedSandbox,
    protocol: AuthorizationProtocol,
    workspace: Path,
    events: RecordingEvents | None = None,
) -> Shell:
    return Shell(
        sandbox=sandbox,
        authorization=Authorization(protocol),
        workspace=workspace,
        events=events,
    )


async def test_run_returns_boxed_output_without_prompt_when_command_succeeds(
    tmp_path: Path,
):
    sandbox = ScriptedSandbox(
        [SandboxResult(exit_code=0, stdout="ok\n", stderr="")]
    )
    protocol = FailProtocol()
    shell = _shell(sandbox, protocol, tmp_path)

    result = await shell.run("ls")

    assert result == "exit_code: 0\nstdout:\nok\n"
    assert sandbox.calls == [("ls", False)]


async def test_run_retries_elevated_after_network_block_when_user_allows(
    tmp_path: Path,
):
    sandbox = ScriptedSandbox(
        [
            SandboxResult(
                exit_code=7,
                stdout="",
                stderr="Network is unreachable",
            ),
            SandboxResult(exit_code=0, stdout="fetched\n", stderr=""),
        ]
    )
    protocol = ScriptedProtocol([DecisionType.ALLOW_ONCE])
    shell = _shell(sandbox, protocol, tmp_path)

    result = await shell.run("curl https://example.com")

    assert result.startswith("elevated (network)\n")
    assert "fetched" in result
    assert sandbox.calls == [
        ("curl https://example.com", False),
        ("curl https://example.com", True),
    ]
    assert protocol.calls == [
        (
            "elevate",
            {
                "command": "curl https://example.com",
                "reasons": ["network"],
            },
        )
    ]


async def test_run_does_not_retry_when_user_denies_elevation(
    tmp_path: Path,
):
    sandbox = ScriptedSandbox(
        [
            SandboxResult(
                exit_code=7,
                stdout="",
                stderr="Network is unreachable",
            )
        ]
    )
    protocol = ScriptedProtocol([DecisionType.DENY])
    events = RecordingEvents()
    shell = _shell(sandbox, protocol, tmp_path, events)

    result = await shell.run("npm install")

    assert "Permission denied by user" in result
    assert sandbox.calls == [("npm install", False)]
    assert EventType.PERMISSION_DENIED in [event.event_type for event in events.emits]


async def test_run_retries_elevated_after_filesystem_block_when_user_allows(
    tmp_path: Path,
):
    sandbox = ScriptedSandbox(
        [
            SandboxResult(
                exit_code=1,
                stdout="",
                stderr="cat: /home/me/.bashrc: No such file or directory",
            ),
            SandboxResult(exit_code=0, stdout="alias ll=ls\n", stderr=""),
        ]
    )
    protocol = ScriptedProtocol([DecisionType.ALLOW_ONCE])
    shell = _shell(sandbox, protocol, tmp_path)

    result = await shell.run("cat /home/me/.bashrc")

    assert result.startswith("elevated (filesystem:")
    assert sandbox.calls == [
        ("cat /home/me/.bashrc", False),
        ("cat /home/me/.bashrc", True),
    ]


async def test_run_does_not_prompt_for_ordinary_nonzero_exit(
    tmp_path: Path,
):
    sandbox = ScriptedSandbox(
        [SandboxResult(exit_code=1, stdout="", stderr="FAILED tests/test_x.py")]
    )
    protocol = FailProtocol()
    shell = _shell(sandbox, protocol, tmp_path)

    result = await shell.run("pytest")

    assert "exit_code: 1" in result
    assert sandbox.calls == [("pytest", False)]


async def test_run_rejects_empty_command(tmp_path: Path):
    sandbox = ScriptedSandbox([])
    protocol = FailProtocol()
    shell = _shell(sandbox, protocol, tmp_path)

    assert await shell.run("   ") == "Error: command is empty"
    assert sandbox.calls == []
