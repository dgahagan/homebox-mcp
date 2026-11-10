"""Archived items: Homebox hides them from a plain listing, so each place
that must still see them asks for them explicitly."""
from __future__ import annotations

import pytest

import homebox_mcp as hb


def alias(value):
    return [{"name": "item_id", "type": "text", "textValue": value}]


# -- resolution -----------------------------------------------------------
def test_write_reaches_an_archived_item_by_name(box):
    old = box.add_item("Old drill", archived=True, notes="keep")
    out = hb.set_item("Old drill", archived=False)
    assert out["archived"] is False
    assert old["archived"] is False
    assert old["notes"] == "keep"


def test_write_reaches_an_archived_item_by_alias(box):
    old = box.add_item("Old drill", archived=True, fields=alias("drill-old"))
    box.add_item("Saw")
    assert hb.set_identity("drill-old", serial_number="SN9")["name"] == "Old drill"
    assert old["serialNumber"] == "SN9"


def test_read_reaches_an_archived_item_by_name(box):
    box.add_item("Old drill", archived=True)
    got = hb.get_item("old drill")
    assert got["name"] == "Old drill"
    assert got["archived"] is True


def test_active_item_wins_over_archived_with_the_same_name(box):
    box.add_item("Drill", archived=True, assetId="000-001")
    new = box.add_item("Drill", assetId="000-002")
    out = hb.set_item("Drill", notes="current")  # not "ambiguous"
    assert out["assetId"] == "000-002"
    assert new["notes"] == "current"


def test_active_alias_wins_over_archived_name(box):
    box.add_item("drill-1", archived=True)
    active = box.add_item("Drill", fields=alias("drill-1"))
    hb.set_item("drill-1", notes="x")
    assert active["notes"] == "x"


def test_two_archived_matches_are_ambiguous(box):
    box.add_item("Drill", archived=True, assetId="000-001")
    box.add_item("Drill", archived=True, assetId="000-002")
    out = hb.set_item("Drill", notes="x")
    assert "ambiguous — 2 items match" in out["error"]
    assert not box.called("PUT", r"/entities/.*")


def test_archived_pass_fetches_detail_for_archived_rows_only(box):
    for i in range(5):
        box.add_item(f"Active {i}")
    box.add_item("Old drill", archived=True, fields=alias("drill-old"))
    hb.set_identity("drill-old", serial_number="SN9")
    detail_gets = [c for c in box.called("GET", r"/entities/e\d+")]
    # 5 active rows once (the active full scan) + the archived row twice
    # (resolve, then the tool's read-back). Not 5 more for the archived pass.
    assert len(detail_gets) == 7


def test_fuzzy_read_does_not_fall_back_to_an_archived_item(box):
    box.add_item("Old drill press", archived=True)
    assert "no item found" in hb.get_item("drill")["error"]


def test_miss_is_still_a_miss(box):
    box.add_item("Saw")
    assert "no item exactly matched" in hb.set_item("Lathe", notes="x")["error"]


# -- delete_location ------------------------------------------------------
def test_delete_location_counts_archived_items(box):
    tote = box.add_location("Tote")
    box.add_item("Old drill", parent=tote, archived=True)
    out = hb.delete_location("Tote", confirm="Tote")
    assert "contains 1 item(s)" in out["error"]
    assert tote["id"] in box.entities
    assert hb.delete_location("Tote", confirm="Tote", confirm_nonempty=True)["ok"]


def test_delete_empty_location(box):
    tote = box.add_location("Tote")
    assert hb.delete_location("Tote", confirm="Tote")["ok"] is True
    assert tote["id"] not in box.entities


# -- field_index ----------------------------------------------------------
def test_field_index_includes_archived_items(box):
    box.add_item("Drill", assetId="000-001", fields=alias("drill-1"))
    box.add_item("Old drill", assetId="000-002", archived=True,
                 fields=alias("drill-old"))
    assert hb.field_index() == {"drill-1": "000-001", "drill-old": "000-002"}


@pytest.mark.parametrize("tool", ["search_items", "warranties_expiring"])
def test_listings_still_hide_archived_items(box, tool):
    box.add_item("Old drill", archived=True,
                 warrantyExpires="2999-01-01T00:00:00Z")
    rows = (hb.search_items("drill") if tool == "search_items"
            else hb.warranties_expiring(before="3000-01-01"))
    assert rows == []
