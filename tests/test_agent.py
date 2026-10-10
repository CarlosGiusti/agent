from collections.abc import Sequence

import pytest

from lib.agents.agent import Agent, AgentConfig
from lib.agents.provider import Completion, Delta, DeltaKind, Provider, StopReason
from lib.agents.types import Message, ToolCall
from lib.events.event_system import Event, EventType
from lib.hooks.hooks import ClosedTurn
from lib.tools.tool import Tool


class SequenceProvider(Provider):
    """Returns a preset list of completions, one per `complete` call."""

    def __init__(self, completions: Sequence[Completion], model: str = "mock-model"):
        super().__init__(model)
        self._completions = list(completions)
        self.complete_calls: list[list[Message]] = []

    async def complete(self, context: list[Message], tools: list[Tool]) -> Completion:
        self.complete_calls.append(list(context))
        if not self._completions:
            raise AssertionError("Provider.complete called more times than expected")
        return self._completions.pop(0)


class ConstantProvider(Provider):
    """Always returns the same completion (used to exhaust max iterations)."""

    def __init__(self, completion: Completion, model: str = "mock-model"):
        super().__init__(model)
        self._completion = completion
        self.call_count = 0

    async def complete(self, context: list[Message], tools: list[Tool]) -> Completion:
        self.call_count += 1
        return self._completion


class FailingProvider(Provider):
    def __init__(self, error: Exception, model: str = "mock-model"):
        super().__init__(model)
        self._error = error
        self.complete_calls: list[list[Message]] = []

    async def complete(self, context: list[Message], tools: list[Tool]) -> Completion:
        self.complete_calls.append(list(context))
        raise self._error


class RecordingEvents:
    def __init__(self) -> None:
        self.emits: list[Event] = []

    def emit(
        self,
        event_type: str,
        data: dict | None = None,
        *,
        ok: bool = True,
        error: str | None = None,
    ) -> None:
        self.emits.append(
            Event(event_type=event_type, data=data or {}, ok=ok, error=error)
        )


def _mock_tool(name: str, function) -> Tool:
    return Tool(
        name=name,
        description=f"Mock {name}",
        arguments={
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        },
        function=function,
    )


def _turn_finished(
    stop_reason: StopReason,
    *,
    ok: bool = True,
    error: str | None = None,
) -> Event:
    return Event(
        event_type=EventType.TURN_FINISHED,
        data={
            "stop_reason": stop_reason.value,
            "completed": stop_reason is StopReason.END_TURN,
        },
        ok=ok,
        error=error,
    )


class RecordingHook:
    def __init__(
        self,
        text: str = "",
        before_error: Exception | None = None,
        after_error: Exception | None = None,
    ) -> None:
        self.text = text
        self.before_error = before_error
        self.after_error = after_error
        self.before_calls: list[str] = []
        self.after_calls: list[ClosedTurn] = []

    async def before_turn(self, prompt: str) -> str:
        self.before_calls.append(prompt)
        if self.before_error is not None:
            raise self.before_error
        return self.text

    async def after_turn(self, turn: ClosedTurn) -> None:
        self.after_calls.append(turn)
        if self.after_error is not None:
            raise self.after_error


def _recording_tool(name: str) -> tuple[Tool, list[dict]]:
    calls: list[dict] = []

    async def function(**args) -> str:
        calls.append(args)
        return f"{name} ok"

    return _mock_tool(name, function), calls


async def test_send_forwards_rule_messages_ahead_of_the_user():
    provider = SequenceProvider([Completion(message="ok")])
    rules = [
        Message(role="rule", content="GLOBAL_OK"),
        Message(role="rule", content="WORKSPACE_OK"),
    ]
    agent = Agent(provider=provider, context=list(rules))

    await agent.send(Message(role="user", content="hi"))

    sent = provider.complete_calls[0]
    assert [message.role for message in sent[:3]] == ["rule", "rule", "user"]
    assert sent[0].content == "GLOBAL_OK"
    assert sent[1].content == "WORKSPACE_OK"
    assert sent[2].content == "hi"


