---
name: create-tools
description: >-
  Creates and registers agent tools in this project. Use when adding a new tool,
  wrapping a function as Tool, updating ToolRegistry, or when the user mentions
  tools, tool schema, file_io, or asks to register a tool (registrar una tool).
---

# Create tools

MCP servers are first-class tools. A server's `inputSchema` is JSON Schema, and
the catalog does not control those servers, so **`Tool.arguments` is JSON Schema
too** — the MCP contract, not a narrower local subset. Local tools are written
in that same language. Providers (`OllamaProvider._to_ollama_tools`) wrap the
schema in a vendor envelope; they do not reinterpret it.

Adding a tool is catalog work, not agent work.

## Architecture

- `Tool` is the stable interface: `name`, `description`, `arguments` (JSON Schema object), `call()`.
- `arguments` matches MCP `Tool.inputSchema`: root `type: "object"`, optional `properties` / `required`, plus any JSON Schema 2020-12 keyword (`items`, nested `properties`, `enum`, `additionalProperties`, `$ref`, …). Extra keys are forwarded to the model. Do not strip them.
- `to_arguments_schema` only fills MCP-legal omissions (`type` / `properties`). It runs in `Tool.__init__` for local and MCP tools.
- The module that owns the function also owns the `Tool(...)` factory (or instance).
- `main` builds the shared local tools with `make_local_tools(workspace, shell, *tools)` and passes that list into `ToolRegistry`. `workspace` and `shell` stay the only named dependencies. A tool that closes over something else is built where that value exists and passed in as an extra `Tool`. MCP tools are merged in `bootstrap()`. No public `register()`.
- Agents dispatch via `self.tools` (`Agent._call_tool`). Do not import `ToolRegistry` from an agent. Do not inject workspace into `call()`.
- File tools close over the workspace path (bound, no prompt). `run_command` closes over `Shell` (box A via `CommandSandbox`, elevation via `Authorization`). Do not pass `Config` into `Tool.__init__`.

## Add a tool

1. Put the function next to its domain (new module under `lib/tools/` or an existing one). Return `str`. Failures are strings, not exceptions the loop must catch (the loop still guards, but tools should be polite).
2. Export a `make_*` factory (or a `Tool` constant if it needs no workspace). `arguments` is a JSON Schema object (`ArgumentsSchema`).
3. If the tool only needs `workspace` or `shell`, call its factory inside `make_local_tools`. If it closes over another value, build the `Tool` where that value is resolved and pass it as an extra argument. Do not add a new parameter to `make_local_tools`.
4. Stop. Do not change `Agent`, `OllamaProvider`, or `main` unless the tool needs a different subset than `registry.tools`. Do not add MCP tools by hand; they come from `bootstrap()`.

`name` must be unique. MCP `bootstrap` indexes by server-prefixed name and will overwrite a local tool with the same key.

## Template

```python
from lib.tools.tool import Tool


def do_thing(path: str) -> str:
    return f"did {path}"


DO_THING = Tool(
    name="do_thing",
    description="One sentence: what it does and when the model should call it.",
    arguments={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "What this argument is, from the model's point of view",
            }
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    function=do_thing,
)
```

Nested objects and arrays are legal when the function takes them — same JSON Schema MCP would send:

```python
"tags": {
    "type": "array",
    "description": "Labels to apply",
    "items": {"type": "string"},
}
```

Registry:

```python
from lib.tools.your_module import make_do_thing

def make_local_tools(workspace: Path, shell: Shell, *tools: Tool) -> list[Tool]:
    return [
        make_read_file(workspace),
        make_write_file(workspace),
        make_run_command(shell),
        make_do_thing(workspace),
        *tools,
    ]
```

Reference implementation: `lib/tools/file_io.py`.

## Rules

- Sync and async functions are both fine; `Tool` wraps sync with `asyncio.to_thread`.
- Keyword-only at call time: `tool.call(**args)`. Parameter names must match schema property names.
- Descriptions are for the model, not for developers. Be explicit about side effects (`write_file` writes to disk).
- Local tools should set `additionalProperties: False` when the argument list is closed. Do not add that (or strip it) on MCP schemas.
- Do not pass the registry into agents. If an agent should not get every tool, filter `registry.tools` in `main`.
- Do not add vendor-specific JSON in the `Tool`. Adaptation stays in the provider (`OllamaProvider._to_ollama_tools`).
- Do not pass `Config` into `Tool`. Workspace bounds close over a `Path` in the factory. Consent for leaving box A lives in `Authorization` via `Shell`, not in `Agent`._call_tool.
