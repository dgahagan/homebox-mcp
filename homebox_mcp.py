#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.10", "httpx>=0.27", "pillow>=10", "pillow-heif>=0.16"]
# ///
"""Homebox MCP server.

A thin wrapper over the Homebox REST API (v0.26+) so Claude can answer
inventory questions ("where is my X", "what's in Tote B-3", "which warranties
expire this year") and perform intake (create item + attach manual).

Config (resolved in order):
  1. environment variables (HOMEBOX_URL / HOMEBOX_TOKEN, plus optional
     HOMEBOX_ALIAS_FIELD / HOMEBOX_LABEL_DIR — see .env.example)
  2. a sibling `.env` file (KEY=VALUE lines) next to this script  [gitignored]

In Homebox >=0.26 items and locations are unified as "entities"; an entity is a
location when its entity-type has isLocation=true. Labels are "tags". This
server exposes items (non-location entities), locations, and tags.
"""
from __future__ import annotations

import datetime
import functools
import os
import sys
import mimetypes
from pathlib import Path
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

__version__ = "0.9.0"


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
# Optional: the name of one custom field treated as a stable item identifier —
# items can be looked up by it, and summaries surface it. Unset = items resolve
# by assetId/name only.
HOMEBOX_ALIAS_FIELD = os.environ.get("HOMEBOX_ALIAS_FIELD", "").strip()
# Optional: where generate_label / qrcode save output. Default: CWD.
HOMEBOX_LABEL_DIR = os.environ.get("HOMEBOX_LABEL_DIR", "").strip()

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

# Client hints: reads can run without prompting; deletes should prompt hard.
_READONLY = ToolAnnotations(readOnlyHint=True)
_IDEMPOTENT = ToolAnnotations(idempotentHint=True)
_DESTRUCTIVE = ToolAnnotations(destructiveHint=True)


# ---------------------------------------------------------------------------
# Version guard + error surfacing
# ---------------------------------------------------------------------------
_MIN_VERSION = (0, 26)
_version_checked = False
_version_error: Optional[str] = None


def _check_min_version() -> None:
    """One-time guard: this server needs the unified-entities API (Homebox
    >= 0.26, the sysadminsmedia fork). Checked lazily on the first tool call so
    the MCP handshake can't fail. Fails open if /status is unreachable or
    unparsable — the real call's own error is more informative than a guess."""
    global _version_checked, _version_error
    if not _version_checked:
        _version_checked = True
        try:
            build = (_get("/status") or {}).get("build") or {}
            ver = (build.get("version") or "").lstrip("v")
            parts = tuple(int(p) for p in ver.split(".")[:2])
            if len(parts) == 2 and parts < _MIN_VERSION:
                _version_error = (
                    f"this server requires Homebox >= 0.26 (the sysadminsmedia "
                    f"fork at homebox.software); your instance reports v{ver}, "
                    f"whose pre-0.26 API (/items, /locations, /labels) is "
                    f"incompatible."
                )
        except Exception:
            pass
    if _version_error:
        raise ToolError(_version_error)


