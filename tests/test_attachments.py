"""Attachment tools: attach, list, download, rename, delete."""
from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import homebox_mcp as hb


@pytest.fixture
def saw(box):
    garage = box.add_location("Garage")
    return box.add_item("Saw", parent=garage, assetId="000-001")


def test_attach_local_file_to_item(box, saw, tmp_path):
    src = tmp_path / "manual.pdf"
    src.write_bytes(b"%PDF")
    out = hb.attach_document("Saw", str(src), "Owner manual 3/4", doc_type="manual")
    # Homebox basenames a title on '/', so the slash must not go out
    assert out == {"ok": True, "item": "Saw", "attached": "Owner manual 3-4",
                   "type": "manual"}
    (att,) = saw["attachments"]
    assert att["title"] == "Owner manual 3-4"
    assert att["primary"] is False


def test_attach_primary_photo_to_location(box, saw, tmp_path):
    src = tmp_path / "spot.jpg"
    src.write_bytes(b"jpg")
    out = hb.attach_document("Garage", str(src), "The spot", doc_type="photo",
                             primary=True)
    assert out["item"] == "Garage"
    garage = next(e for e in box.entities.values() if e["name"] == "Garage")
    assert garage["attachments"][0]["primary"] is True
    assert garage["attachments"][0]["type"] == "photo"


def test_attach_missing_file(box, saw, tmp_path):
    out = hb.attach_document("Saw", str(tmp_path / "nope.pdf"), "x")
    assert "file not found" in out["error"]
    assert not box.called("POST", r"/entities/.*/attachments")


def test_attach_unknown_target(box, saw, tmp_path):
    out = hb.attach_document("Lathe", str(tmp_path / "x.pdf"), "x")
    assert "no item or location exactly matched 'Lathe'" in out["error"]


def test_attach_ambiguous_item_does_not_fall_back_to_location(box, saw, tmp_path):
    box.add_item("Saw", assetId="000-002")
    box.add_location("Saw")
    out = hb.attach_document("Saw", str(tmp_path / "x.pdf"), "x")
    assert "ambiguous" in out["error"]


def test_list_attachments(box, saw):
    att = box.add_attachment(saw, "Manual")
    assert hb.list_attachments("000-001") == [{
        "id": att["id"], "type": "manual", "title": "Manual", "primary": False,
        "mimeType": "application/pdf", "createdAt": "2026-01-01T00:00:00Z"}]


def test_list_attachments_unknown_target(box):
    with pytest.raises(ToolError, match="no item or location found"):
        hb.list_attachments("Lathe")


def test_get_attachment_into_directory_adds_extension(box, saw, tmp_path):
    att = box.add_attachment(saw, "Owner: manual", content=b"%PDF-1.7")
    out = hb.get_attachment("Saw", att["id"], str(tmp_path))
    assert out["bytes"] == 8
    assert out["mimeType"] == "application/pdf"
    assert out["path"] == str(tmp_path / "Owner- manual.pdf")
    assert (tmp_path / "Owner- manual.pdf").read_bytes() == b"%PDF-1.7"


def test_get_attachment_to_file_path(box, saw, tmp_path):
    att = box.add_attachment(saw, "Manual", content=b"abc")
    dest = tmp_path / "sub" / "m.pdf"
    hb.get_attachment("Saw", att["id"], str(dest))
    assert dest.read_bytes() == b"abc"


def test_get_attachment_unknown_id(box, saw, tmp_path):
    out = hb.get_attachment("Saw", "nope", str(tmp_path))
    assert "no attachment with id 'nope'" in out["error"]


def test_rename_attachment_changes_only_what_is_passed(box, saw):
    att = box.add_attachment(saw, "Manual", type="manual", primary=True)
    out = hb.rename_attachment("Saw", att["id"], title="Parts/diagram")
    assert out == {"ok": True, "entity": "Saw", "title": "Parts-diagram",
                   "type": "manual", "primary": True}
    hb.rename_attachment("Saw", att["id"], doc_type="attachment", primary=False)
    assert (att["title"], att["type"], att["primary"]) == (
        "Parts-diagram", "attachment", False)


def test_delete_attachment_wrong_confirm_blocks_delete(box, saw):
    att = box.add_attachment(saw, "Manual")
    out = hb.delete_attachment("Saw", att["id"], confirm="manual")
    assert "confirm must equal" in out["error"]
    assert saw["attachments"] == [att]
    assert not box.called("DELETE", r".*")


def test_delete_attachment(box, saw):
    att = box.add_attachment(saw, "Manual")
    out = hb.delete_attachment("Saw", att["id"], confirm="Manual")
    assert out == {"ok": True, "deleted": "Manual", "from": "Saw"}
    assert saw["attachments"] == []
