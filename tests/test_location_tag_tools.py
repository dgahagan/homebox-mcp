"""create_location, set_location, set_tag, list_tags, import_csv, labels,
finalize_photos."""
from __future__ import annotations

from fake_homebox import LOCATION_TYPE, NIL_UUID

import homebox_mcp as hb


def loc(box, name):
    return next(e for e in box.entities.values() if e["name"] == name)


# -- create_location ------------------------------------------------------
def test_create_location_at_root_is_a_location(box):
    out = hb.create_location("Garage")
    assert out["ok"] is True
    assert box.entities[out["id"]]["entityType"] == LOCATION_TYPE
    assert not box.called("PUT", r"/entities/.*")  # no description, no PUT


def test_create_location_under_parent_with_description(box):
    garage = box.add_location("Garage")
    out = hb.create_location("Tote 1", parent="Garage", description="cables")
    made = box.entities[out["id"]]
    assert made["parent"]["id"] == garage["id"]
    assert made["description"] == "cables"
    assert made["entityType"] == LOCATION_TYPE  # the PUT must not demote it


def test_create_location_duplicate_is_per_parent(box):
    garage = box.add_location("Garage")
    box.add_location("Shelf 1", parent=garage)
    box.add_location("Office")
    assert "already exists under" in hb.create_location(
        "shelf 1", parent="Garage")["error"]
    assert hb.create_location("Shelf 1", parent="Office")["ok"] is True
    assert "already exists under '(root)'" in hb.create_location("GARAGE")["error"]


def test_create_location_unknown_parent(box):
    assert "no location named" in hb.create_location("X", parent="Attic")["error"]
    assert not box.called("POST", r"/entities")


# -- set_location ---------------------------------------------------------
def test_set_location_edits_and_preserves(box):
    garage = box.add_location("Garage")
    tote = box.add_location(
        "Tote 1", parent=garage, assetId="000-007", notes="blue lid",
        tags=["bin"], fields=[{"name": "row", "type": "number", "numberValue": 3}])
    out = hb.set_location("Tote 1", new_name="Tote A", description="cables",
                          tags=["Fragile"], fields={"lid": "blue"})
    assert out["name"] == "Tote A"
    assert out["tags"] == ["Fragile", "bin"]
    assert out["fields"] == {"row": 3, "lid": "blue"}
    assert out["parent"] == "Garage"
    assert tote["notes"] == "blue lid"
    assert tote["assetId"] == "000-007"
    assert tote["entityType"] == LOCATION_TYPE


def test_set_location_tags_remove_and_replace(box):
    box.add_location("Tote", tags=["bin", "fragile"])
    assert hb.set_location("Tote", tags=["BIN"], tags_mode="remove")["tags"] == [
        "fragile"]
    assert hb.set_location("Tote", tags=["x"], tags_mode="replace")["tags"] == ["x"]


def test_set_location_move_and_clear_parent(box):
    box.add_location("Garage")
    box.add_location("Office")
    tote = box.add_location("Tote", parent=loc(box, "Garage"))
    assert hb.set_location("Tote", parent="Office")["parent"] == "Office"
    assert hb.set_location("Tote", clear_parent=True)["parent"] is None
    assert tote["parent"] is None


def test_set_location_refuses_itself_as_parent(box):
    box.add_location("Tote")
    assert "its own parent" in hb.set_location("Tote", parent="Tote")["error"]
    assert not box.called("PUT", r"/entities/.*")


def test_set_location_unknown_entity_type(box):
    box.add_location("Tote")
    assert "no entity type named" in hb.set_location(
        "Tote", entity_type="Vehicle")["error"]
    assert hb.set_location("Tote", entity_type="item")["entityType"] == "Item"


def test_set_location_ambiguous_name_lists_paths(box):
    box.add_location("Shelf", parent=box.add_location("Garage"))
    box.add_location("Shelf", parent=box.add_location("Office"))
    err = hb.set_location("Shelf", notes="x")["error"]
    assert "Garage" in err and "Office" in err
    assert hb.set_location("Office/Shelf", notes="x")["ok"] is True


