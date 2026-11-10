"""Read tools: search filters, location listings, warranties, field index."""
from __future__ import annotations

import datetime

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import homebox_mcp as hb


@pytest.fixture
def house(box):
    garage = box.add_location("Garage")
    shelf = box.add_location("Shelf 1", parent=garage)
    office = box.add_location("Office")
    box.add_tag("power-tool")
    box.add_item("Drill", parent=garage, tags=["power-tool"], assetId="000-001",
                 fields=[{"name": "item_id", "type": "text", "textValue": "drill-1"}])
    box.add_item("Drill bits", parent=shelf, assetId="000-002")
    box.add_item("Desk drill press", parent=office, tags=["power-tool"])
    box.add_item("Old drill", parent=garage, archived=True)
    return box


def names(rows):
    return sorted(r["name"] for r in rows)


# -- search_items ---------------------------------------------------------
def test_search_by_query_hides_archived(house):
    assert names(hb.search_items("drill")) == ["Desk drill press", "Drill",
                                               "Drill bits"]


def test_search_include_archived_flags_them(house):
    rows = hb.search_items("drill", include_archived=True)
    assert "Old drill" in names(rows)
    flagged = {r["name"] for r in rows if r.get("archived")}
    assert flagged == {"Old drill"}


def test_search_by_location_includes_sub_locations(house):
    assert names(hb.search_items(location="Garage")) == ["Drill", "Drill bits"]
    assert names(hb.search_items(location="Garage/Shelf 1")) == ["Drill bits"]


def test_search_location_and_tag_and_query_compose(house):
    assert names(hb.search_items(tags=["power-tool"], location="Garage")) == ["Drill"]
    assert names(hb.search_items("bits", location="Garage")) == ["Drill bits"]
    assert hb.search_items("bits", location="Office") == []


def test_search_location_chunks_parent_ids(house, monkeypatch):
    monkeypatch.setattr(hb, "_PARENT_ID_CHUNK", 1)
    assert names(hb.search_items(location="Garage")) == ["Drill", "Drill bits"]
    assert len(house.called("GET", r"/entities")) == 2  # Garage, then Shelf 1


def test_search_unknown_location_or_tag_is_an_error(house):
    with pytest.raises(ToolError, match="no location named 'Attic'"):
        hb.search_items(location="Attic")
    with pytest.raises(ToolError, match="unknown tag"):
        hb.search_items(tags=["nope"])
    with pytest.raises(ToolError, match="query, tags and/or location"):
        hb.search_items()


def test_search_summary_carries_alias_and_asset_id(house):
    (row,) = hb.search_items("drill", tags=["power-tool"], location="Garage")
    assert row["assetId"] == "000-001"
    assert row["item_id"] == "drill-1"
    assert row["location"] == "Garage"
    assert "archived" not in row


def test_search_limit(house):
    assert len(hb.search_items("drill", limit=2)) == 2


# -- list_locations / location_contents -----------------------------------
def test_list_locations_outline(house):
    assert hb.list_locations() == "- Garage\n  - Shelf 1\n- Office"


def test_location_contents_direct(house):
    out = hb.location_contents("Garage")
    assert names(out["items"]) == ["Drill"]
    assert out["sub_locations"] == ["Shelf 1"]


def test_location_contents_recursive_walks_items_in_items(house):
    drill = next(e for e in house.entities.values() if e["name"] == "Drill")
    house.add_item("Chuck key", parent=drill)
    out = hb.location_contents("Garage", recursive=True)
    by_name = {r["name"]: r["location"] for r in out["items"]}
    assert by_name == {"Drill": "Garage", "Chuck key": "Garage → Drill",
                       "Drill bits": "Garage → Shelf 1"}


def test_location_contents_recursive_truncates(house):
    out = hb.location_contents("Garage", recursive=True, max_items=1)
    assert out["truncated"] is True
    assert len(out["items"]) == 1


def test_location_contents_unknown(house):
    assert "no location named" in hb.location_contents("Attic")["error"]


# -- warranties_expiring --------------------------------------------------
def test_warranties_window_defaults_to_today(box):
    today = datetime.date.today()
    soon = (today + datetime.timedelta(days=30)).isoformat()
    box.add_item("Expired", warrantyExpires="2019-01-01T00:00:00Z")
    box.add_item("Soon", warrantyExpires=f"{soon}T00:00:00Z")
    box.add_item("Far", warrantyExpires="2999-01-01T00:00:00Z")
    box.add_item("None")
    before = (today + datetime.timedelta(days=365)).isoformat()
    assert names(hb.warranties_expiring(before=before)) == ["Soon"]
    rows = hb.warranties_expiring(before=before, after="2000-01-01")
    assert [r["name"] for r in rows] == ["Expired", "Soon"]  # sorted by date
    assert rows[0]["warrantyExpires"] == "2019-01-01"


def test_warranties_lifetime(box):
    box.add_item("Wrench", lifetimeWarranty=True)
    box.add_item("Drill", warrantyExpires="2999-01-01T00:00:00Z")
    assert names(hb.warranties_expiring(lifetime=True)) == ["Wrench"]


def test_warranties_needs_before_or_lifetime(box):
    with pytest.raises(ToolError, match="pass `before`"):
        hb.warranties_expiring()


# -- field_index ----------------------------------------------------------
def test_field_index_defaults_to_alias_field(house):
    assert hb.field_index() == {"drill-1": "000-001"}


def test_field_index_named_field_stringifies(box):
    box.add_item("A", assetId="000-001",
                 fields=[{"name": "volts", "type": "number", "numberValue": 18}])
    box.add_item("B", fields=[{"name": "volts", "type": "number", "numberValue": 20}])
    box.add_item("C", fields=[{"name": "volts", "type": "text", "textValue": ""}])
    assert hb.field_index("volts") == {"18": "000-001", "20": ""}


def test_field_index_needs_a_field_name(box, monkeypatch):
    monkeypatch.setattr(hb, "HOMEBOX_ALIAS_FIELD", "")
    with pytest.raises(ToolError, match="pass field_name"):
        hb.field_index()


# -- barcode_lookup -------------------------------------------------------
def test_barcode_lookup(box):
    out = hb.barcode_lookup("0123456789")
    assert out["ok"] is True
    assert out["result"][0]["barcode"] == "0123456789"
