import asyncio
from pathlib import Path

from lib.authorization.authorization import Authorization
from lib.events.event_system import EventEmitter, EventType
from lib.sandbox.sandbox import (
    TIMEOUT_SECONDS,
    CommandSandbox,
    classify_constraint,
    format_output,
)
from lib.tools.tool import Tool


class Shell:
    def __init__(
        self,
        sandbox: CommandSandbox,
        authorization: Authorization,
        workspace: Path,
        events: EventEmitter | None = None,
    ):
        self.sandbox = sandbox
        self.authorization = authorization
        self.workspace = workspace.resolve()
        self.events = events

    async def run(self, command: str) -> str:
        if not command.strip():
            return "Error: command is empty"

        boxed = await asyncio.to_thread(self.sandbox.run, command, elevated=False)
        reasons = classify_constraint(boxed, command, self.workspace)
        if not reasons:
            return format_output(boxed)

        allowed = await self.authorization.check(command, reasons)
        if not allowed:
            result = (
                "Permission denied by user. Command was not retried outside the "
                "workspace sandbox. You may try a different command or ask the user."
            )
            if self.events is not None:
                self.events.emit(
                    EventType.PERMISSION_DENIED,
                    {"name": "run_command", "reasons": list(reasons)},
                    ok=False,
                    error=result,
                )
            return result

        wide = await asyncio.to_thread(self.sandbox.run, command, elevated=True)
        header = "elevated (" + ", ".join(reasons) + ")"
        return f"{header}\n{format_output(wide)}"


def make_run_command(shell: Shell) -> Tool:
    return Tool(
        name="run_command",
        description=(
            "Run a bash command in the workspace sandbox (no network; filesystem "
            "limited to the project plus read-only system paths). If the sandbox "
            "blocks network or a path outside the workspace, the user is asked to "
            "allow that command once in the unrestricted box. Use for tests, git, "
            "listing files, and any shell task. Returns exit code, stdout, and "
            f"stderr. Times out after {TIMEOUT_SECONDS} seconds."
        ),
        arguments={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Bash command to execute, including pipes and redirects",
                }
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        function=shell.run,
    )