def _tool_errors(fn):
    """Turn transport/API failures into legible tool errors (status + Homebox's
    response body, which says *why*) instead of raw tracebacks, and run the
    one-time version guard. Domain errors (not found, ambiguous, bad args) stay
    as {"error": ...} returns from the tools themselves."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        _check_min_version()
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                detail = (e.response.text or "").strip()[:500]
            except Exception:
                pass
            raise ToolError(
                f"Homebox API error {e.response.status_code} on "
                f"{e.request.method} {e.request.url.path}"
                + (f": {detail}" if detail else "")
            ) from e
        except httpx.RequestError as e:
            raise ToolError(
                f"cannot reach Homebox at {HOMEBOX_URL}: "
                f"{e.__class__.__name__}: {e}"
            ) from e
    return wrapper


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


def _patch(path: str, body: dict) -> Any:
    """Partial update. PATCH /entities/{id} accepts parentId / quantity /
    tagIds / entityTypeId and — unlike PUT — leaves every omitted field alone,
    so no preserve-body dance is needed for those keys."""
    r = _client.patch(path, json=body)
    r.raise_for_status()
    return r.json() if r.content else None


def _delete_path(path: str) -> None:
    r = _client.delete(path)
    r.raise_for_status()


def _rfc3339(d: str) -> str:
    """Normalize a YYYY-MM-DD date (or full timestamp) to an RFC3339 string."""
    d = d.strip()
    if "T" in d:
        return d
    return f"{d}T00:00:00Z"


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


def _field_type(value: Any) -> str:
    """Pick a Homebox custom-field type from a value's JSON type: bool →
    boolean, int/float → number, everything else → text. (bool first — it's an
    int subclass.)"""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "text"


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
        "quantity": entity.get("quantity"),
    }
    if HOMEBOX_ALIAS_FIELD:
        out[HOMEBOX_ALIAS_FIELD] = _field(entity, HOMEBOX_ALIAS_FIELD)
    parent = entity.get("parent") or {}
    out["location"] = parent.get("name")
    if with_path and entity.get("id"):
        out["location_path"] = _location_path(entity["id"])
    return out


def _search_all(
    q: Optional[str] = None,
    parent_ids: Optional[str] = None,
    tags: Optional[list[str]] = None,
    page_size: int = 200,
) -> list[dict]:
    """Every matching entity, following pagination until the reported `total`
    is reached (a single page silently truncates inventories larger than the
    page size). `tags` is a list of tag IDS (repeated query params). Results
    are lightweight summaries — no `fields`, empty `assetId`; fetch
    GET /entities/{id} for full detail.

    NOTE: /entities returns NON-LOCATION entities only (items) — location
    children never appear here, even with parentIds. Locations come from
    /entities/tree (see _walk_tree/_find_location)."""
    out: list[dict] = []
    page = 1
    while True:
        data = _get("/entities", q=q, parentIds=parent_ids, tags=tags,
                    page=page, pageSize=page_size)
        if isinstance(data, dict):
            batch = data.get("items") or data.get("entities") or []
            total = data.get("total")
        else:
            batch, total = (data or []), None
        out.extend(batch)
        if not batch or total is None or len(out) >= int(total):
            break
        page += 1
    return out


def _asset_lookup(ident: str) -> Optional[dict]:
    """Full entity detail by assetId (e.g. '000-028'), or None."""
    try:
        ares = _get(f"/assets/{ident}")
        items = ares.get("items") if isinstance(ares, dict) else None
        if items:
            return _get(f"/entities/{items[0]['id']}")
    except Exception:
        pass
    return None


def _exact_matches(ident: str) -> list[dict]:
    """All entities whose alias field or name equals `ident` (case-insensitive).

    Keyword search first (cheap; matches name/description), then — only if
    nothing matched and an alias field is configured — a full scan, since `q`
    does not index custom fields. List responses are lightweight summaries, so
    full detail is fetched before matching.
    """
    def hit(full: dict) -> bool:
        if HOMEBOX_ALIAS_FIELD and (
                _field(full, HOMEBOX_ALIAS_FIELD) or "").lower() == ident.lower():
            return True
        return (full.get("name") or "").lower() == ident.lower()

    candidates = [_get(f"/entities/{e['id']}") for e in _search_all(q=ident)]
    matches = {full["id"]: full for full in candidates if hit(full)}
    if not matches and HOMEBOX_ALIAS_FIELD:
        for e in _search_all():
            if _is_location(e):
                continue
            full = _get(f"/entities/{e['id']}")
            if hit(full):
                matches[full["id"]] = full
    return list(matches.values())


def _resolve_exact(identifier: str) -> tuple[Optional[dict], Optional[str]]:
    """Resolve an item for a WRITE: assetId, alias field, or exact name only —
    never a fuzzy keyword fallback (a typo must not mutate the wrong item).

    Returns (entity, error). Multiple exact matches are an error listing each
    candidate's assetId.
    """
    ident = identifier.strip()
    full = _asset_lookup(ident)
    if full:
        return full, None
    matches = _exact_matches(ident)
    if not matches:
        alias = f"{HOMEBOX_ALIAS_FIELD}, " if HOMEBOX_ALIAS_FIELD else ""
        return None, (f"no item exactly matched '{identifier}' by assetId, "
                      f"{alias}or name — writes require an exact identifier "
                      f"(use search_items/get_item to find it first)")
    if len(matches) > 1:
        cands = "; ".join(f"{m.get('name')} (assetId {m.get('assetId') or '?'})"
                          for m in matches)
        return None, (f"'{identifier}' is ambiguous — {len(matches)} items "
                      f"match: {cands}. Use the assetId.")
    return matches[0], None


def _resolve_fuzzy(identifier: str) -> Optional[dict]:
    """Find one entity for a READ: assetId, alias field, exact name — falling
    back to the first keyword-search hit that is an item."""
    ident = identifier.strip()
    full = _asset_lookup(ident)
    if full:
        return full
    matches = _exact_matches(ident)
    if matches:
        return matches[0]
    for e in _search_all(q=ident):
        if not _is_location(e):
            return _get(f"/entities/{e['id']}")
    return None


# ---------------------------------------------------------------------------
# Tools — read
# ---------------------------------------------------------------------------
@mcp.tool(annotations=_READONLY)
@_tool_errors
def search_items(
    query: Optional[str] = None,
    tags: Optional[list[str]] = None,
    limit: int = 20,
) -> list[dict]:
    """Search inventory items by name/keyword and/or by tag names (AND of
    both when both given — e.g. everything tagged "power-tool").

    Returns items (not locations) with their immediate location, assetId, and
    the alias custom field (if $HOMEBOX_ALIAS_FIELD is configured). Use
    get_item for full detail on one result.
    """
    tag_ids = None
    if tags:
        known = {t["name"].lower(): t["id"] for t in (_get("/tags") or [])}
        missing = [t for t in tags if t.lower() not in known]
        if missing:
            raise ToolError(f"unknown tag(s) {missing} — see list_tags")
        tag_ids = [known[t.lower()] for t in tags]
    if not query and not tag_ids:
        raise ToolError("pass query and/or tags")
    results = [e for e in _search_all(q=query, tags=tag_ids)
               if not _is_location(e)]
    # list responses omit assetId/fields, so fetch full detail for the page
    return [_summarize(_get(f"/entities/{e['id']}")) for e in results[:limit]]


@mcp.tool(annotations=_READONLY)
@_tool_errors
def get_item(identifier: str) -> dict:
    """Get full detail for one item by assetId (e.g. 000-028), alias custom
    field, or name (fuzzy fallback: first keyword match). Includes location
    path, serial, model, purchase, warranty, custom fields, tags, and
    attachments."""
    e = _resolve_fuzzy(identifier)
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


@mcp.tool(annotations=_READONLY)
@_tool_errors
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


@mcp.tool(annotations=_READONLY)
@_tool_errors
def location_contents(location: str, recursive: bool = False) -> dict:
    """List what is in a location (e.g. a tote or shelf), by location name.

    Returns the items directly in that location plus any sub-locations (their
    names only — not their contents). Set `recursive=True` to instead walk the
    whole subtree and return every item nested under it, each tagged with its
    full location path; use this when a location (e.g. Garage, Basement) has
    sub-locations you also want the contents of, to avoid querying each one by
    hand. `location` is a name or /-separated path (for duplicate names).
    """
    target, err = _find_location(location)
    if err:
        return {"error": err}
    node = target["node"]

    if not recursive:
        return {
            "location": location,
            "items": [_summarize(_get(f"/entities/{e['id']}"))
                      for e in _search_all(parent_ids=target["id"])],
            # /entities never returns locations — sub-locations come from the tree
            "sub_locations": sorted(c.get("name") or ""
                                    for c in node.get("children") or []),
        }

    results: list[dict] = []

    def walk_items(parent_id: str, path: list[str]) -> None:
        for e in _search_all(parent_ids=parent_id):
            full = _get(f"/entities/{e['id']}")
            summary = _summarize(full)
            summary["location"] = " → ".join(path)
            results.append(summary)
            # 0.26 unified entities: items can contain items (a camera bag
            # holding lenses), so recurse into items too
            walk_items(e["id"], path + [e.get("name") or ""])

    def walk_loc(n: dict, path: list[str]) -> None:
        walk_items(n["id"], path)
        for c in n.get("children") or []:
            walk_loc(c, path + [c.get("name") or ""])

    walk_loc(node, [location])
    return {"location": location, "items": results, "recursive": True}


@mcp.tool(annotations=_READONLY)
@_tool_errors
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


@mcp.tool(annotations=_READONLY)
@_tool_errors
def warranties_expiring(
    before: Optional[str] = None,
    after: Optional[str] = None,
    lifetime: bool = False,
) -> list[dict]:
    """List items whose warranty expires in a date window, or items with a
    lifetime warranty.

    `before`/`after` are ISO dates (YYYY-MM-DD): returns items with
    after <= warrantyExpires <= before. `after` defaults to TODAY, so
    already-expired warranties are excluded unless you pass an earlier
    `after`. Set `lifetime=True` to instead list items flagged as lifetime
    warranty (`before`/`after` ignored). Useful for "which warranties expire
    this year".
    """
    if not lifetime and not before:
        raise ToolError("pass `before` (YYYY-MM-DD), or lifetime=True")
    lo = (after or datetime.date.today().isoformat())[:10]
    out = []
    for e in _search_all():
        if _is_location(e):
            continue
        full = _get(f"/entities/{e['id']}")
        if lifetime:
            if full.get("lifetimeWarranty"):
                out.append({**_summarize(full), "lifetimeWarranty": True})
            continue
        w = (full.get("warrantyExpires") or "")[:10]
        if w and lo <= w <= before:
            out.append({**_summarize(full), "warrantyExpires": w})
    return sorted(out, key=lambda x: x.get("warrantyExpires") or x.get("name") or "")


# ---------------------------------------------------------------------------
# Tools — write (intake)
# ---------------------------------------------------------------------------
def _walk_tree() -> list[tuple[dict, list[str]]]:
    """Flatten /entities/tree (locations only) into (node, path-segments) pairs."""
    tree = _get("/entities/tree") or []
    out: list[tuple[dict, list[str]]] = []

    def walk(nodes, path):
        for n in nodes or []:
            p = path + [n.get("name", "")]
            out.append((n, p))
            walk(n.get("children"), p)

    walk(tree, [])
    return out


def _find_location(ref: str) -> tuple[Optional[dict], Optional[str]]:
    """Resolve a location by name or /-separated path (e.g. 'Garage/Shelf 1').

    Returns (match, error) — match is {"id", "node", "path"}. Matching is
    case-insensitive; a path matches on its trailing segments, so
    'Garage/Shelf 1' finds 'House/Garage/Shelf 1'. A bare name that matches
    multiple locations is an error listing each full path (instead of silently
    picking the first) — disambiguate by passing a path.
    """
    want = [s.strip().lower() for s in ref.split("/") if s.strip()]
    if not want:
        return None, "empty location name"
    hits = []
    for node, path in _walk_tree():
        if [s.lower() for s in path[-len(want):]] == want:
            hits.append((node, path))
    if not hits:
        return None, f"no location named '{ref}' (see list_locations)"
    if len(hits) > 1:
        paths = "; ".join("/".join(p) for _, p in hits)
        return None, (f"'{ref}' is ambiguous — {len(hits)} locations match: "
                      f"{paths}. Pass a path (e.g. 'Parent/{ref}').")
    node, path = hits[0]
    return {"id": node.get("id"), "node": node, "path": "/".join(path)}, None


@mcp.tool()
@_tool_errors
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
    fields: Optional[dict] = None,
    tags: Optional[list[str]] = None,
) -> dict:
    """Create an inventory item and enrich it in one call.

    `location` is a location name or /-separated path (must exist; use
    list_locations). `tags` are tag names (must already exist; use list_tags).
    `fields` maps custom-field name -> value; each value's JSON type picks the
    field type (string -> text, number -> number [integer-coerced],
    true/false -> boolean). Returns the new assetId and id.
    """
    parent_id = None
    if location:
        m, err = _find_location(location)
        if err:
            return {"error": err}
        parent_id = m["id"]

    created = _post("/entities", {"name": name, **({"parentId": parent_id} if parent_id else {})})
    iid = created["id"]

    tag_ids = []
    if tags:
        all_tags = {t["name"].lower(): t["id"] for t in (_get("/tags") or [])}
        for t in tags:
            tid = all_tags.get(t.lower())
            if tid:
                tag_ids.append(tid)

    field_entries = [_make_field(k, v, _field_type(v))
                     for k, v in (fields or {}).items() if v is not None]

    body: dict = {"id": iid, "name": name, "quantity": quantity}
    if parent_id:
        body["parentId"] = parent_id
    for k, v in (
        ("manufacturer", manufacturer), ("modelNumber", model),
        ("serialNumber", serial),
        ("purchaseDate", _rfc3339(purchase_date) if purchase_date else None),
        ("purchaseFrom", purchase_from),
        ("warrantyExpires", _rfc3339(warranty_expires) if warranty_expires else None),
        ("notes", notes),
    ):
        if v is not None:
            body[k] = v
    if purchase_price is not None:
        body["purchasePrice"] = purchase_price
    if tag_ids:
        body["tagIds"] = tag_ids
    if field_entries:
        body["fields"] = field_entries

    _put(f"/entities/{iid}", body)
    # assign asset id
    try:
        _post("/actions/ensure-asset-ids", {})
    except Exception:
        pass
    full = _get(f"/entities/{iid}")
    return {"id": iid, "assetId": full.get("assetId"), "name": name,
            "location": location,
            "fields": {f.get("name"): _field_value(f)
                       for f in full.get("fields") or []}}


@mcp.tool()
@_tool_errors
def attach_document(
    identifier: str,
    source: str,
    title: str,
    doc_type: str = "manual",
    primary: bool = False,
) -> dict:
    """Attach a document to an item OR a location. `source` is a local file path
    or an http(s) URL (downloaded then uploaded). `doc_type` is e.g. manual,
    attachment, warranty, receipt, photo. `identifier` is assetId/alias/name
    for an item, or a location name (tried as a fallback if no item matches —
    for a location photo specifically, prefer attach_location_photo, which
    defaults primary=True for wayfinding shots). Set `primary` to make a photo
    the entity's primary image."""
    e, item_err = _resolve_exact(identifier)
    if e:
        iid, name = e["id"], e.get("name")
    else:
        if item_err and "ambiguous" in item_err:
            return {"error": item_err}
        loc, _ = _find_location(identifier)
        if not loc:
            return {"error": f"no item or location exactly matched '{identifier}'"}
        iid, name = loc["id"], identifier

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
@_tool_errors
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
    m, err = _find_location(location)
    if err:
        return {"error": err}
    loc_id = m["id"]

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


