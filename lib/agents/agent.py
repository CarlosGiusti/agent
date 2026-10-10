from abc import ABC
from dataclasses import dataclass
from uuid import uuid4

from lib.agents.provider import Completion, Delta, Provider, StopReason
from lib.agents.types import Message
from lib.events.event_system import EventEmitter, EventSystem, EventType
from lib.hooks.hooks import HOOK_ROLE, ClosedTurn, NullHook, TurnHook
from lib.tools.tool import Tool


@dataclass(frozen=True)
class AgentConfig:
    max_iterations: int = 5

    def __post_init__(self):
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")


def _assistant_message(completion: Completion, *, keep_tools: bool) -> Message:
    return Message(
        role="assistant",
        content=completion.message,
        tool_calls=completion.tool_calls if keep_tools else None,
        thinking=completion.thinking or None,
        thinking_signature=completion.thinking_signature,
    )


def _turn_finished_data(stop_reason: StopReason) -> dict:
    return {
        "stop_reason": stop_reason.value,
        "completed": stop_reason is StopReason.END_TURN,
    }


class Agent(ABC):
    def __init__(
        self,
        provider: Provider,
        context: list[Message] | None = None,
        tools: list[Tool] | None = None,
        config: AgentConfig | None = None,
        events: EventEmitter | None = None,
        hooks: TurnHook | None = None,
    ):
        self.context = context or []
        self.tools = tools or []
        self.config = config or AgentConfig()
        self.provider = provider
        self.events = events or EventSystem()
        self.hooks = hooks or NullHook()

    async def _call_tool(self, name: str, args: dict) -> str:
        self.events.emit(EventType.TOOL_STARTED, {"name": name})
        tool = next((item for item in self.tools if item.name == name), None)
        if tool is None:
            result = f"Unknown tool: {name}"
            self.events.emit(
                EventType.TOOL_FINISHED,
                {"name": name},
                ok=False,
                error=result,
            )
            return result
        try:
            result = str(await tool.call(**args))
        except Exception as exc:  # noqa: BLE001 — tool failures must stay in the loop
            result = f"Error calling {name}: {exc}"
            self.events.emit(
                EventType.TOOL_FINISHED,
                {"name": name},
                ok=False,
                error=str(exc),
            )
            return result

        self.events.emit(EventType.TOOL_FINISHED, {"name": name})
        return result

    async def _loop(self):
        for iteration in range(self.config.max_iterations):
            step = iteration + 1
            self.events.emit(
                EventType.PROVIDER_STARTED,
                {
                    "model": self.provider.model,
                    "iteration": step,
                    "context_size": len(self.context),
                },
            )
            self.provider.on_delta = lambda delta, step=step: self._relay_delta(
                step, delta
            )
            try:
                completion = await self.provider.complete(self.context, self.tools)
            except Exception as exc:
                self.events.emit(
                    EventType.PROVIDER_FINISHED,
                    {"model": self.provider.model, "iteration": step},
                    ok=False,
                    error=str(exc),
                )
                raise
            finally:
                self.provider.on_delta = None

            self.events.emit(
                EventType.PROVIDER_FINISHED,
                {
                    "model": self.provider.model,
                    "iteration": step,
                    "tool_calls": [call.name for call in completion.tool_calls or []],
                },
            )

            if completion.stop_reason is StopReason.MAX_TOKENS:
                self.context.append(_assistant_message(completion, keep_tools=False))
                return completion.message, StopReason.MAX_TOKENS

            self.context.append(_assistant_message(completion, keep_tools=True))

            if not completion.tool_calls:
                return completion.message, StopReason.END_TURN

            for call in completion.tool_calls:
                if not call.id:
                    call.id = str(uuid4())
                name = call.name
                result = await self._call_tool(name, dict(call.arguments))
                self.context.append(
                    Message(
                        role="tool",
                        content=result,
                        tool_name=name,
                        tool_call_id=call.id,
                    )
                )

        return "", StopReason.MAX_ITERATIONS

    def _relay_delta(self, iteration: int, delta: Delta) -> None:
        self.events.emit(
            EventType.PROVIDER_DELTA,
            {
                "kind": delta.kind.value,
                "text": delta.text,
                "iteration": iteration,
            },
        )

    async def _before_turn(self, prompt: str) -> str:
        try:
            return await self.hooks.before_turn(prompt)
        except Exception as exc:  # noqa: BLE001 — a hook failure must not stop the turn
            self.events.emit(
                EventType.HOOK_FINISHED,
                {"name": "before_turn"},
                ok=False,
                error=str(exc),
            )
            return ""

    async def _after_turn(self, stop_reason: StopReason, error: str | None) -> None:
        turn = ClosedTurn(
            stop_reason=stop_reason.value,
            completed=stop_reason is StopReason.END_TURN,
            error=error,
            messages=tuple(self.context),
        )
        try:
            await self.hooks.after_turn(turn)
        except Exception as exc:  # noqa: BLE001 — a hook failure must not stop the turn
            self.events.emit(
                EventType.HOOK_FINISHED,
                {"name": "after_turn"},
                ok=False,
                error=str(exc),
            )

    async def send(self, message: Message):
        self.context.append(message)
        prefix = await self._before_turn(message.content)
        if prefix:
            user_message = self.context.pop()
            self.context.append(Message(role=HOOK_ROLE, content=prefix))
            self.context.append(user_message)
        self.events.emit(EventType.TURN_STARTED)
        try:
            result, stop_reason = await self._loop()
        except Exception as exc:
            await self._after_turn(StopReason.ERROR, str(exc))
            self.events.emit(
                EventType.TURN_FINISHED,
                _turn_finished_data(StopReason.ERROR),
                ok=False,
                error=str(exc),
            )
            raise

        await self._after_turn(stop_reason, None)
        self.events.emit(EventType.TURN_FINISHED, _turn_finished_data(stop_reason))
        return result
