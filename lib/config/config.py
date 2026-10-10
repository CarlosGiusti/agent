from argparse import ArgumentParser
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from pathlib import Path

from lib.events.event_system import ANY_EVENT, Event, EventSystem, EventType
from lib.sinks.jsonl_sink import JsonlSink


@dataclass(frozen=True)
class Args:
    workspace: Path


@dataclass(frozen=True)
class Config:
    args: Args
    events: EventSystem

    @classmethod
    def load(
        cls,
        argv: list[str] | None = None,
        events: EventSystem | None = None,
        sink: Callable[[Event], None] | None = None,
    ) -> "Config":
        args = _parse_args(argv)
        event_system = events or EventSystem()
        handler = sink or JsonlSink(session_id=event_system.session_id).handle
        event_system.on(ANY_EVENT, _logged(handler))
        return cls(args=args, events=event_system)


def _logged(handler: Callable[[Event], None]) -> Callable[[Event], None]:
    def handle(event: Event) -> None:
        if event.event_type == EventType.PROVIDER_DELTA:
            return
        handler(event)

    return handle


def load_dotenv(path: Path, env: MutableMapping[str, str]) -> None:
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key or key in env:
            continue
        env[key] = value.strip().strip("'\"")


def _parse_args(argv: list[str] | None = None) -> Args:
    parser = ArgumentParser(description="Coding Agent")
    parser.add_argument(
        "workspace",
        nargs="?",
        default=".",
        type=Path,
        help="Project folder (default: current directory)",
    )
    parsed = parser.parse_args(argv)
    workspace = parsed.workspace.expanduser().resolve()
    if not workspace.is_dir():
        parser.error(f"not a directory: {workspace}")
    return Args(workspace=workspace)