# -- tags -----------------------------------------------------------------
def test_set_tag_creates_then_edits(box):
    out = hb.set_tag("condition-good", description="works", color="#00ff00")
    assert out == {"ok": True, "name": "condition-good", "description": "works",
                   "color": "#00ff00", "icon": None}
    hb.set_tag("CONDITION-GOOD", icon="mdi-check")
    (tag,) = box.tags.values()
    # the second edit must not wipe what the first one set
    assert (tag["description"], tag["color"], tag["icon"]) == (
        "works", "#00ff00", "mdi-check")
    assert len(box.called("POST", r"/tags")) == 1


def test_set_tag_rename_parent_and_clear(box):
    parent = box.add_tag("condition")
    child = box.add_tag("good")
    assert hb.set_tag("good", new_name="condition-good", parent="Condition")[
        "name"] == "condition-good"
    assert child["parentId"] == parent["id"]
    hb.set_tag("condition-good", clear_parent=True)
    assert child["parentId"] == NIL_UUID


def test_set_tag_unknown_parent(box):
    box.add_tag("good")
    assert "no tag named 'nope'" in hb.set_tag("good", parent="nope")["error"]
    assert not box.called("PUT", r"/tags/.*")


def test_list_tags_names_and_detail(box):
    parent = box.add_tag("condition", color="#fff")
    box.add_tag("good", parentId=parent["id"], description="works")
    assert hb.list_tags() == ["condition", "good"]
    assert hb.list_tags(detail=True) == [
        {"name": "condition", "description": None, "color": "#fff",
         "icon": None, "parent": None},
        {"name": "good", "description": "works", "color": None,
         "icon": None, "parent": "condition"},
    ]


# -- import_csv -----------------------------------------------------------
def test_import_csv_posts_multipart_and_counts_rows(box):
    csv = "HB.name,HB.location\nDrill,Garage\n\nSaw,Garage/Loft\n"
    out = hb.import_csv(csv)
    assert out == {"ok": True, "status_code": 204, "rows_submitted": 2}
    (req,) = [r for r in box.requests if r.url.path.endswith("/entities/import")]
    assert req.headers["content-type"].startswith("multipart/form-data")
    assert b'name="csv"; filename="import.csv"' in req.content
    assert b"Saw,Garage/Loft" in req.content


# -- labels ---------------------------------------------------------------
def test_generate_label_for_location_saves_png(box, tmp_path):
    tote = box.add_location("Tote 1")
    out = hb.generate_label("Tote 1", out_dir=str(tmp_path))
    assert out["kind"] == "location"
    assert out["path"] == str(tmp_path / "label-location-Tote-1.png")
    assert (tmp_path / "label-location-Tote-1.png").read_bytes() == b"\x89PNG-label"
    assert box.called("GET", f"/labelmaker/location/{tote['id']}")


def test_generate_label_for_item_and_asset(box, tmp_path):
    drill = box.add_item("Drill", assetId="000-001")
    hb.generate_label("Drill", kind="item", out_dir=str(tmp_path))
    hb.generate_label("000-001", kind="asset", out_dir=str(tmp_path))
    assert box.called("GET", f"/labelmaker/item/{drill['id']}")
    assert box.called("GET", "/labelmaker/asset/000-001")


def test_generate_label_unknown_target(box, tmp_path):
    assert "no location named" in hb.generate_label(
        "Attic", out_dir=str(tmp_path))["error"]
    assert "no item found" in hb.generate_label(
        "Lathe", kind="item", out_dir=str(tmp_path))["error"]
    assert list(tmp_path.iterdir()) == []


def test_label_dir_falls_back_to_env_then_cwd(box, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert hb.qrcode("https://x.test/a?b=1")["path"] == str(
        tmp_path / "qr-https---x-test-a-b-1.jpg")
    monkeypatch.setattr(hb, "HOMEBOX_LABEL_DIR", str(tmp_path / "labels"))
    out = hb.qrcode("abc")
    assert out["path"] == str(tmp_path / "labels" / "qr-abc.jpg")
    assert out["bytes"] == 7


# -- finalize_photos ------------------------------------------------------
def test_finalize_photos_runs_both_actions_in_order(box):
    assert hb.finalize_photos()["ok"] is True
    assert [c[1] for c in box.called("POST", r"/actions/.*")] == [
        "/actions/set-primary-photos", "/actions/create-missing-thumbnails"]
