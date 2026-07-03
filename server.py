#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2", "httpx>=0.27", "pillow>=10", "pillow-heif>=0.16"]
# ///
"""Homebox MCP server.

A thin wrapper over the Homebox REST API (v0.26+) so Claude can answer
inventory questions ("where is my X", "what's in Tote B-3", "which warranties
expire this year") and perform intake (create item + attach manual).

Config (resolved in order):
  1. environment variables HOMEBOX_URL / HOMEBOX_TOKEN
  2. a sibling `.env` file (KEY=VALUE lines) next to this script  [gitignored]

In Homebox >=0.26 items and locations are unified as "entities"; an entity is a
location when its entity-type has isLocation=true. Labels are "tags". This
server exposes items (non-location entities), locations, and tags.
"""
from __future__ import annotations

import os
import sys
import mimetypes
from pathlib import Path
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _load_env_file() -> None:
    """Populate os.environ from a sibling .env (without clobbering real env)."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_env_file()

HOMEBOX_URL = os.environ.get("HOMEBOX_URL", "http://localhost:7745").rstrip("/")
HOMEBOX_TOKEN = os.environ.get("HOMEBOX_TOKEN", "")
API = f"{HOMEBOX_URL}/api/v1"

if not HOMEBOX_TOKEN:
    sys.stderr.write(
        "homebox-mcp: HOMEBOX_TOKEN is not set (env or homebox-mcp/.env). "
        "Create homebox-mcp/.env from .env.example.\n"
    )

_client = httpx.Client(
    base_url=API,
    headers={"Authorization": HOMEBOX_TOKEN},
    timeout=30.0,
)

mcp = FastMCP("homebox")


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------
def _get(path: str, **params) -> Any:
    r = _client.get(path, params={k: v for k, v in params.items() if v is not None})
    r.raise_for_status()
    return r.json() if r.content else None


def _post(path: str, body: dict) -> Any:
    r = _client.post(path, json=body)
    r.raise_for_status()
    return r.json() if r.content else None


def _put(path: str, body: dict) -> Any:
    r = _client.put(path, json=body)
    r.raise_for_status()
    return r.json() if r.content else None


def _attachment_title(title: str) -> str:
    """Homebox basenames an attachment title on '/' (it treats the name as a
    path), silently truncating e.g. 'front 3/4 view' to '4 view'. Replace
    slashes so titles survive intact."""
    return title.replace("/", "-")


def _heic_to_jpeg(filename: str, content: bytes, ctype: str) -> tuple[str, bytes, str]:
    """Transparently convert HEIC/HEIF input to JPEG.

    Apple HEIC doesn't render in browsers or Homebox, so any .heic/.heif source
    is re-encoded to JPEG before upload. pillow-heif bundles libheif, so there's
    no system package to install. Non-HEIC input passes through untouched.
    """
    if not (filename.lower().endswith((".heic", ".heif"))
            or "heic" in ctype.lower() or "heif" in ctype.lower()):
        return filename, content, ctype
    import io
    import pillow_heif
    from PIL import Image
    pillow_heif.register_heif_opener()
    img = Image.open(io.BytesIO(content)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    newname = (filename.rsplit(".", 1)[0] or "image") + ".jpg"
    return newname, buf.getvalue(), "image/jpeg"


def _load_source(source: str) -> tuple[str, bytes, str]:
    """Load attachment bytes from a local path or an http(s) URL.

    Returns (filename, content, content_type). Raises FileNotFoundError if a
    local path does not exist. HEIC/HEIF is auto-converted to JPEG (see
    _heic_to_jpeg) so Homebox always stores a browser-renderable image.
    """
    if source.startswith(("http://", "https://")):
        resp = httpx.get(source, follow_redirects=True, timeout=60.0)
        resp.raise_for_status()
        filename = source.rsplit("/", 1)[-1] or "document"
        ctype = resp.headers.get("content-type", "application/octet-stream").split(";")[0]
        content = resp.content
    else:
        p = Path(source).expanduser()
        if not p.exists():
            raise FileNotFoundError(source)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        filename, content = p.name, p.read_bytes()
    return _heic_to_jpeg(filename, content, ctype)


def _is_location(entity: dict) -> bool:
    et = entity.get("entityType") or {}
    return bool(et.get("isLocation"))


def _field(entity: dict, name: str) -> Optional[str]:
    for f in entity.get("fields") or []:
        if f.get("name") == name:
            return f.get("textValue") or None
    return None


def _field_value(f: dict) -> Any:
    """Read a custom field's value by its declared type (text/number/boolean)."""
    t = f.get("type", "text")
    if t == "number":
        return f.get("numberValue")
    if t == "boolean":
        return f.get("booleanValue")
    return f.get("textValue")


def _make_field(name: str, value: Any, type: str = "text") -> dict:
    """Build a custom-field entry with the correct value key for its type."""
    entry: dict = {"name": name, "type": type}
    if type == "number":
        # Homebox's number custom field is integer-typed: a float JSON value
        # (e.g. 17000.0, which FastMCP produces from a float-typed arg) makes the
        # API 500 with "Unknown Error". Coerce to int.
        entry["numberValue"] = int(value) if isinstance(value, float) else value
    elif type == "boolean":
        entry["booleanValue"] = value
    else:
        entry["textValue"] = value
    return entry


