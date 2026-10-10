from lib.authorization.protocol import AuthorizationProtocol, DecisionType
from lib.events.event_system import Event
from lib.tools.web_search import SearchResult, make_web_search


class ScriptedProtocol(AuthorizationProtocol):
    def __init__(self, decisions: list[DecisionType]) -> None:
        self._decisions = list(decisions)
        self.calls: list[tuple[str, dict]] = []

    async def check(self, action: str, args: dict) -> DecisionType:
        self.calls.append((action, args))
        if not self._decisions:
            raise AssertionError(f"unexpected prompt for {action}")
        return self._decisions.pop(0)


class FakeSearchBackend:
    def __init__(
        self,
        results: list[SearchResult] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.results = results or []
        self.error = error
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        self.calls.append((query, max_results))
        if self.error is not None:
            raise self.error
        return self.results


class RecordingEvents:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def emit(
        self,
        event_type: str,
        data: dict | None = None,
        *,
        ok: bool = True,
        error: str | None = None,
    ) -> None:
        self.events.append(Event(event_type, data or {}, ok, error))


async def test_deny_does_not_call_backend_and_emits_permission_denied():
    backend = FakeSearchBackend()
    protocol = ScriptedProtocol([DecisionType.DENY])
    events = RecordingEvents()
    tool = make_web_search(backend, protocol, events)

    result = await tool.call(query="current Python release")

    assert "Permission denied" in result
    assert backend.calls == []
    assert protocol.calls == [("web_search", {"query": "current Python release"})]
    assert [(event.event_type, event.data["name"]) for event in events.events] == [
        ("permission.denied", "web_search")
    ]


async def test_allow_calls_backend_and_formats_results():
    backend = FakeSearchBackend(
        [
            SearchResult(
                title="Python 3.13",
                url="https://example.com/python",
                snippet="Current release notes",
            )
        ]
    )
    protocol = ScriptedProtocol([DecisionType.ALLOW_ONCE])
    tool = make_web_search(backend, protocol)

    result = await tool.call(query="  Python release  ", max_results=99)

    assert backend.calls == [("Python release", 10)]
    assert result == (
        "1. Python 3.13\n"
        "   URL: https://example.com/python\n"
        "   Snippet: Current release notes"
    )


async def test_later_search_asks_again():
    backend = FakeSearchBackend()
    protocol = ScriptedProtocol([DecisionType.ALLOW_ONCE, DecisionType.ALLOW_ONCE])
    tool = make_web_search(backend, protocol)

    await tool.call(query="first")
    await tool.call(query="second")

    assert protocol.calls == [
        ("web_search", {"query": "first"}),
        ("web_search", {"query": "second"}),
    ]
    assert backend.calls == [("first", 5), ("second", 5)]


async def test_empty_query_does_not_prompt():
    backend = FakeSearchBackend()
    protocol = ScriptedProtocol([])
    tool = make_web_search(backend, protocol)

    result = await tool.call(query="  ")

    assert result == "Error: query is empty"
    assert protocol.calls == []
    assert backend.calls == []


async def test_backend_failure_is_returned_as_model_readable_text():
    backend = FakeSearchBackend(error=RuntimeError("service unavailable"))
    protocol = ScriptedProtocol([DecisionType.ALLOW_ONCE])
    tool = make_web_search(backend, protocol)

    result = await tool.call(query="news")

    assert result == "Error: web search failed: service unavailable"
