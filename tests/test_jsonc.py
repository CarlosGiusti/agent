from lib.mcp.jsonc import loads


def test_loads_plain_json():
    assert loads('{"mcpServers": {}}') == {"mcpServers": {}}


def test_loads_line_comments_without_touching_urls():
    text = """
    {
      // skip this server
      "url": "https://example.com/mcp/",
      "note": "use // in docs"
    }
    """

    assert loads(text) == {
        "url": "https://example.com/mcp/",
        "note": "use // in docs",
    }


def test_loads_block_comments_and_trailing_commas():
    text = """
    {
      "a": 1, /* ignore */
      "b": [2, 3,],
    }
    """

    assert loads(text) == {"a": 1, "b": [2, 3]}
