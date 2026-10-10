from dataclasses import dataclass
from pathlib import Path

from lib.agents.types import Message
from lib.paths.paths import AGENT_HOME, workspace_agent_dir
from lib.rules.rules import RULE_ROLE
from lib.tools.load_skill import make_load_skill
from lib.tools.tool import Tool

SKILLS_DIR = "skills"
_FRONTMATTER_START = "---\n"
_FRONTMATTER_END = "\n---\n"


@dataclass(frozen=True)
class SkillCatalog:
    index: dict[str, str]
    bodies: dict[str, str]


def resolve_skills(root_dir: Path, workspace_dir: Path) -> SkillCatalog:
    index: dict[str, str] = {}
    bodies: dict[str, str] = {}
    for directory in (root_dir, workspace_dir):
        for path in _markdown_paths(directory):
            parsed = _parse_skill(path.read_text(encoding="utf-8"))
            if parsed is None:
                continue
            name, description, body = parsed
            index[name] = description
            bodies[name] = body
    return SkillCatalog(index=index, bodies=bodies)


def skill_message(catalog: SkillCatalog) -> Message | None:
    if not catalog.index:
        return None
    lines = [f"{name}: {description}" for name, description in catalog.index.items()]
    return Message(role=RULE_ROLE, content="skills:\n" + "\n".join(lines))


def skill_messages(
    workspace: Path, root: Path = AGENT_HOME
) -> tuple[list[Message], Tool]:
    catalog = resolve_skills(root, workspace_agent_dir(workspace))
    if catalog.index:
        print("skills: " + ", ".join(catalog.index))
    message = skill_message(catalog)
    messages = [] if message is None else [message]
    return messages, make_load_skill(catalog.bodies)


def _markdown_paths(directory: Path) -> list[Path]:
    skills_dir = directory / SKILLS_DIR
    if not skills_dir.is_dir():
        return []
    paths = sorted(
        (path for path in skills_dir.glob("*.md") if path.is_file()),
        key=lambda path: path.name,
    )
    return [path.resolve() for path in paths]


def _parse_skill(text: str) -> tuple[str, str, str] | None:
    if not text.startswith(_FRONTMATTER_START):
        return None
    end = text.find(_FRONTMATTER_END, len(_FRONTMATTER_START))
    if end == -1:
        return None
    header = text[len(_FRONTMATTER_START) : end]
    body = text[end + len(_FRONTMATTER_END) :]
    fields: dict[str, str] = {}
    for line in header.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    name = fields.get("name", "")
    description = fields.get("description", "")
    if not name or not description:
        return None
    return name, description, body
