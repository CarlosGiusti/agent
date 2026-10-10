from lib.events.event_system import ANY_EVENT, Event, EventSystem, EventType


def test_event_system_uses_injected_session_id():
    events = EventSystem(session_id="sess-1")

    assert events.session_id == "sess-1"


def test_instances_do_not_share_subscribers():
    first = EventSystem()
    second = EventSystem()
    seen: list[Event] = []
    first.on(EventType.TURN_STARTED, seen.append)

    second.emit(EventType.TURN_STARTED)

    assert seen == []


def test_emit_delivers_to_typed_and_wildcard_subscribers():
    events = EventSystem()
    typed: list[Event] = []
    wildcard: list[Event] = []
    events.on(EventType.TURN_STARTED, typed.append)
    events.on(ANY_EVENT, wildcard.append)

    events.emit(EventType.TURN_STARTED, {"n": 1})

    expected = Event(event_type=EventType.TURN_STARTED, data={"n": 1}, ok=True)
    assert typed == [expected]
    assert wildcard == [expected]


def test_emit_does_not_notify_unrelated_subscribers():
    events = EventSystem()
    seen: list[Event] = []
    events.on(EventType.TOOL_STARTED, seen.append)

    events.emit(EventType.TURN_FINISHED, ok=False, error="boom")

    assert seen == []