async def test_send_returns_final_message_when_there_are_no_tool_calls():
    provider = SequenceProvider([Completion(message="hello world")])
    agent = Agent(provider=provider)

    result = await agent.send(Message(role="user", content="hi"))

    assert result == "hello world"
    assert len(provider.complete_calls) == 1
    assert agent.context[-1].role == "assistant"
    assert agent.context[-1].content == "hello world"
    assert agent.context[-1].tool_calls is None


async def test_send_calls_tool_then_returns_final_message():
    tool, calls = _recording_tool("lookup")
    provider = SequenceProvider(
        [
            Completion(
                message="need a tool",
                tool_calls=[ToolCall(name="lookup", arguments={"q": "weather"})],
            ),
            Completion(message="it's sunny"),
        ]
    )
    agent = Agent(provider=provider, tools=[tool])

    result = await agent.send(Message(role="user", content="weather?"))

    assert result == "it's sunny"
    assert calls == [{"q": "weather"}]
    assert len(provider.complete_calls) == 2

    tool_messages = [message for message in agent.context if message.role == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0].tool_name == "lookup"
    assert tool_messages[0].content == "lookup ok"

    second_context = provider.complete_calls[1]
    assert any(
        message.role == "tool" and message.content == "lookup ok"
        for message in second_context
    )


async def test_send_executes_multiple_tool_calls_in_one_turn():
    alpha, alpha_calls = _recording_tool("alpha")
    beta, beta_calls = _recording_tool("beta")
    provider = SequenceProvider(
        [
            Completion(
                message="",
                tool_calls=[
                    ToolCall(name="alpha", arguments={"n": 1}),
                    ToolCall(name="beta", arguments={"n": 2}),
                ],
            ),
            Completion(message="done"),
        ]
    )
    agent = Agent(provider=provider, tools=[alpha, beta])

    result = await agent.send(Message(role="user", content="go"))

    assert result == "done"
    assert alpha_calls == [{"n": 1}]
    assert beta_calls == [{"n": 2}]

    tool_names = [
        message.tool_name for message in agent.context if message.role == "tool"
    ]
    assert tool_names == ["alpha", "beta"]

    second_context = provider.complete_calls[1]
    names_before_final = [
        message.tool_name for message in second_context if message.role == "tool"
    ]
    assert names_before_final == ["alpha", "beta"]


async def test_send_pairs_tool_results_when_provider_omits_ids():
    tool, calls = _recording_tool("lookup")
    provider = SequenceProvider(
        [
            Completion(
                message="",
                tool_calls=[
                    ToolCall(name="lookup", arguments={"q": "a"}),
                    ToolCall(name="lookup", arguments={"q": "b"}),
                ],
            ),
            Completion(message="done"),
        ]
    )
    agent = Agent(provider=provider, tools=[tool])

    result = await agent.send(Message(role="user", content="go"))

    assert result == "done"
    assert calls == [{"q": "a"}, {"q": "b"}]
    assistant = next(message for message in agent.context if message.tool_calls)
    assert assistant.tool_calls is not None
    ids = [call.id for call in assistant.tool_calls]
    assert all(ids)
    assert ids[0] != ids[1]
    tool_messages = [message for message in agent.context if message.role == "tool"]
    assert [message.tool_call_id for message in tool_messages] == ids
    echoed = [
        message.tool_call_id
        for message in provider.complete_calls[1]
        if message.role == "tool"
    ]
    assert echoed == ids


async def test_send_keeps_tool_call_id_from_provider():
    tool, calls = _recording_tool("lookup")
    provider = SequenceProvider(
        [
            Completion(
                message="",
                tool_calls=[ToolCall(name="lookup", arguments={"q": "a"}, id="call-1")],
            ),
            Completion(message="done"),
        ]
    )
    agent = Agent(provider=provider, tools=[tool])

    await agent.send(Message(role="user", content="go"))

    assert calls == [{"q": "a"}]
    tool_message = next(message for message in agent.context if message.role == "tool")
    assert tool_message.tool_call_id == "call-1"
    assistant = next(message for message in agent.context if message.tool_calls)
    assert assistant.tool_calls is not None
    assert assistant.tool_calls[0].id == "call-1"


