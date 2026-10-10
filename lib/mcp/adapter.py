from mcp.types import Tool as McpTool

from lib.mcp.mcp_client import MCPClient
from lib.tools.tool import Tool


def tool_from_mcp(name: str, mcp_tool: McpTool, client: MCPClient) -> Tool:
    async def invoke(**args) -> str:
        return await client.call_tool(name, args)

    # input_schema is MCP JSON Schema; Tool fills omitted type/properties only.
    return Tool(
        name=name,
        description=mcp_tool.description or "",
        arguments=mcp_tool.input_schema,
        function=invoke,
    )
