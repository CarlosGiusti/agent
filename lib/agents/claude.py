import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any, Protocol

from lib.agents.provider import (
    Completion,
    DeltaKind,
    Provider,
    StopReason,
    format_tool_use,
)
from lib.agents.types import Message, ToolCall
from lib.hooks.hooks import HOOK_ROLE
from lib.rules.rules import RULE_ROLE
from lib.tools.tool import Tool

MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096
THINKING_EFFORT = "medium"

_END_TURN = frozenset({"end_turn", "stop_sequence"})
_TRUNCATED = frozenset({"max_tokens", "model_context_window_exceeded"})


class MessagesTransport(Protocol):
    def events(self, body: dict[str, Any]) -> AsyncIterator[dict[str, Any]]: ...


class MessagesHttp:
    def __init__(
        self,
        api_key: str,
        *,
        url: str = MESSAGES_URL,
        opener: Callable[..., Any] | None = None,
    ):
        self.api_key = api_key
        self.url = url
        self._opener = opener or urllib.request.urlopen

    async def post(self, body: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._post, body)

    def events(self, body: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        return _iter_queue(lambda queue, loop: self._produce_events(body, queue, loop))

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            with self._opener(self._http_request(body)) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise _http_error(exc) from exc
        return _decode_payload(raw)

    def _produce_events(
        self,
        body: dict[str, Any],
        queue: asyncio.Queue[Any],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        try:
            with self._opener(self._http_request(body)) as response:
                for event in _sse_events(response):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            loop.call_soon_threadsafe(queue.put_nowait, None)
        except urllib.error.HTTPError as exc:
            loop.call_soon_threadsafe(queue.put_nowait, _http_error(exc))
        except BaseException as exc:  # noqa: BLE001 — hand the HTTP failure to the loop
            loop.call_soon_threadsafe(queue.put_nowait, exc)

    def _http_request(self, body: dict[str, Any]) -> urllib.request.Request:
        return urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            },
            method="POST",
        )


class ClaudeProvider(Provider):
    def __init__(
        self,
        model: str,
        api_key: str,
        transport: MessagesTransport | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        super().__init__(model)
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.transport = transport if transport is not None else MessagesHttp(api_key)

    async def complete(self, context: list[Message], tools: list[Tool]) -> Completion:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        return await _completion_from_events(
            self,
            self.transport.events(self._request(context, tools)),
        )

    def _request(self, context: list[Message], tools: list[Tool]) -> dict[str, Any]:
        rules = [message.content for message in context if message.role == RULE_ROLE]
        chat = [message for message in context if message.role != RULE_ROLE]
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "stream": True,
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": THINKING_EFFORT},
            "messages": _wire_messages(chat),
        }
        if rules:
            body["system"] = "\n\n".join(rules)
        if tools:
            body["tools"] = [_tool_schema(tool) for tool in tools]
        return body


def _wire_messages(chat: list[Message]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    user_blocks: list[dict[str, Any]] = []
    for message in chat:
        if message.role == "assistant":
            _flush_user(messages, user_blocks)
            messages.append(
                {"role": "assistant", "content": _assistant_blocks(message)}
            )
            continue
        if message.role == "tool":
            user_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content,
                }
            )
            continue
        if message.role in {HOOK_ROLE, "user"}:
            user_blocks.append({"type": "text", "text": message.content})
    _flush_user(messages, user_blocks)
    return messages


def _flush_user(messages: list[dict[str, Any]], blocks: list[dict[str, Any]]) -> None:
    if not blocks:
        return
    messages.append({"role": "user", "content": list(blocks)})
    blocks.clear()


def _assistant_blocks(message: Message) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if message.thinking:
        thinking: dict[str, Any] = {"type": "thinking", "thinking": message.thinking}
        if message.thinking_signature:
            thinking["signature"] = message.thinking_signature
        blocks.append(thinking)
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    for call in message.tool_calls or []:
        blocks.append(
            {
                "type": "tool_use",
                "id": call.id,
                "name": call.name,
                "input": call.arguments,
            }
        )
    return blocks


def _tool_schema(tool: Tool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.arguments,
    }


def _completion(payload: dict[str, Any]) -> Completion:
    reason = payload.get("stop_reason")
    text, calls = _read_content(payload.get("content"))
    if reason in _TRUNCATED:
        return Completion(message=text, stop_reason=StopReason.MAX_TOKENS)
    if reason == "refusal" or reason in _END_TURN:
        return Completion(
            message=text,
            tool_calls=None if reason == "refusal" else calls,
            stop_reason=StopReason.END_TURN,
        )
    if reason == "tool_use":
        return Completion(
            message=text,
            tool_calls=calls,
            stop_reason=StopReason.END_TURN,
        )
    raise RuntimeError(f"unexpected stop_reason: {reason}")


