import json
from pathlib import Path

from mcp import ClientSessionGroup, StdioServerParameters
from mcp.client.session_group import SseServerParameters, StreamableHttpParameters
from mcp.types import Implementation, TextContent

from lib.mcp.jsonc import loads as loads_jsonc
from lib.paths.paths import MCP_CONFIG_PATH

MCP_SERVERS_KEY = "mcpServers"


class MCPClient:
    def __init__(self, config_path: Path = MCP_CONFIG_PATH):
        self.config_path = config_path
        self.servers_config: dict[str, dict] = {}

    async def __aenter__(self) -> "MCPClient":
        self.group = ClientSessionGroup(component_name_hook=self.mcp_component_name)
        await self.group.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.group is not None:
            await self.group.__aexit__(exc_type, exc, tb)

    def load_config(self):
        text = self.config_path.read_text(encoding="utf-8")
        config = loads_jsonc(text)
        self.servers_config = config.get(MCP_SERVERS_KEY, {})

    @property
    def tools(self):
        return self.group.tools

    def _stdio_params(self, entry: dict) -> StdioServerParameters:
        return StdioServerParameters(
            command=entry["command"],
            args=entry.get("args", []),
            env=entry.get("env"),
        )

    def _headers(self, entry: dict) -> dict | None:
        if "headers" in entry:
            return entry["headers"]
        request_init = entry.get("requestInit") or {}
        return request_init.get("headers")

    def _url_params(self, entry: dict) -> StreamableHttpParameters | SseServerParameters:
        headers = self._headers(entry)
        if entry.get("type") == "sse":
            return SseServerParameters(url=entry["url"], headers=headers)
        return StreamableHttpParameters(url=entry["url"], headers=headers)

    def _print_server_tools(self, server_name: str, tool_names: list[str]) -> None:
        print(f"MCP {server_name}: connected ({len(tool_names)} tools)")
        for tool_name in tool_names:
            print(f"  - {tool_name}")

    def mcp_component_name(
        self, name: str, server_info: Implementation | None = None
    ) -> str:
        server_name = server_info.name if server_info else "unknown"
        return f"{server_name}.{name}"

    async def connect_servers(self) -> dict[str, str]:
        if not self.servers_config:
            self.load_config()

        results: dict[str, str] = {}

        for name, entry in self.servers_config.items():
            try:
                if "command" in entry:
                    await self.group.connect_to_server(self._stdio_params(entry))
                    results[name] = "connected"
                elif "url" in entry:
                    await self.group.connect_to_server(self._url_params(entry))
                    results[name] = "connected"
                else:
                    status = "failed: unknown server config"
                    results[name] = status
            except Exception as error:
                status = f"failed: {error}"
                results[name] = status
        return results

    async def call_tool(self, name: str, args: dict) -> str:
        result = await self.group.call_tool(name, args)
        if result.structured_content is not None:
            content = result.structured_content
            if isinstance(content, str):
                return content
            return json.dumps(content, ensure_ascii=False, default=str)

        texts = [
            block.text for block in result.content if isinstance(block, TextContent)
        ]
        if texts:
            return "\n".join(texts)
        return str(result.content)
