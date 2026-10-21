"""move_item: the destination may be a location or (0.26+) a container item."""
from __future__ import annotations

import json

import httpx

# Locations (from /entities/tree): Office (o1), Car (c1).
TREE = [
    {"id": "o1", "name": "Office", "children": []},
    {"id": "c1", "name": "Car", "children": []},
]


def _inventory(router, items, searches=None):
    """Mock a small inventory. `items` maps id -> (name, path names incl. self).

    /entities?q= filters by case-insensitive name substring (items only, as
    in Homebox); /entities/{id}/path returns the given path with ids; each
    item's assetId is '000-<id>'. Every /entities `q` is appended to
    `searches` (None for an unfiltered full scan).
    """
    router.get("/entities/tree").mock(return_value=httpx.Response(200, json=TREE))
    for eid in items:
        router.get(f"/assets/000-{eid}").mock(return_value=httpx.Response(
            200, json={"items": [{"id": eid}]}))
    router.get(url__regex=r"/assets/.*").mock(return_value=httpx.Response(404))
    ids_by_name = {"Office": "o1", "Car": "c1"}
    ids_by_name.update({name: eid for eid, (name, _) in items.items()})

    def search(request):
        q = request.url.params.get("q")
        if searches is not None:
            searches.append(q)
        hits = [{"id": eid} for eid, (name, _) in items.items()
                if (q or "").lower() in name.lower()]
        return httpx.Response(200, json={"items": hits, "total": len(hits)})

    router.get("/entities").mock(side_effect=search)
    for eid, (name, path) in items.items():
        router.get(f"/entities/{eid}").mock(return_value=httpx.Response(200, json={
            "id": eid, "name": name, "assetId": f"000-{eid}",
            "entityType": {"isLocation": False}, "fields": [],
        }))
        router.get(f"/entities/{eid}/path").mock(return_value=httpx.Response(
            200, json=[{"id": ids_by_name[n], "name": n} for n in path]))
    return {eid: router.patch(f"/entities/{eid}").mock(
        return_value=httpx.Response(200, json={})) for eid in items}


def test_move_into_item_by_name(router, good_status):
    patches = _inventory(router, {
        "bag": ("Camera Bag", ["Office", "Camera Bag"]),
        "lens": ("Lens", ["Office", "Lens"]),
    })
    import homebox_mcp as hb
    result = hb.move_item("Lens", "Camera Bag")
    assert result["ok"] is True
    assert json.loads(patches["lens"].calls.last.request.content)["parentId"] == "bag"


def test_move_into_item_path_disambiguates(router, good_status):
    patches = _inventory(router, {
        "bag1": ("Camera Bag", ["Office", "Camera Bag"]),
        "bag2": ("Camera Bag", ["Car", "Camera Bag"]),
        "lens": ("Lens", ["Office", "Lens"]),
    })
    import homebox_mcp as hb
    result = hb.move_item("Lens", "car/camera bag")
    assert result["ok"] is True
    assert json.loads(patches["lens"].calls.last.request.content)["parentId"] == "bag2"


def test_move_into_ambiguous_item_refused(router, good_status):
    patches = _inventory(router, {
        "bag1": ("Camera Bag", ["Office", "Camera Bag"]),
        "bag2": ("Camera Bag", ["Car", "Camera Bag"]),
        "lens": ("Lens", ["Office", "Lens"]),
    })
    import homebox_mcp as hb
    result = hb.move_item("Lens", "Camera Bag")
    assert "ambiguous" in result["error"]
    assert "000-bag1" in result["error"] and "000-bag2" in result["error"]
    assert not patches["lens"].called


def test_move_into_own_descendant_refused(router, good_status):
    patches = _inventory(router, {
        "bag": ("Camera Bag", ["Office", "Camera Bag"]),
        "pouch": ("Lens Pouch", ["Office", "Camera Bag", "Lens Pouch"]),
    })
    import homebox_mcp as hb
    result = hb.move_item("Camera Bag", "Lens Pouch")
    assert "into itself" in result["error"]
    assert not patches["bag"].called


def test_move_into_item_by_asset_id(router, good_status):
    patches = _inventory(router, {
        "bag1": ("Camera Bag", ["Office", "Camera Bag"]),
        "bag2": ("Camera Bag", ["Car", "Camera Bag"]),
        "lens": ("Lens", ["Office", "Lens"]),
    })
    import homebox_mcp as hb
    result = hb.move_item("Lens", "000-bag2")
    assert result["ok"] is True
    assert json.loads(patches["lens"].calls.last.request.content)["parentId"] == "bag2"


def test_move_to_location_skips_alias_scan(router, good_status):
    # HOMEBOX_ALIAS_FIELD is set (conftest); a location destination must not
    # trigger the full-inventory alias scan that _resolve_exact falls back to
    searches: list = []
    patches = _inventory(router, {"lens": ("Lens", ["Office", "Lens"])}, searches)
    import homebox_mcp as hb
    result = hb.move_item("Lens", "Car")
    assert result["ok"] is True
    assert json.loads(patches["lens"].calls.last.request.content)["parentId"] == "c1"
    assert None not in searches


def test_move_location_item_name_clash_refused(router, good_status):
    # an item named 'Car' (in Office) and the location 'Car'
    patches = _inventory(router, {
        "caritem": ("Car", ["Office", "Car"]),
        "lens": ("Lens", ["Office", "Lens"]),
    })
    import homebox_mcp as hb
    result = hb.move_item("Lens", "Car")
    assert "ambiguous" in result["error"]
    assert "location Car" in result["error"]
    assert "item Office/Car (assetId 000-caritem)" in result["error"]
    assert not patches["lens"].called
    # a longer path picks the item
    assert hb.move_item("Lens", "Office/Car")["ok"] is True
    assert json.loads(patches["lens"].calls.last.request.content)["parentId"] == "caritem"


def test_move_unknown_destination(router, good_status):
    patches = _inventory(router, {"lens": ("Lens", ["Office", "Lens"])})
    import homebox_mcp as hb
    result = hb.move_item("Lens", "Basement")
    assert "no location or item named 'Basement'" in result["error"]
    assert not patches["lens"].called
