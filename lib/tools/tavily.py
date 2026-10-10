import asyncio
import json
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from lib.tools.web_search import SearchResult

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


class HttpTransport(Protocol):
    def post(
        self,
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse: ...


class UrllibTransport:
    def post(
        self,
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        request = Request(url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=timeout) as response:
                return HttpResponse(response.status, response.read())
        except HTTPError as error:
            return HttpResponse(error.code, error.read())


class TavilySearchBackend:
    def __init__(self, api_key: str, transport: HttpTransport) -> None:
        if not api_key:
            raise ValueError("Tavily API key is empty")
        self.api_key = api_key
        self.transport = transport

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        body = json.dumps(
            {
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": False,
            }
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = await asyncio.to_thread(
                self.transport.post,
                TAVILY_SEARCH_URL,
                body,
                headers,
                TAVILY_TIMEOUT_SECONDS,
            )
            if response.status != 200:
                detail = response.body.decode("utf-8", errors="replace")
                raise RuntimeError(f"Tavily returned HTTP {response.status}: {detail}")
            payload = json.loads(response.body)
            raw_results = payload.get("results")
            if not isinstance(raw_results, list):
                raise TypeError("Tavily response has no results list")
            return [
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=str(item.get("url") or ""),
                    snippet=str(item.get("content") or ""),
                )
                for item in raw_results
                if isinstance(item, dict)
            ]
        except Exception as exc:
            message = str(exc).replace(self.api_key, "[redacted]")
            raise RuntimeError(message) from exc
