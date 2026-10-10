import json
from pathlib import Path
from typing import cast

from mcp import ClientSessionGroup, StdioServerParameters
from mcp.client.session_group import SseServerParameters, StreamableHttpParameters
from mcp.types import CallToolResult, Implementation, TextContent

from lib.mcp.mcp_client import MCPClient

# MCPClient always constructs mcp.ClientSessionGroup in __aenter__.
# That library is the only MCP backend we use, so the group is not injected.
# Tests assign a fake to client.group and skip the context manager.


class FakeSessionGroup:
    def __init__(
        self,
        *,
        tools: dict | None = None,
        connect_error: Exception | None = None,
        call_results: list[CallToolResult] | None = None,
    ) -> None:
        self.tools = tools if tools is not None else {}
        self.connect_calls: list[
            StdioServerParameters | StreamableHttpParameters | SseServerParameters
        ] = []
        self.call_tool_calls: list[tuple[str, dict]] = []
        self._connect_error = connect_error
        self._call_results = list(call_results or [])

    async def connect_to_server(
        self,
        server_params: StdioServerParameters
        | StreamableHttpParameters
        | SseServerParameters,
    ):
        self.connect_calls.append(server_params)
        if self._connect_error is not None:
            raise self._connect_error

    async def call_tool(self, name: str, args: dict) -> CallToolResult:
        self.call_tool_calls.append((name, args))
        if not self._call_results:
            raise AssertionError(f"unexpected call_tool({name!r})")
        return self._call_results.pop(0)


def _client(
    tmp_path: Path,
    group: FakeSessionGroup,
    servers: dict[str, dict] | None = None,
    config: dict | None = None,
) -> MCPClient:
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps(config or {}), encoding="utf-8")
    client = MCPClient(config_path=path)
    client.group = cast(ClientSessionGroup, group)
    if servers is not None:
        client.servers_config = servers
    return client


def test_load_config_reads_mcp_servers(tmp_path: Path):
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {"command": "npx", "args": ["-y", "server-github"]},
                }
            }
        ),
        encoding="utf-8",
    )
    client = MCPClient(config_path=path)

    client.load_config()

    assert client.servers_config == {
        "github": {"command": "npx", "args": ["-y", "server-github"]},
    }


def test_load_config_defaults_to_empty_when_key_is_missing(tmp_path: Path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"other": True}), encoding="utf-8")
    client = MCPClient(config_path=path)

    client.load_config()

    assert client.servers_config == {}


def test_load_config_allows_comments_and_trailing_commas(tmp_path: Path):
    path = tmp_path / "mcp.json"
    path.write_text(
        """
        {
          // github remote
          "mcpServers": {
            "github": {
              "command": "npx",
              "url": "https://api.githubcopilot.com/mcp/", // keep the slashes
            },
            /* "off": { "command": "nope" }, */
          }
        }
        """,
        encoding="utf-8",
    )
    client = MCPClient(config_path=path)

    client.load_config()

    assert client.servers_config == {
        "github": {
            "command": "npx",
            "url": "https://api.githubcopilot.com/mcp/",
        }
    }


def test_mcp_component_name_prefixes_server_name():
    client = MCPClient()

    name = client.mcp_component_name(
        "create_issue",
        Implementation(name="github", version="1.0"),
    )

    assert name == "github.create_issue"


def test_mcp_component_name_uses_unknown_without_server_info():
    client = MCPClient()

    assert client.mcp_component_name("search") == "unknown.search"


def test_tools_exposes_group_tools(tmp_path: Path):
    group = FakeSessionGroup(tools={"github.search": object()})
    client = _client(tmp_path, group)

    assert client.tools is group.tools


async def test_connect_servers_connects_stdio_command(tmp_path: Path):
    group = FakeSessionGroup()
    client = _client(
        tmp_path,
        group,
        servers={
            "github": {
                "command": "npx",
                "args": ["-y", "server-github"],
                "env": {"TOKEN": "x"},
            }
        },
    )

    results = await client.connect_servers()

    assert results == {"github": "connected"}
    assert len(group.connect_calls) == 1
    params = group.connect_calls[0]
    assert isinstance(params, StdioServerParameters)
    assert params.command == "npx"
    assert params.args == ["-y", "server-github"]
    assert params.env == {"TOKEN": "x"}


