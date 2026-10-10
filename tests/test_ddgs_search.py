from typing import Any

import pytest

from lib.tools.ddgs_search import DdgsSearchBackend
from lib.tools.web_search import SearchResult


class FakeTextSearchClient:
    def __init__(
        self,
        results: list[dict[str, Any]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.results = results if results is not None else []
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def text(self, query: str, *, max_results: int) -> list[dict[str, Any]]:
        self.calls.append((query, max_results))
        if self.error is not None:
            raise self.error
        return self.results


async def test_search_maps_ddgs_fields():
    client = FakeTextSearchClient(
        [
            {
                "title": "Python docs",
                "href": "https://docs.python.org/",
                "body": "Documentation for Python",
            }
        ]
    )
    backend = DdgsSearchBackend(client)

    results = await backend.search("python docs", 3)

    assert results == [
        SearchResult(
            title="Python docs",
            url="https://docs.python.org/",
            snippet="Documentation for Python",
        )
    ]
    assert client.calls == [("python docs", 3)]


async def test_search_skips_non_dict_items():
    client = FakeTextSearchClient(
        [
            "noise",
            {
                "title": "Keep",
                "href": "https://example.com/",
                "body": "ok",
            },
        ]
    )
    backend = DdgsSearchBackend(client)

    results = await backend.search("q", 5)

    assert results == [
        SearchResult(title="Keep", url="https://example.com/", snippet="ok")
    ]


async def test_client_error_becomes_runtime_error():
    client = FakeTextSearchClient(error=RuntimeError("rate limited"))
    backend = DdgsSearchBackend(client)

    with pytest.raises(RuntimeError, match="rate limited"):
        await backend.search("python", 5)
