"""get_item must return the full detail — including `description`.

Regression: get_item built its response from a field whitelist that omitted
`description` (and `insured`, `archived`, `warrantyDetails`), so data written
via set_item was unreadable through any tool except export_csv.
"""
from __future__ import annotations

import httpx


def _full_entity(**overrides):
    entity = {
        "id": "e1",
        "assetId": "000-025",
        "name": "Netzwerkkabel 4 m (Cat 5, schwarz)",
        "description": "Patchkabel RJ45 für Netzwerk/LAN, Cat 5, schwarz.",
        "quantity": 2,
        "insured": True,
        "archived": False,
        "purchasePrice": 5,
        "warrantyDetails": "3 year limited",
        "notes": "",
        "tags": [],
        "fields": [],
        "attachments": [],
    }
    entity.update(overrides)
    return entity


def _mock_item(router, entity):
    router.get("/assets/000-025").mock(return_value=httpx.Response(
        200, json={"items": [{"id": "e1"}]}))
    router.get("/entities/e1").mock(return_value=httpx.Response(
        200, json=entity))
    router.get("/entities/e1/path").mock(return_value=httpx.Response(
        200, json=[{"name": "Bühne"}, {"name": "Netzwerkkabel"},
                   {"name": entity["name"]}]))


def test_get_item_includes_description(router, good_status):
    _mock_item(router, _full_entity())

    import homebox_mcp as hb
    got = hb.get_item("000-025")

    assert got["description"] == "Patchkabel RJ45 für Netzwerk/LAN, Cat 5, schwarz."
    # same omission class: settable via MCP but previously unreadable here
    assert got["insured"] is True
    assert got["archived"] is False
    assert got["warrantyDetails"] == "3 year limited"


def test_get_item_empty_description_is_omitted(router, good_status):
    _mock_item(router, _full_entity(description=""))

    import homebox_mcp as hb
    got = hb.get_item("000-025")

    assert "description" not in got  # _compact drops empty values
    assert got["name"] == "Netzwerkkabel 4 m (Cat 5, schwarz)"
