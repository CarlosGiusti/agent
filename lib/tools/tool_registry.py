from pathlib import Path

from lib.mcp.adapter import tool_from_mcp
from lib.mcp.mcp_client import MCPClient
from lib.tools.file_io import make_read_file, make_write_file
from lib.tools.shell import Shell, make_run_command
from lib.tools.tool import Tool


def make_local_tools(workspace: Path, shell: Shell, *tools: Tool) -> list[Tool]:
    return [
        make_read_file(workspace),
        make_write_file(workspace),
        make_run_command(shell),
        *tools,
    ]


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {tool.name: tool for tool in tools}

    async def bootstrap(self, client: MCPClient) -> None:
        for name, mcp_tool in client.tools.items():
            self._tools[name] = tool_from_mcp(name, mcp_tool, client)

    @property
    def tools(self) -> list[Tool]:
        return list(self._tools.values())