def _upsert_field(body: dict, name: str, value: Any, type: str = "text") -> None:
    """Add or replace a custom field in a PUT body's `fields` list, in place."""
    flds = body.setdefault("fields", [])
    new = _make_field(name, value, type)
    for i, f in enumerate(flds):
        if f.get("name") == name:
            flds[i] = new
            return
    flds.append(new)


def _location_path(entity_id: str) -> str:
    """Full 'A → B → C' location path for an entity (excluding itself)."""
    try:
        nodes = _get(f"/entities/{entity_id}/path") or []
    except Exception:
        return ""
    names = [n.get("name", "") for n in nodes]
    # the path endpoint includes the entity itself last; drop it
    if names:
        names = names[:-1]
    return " → ".join(names)


def _summarize(entity: dict, with_path: bool = False) -> dict:
    out = {
        "id": entity.get("id"),
        "assetId": entity.get("assetId") or None,
        "name": entity.get("name"),
        "item_id": _field(entity, "item_id"),
        "quantity": entity.get("quantity"),
    }
    parent = entity.get("parent") or {}
    out["location"] = parent.get("name")
    if with_path and entity.get("id"):
        out["location_path"] = _location_path(entity["id"])
    return out


def _search_entities(q: Optional[str] = None, page_size: int = 200) -> list[dict]:
    data = _get("/entities", q=q, pageSize=page_size)
    if isinstance(data, dict):
        return data.get("items") or data.get("entities") or []
    return data or []


def _resolve(identifier: str) -> Optional[dict]:
    """Find one entity by assetId, item_id custom field, exact name, or substring.

    Note: list/search responses are lightweight summaries (no `fields`, empty
    `assetId`), so we fetch full detail before matching on those.
    """
    ident = identifier.strip()
    # 1) assetId via the dedicated assets endpoint (e.g. "000-028")
    try:
        ares = _get(f"/assets/{ident}")
        items = ares.get("items") if isinstance(ares, dict) else None
        if items:
            return _get(f"/entities/{items[0]['id']}")
    except Exception:
        pass
    # 2) keyword search (matches name/description), then match on full detail
    candidates = [_get(f"/entities/{e['id']}") for e in _search_entities(q=ident)]
    for full in candidates:
        if (_field(full, "item_id") or "").lower() == ident.lower():
            return full
        if (full.get("name") or "").lower() == ident.lower():
            return full
    # 3) fallback: q does NOT index custom fields, so scan all items for an
    #    exact item_id match (covers lookups by the join slug)
    for e in _search_entities():
        if _is_location(e):
            continue
        full = _get(f"/entities/{e['id']}")
        if (_field(full, "item_id") or "").lower() == ident.lower():
            return full
    # 4) last resort: first keyword candidate that is an item
    for full in candidates:
        if not _is_location(full):
            return full
    return None


# ---------------------------------------------------------------------------
# Tools — read
# ---------------------------------------------------------------------------
@mcp.tool()
def search_items(query: str, limit: int = 20) -> list[dict]:
    """Search inventory items by name/keyword.

    Returns items (not locations) with their immediate location, assetId, and
    item_id. Use get_item for full detail on one result.
    """
    results = [e for e in _search_entities(q=query) if not _is_location(e)]
    # list responses omit assetId/fields, so fetch full detail for the page
    return [_summarize(_get(f"/entities/{e['id']}")) for e in results[:limit]]


@mcp.tool()
def get_item(identifier: str) -> dict:
    """Get full detail for one item by assetId (e.g. 000-028), item_id slug,
    or name. Includes location path, serial, model, purchase, warranty, custom
    fields, tags, and attachments."""
    e = _resolve(identifier)
    if not e:
        return {"error": f"no item found matching '{identifier}'"}
    return {
        "id": e.get("id"),
        "assetId": e.get("assetId") or None,
        "name": e.get("name"),
        "location_path": _location_path(e["id"]),
        "manufacturer": e.get("manufacturer") or None,
        "modelNumber": e.get("modelNumber") or None,
        "serialNumber": e.get("serialNumber") or None,
        "quantity": e.get("quantity"),
        "purchasePrice": e.get("purchasePrice") or None,
        "purchaseDate": e.get("purchaseDate") or None,
        "purchaseFrom": e.get("purchaseFrom") or None,
        "warrantyExpires": e.get("warrantyExpires") or None,
        "lifetimeWarranty": e.get("lifetimeWarranty"),
        "notes": e.get("notes") or None,
        "tags": [t.get("name") for t in e.get("tags") or []],
        "fields": {f.get("name"): _field_value(f) for f in e.get("fields") or []},
        "attachments": [
            {"type": a.get("type"), "title": a.get("title")
             or (a.get("document") or {}).get("title")}
            for a in e.get("attachments") or []
        ],
    }


@mcp.tool()
def list_locations() -> str:
    """Return the full location tree as an indented outline."""
    tree = _get("/entities/tree") or []
    lines: list[str] = []

    def walk(node: dict, depth: int = 0) -> None:
        if node.get("type") == "location" or node.get("children") is not None:
            lines.append("  " * depth + "- " + node.get("name", "?"))
            for c in sorted(node.get("children") or [], key=lambda x: x.get("name", "")):
                walk(c, depth + 1)

    for n in sorted(tree, key=lambda x: x.get("name", "")):
        walk(n)
    return "\n".join(lines)


@mcp.tool()
def location_contents(location: str, recursive: bool = False) -> dict:
    """List what is in a location (e.g. a tote or shelf), by location name.

    Returns the items directly in that location plus any sub-locations (their
    names only — not their contents). Set `recursive=True` to instead walk the
    whole subtree and return every item nested under it, each tagged with its
    full location path; use this when a location (e.g. Garage, Basement) has
    sub-locations you also want the contents of, to avoid querying each one by
    hand.
    """
    # find the location entity id from the tree
    tree = _get("/entities/tree") or []
    target: dict = {"id": None, "node": None}

    def find(nodes: list[dict]) -> None:
        for n in nodes:
            if n.get("name", "").lower() == location.lower() and target["id"] is None:
                target["id"] = n.get("id")
                target["node"] = n
            find(n.get("children") or [])

    find(tree)
    if not target["id"]:
        return {"error": f"no location named '{location}'"}

    if not recursive:
        children = _get("/entities", parentIds=target["id"], pageSize=500)
        items = children.get("items") if isinstance(children, dict) else children
        sublocs = [e.get("name") for e in (items or []) if _is_location(e)]
        return {
            "location": location,
            "items": [_summarize(_get(f"/entities/{e['id']}"))
                      for e in (items or []) if not _is_location(e)],
            "sub_locations": sublocs,
        }

    results: list[dict] = []

    def walk(node: dict, path: list[str]) -> None:
        children = _get("/entities", parentIds=node["id"], pageSize=500)
        items = children.get("items") if isinstance(children, dict) else children
        for e in (items or []):
            if _is_location(e):
                walk(e, path + [e.get("name")])
            else:
                full = _get(f"/entities/{e['id']}")
                summary = _summarize(full)
                summary["location"] = " → ".join(path)
                results.append(summary)

    walk(target["node"], [location])
    return {"location": location, "items": results, "recursive": True}


@mcp.tool()
def list_tags(detail: bool = False) -> Any:
    """List all tag (label) names. Set `detail=True` to instead return full
    tag objects (name, description, color, icon, parent tag name) for
    auditing tag setup — pair with set_tag to edit any of these."""
    tags = _get("/tags") or []
    if not detail:
        return sorted(t.get("name") for t in tags)
    by_id = {t["id"]: t.get("name") for t in tags}
    out = []
    for t in tags:
        parent_id = t.get("parentId")
        parent_name = (
            by_id.get(parent_id)
            if parent_id and parent_id != "00000000-0000-0000-0000-000000000000"
            else None
        )
        out.append({
            "name": t.get("name"),
            "description": t.get("description") or None,
            "color": t.get("color") or None,
            "icon": t.get("icon") or None,
            "parent": parent_name,
        })
    return sorted(out, key=lambda x: x["name"])


@mcp.tool()
def warranties_expiring(before: str) -> list[dict]:
    """List items whose warranty expires on/before an ISO date (YYYY-MM-DD).

    Useful for "which warranties expire this year".
    """
    out = []
    for e in _search_entities():
        if _is_location(e):
            continue
        full = _get(f"/entities/{e['id']}")
        w = (full.get("warrantyExpires") or "")[:10]
        if w and w <= before:
            out.append({**_summarize(full), "warrantyExpires": w})
    return sorted(out, key=lambda x: x["warrantyExpires"])


# ---------------------------------------------------------------------------
# Tools — write (intake)
# ---------------------------------------------------------------------------
def _location_id(name: str) -> Optional[str]:
    tree = _get("/entities/tree") or []
    found = {"id": None}

    def find(nodes):
        for n in nodes:
            if n.get("name", "").lower() == name.lower() and found["id"] is None:
                found["id"] = n.get("id")
            find(n.get("children") or [])

    find(tree)
    return found["id"]


@mcp.tool()
def create_item(
    name: str,
    location: Optional[str] = None,
    quantity: int = 1,
    manufacturer: Optional[str] = None,
    model: Optional[str] = None,
    serial: Optional[str] = None,
    purchase_price: Optional[float] = None,
    purchase_date: Optional[str] = None,
    purchase_from: Optional[str] = None,
    warranty_expires: Optional[str] = None,
    notes: Optional[str] = None,
    item_id: Optional[str] = None,
    category: Optional[str] = None,
    dossier: Optional[str] = None,
    resale_value: Optional[float] = None,
    value_asof: Optional[str] = None,
    value_source: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> dict:
    """Create an inventory item and enrich it in one call.

    `location` is a location name (must exist; use list_locations). `item_id`
    is the stable join slug (e.g. gear-dji-osmo-action6). `tags` are tag names
    (must already exist; use list_tags). `resale_value`/`value_asof`/
    `value_source` are for big depreciating assets only (vehicles, watercraft) —
    NOT general inventory, where purchase_price is the insurance anchor; see
    set_value. Returns the new assetId and id.
    """
    parent_id = _location_id(location) if location else None
    if location and not parent_id:
        return {"error": f"no location named '{location}' (see list_locations)"}

    created = _post("/entities", {"name": name, **({"parentId": parent_id} if parent_id else {})})
    iid = created["id"]

    tag_ids = []
    if tags:
        all_tags = {t["name"].lower(): t["id"] for t in (_get("/tags") or [])}
        for t in tags:
            tid = all_tags.get(t.lower())
            if tid:
                tag_ids.append(tid)

    fields = []
    for fname, fval in (("item_id", item_id), ("category", category), ("dossier", dossier),
                        ("value_asof", value_asof), ("value_source", value_source)):
        if fval is not None:
            fields.append(_make_field(fname, fval, "text"))
    if resale_value is not None:
        fields.append(_make_field("resale_value", resale_value, "number"))

    body: dict = {"id": iid, "name": name, "quantity": quantity}
    if parent_id:
        body["parentId"] = parent_id
    for k, v in (
        ("manufacturer", manufacturer), ("modelNumber", model),
        ("serialNumber", serial), ("purchaseDate", purchase_date),
        ("purchaseFrom", purchase_from), ("warrantyExpires", warranty_expires),
        ("notes", notes),
    ):
        if v is not None:
            body[k] = v
    if purchase_price is not None:
        body["purchasePrice"] = purchase_price
    if tag_ids:
        body["tagIds"] = tag_ids
    if fields:
        body["fields"] = fields

    _put(f"/entities/{iid}", body)
    # assign asset id
    try:
        _post("/actions/ensure-asset-ids", {})
    except Exception:
        pass
    full = _get(f"/entities/{iid}")
    return {"id": iid, "assetId": full.get("assetId"), "name": name,
            "location": location, "item_id": item_id}


@mcp.tool()
def attach_document(
    identifier: str,
    source: str,
    title: str,
    doc_type: str = "manual",
    primary: bool = False,
) -> dict:
    """Attach a document to an item OR a location. `source` is a local file path
    or an http(s) URL (downloaded then uploaded). `doc_type` is e.g. manual,
    attachment, warranty, receipt, photo. `identifier` is assetId/item_id/name
    for an item, or a location name (tried as a fallback if no item matches —
    for a location photo specifically, prefer attach_location_photo, which
    defaults primary=True for wayfinding shots). Set `primary` to make a photo
    the entity's primary image."""
    e = _resolve(identifier)
    name = e.get("name") if e else None
    iid = e["id"] if e else _location_id(identifier)
    if not iid:
        return {"error": f"no item or location found matching '{identifier}'"}
    if name is None:
        name = identifier

    try:
        filename, content, ctype = _load_source(source)
    except FileNotFoundError:
        return {"error": f"file not found: {source}"}

    safe_title = _attachment_title(title)
    files = {"file": (filename, content, ctype)}
    data = {"name": safe_title, "type": doc_type}
    if primary:
        data["primary"] = "true"
    r = _client.post(f"/entities/{iid}/attachments", files=files, data=data)
    r.raise_for_status()
    return {"ok": True, "item": name, "attached": safe_title, "type": doc_type}


@mcp.tool()
def attach_location_photo(
    location: str,
    source: str,
    title: Optional[str] = None,
    primary: bool = True,
) -> dict:
    """Attach a photo to a LOCATION (e.g. a shelf) and make it the location's
    primary image. Use this to give a shelf/tote a "this is the spot" photo for
    family wayfinding. `location` is a location name (see list_locations);
    `source` is a local file path or an http(s) URL."""
    loc_id = _location_id(location)
    if not loc_id:
        return {"error": f"no location named '{location}' (see list_locations)"}

    try:
        filename, content, ctype = _load_source(source)
    except FileNotFoundError:
        return {"error": f"file not found: {source}"}

    safe_title = _attachment_title(title) if title else filename
    files = {"file": (filename, content, ctype)}
    data = {"name": safe_title, "type": "photo"}
    if primary:
        data["primary"] = "true"
    r = _client.post(f"/entities/{loc_id}/attachments", files=files, data=data)
    r.raise_for_status()
    return {"ok": True, "location": location, "attached": safe_title,
            "type": "photo", "primary": primary}


# ---------------------------------------------------------------------------
# Tools — bulk intake (photo pipeline / CSV import)
# ---------------------------------------------------------------------------
_LOCATION_TYPE_ID: Optional[str] = None


def _location_type_id() -> Optional[str]:
    """Resolve (and cache) the entity-type id whose isLocation=true.

    Entity-type ids are per-instance, so we discover it rather than hardcode.
    """
    global _LOCATION_TYPE_ID
    if _LOCATION_TYPE_ID is None:
        for t in _get("/entity-types") or []:
            if t.get("isLocation"):
                _LOCATION_TYPE_ID = t.get("id")
                break
    return _LOCATION_TYPE_ID


@mcp.tool()
def import_csv(csv_text: str) -> dict:
    """Bulk-create items and locations from a Homebox CSV (multipart import).

    This is the bulk creator for the photo-intake pipeline — one request imports
    everything, avoiding ~4 API calls per item. Recognized columns:
      HB.name, HB.location (path e.g. "Garage/Loft" — AUTO-CREATES the hierarchy),
      HB.tags (must already exist; see list_tags), HB.quantity, HB.serial_number,
      HB.model_number, HB.manufacturer, HB.notes, HB.purchase_price,
      HB.purchase_from, HB.purchase_time, HB.warranty_expires,
      HB.field.<custom> (e.g. HB.field.item_id, HB.field.category).
    A location-only row (HB.location set, contents described elsewhere) creates
    just the location. After import, call set_primary_photos + create_thumbnails
    if you attached photos. Returns the count of data rows submitted."""
    rows = max(0, len([ln for ln in csv_text.splitlines() if ln.strip()]) - 1)
    files = {"csv": ("import.csv", csv_text.encode("utf-8"), "text/csv")}
    r = _client.post("/entities/import", files=files)
    r.raise_for_status()
    return {"ok": True, "status_code": r.status_code, "rows_submitted": rows}


@mcp.tool()
def create_location(
    name: str,
    parent: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Create a LOCATION entity (e.g. a new tote/bin/shelf) to bootstrap a
    QR-first capture group. `parent` is an existing location *name* (optional);
    `description` is the optional contents manifest (≤1000 chars). Returns the
    new location id. For deep paths, prefer import_csv's HB.location auto-create."""
    if _location_id(name):
        return {"error": f"a location named '{name}' already exists"}
    parent_id = _location_id(parent) if parent else None
    if parent and not parent_id:
        return {"error": f"no parent location named '{parent}' (see list_locations)"}
    type_id = _location_type_id()
    body: dict = {"name": name}
    if type_id:
        body["entityTypeId"] = type_id
    if parent_id:
        body["parentId"] = parent_id
    created = _post("/entities", body)
    lid = created["id"]
    if description is not None:
        put_body: dict = {"id": lid, "name": name, "description": description}
        if type_id:
            put_body["entityTypeId"] = type_id
        if parent_id:
            put_body["parentId"] = parent_id
        _put(f"/entities/{lid}", put_body)
    return {"ok": True, "id": lid, "name": name, "parent": parent}


@mcp.tool()
def set_location_manifest(location: str, description: str) -> dict:
    """Set a location's `description` to a contents manifest (the AI-written
    "what lives in this bin" text). `location` is a location name. Echoes back
    name/parent/assetId so the PUT does not wipe them (0.26 PUT-clears gotcha)."""
    lid = _location_id(location)
    if not lid:
        return {"error": f"no location named '{location}' (see list_locations)"}
    full = _get(f"/entities/{lid}")
    body: dict = {"id": lid, "name": full.get("name"), "description": description}
    if full.get("assetId"):
        body["assetId"] = full["assetId"]
    if (full.get("entityType") or {}).get("id"):
        body["entityTypeId"] = full["entityType"]["id"]
    parent = full.get("parent") or {}
    if parent.get("id"):
        body["parentId"] = parent["id"]
    _put(f"/entities/{lid}", body)
    return {"ok": True, "location": location, "manifest_chars": len(description)}


def _entity_type_id(name: str) -> Optional[str]:
    for t in (_get("/entity-types") or []):
        if t.get("name", "").lower() == name.lower():
            return t.get("id")
    return None


@mcp.tool()
def set_location(
    location: str,
    new_name: Optional[str] = None,
    parent: Optional[str] = None,
    clear_parent: bool = False,
    description: Optional[str] = None,
    notes: Optional[str] = None,
    tags: Optional[list[str]] = None,
    tags_mode: str = "add",
    entity_type: Optional[str] = None,
    asset_id: Optional[str] = None,
    fields: Optional[dict] = None,
) -> dict:
    """Edit an existing LOCATION's own metadata (find it by its current name;
    see list_locations). General-purpose sibling of set_location_manifest —
    use that one if you're only setting the contents-manifest description.

    Only the args you pass are changed; a full-body PUT preserves everything
    else (existing tags, custom fields, assetId, photos) per the 0.26
    PUT-clears gotcha. `parent` moves it under another existing location name;
    `clear_parent=True` moves it to the root instead. `tags` are tag names
    (auto-created if new); `tags_mode` is add (default) / remove / replace,
    same semantics as set_tags. `entity_type` is an entity-type name (e.g.
    "Item" to convert a location into a non-location entity) — rare, only for
    fixing a mis-created entity. `asset_id` force-overrides the normally
    auto-assigned assetId. `fields` is a dict of custom-field name -> text
    value to upsert (create or overwrite each named field)."""
    loc_id = _location_id(location)
    if not loc_id:
        return {"error": f"no location named '{location}' (see list_locations)"}
    full = _get(f"/entities/{loc_id}")
    body = _preserve_item_body(full)

    if new_name is not None:
        body["name"] = new_name
    if description is not None:
        body["description"] = description
    if notes is not None:
        body["notes"] = notes
    if asset_id is not None:
        body["assetId"] = asset_id
    if entity_type is not None:
        et_id = _entity_type_id(entity_type)
        if not et_id:
            return {"error": f"no entity type named '{entity_type}'"}
        body["entityTypeId"] = et_id

    if clear_parent:
        body.pop("parentId", None)
    elif parent is not None:
        parent_id = _location_id(parent)
        if not parent_id:
            return {"error": f"no location named '{parent}' (see list_locations)"}
        if parent_id == loc_id:
            return {"error": "a location cannot be its own parent"}
        body["parentId"] = parent_id

    if tags is not None:
        if tags_mode not in ("add", "remove", "replace"):
            return {"error": f"tags_mode must be add/remove/replace, got '{tags_mode}'"}
        current = {t["name"].lower(): t["id"] for t in (full.get("tags") or [])}
        if tags_mode == "replace":
            new_ids = _ensure_tag_ids(tags)
        elif tags_mode == "remove":
            drop = {t.lower() for t in tags}
            new_ids = [tid for name, tid in current.items() if name not in drop]
        else:  # add
            new_ids = list(current.values())
            for tid in _ensure_tag_ids(tags):
                if tid not in new_ids:
                    new_ids.append(tid)
        body["tagIds"] = new_ids

    if fields:
        for fname, fval in fields.items():
            _upsert_field(body, fname, fval, "text")

    _put(f"/entities/{loc_id}", body)
    after = _get(f"/entities/{loc_id}")
    return {
        "ok": True,
        "id": loc_id,
        "assetId": after.get("assetId"),
        "name": after.get("name"),
        "description": after.get("description") or None,
        "notes": after.get("notes") or None,
        "parent": (after.get("parent") or {}).get("name"),
        "tags": sorted(t.get("name") for t in after.get("tags") or []),
        "entityType": (after.get("entityType") or {}).get("name"),
        "fields": {f.get("name"): _field_value(f) for f in after.get("fields") or []},
    }


def _rfc3339(d: str) -> str:
    """Normalize a YYYY-MM-DD date (or full timestamp) to an RFC3339 string."""
    d = d.strip()
    if "T" in d:
        return d
    return f"{d}T00:00:00Z"


def _preserve_item_body(full: dict) -> dict:
    """Rebuild a full item PUT body from a GET so a partial update does not wipe
    other fields (the 0.26 PUT-clears gotcha). Override the keys you want, then
    PUT. Preserves scalars, ids, tags, custom fields, and warranty fields."""
    body: dict = {"id": full["id"]}
    for k in (
        "name", "description", "quantity", "insured", "archived",
        "manufacturer", "modelNumber", "serialNumber", "notes",
        "purchasePrice", "purchaseFrom", "purchaseDate",
        "soldDate", "soldTo", "soldPrice", "soldNotes",
        "warrantyExpires", "warrantyDetails",
    ):
        if full.get(k) is not None:
            body[k] = full[k]
    if full.get("lifetimeWarranty") is not None:
        body["lifetimeWarranty"] = full["lifetimeWarranty"]
    if full.get("assetId"):
        body["assetId"] = full["assetId"]
    if (full.get("entityType") or {}).get("id"):
        body["entityTypeId"] = full["entityType"]["id"]
    if (full.get("parent") or {}).get("id"):
        body["parentId"] = full["parent"]["id"]
    tag_ids = [t["id"] for t in (full.get("tags") or []) if t.get("id")]
    if tag_ids:
        body["tagIds"] = tag_ids
    flds = full.get("fields") or []
    if flds:
        # Preserve each field's value by its declared type — coercing everything
        # to textValue (the old behavior) silently wiped numeric/boolean fields.
        body["fields"] = [
            _make_field(f.get("name"), _field_value(f), f.get("type", "text"))
            for f in flds
        ]
    return body


@mcp.tool()
def set_warranty(
    identifier: str,
    expires: Optional[str] = None,
    lifetime: Optional[bool] = None,
    details: Optional[str] = None,
) -> dict:
    """Set warranty info on an existing item (assetId / item_id slug / name).

    `expires` is a YYYY-MM-DD date (warranty end), `lifetime` flags a lifetime
    warranty, `details` is a short terms summary (e.g. "Klein limited lifetime,
    test/measurement instruments excluded"). Only the args you pass are changed;
    everything else on the item (price, tags, custom fields, photos) is preserved
    via a full-body PUT. Pair with the `warranty-active` tag. Used by the shared
    enrichment flow (see inventory/ENRICHMENT.md) and the /enrich-inventory sweep."""
    full = _resolve(identifier)
    if not full:
        return {"error": f"no item matched '{identifier}'"}
    body = _preserve_item_body(full)
    if details is not None:
        body["warrantyDetails"] = details
    if lifetime is not None:
        body["lifetimeWarranty"] = lifetime
    if expires is not None:
        body["warrantyExpires"] = _rfc3339(expires)
    _put(f"/entities/{full['id']}", body)
    after = _get(f"/entities/{full['id']}")
    return {
        "ok": True,
        "assetId": after.get("assetId"),
        "name": after.get("name"),
        "lifetimeWarranty": after.get("lifetimeWarranty"),
        "warrantyExpires": after.get("warrantyExpires"),
        "warrantyDetails": after.get("warrantyDetails"),
    }


@mcp.tool()
def set_value(
    identifier: str,
    resale_value: Optional[float] = None,
    value_asof: Optional[str] = None,
    value_source: Optional[str] = None,
) -> dict:
    """Set current market/resale value on an existing item (assetId / item_id / name).

    For **big depreciating assets only** (vehicles, watercraft) — NOT general
    inventory, where `purchase_price` is the insurance/replacement-cost anchor and
    a resale estimate is effort for little reward. `resale_value` is a number (USD),
    `value_asof` the YYYY-MM-DD the estimate was made (so a stale figure is obvious),
    `value_source` a short basis (e.g. "KBB private-party, high-mileage adj").
    Stored as custom fields; everything else on the item is preserved via a
    full-body PUT (the 0.26 PUT-clears gotcha)."""
    full = _resolve(identifier)
    if not full:
        return {"error": f"no item matched '{identifier}'"}
    body = _preserve_item_body(full)
    if resale_value is not None:
        _upsert_field(body, "resale_value", resale_value, "number")
    if value_asof is not None:
        _upsert_field(body, "value_asof", value_asof, "text")
    if value_source is not None:
        _upsert_field(body, "value_source", value_source, "text")
    _put(f"/entities/{full['id']}", body)
    after = _get(f"/entities/{full['id']}")
    return {
        "ok": True,
        "assetId": after.get("assetId"),
        "name": after.get("name"),
        "fields": {f.get("name"): _field_value(f) for f in after.get("fields") or []},
    }


@mcp.tool()
def set_identity(
    identifier: str,
    manufacturer: Optional[str] = None,
    model_number: Optional[str] = None,
    serial_number: Optional[str] = None,
) -> dict:
    """Set manufacturer/model/serial on an existing item (assetId / item_id / name).

    Only the args you pass are changed; everything else on the item (price,
    tags, custom fields, photos, warranty) is preserved via a full-body PUT
    (the 0.26 PUT-clears gotcha). Use when a nameplate/label photo reveals a
    serial number or a model-number correction after the item was created."""
    full = _resolve(identifier)
    if not full:
        return {"error": f"no item matched '{identifier}'"}
    body = _preserve_item_body(full)
    if manufacturer is not None:
        body["manufacturer"] = manufacturer
    if model_number is not None:
        body["modelNumber"] = model_number
    if serial_number is not None:
        body["serialNumber"] = serial_number
    _put(f"/entities/{full['id']}", body)
    after = _get(f"/entities/{full['id']}")
    return {
        "ok": True,
        "assetId": after.get("assetId"),
        "name": after.get("name"),
        "manufacturer": after.get("manufacturer"),
        "modelNumber": after.get("modelNumber"),
        "serialNumber": after.get("serialNumber"),
    }


def _ensure_tag_ids(names: list[str]) -> list[str]:
    """Resolve tag names to ids, case-insensitively, creating any that don't
    exist yet."""
    existing = {t["name"].lower(): t["id"] for t in (_get("/tags") or [])}
    ids = []
    for name in names:
        tid = existing.get(name.lower())
        if not tid:
            created = _post("/tags", {"name": name})
            tid = created["id"]
            existing[name.lower()] = tid
        ids.append(tid)
    return ids


@mcp.tool()
def set_tags(identifier: str, tags: list[str], mode: str = "add") -> dict:
    """Add, remove, or replace tags on an existing item (assetId / item_id / name).

    `mode` is "add" (default — merges with the item's existing tags), "remove"
    (drops just the named tags, keeps the rest), or "replace" (item ends up
    with exactly these tags, nothing else). Tag names are matched
    case-insensitively against list_tags and auto-created if they don't exist
    yet. Everything else on the item (price, custom fields, photos, warranty)
    is preserved via a full-body PUT (the 0.26 PUT-clears gotcha)."""
    full = _resolve(identifier)
    if not full:
        return {"error": f"no item matched '{identifier}'"}
    if mode not in ("add", "remove", "replace"):
        return {"error": f"mode must be add/remove/replace, got '{mode}'"}
    body = _preserve_item_body(full)
    current = {t["name"].lower(): t["id"] for t in (full.get("tags") or [])}
    if mode == "replace":
        new_ids = _ensure_tag_ids(tags)
    elif mode == "remove":
        drop = {t.lower() for t in tags}
        new_ids = [tid for name, tid in current.items() if name not in drop]
    else:  # add
        new_ids = list(current.values())
        for tid in _ensure_tag_ids(tags):
            if tid not in new_ids:
                new_ids.append(tid)
    body["tagIds"] = new_ids
    _put(f"/entities/{full['id']}", body)
    after = _get(f"/entities/{full['id']}")
    return {
        "ok": True,
        "assetId": after.get("assetId"),
        "name": after.get("name"),
        "tags": sorted(t.get("name") for t in after.get("tags") or []),
    }


def _tag_by_name(name: str) -> Optional[dict]:
    for t in (_get("/tags") or []):
        if t.get("name", "").lower() == name.lower():
            return t
    return None


@mcp.tool()
def set_tag(
    name: str,
    new_name: Optional[str] = None,
    description: Optional[str] = None,
    color: Optional[str] = None,
    icon: Optional[str] = None,
    parent: Optional[str] = None,
    clear_parent: bool = False,
) -> dict:
    """Create or edit a tag's own metadata — NOT what's tagged on an item (see
    set_tags for that). Matches `name` case-insensitively against list_tags;
    creates the tag if it doesn't exist yet. Only the args you pass are
    changed. `parent` nests this tag under another (existing) tag name, for
    grouping related tags in the Homebox UI (e.g. tier-bifl/tier-lifetime-
    warranty under a "tier" parent); pass `clear_parent=True` to un-nest it.
    Use list_tags(detail=True) to see current tag metadata first."""
    t = _tag_by_name(name)
    if not t:
        t = _post("/tags", {"name": name})
    body = {
        "id": t["id"],
        "name": t.get("name"),
        "description": t.get("description") or "",
        "color": t.get("color") or "",
        "icon": t.get("icon") or "",
        "parentId": t.get("parentId") or "00000000-0000-0000-0000-000000000000",
    }
    if new_name is not None:
        body["name"] = new_name
    if description is not None:
        body["description"] = description
    if color is not None:
        body["color"] = color
    if icon is not None:
        body["icon"] = icon
    if clear_parent:
        body["parentId"] = "00000000-0000-0000-0000-000000000000"
    elif parent is not None:
        p = _tag_by_name(parent)
        if not p:
            return {"error": f"no tag named '{parent}' (create it first, or omit parent)"}
        body["parentId"] = p["id"]
    _put(f"/tags/{t['id']}", body)
    after = _tag_by_name(body["name"])
    return {
        "ok": True,
        "name": after.get("name"),
        "description": after.get("description") or None,
        "color": after.get("color") or None,
        "icon": after.get("icon") or None,
    }


def _label_dir(out_dir: Optional[str]) -> Path:
    d = Path(out_dir).expanduser() if out_dir else (
        Path(__file__).resolve().parent.parent / "intake" / "labels"
    )
    d.mkdir(parents=True, exist_ok=True)
    return d


@mcp.tool()
def generate_label(
    identifier: str,
    kind: str = "location",
    out_dir: Optional[str] = None,
) -> dict:
    """Save a printable Homebox label PNG (QR + readable name) for a location or
    item, to stick on a tote so the QR-first capture loop can read it. `kind` is
    "location", "item", or "asset". For location/item, `identifier` is a name or
    slug (resolved to an id); for asset it's the assetId (e.g. 000-028). Saves to
    `out_dir` (default: intake/labels/). Returns the saved file path."""
    kind = kind.lower()
    if kind == "asset":
        ref = identifier
    elif kind == "location":
        ref = _location_id(identifier)
        if not ref:
            return {"error": f"no location named '{identifier}'"}
    else:
        e = _resolve(identifier)
        if not e:
            return {"error": f"no item found matching '{identifier}'"}
        ref, kind = e["id"], "item"
    r = _client.get(f"/labelmaker/{kind}/{ref}")
    r.raise_for_status()
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in identifier)[:48]
    path = _label_dir(out_dir) / f"label-{kind}-{safe}.png"
    path.write_bytes(r.content)
    return {"ok": True, "path": str(path), "kind": kind, "bytes": len(r.content)}


@mcp.tool()
def qrcode(data: str, out_dir: Optional[str] = None) -> dict:
    """Save a raw QR-code JPEG encoding arbitrary `data` (e.g. a deep link).
    For tote labels prefer generate_label (adds the readable name). Saves to
    `out_dir` (default: intake/labels/). Returns the saved file path."""
    r = _client.get("/qrcode", params={"data": data})
    r.raise_for_status()
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in data)[:48] or "qr"
    path = _label_dir(out_dir) / f"qr-{safe}.jpg"
    path.write_bytes(r.content)
    return {"ok": True, "path": str(path), "bytes": len(r.content)}


