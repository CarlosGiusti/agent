import asyncio
import os
from pathlib import Path

from lib.agents.agent import Agent
from lib.agents.claude import ClaudeProvider
from lib.agents.ollama import OllamaProvider
from lib.agents.provider import Provider
from lib.agents.types import Message
from lib.authorization.authorization import Authorization
from lib.authorization.protocol import PromptAuthorizationProtocol
from lib.client.session import Session
from lib.config.config import Config, load_dotenv
from lib.events.event_system import EventSystem, EventType
from lib.hooks.hooks import SubprocessHookProcess, open_hooks
from lib.mcp.mcp_client import MCPClient
from lib.rules.rules import rule_messages
from lib.sandbox.sandbox import BwrapSandbox
from lib.skills.skills import skill_messages
from lib.tools.shell import Shell
from lib.tools.tavily import TavilySearchBackend, UrllibTransport
from lib.tools.tool import Tool
from lib.tools.tool_registry import ToolRegistry, make_local_tools
from lib.tools.web_search import make_web_search


async def main():
    load_dotenv(Path(__file__).resolve().parent / ".env", os.environ)
    config = Config.load()
    session = Session()
    config.events.on(EventType.PROVIDER_DELTA, session.show_delta)
    authorization = Authorization(
        protocol=PromptAuthorizationProtocol(session.ask_elevation)
    )
    shell = Shell(
        sandbox=BwrapSandbox(config.args.workspace),
        authorization=authorization,
        workspace=config.args.workspace,
        events=config.events,
    )
    async with MCPClient() as mcp_client:
        await mcp_client.connect_servers()
        workspace = config.args.workspace
        rules = rule_messages(workspace)
        skills, load_skill = skill_messages(workspace)
        hooks = open_hooks(workspace, SubprocessHookProcess(), config.events)
        local_tools = [load_skill]
        web_search = _web_search(session, config.events)
        if web_search is not None:
            local_tools.append(web_search)
        registry = ToolRegistry(make_local_tools(workspace, shell, *local_tools))
        await registry.bootstrap(mcp_client)

        agent: Agent = Agent(
            provider=_provider(),
            context=[*rules, *skills],
            tools=registry.tools,
            events=config.events,
            hooks=hooks,
        )
        while True:
            message = session.read_message()
            try:
                response = await agent.send(Message(role="user", content=message))
            except Exception as exc:  # noqa: BLE001 — keep the session open after a provider error
                session.show_error(str(exc))
                continue
            session.finish_response(response)


def _provider() -> Provider:
    name = os.environ.get("AGENT_PROVIDER", "ollama") or "ollama"
    if name == "ollama":
        return OllamaProvider(model="qwen3:8b")
    if name == "claude":
        return ClaudeProvider(
            model="claude-sonnet-5-5",
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        )
    raise SystemExit(f"unknown AGENT_PROVIDER: {name}")


def _web_search(session: Session, events: EventSystem) -> Tool | None:
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        return None
    return make_web_search(
        TavilySearchBackend(api_key, UrllibTransport()),
        PromptAuthorizationProtocol(session.ask_web_search),
        events,
    )


if __name__ == "__main__":
    asyncio.run(main())