def _read_content(content: Any) -> tuple[str, list[ToolCall] | None]:
    if isinstance(content, str):
        return content, None
    if not isinstance(content, list):
        return "", None
    texts: list[str] = []
    calls: list[ToolCall] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            texts.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use" and isinstance(block.get("name"), str):
            raw_input = block.get("input")
            arguments = dict(raw_input) if isinstance(raw_input, dict) else {}
            call_id = block.get("id")
            calls.append(
                ToolCall(
                    name=block["name"],
                    arguments=arguments,
                    id=call_id if isinstance(call_id, str) else None,
                )
            )
    return "".join(texts), calls or None


async def _completion_from_events(
    provider: ClaudeProvider,
    events: AsyncIterator[dict[str, Any]],
) -> Completion:
    texts: list[str] = []
    thinking: list[str] = []
    signature: list[str] = []
    calls: list[ToolCall] = []
    current: dict[str, Any] | None = None
    stop_reason: str | None = None
    async for event in events:
        if event.get("type") == "error":
            raise RuntimeError(f"Claude stream error: {event}")
        kind = event.get("type")
        if kind == "content_block_start":
            current = _open_tool(provider, calls, current, event.get("content_block"))
        elif kind == "content_block_delta":
            _apply_delta(
                provider,
                texts,
                thinking,
                signature,
                current,
                event.get("delta"),
            )
        elif kind == "content_block_stop":
            current = _close_tool(provider, calls, current)
        elif kind == "message_delta":
            delta = event.get("delta")
            if isinstance(delta, dict):
                reason = delta.get("stop_reason")
                if isinstance(reason, str):
                    stop_reason = reason
    current = _close_tool(provider, calls, current)
    content: list[dict[str, Any]] = []
    text = "".join(texts)
    if text:
        content.append({"type": "text", "text": text})
    for call in calls:
        content.append(
            {
                "type": "tool_use",
                "id": call.id,
                "name": call.name,
                "input": call.arguments,
            }
        )
    completion = _completion({"stop_reason": stop_reason, "content": content})
    return Completion(
        message=completion.message,
        tool_calls=completion.tool_calls,
        stop_reason=completion.stop_reason,
        thinking="".join(thinking),
        thinking_signature="".join(signature) or None,
    )


def _open_tool(
    provider: ClaudeProvider,
    calls: list[ToolCall],
    current: dict[str, Any] | None,
    block: Any,
) -> dict[str, Any] | None:
    _close_tool(provider, calls, current)
    if not isinstance(block, dict) or block.get("type") != "tool_use":
        return None
    name = block.get("name")
    tool_name = name if isinstance(name, str) else ""
    call_id = block.get("id")
    return {
        "name": tool_name,
        "id": call_id if isinstance(call_id, str) else None,
        "json": "",
    }


def _apply_delta(
    provider: ClaudeProvider,
    texts: list[str],
    thinking: list[str],
    signature: list[str],
    current: dict[str, Any] | None,
    delta: Any,
) -> None:
    if not isinstance(delta, dict):
        return
    kind = delta.get("type")
    if kind == "text_delta":
        piece = str(delta.get("text") or "")
        texts.append(piece)
        provider.emit_delta(DeltaKind.TEXT, piece)
    elif kind == "thinking_delta":
        piece = str(delta.get("thinking") or "")
        thinking.append(piece)
        provider.emit_delta(DeltaKind.THINKING, piece)
    elif kind == "signature_delta":
        signature.append(str(delta.get("signature") or ""))
    elif kind == "input_json_delta" and current is not None:
        current["json"] += str(delta.get("partial_json") or "")


def _close_tool(
    provider: ClaudeProvider,
    calls: list[ToolCall],
    current: dict[str, Any] | None,
) -> None:
    if current is None or not current.get("name"):
        return
    name = str(current["name"])
    arguments = _parse_tool_input(str(current.get("json") or ""))
    calls.append(
        ToolCall(
            name=name,
            arguments=arguments,
            id=current.get("id") if isinstance(current.get("id"), str) else None,
        )
    )
    provider.emit_delta(DeltaKind.TOOL, format_tool_use(name, arguments))


def _parse_tool_input(raw: str) -> dict:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _sse_events(response: Any) -> Iterator[dict[str, Any]]:
    for raw in response:
        line = raw.decode(errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Claude response was not JSON") from exc
        if isinstance(payload, dict):
            yield payload


def _http_error(exc: urllib.error.HTTPError) -> RuntimeError:
    detail = exc.read().decode(errors="replace")
    return RuntimeError(f"Claude HTTP {exc.code}: {detail}")


async def _iter_queue(
    produce: Callable[[asyncio.Queue[Any], asyncio.AbstractEventLoop], None],
) -> AsyncIterator[dict[str, Any]]:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()

    def run() -> None:
        produce(queue, loop)

    worker = asyncio.create_task(asyncio.to_thread(run))
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        await worker


def _decode_payload(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Claude response was not JSON") from exc
    if not isinstance(payload, dict):
        raise TypeError("Claude response was not JSON")
    return payload
