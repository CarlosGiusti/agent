import io
import json
import urllib.error
import urllib.request
from email.message import EmailMessage

import pytest

from lib.agents.claude import THINKING_EFFORT, ClaudeProvider, MessagesHttp
from lib.agents.provider import DeltaKind, StopReason
from lib.agents.types import Message, ToolCall
from lib.tools.tool import Tool


class ScriptedTransport:
    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.bodies: list[dict] = []

    async def events(self, body: dict):
        self.bodies.append(body)
        if not self._responses:
            raise AssertionError("unexpected post")
        payload = self._responses.pop(0)
        for event in _events_from_payload(payload):
            yield event


def _events_from_payload(payload: dict) -> list[dict]:
    content = payload.get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        content = []
    events: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "thinking":
            events.append(
                {
                    "type": "content_block_delta",
                    "delta": {
                        "type": "thinking_delta",
                        "thinking": str(block.get("thinking") or ""),
                    },
                }
            )
            if block.get("signature"):
                events.append(
                    {
                        "type": "content_block_delta",
                        "delta": {
                            "type": "signature_delta",
                            "signature": str(block["signature"]),
                        },
                    }
                )
        elif kind == "tool_use":
            events.append(
                {
                    "type": "content_block_start",
                    "content_block": {
                        "type": "tool_use",
                        "id": block.get("id"),
                        "name": block.get("name"),
                    },
                }
            )
            events.append(
                {
                    "type": "content_block_delta",
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(block.get("input") or {}),
                    },
                }
            )
            events.append({"type": "content_block_stop"})
        else:
            events.append(
                {
                    "type": "content_block_delta",
                    "delta": {
                        "type": "text_delta",
                        "text": str(block.get("text") or ""),
                    },
                }
            )
    events.append(
        {
            "type": "message_delta",
            "delta": {"stop_reason": payload.get("stop_reason")},
        }
    )
    return events


class RecordingOpener:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request):
        self.requests.append(request)
        if self.status >= 400:
            raise urllib.error.HTTPError(
                request.full_url,
                self.status,
                "error",
                EmailMessage(),
                io.BytesIO(self.body),
            )
        return _Response(self.body)


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _end_turn(text: str) -> dict:
    return {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": text}],
    }


def _read_file_tool() -> Tool:
    return Tool(
        name="read_file",
        description="Read a file",
        arguments={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        function=lambda **kwargs: "",
    )


async def test_complete_puts_rules_in_system_and_hook_text_before_the_prompt():
    transport = ScriptedTransport([_end_turn("ok")])
    provider = ClaudeProvider(
        model="claude-sonnet-4-5",
        api_key="test-key",
        transport=transport,
    )

    result = await provider.complete(
        [
            Message(role="rule", content="GLOBAL_OK"),
            Message(role="rule", content="skills:\nping-skill: when PING"),
            Message(role="hook", content="HOOK_BEFORE"),
            Message(role="user", content="hola"),
        ],
        [],
    )

    body = transport.bodies[0]
    assert body["model"] == "claude-sonnet-4-5"
    assert body["max_tokens"] == 4096
    assert body["system"] == "GLOBAL_OK\n\nskills:\nping-skill: when PING"
    assert "HOOK_BEFORE" not in body["system"]
    assert "tools" not in body
    assert body["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "HOOK_BEFORE"},
                {"type": "text", "text": "hola"},
            ],
        }
    ]
    assert result.message == "ok"
    assert result.stop_reason is StopReason.END_TURN
    assert result.tool_calls is None


