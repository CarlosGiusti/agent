# Tools

What the model can call, and how a tool is defined and registered. Permission
boundaries for `run_command` and `web_search` are detailed in
[authorization-and-sandbox.md](authorization-and-sandbox.md); MCP tools in
[mcp.md](mcp.md).

## The `Tool` contract

`Tool` (`lib/tools/tool.py`) has four parts:

| Field | Meaning |
| --- | --- |
| `name` | Unique across local and MCP tools |
| `description` | Written for the model: what it does, when to call it, side effects |
| `arguments` | A JSON Schema object |
| `function` | Sync or async callable. Sync functions run in `asyncio.to_thread` |

The loop calls `tool.call(**arguments)`, so parameter names must match schema
property names. The result is converted to `str`.

### Arguments are JSON Schema

`arguments` follows the same contract as MCP `Tool.inputSchema`: a root
`type: "object"` with optional `properties` / `required`, plus any JSON Schema
keyword (`items`, nested objects, `enum`, `additionalProperties`, `$ref`, ...).
`to_arguments_schema` only fills the MCP-legal omissions (`type`, empty
`properties`) and keeps every other key. Providers wrap the schema in their own
envelope without reinterpreting it.

Local tools set `additionalProperties: false` when their argument list is
closed. MCP schemas are forwarded untouched.

### Failures are strings

A tool should return an error string the model can read rather than raise.
The loop still guards: an exception becomes `Error calling <name>: <exc>` and
an unknown name becomes `Unknown tool: <name>`. Neither ends the turn.

## Registry

```python
def make_local_tools(workspace: Path, shell: Shell, *tools: Tool) -> list[Tool]
```

`make_local_tools` builds the fixed set (`read_file`, `write_file`,
`run_command`) and appends any extra tools. A tool that needs something other
than `workspace` or `shell` is built where that value exists (in `main.py`)
and passed in as an extra argument; `make_local_tools` does not grow new
parameters.

`ToolRegistry(tools)` indexes by name. `bootstrap(mcp_client)` then adds every
MCP tool, overwriting any local tool with the same name. `registry.tools` is
what the `Agent` receives.

Adding a tool is catalog work: a factory in the tool's module, plus wiring in
`make_local_tools` or `main.py`. The agent loop and providers do not change.

## Local tools

| Tool | Arguments | Behavior |
| --- | --- | --- |
| `read_file` | `path` | Reads a UTF-8 file relative to the workspace |
| `write_file` | `path`, `content` | Writes a UTF-8 file relative to the workspace, creating parent directories. No prompt |
| `run_command` | `command` | Runs `bash -c` in the workspace sandbox, elevating with consent when blocked |
| `load_skill` | `name` | Returns a skill body; see [harness.md](harness.md#skills) |
| `web_search` | `query`, `max_results` | Always present. Tavily when `TAVILY_API_KEY` is set, else `ddgs` |

### `read_file` / `write_file`

Workspace-only shortcuts. They exist because a path argument is cheaper and
safer than quoting through bash; `cat` and `tee` inside the sandbox do the
same. They never prompt and never elevate.

- A path that resolves outside the workspace returns an error pointing the
  model at `run_command`.
- `read_file` on a missing file returns `Error: file not found: <path>`.
- `write_file` refuses existing non-files and returns
  `Wrote <n> characters to <path>` on success.

### `run_command`

Closes over `Shell`. The command first runs in the sandbox (no network,
workspace bind, read-only system paths). If the result shows it was blocked by
the network or by a path outside the workspace, the user is asked to allow it
once without the sandbox.

- Empty commands return `Error: command is empty`.
- Output is `exit_code: N`, then `stdout:` / `stderr:` sections or
  `(no output)`, truncated at 50,000 characters.
- Commands time out after 30 seconds (exit code 124).
- An elevated run is prefixed with `elevated (<reasons>)`.
- A denied elevation returns a message telling the model the command was not
  retried.

## Web search

`web_search` returns titles, URLs, and snippets. It does not fetch pages, and
its description tells the model the snippets are untrusted external content.

### Arguments

| Field | Contract |
| --- | --- |
| `query` | Required. Stripped; empty returns `Error: query is empty` without prompting |
| `max_results` | Optional, default 5, clamped to 1-10 |

### Flow

```text
web_search(query, max_results)
  WebSearch.search
    AuthorizationProtocol.check("web_search", {"query": query})
      Deny       -> denial text + permission.denied
      Allow once -> SearchBackend.search(query, limit)
                      TavilySearchBackend   (when TAVILY_API_KEY is set)
                        HttpTransport.post -> POST https://api.tavily.com/search
                      DdgsSearchBackend     (default when the key is empty)
                        TextSearchClient.text -> ddgs metasearch
    formatted results
```

The search runs in the harness process, not in the shell sandbox, and every
query asks for consent; nothing is remembered. A backend failure returns
`Error: web search failed: ...`; an empty result list returns
`No web results found for: <query>`.

### Seams

- `SearchBackend` is a `Protocol`: any object with
  `async search(query, max_results) -> list[SearchResult]` works.
  `SearchResult` has `title`, `url`, `snippet`.
- `HttpTransport` sits one level down, inside Tavily. `UrllibTransport` is the
  production implementation with a 20-second timeout.
- `TextSearchClient` sits one level down, inside `ddgs`. `DdgsClient` is the
  production implementation; tests inject a fake and never open the network.

`main._search_backend` picks Tavily when `TAVILY_API_KEY` is non-empty,
otherwise `ddgs`.

### Tavily request

```json
{
  "query": "...",
  "max_results": 5,
  "search_depth": "basic",
  "include_answer": false
}
```

The key goes in `Authorization: Bearer <key>`, never in the body, and is
replaced with `[redacted]` in any error message. `include_answer` is false on
purpose: the model receives sources, not a provider-written summary. From each
result the backend keeps `title`, `url`, and `content` (as the snippet).

Tavily answers from an index it has already built, so a very recent page may
not appear. The tool neither crawls nor reranks.

### ddgs

`DdgsSearchBackend` calls `TextSearchClient.text(query, max_results=...)` and
maps each hit's `title`, `href`, and `body` to `SearchResult`. No API key.
Results come from public search engines via the `ddgs` package, so rate limits
and HTML changes can break a call; failures surface as
`Error: web search failed: ...`.
