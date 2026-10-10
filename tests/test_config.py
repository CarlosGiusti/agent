from pathlib import Path

import pytest

from lib.config.config import Config, load_dotenv
from lib.events.event_system import Event, EventSystem, EventType


class RecordingSink:
    def __init__(self) -> None:
        self.handled: list[Event] = []

    def handle(self, event: Event) -> None:
        self.handled.append(event)


def test_load_dotenv_fills_missing_keys_and_keeps_existing_ones(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text(
        "# comment\n"
        "\n"
        "export AGENT_PROVIDER=claude\n"
        "ANTHROPIC_API_KEY='from-file'\n"
        "ALREADY=from-file\n"
    )
    env = {"ALREADY": "from-env"}

    load_dotenv(path, env)

    assert env["AGENT_PROVIDER"] == "claude"
    assert env["ANTHROPIC_API_KEY"] == "from-file"
    assert env["ALREADY"] == "from-env"


def test_load_dotenv_skips_a_missing_file(tmp_path: Path):
    env = {"AGENT_PROVIDER": "ollama"}

    load_dotenv(tmp_path / ".env", env)

    assert env == {"AGENT_PROVIDER": "ollama"}


def test_load_returns_validated_args_and_injected_events(tmp_path: Path):
    events = EventSystem(session_id="sess-1")
    sink = RecordingSink()

    config = Config.load(argv=[str(tmp_path)], events=events, sink=sink.handle)

    assert config.args.workspace == tmp_path.resolve()
    assert config.events is events
    assert config.events.session_id == "sess-1"


def test_load_defaults_workspace_to_current_directory():
    events = EventSystem(session_id="sess-1")
    sink = RecordingSink()

    config = Config.load(argv=[], events=events, sink=sink.handle)

    assert config.args.workspace == Path.cwd()
    assert config.events is events


def test_load_rejects_path_that_is_not_a_directory(tmp_path: Path):
    missing = tmp_path / "missing"
    events = EventSystem(session_id="sess-1")
    sink = RecordingSink()

    with pytest.raises(SystemExit):
        Config.load(argv=[str(missing)], events=events, sink=sink.handle)


def test_load_logs_events_and_skips_provider_deltas(tmp_path: Path):
    events = EventSystem(session_id="sess-1")
    sink = RecordingSink()
    Config.load(argv=[str(tmp_path)], events=events, sink=sink.handle)

    events.emit(EventType.TURN_STARTED, {"n": 1})
    events.emit(
        EventType.PROVIDER_DELTA,
        {"kind": "thinking", "text": "Okay", "iteration": 1},
    )
    events.emit(EventType.PROVIDER_FINISHED, {"model": "qwen3:8b", "iteration": 1})

    assert sink.handled == [
        Event(event_type=EventType.TURN_STARTED, data={"n": 1}, ok=True),
        Event(
            event_type=EventType.PROVIDER_FINISHED,
            data={"model": "qwen3:8b", "iteration": 1},
            ok=True,
        ),
    ]
