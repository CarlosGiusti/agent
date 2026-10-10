import asyncio
from typing import Any, Protocol

from ddgs import DDGS

from lib.tools.web_search import SearchResult


class TextSearchClient(Protocol):
    def text(self, query: str, *, max_results: int) -> list[dict[str, Any]]: ...


class DdgsClient:
    def text(self, query: str, *, max_results: int) -> list[dict[str, Any]]:
        return DDGS().text(query, max_results=max_results)


class DdgsSearchBackend:
    def __init__(self, client: TextSearchClient) -> None:
        self.client = client

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        try:
            raw_results = await asyncio.to_thread(
                self.client.text,
                query,
                max_results=max_results,
            )
            if not isinstance(raw_results, list):
                raise TypeError("ddgs response has no results list")
            return [
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=str(item.get("href") or ""),
                    snippet=str(item.get("body") or ""),
                )
                for item in raw_results
                if isinstance(item, dict)
            ]
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc
