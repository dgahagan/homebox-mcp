# homebox-mcp

A thin **MCP server** over the [Homebox](https://homebox.software) REST API
(v0.26+) so an MCP client (e.g. Claude Code) can answer inventory questions and
do intake without hand-rolling `curl`.

**Requirements:** a running Homebox 0.26+ instance, an API token, and
[`uv`](https://docs.astral.sh/uv/) (which resolves the inline
[PEP 723](https://peps.python.org/pep-0723/) dependencies — `mcp`, `httpx`,
`pillow-heif` — into an ephemeral venv on first run; no manual virtualenv).

## Tools

| Tool | Purpose |
|------|---------|
| `search_items(query, limit)` | Keyword search → items with assetId, item_id, location |
| `get_item(identifier)` | Full detail by **assetId** (`000-028`), the **alias custom field** (`$HOMEBOX_ALIAS_FIELD`), or name |
| `list_locations()` | The full location tree as an indented outline |
| `location_contents(location, recursive=False)` | What's in a location/tote (items + sub-locations); `recursive=True` walks the whole subtree and returns every nested item with its full location path — use for locations like Garage/Basement that have sub-locations |
| `list_tags(detail=False)` | All tag (label) names; `detail=True` returns full objects (description, color, icon, parent tag) |
| `warranties_expiring(before)` | Items whose warranty expires on/before an ISO date |
| `create_item(...)` | Create + enrich an item in one call (intake); `fields` dict sets custom fields, typed by JSON value type |
| `set_warranty(identifier, expires, lifetime, details)` | Set warranty fields on an existing item (full-body PUT, no wipe) |
| `set_fields(identifier, fields)` | Create/update custom fields on an existing item — values typed by JSON type: text / number (int-coerced) / boolean (full-body PUT, no wipe) |
| `set_identity(identifier, manufacturer, model_number, serial_number)` | Set manufacturer/model/serial on an existing item (full-body PUT, no wipe) |
| `set_tags(identifier, tags, mode="add")` | Add/remove/replace tags **on an item**; auto-creates unknown tag names (full-body PUT, no wipe) |
| `set_tag(name, new_name, description, color, icon, parent, clear_parent)` | Create/edit **a tag's own** metadata (rename, description, color, icon, parent tag for grouping) — not what's tagged on an item, see `set_tags` |
| `attach_document(identifier, source, title, doc_type, primary)` | Attach a file **path or URL** to an item, or a location by name (fallback) |
| `attach_location_photo(location, source, title, primary)` | Attach a photo to a **location** (shelf/tote) as its primary image |

### Bulk intake (photo pipeline)

| Tool | Purpose |
|------|---------|
| `import_csv(csv_text)` | Bulk-create items + locations from a Homebox CSV (one multipart request). `HB.location` path **auto-creates** the hierarchy; `HB.tags` (existing, **`;`-separated** — a comma list in a quoted field becomes one junk tag), `HB.field.<custom>` (`item_id`, `category`) |
| `create_location(name, parent, description)` | Create a tote/bin/shelf **location** (QR-first bootstrap); `description` = contents manifest |
| `set_location_manifest(location, description)` | Set a location's contents manifest (echoes name/parent/assetId — no PUT-wipe) |
| `set_location(location, new_name, parent, clear_parent, description, notes, tags, tags_mode, entity_type, asset_id, fields)` | General-purpose location editor — name, parent (or move to root), tags, entity type, assetId, notes, custom fields (full-body PUT, no wipe) |
| `generate_label(identifier, kind, out_dir)` | Save a printable label **PNG** (QR + name) for a location/item/asset → `intake/labels/` |
| `qrcode(data, out_dir)` | Save a raw QR **JPEG** for arbitrary data |
| `field_index(field_name?)` | `{field_value: assetId}` index over any custom field (default: `$HOMEBOX_ALIAS_FIELD`) for **dedupe** before import (q doesn't index custom fields) |
| `set_primary_photos()` / `create_thumbnails()` | Finalize after a bulk photo attach |
| `barcode_lookup(code)` | UPC/EAN → name/manufacturer/model (optional, for boxed goods) |

> **Companion script:** `annotate_location_photos.py` turns one wide photo of a
> shelf bank into one boxed/labeled image per shelf (`--rows N`), and can upload
> each straight to its Homebox location with `--upload`. See its `--help`.
>
> **Companion script:** `heic2jpg.py` converts HEIC/HEIF (iPhone photos) to JPEG
> for the bulk-intake vision step and ad-hoc use (`./heic2jpg.py FILE...`). Note
> that `attach_document`/`attach_location_photo` **already auto-convert HEIC to
> JPEG on upload** (so Homebox always stores a browser-renderable image) — the
> script is only needed to feed HEIC frames to vision *before* upload. Both rely
> on `pillow-heif`, which bundles libheif, so there's **no system package to
> install** (no `dnf`/distrobox state to lose on rebuild).

## Setup

1. **Credentials** — copy the template and fill in your API key:
   ```bash
   cp .env.example .env       # .env is gitignored
   chmod 600 .env
   # edit .env: HOMEBOX_URL + HOMEBOX_TOKEN
   ```
2. **Run** — register with your MCP client using the **absolute path** to
   `server.py` in your clone. For Claude Code, `--scope user` makes it available
   from any project:
   ```bash
   claude mcp add homebox --scope user -- \
     "$(command -v uv)" run --script \
     /path/to/homebox-mcp/server.py
   claude mcp get homebox      # should show: Status ✔ Connected
   ```
   The client launches it via `uv run --script server.py`. The server reads
   config from the environment, falling back to the sibling `.env` (resolved
   relative to the script, so the absolute-path invocation still finds it). The
   credential never enters the MCP config — it stays in the gitignored `.env`.

## How it works (and Homebox 0.26 gotchas)

- **Items and locations are unified "entities"** (`/v1/entities`); a location is
  an entity whose entity-type has `isLocation=true`. A new item with no
  entityTypeId lazily gets a default `Item` type.
- **List/search responses are lightweight summaries** — they omit `fields` and
  have an empty `assetId`. The server fetches full detail (`GET /entities/{id}`)
  before returning assetId/item_id, so `search_items`/`get_item` are accurate.
- **`q` does not index custom fields.** Resolving by the `item_id` slug uses a
  scan fallback (one detail fetch per item). Resolving by **assetId** (via
  `/v1/assets/{id}`) or **name** is cheaper — prefer assetId when you have it
  (e.g. the value `create_item` returns).
- **PUT clears omitted fields**, including `assetId`. The server's `create_item`
  assigns the asset ID *after* enrichment so it survives; if you ever PUT an
  entity by hand, echo back `assetId` (and run `POST /actions/ensure-asset-ids`).
- **Attachment titles are basenamed on `/`.** Homebox treats an attachment's
  `name` as path-like, so "front 3/4 view" silently stores as "4 view". The
  server sanitizes `/`→`-` in attach titles (`_attachment_title`).
- **Number custom fields are integer-typed.** A float `numberValue` (e.g.
  `17000.0`, which FastMCP produces from a `float`-typed arg like `resale_value`)
  makes the API **500 "Unknown Error"**. `_make_field` coerces number values to
  `int`; pass whole numbers for `resale_value`.
- **Custom-field values are type-keyed** (`textValue` / `numberValue` /
  `booleanValue`). `_preserve_item_body` rebuilds each field by its declared
  type — coercing everything to `textValue` (an earlier bug) silently wiped
  numeric/boolean fields on any PUT (e.g. `set_warranty` on a row with `resale_value`).

## Security

`.env` holds the API key and is gitignored (mode 600). Never commit it. Rotating
the Homebox `HBOX_AUTH_API_KEY_PEPPER` invalidates the key — mint a new one and
update `.env`.

## License

[MIT](LICENSE) © Dan Gahagan
