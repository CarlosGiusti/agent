import asyncio
import json
from pathlib import Path

import pytest

from lib.agents.types import Message, ToolCall
from lib.events.event_system import Event, EventType
from lib.hooks.hooks import ClosedTurn, Hook, HookOutput, open_hooks


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


class RecordingProcess:
    def __init__(self, outputs: list[HookOutput]) -> None:
        self._outputs = list(outputs)
        self.calls: list[tuple[Path, str, Path]] = []

    async def run(self, script: Path, stdin: str, *, cwd: Path) -> HookOutput:
        self.calls.append((script, stdin, cwd))
        if not self._outputs:
            raise AssertionError(f"unexpected hook process for {script}")
        output = self._outputs.pop(0)
        await asyncio.sleep(0)
        return output


def _script(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    return path.resolve()


def _write_hooks(directory: Path, hooks: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "hooks.json").write_text(
        json.dumps({"version": 1, "hooks": hooks}),
        encoding="utf-8",
    )


def _hooks(
    tmp_path: Path,
    outputs: list[HookOutput],
) -> tuple[Hook, RecordingProcess, RecordingEvents, Path]:
    root = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    process = RecordingProcess(outputs)
    events = RecordingEvents()
    return Hook(root, workspace, process, events), process, events, workspace


def _ok(stdout: str) -> HookOutput:
    return HookOutput(exit_code=0, stdout=stdout, stderr="")


async def test_before_turn_runs_global_then_workspace_and_joins_stdout(tmp_path: Path):
    hooks, process, events, workspace = _hooks(
        tmp_path, [_ok("FROM_GLOBAL\n"), _ok("FROM_PROJECT\n")]
    )
    root = tmp_path / "home"
    global_script = _script(root / "prepend.sh")
    local_script = _script(workspace / ".agent" / "hooks" / "status.sh")
    _script(root / "notes.sh")
    _write_hooks(root, {"before_turn": [{"command": str(global_script)}]})
    _write_hooks(
        workspace / ".agent",
        {"before_turn": [{"command": ".agent/hooks/status.sh"}]},
    )

    text = await hooks.before_turn("hola")

    assert text == "FROM_GLOBAL\nFROM_PROJECT"
    assert [call[0] for call in process.calls] == [global_script, local_script]
    assert json.loads(process.calls[0][1]) == {"prompt": "hola"}
    assert process.calls[0][2] == workspace.resolve()
    assert [event.event_type for event in events.emits] == [
        EventType.HOOK_STARTED,
        EventType.HOOK_STARTED,
        EventType.HOOK_FINISHED,
        EventType.HOOK_FINISHED,
    ]
    assert all(event.ok for event in events.emits)


async def test_before_turn_keeps_successful_stdout_when_another_command_fails(
    tmp_path: Path,
):
    hooks, process, events, workspace = _hooks(
        tmp_path,
        [
            HookOutput(exit_code=1, stdout="LEAK", stderr="nope"),
            _ok("FROM_PROJECT"),
        ],
    )
    root = tmp_path / "home"
    global_script = _script(root / "prepend.sh")
    _script(workspace / ".agent" / "hooks" / "status.sh")
    _write_hooks(root, {"before_turn": [{"command": str(global_script)}]})
    _write_hooks(
        workspace / ".agent",
        {"before_turn": [{"command": ".agent/hooks/status.sh"}]},
    )

    text = await hooks.before_turn("hola")

    assert text == "FROM_PROJECT"
    assert process.calls[0][0] == global_script
    failed = next(event for event in events.emits if event.ok is False)
    assert failed == Event(
        event_type=EventType.HOOK_FINISHED,
        data={"name": "before_turn", "path": str(global_script)},
        ok=False,
        error="nope",
    )


class RaisingProcess:
    async def run(self, script: Path, stdin: str, *, cwd: Path) -> HookOutput:
        raise RuntimeError("spawn failed")


class OverlappingProcess:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.entered = 0

    async def run(self, script: Path, stdin: str, *, cwd: Path) -> HookOutput:
        self.entered += 1
        while self.entered < 2:
            await asyncio.sleep(0)
        label = "GLOBAL" if script.is_relative_to(self.root) else "LOCAL"
        return HookOutput(exit_code=0, stdout=label, stderr="")


async def test_before_turn_drops_stdout_when_the_command_times_out(tmp_path: Path):
    hooks, _process, events, workspace = _hooks(
        tmp_path,
        [HookOutput(exit_code=124, stdout="PARTIAL", stderr="", timed_out=True)],
    )
    _write_hooks(
        workspace / ".agent",
        {"before_turn": [{"command": ".agent/hooks/slow.sh"}]},
    )

    assert await hooks.before_turn("hola") == ""
    assert events.emits[-1].ok is False
    assert events.emits[-1].error == "timed out"


async def test_before_turn_runs_both_layers_together(tmp_path: Path):
    root = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    global_script = _script(root / "prepend.sh")
    local_script = _script(workspace / ".agent" / "hooks" / "status.sh")
    _write_hooks(root, {"before_turn": [{"command": str(global_script)}]})
    _write_hooks(
        workspace / ".agent",
        {"before_turn": [{"command": str(local_script)}]},
    )
    process = OverlappingProcess(root)
    hooks = Hook(root, workspace, process, RecordingEvents())

    text = await asyncio.wait_for(hooks.before_turn("hola"), timeout=1)

    assert process.entered == 2
    assert text == "GLOBAL\nLOCAL"


async def test_before_turn_returns_empty_when_the_process_raises(tmp_path: Path):
    root = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    script = _script(workspace / ".agent" / "hooks" / "prepend.sh")
    _write_hooks(workspace / ".agent", {"before_turn": [{"command": str(script)}]})
    events = RecordingEvents()
    hooks = Hook(root, workspace, RaisingProcess(), events)

    assert await hooks.before_turn("hola") == ""
    assert events.emits[-1] == Event(
        event_type=EventType.HOOK_FINISHED,
        data={"name": "before_turn", "path": str(script.resolve())},
        ok=False,
        error="spawn failed",
    )


async def test_before_turn_skips_missing_scripts(tmp_path: Path):
    hooks, process, events, workspace = _hooks(tmp_path, [])
    _write_hooks(
        workspace / ".agent",
        {"after_turn": [{"command": ".agent/hooks/digest.sh"}]},
    )

    assert await hooks.before_turn("hola") == ""
    assert process.calls == []
    assert events.emits == []


async def test_after_turn_passes_the_closed_turn_and_ignores_stdout(tmp_path: Path):
    call = ToolCall(name="read_file", arguments={"path": "a.txt"}, id="call-1")
    turn = ClosedTurn(
        stop_reason="end_turn",
        completed=True,
        error=None,
        messages=(
            Message(role="hook", content="HOOK_BEFORE"),
            Message(role="user", content="hola"),
            Message(role="assistant", content="", tool_calls=[call]),
            Message(
                role="tool",
                content="file text",
                tool_name="read_file",
                tool_call_id="call-1",
            ),
            Message(role="assistant", content="listo"),
        ),
    )
    hooks, process, events, workspace = _hooks(
        tmp_path, [_ok("ignored"), _ok("also ignored")]
    )
    root = tmp_path / "home"
    _script(root / "digest.sh")
    _script(workspace / ".agent" / "hooks" / "digest.sh")
    _write_hooks(root, {"after_turn": [{"command": str(root / "digest.sh")}]})
    _write_hooks(
        workspace / ".agent",
        {"after_turn": [{"command": ".agent/hooks/digest.sh"}]},
    )

    await hooks.after_turn(turn)

    payload = json.loads(process.calls[0][1])
    assert payload["stop_reason"] == "end_turn"
    assert payload["completed"] is True
    assert payload["error"] is None
    assert payload["messages"][0] == {"role": "hook", "content": "HOOK_BEFORE"}
    assert payload["messages"][2]["tool_calls"] == [
        {"name": "read_file", "arguments": {"path": "a.txt"}, "id": "call-1"}
    ]
    assert payload["messages"][3] == {
        "role": "tool",
        "content": "file text",
        "tool_name": "read_file",
        "tool_call_id": "call-1",
    }
    assert payload["messages"][-1]["content"] == "listo"
    assert len(process.calls) == 2
    assert all(event.ok for event in events.emits)


async def test_after_turn_records_a_failed_command_and_returns(tmp_path: Path):
    hooks, process, events, workspace = _hooks(
        tmp_path,
        [HookOutput(exit_code=1, stdout="LEAK", stderr="disk full")],
    )
    _write_hooks(
        workspace / ".agent",
        {"after_turn": [{"command": ".agent/hooks/digest.sh"}]},
    )
    turn = ClosedTurn(
        stop_reason="error",
        completed=False,
        error="ollama down",
        messages=(Message(role="user", content="hola"),),
    )

    await hooks.after_turn(turn)

    assert json.loads(process.calls[0][1])["error"] == "ollama down"
    assert events.emits[-1].ok is False
    assert events.emits[-1].error == "disk full"


async def test_before_turn_runs_array_entries_in_order_and_skips_bad_ones(
    tmp_path: Path,
):
    hooks, process, _events, workspace = _hooks(
        tmp_path, [_ok("ONE"), _ok("TWO")]
    )
    _script(workspace / "a.sh")
    _script(workspace / "b.sh")
    _write_hooks(
        workspace / ".agent",
        {
            "before_turn": [
                {"command": "a.sh", "timeout": 30, "matcher": "curl|wget"},
                {"timeout": 5},
                {"command": "b.sh"},
            ]
        },
    )

    text = await hooks.before_turn("hola")

    assert [call[0].name for call in process.calls] == ["a.sh", "b.sh"]
    assert text == "ONE\nTWO"


async def test_before_turn_skips_an_illegible_file_and_runs_the_other_layer(
    tmp_path: Path,
):
    hooks, process, events, workspace = _hooks(tmp_path, [_ok("FROM_PROJECT")])
    root = tmp_path / "home"
    root.mkdir()
    (root / "hooks.json").write_text("{", encoding="utf-8")
    local_script = _script(workspace / ".agent" / "hooks" / "status.sh")
    _write_hooks(
        workspace / ".agent",
        {"before_turn": [{"command": str(local_script)}]},
    )

    text = await hooks.before_turn("hola")

    assert text == "FROM_PROJECT"
    assert process.calls[0][0] == local_script
    failed = next(event for event in events.emits if event.ok is False)
    assert failed.data["path"] == str((root / "hooks.json").resolve())
    assert failed.event_type == EventType.HOOK_FINISHED


async def test_before_turn_ignores_a_file_whose_version_is_not_1(tmp_path: Path):
    hooks, process, events, _workspace = _hooks(tmp_path, [])
    root = tmp_path / "home"
    root.mkdir()
    script = _script(root / "prepend.sh")
    (root / "hooks.json").write_text(
        json.dumps(
            {"version": 2, "hooks": {"before_turn": [{"command": str(script)}]}}
        ),
        encoding="utf-8",
    )

    assert await hooks.before_turn("hola") == ""
    assert process.calls == []
    assert events.emits[-1].error == "hooks.json version must be 1"


def test_open_hooks_prints_global_then_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    root = tmp_path / "home"
    workspace = tmp_path / "project"
    before = _script(root / "prepend.sh")
    after = _script(workspace / ".agent" / "hooks" / "digest.sh")
    _write_hooks(root, {"before_turn": [{"command": str(before)}]})
    _write_hooks(workspace / ".agent", {"after_turn": [{"command": str(after)}]})
    process = RecordingProcess([])
    events = RecordingEvents()

    hooks = open_hooks(workspace, process, events, root)
    printed = capsys.readouterr().out

    assert isinstance(hooks, Hook)
    assert f"hooks: {before.resolve()}, {after.resolve()}" in printed
    assert process.calls == []