async def test_send_returns_when_max_tool_iterations_reached():
    tool, calls = _recording_tool("loop")
    provider = ConstantProvider(
        Completion(
            message="again",
            tool_calls=[ToolCall(name="loop", arguments={})],
        )
    )
    events = RecordingEvents()
    agent = Agent(
        provider=provider,
        tools=[tool],
        config=AgentConfig(max_iterations=2),
        events=events,
    )

    result = await agent.send(Message(role="user", content="start"))

    assert result == ""
    assert provider.call_count == 2
    assert len(calls) == 2
    assert events.emits[-1] == _turn_finished(StopReason.MAX_ITERATIONS)


async def test_send_continues_after_tool_call_error():
    async def explode(**_args) -> str:
        raise RuntimeError("disk full")

    tool = _mock_tool("write", explode)
    provider = SequenceProvider(
        [
            Completion(
                message="",
                tool_calls=[ToolCall(name="write", arguments={"path": "a"})],
            ),
            Completion(message="could not write"),
        ]
    )
    agent = Agent(provider=provider, tools=[tool])

    result = await agent.send(Message(role="user", content="write"))

    assert result == "could not write"
    assert len(provider.complete_calls) == 2

    error_messages = [message for message in agent.context if message.role == "tool"]
    assert error_messages[0].content == "Error calling write: disk full"
    assert error_messages[0].tool_name == "write"


class DeltaProvider(Provider):
    def __init__(self) -> None:
        super().__init__("mock-model")

    async def complete(self, context: list[Message], tools: list[Tool]) -> Completion:
        assert self.on_delta is not None
        self.on_delta(Delta(kind=DeltaKind.THINKING, text="hmm"))
        self.on_delta(Delta(kind=DeltaKind.TEXT, text="hi"))
        self.on_delta(Delta(kind=DeltaKind.TOOL, text="read_file"))
        return Completion(message="hi", thinking="hmm", thinking_signature="sig")


async def test_send_relays_deltas_and_keeps_thinking_on_the_assistant():
    provider = DeltaProvider()
    events = RecordingEvents()
    agent = Agent(provider=provider, events=events)

    result = await agent.send(Message(role="user", content="hi"))

    assert result == "hi"
    assert agent.context[-1].thinking == "hmm"
    assert agent.context[-1].thinking_signature == "sig"
    assert agent.context[-1].content == "hi"
    assert events.emits == [
        Event(event_type=EventType.TURN_STARTED, data={}, ok=True),
        Event(
            event_type=EventType.PROVIDER_STARTED,
            data={"model": "mock-model", "iteration": 1, "context_size": 1},
            ok=True,
        ),
        Event(
            event_type=EventType.PROVIDER_DELTA,
            data={"kind": "thinking", "text": "hmm", "iteration": 1},
            ok=True,
        ),
        Event(
            event_type=EventType.PROVIDER_DELTA,
            data={"kind": "text", "text": "hi", "iteration": 1},
            ok=True,
        ),
        Event(
            event_type=EventType.PROVIDER_DELTA,
            data={"kind": "tool", "text": "read_file", "iteration": 1},
            ok=True,
        ),
        Event(
            event_type=EventType.PROVIDER_FINISHED,
            data={"model": "mock-model", "iteration": 1, "tool_calls": []},
            ok=True,
        ),
        _turn_finished(StopReason.END_TURN),
    ]
    assert provider.on_delta is None


async def test_send_emits_turn_and_provider_events_when_there_are_no_tool_calls():
    provider = SequenceProvider([Completion(message="hello world")])
    events = RecordingEvents()
    agent = Agent(provider=provider, events=events)

    result = await agent.send(Message(role="user", content="hi"))

    assert result == "hello world"
    assert events.emits == [
        Event(event_type=EventType.TURN_STARTED, data={}, ok=True),
        Event(
            event_type=EventType.PROVIDER_STARTED,
            data={"model": "mock-model", "iteration": 1, "context_size": 1},
            ok=True,
        ),
        Event(
            event_type=EventType.PROVIDER_FINISHED,
            data={"model": "mock-model", "iteration": 1, "tool_calls": []},
            ok=True,
        ),
        _turn_finished(StopReason.END_TURN),
    ]


