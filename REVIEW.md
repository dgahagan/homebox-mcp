# Homebox MCP Server — Feature-Completeness Review

*Date: 2026-07-03 · Reviewed against: Homebox v0.26.2 (live instance), swagger spec (67 API paths), server.py @ fbbf15b*

## How this review was done

- Read all project files (`server.py`, both companion scripts, README, .env.example, .gitignore).
- Pulled the live instance's swagger spec (`/swagger/doc.json`, 67 paths) and mapped every endpoint against the 23 MCP tools.
- Exercised the actual MCP server end-to-end against the live instance with disposable test data: created a test location + item, ran `get_item`/`search_items` lookups by assetId and item_id slug, ran `set_warranty` and `set_tags`, verified nothing was wiped across two sequential full-body PUTs, tested the (uncovered) `PATCH /entities/{id}` endpoint directly, then deleted everything (item, location, auto-created tag) via raw API calls. **The production instance is back to its pre-review state.**

## TL;DR

The server is in very good shape for its original purpose (intake + inventory Q&A driven by your personal pipeline) and the hard-won Homebox 0.26 gotcha knowledge baked into it (PUT-clears, type-keyed custom fields, attachment title basenaming, integer-only number fields) is genuinely valuable and correct — I verified the preserve-on-PUT behavior live. For a *general-public* release, though, it is roughly **60% feature-complete as a "Homebox MCP"**: it covers create/read/enrich well, but is missing the entire *modify-lifecycle* half of inventory management (move, rename, adjust quantity, delete, archive/sell), all of Homebox's maintenance-log and statistics features, and it has several personal-workflow assumptions hard-coded that would confuse or break for other users.

Three findings I'd call out above all others:

1. **No way to move an item between locations** — arguably the single most common real-world inventory operation ("I moved the drill to the garage"). No rename, no quantity change, no delete either. During this review I had to fall back to raw `curl` to clean up my own test items.
2. **Personal conventions are baked in as if universal** — the `item_id`/`category`/`dossier`/`resale_value` custom-field schema, docstring references to `inventory/ENRICHMENT.md` and `/enrich-inventory`, and a label output dir that defaults to `../intake/labels/` — i.e. **outside this repo** (`_label_dir`, server.py:1055-1060, resolves to `~/work/personal/intake/labels` here).
3. **Silent truncation at 200 entities** — `_search_entities` fetches one page of 200 and never paginates (server.py:217-221). Your instance has 66 entities so it works today, but for a public user with 500 items, `existing_item_ids` (the dedupe index!), `warranties_expiring`, and the item_id resolve fallback all silently miss data. This is the most dangerous class of bug: no error, just wrong answers.

---

## What exists today (23 tools)

| Area | Tools | Verdict |
|---|---|---|
| Read / Q&A | `search_items`, `get_item`, `list_locations`, `location_contents` (recursive option), `list_tags`, `warranties_expiring` | Solid core; verified live |
| Item intake | `create_item` (create + enrich + asset-id in one call), `import_csv` (bulk), `barcode_lookup` | Strong — the CSV path with auto-created location hierarchy is a real differentiator |
| Item enrichment | `set_warranty`, `set_identity`, `set_value`, `set_tags` | Works, preserve-on-PUT verified live; but coverage is slices, not general editing |
| Locations | `create_location`, `set_location` (full editor), `set_location_manifest` | Locations are better served than items — `set_location` can rename/move/retype; items can't |
| Tags | `set_tag` (tag metadata), `set_tags` (on-item), `list_tags(detail=True)` | Complete for tag CRUD except delete |
| Attachments | `attach_document`, `attach_location_photo` (HEIC→JPEG auto-convert, URL or path source) | Upload-only; can't list/download/rename/delete |
| Labels / finalize | `generate_label`, `qrcode`, `set_primary_photos`, `create_thumbnails`, `existing_item_ids` | Works; label dir default is a personal-path leftover |

### Verified live (all on disposable test data, since removed)

- `create_location` → `create_item` (with location, mfr/model/serial, price, date, item_id field) → correct on `get_item` by assetId **and** by item_id slug (scan fallback worked).
- `search_items` found the item with correct assetId/location (list responses omit these; the per-result detail fetch works as documented).
- `set_warranty` then `set_tags`: after both PUTs, serial, price, purchase date, custom field, and tags all intact — `_preserve_item_body` does its job, including the type-keyed custom-field rebuild.
- `set_tags` auto-created an unknown tag as documented.
- Direct `PATCH /entities/{id}` (not used by the server): changed quantity only, wiped nothing — see "Opportunities" below.

