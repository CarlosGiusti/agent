from pathlib import Path

AGENT_DIR = ".agent"
AGENT_HOME = Path.home() / AGENT_DIR
MCP_CONFIG_PATH = AGENT_HOME / "mcp.json"


def workspace_agent_dir(workspace: Path) -> Path:
    return workspace / AGENT_DIR
