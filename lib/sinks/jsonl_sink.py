import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from lib.events.event_system import Event

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_LOGS_DIR = PROJECT_ROOT / "logs"


class JsonlSink:
    def __init__(self, session_id: str, logs_dir: Path = DEFAULT_LOGS_DIR) -> None:
        self.session_id = session_id
        self.logs_dir = logs_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def handle(self, event: Event) -> None:
        path = self.logs_dir / f"{self.session_id}.jsonl"
        record = {
            "logged_at": datetime.now(UTC).isoformat(),
            "session_id": self.session_id,
            **asdict(event),
        }
        try:
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001 — logging must not break the agent
            print(f"JsonlSink failed: {exc}", file=sys.stderr)
