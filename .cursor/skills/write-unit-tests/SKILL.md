---
name: write-unit-tests
description: >-
  Writes pytest unit tests that assert public entry/exit behavior and
  interactions with injected dependencies. Use when adding or changing unit
  tests, pytest files, fakes, or when the user asks to test or cover a class
  (probar, testear).
---

# Write unit tests

We use pytest. Classes under test should receive their dependencies through
injection.

## Gate: dependency injection

Before writing a test, read the SUT's `__init__` (or factory).

If a class under test has a collaborator that should be injected but is not,
stop and ask the user.

Signs that injection is missing (stop, do not patch):

- The SUT calls `Foo()` / `EventSystem()` / opens I/O inside the constructor or
  a method instead of receiving the collaborator.
- Observing the behavior would require `monkeypatch` / `unittest.mock.patch`
  of an internal symbol.
- A concrete collaborator (network, disk, LLM, clock) cannot be replaced from
  the constructor.

Ask concretely: which collaborator to inject, with which protocol, and whether
the constructor must change **before** testing.

Do not "fix" the injection yourself or write a half-finished test. Wait for an
answer.

Exception: modules of pure domain functions (the `lib/sandbox/sandbox.py`
helpers `classify_constraint`, `outside_paths`) have no constructor. There the
SUT **is** the public function, and the DI gate does not apply.

## What to test

We do not test internal functions. We test how data moves through a class's
entry and exit points.

Example: `lib/agents/agent.py` or `tests/test_authorization.py` — given
parameters X passed to `agent.send`, the agent should call the injected fake
dependencies with parameters X and return value X.

| Test | Do not test |
| --- | --- |
| Public methods (`send`, `check`) | `_loop`, `_call_tool`, `_private` helpers |
| Arguments that reach injected dependencies | How the SUT orchestrates internally |
| Return value and visible state (`context`, `protocol.calls`) | Implementation, internal branches, helper order |
| Errors the public contract propagates or translates | Exceptions swallowed inside a private |

If a behavior is only observable by calling a `_private`, the design or the
test is wrong: either a public exit point is missing, or an injected
collaborator is missing (back to the gate).

## How to write them

1. Identify the public entry point and its outputs (return, visible mutation,
   calls to dependencies).
2. Implement **fakes** that satisfy the same protocol/ABC as the real
   dependency and record calls (`complete_calls`, `protocol.calls`).
3. Do not use `MagicMock` / `patch` to replace dependencies. An explicit fake
   is the contract.
4. Build the SUT by injecting those fakes.
5. Call **only** the public method.
6. Assert whichever of these apply:
   - the return value
   - calls to each fake (name, args, count, order if the contract requires it)
   - visible state the caller could read (`agent.context`, `protocol.calls`)
7. File: `tests/test_<module>.py`. Module-level `async def test_*` /
   `def test_*` functions. `asyncio_mode = auto`: do not add
   `@pytest.mark.asyncio`.
8. Run `pytest` on the touched files.

## Canonical shape

```python
async def test_send_returns_final_message_when_there_are_no_tool_calls():
    provider = SequenceProvider([Completion(message="hello world")])
    agent = Agent(provider=provider)

    result = await agent.send(Message(role="user", content="hi"))

    assert result == "hello world"
    assert len(provider.complete_calls) == 1
    assert agent.context[-1].role == "assistant"
    assert agent.context[-1].content == "hello world"
```

Entry: `send(Message(...))`.
Outputs: returns `"hello world"`, `provider.complete` called once, and the
visible `context`. `_loop` is not called, and how the agent builds the
completion internally is not inspected.

Same pattern for authorization: entry `auth.check(command, reasons)`, outputs a
bool plus `protocol.calls`. For the shell: entry `shell.run(command)`, outputs
a string plus `sandbox.calls` and `protocol.calls`. See
`tests/test_authorization.py` and `tests/test_shell.py`.

## Fakes

Follow the repo style: subclass the protocol, script the responses, record the
calls.

```python
class ScriptedProtocol(AuthorizationProtocol):
    def __init__(self, decisions: list[DecisionType]) -> None:
        self._decisions = list(decisions)
        self.calls: list[tuple[str, dict]] = []

    async def check(self, action: str, args: dict) -> DecisionType:
        self.calls.append((action, args))
        if not self._decisions:
            raise AssertionError(f"unexpected prompt for {action}")
        return self._decisions.pop(0)
```

Helpers such as `_auth(...)` or `_recording_tool(...)` are fine when they
avoid repeated wiring. Do not extract "test base" classes.

A fake that runs out of responses must fail with an explicit `AssertionError`
(an extra call), not return a silent default.

## Out of scope

- Integration tests with network, `bwrap`, or a real disk (except `tmp_path`
  for file tools).
- Covering private lines "just in case".
- Patching `Agent._call_tool` or `Authorization` internals to force a branch.
- Adding test dependencies other than pytest / pytest-asyncio.
