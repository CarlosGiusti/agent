from contextlib import nullcontext
from dataclasses import dataclass
from typing import TextIO

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from lib.authorization.protocol import DecisionType
from lib.client.keys import KeyReader, TerminalKeys
from lib.events.event_system import Event

_DELTA_STYLES = {
    "thinking": "yellow",
    "text": "green",
    "tool": "cyan",
}
_INDENT = "  "
_INDENTED = frozenset({"thinking", "tool"})

_MAX_COMMAND_CHARS = 200
_MAX_QUERY_CHARS = 200
_OPTIONS: tuple[tuple[DecisionType, str], ...] = (
    (DecisionType.ALLOW_ONCE, "Allow once"),
    (DecisionType.DENY, "Deny"),
)
_DENY_INDEX = 1


@dataclass(frozen=True)
class TranscriptEntry:
    role: str
    text: str


class Session:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console(highlight=False)
        self.history: list[TranscriptEntry] = []
        self._delta_kind: str | None = None
        self._line_start = True
        self._saw_text = False

    def read_message(self, *, stream: TextIO | None = None) -> str:
        text = self.console.input("[bold cyan]>[/bold cyan] ", stream=stream)
        text = text.removesuffix("\n").removesuffix("\r")
        self.history.append(TranscriptEntry("user", text))
        return text

    def show_delta(self, event: Event) -> None:
        kind = str(event.data.get("kind") or "")
        text = str(event.data.get("text") or "")
        style = _DELTA_STYLES.get(kind)
        if style is None or text == "":
            return
        if kind != self._delta_kind:
            if self._delta_kind not in (None, "tool"):
                self.console.print()
            self._delta_kind = kind
            self._line_start = True
        visible = text
        if kind in _INDENTED:
            visible, self._line_start = _indent_lines(text, self._line_start)
        if kind == "tool":
            self.console.print(Text(visible, style=style))
        else:
            self.console.print(Text(visible, style=style), end="")
            if kind == "text":
                self._saw_text = True
        self._append_chunk(kind, text)

    def finish_response(self, text: str) -> None:
        self._end_line()
        if not self._saw_text and text:
            self._show("assistant", text, "agent", "bold green")
        self._saw_text = False

    def show_assistant(self, text: str) -> None:
        self._show("assistant", text, "agent", "bold green")

    def show_error(self, text: str) -> None:
        self._end_line()
        self._saw_text = False
        self._show("error", text, "error", "bold red")

    def ask_elevation(
        self,
        action: str,
        args: dict,
        *,
        keys: KeyReader | None = None,
    ) -> DecisionType:
        selected = _DENY_INDEX
        source = nullcontext(keys) if keys is not None else TerminalKeys()
        with (
            source as reader,
            Live(
                _elevation_panel(action, args, selected),
                console=self.console,
                auto_refresh=False,
                screen=False,
                redirect_stdout=False,
                redirect_stderr=False,
            ) as live,
        ):
            while True:
                key = reader.read()
                if key == "up":
                    selected = (selected - 1) % len(_OPTIONS)
                elif key == "down":
                    selected = (selected + 1) % len(_OPTIONS)
                elif key == "enter":
                    decision = _OPTIONS[selected][0]
                    live.transient = decision is DecisionType.ALLOW_ONCE
                    return decision
                else:
                    continue
                live.update(
                    _elevation_panel(action, args, selected),
                    refresh=True,
                )

    def ask_web_search(
        self,
        action: str,
        args: dict,
        *,
        keys: KeyReader | None = None,
    ) -> DecisionType:
        selected = _DENY_INDEX
        source = nullcontext(keys) if keys is not None else TerminalKeys()
        with (
            source as reader,
            Live(
                _web_search_panel(action, args, selected),
                console=self.console,
                auto_refresh=False,
                screen=False,
                redirect_stdout=False,
                redirect_stderr=False,
            ) as live,
        ):
            while True:
                key = reader.read()
                if key == "up":
                    selected = (selected - 1) % len(_OPTIONS)
                elif key == "down":
                    selected = (selected + 1) % len(_OPTIONS)
                elif key == "enter":
                    decision = _OPTIONS[selected][0]
                    live.transient = decision is DecisionType.ALLOW_ONCE
                    return decision
                else:
                    continue
                live.update(
                    _web_search_panel(action, args, selected),
                    refresh=True,
                )

    def _end_line(self) -> None:
        if self._delta_kind not in (None, "tool"):
            self.console.print()
        self._delta_kind = None

    def _append_chunk(self, role: str, text: str) -> None:
        if self.history and self.history[-1].role == role:
            previous = self.history[-1]
            self.history[-1] = TranscriptEntry(role, previous.text + text)
            return
        self.history.append(TranscriptEntry(role, text))

    def _show(self, role: str, text: str, label: str, style: str) -> None:
        self.history.append(TranscriptEntry(role, text))
        self.console.print()
        self.console.print(Text(label, style=style))
        self.console.print(text, markup=False, highlight=False)


def _elevation_panel(action: str, args: dict, selected: int) -> Panel:
    command = str(args.get("command") or "")
    if len(command) > _MAX_COMMAND_CHARS:
        command = command[:_MAX_COMMAND_CHARS] + "..."
    body = Text()
    body.append(f"action: {action}\n")
    body.append(f"command: {command}\n")
    body.append("reasons:\n")
    body.append(_reasons(args.get("reasons")))
    body.append("\n\n")
    for index, (_, label) in enumerate(_OPTIONS):
        marker = "› " if index == selected else "  "
        style = "bold reverse" if index == selected else "dim"
        body.append(f"{marker}{label}\n", style=style)
    return Panel(
        body,
        title="Elevation required",
        subtitle="↑↓ move   enter select",
        border_style="yellow",
    )


def _web_search_panel(action: str, args: dict, selected: int) -> Panel:
    query = str(args.get("query") or "")
    if len(query) > _MAX_QUERY_CHARS:
        query = query[:_MAX_QUERY_CHARS] + "..."
    body = Text()
    body.append(f"action: {action}\n")
    body.append(f"query: {query}\n\n")
    for index, (_, label) in enumerate(_OPTIONS):
        marker = "› " if index == selected else "  "
        style = "bold reverse" if index == selected else "dim"
        body.append(f"{marker}{label}\n", style=style)
    return Panel(
        body,
        title="Web search",
        subtitle="↑↓ move   enter select",
        border_style="yellow",
    )


def _indent_lines(text: str, line_start: bool) -> tuple[str, bool]:
    parts: list[str] = []
    for char in text:
        if line_start and char != "\n":
            parts.append(_INDENT)
            line_start = False
        parts.append(char)
        if char == "\n":
            line_start = True
    return "".join(parts), line_start


def _reasons(reasons: object) -> str:
    if not isinstance(reasons, list) or not reasons:
        return "  (none)"
    return "\n".join(f"  - {item}" for item in reasons)
