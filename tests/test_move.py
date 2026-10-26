"""move_item: the destination may be a location or (0.26+) a container item."""
from __future__ import annotations

import json

import httpx

# Locations (from /entities/tree): Office (o1), Car (c1).
TREE = [
    {"id": "o1", "name": "Office", "children": []},
    {"id": "c1", "name": "Car", "children": []},
]


def _tree_ids(nodes):
    out = {}
    for n in nodes:
        out[n["name"]] = n["id"]
        out.update(_tree_ids(n.get("children") or []))
    return out


def _inventory(router, items, tree=TREE, broken_paths=()):
    """Mock a small inventory. `items` maps id -> (name, path names incl. self)
    or (name, path, asset number); the asset number defaults to the item's
    position, counting from 1.

    /entities?q= filters by case-insensitive name substring (items only, as
    in Homebox); /entities/{id}/path returns the given path with ids (500 for
    ids in `broken_paths`); /assets/{ref} parses `ref` the way Homebox does —
    strip '-', then read an integer — so '12' finds asset 000-012. Returns
    (patch routes by id, the /entities search route, the /assets route).
    """
    router.get("/entities/tree").mock(return_value=httpx.Response(200, json=tree))
    ids_by_name = _tree_ids(tree)
    ids_by_name.update({spec[0]: eid for eid, spec in items.items()})
    nums = {eid: spec[2] if len(spec) > 2 else i
            for i, (eid, spec) in enumerate(items.items(), 1)}

    def asset(request):
        ref = request.url.path.rsplit("/", 1)[-1].replace("-", "")
        hits = [eid for eid, n in nums.items() if ref.isdigit() and int(ref) == n]
        return httpx.Response(200, json={"items": [{"id": e} for e in hits]})

    assets = router.get(url__regex=r"/assets/.*").mock(side_effect=asset)

    def search(request):
        q = request.url.params.get("q")
        hits = [{"id": eid} for eid, spec in items.items()
                if (q or "").lower() in spec[0].lower()]
        return httpx.Response(200, json={"items": hits, "total": len(hits)})

    searches = router.get("/entities").mock(side_effect=search)
    for eid, (name, path, *_) in items.items():
        router.get(f"/entities/{eid}").mock(return_value=httpx.Response(200, json={
            "id": eid, "name": name, "assetId": f"000-{nums[eid]:03d}",
            "entityType": {"isLocation": False}, "fields": [],
        }))
        router.get(f"/entities/{eid}/path").mock(return_value=(
            httpx.Response(500) if eid in broken_paths else httpx.Response(
                200, json=[{"id": ids_by_name[n], "name": n} for n in path])))
    patches = {eid: router.patch(f"/entities/{eid}").mock(
        return_value=httpx.Response(200, json={})) for eid in items}
    return patches, searches, assets


def _parent(patch):
    return json.loads(patch.calls.last.request.content)["parentId"]


def test_move_into_item_by_name(router, good_status):
    patches, _, _ = _inventory(router, {
        "bag": ("Camera Bag", ["Office", "Camera Bag"]),
        "lens": ("Lens", ["Office", "Lens"]),
    })
    import homebox_mcp as hb
    assert hb.move_item("Lens", "Camera Bag")["ok"] is True
    assert _parent(patches["lens"]) == "bag"


def test_move_into_item_path_disambiguates(router, good_status):
    patches, _, _ = _inventory(router, {
        "bag1": ("Camera Bag", ["Office", "Camera Bag"]),
        "bag2": ("Camera Bag", ["Car", "Camera Bag"]),
        "lens": ("Lens", ["Office", "Lens"]),
    })
    import homebox_mcp as hb
    assert hb.move_item("Lens", "car/camera bag")["ok"] is True
    assert _parent(patches["lens"]) == "bag2"


def test_move_into_ambiguous_item_refused(router, good_status):
    patches, _, _ = _inventory(router, {
        "bag1": ("Camera Bag", ["Office", "Camera Bag"]),
        "bag2": ("Camera Bag", ["Car", "Camera Bag"]),
        "lens": ("Lens", ["Office", "Lens"]),
    })
    import homebox_mcp as hb
    result = hb.move_item("Lens", "Camera Bag")
    assert "ambiguous — 2 items match" in result["error"]
    assert "Office/Camera Bag (assetId 000-001)" in result["error"]
    assert "Car/Camera Bag (assetId 000-002)" in result["error"]
    assert not patches["lens"].called


def test_move_into_own_descendant_refused(router, good_status):
    patches, _, _ = _inventory(router, {
        "bag": ("Camera Bag", ["Office", "Camera Bag"]),
        "pouch": ("Lens Pouch", ["Office", "Camera Bag", "Lens Pouch"]),
    })
    import homebox_mcp as hb
    result = hb.move_item("Camera Bag", "Lens Pouch")
    assert "into itself" in result["error"]
    assert not patches["bag"].called


