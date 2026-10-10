import json
from datetime import datetime
from pathlib import Path

from lib.events.event_system import Event
from lib.sinks.jsonl_sink import JsonlSink


def test_handle_writes_event_to_session_jsonl(tmp_path: Path):
    sink = JsonlSink(session_id="sess-1", logs_dir=tmp_path)
    event = Event(event_type="turn.started", data={"n": 1}, ok=True)

    sink.handle(event)

    path = tmp_path / "sess-1.jsonl"
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["session_id"] == "sess-1"
    assert record["event_type"] == "turn.started"
    assert record["data"] == {"n": 1}
    assert record["ok"] is True
    assert record["error"] is None
    datetime.fromisoformat(record["logged_at"])


def test_handle_appends_a_line_per_event(tmp_path: Path):
    sink = JsonlSink(session_id="sess-1", logs_dir=tmp_path)

    sink.handle(Event(event_type="turn.started", data={}, ok=True))
    sink.handle(
        Event(event_type="turn.finished", data={}, ok=False, error="ollama down")
    )

    lines = (tmp_path / "sess-1.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event_type"] == "turn.started"
    assert json.loads(lines[1])["error"] == "ollama down"
