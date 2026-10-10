# Harness: rules, skills, and hooks

The agent runs three mechanisms; the harness supplies their content. All three
read from two layers:

| Layer | Directory |
| --- | --- |
| Global | `~/.agent/` |
| Workspace | `{workspace}/.agent/` |

None of them changes the loop. Rules and the skill index are resolved before
the `Agent` is built and enter as `role="rule"` messages; hooks are an injected
`TurnHook` collaborator.

## Rules

Always-on instructions.

- Source: every `*.md` directly inside `rules/` (no subdirectories). A missing
  `rules/` contributes nothing.
- Order: global files by filename, then workspace files by filename. The same
  filename in both layers produces two messages.
- Each file becomes one `Message(role="rule", content=<file body>)`. The path
  is not part of the content.
- There is no frontmatter: every file applies on every turn. Selection by
  `description`, `globs`, or `alwaysApply` is not supported.
- `rule_messages` prints the resolved paths at startup. Edits require a
  restart.

The messages sit at the front of the context and are resent on every
`complete`. Providers join all `rule` messages into a single system prompt (see
[architecture.md](architecture.md#providers)).

## Skills

On-demand procedures. The index is cheap and always present; the body is paid
for only when the model asks for it.

### File format

Every `*.md` directly inside `skills/`, with a frontmatter block at the very
top. `name` and `description` are single-line scalars; the body is everything
after the closing `---`.

```markdown
---
name: release-notes
description: Use when the user asks for release notes; summarizes merged changes.
---
1. Run `git log --oneline` since the last tag.
2. ...
```

A file without both `name` and `description`, or one that does not start with
`---`, is skipped.

### Resolution

`resolve_skills` reads the global layer, then the workspace layer, each in
filename order. A later file with the same `name` replaces the description and
body but keeps the original position in the index; new names are appended.
So the workspace overrides global, and within a layer the alphabetically later
file wins.

### What the model sees

One extra `rule` message after the rules, only if there is at least one skill:

```text
skills:
release-notes: Use when the user asks for release notes; summarizes merged changes.
```

The body is fetched with the `load_skill` tool:

- `load_skill(name: str) -> str` returns the winning body.
- An unknown name returns `Error: unknown skill: <name>`.

`skill_messages` prints the skill names at startup and returns the index
message plus `load_skill`, closed over the resolved bodies. Edits require a
restart.

`.agent/skills/` (harness skills, loaded by this agent) is unrelated to
`.cursor/skills/` (Cursor IDE skills for contributing to this repo).

## Hooks

Scripts that run around every turn. Unlike events, which only observe, a hook
can add context.

### Configuration

`~/.agent/hooks.json` and `{workspace}/.agent/hooks.json`. Both are optional
and re-read on every turn.

```json
{
  "version": 1,
  "hooks": {
    "before_turn": [{ "command": ".agent/hooks/before_turn.sh" }],
    "after_turn": [{ "command": ".agent/hooks/after_turn.sh" }]
  }
}
```

- The file is strict JSON: comments are a parse error. (`mcp.json` accepts
  comments; `hooks.json` does not.)
- `version` must be `1`. Other hook names and extra entry keys (`timeout`,
  `matcher`) are ignored.
- `command` is a path to an executable, absolute or relative to the
  workspace. Entries without `command` are skipped.
- An unreadable file, invalid JSON, or a wrong `version` drops that layer and
  emits a failed `hook.finished`; the turn continues.
- Commands run global first, then workspace, in array order.
  `open_hooks` prints the resolved paths at startup.

### Process

- Each script runs with the user's environment and the workspace as cwd. It
  is **not** sandboxed and does not go through `Authorization`.
- All commands of one hook run concurrently. Each gets 5 seconds; the turn
  waits for the slowest.
- On timeout the agent kills the script's process group and discards its
  stdout. A child that detached into its own session (`setsid`, a daemon)
  keeps running and is not managed.
- Stdin is one line of JSON.

### `before_turn`

Stdin:

```json
{"prompt": "hello"}
```

On exit 0 within the timeout, stdout is stripped and kept. Non-empty outputs
are joined with newlines into one `Message(role="hook")`, inserted just
before the user message of that turn. It stays in the context for later
turns. Timeouts, non-zero exits, and launch errors contribute nothing.

Ollama sends the hook message as `user`; Claude sends it as a `text` block in
the same user message as the prompt. It never joins the system prompt.

### `after_turn`

Runs after the turn closes, on both normal return and provider error. Stdin:

```json
{"stop_reason": "end_turn", "completed": true, "error": null, "messages": [...]}
```

`messages` is the full context reached, including hook text, with `role`,
`content`, and `tool_name` / `tool_call_id` / `tool_calls` when present.
Stdout is ignored. If the provider raised, `send` re-raises after the hook.

### Events

Each script emits `hook.started` and `hook.finished` with `name` and `path`.
`hook.finished` has `ok=false` when the script raised, timed out, exited
non-zero, or its `hooks.json` failed to load. See
[events-and-logs.md](events-and-logs.md).
