from pathlib import Path

from lib.agents.types import Message
from lib.paths.paths import AGENT_HOME, workspace_agent_dir

RULE_ROLE = "rule"
RULES_DIR = "rules"


def rule_paths(global_dir: Path, workspace_dir: Path) -> list[Path]:
    return [
        *_markdown_paths(global_dir),
        *_markdown_paths(workspace_dir),
    ]


def resolve_rules(global_dir: Path, workspace_dir: Path) -> list[Message]:
    return [_message(path) for path in rule_paths(global_dir, workspace_dir)]


def rule_messages(workspace: Path, root: Path = AGENT_HOME) -> list[Message]:
    directory = workspace_agent_dir(workspace)
    paths = rule_paths(root, directory)
    if paths:
        print("rules: " + ", ".join(str(path) for path in paths))
    return resolve_rules(root, directory)


def _markdown_paths(directory: Path) -> list[Path]:
    rules_dir = directory / RULES_DIR
    if not rules_dir.is_dir():
        return []
    paths = sorted(
        (path for path in rules_dir.glob("*.md") if path.is_file()),
        key=lambda path: path.name,
    )
    return [path.resolve() for path in paths]


def _message(path: Path) -> Message:
    return Message(role=RULE_ROLE, content=path.read_text(encoding="utf-8"))
