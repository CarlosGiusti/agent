# AGENTS.md

## Scope

This repo is a learning lab for **building a coding-agent harness**, not a product.
The work is the loop around the model: tool contracts, permissions, context, and
failure modes. Study Claude Code, Cursor, OpenCode, and byo-coding-agent as
architecture references — do not copy them blindly.

## Out of scope

Do not turn this harness into a Cursor/Claude clone (plugin marketplace, Tab
completions, cloud agents, or a general app). Do not add frameworks or SDKs
that hide the loop. If a change does not teach something about agents, skip it.

## Tech stack

- Language: Python 3.12
- Package/run: `uv` (`uv sync`, `uv run main.py`, `uv run pytest`)
- Agent runtime: Ollama (`qwen3:8b`) by default, Claude with `AGENT_PROVIDER=claude` (selected in `main.py`)
- Tools: local `Tool` + `ToolRegistry`; MCP servers via `MCPClient`
- Tests: pytest + pytest-asyncio (`asyncio_mode = auto`); fakes, not MagicMock
- Lint/types: ruff, pyright
- Layout: `lib/` (agents, tools, authorization, sandbox, events, sinks, mcp, rules, skills, hooks, client, config, paths), `tests/`, `docs/`, `logs/`
- Docs: `README.md` is the entry point; contracts live in `docs/`. Keep them in English and update the matching doc when a contract changes.



## Permission contract

Two boxes. Authorization does **not** match command strings to decide policy.

- **Box A (default):** `run_command` runs under bubblewrap: workspace bind + `--unshare-net`. No prompt.
- **Box B (elevated):** same command, no bwrap, network on, starts in the workspace but can leave. Always **allow once** (never persist). Prompt shows *why* A blocked (network and/or path outside the bind), not raw tool-arg dumps.
- Elevation: `Shell.run` tries A, `classify_constraint` reads the **result** (stderr + paths), `Authorization.check` asks, then A is retried as B.
- `read_file` / `write_file` stay as workspace-only shortcuts (no prompt, no elevation). They are not required: `cat`/`grep`/`tee` in box A do the same. They exist because a path arg is cheaper and safer than quoting bash. Paths outside the workspace return an error pointing at `run_command`.
- `web_search` runs in the harness process with its own `PromptAuthorizationProtocol`: every query asks, allow once, nothing persists. It does not go through `Authorization.check`.
- MCP tools and hook scripts are not boxed. They run with host authority.

Full description: `docs/authorization-and-sandbox.md`.



### Composition hole

Each lock is honest in isolation. Together they leak.

1. **Context exfil:** box A (or `read_file`) can put workspace secrets (`.env`) into the transcript. After a later elevation, box B has network and can `curl` that text. The sandbox never opened `.ssh` on disk; the chat did.
2. **Missed elevation:** `python steal.py` may touch `~/.ssh` inside the script. Box A fails with ENOENT; `classify_constraint` only elevates when the **output or command** names an outside path *and* looks like a filesystem block. A script that fails opaquely is not retried in B. Conversely, a test failure that prints `/home/me/.ssh` can false-prompt.
3. **Allow once is the whole machine for that command:** B has no workspace bind and full network. One approved `python app.py` can read home and talk to the internet. We do not parse the command to shrink B.
4. **System mounts in A:** `/usr`, `/etc`, … are read-only so bash works. `cat /etc/passwd` does not leave the bind and does not elevate.
5. **MCP and hooks** bypass both boxes.

Do not “fix” this by matching `command` strings for allow/deny. That is the old system. The hole is the lesson: boxes bound processes, not the transcript, and elevation is a coarse capability.

## Never-ever

1. **Never change harness semantics without an agreed contract.** Loop, stop
  conditions, tool schemas/names, permission boundaries, and error surfaces
   are owned by the human. If those need to move, stop and ask. Do not "just
   implement" them. Adding a local tool is catalog work: factory in the tool
   module and `make_local_tools` in `tool_registry.py`. Do not edit `Agent`,
   `OllamaProvider`, or `main` unless the human asked for a different tool
   subset or wiring.
2. **Never give elevation a free pass or a sticky grant.** Box A is the default
  for `run_command`. Box B always prompts and never stores Allow. Do not add a
   network or unsandboxed shell path that skips `Authorization.check`.
   `write_file` may mutate the workspace without a prompt because the workspace
   path bound is the lock. Do not install packages with `pip`; this repo uses
   `uv`.
3. **Never hide a missing seam behind a mock.** Unit tests assert public
  entry/exit and calls to **injected** collaborators. If the SUT constructs
   `EventSystem()`, opens I/O, or would require `monkeypatch` / `MagicMock.patch`
   of a private, stop and ask which collaborator to inject. Do not test
   `_private` methods. Tool failures return strings the model can read; they
   do not raise into the loop as the contract. `BwrapSandbox` is a collaborator:
   tests inject a fake `CommandSandbox`, they do not run `bwrap`.



## Assistant may do

Concrete file/shell/HTTP bodies **inside** an already-designed tool, schema
glue, fixtures, and docs that do not change agent semantics.

## Done

A change is done when `uv run pytest` on the touched tests is green and the
diff does not include loop/policy/schema files unless the human explicitly
asked for that contract change.