import asyncio
import json
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from lib.agents.types import Message, ToolCall
from lib.events.event_system import EventEmitter, EventType
from lib.paths.paths import AGENT_HOME, workspace_agent_dir

HOOK_ROLE = "hook"
HOOK_TIMEOUT_SECONDS = 5
HOOKS_FILE = "hooks.json"
BEFORE_TURN = "before_turn"
AFTER_TURN = "after_turn"


@dataclass(frozen=True)
class HookOutput:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass(frozen=True)
class ClosedTurn:
    stop_reason: str
    completed: bool
    error: str | None
    messages: tuple[Message, ...]


class HookProcess(Protocol):
    async def run(self, script: Path, stdin: str, *, cwd: Path) -> HookOutput: ...


class TurnHook(Protocol):
    async def before_turn(self, prompt: str) -> str: ...

    async def after_turn(self, turn: ClosedTurn) -> None: ...


class NullHook:
    async def before_turn(self, prompt: str) -> str:
        return ""

    async def after_turn(self, turn: ClosedTurn) -> None:
        return None


class SubprocessHookProcess:
    def __init__(self, timeout: float = HOOK_TIMEOUT_SECONDS) -> None:
        self.timeout = timeout

    async def run(self, script: Path, stdin: str, *, cwd: Path) -> HookOutput:
        try:
            proc = await asyncio.create_subprocess_exec(
                str(script),
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            return HookOutput(exit_code=127, stdout="", stderr=str(exc))
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(stdin.encode("utf-8")),
                timeout=self.timeout,
            )
        except TimeoutError:
            await _stop_group(proc)
            return HookOutput(
                exit_code=124,
                stdout="",
                stderr="timed out",
                timed_out=True,
            )
        return HookOutput(
            exit_code=proc.returncode if proc.returncode is not None else 1,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )


class Hook:
    def __init__(
        self,
        root: Path,
        workspace: Path,
        process: HookProcess,
        events: EventEmitter,
    ) -> None:
        self.root = root
        self.workspace = workspace.resolve()
        self.process = process
        self.events = events

    async def before_turn(self, prompt: str) -> str:
        stdin = _json({"prompt": prompt})
        scripts = self._scripts(BEFORE_TURN)
        outputs = await asyncio.gather(
            *(self._run(script, stdin, name="before_turn") for script in scripts)
        )
        chunks: list[str] = []
        for output in outputs:
            if output is None or output.timed_out or output.exit_code != 0:
                continue
            text = output.stdout.strip()
            if text:
                chunks.append(text)
        return "\n".join(chunks)

    async def after_turn(self, turn: ClosedTurn) -> None:
        stdin = _json(
            {
                "stop_reason": turn.stop_reason,
                "completed": turn.completed,
                "error": turn.error,
                "messages": [_message_payload(message) for message in turn.messages],
            }
        )
        scripts = self._scripts(AFTER_TURN)
        await asyncio.gather(
            *(self._run(script, stdin, name="after_turn") for script in scripts)
        )

    async def _run(self, script: Path, stdin: str, *, name: str) -> HookOutput | None:
        data = {"name": name, "path": str(script)}
        self.events.emit(EventType.HOOK_STARTED, data)
        try:
            output = await self.process.run(script, stdin, cwd=self.workspace)
        except Exception as exc:  # noqa: BLE001 — a hook failure must not stop the turn
            self.events.emit(EventType.HOOK_FINISHED, data, ok=False, error=str(exc))
            return None
        if output.timed_out or output.exit_code != 0:
            error = output.stderr.strip() or (
                "timed out" if output.timed_out else f"exit {output.exit_code}"
            )
            self.events.emit(EventType.HOOK_FINISHED, data, ok=False, error=error)
            return output
        self.events.emit(EventType.HOOK_FINISHED, data)
        return output

    def _scripts(self, name: str) -> list[Path]:
        scripts: list[Path] = []
        for config in _config_paths(self.root, self.workspace):
            loaded = _load_hooks(config, self.workspace)
            if loaded is None:
                continue
            if loaded.error is not None:
                self.events.emit(
                    EventType.HOOK_FINISHED,
                    {"name": name, "path": str(config.resolve())},
                    ok=False,
                    error=loaded.error,
                )
                continue
            scripts.extend(loaded.commands.get(name, []))
        return scripts


def open_hooks(
    workspace: Path,
    process: HookProcess,
    events: EventEmitter,
    root: Path = AGENT_HOME,
) -> Hook:
    hook = Hook(root, workspace, process, events)
    paths = hook._scripts(BEFORE_TURN) + hook._scripts(AFTER_TURN)
    if paths:
        print("hooks: " + ", ".join(str(path) for path in paths))
    return hook


@dataclass(frozen=True)
class _HookFile:
    commands: dict[str, list[Path]]
    error: str | None


def _config_paths(root: Path, workspace: Path) -> tuple[Path, Path]:
    return (root / HOOKS_FILE, workspace_agent_dir(workspace) / HOOKS_FILE)


def _load_hooks(path: Path, workspace: Path) -> _HookFile | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _HookFile(commands={}, error=str(exc))
    if not isinstance(data, dict) or data.get("version") != 1:
        return _HookFile(commands={}, error="hooks.json version must be 1")
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        return _HookFile(commands={}, error="hooks must be an object")
    commands: dict[str, list[Path]] = {}
    for name in (BEFORE_TURN, AFTER_TURN):
        entries = hooks.get(name, [])
        if not isinstance(entries, list):
            return _HookFile(commands={}, error=f"{name} must be a list")
        commands[name] = [
            script
            for entry in entries
            if (script := _command_path(entry, workspace)) is not None
        ]
    return _HookFile(commands=commands, error=None)


def _command_path(entry: object, workspace: Path) -> Path | None:
    if not isinstance(entry, dict):
        return None
    command = entry.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    script = Path(command)
    if not script.is_absolute():
        script = workspace / script
    return script.resolve()


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _message_payload(message: Message) -> dict:
    payload: dict = {"role": message.role, "content": message.content}
    if message.tool_name:
        payload["tool_name"] = message.tool_name
    if message.tool_call_id:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        payload["tool_calls"] = [
            _tool_call_payload(call) for call in message.tool_calls
        ]
    return payload


def _tool_call_payload(call: ToolCall) -> dict:
    return {"name": call.name, "arguments": call.arguments, "id": call.id}


async def _stop_group(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    pid = proc.pid
    if pid is not None:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except ProcessLookupError:
                return
    try:
        await asyncio.wait_for(proc.wait(), timeout=1)
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            return
        await proc.wait()
