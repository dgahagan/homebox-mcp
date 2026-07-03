# Homebox MCP — Implementation Plan

*Date: 2026-07-03 · Executes the decisions in [REVIEW.md](REVIEW.md). Scope: v1 public release (Tier 1 hardened) + personal migration. Tier 2 is listed at the end as v1.1 backlog only.*

Sizes: **S** = under an hour, **M** = a session, **L** = multiple sessions.

---

## Phase 0 — Repo groundwork (S)

- [x] 0.1 Rename branch `master` → `main` (matches repo config; do before anything else so history stays clean). *(done 2026-07-03)*
- [x] 0.2 Add `__version__ = "0.9.0"` to the server and a `CHANGELOG.md` (Keep-a-Changelog format, one `Unreleased` section). *(done 2026-07-03; final public version number decided at the publish gate)*
- [x] 0.3 Fix `.env.example`: drop the `CREDENTIALS.local.md` reference; document `HOMEBOX_URL`, `HOMEBOX_TOKEN`, and the two new vars from 1.5 (commented out, with defaults). *(done 2026-07-03)*

## Phase 1 — De-personalize + harden (P0 correctness)

The order below matters: config first (1.1), then the helpers everything else uses (1.2–1.4), then the API-surface changes (1.5–1.8), then the behavior fixes (1.9–1.11).

- [x] 1.1 **Config generalization (S)** — new env vars, resolved like the existing ones: *(done 2026-07-03; both values also added to the personal gitignored `.env` so behavior is unchanged locally — Phase 4.1 partially pre-done)*
  - `HOMEBOX_ALIAS_FIELD` — names one custom field used for identifier resolution and the dedupe index. Unset = resolution by assetId/name only. (Personal value: `item_id`.)
  - `HOMEBOX_LABEL_DIR` — output dir for `generate_label`/`qrcode`. Default: current working directory. (Kills the `../intake/labels` escape.)