---

## API coverage map

Homebox v0.26.2 exposes 67 paths. Excluding auth/user/group-membership plumbing an MCP shouldn't own (~15 paths), the inventory-relevant surface is ~50 paths. Coverage:

### Covered well ✅
`GET/POST /entities`, `GET/PUT /entities/{id}`, `/entities/tree`, `/entities/{id}/path`, `POST /entities/import`, `POST /entities/{id}/attachments`, `GET /assets/{id}`, `GET/POST /tags`, `PUT /tags/{id}`, `actions/ensure-asset-ids`, `actions/set-primary-photos`, `actions/create-missing-thumbnails`, `/labelmaker/*`, `/qrcode`, `/products/search-from-barcode`, `GET /entity-types` (internal).

### Not covered — grouped by user impact

**Tier 1 — expected of any complete inventory MCP:**

| Missing capability | Endpoint(s) | Notes |
|---|---|---|
| Move item to another location | `PATCH /entities/{id}` (`parentId`) | Verified live: PATCH is partial and wipe-safe for `parentId`/`quantity`/`tagIds`/`entityTypeId`. One small, safe call — no preserve-body dance needed. |
| General item edit (rename, quantity, description, notes, purchase info, insured/archived) | `PUT /entities/{id}` (existing preserve helper) | `set_location` already proves the pattern for locations; items have no equivalent. Could be one `set_item(...)` tool mirroring `set_location`. |
| Delete item / location | `DELETE /entities/{id}` | Needed to fix intake mistakes. Wants a safety design (see Open Questions). |
| Delete tag | `DELETE /tags/{id}` | `set_tags` auto-creates tags — typos accumulate forever with no way to remove them. |
| Attachment management (list w/ ids, rename, delete, download) | `GET/PUT/DELETE /entities/{id}/attachments/{attachment_id}` | `get_item` shows titles only — no ids, so nothing is actionable. Download would let the assistant re-read an attached manual. |
| Filter items by tag | `GET /entities?tags=...` | "Show me everything tagged warranty-active" currently requires a full scan. The param already exists. |
| Pagination everywhere | `GET /entities?page=` | Fix the silent 200-entity truncation with a paginate-all helper. |

**Tier 2 — Homebox features the MCP ignores entirely:**

| Feature | Endpoint(s) | Why it matters for a public MCP |
|---|---|---|
| Maintenance log | `GET/POST /entities/{id}/maintenance`, `GET /maintenance`, `PUT/DELETE /maintenance/{id}` | A headline Homebox feature. "Log that I changed the mower oil today" / "what maintenance is due?" are natural assistant asks. |
| Statistics | `GET /groups/statistics` (+ `/locations`, `/tags`, `/purchase-price`) | "What's my inventory worth?" — one cheap call (verified: returns totals incl. `totalItemPrice`). Also the correct fast path for insurance-summary questions. |
| Full CSV export | `GET /entities/export` | Complement to `import_csv`; backup/report story. |
| Sold / archive lifecycle | `PUT /entities/{id}` (`soldDate`, `soldTo`, `soldPrice`, `archived`) | Fields already preserved by `_preserve_item_body`; there's just no tool to set them. |
| Duplicate item | `POST /entities/{id}/duplicate` | Cheap to add; handy for "I bought a second one". |
| Custom-field discovery | `GET /entities/fields`, `/entities/fields/values` | Lets the assistant learn *a given user's* field schema instead of assuming yours. Key to de-personalizing the server. |

**Tier 3 — probably out of scope (agree/disagree welcome):**
Templates (`/templates*`), notifiers, entity-type CRUD, group invitations/members, user/password management, `actions/wipe-inventory` (should *never* be exposed to an LLM), currency list.

---

## Design & robustness issues

Ordered by severity for a public release.

