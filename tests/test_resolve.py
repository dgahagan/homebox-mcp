"""Tests for _resolve_exact — the WRITE-path resolver (assetId/alias/name only).

HOMEBOX_ALIAS_FIELD is 'item_id' (set in conftest).
"""
from __future__ import annotations

import httpx


def _entity(eid, name, item_id=None, asset=None):
    fields = []
    if item_id is not None:
        fields.append({"name": "item_id", "type": "text", "textValue": item_id})
    return {"id": eid, "name": name, "assetId": asset, "entityType": {},
            "fields": fields}


def test_resolve_exact_name_match(router, good_status):
    router.get("/assets/Cordless Drill").mock(return_value=httpx.Response(404))
    router.get("/entities").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "e1"}], "total": 1})
    )
    router.get("/entities/e1").mock(
        return_value=httpx.Response(200, json=_entity("e1", "Cordless Drill", asset="000-1"))
    )
    import homebox_mcp as hb
    entity, err = hb._resolve_exact("Cordless Drill")
    assert err is None
    assert entity["id"] == "e1"


def test_resolve_exact_alias_field_match_via_full_scan(router, good_status):
    router.get("/assets/drill-01").mock(return_value=httpx.Response(404))

    def entities(request):
        q = request.url.params.get("q")
        if q == "drill-01":
            # keyword search matches nothing (q does not index custom fields)
            return httpx.Response(200, json={"items": [], "total": 0})
        # full scan returns everything
        return httpx.Response(200, json={"items": [{"id": "e1"}, {"id": "e2"}], "total": 2})

    router.get("/entities").mock(side_effect=entities)
    router.get("/entities/e1").mock(
        return_value=httpx.Response(200, json=_entity("e1", "Other", item_id="widget-99"))
    )
    router.get("/entities/e2").mock(
        return_value=httpx.Response(200, json=_entity("e2", "Drill", item_id="drill-01", asset="000-2"))
    )
    import homebox_mcp as hb
    entity, err = hb._resolve_exact("drill-01")
    assert err is None
    assert entity["id"] == "e2"


def test_resolve_exact_no_match_returns_error(router, good_status):
    router.get("/assets/ghost").mock(return_value=httpx.Response(404))

    def entities(request):
        if request.url.params.get("q") == "ghost":
            return httpx.Response(200, json={"items": [], "total": 0})
        return httpx.Response(200, json={"items": [{"id": "e1"}], "total": 1})

    router.get("/entities").mock(side_effect=entities)
    router.get("/entities/e1").mock(
        return_value=httpx.Response(200, json=_entity("e1", "Not It", item_id="nope"))
    )
    import homebox_mcp as hb
    entity, err = hb._resolve_exact("ghost")
    assert entity is None
    assert err is not None
    assert "exactly matched" in err


def test_resolve_exact_two_matches_is_ambiguous(router, good_status):
    router.get("/assets/Drill").mock(return_value=httpx.Response(404))
    router.get("/entities").mock(
        return_value=httpx.Response(
            200, json={"items": [{"id": "e1"}, {"id": "e2"}], "total": 2}
        )
    )
    router.get("/entities/e1").mock(
        return_value=httpx.Response(200, json=_entity("e1", "Drill", asset="000-1"))
    )
    router.get("/entities/e2").mock(
        return_value=httpx.Response(200, json=_entity("e2", "Drill", asset="000-2"))
    )
    import homebox_mcp as hb
    entity, err = hb._resolve_exact("Drill")
    assert entity is None
    assert "ambiguous" in err
    assert "000-1" in err and "000-2" in err


def _numbered(router, item_id=None):
    """Asset 000-012 is a location; an item is named (or aliased) '12'.
    /assets parses refs the way Homebox does (strip '-', read an integer),
    so a non-strict '12' would reach the location if it were sent there."""
    def asset(request):
        ref = request.url.path.rsplit("/", 1)[-1].replace("-", "")
        hit = ref.isdigit() and int(ref) == 12
        return httpx.Response(200, json={"items": [{"id": "loc12"}] if hit else []})

    assets = router.get(url__regex=r"/assets/.*").mock(side_effect=asset)
    router.get("/entities/loc12").mock(return_value=httpx.Response(
        200, json=_entity("loc12", "West wall", asset="000-012")))
    name = "Bin" if item_id else "12"
    router.get("/entities").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "e1"}], "total": 1}))
    router.get("/entities/e1").mock(return_value=httpx.Response(
        200, json=_entity("e1", name, item_id=item_id, asset="000-040")))
    return assets


def test_resolve_exact_numeric_name_is_not_an_asset_id(router, good_status):
    assets = _numbered(router)
    import homebox_mcp as hb
    entity, err = hb._resolve_exact("12")
    assert err is None
    assert entity["id"] == "e1"
    assert assets.call_count == 0


def test_resolve_exact_numeric_alias_is_not_an_asset_id(router, good_status):
    _numbered(router, item_id="1-2")
    import homebox_mcp as hb
    entity, err = hb._resolve_exact("1-2")
    assert err is None
    assert entity["id"] == "e1"


def test_resolve_fuzzy_numeric_name_is_not_an_asset_id(router, good_status):
    _numbered(router)
    import homebox_mcp as hb
    assert hb._resolve_fuzzy("12")["id"] == "e1"


def test_resolve_exact_strict_asset_id_still_resolves(router, good_status):
    assets = _numbered(router)
    import homebox_mcp as hb
    entity, err = hb._resolve_exact(" 000-012 ")
    assert err is None
    assert entity["id"] == "loc12"
    assert assets.call_count == 1