async def test_complete_groups_tool_results_and_sends_the_tool_schema():
    transport = ScriptedTransport([_end_turn("done")])
    provider = ClaudeProvider(
        model="claude-sonnet-4-5", api_key="k", transport=transport
    )
    call = ToolCall(name="read_file", arguments={"path": "a.txt"}, id="call-1")
    other = ToolCall(name="read_file", arguments={"path": "b.txt"}, id="call-2")

    await provider.complete(
        [
            Message(role="user", content="hi"),
            Message(role="assistant", content="looking", tool_calls=[call, other]),
            Message(
                role="tool",
                content="file text",
                tool_name="read_file",
                tool_call_id="call-1",
            ),
            Message(
                role="tool",
                content="other",
                tool_name="read_file",
                tool_call_id="call-2",
            ),
            Message(role="hook", content="HOOK_BEFORE"),
            Message(role="user", content="sigue"),
        ],
        [_read_file_tool()],
    )

    body = transport.bodies[0]
    assert "system" not in body
    assert body["tools"] == [
        {
            "name": "read_file",
            "description": "Read a file",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    ]
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "looking"},
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "read_file",
                    "input": {"path": "a.txt"},
                },
                {
                    "type": "tool_use",
                    "id": "call-2",
                    "name": "read_file",
                    "input": {"path": "b.txt"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call-1",
                    "content": "file text",
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "call-2",
                    "content": "other",
                },
                {"type": "text", "text": "HOOK_BEFORE"},
                {"type": "text", "text": "sigue"},
            ],
        },
    ]


async def test_complete_returns_tool_calls_for_tool_use():
    transport = ScriptedTransport(
        [
            {
                "stop_reason": "tool_use",
                "content": [
                    {"type": "text", "text": "reading"},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "read_file",
                        "input": {"path": "a.txt"},
                    },
                ],
            }
        ]
    )
    provider = ClaudeProvider(
        model="claude-sonnet-4-5", api_key="k", transport=transport
    )

    result = await provider.complete([Message(role="user", content="hi")], [])

    assert result.message == "reading"
    assert result.stop_reason is StopReason.END_TURN
    assert result.tool_calls == [
        ToolCall(name="read_file", arguments={"path": "a.txt"}, id="toolu_1")
    ]


@pytest.mark.parametrize("stop_reason", ["end_turn", "stop_sequence"])
async def test_complete_maps_a_finished_reply_to_end_turn(stop_reason: str):
    transport = ScriptedTransport(
        [
            {
                "stop_reason": stop_reason,
                "content": [{"type": "text", "text": "listo"}],
            }
        ]
    )
    provider = ClaudeProvider(
        model="claude-sonnet-4-5", api_key="k", transport=transport
    )

    result = await provider.complete([Message(role="user", content="hi")], [])

    assert result.message == "listo"
    assert result.stop_reason is StopReason.END_TURN
    assert result.tool_calls is None


@pytest.mark.parametrize(
    "stop_reason",
    ["max_tokens", "model_context_window_exceeded"],
)
async def test_complete_drops_tool_calls_when_the_reply_is_truncated(stop_reason: str):
    transport = ScriptedTransport(
        [
            {
                "stop_reason": stop_reason,
                "content": [
                    {"type": "text", "text": "partial"},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "read_file",
                        "input": {},
                    },
                ],
            }
        ]
    )
    provider = ClaudeProvider(
        model="claude-sonnet-4-5", api_key="k", transport=transport
    )

    result = await provider.complete([Message(role="user", content="hi")], [])

    assert result.message == "partial"
    assert result.stop_reason is StopReason.MAX_TOKENS
    assert result.tool_calls is None


async def test_complete_returns_refusal_text_as_end_turn():
    transport = ScriptedTransport(
        [
            {
                "stop_reason": "refusal",
                "content": [
                    {"type": "text", "text": "no"},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "read_file",
                        "input": {},
                    },
                ],
            }
        ]
    )
    provider = ClaudeProvider(
        model="claude-sonnet-4-5", api_key="k", transport=transport
    )

    result = await provider.complete([Message(role="user", content="hi")], [])

    assert result.message == "no"
    assert result.stop_reason is StopReason.END_TURN
    assert result.tool_calls is None


@pytest.mark.parametrize("stop_reason", ["pause_turn", "banana"])
async def test_complete_raises_on_an_unhandled_stop_reason(stop_reason: str):
    transport = ScriptedTransport(
        [{"stop_reason": stop_reason, "content": [{"type": "text", "text": "x"}]}]
    )
    provider = ClaudeProvider(
        model="claude-sonnet-4-5", api_key="k", transport=transport
    )

    with pytest.raises(RuntimeError, match="unexpected stop_reason"):
        await provider.complete([Message(role="user", content="hi")], [])


async def test_complete_raises_before_the_request_when_the_key_is_missing():
    transport = ScriptedTransport([])
    provider = ClaudeProvider(
        model="claude-sonnet-4-5", api_key="", transport=transport
    )

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        await provider.complete([Message(role="user", content="hi")], [])

    assert transport.bodies == []


