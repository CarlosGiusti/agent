import json
import sys

from lib.events.event_system import Event


class ConsoleSink:
    def handle(self, event: Event) -> None:
        status = "ok" if event.ok else "error"
        payload = (
            event.error
            if event.error
            else json.dumps(event.data, default=str, ensure_ascii=False)
        )
        print(
            f"[{status}] {event.event_type} {payload}",
            file=sys.stderr,
        )
