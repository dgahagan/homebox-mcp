"""create_item: `location` resolves like move_item's destination (#18)."""
from __future__ import annotations

import json

import httpx
from test_move import _inventory


def _create_routes(router):
    """POST /entities -> new id 'new'; PUT, ensure-asset-ids and the final
    GET of the new item. Returns (post route, put route)."""
    post = router.post("/entities").mock(
        return_value=httpx.Response(200, json={"id": "new"}))
    put = router.put("/entities/new").mock(return_value=httpx.Response(200, json={}))
    router.post("/actions/ensure-asset-ids").mock(return_value=httpx.Response(200, json={}))
    router.get("/entities/new").mock(return_value=httpx.Response(
        200, json={"id": "new", "name": "Lens", "assetId": "000-099", "fields": []}))
    return post, put


def _parents(post, put):
    return (json.loads(post.calls.last.request.content).get("parentId"),
            json.loads(put.calls.last.request.content).get("parentId"))


def test_create_into_item_by_name(router, good_status):
    _inventory(router, {"bag": ("Camera Bag", ["Office", "Camera Bag"])})
    post, put = _create_routes(router)
    import homebox_mcp as hb
    assert hb.create_item("Lens", location="Camera Bag")["id"] == "new"
    assert _parents(post, put) == ("bag", "bag")


def test_create_into_item_by_path(router, good_status):
    _inventory(router, {
        "bag1": ("Camera Bag", ["Office", "Camera Bag"]),
        "bag2": ("Camera Bag", ["Car", "Camera Bag"]),
    })
    post, put = _create_routes(router)
    import homebox_mcp as hb
    hb.create_item("Lens", location="Car/Camera Bag")
    assert _parents(post, put) == ("bag2", "bag2")


def test_create_into_item_by_asset_id(router, good_status):
    _inventory(router, {
        "bag1": ("Camera Bag", ["Office", "Camera Bag"]),
        "bag2": ("Camera Bag", ["Car", "Camera Bag"]),
    })
    post, put = _create_routes(router)
    import homebox_mcp as hb
    hb.create_item("Lens", location="000-002")
    assert _parents(post, put) == ("bag2", "bag2")


def test_create_location_wins_over_same_named_item(router, good_status):
    _inventory(router, {"desk": ("Office", ["Car", "Office"])})
    post, put = _create_routes(router)
    import homebox_mcp as hb
    hb.create_item("Lens", location="Office")
    assert _parents(post, put) == ("o1", "o1")


def test_create_into_ambiguous_item_refused(router, good_status):
    _inventory(router, {
        "bag1": ("Camera Bag", ["Office", "Camera Bag"]),
        "bag2": ("Camera Bag", ["Car", "Camera Bag"]),
    })
    post, _ = _create_routes(router)
    import homebox_mcp as hb
    result = hb.create_item("Lens", location="Camera Bag")
    assert "ambiguous — 2 items match" in result["error"]
    assert not post.called


def test_create_unknown_location_refused(router, good_status):
    _inventory(router, {})
    post, _ = _create_routes(router)
    import homebox_mcp as hb
    result = hb.create_item("Lens", location="Nowhere")
    assert "no location or item named 'Nowhere'" in result["error"]
    assert not post.called


def test_create_without_location_has_no_parent(router, good_status):
    post, put = _create_routes(router)
    import homebox_mcp as hb
    hb.create_item("Lens")
    assert _parents(post, put) == (None, None)