async def test_send_emits_tool_events_then_final_provider_turn():
    tool, calls = _recording_tool("lookup")
    provider = SequenceProvider(
        [
            Completion(
                message="need a tool",
                tool_calls=[ToolCall(name="lookup", arguments={"q": "weather"})],
            ),
            Completion(message="it's sunny"),
        ]
    )
    events = RecordingEvents()
    agent = Agent(provider=provider, tools=[tool], events=events)

    result = await agent.send(Message(role="user", content="weather?"))

    assert result == "it's sunny"
    assert calls == [{"q": "weather"}]
    assert events.emits == [
        Event(event_type=EventType.TURN_STARTED, data={}, ok=True),
        Event(
            event_type=EventType.PROVIDER_STARTED,
            data={"model": "mock-model", "iteration": 1, "context_size": 1},
            ok=True,
        ),
        Event(
            event_type=EventType.PROVIDER_FINISHED,
            data={"model": "mock-model", "iteration": 1, "tool_calls": ["lookup"]},
            ok=True,
        ),
        Event(event_type=EventType.TOOL_STARTED, data={"name": "lookup"}, ok=True),
        Event(event_type=EventType.TOOL_FINISHED, data={"name": "lookup"}, ok=True),
        Event(
            event_type=EventType.PROVIDER_STARTED,
            data={"model": "mock-model", "iteration": 2, "context_size": 3},
            ok=True,
        ),
        Event(
            event_type=EventType.PROVIDER_FINISHED,
            data={"model": "mock-model", "iteration": 2, "tool_calls": []},
            ok=True,
        ),
        _turn_finished(StopReason.END_TURN),
    ]


async def test_send_emits_tool_finished_error_when_tool_raises():
    async def explode(**_args) -> str:
        raise RuntimeError("disk full")

    tool = _mock_tool("write", explode)
    provider = SequenceProvider(
        [
            Completion(
                message="",
                tool_calls=[ToolCall(name="write", arguments={"path": "a"})],
            ),
            Completion(message="could not write"),
        ]
    )
    events = RecordingEvents()
    agent = Agent(provider=provider, tools=[tool], events=events)

    result = await agent.send(Message(role="user", content="write"))

    assert result == "could not write"
    assert (
        Event(
            event_type=EventType.TOOL_STARTED,
            data={"name": "write"},
            ok=True,
        )
        in events.emits
    )
    assert (
        Event(
            event_type=EventType.TOOL_FINISHED,
            data={"name": "write"},
            ok=False,
            error="disk full",
        )
        in events.emits
    )
    assert events.emits[-1] == _turn_finished(StopReason.END_TURN)


async def test_send_emits_tool_finished_error_when_tool_is_unknown():
    provider = SequenceProvider(
        [
            Completion(
                message="",
                tool_calls=[ToolCall(name="missing", arguments={})],
            ),
            Completion(message="no such tool"),
        ]
    )
    events = RecordingEvents()
    agent = Agent(provider=provider, events=events)

    result = await agent.send(Message(role="user", content="go"))

    assert result == "no such tool"
    tool_messages = [message for message in agent.context if message.role == "tool"]
    assert tool_messages[0].content == "Unknown tool: missing"
    assert (
        Event(
            event_type=EventType.TOOL_STARTED,
            data={"name": "missing"},
            ok=True,
        )
        in events.emits
    )
    assert (
        Event(
            event_type=EventType.TOOL_FINISHED,
            data={"name": "missing"},
            ok=False,
            error="Unknown tool: missing",
        )
        in events.emits
    )


async def test_send_emits_failed_turn_when_provider_raises():
    provider = FailingProvider(RuntimeError("ollama down"))
    events = RecordingEvents()
    agent = Agent(provider=provider, events=events)

    with pytest.raises(RuntimeError, match="ollama down"):
        await agent.send(Message(role="user", content="hi"))

    assert agent.context[0] == Message(role="user", content="hi")
    assert events.emits == [
        Event(event_type=EventType.TURN_STARTED, data={}, ok=True),
        Event(
            event_type=EventType.PROVIDER_STARTED,
            data={"model": "mock-model", "iteration": 1, "context_size": 1},
            ok=True,
        ),
        Event(
            event_type=EventType.PROVIDER_FINISHED,
            data={"model": "mock-model", "iteration": 1},
            ok=False,
            error="ollama down",
        ),
        _turn_finished(StopReason.ERROR, ok=False, error="ollama down"),
    ]


