from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, NotRequired, TypedDict, cast

JsonSchemaType = Literal[
    "string", "number", "integer", "boolean", "array", "object", "null"
]


class JsonSchema(TypedDict, total=False):
    """A JSON Schema node. Nested keywords are part of the contract, not extras."""

    type: JsonSchemaType | list[JsonSchemaType]
    description: str
    properties: dict[str, JsonSchema]
    required: list[str]
    items: JsonSchema
    enum: list[Any]
    additionalProperties: bool | JsonSchema
    default: Any
    title: str


class ArgumentsSchema(TypedDict):
    """Root JSON Schema for tool arguments.

    Same contract as MCP ``Tool.inputSchema``: a JSON Schema object. Servers may
    omit ``properties`` / ``required`` and may include any JSON Schema 2020-12
    keyword (``items``, nested ``properties``, ``$ref``, …). Those keys are
    forwarded to the model; do not strip them.
    """

    type: Literal["object"]
    properties: NotRequired[dict[str, JsonSchema]]
    required: NotRequired[list[str]]
    additionalProperties: NotRequired[bool | JsonSchema]
    description: NotRequired[str]
    title: NotRequired[str]


def to_arguments_schema(schema: Mapping[str, Any] | None) -> ArgumentsSchema:
    """Fill MCP-legal omissions. Extra JSON Schema keys are kept."""
    arguments: dict[str, Any] = dict(schema) if schema else {}
    if "type" not in arguments:
        arguments["type"] = "object"
    if "properties" not in arguments:
        arguments["properties"] = {}
    return cast(ArgumentsSchema, arguments)
