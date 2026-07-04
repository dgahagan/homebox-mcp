"""FastMCP smoke test: tools are registered with the right schemas + hints."""
from __future__ import annotations

import asyncio

import homebox_mcp as hb


def _tools_by_name():
    tools = asyncio.run(hb.mcp.list_tools())
    return tools, {t.name: t for t in tools}


def test_many_tools_registered():
    tools, _ = _tools_by_name()
    assert len(tools) > 25


def test_search_items_schema_properties():
    _, by_name = _tools_by_name()
    props = by_name["search_items"].inputSchema.get("properties", {})
    assert set(props) == {"query", "tags", "limit"}


def test_delete_item_is_destructive():
    _, by_name = _tools_by_name()
    assert by_name["delete_item"].annotations.destructiveHint is True


def test_get_item_is_read_only():
    _, by_name = _tools_by_name()
    assert by_name["get_item"].annotations.readOnlyHint is True
