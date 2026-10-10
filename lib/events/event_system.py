from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

ANY_EVENT = "*"


class EventType(StrEnum):
    TURN_STARTED = "turn.started"
    TURN_FINISHED = "turn.finished"
    PROVIDER_STARTED = "provider.started"
    PROVIDER_DELTA = "provider.delta"
    PROVIDER_FINISHED = "provider.finished"
    PERMISSION_DENIED = "permission.denied"
    TOOL_STARTED = "tool.started"
    TOOL_FINISHED = "tool.finished"
    HOOK_STARTED = "hook.started"
    HOOK_FINISHED = "hook.finished"


@dataclass(frozen=True)
class Event:
    event_type: str
    data: dict
    ok: bool
    error: str | None = None


class EventEmitter(Protocol):
    def emit(
        self,
        event_type: str,
        data: dict | None = None,
        *,
        ok: bool = True,
        error: str | None = None,
    ) -> None: ...


class EventSystem:
    def __init__(self, session_id: str | None = None) -> None:
        self.session_id = session_id or str(uuid4())
        self.subscribers: defaultdict[str, list[Callable[[Event], None]]] = defaultdict(
            list
        )

    def on(self, event_type: str, subscriber: Callable[[Event], None]) -> None:
        self.subscribers[event_type].append(subscriber)

    def emit(
        self,
        event_type: str,
        data: dict | None = None,
        *,
        ok: bool = True,
        error: str | None = None,
    ) -> None:
        event = Event(
            event_type=event_type,
            data=data or {},
            ok=ok,
            error=error,
        )
        for subscriber in list(self.subscribers[event.event_type]):
            subscriber(event)
        if event.event_type != ANY_EVENT:
            for subscriber in list(self.subscribers[ANY_EVENT]):
                subscriber(event)