async def test_connect_servers_connects_url_as_streamable_http(tmp_path: Path):
    group = FakeSessionGroup()
    client = _client(
        tmp_path,
        group,
        servers={
            "github": {
                "url": "https://api.githubcopilot.com/mcp/",
                "headers": {"Authorization": "Bearer test-token"},
            }
        },
    )

    results = await client.connect_servers()

    assert results == {"github": "connected"}
    assert len(group.connect_calls) == 1
    params = group.connect_calls[0]
    assert isinstance(params, StreamableHttpParameters)
    assert params.url == "https://api.githubcopilot.com/mcp/"
    assert params.headers == {"Authorization": "Bearer test-token"}


async def test_connect_servers_reads_headers_from_request_init(tmp_path: Path):
    group = FakeSessionGroup()
    client = _client(
        tmp_path,
        group,
        servers={
            "github": {
                "url": "https://api.githubcopilot.com/mcp/",
                "requestInit": {
                    "headers": {"Authorization": "Bearer vscode-style"},
                },
            }
        },
    )

    results = await client.connect_servers()

    assert results == {"github": "connected"}
    params = group.connect_calls[0]
    assert isinstance(params, StreamableHttpParameters)
    assert params.headers == {"Authorization": "Bearer vscode-style"}


async def test_connect_servers_connects_sse_url(tmp_path: Path):
    group = FakeSessionGroup()
    client = _client(
        tmp_path,
        group,
        servers={
            "remote": {
                "type": "sse",
                "url": "http://localhost:3000/sse",
            }
        },
    )

    results = await client.connect_servers()

    assert results == {"remote": "connected"}
    params = group.connect_calls[0]
    assert isinstance(params, SseServerParameters)
    assert params.url == "http://localhost:3000/sse"


async def test_connect_servers_fails_unknown_config(tmp_path: Path):
    group = FakeSessionGroup()
    client = _client(
        tmp_path,
        group,
        servers={"broken": {"transport": "foo"}},
    )

    results = await client.connect_servers()

    assert results == {"broken": "failed: unknown server config"}
    assert group.connect_calls == []


async def test_connect_servers_loads_config_when_empty(tmp_path: Path):
    group = FakeSessionGroup()
    client = _client(
        tmp_path,
        group,
        config={
            "mcpServers": {"echo": {"command": "uvx", "args": ["mcp-echo"]}},
        },
    )

    results = await client.connect_servers()

    assert results == {"echo": "connected"}
    params = group.connect_calls[0]
    assert isinstance(params, StdioServerParameters)
    assert params.command == "uvx"
    assert params.args == ["mcp-echo"]


async def test_connect_servers_reports_connect_error(tmp_path: Path):
    group = FakeSessionGroup(connect_error=RuntimeError("spawn failed"))
    client = _client(
        tmp_path,
        group,
        servers={"github": {"command": "npx"}},
    )

    results = await client.connect_servers()

    assert results == {"github": "failed: spawn failed"}
    assert len(group.connect_calls) == 1


async def test_call_tool_returns_structured_string(tmp_path: Path):
    group = FakeSessionGroup(
        call_results=[CallToolResult(content=[], structured_content="ok")]
    )
    client = _client(tmp_path, group)

    result = await client.call_tool("github.search", {"q": "agent"})

    assert result == "ok"
    assert group.call_tool_calls == [("github.search", {"q": "agent"})]


async def test_call_tool_dumps_structured_object(tmp_path: Path):
    group = FakeSessionGroup(
        call_results=[
            CallToolResult(content=[], structured_content={"id": 1, "ok": True})
        ]
    )
    client = _client(tmp_path, group)

    result = await client.call_tool("github.create_issue", {"title": "x"})

    assert json.loads(result) == {"id": 1, "ok": True}
    assert group.call_tool_calls == [("github.create_issue", {"title": "x"})]


async def test_call_tool_joins_text_content(tmp_path: Path):
    group = FakeSessionGroup(
        call_results=[
            CallToolResult(
                content=[
                    TextContent(type="text", text="line one"),
                    TextContent(type="text", text="line two"),
                ]
            )
        ]
    )
    client = _client(tmp_path, group)

    result = await client.call_tool("read", {})

    assert result == "line one\nline two"


async def test_call_tool_stringifies_content_without_text(tmp_path: Path):
    group = FakeSessionGroup(call_results=[CallToolResult(content=[])])
    client = _client(tmp_path, group)

    result = await client.call_tool("noop", {})

    assert result == str([])
