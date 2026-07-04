"""Pure-function tests (no HTTP): the type-mapping and formatting helpers."""
from __future__ import annotations

import homebox_mcp as hb


# ---------------------------------------------------------------------------
# _preserve_item_body — the most regression-prone logic (0.26 PUT-clears gotcha)
# ---------------------------------------------------------------------------
def test_preserve_item_body_full_round_trip():
    full = {
        "id": "e1",
        "name": "Cordless Drill",
        "description": "20V brushless",
        "quantity": 2,
        "insured": True,
        "archived": False,
        "manufacturer": "DeWalt",
        "modelNumber": "DCD777",
        "serialNumber": "SN-123",
        "notes": "spare battery in drawer",
        "purchasePrice": 129.0,
        "purchaseFrom": "Home Depot",
        "purchaseDate": "2020-01-02T00:00:00Z",
        "warrantyExpires": "2023-01-02T00:00:00Z",
        "warrantyDetails": "3 year limited",
        "lifetimeWarranty": False,
        "assetId": "000-028",
        "entityType": {"id": "et-item", "name": "Item"},
        "parent": {"id": "loc-shelf", "name": "Shelf 1"},
        "tags": [{"id": "t1", "name": "power-tool"}, {"id": "t2", "name": "battery"}],
        "fields": [
            {"name": "item_id", "type": "text", "textValue": "drill-01"},
            {"name": "watts", "type": "number", "numberValue": 17000},
            {"name": "loaned", "type": "boolean", "booleanValue": True},
        ],
    }

    body = hb._preserve_item_body(full)

    # scalars preserved
    assert body["id"] == "e1"
    assert body["name"] == "Cordless Drill"
    assert body["quantity"] == 2
    assert body["insured"] is True
    assert body["archived"] is False
    assert body["manufacturer"] == "DeWalt"
    assert body["purchasePrice"] == 129.0
    assert body["warrantyDetails"] == "3 year limited"
    assert body["lifetimeWarranty"] is False
    # ids flattened
    assert body["assetId"] == "000-028"
    assert body["entityTypeId"] == "et-item"
    assert body["parentId"] == "loc-shelf"
    # tags -> tagIds
    assert body["tagIds"] == ["t1", "t2"]
    # custom fields keep the correct type-keyed value
    assert body["fields"] == [
        {"name": "item_id", "type": "text", "textValue": "drill-01"},
        {"name": "watts", "type": "number", "numberValue": 17000},
        {"name": "loaned", "type": "boolean", "booleanValue": True},
    ]


def test_preserve_item_body_omits_absent_optionals():
    body = hb._preserve_item_body({"id": "e9", "name": "Bare"})
    assert body == {"id": "e9", "name": "Bare"}
    assert "tagIds" not in body
    assert "fields" not in body
    assert "assetId" not in body


# ---------------------------------------------------------------------------
# _field_type / _make_field / _field_value
# ---------------------------------------------------------------------------
def test_field_type_bool_before_int():
    # bool is an int subclass — it must resolve to "boolean", not "number".
    assert hb._field_type(True) == "boolean"
    assert hb._field_type(False) == "boolean"


def test_field_type_numbers_and_text():
    assert hb._field_type(5) == "number"
    assert hb._field_type(5.5) == "number"
    assert hb._field_type("hello") == "text"
    assert hb._field_type(None) == "text"


def test_make_field_number_coerces_float_to_int():
    # Homebox's number field is integer-typed; a float 500s the API.
    assert hb._make_field("watts", 17000.5, "number") == {
        "name": "watts", "type": "number", "numberValue": 17000,
    }
    assert hb._make_field("qty", 7, "number")["numberValue"] == 7


def test_make_field_boolean_and_text():
    assert hb._make_field("loaned", True, "boolean") == {
        "name": "loaned", "type": "boolean", "booleanValue": True,
    }
    assert hb._make_field("note", "hi") == {
        "name": "note", "type": "text", "textValue": "hi",
    }


def test_field_value_reads_by_declared_type():
    assert hb._field_value({"type": "number", "numberValue": 3}) == 3
    assert hb._field_value({"type": "boolean", "booleanValue": False}) is False
    assert hb._field_value({"type": "text", "textValue": "x"}) == "x"
    # default type is text
    assert hb._field_value({"textValue": "y"}) == "y"


# ---------------------------------------------------------------------------
# _rfc3339 / _attachment_title
# ---------------------------------------------------------------------------
def test_rfc3339_date_only_gets_midnight_utc():
    assert hb._rfc3339("2026-01-02") == "2026-01-02T00:00:00Z"


def test_rfc3339_passes_through_full_timestamp():
    assert hb._rfc3339("2026-01-02T09:30:00Z") == "2026-01-02T09:30:00Z"


def test_attachment_title_replaces_slashes():
    # Homebox truncates on '/', so 'front 3/4 view' -> 'front 3-4 view'.
    assert hb._attachment_title("front 3/4 view") == "front 3-4 view"
    assert hb._attachment_title("no slashes") == "no slashes"
