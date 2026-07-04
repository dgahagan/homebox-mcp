"""Reporting + lifecycle tools: inventory_stats, export_csv,
list_custom_fields, mark_sold, duplicate_item.

Every request is served by the respx router (assert_all_mocked=True); nothing
touches a live Homebox. Write-path tools resolve an item via the standard mock
trio (GET /assets/{ident} 404, GET /entities?q= one hit, GET /entities/{id}).
"""
from __future__ import annotations

import json

import httpx
import pytest

import homebox_mcp as hb


def _mock_item(router, name="Drill", eid="e1", asset="000-1", **extra):
    detail = {
        "id": eid, "name": name, "assetId": asset,
        "entityType": {"isLocation": False}, "fields": [],
    }
    detail.update(extra)
    router.get(f"/assets/{name}").mock(return_value=httpx.Response(404))
    router.get("/entities").mock(
        return_value=httpx.Response(200, json={"items": [{"id": eid}], "total": 1})
    )
    router.get(f"/entities/{eid}").mock(
        return_value=httpx.Response(200, json=detail)
    )


# --- inventory_stats -------------------------------------------------------
def test_inventory_stats_totals(router, good_status):
    route = router.get("/groups/statistics").mock(
        return_value=httpx.Response(200, json={"totalItemPrice": 100})
    )
    result = hb.inventory_stats(by="totals")
    assert route.called
    assert result["totalItemPrice"] == 100


def test_inventory_stats_locations(router, good_status):
    route = router.get("/groups/statistics/locations").mock(
        return_value=httpx.Response(200, json=[{"name": "Garage", "total": 50}])
    )
    result = hb.inventory_stats(by="locations")
    assert route.called
    assert result[0]["name"] == "Garage"


def test_inventory_stats_purchase_price_passes_dates(router, good_status):
    route = router.get("/groups/statistics/purchase-price").mock(
        return_value=httpx.Response(200, json={"entries": []})
    )
    hb.inventory_stats(by="purchase-price", start="2026-01-01", end="2026-06-30")
    params = route.calls.last.request.url.params
    assert params["start"] == "2026-01-01"
    assert params["end"] == "2026-06-30"


def test_inventory_stats_bogus_raises(router, good_status):
    with pytest.raises(hb.ToolError):
        hb.inventory_stats(by="bogus")


# --- export_csv ------------------------------------------------------------
def test_export_csv_writes_file_and_counts_rows(router, good_status, tmp_path):
    csv = "name,quantity\nDrill,1\nSaw,2\n"
    router.get("/entities/export").mock(return_value=httpx.Response(200, text=csv))
    dest = tmp_path / "out.csv"
    result = hb.export_csv(save_to=str(dest))
    assert result["ok"] is True
    assert result["rows"] == 2  # 2 data rows, header excluded
    assert result["path"] == str(dest)
    assert dest.read_text() == csv


# --- list_custom_fields ----------------------------------------------------
def test_list_custom_fields_names(router, good_status):
    route = router.get("/entities/fields").mock(
        return_value=httpx.Response(200, json=["item_id", "color"])
    )
    result = hb.list_custom_fields()
    assert route.called
    assert "item_id" in result


def test_list_custom_fields_values(router, good_status):
    route = router.get("/entities/fields/values").mock(
        return_value=httpx.Response(200, json=["red", "blue"])
    )
    result = hb.list_custom_fields(field="color")
    assert route.calls.last.request.url.params["field"] == "color"
    assert result == ["red", "blue"]


# --- mark_sold -------------------------------------------------------------
def test_mark_sold_sends_sold_fields_and_preserves(router, good_status):
    _mock_item(router, purchasePrice="25.0")
    put = router.put("/entities/e1").mock(return_value=httpx.Response(200, json={}))
    result = hb.mark_sold(
        "Drill", sold_price=50.0, sold_to="Bob",
        sold_date="2026-07-01", sold_notes="cash",
    )
    assert result["ok"] is True
    body = json.loads(put.calls.last.request.content)
    assert body["soldPrice"] == 50.0
    assert body["soldTo"] == "Bob"
    assert body["soldDate"] == "2026-07-01T00:00:00Z"  # normalized
    assert body["soldNotes"] == "cash"
    assert body["name"] == "Drill"           # preserved from fetched entity
    assert body["purchasePrice"] == "25.0"   # preserved from fetched entity


def test_mark_sold_clear_omits_sold_keys(router, good_status):
    _mock_item(
        router, purchasePrice="25.0", soldPrice="50.0", soldTo="Bob",
        soldDate="2026-07-01T00:00:00Z", soldNotes="cash",
    )
    put = router.put("/entities/e1").mock(return_value=httpx.Response(200, json={}))
    hb.mark_sold("Drill", clear=True)
    body = json.loads(put.calls.last.request.content)
    for k in ("soldPrice", "soldTo", "soldDate", "soldNotes"):
        assert k not in body  # cleared despite being on the fetched entity
    assert body["name"] == "Drill"  # non-sold fields still preserved


# --- duplicate_item --------------------------------------------------------
def test_duplicate_item_defaults_and_flow(router, good_status):
    _mock_item(router, name="Drill", eid="e1")
    dup = router.post("/entities/e1/duplicate").mock(
        return_value=httpx.Response(200, json={"id": "e2"})
    )
    ensure = router.post("/actions/ensure-asset-ids").mock(
        return_value=httpx.Response(200, json={})
    )
    router.get("/entities/e2").mock(
        return_value=httpx.Response(200, json={
            "id": "e2", "name": "Copy of Drill", "assetId": "000-2",
        })
    )
    result = hb.duplicate_item("Drill")
    body = json.loads(dup.calls.last.request.content)
    assert body["copyAttachments"] is False
    assert body["copyCustomFields"] is True
    assert body["copyMaintenance"] is False
    assert body["copyPrefix"] == "Copy of "
    assert ensure.called
    assert result["id"] == "e2"
    assert result["assetId"] == "000-2"