async def test_messages_http_posts_the_body_and_returns_the_payload():
    opener = RecordingOpener(200, json.dumps(_end_turn("ok")).encode())
    http = MessagesHttp(api_key="test-key", opener=opener)

    payload = await http.post({"model": "claude-sonnet-4-5", "messages": []})

    request = opener.requests[0]
    assert payload == _end_turn("ok")
    assert request.full_url == "https://api.anthropic.com/v1/messages"
    assert request.get_method() == "POST"
    assert request.get_header("X-api-key") == "test-key"
    assert request.get_header("Anthropic-version") == "2023-06-01"
    assert request.get_header("Content-type") == "application/json"
    assert isinstance(request.data, bytes)
    assert json.loads(request.data) == {"model": "claude-sonnet-4-5", "messages": []}


async def test_complete_streams_thinking_text_and_one_tool_name():
    transport = ScriptedTransport(
        [
            {
                "stop_reason": "tool_use",
                "content": [
                    {"type": "thinking", "thinking": "hmm", "signature": "sig"},
                    {"type": "text", "text": "reading"},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "read_file",
                        "input": {"path": "a.txt"},
                    },
                ],
            }
        ]
    )
    provider = ClaudeProvider(
        model="claude-sonnet-4-5", api_key="k", transport=transport
    )
    deltas: list[tuple[DeltaKind, str]] = []
    provider.on_delta = lambda delta: deltas.append((delta.kind, delta.text))

    result = await provider.complete([Message(role="user", content="hi")], [])

    assert transport.bodies[0]["stream"] is True
    assert transport.bodies[0]["thinking"] == {
        "type": "adaptive",
        "display": "summarized",
    }
    assert transport.bodies[0]["output_config"] == {"effort": THINKING_EFFORT}
    assert deltas == [
        (DeltaKind.THINKING, "hmm"),
        (DeltaKind.TEXT, "reading"),
        (DeltaKind.TOOL, 'read_file path="a.txt"'),
    ]
    assert result.message == "reading"
    assert result.thinking == "hmm"
    assert result.thinking_signature == "sig"
    assert result.stop_reason is StopReason.END_TURN
    assert result.tool_calls == [
        ToolCall(name="read_file", arguments={"path": "a.txt"}, id="toolu_1")
    ]


async def test_complete_replays_thinking_before_the_answer():
    transport = ScriptedTransport([_end_turn("done")])
    provider = ClaudeProvider(
        model="claude-sonnet-4-5", api_key="k", transport=transport
    )
    call = ToolCall(name="read_file", arguments={"path": "a.txt"}, id="call-1")

    await provider.complete(
        [
            Message(
                role="assistant",
                content="looking",
                thinking="hmm",
                thinking_signature="sig",
                tool_calls=[call],
            )
        ],
        [],
    )

    assert transport.bodies[0]["messages"][0]["content"][0] == {
        "type": "thinking",
        "thinking": "hmm",
        "signature": "sig",
    }


class _Lines:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class LineOpener:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request):
        self.requests.append(request)
        return _Lines(self.lines)


async def test_messages_http_reads_sse_events():
    opener = LineOpener(
        [
            b"event: content_block_delta\n",
            b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"ok"}}\n',
            b"\n",
            b"data: [DONE]\n",
        ]
    )
    http = MessagesHttp(api_key="test-key", opener=opener)

    events = [event async for event in http.events({"model": "m"})]

    assert events == [
        {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "ok"},
        }
    ]
    body = opener.requests[0].data
    assert isinstance(body, bytes)
    assert json.loads(body) == {"model": "m"}


async def test_messages_http_events_raise_on_401():
    opener = RecordingOpener(401, b'{"error":"bad"}')
    http = MessagesHttp(api_key="test-key", opener=opener)

    with pytest.raises(RuntimeError, match="Claude HTTP 401"):
        async for _event in http.events({"model": "m"}):
            pass


async def test_messages_http_raises_on_401():
    opener = RecordingOpener(401, b'{"error":"bad"}')
    http = MessagesHttp(api_key="test-key", opener=opener)

    with pytest.raises(RuntimeError, match="Claude HTTP 401"):
        await http.post({"model": "claude-sonnet-4-5"})
