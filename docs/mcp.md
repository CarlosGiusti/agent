# MCP

MCP servers contribute tools next to the local ones. They run with the
authority of the host process: no sandbox, no `Authorization` prompt.

## Configuration

`~/.agent/mcp.json` (global only). The file must exist; an empty
`{"mcpServers": {}}` is valid. It is parsed as JSONC: `//` and `/* */`
comments and trailing commas are allowed, matching the VS Code / Cursor
format.

```jsonc
{
  "mcpServers": {
    // stdio server
    "postgres": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://..."],
      "env": { "PGPASSWORD": "..." }
    },
    // Streamable HTTP server
    "docs": {
      "url": "https://example.com/mcp",
      "headers": { "Authorization": "Bearer ..." }
    },
    // SSE server
    "legacy": { "url": "https://example.com/sse", "type": "sse" }
  }
}
```

| Entry has | Transport | Fields |
| --- | --- | --- |
| `command` | stdio | `command`, optional `args`, optional `env` |
| `url` | Streamable HTTP | `url`, optional `headers` (or `requestInit.headers`) |
| `url` + `"type": "sse"` | SSE | same as HTTP |

## Connection

`MCPClient` is an async context manager around the MCP SDK's
`ClientSessionGroup`. `main.py` opens it for the whole session and calls
`connect_servers()`, which returns a `name -> status` map. A server that fails
to connect (or has neither `command` nor `url`) is recorded as
`failed: ...` and skipped; the others still connect.

## Tools

`ToolRegistry.bootstrap` turns each MCP tool into a regular `Tool`:

- **Name:** `<server>.<tool>`, where `<server>` is the name the server reports
  in its initialization info, not the key in `mcp.json`.
- **Description:** the server's description.
- **Arguments:** the server's `inputSchema`, forwarded as-is (only a missing
  `type` / `properties` is filled in). See [tools.md](tools.md#arguments-are-json-schema).
- **Call:** `MCPClient.call_tool(name, args)` returns a string: the
  structured content as JSON if present, otherwise the text blocks joined by
  newlines, otherwise the raw content rendered as a string.

MCP tools are added after local tools and overwrite any local tool with the
same name. Errors raised by a call are caught by the loop and returned to the
model like any other tool failure.

## Trust

MCP is outside both sandbox boxes. A server can read files, use the network,
and act on external systems with whatever credentials it was given. This is
one of the documented holes in the permission model; see
[authorization-and-sandbox.md](authorization-and-sandbox.md#composition-hole).
