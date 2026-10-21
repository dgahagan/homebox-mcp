# Decision log

Reasoning behind non-obvious changes, newest first. Companion documents:
[REVIEW.md](REVIEW.md) (the original feature-completeness review that drove
v0.9) and [PLAN.md](PLAN.md) (the phased implementation plan).

---

## 2026-10-21 — SHA-pin every action; checkout v7, setup-uv v10

### Why

Dependabot opened #8 (setup-uv 5→7) and #9 (checkout 4→7). setup-uv was
already at v10.3.0, but Dependabot stopped at v7. Since v8, setup-uv publishes
no floating `@v8`-style major tags, as a supply-chain measure after the
tj-actions compromise. `ci.yml` referenced `@v5`, a floating tag, so v7 was
the newest version Dependabot could offer in that form.

### What changed

- `ci.yml` now pins actions by commit SHA with a `# vX.Y.Z` comment, the same
  as `publish.yml`. Every action in the repo is now SHA-pinned.
- checkout 4.4.0 → 7.0.1 and setup-uv 5.4.2 → 10.3.0 in both workflows. Each
  SHA was checked against its upstream tag before pinning.
- This replaces #8 and #9.

### Breaking changes checked

None apply here. checkout: node24 runtime (fine on GitHub-hosted runners),
credentials stored in a separate file (no step pushes), fork checkouts
blocked for `pull_request_target`/`workflow_run` (no workflow checks out
under those events). setup-uv: no auto-activated venv (`uv run --with` needs
none), `server-url` and the old manifest format removed (not used), cache
pruning off by default (a little more cache storage), and no automatic cache
on `release`, `pull_request_target` or `workflow_run` (these workflows run on
`push` and `pull_request` only).

### Side effect

Dependabot still proposes action updates, now as SHA changes. They never
auto-merge (auto-merge covers `uv` only), so each one gets a review. The SHA
pin removes the risk of a moved tag.

---

## 2026-10-21 — Raise the Python floor to 3.11 for Pillow 13 (v1.2.0)

### Why

