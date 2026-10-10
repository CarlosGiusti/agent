import json

import pytest

from lib.tools.tavily import (
    TAVILY_SEARCH_URL,
    TAVILY_TIMEOUT_SECONDS,
    HttpResponse,
    TavilySearchBackend,
)
from lib.tools.web_search import SearchResult


class FakeTransport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, bytes, dict[str, str], float]] = []

    def post(
        self,
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        self.calls.append((url, body, headers, timeout))
        return self.response


async def test_search_posts_request_and_parses_results():
    payload = {
        "results": [
            {
                "title": "Python docs",
                "url": "https://docs.python.org/",
                "content": "Documentation for Python",
            }
        ]
    }
    transport = FakeTransport(HttpResponse(200, json.dumps(payload).encode()))
    backend = TavilySearchBackend("secret-key", transport)

    results = await backend.search("python docs", 3)

    assert results == [
        SearchResult(
            title="Python docs",
            url="https://docs.python.org/",
            snippet="Documentation for Python",
        )
    ]
    assert len(transport.calls) == 1
    url, body, headers, timeout = transport.calls[0]
    assert url == TAVILY_SEARCH_URL
    assert json.loads(body) == {
        "query": "python docs",
        "max_results": 3,
        "search_depth": "basic",
        "include_answer": False,
    }
    assert headers == {
        "Authorization": "Bearer secret-key",
        "Content-Type": "application/json",
    }
    assert timeout == TAVILY_TIMEOUT_SECONDS


async def test_http_error_does_not_expose_api_key():
    transport = FakeTransport(HttpResponse(401, b'{"error":"invalid secret-key"}'))
    backend = TavilySearchBackend("secret-key", transport)

    with pytest.raises(RuntimeError) as caught:
        await backend.search("python", 5)

    assert "HTTP 401" in str(caught.value)
    assert "secret-key" not in str(caught.value)
    assert "[redacted]" in str(caught.value)