1. **Silent truncation ≥200 entities** (server.py:217) — described above. Fix: loop `page`/`pageSize` until `total` reached, in one `_search_all()` helper used by `existing_item_ids`, `warranties_expiring`, and `_resolve`.
2. **Fuzzy resolve can mutate the wrong item** (server.py:224-258) — `_resolve`'s last resort returns the *first keyword candidate*. Fine for `get_item`; risky when `set_warranty`/`set_tags`/`set_identity` write through it. A typo'd identifier can silently update a different item. Fix: for write tools, require exact assetId/item_id/name match and return a disambiguation list ("did you mean…") otherwise.
3. **Personal conventions hard-coded** — `item_id`/`category`/`dossier`/`resale_value`/`value_asof`/`value_source` as named parameters on `create_item`/`set_value`; docstrings referencing `inventory/ENRICHMENT.md`, `/enrich-inventory`, "QR-first capture loop"; `.env.example` referencing `CREDENTIALS.local.md`; `generate_label`/`qrcode` default output dir `Path(__file__).parent.parent/"intake"/"labels"` which writes **outside the repo**. Fix options: (a) generalize to a `fields: dict` parameter (the `set_location` pattern) and present item_id/category as an optional documented convention, or (b) keep them but label clearly. Label dir should default to CWD or `~/.cache/homebox-mcp/labels`.
4. **N+1 request patterns** — `search_items` does one detail GET per result; `warranties_expiring` and `existing_item_ids` fetch detail for *every* entity; recursive `location_contents` does one list call per location plus one detail GET per item. At 66 entities this is fine; at 1,000+ it's 1,000+ sequential HTTP calls per question. Mitigations: honor the `tags` filter, cache the tree, batch with async httpx, and use `/groups/statistics` where it answers the question outright.
5. **Inconsistent error surfacing** — some failures return `{"error": ...}`, others raise raw `httpx.HTTPStatusError` (the Homebox response body, which usually says *why*, is discarded). One `_api_error()` wrapper returning `{"error": status, "detail": body}` would make every tool's failure legible to the model.
6. **Date normalization inconsistency** — `set_warranty` normalizes `YYYY-MM-DD` → RFC3339 (`_rfc3339`); `create_item` passes `warranty_expires`/`purchase_date` through raw. Worked in my live test for `purchaseDate`, but the two paths should share the helper.
7. **Duplicate location names are first-match-wins** — every location arg resolves by case-insensitive name against the whole tree; two totes named "Shelf 1" under different parents are indistinguishable, and `create_location` refuses the second outright. Fix: accept a path (`Garage/Shelf 1`) and/or error on ambiguity listing full paths.
8. **`warranties_expiring` includes long-dead warranties** — `w <= before` has no lower bound, so "expiring this year" includes ones that expired in 2019. Wants an `after`/`include_expired` option, plus a way to list lifetime-warranty items.
9. **Item-in-item nesting invisible** — 0.26 unified entities, so an item can contain items (camera bag → lenses). `location_contents(recursive=True)` only recurses into `isLocation` children, so nested items are missed; `search_items`' summary shows `location` as the direct parent, which may be an item.
10. **No version guard** — the server is 0.26-only by design (entities API). Against a pre-0.26 instance every tool 404s cryptically. One `GET /status` at startup with a clear "requires Homebox ≥ 0.26" error would save every mis-versioned user an hour.
11. **MCP polish** — no tool annotations (`readOnlyHint`/`destructiveHint`/`idempotentHint`), which matter once destructive tools exist; no MCP resources (the location tree is a natural one); server has no version string.

## Opportunity: use PATCH where it fits

`PATCH /entities/{id}` is partial and wipe-safe (verified live) for `parentId`, `quantity`, `tagIds`, `entityTypeId`. That means **move-item, quantity-change, and set_tags can skip the whole GET→preserve→PUT dance** — smaller requests, no read-modify-write race, immune to future preserve-body bugs. Everything else (name, warranty, purchase, custom fields) still needs the PUT path, so `_preserve_item_body` stays — it's just needed less often.

---

## Public-release readiness (non-code)