Nine Pillow advisories were open (Dependabot alerts #19–#27), one critical:
a heap overflow in `ImagingJpegDecode`. All of them are fixed only in Pillow
13.0.0. Dependabot could not open a fix PR (`security_update_not_possible`,
latest resolvable 12.3.0). The cause was `requires-python = ">=3.10"`: Pillow
13 requires Python 3.11, so the resolver had to keep 12.x for 3.10.

The server decodes photos that users supply (`_heic_to_jpeg`). Malformed
image input is exactly what these advisories cover, so the risk is real here,
not theoretical.

### What changed

- `requires-python` is `>=3.11` and the minimum Pillow is `>=13`, in all five
  places that declare them: `pyproject.toml` and the PEP 723 headers in
  `homebox_mcp.py`, `server.py`, `heic2jpg.py` and
  `annotate_location_photos.py`.
- The CI matrix tests 3.11 and 3.13. Ruff targets `py311`.
- The `main: require CI` ruleset names the required test checks by their
  Python version, so it was updated from `test (3.10, …)` to
  `test (3.11, …)`. Without that, every PR waits forever on checks that no
  longer run.

### Alternatives rejected

- **Keep 3.10 with an environment marker** (`pillow>=13; python_version>="3.11"`)
  — 3.10 users would still install a vulnerable Pillow, and Python 3.10 is
  end-of-life this month. It keeps support for a dead interpreter at the cost
  of shipping known-vulnerable decoding.
- **Upper-bound Pillow (`<14`)** — not done. A cap on Pillow is what blocks
  the next security fix. The mcp cap exists because mcp breaks its API across
  majors. Pillow's API surface used here (`Image.open`, `convert`, `save`) is
  stable.

---

## 2026-10-11 — Auto-merge Dependabot patch and minor updates

### Why

Two Dependabot security PRs (#3 anyio, #4 pyjwt) sat open for 23 and 9 days
with 16 alerts behind them, two of them critical. Both changed only `uv.lock`
and both had green CI the whole time. The review they waited for added nothing
the CI run had not already checked. A human queue is the wrong gate for that
kind of change.

### What changed

- `.github/workflows/dependabot-auto-merge.yml` arms GitHub's native
  auto-merge on Dependabot `uv` PRs whose `update-type` is semver patch or
  minor. It does not merge anything itself.
- A repository ruleset on `main` requires the five CI checks (`lint` and the
  four `test (...)` jobs). That is what holds an armed PR until CI passes.
  Without it, `--auto` has nothing to wait for and merges at once.
- `.github/dependabot.yml` adds weekly version updates for `uv` and
  `github-actions`, with patch and minor grouped into one PR per ecosystem.
  Before this, only security updates existed, so updates arrived only after
  an alert had already fired.
- `publish.yml` actions are pinned by commit SHA instead of tag.

### Choices worth knowing

**Majors are never auto-merged.** The mcp 1.x → 2.x break (#1, #2) is the
reason: a major bump can need real work, not a click.

**GitHub Actions bumps are never auto-merged, only `uv`.** The same actions
run in `publish.yml`, which holds `id-token: write`: the PyPI trusted-publishing
credential. A compromised action release that auto-merged would run with the
power to publish homebox-mcp at the next tag. That is a path to users, so it
gets a human. The `tj-actions/changed-files` compromise (March 2025) is the
pattern: a trusted action's tags were moved to malicious code.

**`publish.yml` actions are pinned by SHA.** A tag is mutable, so `@v4` runs
whatever its owner points it at, with or without Dependabot. A SHA cannot
move. Dependabot proposes the pin updates, and per the rule above they are
reviewed by hand. `ci.yml` stays tag-pinned: its jobs hold only a read token.

**Allowlist, not "anything but major".** `fetch-metadata` can return an empty
`update-type`. An allowlist makes that case wait for a human instead of
merging.

**`pull_request_target`, not `pull_request`.** Dependabot-triggered
`pull_request` runs get a read-only token, which cannot enable auto-merge.
`pull_request_target` runs with a write token in the base-repo context. That
is only safe because the job never checks out or runs PR code. The workflow
comment says so, so a later edit does not add a checkout step by accident.
The job checks the PR author (`pull_request.user.login`), not `github.actor`,
which is whoever caused the latest event. It also requires the PR head to be
in this repo, since `pull_request_target` fires for fork PRs too.

**`fetch-metadata` is pinned by SHA**, unlike the tag-pinned actions in
`ci.yml`, because this job holds a write token. The `github-actions` ecosystem
keeps the pin current.

**A 3-day cooldown on version updates.** A new release must age three days
before Dependabot proposes it. Compromised or broken releases are usually
yanked within that window. Security updates ignore the cooldown, which is the
right way round.

**The ruleset has an admin bypass.** Direct pushes to `main` are the normal
workflow here and stay possible. The required checks gate PRs, not the
maintainer.

### Accepted risk

A malicious Python patch release that survives the cooldown and passes CI
would merge on its own. The blast radius is CI and the dev environment, not
users: `uv.lock` is not in the published wheel, and `publish.yml` runs only on
`v*` tags, with SHA-pinned actions that only change after review. A release
still needs a human to tag it.

### Side effect

A merge done with `GITHUB_TOKEN` does not trigger new workflow runs, so an
auto-merged PR produces no `push` CI run on `main`. CI already ran on the PR,
so nothing goes unchecked.

---

## 2026-09-15 — Migrate to mcp 2.x, require it outright (1.1.0)

Closes the follow-up opened by the 1.0.1 pin below.

### What the migration actually cost

Three lines in `homebox_mcp.py`: the `FastMCP` import, the `ToolError` import,
and the constructor. `ToolAnnotations` never moved. `@mcp.tool(annotations=…)`
and `mcp.run()` kept their signatures. The rest was test-side renaming, because
2.x made the pydantic models snake_case: `Tool.inputSchema` → `input_schema`,
`annotations.destructiveHint` → `destructive_hint`. The camelCase **wire**
aliases are unchanged, so nothing a client sees moved.

### Measured before deciding, not after

The issue's stated risk was that 2.x might generate different JSON Schema and
blow the ~7.5k-token tool-surface budget that the 2026-07-04 entry below spent
real effort on. That was checked rather than assumed: every registered tool was
dumped by wire alias under mcp 1.x, 2.0.0 and 2.2.0 and diffed.

All three are **byte-for-byte identical** — 39 tools, 32,485 characters, 70
`anyOf` nodes, 188 `title` keys. The budget did not move, so it did not
constrain the decision.

### Why require `>=2,<3` instead of supporting both

Supporting both majors would have been cheap in code — every API this server
touches is compatible, so a five-line `try/except ImportError` shim would have
done it. It was still rejected.

A compat shim that CI does not exercise on both sides is not compatibility, it
is an untested branch on the most load-bearing import in the module. Testing
both sides doubles the matrix to keep working an install path that barely
exists: `uvx` and `pipx` build isolated environments, so the only person helped
is someone vendoring this server into an environment already pinned to `mcp<2`.
That is a narrow enough case to answer with "pin homebox-mcp 1.0.1".

### A behavior change worth keeping

Under 1.x an unhandled exception reached the client with its message intact;
2.x masks it as `UnexpectedToolError`. Deliberate `ToolError` messages — the
Homebox status code and response body that `_tool_errors` raises, which the
2026-07-03 entry calls out as a property worth protecting — still arrive
verbatim. So the legible errors survive and the accidental disclosures stop.

### Two pins added, same reasoning as #1

`MCPServer` accepts a `version`, so `__version__` is now reported in the
`initialize` handshake. A user reporting "the server is broken" hands over the
exact release without being asked.

CI gained an mcp axis (declared floor 2.0.0 and newest allowed) plus an
assertion on the resolved major, so mcp 3.x lands as a red build rather than a
user's crash. Ruff got the same treatment for the same reason: the version was
unpinned and the rule set implicit, so ruff 0.16 widening its defaults turned
lint red with 111 findings in code nobody had touched. The rule set is now
explicit in `pyproject.toml` and the version pinned in the workflow. The
underlying lesson from #1 generalizes past `mcp` — **an unpinned input with an
implicit contract will eventually change on someone else's schedule.**

---

## 2026-09-15 — Pin `mcp<2` rather than migrate (1.0.1)

### What broke

Issue [#1](https://github.com/dgahagan/homebox-mcp/issues/1): `uvx
homebox-mcp` crashed for every new user. 1.0.0 shipped `mcp>=1.10` with no
upper bound, `mcp` 2.0.0 landed after that release, and 2.x removed
`mcp.server.fastmcp` — FastMCP is now `mcp.server.mcpserver.MCPServer`.
`homebox_mcp.py` imports `FastMCP` and `ToolError` from the old path, so the
process died at import, before any Homebox request. Clients just reported the
server as unavailable, which made it look like a config or auth problem.

### The decision

Ship the one-line pin as a patch release, not a 2.x migration.

- The break is total and affects every fresh install, so time-to-fix
  dominates. A pin is verifiable in minutes; a migration is not.
- The 2.x API changed more than the class name (tool registration, error
  types, context handling), so migrating touches all 39 tools and the test
  suite — that is a feature release with its own review, not a hotfix.
- `mcp` 1.x still works and is still resolvable, so the pin costs users
  nothing today beyond a slightly older transitive dependency.

### Why an upper bound is now permanent policy here

An unbounded major-version range on the one dependency that defines the
server's entire API surface is a latent break that fires on *someone else's*
release schedule, with no signal on our side. Every place the dependency set
appears — `pyproject.toml` and the PEP 723 headers in both `homebox_mcp.py`
and `server.py` — carries the bound, and all three must move together.

### Follow-up

Migration to the mcp 2.x API is tracked as its own issue. When it lands, the
bound moves to `>=2,<3` rather than being removed.

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
