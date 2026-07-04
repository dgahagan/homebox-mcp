"""_find_location: resolve a location by bare name or /-separated path suffix."""
from __future__ import annotations

import httpx


def _tree(router, tree):
    router.get("/entities/tree").mock(return_value=httpx.Response(200, json=tree))


def test_find_location_unique_bare_name(router, good_status):
    _tree(router, [{
        "id": "g1", "name": "Garage",
        "children": [{"id": "s1", "name": "Shelf", "children": []}],
    }])
    import homebox_mcp as hb
    match, err = hb._find_location("Shelf")
    assert err is None
    assert match["id"] == "s1"
    assert match["path"] == "Garage/Shelf"


def test_find_location_ambiguous_bare_name(router, good_status):
    _tree(router, [
        {"id": "a", "name": "Parent A",
         "children": [{"id": "sa", "name": "Shelf", "children": []}]},
        {"id": "b", "name": "Parent B",
         "children": [{"id": "sb", "name": "Shelf", "children": []}]},
    ])
    import homebox_mcp as hb
    match, err = hb._find_location("Shelf")
    assert match is None
    assert "ambiguous" in err
    assert "Parent A/Shelf" in err
    assert "Parent B/Shelf" in err


def test_find_location_path_suffix_disambiguates(router, good_status):
    _tree(router, [
        {"id": "a", "name": "Parent A",
         "children": [{"id": "sa", "name": "Shelf", "children": []}]},
        {"id": "b", "name": "Parent B",
         "children": [{"id": "sb", "name": "Shelf", "children": []}]},
    ])
    import homebox_mcp as hb
    match, err = hb._find_location("Parent B/Shelf")
    assert err is None
    assert match["id"] == "sb"


def test_find_location_unknown(router, good_status):
    _tree(router, [{"id": "g1", "name": "Garage", "children": []}])
    import homebox_mcp as hb
    match, err = hb._find_location("Basement")
    assert match is None
    assert "no location named" in err
