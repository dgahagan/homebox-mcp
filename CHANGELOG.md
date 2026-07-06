# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning: [SemVer](https://semver.org/).

## [1.0.0] - 2026-07-05

### Changed (BREAKING) — context/tool-surface optimization (see docs/DECISIONS.md)
- Removed `set_location_manifest` (use `set_location(description=…)`) and
  `attach_location_photo` (use `attach_document(…, doc_type="photo",
  primary=True)` with a location name).
- Merged `set_primary_photos` + `create_thumbnails` into `finalize_photos()`.
- `list_custom_fields(field=…)` now returns `{total, values}` capped by
  `limit` (default 200) with a `truncated` marker.

### Changed
- Choice params (`mode`, `tags_mode`, `status`, `by`, `kind`) are typed as
  enums in the tool schemas — invalid values are rejected before the call.
- Responses omit null fields (a sparse `get_item` drops from 17 keys to 4);
  `location_contents(recursive=True)` caps at `max_items` (default 500) with
  an explicit truncation marker.
- Tool descriptions deduplicated (~10% smaller schema surface for
  eager-loading MCP clients: 42 tools/~8.3k tokens → 39 tools/~7.5k).

### Added
- Maintenance log: `log_maintenance`, `list_maintenance` (per-item or
  inventory-wide, scheduled/completed/both), `set_maintenance` (e.g. mark a
  scheduled entry completed), `delete_maintenance` (confirm-gated).
- Reporting: `inventory_stats` (totals / by-location / by-tag /
  purchase-price-over-time), `export_csv` (full-inventory CSV backup),
  `list_custom_fields` (discover field names and values in use).
- Sold lifecycle: `mark_sold` (price/buyer/date/notes, `clear=True` to
  un-sell).
- `duplicate_item` (copies custom fields by default; attachments/maintenance
  opt-in; documented alias-field dedupe caveat).

## [0.9.1] - 2026-07-03

### Added
- `server.json` + `mcp-name` marker in the README for the official MCP
  registry listing (registry ownership validation reads the PyPI README).

## [0.9.0] - 2026-07-03

### Changed (BREAKING)
- `create_item`: personal named params (`item_id`, `category`, `dossier`,
  `resale_value`, `value_asof`, `value_source`) replaced by a generic
  `fields: dict` — each value's JSON type picks the custom-field type
  (string → text, number → number [integer-coerced], boolean → boolean).
- `set_value` replaced by generic `set_fields(identifier, fields)` (same typed
  mapping, upsert semantics).
- `existing_item_ids()` replaced by `field_index(field_name?)` — a
  `{field_value: assetId}` index over any custom field, defaulting to
  `$HOMEBOX_ALIAS_FIELD`.
- Write tools (`set_warranty`, `set_fields`, `set_identity`, `set_tags`,
  `attach_document`) now require an exact identifier (assetId, alias field, or
  name); fuzzy matches are refused and multiple exact matches error with the
  candidates listed, so a typo can no longer mutate the wrong item. Read tools
  (`get_item`, `generate_label`) keep the fuzzy fallback.
- `set_location`'s `fields` values are typed by JSON type (was text-only).

### Added
- `move_item(identifier, location)` — move an item via partial PATCH.
- `set_item(...)` — general item editor (rename, description, notes, quantity,
  purchase info, insured/archived, custom fields); quantity-only edits use
  PATCH.
- Confirm-gated delete tools: `delete_item`, `delete_location` (refuses
  non-empty unless `confirm_nonempty=True`; sub-locations cascade, items are
  orphaned), `delete_tag`, `delete_attachment` — `confirm` must equal the
  target's exact name; all carry the MCP `destructiveHint` annotation.
- Attachment management: `list_attachments` (ids/types/titles),
  `get_attachment` (download to a local path), `rename_attachment`
  (title/type/primary).
- `search_items` accepts `tags` (tag names) and/or `query`.
- MCP tool annotations (`readOnlyHint`/`idempotentHint`/`destructiveHint`) on
  all tools.
- `HOMEBOX_ALIAS_FIELD` env var: name one custom field as a stable item
  identifier (items resolve by it; summaries surface it). Previously the
  `item_id` field name was hard-coded.
- `HOMEBOX_LABEL_DIR` env var: where `generate_label`/`qrcode` save output
  (default: current directory; previously a hard-coded personal path).
- `warranties_expiring`: `after` bound (defaults to today, so already-expired
  warranties are excluded by default) and `lifetime=True` to list
  lifetime-warranty items.
- Locations can be referenced by `/`-separated path (e.g. `Garage/Shelf 1`)
  everywhere a location name is accepted; ambiguous bare names now error with
  the matching paths listed instead of silently using the first match.
- Version guard: tools fail with a clear message against pre-0.26 Homebox.
- `__version__` on the server module.
- This changelog.
- Packaging: `pyproject.toml` (PyPI name `homebox-mcp`, console script
  `homebox-mcp`); the module renamed `server.py` → `homebox_mcp.py` with a
  `server.py` compatibility shim so existing MCP registrations keep working.
- Test suite (pytest + respx, fully mocked) and GitHub Actions CI
  (tests on Python 3.10/3.13, ruff) + PyPI trusted-publishing workflow.
- README overhauled for public release; CONTRIBUTING.md added.

### Fixed
- `location_contents` never listed sub-locations on Homebox 0.26.2:
  `GET /entities` returns non-location entities only, so location children
  were invisible to `parentIds` queries (latent upstream-behavior bug).
  Sub-locations now come from the entity tree; `delete_location`'s emptiness
  check counts both kinds.
- `location_contents(recursive=True)` now finds items nested inside items
  (0.26 unified entities — e.g. lenses inside a camera bag).
- `set_tags` uses partial PATCH instead of the GET→preserve→PUT cycle.
- Pagination: entity listings over one page (200 entities) were silently
  truncated, breaking search/dedupe/warranty sweeps on large inventories.
- API failures now surface as legible tool errors (HTTP status + Homebox's
  response body) instead of raw tracebacks.
- `create_item` date args are RFC3339-normalized like `set_warranty`'s.
- `create_location` duplicate check is per-parent, so the same tote/shelf name
  can exist under different parents.

### Changed
- Default branch renamed `master` → `main`.
- `.env.example` documents all supported environment variables.

[1.0.0]: https://github.com/dgahagan/homebox-mcp/compare/v0.9.1...v1.0.0
[0.9.1]: https://github.com/dgahagan/homebox-mcp/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/dgahagan/homebox-mcp/releases/tag/v0.9.0
