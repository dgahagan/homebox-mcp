# homebox-mcp

<!-- mcp-name: io.github.dgahagan/homebox-mcp -->

[![PyPI version](https://img.shields.io/pypi/v/homebox-mcp)](https://pypi.org/project/homebox-mcp/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/dgahagan/homebox-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/dgahagan/homebox-mcp/actions/workflows/ci.yml)

An [MCP](https://modelcontextprotocol.io) server over the
[Homebox](https://homebox.software) REST API, so an MCP client like Claude can
work with your home inventory in plain language: *"where is my impact driver",*
*"what's in Tote B-3", "which warranties expire this year", "add this drill —
here's a photo of the receipt and the model number".* It answers questions,
performs intake (create an item, attach the manual/receipt, tag and file it),
manages attachments, and prints QR labels for totes and shelves — no
hand-rolled `curl`.

## Quickstart

You need a running Homebox instance (**0.26+**, see [Requirements](#requirements))
and an API key from **Profile → API Keys** in the Homebox web UI.

### Claude Code

```bash
claude mcp add homebox --scope user \
  -e HOMEBOX_URL=https://homebox.example.com \
  -e HOMEBOX_TOKEN=hb_xxxxxxxxxxxxxxxxxxxxxxxx \
  -- uvx homebox-mcp
claude mcp get homebox      # should show: Status ✔ Connected
```

`--scope user` makes it available from any project. `uvx` fetches and runs the
published package in an ephemeral environment — nothing to install first.

### Claude Desktop / generic MCP clients

Add to your client's MCP config (e.g. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "homebox": {
      "command": "uvx",
      "args": ["homebox-mcp"],
      "env": {
        "HOMEBOX_URL": "https://homebox.example.com",
        "HOMEBOX_TOKEN": "hb_xxxxxxxxxxxxxxxxxxxxxxxx"
      }
    }
  }
}
```

### From a clone (no PyPI)

The module carries [PEP 723](https://peps.python.org/pep-0723/) inline
dependencies, so it also runs standalone with `uv run --script` — `uv` resolves
`mcp`, `httpx`, `pillow`, and `pillow-heif` into an ephemeral venv on first run.
Instead of env vars, drop credentials in a `.env` file next to the module:

```bash
git clone https://github.com/dgahagan/homebox-mcp
cd homebox-mcp
cp .env.example .env        # .env is gitignored
chmod 600 .env
# edit .env: HOMEBOX_URL + HOMEBOX_TOKEN
```

Then register the **absolute path** to the module (or to `server.py`, a
compatibility shim kept for older registrations):

```bash
claude mcp add homebox --scope user -- \
  uv run --script /path/to/homebox-mcp/homebox_mcp.py
```

The server reads config from the environment, falling back to the sibling
`.env` (resolved relative to the script, so the absolute-path invocation still
finds it). The credential never enters the MCP config — it stays in the
gitignored `.env`.

## Requirements

- **Homebox 0.26 or newer** — the actively maintained
  [sysadminsmedia fork](https://github.com/sysadminsmedia/homebox) at
  [homebox.software](https://homebox.software). This server speaks the unified
  **entities** API introduced in 0.26. Older instances use a different API
  (`/items`, `/locations`, `/labels`) and are rejected on the first tool call
  with a clear error rather than failing cryptically.
- **An API key** — Homebox **Profile → API Keys** (the token starts with `hb_`).
- **[`uv`](https://docs.astral.sh/uv/)**, or any **Python ≥ 3.10** environment
  where you install `homebox-mcp` yourself.

## Configuration

All configuration is via environment variables (or the sibling `.env` for the
clone workflow):

| Variable | Required | Purpose |
|----------|----------|---------|
| `HOMEBOX_URL` | yes | Base URL of your Homebox instance (no trailing slash). |
| `HOMEBOX_TOKEN` | yes | Homebox API key (`hb_…`). |
| `HOMEBOX_ALIAS_FIELD` | no | Name of one custom field to treat as a stable item identifier — items can be resolved by it, summaries surface it, and `field_index` defaults to it. Unset = resolve by assetId/name only. See [Conventions](#conventions-optional). |
| `HOMEBOX_LABEL_DIR` | no | Where `generate_label` / `qrcode` save output. Default: current working directory. |

## Tools

~39 tools. Reads accept a **fuzzy** identifier (assetId, alias field, exact
name, then first keyword match). Write tools require an **exact** identifier
(assetId, alias field, or exact name) — a typo or an ambiguous match is refused
rather than mutating the wrong item. Locations are referenced by name or
`/`-separated path (e.g. `Garage/Shelf 1`) to disambiguate duplicate names.

### Read / Q&A

| Tool | Purpose |
|------|---------|
| `search_items(query?, tags?, limit=20)` | Search items by keyword and/or tag names (AND of both); returns each with assetId, location, and the alias field. |
| `get_item(identifier)` | Full detail for one item: location path, identity, purchase, warranty, custom fields, tags, attachments. |
| `list_locations()` | The full location tree as an indented outline. |
| `location_contents(location, recursive=False)` | Items directly in a location plus its sub-location names; `recursive=True` walks the whole subtree and returns every nested item with its full location path. |
| `list_tags(detail=False)` | All tag (label) names; `detail=True` returns full objects (description, color, icon, parent tag). |
| `warranties_expiring(before?, after?, lifetime=False)` | Items whose warranty expires in `after…before` (`after` defaults to today, excluding already-expired); `lifetime=True` lists lifetime-warranty items. |

### Create & intake

| Tool | Purpose |
|------|---------|
| `create_item(name, location?, quantity=1, manufacturer?, model?, serial?, purchase_price?, purchase_date?, purchase_from?, warranty_expires?, notes?, fields?, tags?)` | Create and enrich an item in one call. `fields` is a dict typed by JSON value (string→text, number→number [integer-coerced], bool→boolean); `tags` must already exist. Returns the new assetId. |
| `import_csv(csv_text)` | Bulk-create items and locations from a Homebox CSV in one multipart request. `HB.location` **auto-creates** the path hierarchy; recognizes `HB.name`, `HB.tags`, `HB.quantity`, `HB.serial_number`, `HB.model_number`, `HB.manufacturer`, `HB.notes`, `HB.purchase_*`, `HB.warranty_expires`, `HB.field.<name>`. |
| `create_location(name, parent?, description?)` | Create a location (tote/bin/shelf) to bootstrap a new storage spot; `description` doubles as a contents manifest. |
| `barcode_lookup(code)` | UPC/EAN → name/manufacturer/model (optional, for boxed goods). |
| `duplicate_item(identifier, copy_attachments=False, copy_custom_fields=True, copy_maintenance=False, prefix="Copy of ")` | Duplicate an item ("I bought a second one"). Copied custom fields include the alias field verbatim — give the copy its own value after. |

### Edit

All write tools resolve by exact identifier and preserve everything you don't
touch (a full-body PUT that echoes the rest of the item back — see
[gotchas](#how-it-works-and-homebox-026-gotchas)).

| Tool | Purpose |
|------|---------|
| `set_item(identifier, new_name?, description?, notes?, quantity?, purchase_price?, purchase_date?, purchase_from?, insured?, archived?, fields?)` | General item editor: rename, notes, quantity, purchase info, insured/archived flags, custom fields. Quantity-only edits use a partial PATCH. |
| `move_item(identifier, location)` | Move an item to another location (partial PATCH — nothing else changes). |
| `set_warranty(identifier, expires?, lifetime?, details?)` | Set warranty end date, lifetime flag, and terms summary. |
| `set_identity(identifier, manufacturer?, model_number?, serial_number?)` | Set manufacturer / model / serial (e.g. after a nameplate photo reveals them). |
| `set_fields(identifier, fields)` | Create or overwrite custom fields (upsert; typed by JSON value type). |
| `set_tags(identifier, tags, mode="add")` | Add / remove / replace tags on an item; unknown tag names are auto-created (partial PATCH). |
| `set_tag(name, new_name?, description?, color?, icon?, parent?, clear_parent=False)` | Edit **a tag's own** metadata (rename, color, icon, parent tag for grouping) — not what's tagged on an item. Creates the tag if new. |
| `set_location(location, new_name?, parent?, clear_parent?, description?, notes?, tags?, tags_mode?, entity_type?, asset_id?, fields?)` | General location editor: rename, move (or `clear_parent` to root), tags, notes, entity type, assetId, custom fields. |
| `mark_sold(identifier, sold_price?, sold_to?, sold_date?, sold_notes?, clear=False)` | Record a sale (price/buyer/date/notes) or `clear=True` to un-sell; pair with `set_item(archived=True)` to retire the item. |

### Maintenance log

| Tool | Purpose |
|------|---------|
| `log_maintenance(identifier, name, description?, completed_date?, scheduled_date?, cost?)` | Add an entry — "changed the mower oil today" (completed) or "sharpen blades in spring" (scheduled). |
| `list_maintenance(identifier?, status="both")` | Entries for one item, or across the whole inventory ("what maintenance is due?"); `status` = scheduled / completed / both. |
| `set_maintenance(entry_id, ...)` | Edit an entry — e.g. mark a scheduled one completed by setting `completed_date`. |
| `delete_maintenance(entry_id, confirm)` | Delete one entry (`confirm` = its exact name). |

### Reporting

| Tool | Purpose |
|------|---------|
| `inventory_stats(by="totals", start?, end?)` | Totals (counts, total value, warranty count), value by location or tag, or purchase-price over time — the cheap way to answer "what's my inventory worth?". |
| `export_csv(save_to?)` | Export the whole inventory as a Homebox CSV (complement of `import_csv`; quick backup). |
| `list_custom_fields(field?)` | Discover the custom-field schema in use: all field names, or every distinct value of one field. |

### Attachments

| Tool | Purpose |
|------|---------|
| `attach_document(identifier, source, title, doc_type="manual", primary=False)` | Attach a file (local **path or http(s) URL**) to an item, or to a location by name as a fallback. `doc_type` e.g. manual/receipt/warranty/photo; `primary=True` makes a photo the entity's primary image (e.g. a location's "this is the spot" wayfinding shot). |
| `list_attachments(identifier)` | List an item's or location's attachments **with their ids** (the handle the other attachment tools need). |
| `get_attachment(identifier, attachment_id, save_to)` | Download one attachment to a local path so its content (e.g. a manual) can be read. |
| `rename_attachment(identifier, attachment_id, title?, doc_type?, primary?)` | Update an attachment's title, type, or primary flag. |

> Uploads auto-convert HEIC/HEIF (iPhone photos) to JPEG so Homebox always
> stores a browser-renderable image, and sanitize `/` in titles (Homebox treats
> a title as a path and would otherwise truncate it).

### Deletes (confirm-gated)

Every delete requires `confirm` to equal the target's exact name and carries
the MCP `destructiveHint` annotation, so clients prompt before running them.

| Tool | Purpose |
|------|---------|
| `delete_item(identifier, confirm)` | Permanently delete an item and its attachments. No undo. |
| `delete_location(location, confirm, confirm_nonempty=False)` | Delete a location. A non-empty one is refused unless `confirm_nonempty=True`: **sub-locations cascade (are deleted); items are orphaned to the top level** (not deleted). |
| `delete_tag(name, confirm)` | Delete a tag itself (removed from every tagged item; the items survive). |
| `delete_attachment(identifier, attachment_id, confirm)` | Delete one attachment (`confirm` = its exact title). |

### Labels & finalize

| Tool | Purpose |
|------|---------|
| `generate_label(identifier, kind="location", out_dir?)` | Save a printable label **PNG** (QR + readable name) for a `location`, `item`, or `asset`. Saves to `$HOMEBOX_LABEL_DIR` (else CWD). |
| `qrcode(data, out_dir?)` | Save a raw QR **JPEG** for arbitrary data (prefer `generate_label` for totes — it adds the name). |
| `finalize_photos()` | Finalize after a bulk photo attach: set missing primary images, then generate missing thumbnails. |
| `field_index(field_name?)` | `{field_value: assetId}` index over a custom field (default `$HOMEBOX_ALIAS_FIELD`) — a one-pass **dedupe** index before a bulk import, since `q` doesn't index custom fields. |

## How it works (and Homebox 0.26 gotchas)

The value of this server is as much in what it works *around* as in what it
exposes. These are hard-won behaviors of the 0.26 entities API:

- **Items and locations are unified "entities"** (`/v1/entities`); a location is
  an entity whose entity-type has `isLocation=true`. A new item with no
  entityTypeId lazily gets a default `Item` type.
- **`GET /entities` returns non-location entities only.** Location children never
  appear in `/entities` results, even with `parentIds` — sub-locations come from
  the entity tree (`/entities/tree`). `location_contents` merges both sources, and
  `delete_location`'s emptiness check counts items *and* sub-locations.
- **Deleting a non-empty location cascades sub-locations but orphans items.**
  Force-deleting a location (`confirm_nonempty=True`) deletes its sub-locations
  with it, but its **items survive**, re-parented to the top level. Move items
  out first if you don't want them loose.
- **List/search responses are lightweight summaries** — they omit `fields` and
  have an empty `assetId`. The server fetches full detail (`GET /entities/{id}`)
  before returning assetId/alias, so `search_items`/`get_item` are accurate.
- **`q` does not index custom fields.** Resolving by an alias-field value uses a
  scan fallback (one detail fetch per item). Resolving by **assetId** (via
  `/v1/assets/{id}`) or **name** is cheaper — prefer assetId when you have it
  (e.g. the value `create_item` returns).
- **PUT clears omitted fields**, including `assetId`. Every write tool rebuilds a
  full body from a fresh GET (`_preserve_item_body`) and overrides only the
  targeted keys. `create_item` assigns the asset ID *after* enrichment so it
  survives.
- **Attachment titles are basenamed on `/`.** Homebox treats a title as
  path-like, so "front 3/4 view" silently stores as "4 view". The server
  sanitizes `/`→`-` in attach titles.
- **Number custom fields are integer-typed.** A float `numberValue` (e.g.
  `17000.0`, which FastMCP produces from a `float`-typed arg) makes the API
  **500 "Unknown Error"**. The server coerces number values to `int`; pass whole
  numbers.
- **Custom-field values are type-keyed** (`textValue` / `numberValue` /
  `booleanValue`). The server rebuilds each field by its declared type —
  coercing everything to `textValue` (an earlier bug) silently wiped
  numeric/boolean fields on any PUT.
- **Pagination.** Entity listings over one page (200) were silently truncated;
  the server pages through to the reported `total`, so search, dedupe, and
  warranty sweeps stay correct on large inventories.

## Conventions (optional)

Homebox's built-in `assetId` is a stable numeric handle, but it isn't something
you'd type from memory. If you want a **human-meaningful stable identifier** —
useful for cross-referencing items with receipts, spreadsheets, or another
system — add a custom field (say `item_id`) holding a slug like
`makita-xdt131` and point `HOMEBOX_ALIAS_FIELD` at it. Then:

- items resolve by that slug in every read and write tool,
- `search_items`/`get_item` summaries surface it, and
- `field_index()` defaults to it, giving you a one-call dedupe map before a bulk
  import.

This is entirely optional — leave `HOMEBOX_ALIAS_FIELD` unset and items resolve
by assetId or name only. Nothing about the field name is special; any single
custom field works.

## Companion scripts

Two standalone helper scripts ship in the repo (not MCP tools):

- **`annotate_location_photos.py`** turns one wide photo of a shelf bank into one
  boxed/labeled image per shelf (`--rows N`), and can upload each straight to its
  Homebox location with `--upload`. See its `--help`.
- **`heic2jpg.py`** converts HEIC/HEIF (iPhone photos) to JPEG for ad-hoc use
  (`./heic2jpg.py FILE...`). Note the attach tools **already auto-convert HEIC on
  upload**, so this is only needed to feed HEIC frames to a vision step *before*
  upload.

Both rely on `pillow-heif`, which bundles libheif — there's no system package to
install.

## Security

- **API key handling.** In the clone workflow the key lives in `.env`, which is
  gitignored and should be mode `600` (`chmod 600 .env`). Never commit it. In the
  `uvx` workflow it's passed through your MCP client's config — treat that file
  as a secret too.
- **Key rotation.** Rotating the Homebox `HBOX_AUTH_API_KEY_PEPPER` invalidates
  every issued key — mint a new one and update your config.
- **Confirm-gated deletes.** All four delete tools require `confirm` to equal the
  target's exact name and carry the MCP `destructiveHint` annotation, so a
  well-behaved client prompts before any destructive call.
- **Use HTTPS** for any remote Homebox instance — the API key is sent on every
  request in the `Authorization` header.

## Development

```bash
git clone https://github.com/dgahagan/homebox-mcp
cd homebox-mcp

# Run the test suite (httpx mocked with respx — no live instance needed)
uv run --with pytest --with respx --with . pytest

# Lint
uv run --with ruff ruff check .
```

The server is a single module (`homebox_mcp.py`) with PEP 723 inline
dependencies. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and PR
expectations, and [CHANGELOG.md](CHANGELOG.md) for release notes.

## License

[MIT](LICENSE) © Dan Gahagan
