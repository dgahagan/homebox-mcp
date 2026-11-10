"""Item write tools against the fake server, whose PUT replaces the entity.

Each test seeds an item that has a value in every preserved group — scalars,
warranty, sold, parent, tags, typed custom fields — changes one thing, and
asserts the rest survived. That is the 0.26 PUT-clears gotcha, tested at the
tool level instead of only in _preserve_item_body.
"""
from __future__ import annotations

import pytest

import homebox_mcp as hb


@pytest.fixture
def drill(box):
    garage = box.add_location("Garage")
    return box.add_item(
        "Drill", parent=garage, assetId="000-001", tags=["power-tool"],
        description="18V cordless", quantity=2, insured=True,
        manufacturer="Makita", modelNumber="XFD10", serialNumber="SN1",
        notes="two batteries", purchasePrice=99.5, purchaseFrom="Acme",
        purchaseDate="2024-01-02T00:00:00Z",
        warrantyExpires="2027-01-02T00:00:00Z", warrantyDetails="3 year",
        soldTo="Nobody",
        fields=[
            {"name": "item_id", "type": "text", "textValue": "drill-makita"},
            {"name": "volts", "type": "number", "numberValue": 18},
            {"name": "cordless", "type": "boolean", "booleanValue": True},
        ],
    )


UNTOUCHED = {
    "name": "Drill", "description": "18V cordless", "quantity": 2,
    "insured": True, "manufacturer": "Makita", "modelNumber": "XFD10",
    "serialNumber": "SN1", "notes": "two batteries", "purchasePrice": 99.5,
    "purchaseFrom": "Acme", "purchaseDate": "2024-01-02T00:00:00Z",
    "warrantyExpires": "2027-01-02T00:00:00Z", "warrantyDetails": "3 year",
    "soldTo": "Nobody", "assetId": "000-001",
}


def assert_preserved(entity, *, except_keys=()):
    for k, v in UNTOUCHED.items():
        if k not in except_keys:
            assert entity[k] == v, k
    assert entity["parent"]["name"] == "Garage"
    assert [t["name"] for t in entity["tags"]] == ["power-tool"]
    by_name = {f["name"]: f for f in entity["fields"]}
    assert by_name["item_id"]["textValue"] == "drill-makita"
    assert by_name["volts"]["numberValue"] == 18
    assert by_name["cordless"]["booleanValue"] is True


# -- set_warranty ---------------------------------------------------------
def test_set_warranty_changes_only_warranty(box, drill):
    out = hb.set_warranty("000-001", expires="2030-05-06", lifetime=True,
                          details="extended")
    assert out["ok"] is True
    assert drill["warrantyExpires"] == "2030-05-06T00:00:00Z"
    assert drill["lifetimeWarranty"] is True
    assert drill["warrantyDetails"] == "extended"
    assert_preserved(drill, except_keys=("warrantyExpires", "warrantyDetails"))


def test_set_warranty_resolves_by_alias_field(box, drill):
    assert hb.set_warranty("drill-makita", details="x")["name"] == "Drill"
    assert drill["warrantyDetails"] == "x"


def test_set_warranty_refuses_fuzzy_identifier(box, drill):
    out = hb.set_warranty("Dril", details="x")
    assert "no item exactly matched" in out["error"]
    assert not box.called("PUT", r"/entities/.*")


# -- set_identity ---------------------------------------------------------
def test_set_identity_changes_only_identity(box, drill):
    out = hb.set_identity("Drill", serial_number="SN2")
    assert out["serialNumber"] == "SN2"
    assert out["manufacturer"] == "Makita"
    assert_preserved(drill, except_keys=("serialNumber",))


