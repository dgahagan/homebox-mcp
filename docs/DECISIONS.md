# Decision log

Reasoning behind non-obvious changes, newest first. Companion documents:
[REVIEW.md](REVIEW.md) (the original feature-completeness review that drove
v0.9) and [PLAN.md](PLAN.md) (the phased implementation plan).

---

## 2026-07-04 — Context & tool-surface optimization (pre-1.0.0)

### Why now

An audit against current MCP best practices (tool consolidation, schema/token
cost, response efficiency) before tagging 1.0.0 — the last moment tool
removals are cheap, since 1.0.0 freezes the public surface.

### Measured baseline

42 tools ≈ **8,300 tokens** of schema+description context for clients that
eager-load MCP tools (Claude Desktop and most others). Claude Code defers MCP
schemas and loads them on demand, so it pays ~0 — the optimization is for the
rest of the ecosystem. Breakdown: ~3.2k descriptions, ~3.9k input schemas
(FastMCP's `Optional` → `anyOf[X, null]` + auto titles are inherently
verbose), ~0.2k output schemas.

### Changes (42 → 39 tools, ≈ 7,500 tokens)

| Change | Reasoning |
|---|---|
| **Removed `set_location_manifest`** | Strict subset of `set_location(description=…)`. Two tools for one write forces the model to choose between near-identical options — the classic overlap smell. |
| **Removed `attach_location_photo`** | Subset of `attach_document` (location fallback + `doc_type="photo"` + `primary=True`). The wayfinding-photo use case moved into `attach_document`'s docstring. |
| **Merged `set_primary_photos` + `create_thumbnails` → `finalize_photos`** | Always called as a pair, in that order; a tool boundary between them only created a way to forget the second call. |
| **`Literal` enums on choice params** (`mode`, `tags_mode`, `status`, `by`, `kind`) | Plain `str` params documented valid values in prose only; `Literal` puts real enums in the JSON schema so bad values are rejected before the call instead of erroring after. |
| **Null-stripping in responses** (`_compact`) | `get_item`/summaries returned every null field (`"soldTo": null, …`). Absence reads the same as null to a model; a sparse item dropped from 17 keys to 4. Compounds in recursive listings. |
| **Caps on unbounded outputs** | `location_contents(recursive=True)` gained `max_items=500` and `list_custom_fields(field=…)` gained `limit=200`, both with explicit `truncated` markers — a 1,000-item instance must not be able to blow out the caller's context silently. `field_index` deliberately stays uncapped: it exists for dedupe, and a truncated dedupe index is worse than a big one (documented in its docstring instead). |
| **Docstring dedupe** | "preserved via a full-body PUT (the 0.26 PUT-clears gotcha)" appeared in ~7 tool descriptions. The full explanation lives once (in `set_item` and the README gotchas); the others say "other fields are preserved." Tool descriptions are paid for in every eager-loading conversation. |

### Considered and deferred

- **Folding `set_warranty` / `set_identity` / `mark_sold` into `set_item`** —
  would drop 3 more tools (~750 tokens) and remove "which editor?" ambiguity,
  but `set_item` would grow to ~20 params (its own schema bloat + mis-call
  risk), and it breaks the `enrich-inventory` skill's `set_warranty` calls.
  The current split is semantically clean; revisit only if users report
  confusion.
- **Merging the four `delete_*` tools into one mode-switched tool** — rejected.
  Separate tools keep per-target `destructiveHint` annotations and distinct
  confirm semantics; collapsing them weakens the safety story for no
  meaningful token win.
- **Merging `qrcode` into `generate_label`** — kept separate; `qrcode` encodes
  arbitrary data (deep links), which is a different contract than entity
  labels. Marginal either way.
- **MCP resources/prompts** (e.g. location tree as a resource) — optional
  spec surface with little payoff for a tool-driven server; skipped.
- **Input-schema slimming** — the remaining ~3.7k tokens of input schema are
  mostly FastMCP's `anyOf`-nullable pattern and auto-generated `title` keys;
  not worth hand-hacking generated schemas to fix.

### What the audit confirmed was already right

Tool annotations on everything, confirm-gated deletes, exact-vs-fuzzy
identifier split, legible API errors, and files-not-payloads for CSV export
and attachment downloads.

---

## 2026-07-03 — v0.9 design decisions (summary; details in REVIEW.md/PLAN.md)

- **Single codebase, no personal fork** — personal conventions live in env
  config (`HOMEBOX_ALIAS_FIELD`, `HOMEBOX_LABEL_DIR`) and the caller's skills,
  not in server code, so the public repo and personal instance can't drift.
- **Generic `fields: dict` over named personal params** — custom-field typing
  by JSON value type; the item_id slug scheme became an optional convention.
- **Exact-match resolution for writes, fuzzy for reads** — a typo must not
  mutate the wrong item; ambiguity errors list candidates instead of guessing.
- **Confirm-gated deletes** (`confirm` = exact target name) + MCP
  `destructiveHint`.
- **`server.py` shim after the `homebox_mcp.py` rename** — keeps pre-rename
  MCP registrations working.
- **0.9.x for public shakeout** — 1.0.0 after the surface has had real users.