def _resolve_any(identifier: str) -> Optional[dict]:
    """Item (fuzzy) or location by name/path — full entity detail, or None."""
    e = _resolve_fuzzy(identifier)
    if e:
        return e
    loc, _ = _find_location(identifier)
    return _get(f"/entities/{loc['id']}") if loc else None


@mcp.tool(annotations=_READONLY)
@_tool_errors
def list_attachments(identifier: str) -> list[dict]:
    """List an item's (or location's) attachments with their ids — the handle
    needed by get_attachment / rename_attachment / delete_attachment.
    `identifier` is assetId / alias field / name, or a location name/path."""
    e = _resolve_any(identifier)
    if not e:
        raise ToolError(f"no item or location found matching '{identifier}'")
    return [{
        "id": a.get("id"),
        "type": a.get("type"),
        "title": a.get("title") or (a.get("document") or {}).get("title"),
        "primary": a.get("primary"),
        "mimeType": a.get("mimeType"),
        "createdAt": a.get("createdAt"),
    } for a in e.get("attachments") or []]


def _find_attachment(entity: dict, attachment_id: str) -> Optional[dict]:
    for a in entity.get("attachments") or []:
        if a.get("id") == attachment_id:
            return a
    return None


@mcp.tool()
@_tool_errors
def get_attachment(identifier: str, attachment_id: str, save_to: str) -> dict:
    """Download one attachment (see list_attachments for ids) to a local path,
    so its content — e.g. an attached manual or receipt — can be read.
    `save_to` is a directory (keeps a name based on the title) or a full file
    path."""
    e = _resolve_any(identifier)
    if not e:
        raise ToolError(f"no item or location found matching '{identifier}'")
    a = _find_attachment(e, attachment_id)
    if not a:
        return {"error": f"no attachment with id '{attachment_id}' on "
                         f"'{e.get('name')}' (see list_attachments)"}
    r = _client.get(f"/entities/{e['id']}/attachments/{attachment_id}")
    r.raise_for_status()
    dest = Path(save_to).expanduser()
    if dest.is_dir():
        title = a.get("title") or (a.get("document") or {}).get("title") or attachment_id
        safe = "".join(c if c.isalnum() or c in "-_. " else "-" for c in title)
        ext = mimetypes.guess_extension(
            (r.headers.get("content-type") or "").split(";")[0]) or ""
        dest = dest / (safe if safe.lower().endswith(ext.lower()) or not ext
                       else safe + ext)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return {"ok": True, "path": str(dest), "bytes": len(r.content),
            "mimeType": (r.headers.get("content-type") or "").split(";")[0]}


