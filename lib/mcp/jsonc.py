"""JSON with comments (JSONC), as used by VS Code / Cursor mcp.json."""

import json
from typing import Any


def loads(text: str) -> Any:
    return json.loads(_strip(text))


def _strip(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False

    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""

        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue

        if ch == "/" and nxt == "/":
            i += 2
            while i < n and text[i] not in "\r\n":
                i += 1
            continue

        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                if text[i] in "\r\n":
                    out.append(text[i])
                i += 1
            i = i + 2 if i + 1 < n else n
            continue

        if ch in "}]":
            _drop_trailing_comma(out)
            out.append(ch)
            i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _drop_trailing_comma(out: list[str]) -> None:
    i = len(out)
    while i > 0 and out[i - 1].isspace():
        i -= 1
    if i > 0 and out[i - 1] == ",":
        del out[i - 1]
