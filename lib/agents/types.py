from dataclasses import dataclass


@dataclass
class ToolCall:
    name: str
    arguments: dict
    id: str | None = None


@dataclass
class Message:
    role: str
    content: str = ""
    tool_name: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    thinking: str | None = None
    thinking_signature: str | None = None
