import asyncio
from collections.abc import Callable, Iterator
from typing import Any

from ollama import chat

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

Chat = Callable[..., Iterator[Any]]


class OllamaProvider(Provider):
    def __init__(self, model: str, chat: Chat | None = None):
        super().__init__(model)
        self._chat = chat or _default_chat

    def to_provider_messages(self, context: list[Message]) -> list[dict[str, Any]]:
        rules = [message for message in context if message.role == RULE_ROLE]
        chat = [message for message in context if message.role != RULE_ROLE]
        messages: list[dict[str, Any]] = []
        if rules:
            messages.append(
                {
                    "role": "system",
                    "content": "\n\n".join(message.content for message in rules),
                }
            )
        for message in chat:
            role = "user" if message.role == HOOK_ROLE else message.role
            item: dict[str, Any] = {"role": role, "content": message.content}
            if message.tool_name:
                item["tool_name"] = message.tool_name
            if message.tool_call_id:
                item["tool_call_id"] = message.tool_call_id
            if message.thinking:
                item["thinking"] = message.thinking
            if message.tool_calls:
                tool_calls = []
                for call in message.tool_calls:
                    tool_call: dict[str, Any] = {
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": call.arguments,
                        },
                    }
                    if call.id:
                        tool_call["id"] = call.id
                    tool_calls.append(tool_call)
                item["tool_calls"] = tool_calls
            messages.append(item)
        return messages

    def _to_ollama_tools(self, tools: list[Tool]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.arguments,
                },
            }
            for tool in tools
        ]

    async def complete(self, context: list[Message], tools: list[Tool]) -> Completion:
        text: list[str] = []
        thinking: list[str] = []
        calls: list[ToolCall] = []
        done_reason: str | None = None
        async for chunk in self._stream(context, tools):
            message = chunk.message
            piece = message.thinking or ""
            if piece:
                thinking.append(piece)
                self.emit_delta(DeltaKind.THINKING, piece)
            piece = message.content or ""
            if piece:
                text.append(piece)
                self.emit_delta(DeltaKind.TEXT, piece)
            _absorb_tool_calls(calls, message.tool_calls)
            if chunk.done_reason:
                done_reason = chunk.done_reason
        for call in calls:
            if call.name:
                self.emit_delta(
                    DeltaKind.TOOL, format_tool_use(call.name, call.arguments)
                )
        stop_reason = (
            StopReason.MAX_TOKENS if done_reason == "length" else StopReason.END_TURN
        )
        return Completion(
            message="".join(text),
            tool_calls=calls or None,
            stop_reason=stop_reason,
            thinking="".join(thinking),
        )

    async def _stream(self, context: list[Message], tools: list[Tool]):
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()

        def produce() -> None:
            try:
                for chunk in self._chat(
                    model=self.model,
                    messages=self.to_provider_messages(context),
                    tools=self._to_ollama_tools(tools),
                    stream=True,
                    think=True,
                ):
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
                loop.call_soon_threadsafe(queue.put_nowait, None)
            except BaseException as exc:  # noqa: BLE001 — hand the chat failure to the loop
                loop.call_soon_threadsafe(queue.put_nowait, exc)

        worker = asyncio.create_task(asyncio.to_thread(produce))
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


def _default_chat(**kwargs: Any) -> Iterator[Any]:
    return chat(**kwargs)


def _absorb_tool_calls(calls: list[ToolCall], raw_calls: Any) -> None:
    if not raw_calls:
        return
    for index, raw in enumerate(raw_calls):
        name, arguments, call_id = _tool_parts(raw)
        if index >= len(calls):
            calls.append(ToolCall(name=name, arguments=arguments, id=call_id))
            continue
        current = calls[index]
        if name:
            current.name = name
        if arguments:
            current.arguments = arguments
        if call_id:
            current.id = call_id


def _tool_parts(raw: Any) -> tuple[str, dict, str | None]:
    function = raw.function
    name = function.name or ""
    arguments = function.arguments
    if not isinstance(arguments, dict):
        arguments = {}
    call_id = getattr(raw, "id", None)
    return name, dict(arguments), call_id if isinstance(call_id, str) else None