async def test_send_stops_on_max_tokens_without_running_tools():
    tool, calls = _recording_tool("lookup")
    provider = SequenceProvider(
        [
            Completion(
                message="partial",
                tool_calls=[ToolCall(name="lookup", arguments={"q": "weather"})],
                stop_reason=StopReason.MAX_TOKENS,
            )
        ]
    )
    events = RecordingEvents()
    agent = Agent(provider=provider, tools=[tool], events=events)

    result = await agent.send(Message(role="user", content="weather?"))

    assert result == "partial"
    assert calls == []
    assert agent.context[-1].role == "assistant"
    assert agent.context[-1].content == "partial"
    assert agent.context[-1].tool_calls is None
    assert events.emits[-1] == _turn_finished(StopReason.MAX_TOKENS)


async def test_send_inserts_hook_text_before_the_user_and_closes_the_turn():
    provider = SequenceProvider([Completion(message="HOOK_BEFORE")])
    events = RecordingEvents()
    hooks = RecordingHook(text="The first line must be HOOK_BEFORE.")
    agent = Agent(provider=provider, events=events, hooks=hooks)

    result = await agent.send(Message(role="user", content="hola"))

    assert result == "HOOK_BEFORE"
    assert hooks.before_calls == ["hola"]
    assert [message.role for message in provider.complete_calls[0][:2]] == [
        "hook",
        "user",
    ]
    assert provider.complete_calls[0][0].content == "The first line must be HOOK_BEFORE."
    closed = hooks.after_calls[0]
    assert closed.stop_reason == "end_turn"
    assert closed.completed is True
    assert closed.error is None
    assert [message.role for message in closed.messages] == [
        "hook",
        "user",
        "assistant",
    ]
    assert closed.messages[-1].content == "HOOK_BEFORE"
    assert events.emits[0].event_type == EventType.TURN_STARTED
    assert events.emits[-1] == _turn_finished(StopReason.END_TURN)


async def test_send_continues_when_before_turn_raises():
    provider = SequenceProvider([Completion(message="still here")])
    events = RecordingEvents()
    hooks = RecordingHook(before_error=RuntimeError("hook broke"))
    agent = Agent(provider=provider, events=events, hooks=hooks)

    result = await agent.send(Message(role="user", content="hola"))

    assert result == "still here"
    assert [message.role for message in agent.context] == ["user", "assistant"]
    assert events.emits[0] == Event(
        event_type=EventType.HOOK_FINISHED,
        data={"name": "before_turn"},
        ok=False,
        error="hook broke",
    )
    assert hooks.after_calls[0].stop_reason == "end_turn"


async def test_send_returns_model_text_when_after_turn_raises():
    provider = SequenceProvider([Completion(message="still here")])
    events = RecordingEvents()
    hooks = RecordingHook(after_error=RuntimeError("digest failed"))
    agent = Agent(provider=provider, events=events, hooks=hooks)

    result = await agent.send(Message(role="user", content="hola"))

    assert result == "still here"
    assert hooks.after_calls[0].completed is True
    assert events.emits[-2] == Event(
        event_type=EventType.HOOK_FINISHED,
        data={"name": "after_turn"},
        ok=False,
        error="digest failed",
    )
    assert events.emits[-1] == _turn_finished(StopReason.END_TURN)


async def test_send_runs_after_turn_when_provider_raises():
    provider = FailingProvider(RuntimeError("ollama down"))
    hooks = RecordingHook()
    agent = Agent(provider=provider, hooks=hooks)

    with pytest.raises(RuntimeError, match="ollama down"):
        await agent.send(Message(role="user", content="hola"))

    closed = hooks.after_calls[0]
    assert closed.stop_reason == "error"
    assert closed.completed is False
    assert closed.error == "ollama down"
    assert [message.content for message in closed.messages] == ["hola"]
