"""Confirm-gated deletes must not issue the DELETE unless confirm matches."""
from __future__ import annotations

import httpx


def _mock_item(router, name="Widget", eid="e1", asset="000-9"):
    """Wire up _resolve_exact to find one non-location item named `name`."""
    router.get(f"/assets/{name}").mock(return_value=httpx.Response(404))
    router.get("/entities").mock(
        return_value=httpx.Response(200, json={"items": [{"id": eid}], "total": 1})
    )
    router.get(f"/entities/{eid}").mock(
        return_value=httpx.Response(200, json={
            "id": eid, "name": name, "assetId": asset,
            "entityType": {"isLocation": False}, "fields": [],
        })
    )


def test_delete_item_wrong_confirm_blocks_delete(router, good_status):
    _mock_item(router)
    delete = router.delete("/entities/e1").mock(return_value=httpx.Response(204))
    import homebox_mcp as hb
    result = hb.delete_item("Widget", confirm="nope")
    assert "confirm must equal" in result["error"]
    assert not delete.called


def test_delete_item_correct_confirm_issues_delete(router, good_status):
    _mock_item(router)
    delete = router.delete("/entities/e1").mock(return_value=httpx.Response(204))
    import homebox_mcp as hb
    result = hb.delete_item("Widget", confirm="Widget")
    assert result["ok"] is True
    assert delete.called


def test_delete_tag_wrong_confirm_blocks_delete(router, good_status):
    router.get("/tags").mock(
        return_value=httpx.Response(200, json=[{"id": "t1", "name": "junk"}])
    )
    delete = router.delete("/tags/t1").mock(return_value=httpx.Response(204))
    import homebox_mcp as hb
    result = hb.delete_tag("junk", confirm="wrong")
    assert "confirm must equal" in result["error"]
    assert not delete.called


def test_delete_tag_correct_confirm_issues_delete(router, good_status):
    router.get("/tags").mock(
        return_value=httpx.Response(200, json=[{"id": "t1", "name": "junk"}])
    )
    delete = router.delete("/tags/t1").mock(return_value=httpx.Response(204))
    import homebox_mcp as hb
    result = hb.delete_tag("junk", confirm="junk")
    assert result["ok"] is True
    assert delete.called


def test_delete_location_nonempty_refused(router, good_status):
    # tree: Garage (g1) has a child sub-location, so it's non-empty.
    tree = [{
        "id": "g1", "name": "Garage",
        "children": [{"id": "s1", "name": "Shelf", "children": []}],
    }]
    router.get("/entities/tree").mock(return_value=httpx.Response(200, json=tree))
    router.get("/entities").mock(
        return_value=httpx.Response(200, json={"items": [], "total": 0})
    )
    delete = router.delete("/entities/g1").mock(return_value=httpx.Response(204))
    import homebox_mcp as hb
    result = hb.delete_location("Garage", confirm="Garage")
    assert "not deleted" in result["error"]
    assert "sub-location" in result["error"]
    assert not delete.called
