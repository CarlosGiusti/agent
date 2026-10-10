import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from lib.agents.types import Message, ToolCall
from lib.tools.tool import Tool


class StopReason(StrEnum):
    END_TURN = "end_turn"
    MAX_TOKENS = "max_tokens"
    MAX_ITERATIONS = "max_iterations"
    ERROR = "error"


class DeltaKind(StrEnum):
    THINKING = "thinking"
    TEXT = "text"
    TOOL = "tool"


_MAX_ARG_CHARS = 200


def format_tool_use(name: str, arguments: dict) -> str:
    if not arguments:
        return name
    parts = [f"{key}={_format_arg(value)}" for key, value in arguments.items()]
    return f"{name} {' '.join(parts)}"


def _format_arg(value: object) -> str:
    if isinstance(value, str) and len(value) > _MAX_ARG_CHARS:
        value = value[:_MAX_ARG_CHARS] + "..."
    return json.dumps(value, ensure_ascii=False)


@dataclass(frozen=True)
class Delta:
    kind: DeltaKind
    text: str


@dataclass(frozen=True)
class Completion:
    message: str
    tool_calls: list[ToolCall] | None = None
    stop_reason: StopReason = StopReason.END_TURN
    thinking: str = ""
    thinking_signature: str | None = None


class Provider(ABC):
    def __init__(self, model: str):
        self.model = model
        self.on_delta: Callable[[Delta], None] | None = None

    def emit_delta(self, kind: DeltaKind, text: str) -> None:
        if self.on_delta is None or text == "":
            return
        self.on_delta(Delta(kind=kind, text=text))

    @abstractmethod
    async def complete(self, context: list[Message], tools: list[Tool]) -> Completion:
        pass
