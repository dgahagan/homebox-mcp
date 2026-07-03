# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning: [SemVer](https://semver.org/).

## [Unreleased]

### Added
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

### Fixed
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