| Item | Status | Notes |
|---|---|---|
| License | ✅ MIT | |
| Secrets hygiene | ✅ | `.env` gitignored + mode 600; nothing sensitive in history (single initial commit) |
| README | ⚠️ | Good gotcha documentation (worth keeping — it's the repo's best asset). But: Claude Code-only setup (add generic MCP JSON config for Claude Desktop etc.); references personal workflow (`intake/labels`, enrichment); `.env.example` mentions `CREDENTIALS.local.md`; should state explicitly this targets **sysadminsmedia Homebox ≥ 0.26** (the actively-maintained fork at homebox.software) and why pre-0.26 won't work |
| Packaging | ⚠️ | PEP 723 single-file + `uv run --script` is legitimately nice (zero-install). For reach, consider *also* publishing to PyPI so `uvx homebox-mcp` works — that's the config one-liner most MCP users expect. Decision point below. |
| Tests / CI | ❌ | None. Minimum viable: a mocked-httpx unit suite for the preserve/PUT logic + resolve, and a lint (ruff) GitHub Action. The preserve-body logic is exactly the kind of thing that regresses silently. |
| Versioning / changelog | ❌ | No server version, no CHANGELOG. |
| Branch naming | ⚠️ | Local branch is `master`; repo config expects `main` for PRs — pick one before publishing. |
| Companion scripts | ❓ | `heic2jpg.py` is generic and small — keep. `annotate_location_photos.py` is niche-but-delightful; fine to keep with its README blurb, or split out. |
| Registry listing | ❌ | Consider submitting to the MCP registry / servers list once stable. |

---

## Suggested priority (draft — for discussion, not yet a plan)

1. **P0 (correctness for other people's data):** pagination fix; exact-match-only resolve for writes; error surfacing; version guard; label-dir default; strip/generalize personal references.
2. **P1 (feature-complete core):** `move_item` + `set_item` (general editor); `set_quantity` via PATCH; delete item/location/tag/attachment (with safety); attachment list/rename/download; tag-filtered search.
3. **P2 (Homebox parity):** maintenance log tools; statistics; export; sold/archive; duplicate; custom-field discovery.
4. **P3 (distribution):** PyPI packaging decision; tests + CI; generic client docs; MCP annotations/resources; registry listing.

---

## Decisions (2026-07-03)

1. **Custom fields → generalize.** `create_item`/`set_item` take a generic `fields: dict` (the `set_location` pattern). The item_id/category scheme becomes an optional documented convention, not API surface.
2. **Delete tools → yes, with confirm param.** `delete_item`/`delete_location`/`delete_attachment`/`delete_tag` require e.g. `confirm=<exact item name>` to execute, plus MCP `destructiveHint` annotations so clients prompt.
3. **Packaging → PyPI + keep script runnable.** Add `pyproject.toml` and publish so `uvx homebox-mcp` works, while `server.py` stays runnable standalone via `uv run --script`.
4. **v1 scope → Tier 1 hardened, Tier 2 fast-follow.** v1 = de-personalized, paginated, safe writes, item move/edit/delete, attachment management. Maintenance log / statistics / export / sold-archive land in v1.1.

Still open (minor): keep both companion scripts in the public repo, or trim to a pure MCP server repo?

## Personal vs. public: single-codebase strategy (decided 2026-07-03)

**No fork, no personal branch.** The public server is made fully generic; everything personal moves into configuration or the prompt layer. One clone serves both roles (it is already registered at user scope as the live MCP), so drift is impossible by construction.

| Personal behavior | Generic mechanism | Personal setting (gitignored `.env`) |
|---|---|---|
| `item_id` slug resolution + dedupe index | `HOMEBOX_ALIAS_FIELD` env names one custom field used for resolve; unset = assetId/name only. `existing_item_ids` → generic `field_index(field_name)`. | `HOMEBOX_ALIAS_FIELD=item_id` |
| Named field params (`item_id`, `category`, `dossier`, `value_*`) | Generic `fields: dict` on `create_item`/`set_item` (Decision 1) | callers pass the dict |
| Label dir `../intake/labels` | `HOMEBOX_LABEL_DIR` env, default CWD | points at the intake folder |
| Workflow guidance in docstrings (ENRICHMENT.md, `/enrich-inventory`, `warranty-active` pairing) | Removed — API docstrings describe the API only | already encoded in `~/.claude` skills (`intake-item`, `enrich-inventory`, `process-intake-batch`) |

One-time migration when signatures change: update the `~/.claude` skills' `create_item`/`set_value` call patterns to the `fields: dict` form, add the two env vars. Include this skill sweep in the implementation plan.

Escape hatch (only if something un-generalizable appears later): optional `HOMEBOX_MCP_EXTENSIONS=<path>.py` hook that registers extra private tools from a gitignored file importing the public helpers. Not needed today.
