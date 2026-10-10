# Events and logs

The loop, tools, and hooks never call each other to report what happened. They
publish an `Event` on a bus; whoever cares subscribes. Events observe a turn;
they never change it.

## The bus

`EventSystem` (`lib/events/event_system.py`) is created in `Config.load` with
a `session_id` (UUID). The same instance is passed to the agent, the shell,
`web_search`, and the hooks.

An `Event` has `event_type`, `data` (dict), `ok` (default true), and `error`
(string or `None`).

- `on(event_type, subscriber)` appends a subscriber. There is no filtering,
  priority, or unsubscribe.
- `emit(...)` builds the event, calls the subscribers of that type, then the
  subscribers of `*` (unless the event type is itself `*`). It iterates over a
  copy of the list, so a subscriber may subscribe others while being called.
- An event nobody listens to is dropped.

Collaborators depend on the `EventEmitter` protocol (just `emit`), so tests
can inject a recording fake.

## Subscribers

| Subscriber | Subscribed to | Does |
| --- | --- | --- |
| `JsonlSink.handle` | `*` | Appends the event to `logs/<session_id>.jsonl`, except `provider.delta` |
| `Session.show_delta` | `provider.delta` | Paints streaming output in the terminal |

`Config.load` wires the sink; `main.py` wires the session. The agent imports
neither. `ConsoleSink` (one `[ok]` / `[error]` line per event on stderr)
exists but is not wired.

## Event catalog

| Event | `data` | `ok` is false when |
| --- | --- | --- |
| `turn.started` | empty | - |
| `turn.finished` | `stop_reason`, `completed` | `complete` raised; `error` is the message |
| `provider.started` | `model`, `iteration`, `context_size` | - |
| `provider.delta` | `kind` (`thinking`, `text`, `tool`), `text`, `iteration` | - |
| `provider.finished` | `model`, `iteration`, and `tool_calls` (names) on success | `complete` raised; no `tool_calls` |
| `tool.started` | `name` | - |
| `tool.finished` | `name` | Unknown tool or the call raised; `error` is the reason |
| `hook.started` | `name` (`before_turn` / `after_turn`), `path` | - |
| `hook.finished` | `name`, and `path` when known | Script raised, timed out, exited non-zero, `hooks.json` failed to load, or the hook object raised |
| `permission.denied` | `name: "run_command"` with `reasons`, or `name: "web_search"` with `query` | Always false; `error` is the text returned to the model |

`completed` is true only when `stop_reason` is `end_turn`.

A turn with one tool call reads: `hook.*` for `before_turn` (if configured),
`turn.started`, then per iteration `provider.started`, the deltas,
`provider.finished`, and `tool.started` / `tool.finished` for each call;
finally `hook.*` for `after_turn` and `turn.finished`.

## The log

One file per process: `logs/<session_id>.jsonl` in the project root (next to
`main.py`, not in the workspace). The directory is created when the sink is
built; every event opens the file in append mode and writes one line.

| Field | Meaning |
| --- | --- |
| `logged_at` | UTC ISO timestamp, set when written |
| `session_id` | The `EventSystem` UUID |
| `event_type` | From the catalog above |
| `data` | The event dict |
| `ok` | `true` / `false` |
| `error` | Text or `null` |

`provider.delta` is filtered out before the sink: every thinking or text
chunk would otherwise be its own line. A write failure prints
`JsonlSink failed: ...` to stderr and never breaks the turn.