@mcp.tool()
def existing_item_ids() -> dict:
    """Return {item_id: assetId} for every entity that has an item_id custom
    field — a one-pass index for DEDUPE before a bulk import (q does not index
    custom fields, so this scans). assetId may be empty for un-asset'd rows."""
    out: dict[str, str] = {}
    for e in _search_entities():
        full = _get(f"/entities/{e['id']}")
        iid = _field(full, "item_id")
        if iid:
            out[iid] = full.get("assetId") or ""
    return out


@mcp.tool()
def set_primary_photos() -> dict:
    """Ensure every entity with photos has a primary image set (finalize step
    after a bulk photo attach). Thin wrapper over POST /actions/set-primary-photos."""
    return {"ok": True, "result": _post("/actions/set-primary-photos", {})}


@mcp.tool()
def create_thumbnails() -> dict:
    """Generate any missing photo thumbnails (finalize step after a bulk photo
    attach). Thin wrapper over POST /actions/create-missing-thumbnails."""
    return {"ok": True, "result": _post("/actions/create-missing-thumbnails", {})}


@mcp.tool()
def barcode_lookup(code: str) -> dict:
    """Look up a UPC/EAN barcode → name/manufacturer/model (optional path for
    boxed goods). Thin wrapper over GET /products/search-from-barcode."""
    try:
        return {"ok": True, "result": _get("/products/search-from-barcode", data=code)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"barcode lookup failed: {exc}"}


if __name__ == "__main__":
    mcp.run()
