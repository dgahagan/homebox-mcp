"""Maintenance-log tools: log/list/set/delete_maintenance.

All HTTP goes through the respx router (assert_all_mocked=True) so nothing
escapes to a live Homebox. The write path resolves an item via the same mock
trio as the rest of the suite: GET /assets/{ident} 404, GET /entities?q= with
one exact-name hit, GET /entities/{id} for detail.
"""
from __future__ import annotations

import json

import httpx
import pytest

import homebox_mcp as hb


def _mock_item(router, name="Mower", eid="e1", asset="000-9"):
    """Wire up _resolve_exact / _resolve_fuzzy to find one item named `name`."""
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


# --- log_maintenance -------------------------------------------------------
def test_log_maintenance_sends_cost_string_and_rfc3339(router, good_status):
    _mock_item(router)
    route = router.post("/entities/e1/maintenance").mock(
        return_value=httpx.Response(200, json={"id": "m1", "name": "Oil change"})
    )
    result = hb.log_maintenance(
        "Mower", name="Oil change", completed_date="2026-07-01", cost=12.5
    )
    assert result["ok"] is True
    body = json.loads(route.calls.last.request.content)
    assert body["cost"] == "12.5"  # cost float serialized as a STRING
    assert body["completedDate"] == "2026-07-01T00:00:00Z"  # RFC3339-normalized


def test_log_maintenance_unresolved_item_returns_error(router, good_status):
    router.get("/assets/Ghost").mock(return_value=httpx.Response(404))
    router.get("/entities").mock(
        return_value=httpx.Response(200, json={"items": [], "total": 0})
    )
    result = hb.log_maintenance("Ghost", name="Oil change")
    assert "no item exactly matched" in result["error"]


# --- list_maintenance ------------------------------------------------------
def test_list_maintenance_global_truncates_dates(router, good_status):
    entries = [{
        "id": "m1", "name": "Oil change",
        "completedDate": "2026-07-01T00:00:00Z", "scheduledDate": "",
        "cost": "12.5", "itemName": "Mower",
    }]
    route = router.get("/maintenance").mock(
        return_value=httpx.Response(200, json=entries)
    )
    result = hb.list_maintenance(status="scheduled")
    assert route.calls.last.request.url.params["status"] == "scheduled"
    assert result[0]["completedDate"] == "2026-07-01"  # truncated YYYY-MM-DD
    assert result[0]["item"] == "Mower"


def test_list_maintenance_invalid_status_raises(router, good_status):
    with pytest.raises(hb.ToolError):
        hb.list_maintenance(status="bogus")


def test_list_maintenance_per_item_hits_entity_route(router, good_status):
    _mock_item(router)
    route = router.get("/entities/e1/maintenance").mock(
        return_value=httpx.Response(200, json=[])
    )
    hb.list_maintenance(identifier="Mower", status="completed")
    assert route.called
    assert route.calls.last.request.url.params["status"] == "completed"


# --- set_maintenance -------------------------------------------------------
def test_set_maintenance_merges_and_normalizes(router, good_status):
    # No GET /maintenance/{id} exists — the tool finds the entry in the list.
    entries = [{
        "id": "m1", "name": "Oil change", "description": "",
        "completedDate": "", "scheduledDate": "2026-06-01T00:00:00Z",
        "cost": "0",
    }]
    router.get("/maintenance").mock(return_value=httpx.Response(200, json=entries))
    put = router.put("/maintenance/m1").mock(
        return_value=httpx.Response(200, json={
            "id": "m1", "name": "Oil change",
            "completedDate": "2026-07-02T00:00:00Z",
        })
    )
    result = hb.set_maintenance("m1", completed_date="2026-07-02")
    assert result["ok"] is True
    body = json.loads(put.calls.last.request.content)
    assert body["name"] == "Oil change"  # unchanged field re-sent
    assert body["completedDate"] == "2026-07-02T00:00:00Z"  # normalized


def test_set_maintenance_unknown_entry_returns_error(router, good_status):
    router.get("/maintenance").mock(return_value=httpx.Response(200, json=[]))
    put = router.put("/maintenance/nope").mock(return_value=httpx.Response(200, json={}))
    result = hb.set_maintenance("nope", name="x")
    assert "no maintenance entry" in result["error"]
    assert not put.called


# --- delete_maintenance ----------------------------------------------------
def test_delete_maintenance_wrong_confirm_blocks_delete(router, good_status):
    router.get("/maintenance").mock(
        return_value=httpx.Response(200, json=[{"id": "m1", "name": "Oil change"}])
    )
    delete = router.delete("/maintenance/m1").mock(return_value=httpx.Response(204))
    result = hb.delete_maintenance("m1", confirm="wrong")
    assert "confirm must equal" in result["error"]
    assert not delete.called


def test_delete_maintenance_correct_confirm_issues_delete(router, good_status):
    router.get("/maintenance").mock(
        return_value=httpx.Response(200, json=[{"id": "m1", "name": "Oil change"}])
    )
    delete = router.delete("/maintenance/m1").mock(return_value=httpx.Response(204))
    result = hb.delete_maintenance("m1", confirm="Oil change")
    assert result["ok"] is True
    assert delete.called
