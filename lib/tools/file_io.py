from pathlib import Path

from lib.tools.tool import Tool


def make_read_file(workspace: Path) -> Tool:
    root = workspace.resolve()

    def read_file(path: str) -> str:
        target = (root / path).expanduser().resolve()
        if not target.is_relative_to(root):
            return (
                "Error: path is outside the workspace. Use run_command if you need "
                "a path outside the project (that requires user elevation)."
            )
        if not target.is_file():
            return f"Error: file not found: {path}"
        return target.read_text(encoding="utf-8")

    return Tool(
        name="read_file",
        description=(
            "Read a text file relative to the workspace. Faster than cat for a "
            "single file. Cannot leave the workspace; use run_command for that."
        ),
        arguments={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to the workspace",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        function=read_file,
    )


def make_write_file(workspace: Path) -> Tool:
    root = workspace.resolve()

    def write_file(path: str, content: str) -> str:
        target = (root / path).expanduser().resolve()
        if not target.is_relative_to(root):
            return (
                "Error: path is outside the workspace. Use run_command if you need "
                "to write outside the project (that requires user elevation)."
            )
        if target.exists() and not target.is_file():
            return f"Error: path is not a file: {path}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to {path}"

    return Tool(
        name="write_file",
        description=(
            "Write a text file relative to the workspace. No approval: the "
            "workspace bound is the lock. Cannot leave the workspace."
        ),
        arguments={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to the workspace",
                },
                "content": {
                    "type": "string",
                    "description": "Text content to write",
                },
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        function=write_file,
    )
