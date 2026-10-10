from lib.tools.tool import Tool


def make_load_skill(bodies: dict[str, str]) -> Tool:
    known = dict(bodies)

    def load_skill(name: str) -> str:
        body = known.get(name)
        if body is None:
            return f"Error: unknown skill: {name}"
        return body

    return Tool(
        name="load_skill",
        description=(
            "Load the body of a skill by name. Call this when a skill in the "
            "index matches the user's request, before following that procedure."
        ),
        arguments={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name from the index",
                }
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        function=load_skill,
    )
