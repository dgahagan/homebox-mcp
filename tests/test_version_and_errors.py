"""Version guard (_check_min_version) and API-error surfacing (_tool_errors)."""
from __future__ import annotations

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError


def test_old_version_blocks_every_tool(router, mock_status):
    mock_status(version="v0.21.3")
    # /tags is intentionally NOT mocked: the guard must fire before any call.
    import homebox_mcp as hb
    with pytest.raises(ToolError) as exc:
        hb.list_tags()
    assert "0.26" in str(exc.value)


def test_supported_version_proceeds(router, mock_status):
    mock_status(version="v0.26.2")
    router.get("/tags").mock(return_value=httpx.Response(200, json=[]))
    import homebox_mcp as hb
    assert hb.list_tags() == []


def test_unreachable_status_fails_open(router, mock_status):
    # /status connect error -> guard swallows it -> tool proceeds to its own call.
    mock_status(side_effect=httpx.ConnectError("boom"))
    router.get("/tags").mock(return_value=httpx.Response(200, json=[]))
    import homebox_mcp as hb
    assert hb.list_tags() == []


def test_api_500_surfaces_status_and_body(router, good_status):
    router.get("/tags").mock(
        return_value=httpx.Response(500, json={"error": "boom detail"})
    )
    import homebox_mcp as hb
    with pytest.raises(ToolError) as exc:
        hb.list_tags()
    msg = str(exc.value)
    assert "500" in msg
    assert "boom detail" in msg