@mcp.tool(annotations=_IDEMPOTENT)
@_tool_errors
def rename_attachment(
    identifier: str,
    attachment_id: str,
    title: Optional[str] = None,
    doc_type: Optional[str] = None,
    primary: Optional[bool] = None,
) -> dict:
    """Update an attachment's title, type (manual/attachment/warranty/receipt/
    photo), or primary flag (see list_attachments for ids). Only the args you
    pass change; the rest is re-sent as-is."""
    e = _resolve_any(identifier)
    if not e:
        raise ToolError(f"no item or location found matching '{identifier}'")
    a = _find_attachment(e, attachment_id)
    if not a:
        return {"error": f"no attachment with id '{attachment_id}' on "
                         f"'{e.get('name')}' (see list_attachments)"}
    body = {
        "title": _attachment_title(title) if title is not None
        else (a.get("title") or (a.get("document") or {}).get("title") or ""),
        "type": doc_type if doc_type is not None else a.get("type"),
        "primary": primary if primary is not None else bool(a.get("primary")),
    }
    _put(f"/entities/{e['id']}/attachments/{attachment_id}", body)
    return {"ok": True, "entity": e.get("name"), **body}


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
@_tool_errors
def import_csv(csv_text: str) -> dict:
    """Bulk-create items and locations from a Homebox CSV (multipart import).

    This is the bulk creator for the photo-intake pipeline — one request imports
    everything, avoiding ~4 API calls per item. Recognized columns:
      HB.name, HB.location (path e.g. "Garage/Loft" — AUTO-CREATES the hierarchy),
      HB.tags (must already exist; see list_tags), HB.quantity, HB.serial_number,
      HB.model_number, HB.manufacturer, HB.notes, HB.purchase_price,
      HB.purchase_from, HB.purchase_time, HB.warranty_expires,
      HB.field.<name> (custom fields).
    A location-only row (HB.location set, contents described elsewhere) creates
    just the location. After import, call set_primary_photos + create_thumbnails
    if you attached photos. Returns the count of data rows submitted."""
    rows = max(0, len([ln for ln in csv_text.splitlines() if ln.strip()]) - 1)
    files = {"csv": ("import.csv", csv_text.encode("utf-8"), "text/csv")}
    r = _client.post("/entities/import", files=files)
    r.raise_for_status()
    return {"ok": True, "status_code": r.status_code, "rows_submitted": rows}