def test_move_into_item_by_asset_id(router, good_status):
    patches, _, _ = _inventory(router, {
        "bag1": ("Camera Bag", ["Office", "Camera Bag"]),
        "bag2": ("Camera Bag", ["Car", "Camera Bag"]),
        "lens": ("Lens", ["Office", "Lens"]),
    })
    import homebox_mcp as hb
    assert hb.move_item("Lens", "000-002")["ok"] is True
    assert _parent(patches["lens"]) == "bag2"


def test_move_to_location_makes_no_item_lookups(router, good_status):
    patches, searches, assets = _inventory(router, {
        "lens": ("Lens", ["Office", "Lens"]),
    })
    import homebox_mcp as hb
    assert hb.move_item("000-001", "Car")["ok"] is True
    assert _parent(patches["lens"]) == "c1"
    # one /assets call resolves the moved item; none for the destination
    assert assets.call_count == 1
    assert not searches.called


def test_location_wins_over_item_with_same_name(router, good_status):
    # an item named 'Car' (in Office) and the location 'Car'
    patches, _, _ = _inventory(router, {
        "caritem": ("Car", ["Office", "Car"]),
        "lens": ("Lens", ["Office", "Lens"]),
    })
    import homebox_mcp as hb
    assert hb.move_item("Lens", "Car")["ok"] is True
    assert _parent(patches["lens"]) == "c1"
    # a longer path matches no location, so it reaches the item
    assert hb.move_item("Lens", "Office/Car")["ok"] is True
    assert _parent(patches["lens"]) == "caritem"


def test_location_sharing_an_items_path_stays_reachable(router, good_status):
    # the location House/Office/Desk and an item Desk in House/Office
    tree = [{"id": "h1", "name": "House", "children": [
        {"id": "o1", "name": "Office", "children": [
            {"id": "d1", "name": "Desk", "children": []}]}]}]
    patches, _, _ = _inventory(router, {
        "desk": ("Desk", ["House", "Office", "Desk"]),
        "lens": ("Lens", ["House", "Office", "Lens"]),
    }, tree=tree)
    import homebox_mcp as hb
    for ref in ("Desk", "Office/Desk", "House/Office/Desk"):
        assert hb.move_item("Lens", ref)["ok"] is True
        assert _parent(patches["lens"]) == "d1"
    assert hb.move_item("Lens", "000-001")["ok"] is True
    assert _parent(patches["lens"]) == "desk"


def test_numbered_location_is_not_read_as_asset_id(router, good_status):
    # Homebox reads '12' as asset 000-012; the bin must still win
    tree = [{"id": "g1", "name": "Garage", "children": [
        {"id": "b12", "name": "12", "children": []}]}]
    patches, _, _ = _inventory(router, {
        "bolt": ("Bolt", ["Garage", "Bolt"], 12),
        "lens": ("Lens", ["Garage", "Lens"]),
    }, tree=tree)
    import homebox_mcp as hb
    for ref in ("12", "Garage/12"):
        assert hb.move_item("Lens", ref)["ok"] is True
        assert _parent(patches["lens"]) == "b12"
    # even when the item with that assetId is the one being moved
    assert hb.move_item("Bolt", "12")["ok"] is True
    assert _parent(patches["bolt"]) == "b12"


def test_item_named_like_an_asset_id_found_by_name(router, good_status):
    # '000-5' is not the strict assetId form, so it is matched by name
    patches, _, _ = _inventory(router, {
        "box": ("000-5", ["Office", "000-5"]),
        "lens": ("Lens", ["Office", "Lens"], 5),
    })
    import homebox_mcp as hb
    assert hb.move_item("Lens", "000-5")["ok"] is True
    assert _parent(patches["lens"]) == "box"


def test_item_with_unreadable_path_is_skipped(router, good_status):
    patches, _, _ = _inventory(router, {
        "bag1": ("Camera Bag", ["Office", "Camera Bag"]),
        "bag2": ("Camera Bag", ["Car", "Camera Bag"]),
        "lens": ("Lens", ["Office", "Lens"]),
    }, broken_paths={"bag2"})
    import homebox_mcp as hb
    assert hb.move_item("Lens", "Office/Camera Bag")["ok"] is True
    assert _parent(patches["lens"]) == "bag1"


def test_ambiguous_location_suggests_a_path(router, good_status):
    tree = TREE + [{"id": "g1", "name": "Garage", "children": [
        {"id": "s1", "name": "Shelf", "children": []}]},
        {"id": "a1", "name": "Attic", "children": [
            {"id": "s2", "name": "Shelf", "children": []}]}]
    patches, _, _ = _inventory(router, {"lens": ("Lens", ["Office", "Lens"])},
                               tree=tree)
    import homebox_mcp as hb
    result = hb.move_item("Lens", "Shelf")
    assert "2 locations match: Garage/Shelf; Attic/Shelf" in result["error"]
    assert "'Parent/Shelf'" in result["error"]
    assert "assetId" not in result["error"]
    assert not patches["lens"].called


def test_move_unknown_destination(router, good_status):
    patches, _, _ = _inventory(router, {"lens": ("Lens", ["Office", "Lens"])})
    import homebox_mcp as hb
    result = hb.move_item("Lens", "Basement")
    assert "no location or item named 'Basement'" in result["error"]
    assert not patches["lens"].called