# -- set_fields -----------------------------------------------------------
def test_set_fields_upserts_and_types_values(box, drill):
    out = hb.set_fields("Drill", {"volts": 20, "chuck": "1/2 in", "kit": False,
                                  "torque": 54.0, "skipped": None})
    assert out["fields"] == {"item_id": "drill-makita", "volts": 20,
                             "cordless": True, "chuck": "1/2 in", "kit": False,
                             "torque": 54}
    sent = {f["name"]: f for f in box.body("PUT", r"/entities/.*")["fields"]}
    assert sent["chuck"] == {"name": "chuck", "type": "text", "textValue": "1/2 in"}
    assert sent["kit"] == {"name": "kit", "type": "boolean", "booleanValue": False}
    # Homebox 500s on a float number value, so it must go out as an int
    assert isinstance(sent["torque"]["numberValue"], int)
    assert "skipped" not in sent
    assert drill["warrantyDetails"] == "3 year"


# -- set_item -------------------------------------------------------------
def test_set_item_quantity_only_uses_patch(box, drill):
    out = hb.set_item("Drill", quantity=5)
    assert out["quantity"] == 5
    assert box.body("PATCH", r"/entities/.*") == {"id": drill["id"], "quantity": 5}
    assert not box.called("PUT", r"/entities/.*")
    assert_preserved(drill, except_keys=("quantity",))


def test_set_item_edits_and_preserves_the_rest(box, drill):
    out = hb.set_item("Drill", new_name="Drill/Driver", description="brushless",
                      purchase_date="2025-03-04", archived=True,
                      fields={"volts": 20})
    assert out["name"] == "Drill/Driver"
    assert out["archived"] is True
    assert out["fields"]["volts"] == 20
    assert drill["purchaseDate"] == "2025-03-04T00:00:00Z"
    assert drill["warrantyDetails"] == "3 year"
    assert drill["soldTo"] == "Nobody"
    assert drill["parent"]["name"] == "Garage"
    assert [t["name"] for t in drill["tags"]] == ["power-tool"]


def test_set_item_can_set_false_and_zero(box, drill):
    hb.set_item("Drill", insured=False, purchase_price=0)
    assert drill["insured"] is False
    assert drill["purchasePrice"] == 0
    assert_preserved(drill, except_keys=("insured", "purchasePrice"))


def test_set_item_ambiguous_name_refused(box, drill):
    box.add_item("Drill", assetId="000-002")
    out = hb.set_item("Drill", notes="x")
    assert "ambiguous — 2 items match" in out["error"]
    assert not box.called("PUT", r"/entities/.*")


# -- set_tags -------------------------------------------------------------
def test_set_tags_add_merges_and_creates(box, drill):
    box.add_tag("cordless")
    out = hb.set_tags("Drill", ["Cordless", "brand-new"])
    assert out["tags"] == ["brand-new", "cordless", "power-tool"]
    assert not box.called("PUT", r"/entities/.*")  # tags go via PATCH
    assert_preserved_except_tags(drill)


def assert_preserved_except_tags(entity):
    for k, v in UNTOUCHED.items():
        assert entity[k] == v, k
    assert len(entity["fields"]) == 3


def test_set_tags_remove(box, drill):
    hb.set_tags("Drill", ["cordless"])
    assert hb.set_tags("Drill", ["POWER-TOOL"], mode="remove")["tags"] == ["cordless"]


def test_set_tags_replace(box, drill):
    assert hb.set_tags("Drill", ["loaned"], mode="replace")["tags"] == ["loaned"]


def test_set_tags_bad_mode_refused(box, drill):
    assert "mode must be" in hb.set_tags("Drill", ["x"], mode="toggle")["error"]
    assert not box.called("PATCH", r"/entities/.*")


# -- get_item -------------------------------------------------------------
def test_get_item_returns_sold_fields(box, drill):
    hb.mark_sold("Drill", sold_price=40, sold_to="Sam", sold_date="2026-02-03",
                 sold_notes="cash")
    got = hb.get_item("Drill")
    assert got["soldPrice"] == 40
    assert got["soldTo"] == "Sam"
    assert got["soldDate"] == "2026-02-03T00:00:00Z"
    assert got["soldNotes"] == "cash"
    assert got["location_path"] == "Garage"


def test_get_item_omits_sold_fields_when_unsold(box):
    box.add_item("Saw")
    got = hb.get_item("Saw")
    assert not {"soldDate", "soldTo", "soldPrice", "soldNotes"} & set(got)
