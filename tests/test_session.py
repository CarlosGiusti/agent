from io import StringIO

from rich.console import Console

from lib.authorization.protocol import DecisionType, PromptAuthorizationProtocol
from lib.client.session import Session, TranscriptEntry
from lib.events.event_system import Event, EventType


class ScriptedKeys:
    def __init__(self, keys: list[str]) -> None:
        self._keys = iter(keys)

    def read(self) -> str:
        return next(self._keys)


def _session(buf: StringIO) -> Session:
    console = Console(
        file=buf,
        force_terminal=True,
        force_interactive=True,
        width=80,
        color_system=None,
        highlight=False,
        _environ={"TERM": "xterm-256color"},
    )
    return Session(console)


def _elevation(session: Session, keys: list[str]) -> DecisionType:
    return session.ask_elevation(
        "elevate",
        {"command": "curl https://example.com", "reasons": ["network"]},
        keys=ScriptedKeys(keys),
    )


def _web_search(session: Session, keys: list[str]) -> DecisionType:
    return session.ask_web_search(
        "web_search",
        {"query": "current Python release"},
        keys=ScriptedKeys(keys),
    )


def _delta(kind: str, text: str) -> Event:
    return Event(
        event_type=EventType.PROVIDER_DELTA,
        data={"kind": kind, "text": text},
        ok=True,
    )


def test_deltas_stay_in_the_history_without_repeating_the_answer():
    buf = StringIO()
    session = _session(buf)

    session.show_delta(_delta("thinking", "hmm\n"))
    session.show_delta(_delta("thinking", "more"))
    session.show_delta(_delta("text", "ok"))
    session.show_delta(_delta("tool", 'read_file path="a.txt"'))
    session.finish_response("ok")

    assert session.history == [
        TranscriptEntry("thinking", "hmm\nmore"),
        TranscriptEntry("text", "ok"),
        TranscriptEntry("tool", 'read_file path="a.txt"'),
    ]
    printed = buf.getvalue()
    assert "  hmm\n  more\n" in printed
    assert '  read_file path="a.txt"' in printed
    assert "\nok\n" in printed
    assert "\n  ok" not in printed


def test_finish_response_prints_the_answer_when_nothing_was_streamed():
    buf = StringIO()
    session = _session(buf)

    session.finish_response("hello")

    assert session.history == [TranscriptEntry("assistant", "hello")]
    assert "hello" in buf.getvalue()
    assert "agent" in buf.getvalue()


def test_read_message_and_assistant_stay_in_the_history():
    buf = StringIO()
    session = _session(buf)

    message = session.read_message(stream=StringIO("hola\n"))
    session.show_assistant("GLOBAL_OK")

    assert message == "hola"
    assert session.history == [
        TranscriptEntry("user", "hola"),
        TranscriptEntry("assistant", "GLOBAL_OK"),
    ]
    printed = buf.getvalue()
    assert "agent" in printed
    assert "GLOBAL_OK" in printed


def test_up_then_enter_allows_and_clears_the_prompt():
    buf = StringIO()
    session = _session(buf)

    decision = _elevation(session, ["up", "enter"])

    assert decision is DecisionType.ALLOW_ONCE
    written = buf.getvalue()
    assert "curl https://example.com" in written
    assert "network" in written
    assert "› Allow once" in written
    assert written.endswith("\x1b[2K")


def test_enter_denies_and_leaves_the_prompt():
    buf = StringIO()
    session = _session(buf)

    decision = _elevation(session, ["enter"])

    assert decision is DecisionType.DENY
    written = buf.getvalue()
    assert "curl https://example.com" in written
    assert "› Deny" in written
    assert "› Allow once" not in written
    assert not written.endswith("\x1b[2K")


def test_arrow_repaints_the_highlight_before_enter():
    buf = StringIO()
    session = _session(buf)

    decision = _elevation(session, ["up", "up", "enter"])

    assert decision is DecisionType.DENY
    assert "› Allow once" in buf.getvalue()


def test_down_from_deny_wraps_to_allow_once():
    buf = StringIO()
    session = _session(buf)

    assert _elevation(session, ["down", "enter"]) is DecisionType.ALLOW_ONCE


def test_a_later_elevation_asks_again():
    buf = StringIO()
    session = _session(buf)

    first = _elevation(session, ["up", "enter"])
    second = _elevation(session, ["up", "enter"])

    assert first is DecisionType.ALLOW_ONCE
    assert second is DecisionType.ALLOW_ONCE
    assert buf.getvalue().count("Elevation required") == 6


def test_web_search_prompt_shows_query_and_allows_once():
    buf = StringIO()
    session = _session(buf)

    decision = _web_search(session, ["up", "enter"])

    assert decision is DecisionType.ALLOW_ONCE
    written = buf.getvalue()
    assert "Web search" in written
    assert "current Python release" in written
    assert "› Allow once" in written


def test_web_search_prompt_defaults_to_deny():
    buf = StringIO()
    session = _session(buf)

    decision = _web_search(session, ["enter"])

    assert decision is DecisionType.DENY
    assert "› Deny" in buf.getvalue()


def test_an_ignored_key_does_not_change_the_selection():
    buf = StringIO()
    session = _session(buf)

    decision = _elevation(session, ["x", "enter"])

    assert decision is DecisionType.DENY
    assert "› Allow once" not in buf.getvalue()


async def test_prompt_protocol_forwards_the_asker_decision():
    seen: list[tuple[str, dict]] = []

    def ask(action: str, args: dict) -> DecisionType:
        seen.append((action, args))
        return DecisionType.DENY

    protocol = PromptAuthorizationProtocol(ask)

    assert await protocol.check("elevate", {"command": "ls"}) is DecisionType.DENY
    assert seen == [("elevate", {"command": "ls"})]
