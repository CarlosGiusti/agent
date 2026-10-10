import pytest

from lib.agents.ollama import OllamaProvider
from lib.agents.provider import Delta, DeltaKind, StopReason
from lib.agents.types import Message, ToolCall


def test_to_provider_messages_folds_rules_into_one_system_message():
    provider = OllamaProvider(model="qwen3:8b")
    context = [
        Message(role="rule", content="GLOBAL_OK"),
        Message(role="user", content="hi"),
        Message(role="rule", content="WORKSPACE_OK"),
    ]

    messages = provider.to_provider_messages(context)

    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[0]["content"] == "GLOBAL_OK\n\nWORKSPACE_OK"
    assert messages[1] == {"role": "user", "content": "hi"}
    assert all(message["role"] != "rule" for message in messages)


def test_to_provider_messages_sends_hook_text_as_user_outside_the_system():
    provider = OllamaProvider(model="qwen3:8b")
    context = [
        Message(role="rule", content="GLOBAL_OK"),
        Message(role="hook", content="HOOK_BEFORE"),
        Message(role="user", content="hola"),
    ]

    messages = provider.to_provider_messages(context)

    assert [message["role"] for message in messages] == ["system", "user", "user"]
    assert messages[0]["content"] == "GLOBAL_OK"
    assert "HOOK_BEFORE" not in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "HOOK_BEFORE"}
    assert messages[2] == {"role": "user", "content": "hola"}


def test_to_provider_messages_leaves_chat_unchanged_without_rules():
    provider = OllamaProvider(model="qwen3:8b")
    call = ToolCall(name="read_file", arguments={"path": "a.txt"}, id="call-1")
    context = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="", tool_calls=[call]),
        Message(
            role="tool",
            content="file text",
            tool_name="read_file",
            tool_call_id="call-1",
        ),
    ]

    messages = provider.to_provider_messages(context)

    assert messages[0] == {"role": "user", "content": "hi"}
    assert messages[1]["role"] == "assistant"
    assert messages[1]["tool_calls"][0]["id"] == "call-1"
    assert messages[1]["tool_calls"][0]["function"]["name"] == "read_file"
    assert "thinking" not in messages[1]
    assert messages[2] == {
        "role": "tool",
        "content": "file text",
        "tool_name": "read_file",
        "tool_call_id": "call-1",
    }
    assert all(message["role"] != "system" for message in messages)


def test_to_provider_messages_sends_thinking_back_on_the_assistant():
    provider = OllamaProvider(model="qwen3:8b")
    call = ToolCall(name="read_file", arguments={"path": "a.txt"}, id="call-1")

    messages = provider.to_provider_messages(
        [
            Message(
                role="assistant",
                content="ok",
                thinking="hmm",
                tool_calls=[call],
            )
        ]
    )

    assert messages[0]["thinking"] == "hmm"
    assert messages[0]["content"] == "ok"


class _Function:
    def __init__(self, name: str, arguments: dict) -> None:
        self.name = name
        self.arguments = arguments


class _Call:
    def __init__(self, name: str, arguments: dict, call_id: str | None = None) -> None:
        self.function = _Function(name, arguments)
        self.id = call_id


class _Chunk:
    def __init__(
        self,
        thinking: str = "",
        content: str = "",
        tool_calls: list[_Call] | None = None,
        done_reason: str | None = None,
    ) -> None:
        self.message = _FunctionChunk(thinking, content, tool_calls)
        self.done_reason = done_reason


class _FunctionChunk:
    def __init__(
        self,
        thinking: str,
        content: str,
        tool_calls: list[_Call] | None,
    ) -> None:
        self.thinking = thinking
        self.content = content
        self.tool_calls = tool_calls


async def test_complete_emits_thinking_text_and_one_tool_name():
    calls: list[dict] = []

    def chat(**kwargs):
        calls.append(kwargs)
        return iter(
            [
                _Chunk(thinking="hmm"),
                _Chunk(thinking=" more"),
                _Chunk(content="ok"),
                _Chunk(
                    tool_calls=[_Call("read_file", {"path": "a"}, "c1")],
                    done_reason="stop",
                ),
                _Chunk(tool_calls=[_Call("read_file", {"path": "a.txt"}, "c1")]),
            ]
        )

    provider = OllamaProvider(model="qwen3:8b", chat=chat)
    deltas: list[Delta] = []
    provider.on_delta = deltas.append

    result = await provider.complete([Message(role="user", content="hi")], [])

    assert calls[0]["stream"] is True
    assert calls[0]["think"] is True
    assert [(delta.kind, delta.text) for delta in deltas] == [
        (DeltaKind.THINKING, "hmm"),
        (DeltaKind.THINKING, " more"),
        (DeltaKind.TEXT, "ok"),
        (DeltaKind.TOOL, 'read_file path="a.txt"'),
    ]
    assert result.message == "ok"
    assert result.thinking == "hmm more"
    assert result.stop_reason is StopReason.END_TURN
    assert result.tool_calls == [
        ToolCall(name="read_file", arguments={"path": "a.txt"}, id="c1")
    ]


async def test_complete_raises_when_chat_fails():
    def chat(**_kwargs):
        raise RuntimeError("ollama down")

    provider = OllamaProvider(model="qwen3:8b", chat=chat)

    with pytest.raises(RuntimeError, match="ollama down"):
        await provider.complete([Message(role="user", content="hi")], [])