- [x] 1.2 **Pagination (M)** — `_search_all(q=None, parent_ids=None)` helper that loops `page`/`pageSize` until the reported `total` is reached. Replaced every call site incl. `location_contents`' child listing (was its own 500-cap truncation). Live-verified page_size=10 returns the full set. `tags` param arrives with 2.6; mocked 450-entity unit test lands with 3.2. *(done 2026-07-03)*
- [x] 1.3 **Error surfacing (M)** — implemented as a `@_tool_errors` decorator on all 24 tools. **Deviation from plan:** transport/API failures raise MCP `ToolError` (status + method + path + Homebox's response body) instead of returning `{"error": ..., "detail": ...}` dicts — return-shape dicts would collide with FastMCP's structured-output schemas on non-dict-returning tools; ToolError is the MCP-native mechanism and reads the same to the model. Domain errors (not found, ambiguous, bad args) remain `{"error": ...}` returns. *(done 2026-07-03)*
- [x] 1.4 **Version guard (S)** — lazy, cached check on first tool call via the same decorator; fails open if `/status` is unreachable. Live-verified: 0.26.2 passes, simulated v0.21.3 yields the clear error. *(done 2026-07-03)*
- [x] 1.5 **`fields: dict` generalization (M)** — done as planned; typed mapping live-verified (text/number/boolean stored with correct types; float→int coercion confirmed). *(done 2026-07-03)*
- [x] 1.6 **Resolve safety for writes (M)** — `_resolve_exact` (writes: set_warranty/set_fields/set_identity/set_tags/attach_document) + `_resolve_fuzzy` (reads: get_item, generate_label). Live-verified: partial-name write refused; duplicate-name write refused listing both assetIds; attach_document refuses ambiguity before falling back to locations. *(done 2026-07-03)*
- [x] 1.7 **`existing_item_ids` → `field_index(field_name?)` (S)** — done; reads typed field values, defaults to `HOMEBOX_ALIAS_FIELD`, errors if neither given. *(done 2026-07-03)*
- [x] 1.8 **Strip personal references (S)** — all docstrings + module header swept; only generic wording remains (grep-verified). README got a minimal accuracy pass for the renamed tools; full overhaul stays in 3.4. *(done 2026-07-03)*
- [x] 1.9 **Date normalization (S)** — `create_item` routes `purchase_date`/`warranty_expires` through `_rfc3339`. Live-verified round-trip (Homebox echoes dates back date-only; both forms accepted). *(done 2026-07-03)*
- [x] 1.10 **`warranties_expiring` bounds (S)** — `after` defaults to today (excludes long-expired), `lifetime=True` lists lifetime-warranty items; missing `before` raises a usage error. *(done 2026-07-03)*
- [x] 1.11 **Location path resolution (M)** — `_find_location` resolves names or `/`-paths (suffix match, case-insensitive) everywhere a location is accepted; ambiguous bare names error with all full paths listed; `create_location` duplicate check is per-parent. Live-verified with same-named ZZ shelves under two parents. *(done 2026-07-03)*

## Phase 2 — Feature-complete core (P1 new tools)

- [x] 2.1 **`move_item(identifier, location)` (S)** — done; live-verified price/fields/assetId untouched after the PATCH move. *(done 2026-07-03)*
- [x] 2.2 **`set_item(...)` general editor (M)** — done; quantity-only edits use PATCH, everything else the preserve-PUT. *(done 2026-07-03)*
- [x] 2.3 **`set_tags` via PATCH (S)** — done for `set_tags`. **Deviation:** `set_location`'s tags arm keeps the single full PUT — it must echo `tagIds` in its PUT anyway (PUT-clears), so a separate PATCH would just add a second write. *(done 2026-07-03)*
- [x] 2.4 **Delete tools (M)** — done as designed. Discovered + documented: force-deleting a non-empty location cascades sub-locations but ORPHANS items to the top level. *(done 2026-07-03)*
- [x] 2.5 **Attachment management (M)** — done: list (ids/types/titles/mime), rename (title/type/primary via PUT merge), download (dir or file path, extension from content-type). Live round-trip verified. *(done 2026-07-03)*
- [x] 2.6 **Tag-filtered search (S)** — done; unknown tag names error listing the problem, query+tags compose. *(done 2026-07-03)*
- [x] 2.7 **Item-in-item nesting (S)** — done; nested item found with its full path in live test. **Discovery en route:** `GET /entities` returns non-location entities ONLY — sub-locations were never visible to `parentIds` queries (latent bug in the original `location_contents` too). Sub-locations now come from the tree; `delete_location` counts both kinds. *(done 2026-07-03)*
- [x] 2.8 **MCP tool annotations (S)** — done: readOnly ×9, idempotent ×13, destructive ×4; `mcp` floor bumped to >=1.10. *(done 2026-07-03)*

## Phase 3 — Packaging, tests, distribution

- [ ] 3.1 **Module layout (M)** — rename `server.py` → `homebox_mcp.py` (proper PyPI module name); keep a thin `server.py` shim (with its own PEP 723 header) that imports and runs it, **so your existing user-scope MCP registration pointing at `server.py` keeps working untouched**. `pyproject.toml` (hatchling): `[project] name = "homebox-mcp"`, console script `homebox-mcp = "homebox_mcp:main"`. Both `uvx homebox-mcp` and `uv run --script server.py` work.
- [ ] 3.2 **Tests (L)** — pytest + `respx` (httpx mocking). Priority order:
  1. `_preserve_item_body` round-trip incl. type-keyed custom fields (the silent-regression magnet),
  2. `_make_field`/`_field_value` typed mapping incl. float→int coercion,
  3. `_resolve_exact` (exact / ambiguous / miss) and alias-field resolution,
  4. pagination across page boundaries,
  5. confirm-gated deletes (wrong confirm → refused, no DELETE issued),
  6. `_rfc3339`, `_attachment_title`, location-path resolution.
  Plus one in-memory FastMCP smoke test (list tools, call one read tool against mocked API).
- [ ] 3.3 **CI (S)** — GitHub Actions: ruff + pytest on Python 3.10 and 3.13; PyPI publish via trusted publishing on tag push.
- [ ] 3.4 **Docs overhaul (M)** — README: uvx quickstart first; config blocks for Claude Code, Claude Desktop, and generic MCP JSON; regenerate the tool table; **keep the 0.26 gotchas section** (it's the repo's best asset); state plainly this targets sysadminsmedia Homebox ≥ 0.26 and why pre-0.26 won't work; remove personal-workflow references; document `HOMEBOX_ALIAS_FIELD` / `HOMEBOX_LABEL_DIR` with the "conventions like an item_id slug field" framing as an optional pattern. Companion scripts: **keep both** (default recommendation — `heic2jpg.py` is generic, `annotate_location_photos.py` is a documented differentiator); flag here if you'd rather trim.
- [ ] 3.5 **Publish (S)** — create the public GitHub repo, push, tag `v1.0.0`, verify PyPI publish, submit to the MCP registry / servers list.

## Phase 4 — Personal migration (same working session as 1.5's breaking change)

- [x] 4.1 Add to the gitignored `.env`: `HOMEBOX_ALIAS_FIELD=item_id`, `HOMEBOX_LABEL_DIR=<intake labels path>`. *(done 2026-07-03, during Phase 1)*
- [x] 4.2 Sweep `~/.claude` skills — updated `intake-item` (create_item fields-dict signature + dossier via set_fields), `enrich-inventory` and `process-intake-batch` (`existing_item_ids()` → `field_index()`). `ENRICHMENT.md` in the obsidian project grep-checked: only uses `set_warranty`/`attach_document`, whose signatures are unchanged. *(done 2026-07-03, same session as 1.5)*
- [x] 4.3 **Live verification** — 17-check suite on the production instance, disposable ZZ data only: fields-dict create with typed values, alias/name/assetId writes, fuzzy + ambiguous writes refused, fuzzy reads intact, field_index, set_fields upsert/preserve, attachment round-trip incl. ambiguity guard; all entities deleted, 404-verified. (`move_item`/confirm-gated delete parts of this check move to Phase 2 where those tools are built.) *(done 2026-07-03)*

---

## Sequencing & risk notes

- **Phases run 0 → 1 → 2 → (3 ∥ 4).** Phase 4 must land in the same session as 1.5; Phase 3 can proceed in parallel after Phase 2.
- **One breaking-change window.** All signature changes (1.5, 1.6, 1.7) land together in one commit, with 4.2's skill sweep in the same session. Nothing else breaks callers.
- **Your MCP registration doesn't change.** The `server.py` shim (3.1) keeps the existing user-scope registration valid; `.env` continues to be found next to the script.
- **Production safety.** All live testing uses disposable `ZZ MCP…`-prefixed entities, deleted at the end of each session (protocol proven during the review). Delete tools are exercised against test entities only. `actions/wipe-inventory` is never exposed.
- **Verification bar per phase:** Phase 1–2 items each get a unit test where logic warrants (list in 3.2) plus a live disposable-data check; the phase isn't done until both pass.

## v1.1 backlog (Tier 2 — out of scope for this plan)

Maintenance log tools (`log_maintenance`, `list_maintenance`, upcoming-across-items) · statistics (`inventory_stats`, by-location/by-tag/price-over-time) · CSV export · sold/archive lifecycle on `set_item` · `duplicate_item` · custom-field discovery (`list_custom_fields` via `/entities/fields[/values]`) · optional `HOMEBOX_MCP_EXTENSIONS` hook (only if a real un-generalizable need appears).

## Points to confirm at plan review

1. **Module rename + shim** (3.1): OK with `homebox_mcp.py` + `server.py` shim? (Alternative: keep `server.py` as the real module and accept the ugly top-level `server` name on PyPI — not recommended.)
2. **`set_value` → `set_fields`**: the vehicle-value convention (`resale_value`/`value_asof`/`value_source`) becomes skill-layer knowledge calling `set_fields`. Any objection?
3. **Companion scripts**: default is keep both in the public repo.
4. **Version target**: publish as `v1.0.0`, or start `v0.9.x` until it's had public shakeout?