@mcp.tool()
@_tool_errors
def create_location(
    name: str,
    parent: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Create a LOCATION entity (e.g. a new tote/bin/shelf) to bootstrap a
    new storage spot. `parent` is an existing location name or /-separated
    path (optional); `description` is optional free text, e.g. a contents
    summary (≤1000 chars). Returns the new location id. For deep paths,
    prefer import_csv's HB.location auto-create."""
    parent_match = None
    if parent:
        parent_match, err = _find_location(parent)
        if err:
            return {"error": err}
    # duplicate check is per-parent: the same name may exist elsewhere
    siblings = (parent_match["node"].get("children") if parent_match
                else _get("/entities/tree")) or []
    if any((s.get("name") or "").lower() == name.lower() for s in siblings):
        return {"error": f"a location named '{name}' already exists under "
                         f"'{parent_match['path'] if parent_match else '(root)'}'"}
    parent_id = parent_match["id"] if parent_match else None
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


@mcp.tool(annotations=_IDEMPOTENT)
@_tool_errors
def set_location_manifest(location: str, description: str) -> dict:
    """Set a location's `description` to a contents manifest (a "what lives in
    this bin" summary). `location` is a location name or /-separated path.
    Echoes back name/parent/assetId so the PUT does not wipe them (0.26
    PUT-clears gotcha)."""
    m, err = _find_location(location)
    if err:
        return {"error": err}
    lid = m["id"]
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


@mcp.tool(annotations=_IDEMPOTENT)
@_tool_errors
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
    auto-assigned assetId. `fields` is a dict of custom-field name -> value to
    upsert (create or overwrite each named field; the value's JSON type picks
    the field type)."""
    m, err = _find_location(location)
    if err:
        return {"error": err}
    loc_id = m["id"]
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
        pm, err = _find_location(parent)
        if err:
            return {"error": err}
        parent_id = pm["id"]
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
            _upsert_field(body, fname, fval, _field_type(fval))

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


@mcp.tool(annotations=_IDEMPOTENT)
@_tool_errors
def set_warranty(
    identifier: str,
    expires: Optional[str] = None,
    lifetime: Optional[bool] = None,
    details: Optional[str] = None,
) -> dict:
    """Set warranty info on an existing item (assetId / alias field / exact name).

    `expires` is a YYYY-MM-DD date (warranty end), `lifetime` flags a lifetime
    warranty, `details` is a short terms summary (e.g. "limited lifetime,
    test/measurement instruments excluded"). Only the args you pass are
    changed; everything else on the item (price, tags, custom fields, photos)
    is preserved via a full-body PUT (the 0.26 PUT-clears gotcha)."""
    full, err = _resolve_exact(identifier)
    if err:
        return {"error": err}
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


@mcp.tool(annotations=_IDEMPOTENT)
@_tool_errors
def set_fields(identifier: str, fields: dict) -> dict:
    """Create or update custom fields on an existing item (assetId / alias
    field / exact name).

    `fields` maps custom-field name -> value; each value's JSON type picks the
    field type (string -> text, number -> number [integer-coerced — the API
    500s on float number values], true/false -> boolean). Upsert semantics:
    named fields are created or overwritten, others untouched. Everything else
    on the item is preserved via a full-body PUT (the 0.26 PUT-clears gotcha)."""
    full, err = _resolve_exact(identifier)
    if err:
        return {"error": err}
    body = _preserve_item_body(full)
    for fname, fval in (fields or {}).items():
        if fval is not None:
            _upsert_field(body, fname, fval, _field_type(fval))
    _put(f"/entities/{full['id']}", body)
    after = _get(f"/entities/{full['id']}")
    return {
        "ok": True,
        "assetId": after.get("assetId"),
        "name": after.get("name"),
        "fields": {f.get("name"): _field_value(f) for f in after.get("fields") or []},
    }


@mcp.tool(annotations=_IDEMPOTENT)
@_tool_errors
def set_identity(
    identifier: str,
    manufacturer: Optional[str] = None,
    model_number: Optional[str] = None,
    serial_number: Optional[str] = None,
) -> dict:
    """Set manufacturer/model/serial on an existing item (assetId / alias
    field / exact name).

    Only the args you pass are changed; everything else on the item (price,
    tags, custom fields, photos, warranty) is preserved via a full-body PUT
    (the 0.26 PUT-clears gotcha). Use when a nameplate/label photo reveals a
    serial number or a model-number correction after the item was created."""
    full, err = _resolve_exact(identifier)
    if err:
        return {"error": err}
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


@mcp.tool(annotations=_IDEMPOTENT)
@_tool_errors
def move_item(identifier: str, location: str) -> dict:
    """Move an item to another location (assetId / alias field / exact name).

    `location` is a location name or /-separated path (see list_locations).
    Uses a partial PATCH, so nothing else on the item changes."""
    full, err = _resolve_exact(identifier)
    if err:
        return {"error": err}
    m, lerr = _find_location(location)
    if lerr:
        return {"error": lerr}
    _patch(f"/entities/{full['id']}", {"id": full["id"], "parentId": m["id"]})
    return {"ok": True, "assetId": full.get("assetId"),
            "name": full.get("name"),
            "location_path": _location_path(full["id"]) or m["path"]}


@mcp.tool(annotations=_IDEMPOTENT)
@_tool_errors
def set_item(
    identifier: str,
    new_name: Optional[str] = None,
    description: Optional[str] = None,
    notes: Optional[str] = None,
    quantity: Optional[int] = None,
    purchase_price: Optional[float] = None,
    purchase_date: Optional[str] = None,
    purchase_from: Optional[str] = None,
    insured: Optional[bool] = None,
    archived: Optional[bool] = None,
    fields: Optional[dict] = None,
) -> dict:
    """General item editor (assetId / alias field / exact name): rename, edit
    description/notes/quantity, purchase info, insured/archived flags, and
    custom fields in one call.

    Only the args you pass are changed. Quantity-only changes go via a partial
    PATCH; anything else uses a full-body PUT that preserves the rest of the
    item (the 0.26 PUT-clears gotcha). `fields` maps custom-field name ->
    value, typed by JSON type (see set_fields). To move an item use move_item;
    for warranty/identity/tags see set_warranty/set_identity/set_tags."""
    full, err = _resolve_exact(identifier)
    if err:
        return {"error": err}
    iid = full["id"]

    others = [new_name, description, notes, purchase_price, purchase_date,
              purchase_from, insured, archived, fields]
    if quantity is not None and all(v is None for v in others):
        _patch(f"/entities/{iid}", {"id": iid, "quantity": quantity})
    else:
        body = _preserve_item_body(full)
        if new_name is not None:
            body["name"] = new_name
        if description is not None:
            body["description"] = description
        if notes is not None:
            body["notes"] = notes
        if quantity is not None:
            body["quantity"] = quantity
        if purchase_price is not None:
            body["purchasePrice"] = purchase_price
        if purchase_date is not None:
            body["purchaseDate"] = _rfc3339(purchase_date)
        if purchase_from is not None:
            body["purchaseFrom"] = purchase_from
        if insured is not None:
            body["insured"] = insured
        if archived is not None:
            body["archived"] = archived
        for fname, fval in (fields or {}).items():
            if fval is not None:
                _upsert_field(body, fname, fval, _field_type(fval))
        _put(f"/entities/{iid}", body)
    after = _get(f"/entities/{iid}")
    return {
        "ok": True,
        "assetId": after.get("assetId"),
        "name": after.get("name"),
        "description": after.get("description") or None,
        "notes": after.get("notes") or None,
        "quantity": after.get("quantity"),
        "purchasePrice": after.get("purchasePrice") or None,
        "purchaseDate": after.get("purchaseDate") or None,
        "purchaseFrom": after.get("purchaseFrom") or None,
        "insured": after.get("insured"),
        "archived": after.get("archived"),
        "fields": {f.get("name"): _field_value(f) for f in after.get("fields") or []},
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


@mcp.tool(annotations=_IDEMPOTENT)
@_tool_errors
def set_tags(identifier: str, tags: list[str], mode: str = "add") -> dict:
    """Add, remove, or replace tags on an existing item (assetId / alias
    field / exact name).

    `mode` is "add" (default — merges with the item's existing tags), "remove"
    (drops just the named tags, keeps the rest), or "replace" (item ends up
    with exactly these tags, nothing else). Tag names are matched
    case-insensitively against list_tags and auto-created if they don't exist
    yet. Everything else on the item is untouched (partial PATCH)."""
    full, err = _resolve_exact(identifier)
    if err:
        return {"error": err}
    if mode not in ("add", "remove", "replace"):
        return {"error": f"mode must be add/remove/replace, got '{mode}'"}
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
    _patch(f"/entities/{full['id']}", {"id": full["id"], "tagIds": new_ids})
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


@mcp.tool(annotations=_IDEMPOTENT)
@_tool_errors
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
    grouping related tags in the Homebox UI (e.g. several condition-* tags
    under a "condition" parent); pass `clear_parent=True` to un-nest it.
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


# ---------------------------------------------------------------------------
# Tools — delete (confirm-gated)
# ---------------------------------------------------------------------------
def _confirm_gate(kind: str, actual_name: str, confirm: str) -> Optional[dict]:
    """Deletes require `confirm` to equal the target's exact name (case-
    sensitive) so a resolver surprise can never delete the wrong thing."""
    if confirm != actual_name:
        return {"error": f"not deleted: confirm must equal the {kind}'s exact "
                         f"name '{actual_name}' (got '{confirm}')"}
    return None


@mcp.tool(annotations=_DESTRUCTIVE)
@_tool_errors
def delete_item(identifier: str, confirm: str) -> dict:
    """PERMANENTLY delete an item (assetId / alias field / exact name),
    including its attachments. `confirm` must equal the item's exact name.
    There is no undo."""
    full, err = _resolve_exact(identifier)
    if err:
        return {"error": err}
    if _is_location(full):
        return {"error": f"'{identifier}' is a location — use delete_location"}
    gate = _confirm_gate("item", full.get("name") or "", confirm)
    if gate:
        return gate
    _delete_path(f"/entities/{full['id']}")
    return {"ok": True, "deleted": full.get("name"),
            "assetId": full.get("assetId")}


@mcp.tool(annotations=_DESTRUCTIVE)
@_tool_errors
def delete_location(
    location: str,
    confirm: str,
    confirm_nonempty: bool = False,
) -> dict:
    """PERMANENTLY delete a location (name or /-separated path). `confirm`
    must equal the location's exact name. A location that still contains items
    or sub-locations is refused unless `confirm_nonempty=True` — sub-locations
    are deleted with it, and its items are ORPHANED to the top level (not
    deleted). There is no undo."""
    m, err = _find_location(location)
    if err:
        return {"error": err}
    gate = _confirm_gate("location", (m["node"].get("name") or ""), confirm)
    if gate:
        return gate
    sublocs = m["node"].get("children") or []       # tree: /entities omits locations
    items = _search_all(parent_ids=m["id"])
    if (sublocs or items) and not confirm_nonempty:
        return {"error": f"not deleted: '{m['path']}' contains {len(items)} "
                         f"item(s), {len(sublocs)} sub-location(s). Move them "
                         f"out, or pass confirm_nonempty=True (sub-locations "
                         f"are deleted; items are orphaned to the top level)."}
    _delete_path(f"/entities/{m['id']}")
    return {"ok": True, "deleted": m["path"],
            "contained": {"items": len(items), "sub_locations": len(sublocs)}}


@mcp.tool(annotations=_DESTRUCTIVE)
@_tool_errors
def delete_tag(name: str, confirm: str) -> dict:
    """PERMANENTLY delete a tag itself (it is removed from every tagged item;
    the items survive). `confirm` must equal the tag's exact name."""
    t = _tag_by_name(name)
    if not t:
        return {"error": f"no tag named '{name}' (see list_tags)"}
    gate = _confirm_gate("tag", t.get("name") or "", confirm)
    if gate:
        return gate
    _delete_path(f"/tags/{t['id']}")
    return {"ok": True, "deleted": t.get("name")}


@mcp.tool(annotations=_DESTRUCTIVE)
@_tool_errors
def delete_attachment(identifier: str, attachment_id: str, confirm: str) -> dict:
    """PERMANENTLY delete one attachment from an item or location (see
    list_attachments for ids). `confirm` must equal the attachment's exact
    title."""
    e = _resolve_any(identifier)
    if not e:
        raise ToolError(f"no item or location found matching '{identifier}'")
    a = _find_attachment(e, attachment_id)
    if not a:
        return {"error": f"no attachment with id '{attachment_id}' on "
                         f"'{e.get('name')}' (see list_attachments)"}
    title = a.get("title") or (a.get("document") or {}).get("title") or ""
    gate = _confirm_gate("attachment", title, confirm)
    if gate:
        return gate
    _delete_path(f"/entities/{e['id']}/attachments/{attachment_id}")
    return {"ok": True, "deleted": title, "from": e.get("name")}


# ---------------------------------------------------------------------------
# Tools — labels
# ---------------------------------------------------------------------------
def _label_dir(out_dir: Optional[str]) -> Path:
    d = Path(out_dir).expanduser() if out_dir else (
        Path(HOMEBOX_LABEL_DIR).expanduser() if HOMEBOX_LABEL_DIR else Path.cwd()
    )
    d.mkdir(parents=True, exist_ok=True)
    return d


@mcp.tool()
@_tool_errors
def generate_label(
    identifier: str,
    kind: str = "location",
    out_dir: Optional[str] = None,
) -> dict:
    """Save a printable Homebox label PNG (QR + readable name) for a location
    or item, e.g. to stick on a tote/bin. `kind` is "location", "item", or
    "asset". For location/item, `identifier` is a name or slug (resolved to an
    id); for asset it's the assetId (e.g. 000-028). Saves to `out_dir`
    (default: $HOMEBOX_LABEL_DIR, else the current directory). Returns the
    saved file path."""
    kind = kind.lower()
    if kind == "asset":
        ref = identifier
    elif kind == "location":
        m, err = _find_location(identifier)
        if err:
            return {"error": err}
        ref = m["id"]
    else:
        e = _resolve_fuzzy(identifier)
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
@_tool_errors
def qrcode(data: str, out_dir: Optional[str] = None) -> dict:
    """Save a raw QR-code JPEG encoding arbitrary `data` (e.g. a deep link).
    For tote labels prefer generate_label (adds the readable name). Saves to
    `out_dir` (default: $HOMEBOX_LABEL_DIR, else the current directory).
    Returns the saved file path."""
    r = _client.get("/qrcode", params={"data": data})
    r.raise_for_status()
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in data)[:48] or "qr"
    path = _label_dir(out_dir) / f"qr-{safe}.jpg"
    path.write_bytes(r.content)
    return {"ok": True, "path": str(path), "bytes": len(r.content)}


@mcp.tool(annotations=_READONLY)
@_tool_errors
def field_index(field_name: Optional[str] = None) -> dict:
    """Return {field_value: assetId} for every entity that has the named
    custom field — a one-pass index for DEDUPE before a bulk import (q does
    not index custom fields, so this scans every entity). `field_name`
    defaults to $HOMEBOX_ALIAS_FIELD. assetId may be empty for un-asset'd
    rows."""
    fname = (field_name or HOMEBOX_ALIAS_FIELD or "").strip()
    if not fname:
        raise ToolError("pass field_name (no HOMEBOX_ALIAS_FIELD is configured)")
    out: dict[str, str] = {}
    for e in _search_all():
        full = _get(f"/entities/{e['id']}")
        val = next((_field_value(f) for f in full.get("fields") or []
                    if f.get("name") == fname), None)
        if val is not None and val != "":
            out[str(val)] = full.get("assetId") or ""
    return out


@mcp.tool(annotations=_IDEMPOTENT)
@_tool_errors
def set_primary_photos() -> dict:
    """Ensure every entity with photos has a primary image set (finalize step
    after a bulk photo attach). Thin wrapper over POST /actions/set-primary-photos."""
    return {"ok": True, "result": _post("/actions/set-primary-photos", {})}


@mcp.tool(annotations=_IDEMPOTENT)
@_tool_errors
def create_thumbnails() -> dict:
    """Generate any missing photo thumbnails (finalize step after a bulk photo
    attach). Thin wrapper over POST /actions/create-missing-thumbnails."""
    return {"ok": True, "result": _post("/actions/create-missing-thumbnails", {})}


@mcp.tool(annotations=_READONLY)
@_tool_errors
def barcode_lookup(code: str) -> dict:
    """Look up a UPC/EAN barcode → name/manufacturer/model (optional path for
    boxed goods). Thin wrapper over GET /products/search-from-barcode."""
    try:
        return {"ok": True, "result": _get("/products/search-from-barcode", data=code)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"barcode lookup failed: {exc}"}


def main() -> None:
    """Entry point for the `homebox-mcp` console script and direct runs."""
    mcp.run()


if __name__ == "__main__":
    main()
