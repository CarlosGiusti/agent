# agent

A learning lab for building a coding-agent harness. The work is the loop around
the model: tool contracts, permissions, context, and failure modes. It is not a
product and not a Cursor/Claude clone.

The agent itself runs three harness mechanisms — rules, skills, and hooks — so
that a harness (the rule text, the skill corpus, the hook scripts) can live
outside this repo and be tried on personal projects.

## Requirements

- Python 3.12 and [`uv`](https://docs.astral.sh/uv/)
- [bubblewrap](https://github.com/containers/bubblewrap) (`bwrap`) for the
  default `run_command` sandbox
- [Ollama](https://ollama.com/) with `qwen3:8b` pulled, or an Anthropic API key
- `~/.agent/mcp.json` (may declare no servers; see [docs/mcp.md](docs/mcp.md))

## Setup

```bash
uv sync
```

## Run

```bash
uv run main.py [workspace]
```

`workspace` is the project folder the agent works on. It defaults to the
current directory and must exist. File tools and the default shell sandbox are
bound to it.

## Environment

`main.py` reads `.env` next to itself and copies each `KEY=value` into the
environment. Variables already set in the environment win.

| Variable | Effect |
| --- | --- |
| `AGENT_PROVIDER` | `ollama` (default, `qwen3:8b`) or `claude` (`claude-sonnet-5-5`). Any other value exits at startup |
| `ANTHROPIC_API_KEY` | Required when `AGENT_PROVIDER=claude`. If empty, each turn ends with `stop_reason` `error` |
| `TAVILY_API_KEY` | When set, `web_search` uses Tavily. When empty, it falls back to `ddgs` (no key) |

## Harness configuration

Everything the harness supplies lives under `~/.agent/` (global) and
`{workspace}/.agent/` (per project):

| Path | Purpose |
| --- | --- |
| `rules/*.md` | Always-on instructions, sent as the system prompt |
| `skills/*.md` | On-demand procedures, listed by name and loaded with `load_skill` |
| `hooks.json` | `before_turn` / `after_turn` scripts |
| `~/.agent/mcp.json` | MCP servers (global only) |

Rules and skills are read once at startup; restart to pick up edits. Hook
files are re-read every turn. Details in [docs/harness.md](docs/harness.md).

## Documentation

| Document | Covers |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | Startup wiring, the agent loop, messages and stop reasons, providers (Ollama, Claude), thinking, and the terminal client |
| [docs/harness.md](docs/harness.md) | Rules, skills, and hooks |
| [docs/tools.md](docs/tools.md) | The `Tool` contract, registry, local tools, and web search |
| [docs/mcp.md](docs/mcp.md) | MCP server configuration and how MCP tools join the registry |
| [docs/authorization-and-sandbox.md](docs/authorization-and-sandbox.md) | Box A / Box B, elevation, web search consent, and the composition hole |
| [docs/events-and-logs.md](docs/events-and-logs.md) | The event bus, event catalog, and JSONL logs |

[AGENTS.md](AGENTS.md) holds the rules for coding assistants working in this
repo. `.cursor/skills/` holds Cursor skills for contributing (adding tools,
writing tests); they are unrelated to the harness skills in `.agent/skills/`.

## Development

```bash
uv run pytest
uv run ruff check .
uv run pyright
```

Tests use fakes injected through constructors, not `MagicMock` or patching.
They never touch the network or run `bwrap`.

## Possible next steps

- `spawn_agent`: a fresh context with a reduced tool set that returns a string.
- Context budget: trim old tool results. With Ollama the usable window is
  `num_ctx`.
- A message transcript on disk, alongside the event log, so an `after_turn`
  hook can persist a session.
- Raising `max_iterations` beyond 5 for tasks that combine skills and tools.
