# Snapshot-UIUX — Sequencing & Consolidation

> **Coordination document.** Reads FIRST before any implementation;
> resolves the contract deltas between `be-plan.md` and `fe-plan.md`,
> sizes the initiative into 6 implementable phases, and pins the
> merge order against the in-flight `feature/unify-spawn-tools`
> branch (job `7e6a62db`). The two specialist plans remain the
> source of truth for their respective lanes; this file is the
> bridge that makes them one implementable initiative.
>
> **Worktree:** `/home/nea/ensemble-src-wt-snapshot-uiux` (branch
> `feature/snapshot-uiux` @ `0aba9924`, base = `ac399874`).
> **Status:** PLAN-ONLY. No source files modified, no commits made.
> **Inputs read:** `be-plan.md` (892 L), `fe-plan.md` (736 L),
> `design/design-spec.md` (754 L, pinned SHA `2ca69147`).

---

## 1. BE↔FE contract reconciliation (AUTHORITATIVE)

> The two specialist plans were drafted in parallel. This section
> resolves every conflict with a **binding decision** that
> implementation reads FIRST. Any deviation from §1 in either plan
> is overridden by this document.
>
> **Design-spec-side deltas:** the design-spec-side value changes
> recorded in D-6/D-7 below are canonically amended by
> `design/design-spec-amendment-contract-reconciliation.md`
> (authored concurrently by the designer; sanctioned spec-side
> amendment). This section REFERENCES that file — it does not
> create or duplicate it.

### 1.1 Verified deltas (D-1 … D-7)

Each row was verified against the two source files. Citations:
`be-plan.md §N.M` and `fe-plan.md §N.M` below.

| # | Topic | be-plan.md | fe-plan.md | Resolution (binding) |
|---|-------|------------|------------|----------------------|
| **D-1** | List envelope key | `{items, total}` (§4.1, mirrors `BlueprintListResponse` at `daemon/routers/blueprints.py:122-126`) | `{snapshots, total}` (§3.1 line 86, derived from design-spec.md §7.3) | **BE canon wins.** Envelope key is `items`. FE `SnapshotListResponse` interface uses `items: SnapshotRow[]; total: number`. The `?snapshots=` design-spec text in §7.3 is the older pre-canon draft — superseded. |
| **D-2** | Agent filter param name | `agent` (§4.1 line 96; exact match against `created_by_agent_id`, see D4) | `agent_id` (§3.1 line 74) | **BE wins.** Param is `agent`. FE `SnapshotFilters.agent_id: string \| null` maps to `?agent=`. Naming inside the TS interface stays `agent_id` (matches the field name) — only the wire-param name is `agent`. |
| **D-3** | Sort keys + default | 8 keys: `created_at_desc` (default), `created_at_asc`, `title_asc`, `title_desc`, `status_asc`, `status_desc`, `project_id_asc`, `project_id_desc` (§4.1 table line 106-117) | 4 keys: `created_desc` (default), `created_asc`, `title_asc`, `warm_desc` (§3.1 line 78) | **BE allow-list wins** (8 keys, more useful + status/project dimensions). FE `warm_desc` (warm-spawn-count sort) is **NOT implementable in BE v1** — no join per §6.2 of the design spec (which describes a future Phase-2 join, not v1). **Action:** drop `warm_desc` from FE sort options in v1; ship 4 of the 4 FE-visible keys (`created_desc`→`created_at_desc`, `created_asc`→`created_at_asc`, `title_asc`→`title_asc`, plus a 4th picked from BE's extras — **recommend `status_asc`** as the lowest-risk 4th; defer the rest as follow-ups). Document the rename + drop in the FE service's `SnapshotFilters.sort` type with a one-line comment. |
| **D-4** | `task_summary` in list items | List item = `to_dict()` shape; **includes** `task_summary` (§4.1 line 134) | List EXCLUDES `task_summary` (§3.1 line 110 "list response does **NOT** include `digest` or `task_summary`") | **Resolve by addendum to BE plan T3.** The current BE skeleton at `_to_list_item` (be-plan.md §7 line 569-573) only pops `digest`. **Add one line: `d.pop("task_summary", None)`** so list payloads stay lean per FE assumption. Detail endpoint still carries it (full `to_dict()` per §4.2). The plan file itself is not edited — this section IS the addendum. *(Amendment status 2026-10-05: A-2 is now ALSO applied inline in be-plan.md §4.1/§7 per amendment finding #3 — the two texts agree; pop is idempotent.)* |
| **D-5** | `project_name` / `warm_spawn_count` in list | Not in BE v1 (`to_dict()` has `project_id` only; warm-count join is a future Phase-2 per design-spec.md §6.3/§7.4) | FE §3.6 fallback table **already covers both drops** (line 145-146) | **No BE change.** FE renders `project_id`-derived label + `—` in the Warm column. Note: the existing `count_by_project` method (`daemon/repositories/snapshot/repository.py:481-489`) does name the project; if cheap, the FE could join that on the page (1 extra round-trip), but v1 stays server-light per be-plan §3 D1. |
| **D-6** | `limit` / pageSize | BE: `limit` default `50`, `ge=1, le=200` (be-plan §4.1 param table — already canon there) | FE: paginator `pageSize` default `25`, options `[10, 25, 50]` (fe-plan §3.1, §5.2, §5.3) | **RECONCILED — no BE↔FE conflict.** FE always sends an explicit `limit` from the paginator, so the BE default `50` only applies to non-FE callers. The design-spec's 25/50 page-size values are **superseded by this row** (the design-spec is not the contract surface for wire params — this table is). |
| **D-7** | Age presets | N/A on the wire — BE takes `created_after` ISO only; age is FE-derived (`computeAgeCutoff`) | FE ships exactly **4 presets**: `24h` / `7d` / `30d` / `all`, default **`all`** (fe-plan §5.1/§5.2) | **RECONCILED.** The design-spec's "30d default" is **superseded** (default is `all`); its "90d" preset is **DROPPED / deferred to follow-ups** — recorded explicitly here so spec and implementation do not drift. |

### 1.2 Verified alignments (OK-1 … OK-4)

| # | Topic | be-plan.md | fe-plan.md | Action |
|---|-------|------------|------------|--------|
| **OK-1** | Metrics URL | `GET /api/snapshots/metrics` canonical; legacy `GET /api/settings/snapshot-usage-metrics` kept deprecated 1 release (D8) | Primary = `/api/snapshots/metrics`; legacy-fallback constant documented in §3.6 / §6.6 | **MATCH.** After FE lands, `SettingsService.getSnapshotUsageMetrics` becomes unused-by-FE dead code hitting the deprecated endpoint — record as **optional follow-up** (do NOT delete while the legacy endpoint lives, to keep deprecation cycle honest). |
| **OK-2** | Tag encoding | Repeat-param `?tags=a&tags=b` (D3) | Repeat-param (§6.1) | **MATCH.** FE `SnapshotService.buildParams` uses `HttpParams.append('tags', v)` in a loop. |
| **OK-3** | Toggle | Stays at `GET/PUT /api/settings/snapshot-create` (§4.4; not in scope) | Same (§3.4) | **MATCH.** No router move; regression pin in be-plan §8.6 case 23. |
| **OK-4** | Digest | Detail-only, `?include=digest`; default `digest: {}` present-but-empty per D7 (§4.2 line 205) | Lazy-fetch on explicit user action (§6.4) | **MATCH.** Drawer calls `getById(id, { includeDigest: true })` only on "Show digest" click. |

### 1.3 Binding implementation addenda (NOT in either plan)

Two adjustments the two plans don't say but the implementation must do:

| # | Addendum | Source |
|---|----------|--------|
| **A-1** | `SnapshotListResponse` (BE) and `SnapshotListResponse` (FE TS interface) must use the same key name `items` — D-1 above. | D-1 resolution |
| **A-2** | Add `d.pop("task_summary", None)` to BE `_to_list_item` (between line 572 and 573 of be-plan.md §7). | D-4 resolution |

---

## 2. Initiative phases (one overnight implementation sitting)

Sized for a single engineer serialised through the night. The
user-stated hard requirement is "one overnight sitting" — the math
below shows that requirement is **tight** with single-engineer
serial work; the cut line is identified at §2.3.

### 2.1 Phase table (6 phases, sequential A→F)

| Phase | Title | Hours | Parallel? | What lands | Verifies |
|-------|-------|-------|-----------|------------|----------|
| **P0** | Contract freeze | 0.5 | — (reads only) | This sequencing doc + the four D-* resolutions. Both implementers read §1 of this file before opening a single line. | Implementation has zero ambiguity at start. |
| **P1** | BE foundation | 7.0 | **P3 (FE scaffold) can run in parallel** once P0 completes | `daemon/routers/snapshots.py` (new, 3 endpoints); `daemon/routers/snapshot_schemas.py` (new, ~60 L); `daemon/repositories/snapshot/repository.py` (new `list_with_filters` + 3-line `list_active_by_project` compat wrapper); `daemon/routers/settings.py` (1-line deprecated proxy for `/snapshot-usage-metrics`); `daemon/api.py` (`include_router` after line 2991, import); `CHANGELOG.md` entry. Per be-plan T1, T2, T3, T4, T5, T9 = 2.0+0.5+3.0+1.0+0.25+0.25 = **7.0h**. | `daemon.api.create_app` boots; new routes resolvable; FE can hit them via curl. |
| **P2** | BE tests green | 4.5 | sequential after P1 | `tests/unit/routers/test_snapshots.py` (new, **26 cases** per be-plan §8.1 — INCL. case 23 = settings toggle regression pin per amendment pass 3 fix #7); `tests/unit/test_snapshot_list_with_filters.py` (new, 10 cases per §8.2); `tests/unit/test_snapshot_repository.py` (extend with 7 cases per §8.3); `tests/unit/test_snapshot_search_service.py` (1 regression pin per §8.4). **Package-wide rollup = 44 unique cases** (§4.1). Per be-plan T6+T7+T8 = 2.0+2.0+0.25 = **4.25h** rounded to **4.5h** with 15-min tag-parity drift-pin test. | `cd /home/nea/ensemble-src-wt-snapshot-uiux && uv run pytest tests/unit/routers/test_snapshots.py tests/unit/test_snapshot_list_with_filters.py tests/unit/test_snapshot_repository.py tests/unit/test_snapshot_search_service.py` all green. **Gate idiom (PASS 4 amendment, blocker #2):** every pytest gate is **worktree-rooted** (`cd /home/nea/ensemble-src-wt-snapshot-uiux`) **and uses `uv run pytest`** (the project's uv-managed `.venv` is the only CPython 3.13 in the repo; bare `pytest` or `cd /home/nea/ensemble-src && pytest` would silently run the main-checkout's `.venv` and tests against the wrong tree). |
| **P2.5** | Toolchain bootstrap (PASS 4 amendment, blocker #3b) | 0.25 | once, before P1+P3 split | `cd /home/nea/ensemble-src-wt-snapshot-uiux && uv sync` (creates the worktree `.venv`; gate from blocker #2); `cd frontend && npm ci` (lockfile-exact; gate from blocker #3b); `cd frontend && npx playwright install --with-deps chromium` (one-time browser binary install). **Hard constraint: no new dependencies permitted** — uses ONLY the existing `playwright/test` devDependency and existing `@angular/material/*` modules. Adding any package is a plan-amendment-grade change. | `uv run python -c "import daemon; print(daemon.__file__)"` resolves inside the worktree (not the main checkout); `npm ls playwright` resolves to the locked version; `ls ~/.cache/ms-playwright/chromium-*` shows the installed browser. |
| **P3** | FE scaffold + service (ADDITIVE-ONLY — no deletions) | 2.5 | **parallel with P1+P2** (does not need BE running); depends on P0 only | New files: `frontend/src/app/models/snapshot.model.ts`; `frontend/src/app/services/snapshot.service.ts` (URL constants only + `buildParams` + `computeAgeCutoff` helpers — HTTP calls can be stubbed during P3). Per fe-plan T1-T9 (scaffold+route+menu+service) = 0.45+1.0+0.75+0.25 = **2.45h** rounded. **Deletion ownership: NONE in P3** — the Settings deletion is **P4-owned** and rides the SAME commit as the functional page (atomicity mandate, §6.1 `c6`; fe-plan §10 preamble). | `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` green; navigating to `/snapshots` renders an empty page; gear menu shows the Snapshots item directly after Settings, BEFORE the conditional Database/Maintenance appends (`app.ts:747`, `:792` — amendment finding #7). |
| **P4** | FE table + drawer + metrics + filters + relocation (**P4 OWNS the Settings deletion**) | 4.5 | sequential after P3; **requires P1 endpoints up** for end-to-end smoke | Wire `SnapshotService` to real HTTP; implement `SnapshotsComponent` filter bar + header toggle; `SnapshotsTableComponent` (8 columns, paginator, skeleton, states); `SnapshotDetailDrawerComponent` (7 sections — D-5: warm-spawn-count section OMITTED; copy-id, Show-digest lazy-load); relocation (**P4-owned, SAME COMMIT as the functional page** — atomicity mandate): delete `settings.component.html:242-321 + :323-358`; `settings.component.ts:12, 107-142, 278-279, 715-810`; `settings.component.spec.ts:1345-1533` (snapshot banner + mock classes + the ONE describe block, ~190 lines) **PLUS the 3 snapshot mock lines at `:1566-1570`** inside the Timezone suite's `TimezoneTestBedService` (amendment finding #1 — the original `:1346-1985` / "~640 lines" range wrongly swallowed ~450 lines of the unrelated `:1535-1996` Timezone suite; none of that may be deleted). Per fe-plan T14-T21 = 1.5+2.0+0.75 = **4.25h** rounded to **4.5h**. | Browser: page loads, table renders, filters work, drawer opens, copy-id works, `/settings` no longer shows the two snapshot sections, and the full Timezone spec suite still passes. |
| **P5** | FE specs + final green | 1.5 | sequential after P4 | `snapshots.component.spec.ts` (**13 cases** per fe-plan §8.1 row 1 — incl. pass 4 amendment adds (l) + (m) for agent-filter population; amendment pass 4 #8 restructure); `snapshots-table.component.spec.ts` (**9 cases** per §8.1 row 2 — now PRESENTATIONAL, no fetch); `snapshot-detail-drawer.component.spec.ts` (**10 cases** per §8.1 row 3 — incl. lazy digest, 200KB guard, retry, plus (a) updated to reflect drawer's own fetch lifecycle); `snapshot.service.spec.ts` (**6 cases** per §8.1 row 4); `settings.component.spec.ts` settings-clean regression (1 case per §8.2); route + menu regression in `app.component.spec.ts` (2 cases per §8.3). **Subtotal = 38 new + 3 regression = 41 cases** (§4.2). `cd frontend && ./node_modules/.bin/tsc --noEmit` + `cd frontend && npm test` final pass. Per fe-plan T22-T26 = 0.5+0.4+0.3+0.1+0.1+0.15 = **1.55h** rounded. | `tsc` and `jest` both green; no `--bail` failures. |
| **P6** | E2E smoke (tester handoff) | 1.0 | sequential after P5 | Execute the ordered checklist in §4.3 of this file (steps 1-10 + 11a-11c per amendment finding #8); sign off the design-spec ACs cited. | Tester E2E sign-off. |

**Subtotals:**
- Serial (P0 → P2.5 → P1 → P2 → P3 → P4 → P5 → P6): **21.75h** (0.5 + 0.25 + 7.0 + 4.5 + 2.5 + 4.5 + 1.5 + 1.0)
- With P3 parallelised against P1+P2 in wall-clock (P3 overlaps P1+P2; P2.5 is a one-time entry-criterion that runs before the split; depends only on P0): **19.25h** wall-clock (saves 2.5h)

### 2.2 Per-role wall-clock

| Lane | Hours | Sequence |
|------|-------|----------|
| **BE engineer** (single) | 7.0 + 4.5 = **11.5h** | P0 → P1 → P2 → (idle for P3, P4, P5 unless dual-role) → P6 |
| **FE engineer** (single) | 2.5 + 4.5 + 1.5 = **8.5h** | P0 → P3 → P4 → P5 → P6 |
| **Single engineer doing both** | **21.75h** | P0 → P1+P2 → P3 → P4 → P5 → P6 (no parallelism; P2.5 bootstrap absorbed in P0) |
| **Pair (BE serialises FE)** | **13–14.5h wall-clock** | P0 (joint) → P1+P3 in parallel → P2 → P4 → P5+P6 (P2.5 bootstrap +0.25h absorbed in the P0 joint block) |

### 2.3 Total-vs-overnight verdict — **PAIR MODE DECLARED**

> **Run mode (LEADER RULING, amendment pass 3 blocker #3; refined
> pass 4 note #11):** pair mode is the DECLARED run mode for this
> initiative. 2 developer agents in parallel (BE lane + FE lane), 1
> overnight, **≈13–14.5h wall-clock** — fits one long overnight
> comfortably (14.5h fits a long night; the §2.3 cut line below
> stays the pressure valve if it slips). The wall-clock gate is
> thereby ticked. Serial/2-overnight stays as a documented FALLBACK
> only (recorded in run-brief §7 + below).

- **One engineer serialised:** **21.75 hours**. That is **TIGHT** for
  a single overnight sitting (rough 22:00 → 06:00 = 8h, OR
  22:00 → 10:00 = 12h if you sleep through; neither fits 21.75h).
  Realistic verdict: **single-engineer serial work DOES NOT fit
  one overnight; it spans 2-3 nights** at focused pace with normal
  breaks. **Fallback only — not the declared run mode.**
- **Pair (BE + FE in parallel):** **≈13–14.5h wall-clock** — fits one
  long overnight comfortably. **DECLARED run mode (per blocker #3;
  refined pass 4 note #11).** 14.5h is the realistic upper bound (a
  long night; the §2.3 cut line stays the pressure valve). The
  wall-clock gate is ticked.
- **Recommended cut line if time runs short (prioritise what ships
  first; the rest can slip to the morning after):**
  | Priority | Phase | Why this is the cut line |
  |----------|-------|--------------------------|
  | **P0 (must)** | Contract freeze | No code without this. |
  | **P1 (must)** | BE foundation | Endpoints are the contract surface. |
  | **P3 (must)** | FE scaffold + route + menu (additive only — no deletions) | Without the scaffold, there is no page to relocate into. |
  | **P4 (must)** | FE table + drawer + the Settings deletion in the SAME commit (table only, no separate metrics-strip slip — see qualifier below) | The page must render the table, and the deletion must ride the same commit: no intermediate state where the toggle/metrics UI exists nowhere (atomicity mandate, §6.1 `c6`). |
  | **P2 (must before merge)** | BE tests | Cannot merge without green (4-GREEN merge gate, §6.4). |
  | **P5 (must before merge)** | FE specs + tsc + jest green | Cannot merge without green. |
  | **P6 (must before merge)** | **Automated Playwright e2e (§4.3)** — replaces the manual browser smoke from previous iterations | Cannot merge without Playwright e2e green (4-GREEN merge gate, §6.4). |
  | **Slip to next morning (acceptable — PRE-RELOCATION ONLY)** | FE metrics strip (subset of P4) — `metrics-strip` section can land as a separate commit; toggles between two cards already proven in P4. **QUALIFIER (amendment finding #6b):** this slip is valid ONLY PRE-relocation — i.e., only while the Settings-side metrics block is still rendered. Once the relocation commit lands, the strip MUST be in it (the Settings-side metrics `<section>` is deleted in that same commit; letting the strip slip past it would leave the metrics UI existing NOWHERE). |
  | **Slip to follow-up** | `tsc` exhaustive lint of new SCSS; contrast audit at AC-9.2; light-mode forward-compat (out of project scope today per fe-plan F5). |

**Operational recommendation (pair mode = DEFAULT):** spawn 2
developer agents in parallel — one on the BE lane (P1 → P2), one
on the FE lane (P3 → P4 → P5). P6 (automated Playwright e2e)
runs after both lanes converge; it is a serial handoff. The BE
agent must finish P2 before the FE agent's P4 can fully wire
HTTP — the handoff is "P2 green pytest output + running daemon
+ new routes curlable from `localhost:8079`". The FE agent
pre-flies with stubbed HTTP during P3 (does not need the BE
daemon running). **Fallback only:** if a single engineer is doing
both lanes, schedule two consecutive overnights (P0-P3 night 1,
P4-P6 night 2).

---

## 3. Coupling with `feature/unify-spawn-tools` (job 7e6a62db, 2 commits + residual dirty tree)

> **Status refresh (amendment pass 4, note #9):** the unify-spawn-tools
> worktree at `/home/nea/ensemble-src-wt-unify-spawn-tools` now carries
> **TWO COMMITS** on top of `ac399874` (the base this branch was cut
> from), plus a residual dirty tree:
> 1. `62c33c40 refactor: unify spawn tooling — spawn_instance gains gated snapshot warm-start; spawn_hot_instance removed`
> 2. `2fa92fa8 fix: review riders — ari rule agent list, loader comment, gate-on/no-task cold pin`
>
> Verified 2026-10-05 via `git log --oneline -5` on the worktree.
> The original §3 mechanics ("0-commit / pre-implementation / dirty
> tree") are **superseded by this refresh** — the branch now has
> shape; only the dirty-tree layer remains in flux. The hunk-by-hunk
> touch-set below (§3.4) is still accurate as the change surface;
> the rebase / conflict guidance now treats the 2 commits as
> **real** (not a dirty-tree forecast). The mirrored contingency
> (rebase-onto-them if they merge first) is **new** in §3.5.

### 3.1 BE-side overlap: ONE shared file — `daemon/routers/settings.py` (auto-merge expected)

The original claim in this section read "**BE-side overlap: ZERO**".
**Amended 2026-10-05 (amendment finding #9); refreshed pass 4 (note #9):**
a verified `git -C /home/nea/ensemble-src-wt-unify-spawn-tools diff -U0` shows
their dirty tree **DOES** modify `daemon/routers/settings.py`, with two
hunks: `@@ -649` (docstring hunk at the `get_plane_config` area) and
`@@ -713,3 → 715,4` adjacent to `get_snapshot_usage_metrics` — the
exact handler this branch deprecates and proxies. Both hunks are
expected to auto-merge against this branch's 1-line deprecated-proxy
insertion; **re-verify pre-merge** (hunk heads can drift as their
dirty tree evolves). (be-plan §12's original "No file or line is
shared" claim is amended in the same pass.)

Verified against both branches' touch sets (corrected):

| This branch (BE) | unify-spawn-tools (BE) | Overlap? |
|------------------|------------------------|----------|
| `daemon/routers/snapshots.py` (NEW) | — | None |
| `daemon/routers/snapshot_schemas.py` (NEW) | — | None |
| `daemon/repositories/snapshot/repository.py` (add `list_with_filters` + 3-line compat wrapper at `:423-449`) | `daemon/registry.py:302` (new `snapshot_enabled` field), `daemon/registry.py:640, 706` (read site) | None |
| `daemon/routers/settings.py:702` (1-line deprecated proxy) | `daemon/routers/settings.py` — 2 hunks: `@@ -649` (doc), `@@ -713,3 → 715,4` (`get_snapshot_usage_metrics` area); plus `daemon/tools/snapshot_tools.py:917+`, `daemon/tools/instance.py:2092` | **SHARED FILE** (`settings.py` only) — doc-hunk + metrics-handler-adjacent; auto-merge expected; re-verify pre-merge |
| `daemon/api.py:2991` (`include_router` insertion) | `daemon/services/_tool_registry.py` (does not exist at this SHA; the file is renamed/moved) | None |

Per be-plan §12 (amended in the same pass): the only shared BE file is
`daemon/routers/settings.py` (above); everything else stays disjoint.

### 3.2 FE-side overlap: settings.component.html only — REBASE REQUIRED

The other branch's `feature/unify-spawn-tools` worktree contains
modifications to:

```
frontend/src/app/pages/settings/settings.component.html:252:
  <code>meta.json</code> <code>snapshot_enabled: true</code> (enabled
frontend/src/app/pages/settings/settings.component.html:302:
  <code>snapshot_enabled</code> in their <code>meta.json</code>).
```

**Amendment finding #9 (verified 2026-10-05 via `git diff -U0` on
`/home/nea/ensemble-src-wt-unify-spawn-tools/`); refreshed pass 4
(note #9 — the worktree is now 2 commits + dirty, not 0-commit):** their
dirty tree's `settings.component.html` diff has **THREE** hunks, not
one contiguous edit — `@@ -249,5 +249,11`, `@@ -293,2 +299,4`, and
`@@ -330,3 +338,4` (the third INSIDE the `:323-358` metrics `<section>`
this branch deletes). All three live inside or adjacent to the sections
our branch deletes; the two-line excerpt above is illustrative, not
exhaustive.

Verified by `git diff -U0` on `/home/nea/ensemble-src-wt-unify-spawn-tools/`
2026-10-05 (supersedes the earlier grep-only verification). These lines
live **inside the section our branch
deletes** (the `<section>` at html:242-321). The other branch
currently sits at 2 commits on `ac399874` (`62c33c40`, `2fa92fa8` —
pass 4 note #9) plus a residual dirty tree; when they go to merge
their next commit, the snapshot-section hunks (especially the
`@@ -330,3 +338,4` inside the deleted `:323-358` metrics block) will
fail to apply unless they rebase.

**Exact coordination note for the PR description of this branch:**

> **Heads-up to `feature/unify-spawn-tools` author (job 7e6a62db):**
> This PR deletes `frontend/src/app/pages/settings/settings.component.html:242-321`
> and `:323-358` (the two `<section>` blocks for "Agent Snapshots"
> toggle and "Snapshot Usage Metrics") plus their backing signals in
> `settings.component.ts:12, 107-142, 278-279, 715-810`. The per-agent
> `snapshot_enabled` copy at `settings.component.html:252/:302`
> therefore has no home in the new layout.
>
> **Action required from your branch:** rebase `feature/unify-spawn-tools`
> onto `feature/snapshot-uiux` after this PR lands. The per-agent
> gate UI is being added to the **new `/snapshots` page** (per-row
> toggle in the table — design-spec §6.4, deferred to the next
> follow-up commission that follows the `daemon/registry.py:302`
> field landing). All THREE of your `settings.component.html` hunks
> (`@@ -249`, `@@ -293`, `@@ -330` — the last inside the deleted
> `:323-358` metrics block) will fail to
> apply cleanly; either rebase and re-derive against the new page
> or split your branch into two commits — the registry/daemon BE
> change and the FE re-derivation. Your BE `settings.py` hunks
> (`@@ -649` doc, `@@ -713,3 → 715,4` metrics-handler area) are
> expected to auto-merge, but re-verify at rebase time.

### 3.3 Per-agent `snapshot_enabled` gating — merge-order invariant

**Restate, in writing:**

> Both `feature/snapshot-uiux` and `feature/unify-spawn-tools` ship
> **stub-free v1** with respect to the per-agent `snapshot_enabled`
> UI surface. The new BE field lands in `feature/unify-spawn-tools`
> at `daemon/registry.py:302` (verified above). The new FE toggle
> in the `/snapshots` page is a **separate follow-up commission**
> that follows this one — it does NOT ride either branch.
>
> **Merge order (binding):**
> 1. `feature/snapshot-uiux` lands first (this branch).
> 2. `feature/unify-spawn-tools` rebases onto (1) and lands.
> 3. A third commission (snapshot-uiux v2) adds the per-row per-agent
>    toggle in the table, coupling to the registry field from (2).
>
> Any deviation from this order creates a frontend referencing a
> backend field that doesn't exist yet, OR a backend field with no
> UI surface that the user expects.

### 3.4 Touch-set side-by-side (merge-conflict checklist)

| File | This branch (snapshot-uiux) | unify-spawn-tools |
|------|----------------------------|-------------------|
| `daemon/routers/snapshots.py` | NEW (whole file) | untouched |
| `daemon/routers/snapshot_schemas.py` | NEW (whole file) | untouched |
| `daemon/repositories/snapshot/repository.py` | +1 method + 3-line wrapper | untouched (verified) |
| `daemon/routers/settings.py` | +1-line deprecated proxy at :702 | **M — 2 hunks** `@@ -649` (doc) + `@@ -713,3 → 715,4` (`get_snapshot_usage_metrics` area — the handler this branch deprecates); auto-merge expected, **re-verify pre-merge** (amendment #9) |
| `daemon/api.py` | +1 import + 1 `include_router` at :2991 | untouched |
| `daemon/registry.py` | untouched | +1 field at :302; +2 read sites at :640, :706 |
| `daemon/tools/snapshot_tools.py` | untouched | ~10 edits (lines 16, 37, 42, 44, 50, 100, 126, 153, 159, 567) |
| `daemon/tools/instance.py` | untouched | edits at :5138, :5203 |
| `daemon/repositories/snapshot/models.py` | untouched (NOT in this plan's touch set) | M (their dirty tree: 3 hunks `@@ -52`, `@@ -82`, `@@ -311`) — **DISJOINT**, listed so the pre-merge checker does not false-alarm (amendment #9) |
| `daemon/services/snapshot_metrics_service.py` | untouched (NOT in this plan's touch set) | M (their dirty tree: 2 hunks `@@ -10`, `@@ -107`) — **DISJOINT**, same note |
| `daemon/services/snapshot_search_service.py` | untouched (NOT in this plan's touch set) | M (their dirty tree: 1 hunk `@@ -51`) — **DISJOINT**, same note |
| `frontend/src/app/pages/settings/settings.component.html` | DELETES :242-321, :323-358 (full sections) | **3 hunks** ~:249, ~:293, ~:330 — the third INSIDE the deleted :323-358 metrics block (amendment #9; supersedes the earlier ":252, :302" two-line note) |
| `frontend/src/app/pages/settings/settings.component.ts` | DELETES :12, :107-142, :278-279, :715-810 | may add new per-agent signal trio (verify on rebase) |
| `frontend/src/app/services/settings.service.ts` | untouched (the 3 methods stay) | possibly edits the 3 methods (verify) |
| `CHANGELOG.md` | +1 entry | +1 entry (separate) |
| `agents/{ari,developer[v2],leader,tester}/*` | untouched | M (per `git status -sb` on the other worktree) |

**Action at merge:** if any line in the column "snapshot-uiux" is
also touched by the other branch on rebase, stop and re-plan — a
3rd pair-of-eyes is cheaper than a 2am revert. Per be-plan §12
line 848-850.

### 3.5 Mirrored contingency — if THEY merge to `latest` first (pass 4 note #9)

> **Scenario (pass 4 note #9).** The declared merge order in §3.3
> is: `feature/snapshot-uiux` lands first; `feature/unify-spawn-tools`
> rebases onto it. That is the EXPECTED path. But the OTHER order is
> possible — if `feature/unify-spawn-tools` lands on `latest` first
> (they now have 2 commits + a residual dirty tree, so the bar to land
> has dropped), then **WE rebase onto them** and resolve the same two
> shared files in reverse.

**Two shared files (in either merge direction):**

| # | File | Conflict surface | Resolution guidance (WE rebase onto THEM) |
|---|------|------------------|---------------------------------------------|
| 1 | `daemon/routers/settings.py` | Their hunks (`@@ -649` docstring area; `@@ -713,3 → 715,4` adjacent to `get_snapshot_usage_metrics`) vs our deprecated-proxy + new `Response` import (be-plan §5.2 amendment pass 4 #5) | **Merge BOTH.** Our deprecated-proxy handler lives BELOW the `get_snapshot_usage_metrics` handler (the one they edit at `@@ -713,3 → 715,4`) and imports `Response` for the manual `Deprecation`/`Sunset`/`Link` headers (precedent: `daemon/routers/blueprints.py:25`). Their docstring hunk at `@@ -649` is disjoint from our changes. Take both ours and theirs. **Re-verify** the merged file: `from fastapi import Response` is present in the imports; the new deprecated handler at `:702` (post-merge) is intact; their `@@ -649` docstring is preserved verbatim. |
| 2 | `frontend/src/app/pages/settings/settings.component.html` | Their 3 hunks (`@@ -249,5 +249,11`, `@@ -293,2 +299,4`, `@@ -330,3 +338,4`) all live INSIDE or ADJACENT to the sections our branch deletes (`:242-321` toggle, `:323-358` metrics) | **OUR DELETION WINS** on `@@ -330,3 +338,4` (the third hunk is INSIDE the `:323-358` metrics block we delete — the deleted lines are gone, the hunk cannot apply). For `@@ -249,5 +249,11` and `@@ -293,2 +299,4` (the per-agent `snapshot_enabled` copy), the per-agent copy they add is part of the **stub-free v1** invariant (§3.3 standing merge-order rule): the UI is being relocated to the new `/snapshots` page. **Resolution: drop their per-agent hunks from the rebase**; they will re-derive onto the new `/snapshots` page in a follow-up commission (per the §3.3 standing merge-order rule and the standing merge-order invariant). |

**Provenance:** This mirrored contingency is **new in amendment pass 4
(note #9)**. It is the inverse of §3.2's "they rebase onto us"
guidance; together the two form a complete merge-order matrix.

---

## 4. Consolidated test & acceptance strategy

### 4.1 BE test summary — **44 unique cases (PINNED)**

> **Identity note (mandatory).** 26 router cases INCLUDE case 23
> (`test_settings_toggle_regression`); the §8.6 regression pin IS
> router case 23, NOT a separate case. The single package-wide
> figure is **44 unique**. Earlier figures ("33", "45") were the
> bug being fixed in amendment pass 3.

| Surface | Unique cases | File | Citation |
|---------|-------------|------|----------|
| Router | 26 (incl. case 23 = settings toggle regression pin; amendment pins #24-26: task_summary exclusion, metrics surface-raise fail-soft, 1s-apart sort pin) | `tests/unit/routers/test_snapshots.py` (new) | be-plan §8.1 |
| Repo (SQLite + PG parity, new file) | 10 | `tests/unit/test_snapshot_list_with_filters.py` (new) | be-plan §8.2 |
| Repo (extend existing) | 7 | `tests/unit/test_snapshot_repository.py` | be-plan §8.3 (cases 1-7 from §8.2) |
| Spawn-hot WARM regression (extend existing) | 1 | `tests/unit/test_snapshot_search_service.py` | be-plan §8.4 |

**Total BE unique cases: 44** (26 router + 10 new-repo + 7
repo-ext + 1 search-ext). The settings toggle regression pin lives
inline as router case 23 — NOT double-counted. Identity repeats in
be-plan §8.6 ("§8.6 IS case 23 of §8.1, NOT a new case").

### 4.2 FE test summary — **38 new cases + 3 regression pins = 41 total**

> **FE count map (verified 2026-10-05 against fe-plan §8.1; pass 4
> amendment #8 restructure):**
> 13 = page-host (`snapshots.component.spec.ts`, cases a-m — pass 4
> amendment #8 adds cases (l) + (m) for agent-filter population, and
> the page now OWNS the list() fetch + seenAgents population per #8
> leader ruling);
> 9 = presentational table (`snapshots-table.component.spec.ts`, cases
> a-i — no service injection, no fetch, receives rows via input,
> emits `rowClick` + page events ONLY);
> 10 = drawer (`snapshot-detail-drawer.component.spec.ts`, cases a-j
> — the drawer OWNS its lazy detail fetch + digest + 200KB guard +
> retry per #7 leader ruling; case (a) updated to reflect the
> drawer's own fetch lifecycle);
> 6 = service (`snapshot.service.spec.ts`, cases a-f).
> **Subtotal = 38.** Plus 3 regression pins
> (settings-clean = 1, route = 1, menu = 1) → **41 total** cases
> that `cd frontend && npm test` exercises. (Pass 4 amendment #8
> counts: page-host 11→13, FE new-file total 36→38, +3 pins = 41.)

| Surface | New cases | File | Citation |
|---------|-----------|------|----------|
| Page host | 13 (a-m, incl. amendment pass 4 #8 adds (l) + (m) for agent-filter population; pass 4 #8 restructure moves list() fetch + seenAgents population to the page; pass 3 adds: save-error, metrics error/empty, R10 cold-list, #10 reset-in-same-write) | `snapshots.component.spec.ts` (new) | fe-plan §8.1 row 1 |
| Self-fetching table (now PRESENTATIONAL — pass 4 #8) | 9 (a-i, incl. amendment adds: R11 stale-response race, #10 offset=0 single-request) | `snapshots-table.component.spec.ts` (new) | fe-plan §8.1 row 2 |
| Drawer (owns lazy detail fetch + digest + 200KB guard + retry — pass 4 #7) | 10 (a-j, amendment-enumerated: loading skeleton, fetch error+retry, lazy digest only-on-click, digest error+retry, 200KB guard + copy, copy-id; case (a) updated for drawer's own fetch lifecycle) | `snapshot-detail-drawer.component.spec.ts` (new) | fe-plan §8.1 row 3 |
| Service | 6 | `snapshot.service.spec.ts` (new) | fe-plan §8.1 row 4 |
| Settings-clean regression | 1 | `settings.component.spec.ts` (extend) | fe-plan §8.2 |
| Route regression | 1 | `app.component.spec.ts` (extend) | fe-plan §8.3 |
| Menu regression | 1 | `app.component.spec.ts` (extend) | fe-plan §8.3 |

**Total FE cases: 38 new + 3 regression pins = 41.** All run via
`cd frontend && npm test` per fe-plan §8.5 BUILD/TEST DISCIPLINE.

### 4.3 TESTER E2E SMOKE — automated Playwright spec plan (P6)

> **Re-scope (amendment pass 3, blocker #2).** P6 is now an
> **automated Playwright e2e suite** replacing the manual
> browser-step checklist. Each step keeps its pass condition but
> is expressed as an automated assertion; the suite runs in CI as
> a merge gate. The drawer-error path (step 11c) is forced
> deterministically via **Playwright route-interception**
> (`page.route('**/api/snapshots/*', route => route.abort())`),
> not by stopping the daemon.
>
> **Post-merge manual eyeball is NON-GATING.** It is optional and
> runs only if a developer wants human confirmation; it does NOT
> block merge.

**File:** `frontend/e2e/snapshots.spec.ts` (new — Playwright spec; lives
under the FE test surface per fe-plan §8.5 BUILD/TEST DISCIPLINE
style). The suite uses `playwright/test` (the project's e2e runner;
verified present in `frontend/package.json`).

> **Path & runner (pass 4 amendment, blocker #3a).** The path is
> `frontend/e2e/snapshots.spec.ts` — the existing
> `frontend/playwright.config.ts:4` pins `testDir: './e2e'`
> (relative to `frontend/`, so the resolved directory is
> `frontend/e2e/`). The base config's `webServer` array at
> `:17-37` uses `reuseExistingServer: true` (`:21` + `:34`) — that
> pattern re-uses the developer's main-checkout daemon on port
> 8079, which is **the wrong daemon** for this branch (it would
> silently e2e the live 8079, not the worktree's daemon). The
> dedicated config below fixes this (blocker #3c).
>
> **Runner idiom:** `cd frontend && npx playwright test e2e/snapshots.spec.ts`
> (npx invoked from INSIDE the `frontend/` package dir — picks up the
> local devDependency `playwright/test`; bare `npx playwright` outside
> a package dir is forbidden by the project's incident-pinned
> convention; cf. fe-plan §8.5 BUILD/TEST DISCIPLINE — bare
> `npx tsc` outside `frontend/` is the prior-incident pattern
> that triggered this rule).

| # | Step | Playwright assertion | Pass condition | Design-spec AC |
|---|------|----------------------|----------------|-----------------|
| 1 | Open `/snapshots` via the gear menu (item "Snapshots" with bookmarks icon, AFTER Settings, BEFORE conditional Database/Maintenance appends at `app.ts:747`/`:792`). | `await page.goto('/snapshots')` after navigating from gear menu (click `[data-test="gear-menu"]` → `[data-test="menu-snapshots"]`); assert `page.url() === '/snapshots'`; `await expect(page).toHaveTitle(/Snapshots/)`; assert no `page.on('pageerror')` fires. | URL is `/snapshots`; `document.title` contains "Snapshots"; no console errors. | AC-1.1, AC-1.2, AC-1.3 |
| 2 | Confirm the page renders the header (H1 "Snapshots"), header toggle (Enabled/Disabled radio + Apply), metrics strip, filter bar, table with 8 column headers (Title, Project, Agent, Status, Tags, Created, Warm, Actions), and paginator. | `await expect(page.locator('h1')).toHaveText('Snapshots')`; `expect(page.getByRole('radio', { name: /Enabled/i })).toBeVisible()`; assert 8 `<th>` cells with the expected text via `page.locator('table thead th')`; paginator `expect(page.locator('[data-test="paginator"]')).toBeVisible()`. Empty state path: `expect(page.getByText(/No snapshots yet/i)).toBeVisible()` when API returns `total: 0`. | All sections present; paginator shows "1 – N of M" where M > 0 if any snapshots exist; otherwise the "No snapshots yet" empty state. | AC-2.1, AC-4.1, AC-5.1, AC-7.1 / AC-7.3 |
| 3 | Exercise each filter one at a time, asserting the request URL contains the expected param AND the table re-fetches: (a) Project → a known project id; (b) Agent → "coder"; (c) Tags → `kind:implementation`; (d) Status multi → 2 chips; (e) Age → "24h"; (f) Sort → "Oldest first". After each: assert 200 response, list re-renders, `pageIndex` reset to 0. | Use `page.waitForRequest(req => req.url().includes('/api/snapshots?'))` after each control interaction; capture `req.url()` and assert it contains the right param name (`project_id`, `agent`, `tags=...&tags=...`, `status=...&status=...`, `created_after=...`, `sort=created_at_asc`); assert `req.response()?.status() === 200`; assert `expect(page.locator('[data-test="paginator-page-1"]')).toBeVisible()` (pageIndex back to 0). | Each filter change produces a single `GET /api/snapshots?...` with the right param; 200 response; pageIndex resets to 0. | AC-4.1, AC-4.4, AC-4.5 |
| 4 | Apply a filter combination that returns 0 rows (e.g. nonexistent tag); confirm the "Clear filters" CTA appears inline; click it; confirm all filters reset and the list re-fetches with defaults. | Inject a filter via `page.locator('[data-test="filter-tag-input"]').fill('nonexistent:tag')`; `expect(page.getByRole('button', { name: /Clear filters/i })).toBeVisible()`; click; assert request URL drops all filter params (`req.url() === /\/api\/snapshots\?/`) and the table re-renders with non-empty defaults if any snapshots exist. | Table shows filtered-empty state with a "Clear filters" button; click resets every control to its default; request URL drops all filter params. | AC-4.2, AC-7.4 |
| 5 | Click a table row; confirm the detail drawer slides in from the right; verify 7 section headers are present in order (Task summary, Git anchor, Runtime / Model, Supersedes chain, Tags, Timestamps, Context) — **D-5: the "Warm-spawn count" section is OMITTED in v1** (the TABLE's "Warm" column stays, rendering `—`; the warm-spawn-count drawer section is removed per amendment D-5); click "Show digest"; confirm a second `GET /api/snapshots/{id}?include=digest` request fires AND the digest renders as a pretty-printed `<pre>` block. Click the Copy ID button; confirm clipboard contains the full UUID. | `await page.locator('table tbody tr').first().click()`; `expect(page.locator('[data-test="snapshot-drawer"]')).toBeVisible()`; assert the 7 section headers via `page.locator('[data-test="drawer-section"] h3')`; click "Show digest" → `await page.waitForRequest(req => req.url().includes('/api/snapshots/') && req.url().includes('include=digest'))`; assert 200; `expect(page.locator('[data-test="digest-pre"]')).toBeVisible()`; click "Copy ID" → assert `await page.evaluate(() => navigator.clipboard.readText())` matches the row's `id`. | Drawer open; 7 sections visible; digest section lazy-loaded; clipboard API called with the row's `id`. | AC-6.1, AC-6.2, AC-6.3, AC-6.4, AC-6.5, AC-6.6, AC-6.7, AC-6.8, AC-6.9 |
| 6 | Scroll to the metrics strip; confirm the "Capture counts" card renders ≥1 row when the metrics endpoint returns data, AND the "Warmed snapshots" card renders ≥1 row when `spawn_counts_per_snapshot` is non-empty. If the warmed card is empty, confirm it is hidden (not blank). | `expect(page.locator('[data-test="metrics-capture-card"]')).toBeVisible()`; assert ≥1 row via `page.locator('[data-test="metrics-capture-card"] tbody tr')` when `capture_counts` non-empty; same for `metrics-warmed-card`; assert warmed card NOT in DOM when `spawn_counts_per_snapshot` is empty. | Both cards visible when data exists; Warmed card absent when empty; capture card shows "No captures yet" when empty. | AC-3.1, AC-3.2, AC-3.3 |
| 7 | Toggle the header radio from "Enabled" to "Disabled"; confirm "Unsaved changes" hint appears and Apply becomes enabled; click Apply; confirm a `PUT /api/settings/snapshot-create` with `{"enabled": false}` fires; on 200, confirm the hint hides. Reload the page; confirm the toggle reflects the new state. | `await page.getByRole('radio', { name: /Disabled/i }).check()`; `expect(page.getByText(/Unsaved changes/i)).toBeVisible()`; `await page.waitForRequest(req => req.method() === 'PUT' && req.url().endsWith('/api/settings/snapshot-create'))`; click Apply; assert request body is `{"enabled": false}` via `req.postDataJSON()`; assert response `200`; `expect(page.getByText(/Unsaved changes/i)).not.toBeVisible()`; `await page.reload()`; re-assert radio state. | PUT fires, 200 response, hint hides, reload persists. | AC-2.1, AC-2.2, AC-2.3, AC-2.4 |
| 8 | Navigate to `/settings`; confirm the "Agent Snapshots" `<section>` AND the "Snapshot Usage Metrics" `<section>` are both ABSENT. The page should not reference `snapshotCreate*` or `snapshotMetrics*` signals anywhere. | `await page.goto('/settings')`; `expect(page.getByRole('heading', { name: /Agent Snapshots/i })).toHaveCount(0)`; same for "Snapshot Usage Metrics"; rendered DOM check via `await page.locator('main').innerHTML()` MUST NOT contain `snapshotCreate` or `snapshotMetrics`. | The settings-clean regression test in `settings.component.spec.ts` passes; the rendered DOM contains no `snapshotCreate*` or `snapshotMetrics*` references. | AC-1.4 |
| 9 | Hit the legacy `GET /api/settings/snapshot-usage-metrics` endpoint directly via the page-context fetch; confirm 200 with the same JSON body as the new `/api/snapshots/metrics` endpoint; confirm `Deprecation: true` header is present in the response. | `const resp = await page.request.get('/api/settings/snapshot-usage-metrics')`; `expect(resp.status()).toBe(200)`; `expect(resp.headers()['deprecation']).toBe('true')`; assert body deep-equals a parallel fetch to `/api/snapshots/metrics` (assert `JSON.stringify(resp1) === JSON.stringify(resp2)`). | Legacy endpoint returns 200 with `Deprecation: true` header and body identical to the new endpoint. | OK-1 (regression pin) |
| 10 | Run all gates locally: tsc + jest + pytest + Playwright e2e. | `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` returns 0; `cd frontend && npm test` returns 0; `cd /home/nea/ensemble-src-wt-snapshot-uiux && uv run pytest tests/unit/routers/test_snapshots.py tests/unit/test_snapshot_list_with_filters.py tests/unit/test_snapshot_repository.py tests/unit/test_snapshot_search_service.py` returns 0; `cd frontend && npx playwright test e2e/snapshots.spec.ts` returns 0 (all 4 steps above green). | tsc returns 0; jest returns 0; pytest returns 0; Playwright e2e returns 0. **The merge gate (seq §6.4) is "ALL FOUR GREEN".** **Pytest gate idiom (pass 4 amendment, blocker #2):** worktree-rooted + `uv run pytest` (project's uv-managed `.venv` only; bare `pytest` or main-checkout root runs the wrong tree). **Playwright gate idiom (pass 4 amendment, blocker #3a):** invoked from INSIDE `frontend/` so the local devDependency resolves; path is `e2e/snapshots.spec.ts` (relative to `frontend/`; the base `playwright.config.ts:4 testDir: './e2e'` is the anchor). | fe-plan §8.5 (verbatim); seq §6.4 merge gate. |
| 11a | With the detail drawer open, press **Escape** (amendment finding #8). | `await page.locator('table tbody tr').first().click()` to open; `await page.keyboard.press('Escape')`; `expect(page.locator('[data-test="snapshot-drawer"]')).not.toBeVisible()`; `expect(page.locator('table tbody tr').first()).toBeVisible()` (table remains interactive); assert no `page.on('pageerror')` fires. | Drawer closes; `selectedSnapshotId()` / `drawerSnapshot()` clear; the table behind remains interactive; no console errors. | AC-6.x (drawer interaction), fe-plan §10.5 task 18 |
| 11b | Re-open the drawer on any row, then **click the backdrop** (amendment finding #8). | `await page.locator('table tbody tr').first().click()`; `await page.locator('[data-test="drawer-backdrop"]').click()`; same assertions as 11a. | Drawer closes with the same cleared state as 11a; row selection does not leak into a phantom detail fetch. | AC-6.x (drawer interaction), fe-plan §10.5 task 18 |
| 11c | **Drawer-error path (deterministic via Playwright route-interception):** block the drawer's detail request before clicking the row; confirm the drawer renders the "Failed to load snapshot details — Retry" error block; restore the route and click Retry → drawer loads and renders the 7 sections; close control works throughout. | `await page.route('**/api/snapshots/*', route => route.abort())`; `await page.locator('table tbody tr').first().click()`; `expect(page.locator('[data-test="drawer-error"]')).toBeVisible()`; `expect(page.locator('[data-test="drawer-error"]')).toContainText(/Failed to load snapshot details/i)`; `expect(page.locator('[data-test="drawer-retry"]')).toBeVisible()`; `await page.unroute('**/api/snapshots/*')`; click Retry; assert 7 sections render; assert close button (`[data-test="drawer-close"]`) works at any point in the sequence. | Drawer body shows the "Failed to load snapshot details — Retry" error block on the aborted fetch; after restoring the route and clicking Retry, the drawer loads and renders the 7 sections; close control works throughout. | AC-6.1-6.9 (error state), fe-plan §7.2 |

**Done condition:** all 10 numbered steps + 11a-11c pass in CI
(`cd frontend && npx playwright test e2e/snapshots.spec.ts` exits 0).
Post-merge manual eyeball is OPTIONAL and **NON-GATING**.

**Runner requirement:** Playwright + browser binaries must be
available in CI (install via `npx playwright install --with-deps chromium`
on the runner image; the project precedent is
`frontend/playwright.config.ts`). This is a one-time bootstrap, NOT
part of the per-PR pipeline cost.

### 4.3.1 Dedicated Playwright config — port hygiene (pass 4 amendment, blocker #3c)

> **Why a dedicated config.** The base `frontend/playwright.config.ts`
> mounts a `webServer` array (`:17-37`) that runs `cd .. && bash dev.sh`
> on port 8079 with `reuseExistingServer: true` (`:21` + `:34`) — the
> pattern re-uses the developer's main-checkout daemon. For this
> branch's e2e that is **the wrong daemon**: it silently e2e-tests
> the live 8079 main-checkout daemon, not the worktree's. The branch
> MUST launch a DEDICATED backend+frontend pair FROM THE WORKTREE on
> distinct strict ports, with `reuseExistingServer: false`, the API
> target set via env override, and zero collision with the live
> 8079/4199.

**Recommended dedicated config — `frontend/playwright.snapshots.config.ts`**
(new file; loaded via `--config` flag, leaves the base config
untouched for sibling specs):

```ts
// frontend/playwright.snapshots.config.ts
//
// Amendment pass 4 (blocker #3c): dedicated port pair for the
// snapshot-uiux e2e. Loaded via:
//   cd frontend && npx playwright test \
//     --config playwright.snapshots.config.ts e2e/snapshots.spec.ts
//
// Citations to the base config being overridden:
//   - testDir: './e2e'                       (playwright.config.ts:4)
//   - webServer[0].port: 8079                (playwright.config.ts:20)
//   - webServer[0].reuseExistingServer: true (playwright.config.ts:21)
//   - webServer[1].port: 4199                (playwright.config.ts:33)
//   - webServer[1].reuseExistingServer: true (playwright.config.ts:34)
//
// This dedicated file:
//   - extends the base so all reporters / projects / devices inherit
//   - narrows testMatch to e2e/snapshots.spec.ts ONLY
//   - launches a dedicated backend on 18079 (uvicorn against the
//     worktree .venv) with reuseExistingServer: false
//   - launches a dedicated frontend on 14199 (ng serve against the
//     worktree node_modules) with reuseExistingServer: false
//   - sets SNAPSHOTS_API_BASE env so the FE points at 18079
//
import { defineConfig, devices } from '@playwright/test';
import baseConfig from './playwright.config';

export default defineConfig({
  ...baseConfig,
  testDir: './e2e',
  testMatch: /snapshots\.spec\.ts$/,
  // Override the webServer array: dedicated ports, no reuse.
  webServer: [
    {
      // Worktree-rooted dev.sh on port 18079 (NOT 8079).
      // SNAPSHOTS_API_BASE is forwarded so the FE proxy can route
      // /api → 18079 instead of 8079.
      command: `cd .. && SNAPSHOTS_API_BASE=http://localhost:18079 bash dev.sh`,
      port: 18079,
      reuseExistingServer: false,
      timeout: 60000,
      stdout: 'pipe',
      stderr: 'pipe',
      env: {
        OPENAI_API_KEY: process.env.OPENAI_API_KEY || '',
        LOG_LEVEL: 'info',
        ENSEMBLE_PORT: '18079',  // dev.sh honors this knob
      },
    },
    {
      // Worktree-rooted ng serve on port 14199 (NOT 4199), with the
      // proxy /api target overridden via env to hit 18079.
      command: 'ng serve --port 14199',
      port: 14199,
      reuseExistingServer: false,
      timeout: 120000,
      env: {
        // The project's dev proxy reads this; check the proxy.conf.js
        // at the worktree root for the exact env name (likely
        // API_TARGET or PROXY_API_TARGET). Exact name verified at
        // implementation time against the live proxy config.
        API_TARGET: 'http://localhost:18079',
      },
    },
  ],
  use: {
    ...baseConfig.use,
    baseURL: 'http://localhost:14199',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
```

**Invocation (pass 4 amendment, blocker #3a + #3c):**

```bash
cd frontend && npx playwright test \
  --config playwright.snapshots.config.ts e2e/snapshots.spec.ts
```

The `cd frontend` is non-negotiable (npx inside a package dir
resolves the local devDependency; bare npx outside is the prior-
incident pattern). The `--config` flag loads the dedicated config;
no merge into the base config is required. After the e2e passes,
the dedicated config is REUSABLE for the next snapshot-uiux e2e
PR and any other test that needs a dedicated daemon.

### 4.3.2 FE toolchain bootstrap (pass 4 amendment, blocker #3b)

> **Constraint (binding).** **No new dependencies permitted — hard
> constraint.** The e2e spec uses ONLY existing `playwright/test`
> (already a devDependency in `frontend/package.json`) and the
> project's existing `@angular/material/*` modules. Adding any
> package is a plan-amendment-grade change.

**Bootstrap sequence (one-time, on a fresh clone OR first-time-on-this-host):**

```bash
# 1. Sync the worktree's .venv (uv-managed; 3.13; the only CPython
#    that matches the lazy-annotation target). This is the gate from
#    pass 4 amendment blocker #2; runs ONCE per worktree.
cd /home/nea/ensemble-src-wt-snapshot-uiux && uv sync

# 2. Install FE devDependencies from the locked package.json
#    (`npm ci` is lockfile-exact, NOT `npm install` which may
#    update the lockfile — the hard constraint above forbids new
#    deps; `npm ci` is the safe idiom). This is the gate from
#    pass 4 amendment blocker #3b.
cd frontend && npm ci

# 3. Install Playwright browser binaries (one-time per host).
cd frontend && npx playwright install --with-deps chromium
```

These three lines are the entry criterion for P3 (FE scaffold) AND
P6 (Playwright e2e). They appear as a single P2.5 row in the
phase table (§2.1) — runs ONCE before the parallel P1+P3 split.
Runs against the worktree, NOT the main checkout (blocker #2 +
#3a invariants).

---

## 5. Consolidated risks (deduped)

Merge of be-plan §9 (R1-R10) + fe-plan §11 (R1-R12). Cross-cutting
risks the sibling plans can't see are flagged with **[CROSS]**.
Owner column = which plan owns the mitigation.

| ID | Risk | Severity | Likelihood | Mitigation | Owner plan |
|----|------|----------|------------|------------|------------|
| **RX-1** | Contract deltas D-1..D-4 unresolved at implementation time (this file is the binding reconciliation; the two plans still disagree in their text). **[CROSS]** | High | Medium | This sequencing doc §1 is the source of truth; both implementers read it FIRST. | THIS DOC |
| **RX-2** | Overnight budget overrun (single engineer serial = 21.75h does not fit one night). **[CROSS]** | High | High | §2.3 cut line; schedule 2 overnights OR run as pair; P3 parallelises against P1+P2 to save 2.5h. | THIS DOC |
| **RX-3** | `feature/unify-spawn-tools` rebase required after this PR lands; merge conflict at `settings.component.html:252/:302` if the other branch is not rebased. **[CROSS]** | Medium | High | §3.2 coordination note pasted verbatim into PR description; side-by-side touch-set table in §3.4 for the merger. | THIS DOC |
| **RX-4** | `task_summary` in list payloads (be-plan §4.1 includes it, fe-plan §3.1 excludes it). **[CROSS]** | Medium | High | Addendum A-2 to be-plan T3: add `d.pop("task_summary", None)` to `_to_list_item` (between line 572 and 573 of be-plan §7). | BE |
| **RX-5** | `digest` payload weight on list endpoint (25k tokens/row). | High | High | D7 — strip `digest` at router layer; detail endpoint opt-in via `?include=digest`. | BE |
| **RX-6** | Tag-encoding mismatch between FE and BE. | High | Medium | D3 — repeat-param as FE contract; test case 12 (be-plan §8.1) pins it; fe-plan §6.1 keeps the same encoding. | BOTH |
| **RX-7** | `created_at` ISO-TEXT lexicographic sort pitfall near TZ boundaries. | Medium | Low | `_now_iso()` is the only writer; +00:00 invariant; test seeds 2 rows 1s apart and asserts order. | BE |
| **RX-8** | `manager._snapshot_repo` private-attribute access. | Low | Low | Project precedent at `daemon/routers/blueprints.py:336`; follow-up could promote to public accessor. | BE |
| **RX-9** | UUID-validation pattern diverges from `_validate_project_id` (inconsistent 400 bodies). | Low | Low | Reuse the `uuid.UUID(...)` try/except; copy the 400 detail string verbatim. | BE |
| **RX-10** | Status-chip contrast fails WCAG-AA in light mode. | Low | Medium | design-spec §6.7 already specifies `font-weight: 600` for `.status-active` / `.status-running` in light mode. | FE |
| **RX-11** | Drawer's `mat-drawer` focus trap blocks the Apply button. | Medium | Medium | Use `MatDrawer` (not `MatSidenav`); `[opened]` binding only; do not call `cdkTrapFocusAutoCapture`. | FE |
| **RX-12** | Digest fetch fails → user sees blank section with no error state. | Medium | Medium | brief §4(e) — render dedicated error block in the digest section with Retry. | FE |
| **RX-13** | SnapshotService.list() in-flight when filter changes → race (older response overwrites newer). | Medium | Medium | `switchMap` OR track a request-id signal and discard stale responses. | FE |
| **RX-14** | `SettingsService.getSnapshotUsageMetrics` becomes dead code after FE lands (hits the deprecated endpoint). | Low | High | Optional follow-up: delete after legacy endpoint removal (1 release window per be-plan D8). Do NOT delete now — keeps deprecation cycle honest. | BE / DEFERRED |
| **RX-15** | Indexes inadequate for new filter combos at scale (agent, age, tag). | Medium | Low | Existing `(project_id, status)` index covers the dominant case; others are bounded by `limit`. Phase-2 backlog (no index in v1). | BE / DEFERRED |
| **RX-16** | R12 GIN index on `domain_tags` (PG) is the rider-(f) deferred item. | Low | Low | Phase-2 backlog per be-plan §10; SQLite keeps the Python scan. | BE / DEFERRED |
| **RX-17** | `tags` chip input fires one refetch per keystroke. | Low | High | 250ms debounce via a `setTimeout`-based effect in `SnapshotsTableComponent` (fe-plan §5.3). | FE |
| **RX-18** | Project list not yet populated when user opens `/snapshots` cold. | Medium | Medium | Inject `ProjectService`; call `projectService.listProjects()` in `ngOnInit` if `projects().length === 0`. | FE |

**Dedup notes:** R1 (be-plan, digest weight) and R8 (fe-plan, digest
> 200KB) are both about the digest payload — collapsed to RX-5.
R3 (be-plan, PG/SQLite JSONB drift) and the tag parity test in
fe-plan are the same surface — collapsed into RX-6. R2 (be-plan,
tag encoding) and the FE repeat-param decision are the same —
collapsed into RX-6.

---

## 6. Merge / PR + docs sequencing

### 6.1 Single PR with phase-sliced commits

**Recommendation: ONE feature branch (`feature/snapshot-uiux`),
ONE PR, with commits sequenced per phase so review is phase-sliced.**

| Commit | Title | Files | Phase |
|--------|-------|-------|-------|
| **`c0`** | **`docs(planning): snapshot-uiux plan package (3 files: be-plan.md + fe-plan.md + sequencing.md; amendment pass 4 escalation)`** | `.agents/shared/planning/snapshot-uiux/be-plan.md`, `fe-plan.md`, `sequencing.md` (plan-only files; no source) | **P0 (LAND FIRST — pass 4 note #12)** |
| `c1` | `be(routers): add /api/snapshots list/detail/metrics + repo list_with_filters` | `daemon/routers/snapshots.py`, `daemon/routers/snapshot_schemas.py`, `daemon/repositories/snapshot/repository.py`, `daemon/routers/settings.py` (deprecated proxy — incl. `Response` import per pass 4 #5; `_proxy_to_snapshots_metrics` defined in `snapshots.py` and IMPORTED by `settings.py` per pass 4 #6), `daemon/routers/__init__.py` (1-line re-export — `from .snapshots import router as snapshots_router`, the convention at `__init__.py:3-27`; single import surface for `daemon/api.py` + `tests/unit/routers/test_snapshots.py`), `daemon/api.py` | P1 |
| `c2` | `be(tests): router 26 cases (incl. case 23 = settings toggle pin) + repo 10 cases + repo-ext 7 cases + search-ext 1 case` | `tests/unit/routers/test_snapshots.py`, `tests/unit/test_snapshot_list_with_filters.py`, `tests/unit/test_snapshot_repository.py`, `tests/unit/test_snapshot_search_service.py` | P2 |
| `c3` | `be(docs): CHANGELOG entry for /api/snapshots + metrics relocation` | `CHANGELOG.md` | P1 tail |
| `c4` | `fe(model+service): SnapshotModel + SnapshotService with buildParams/computeAgeCutoff` | `frontend/src/app/models/snapshot.model.ts`, `frontend/src/app/models/index.ts`, `frontend/src/app/services/snapshot.service.ts` | P3 |
| `c5` | `fe(routing): register /snapshots route + gear menu Snapshots item after Settings (before conditional appends — app.ts:747/:792, amendment #7)` | `frontend/src/app/app.routes.ts`, `frontend/src/app/app.ts` | P3 |
| `c6` | `fe(relocation): add functional /snapshots page (toggle + metrics strip + table + drawer) AND remove the snapshot blocks from /settings — ONE commit (atomicity mandate, amendment #6d)` | `frontend/src/app/pages/snapshots/*`, `frontend/src/app/components/snapshot-detail-drawer/*`, `frontend/src/app/pages/settings/settings.component.html` (delete :242-321, :323-358), `settings.component.ts` (delete :12, :107-142, :278-279, :715-810), `settings.component.spec.ts` (delete :1345-1533 + the 3 mock lines at :1566-1570 — amendment #1) | P3 + P4 — **combined commit covers the P3+P4 relocation; review it as one atomic unit** |
| `c7` | `fe(tests): 4 new spec files + settings-clean regression + route/menu regression` | `frontend/src/app/pages/snapshots/snapshots.component.spec.ts`, `snapshots-table.component.spec.ts`, `frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.spec.ts`, `frontend/src/app/services/snapshot.service.spec.ts`, `settings.component.spec.ts` (extend), `app.component.spec.ts` (extend) | P5 |

The split-PR alternative is rejected because: the relocation (now
combined into `c6` per the atomicity mandate — amendment finding #6d)
makes `c4-c5` not navigable in isolation (the gear menu points
at an empty page); merging the new-page-functional commit and the
Settings-deletion commit into ONE (noted as covering the P3+P4
relocation) preserves phase-slice reviewability without ever leaving
the toggle/metrics UI homeless; a single PR keeps the deprecation
cycle honest and avoids 2 reviewer handoffs. *(Amendment note: the
original c6+c7 split was merged into the new c6 per finding #6d; the
test commit was renumbered c8 → c7.)*

### 6.2 CHANGELOG entry content

> ## [Unreleased]
> ### Added
> - **`GET /api/snapshots`** — paginated, filterable list of snapshots
>   (8 sort keys, filters for `project_id` / `agent` / `status` (multi) /
>   `tags` (multi) / `created_after` / `created_before` / `limit` /
>   `offset`). Response envelope: `{"items": [...], "total": N}`.
> - **`GET /api/snapshots/{id}`** — single snapshot detail.
>   `?include=digest` opts into the unbounded digest payload
>   (default: `digest: {}`).
> - **`GET /api/snapshots/metrics`** — R16 monitoring counters
>   (capture + warm-spawn counts). This is the new canonical home
>   for the metrics surface; replaces the old settings-router
>   endpoint.
> - **FE:** new global `/snapshots` page (gear menu, bookmarks icon,
>   peer of `/settings` / `/schedules`).
> - **FE:** relocated `Agent Snapshots` toggle + `Snapshot Usage
>   Metrics` strip from `/settings` to `/snapshots`.
> - **FE:** `SnapshotService` (`providedIn: 'root'`) owns all
>   snapshot HTTP calls.
> ### Deprecated
> - **`GET /api/settings/snapshot-usage-metrics`** — kept as a
>   1-line re-export for one release window. `Deprecation: true`
>   header set. Removal planned for the next minor release
>   (follow-up ticket to be filed).
> ### Migration
> - External callers of the deprecated metrics endpoint should
>   migrate to `GET /api/snapshots/metrics`. The response body
>   is identical; the URL is the only change.

### 6.3 Legacy metrics endpoint removal — follow-up ticket framing

After the FE has shipped AND a CHANGELOG note has been live for one
release window, file a separate ticket:

> **Title:** "Remove deprecated `GET /api/settings/snapshot-usage-metrics`"
> **Scope:** `daemon/routers/settings.py:702-738` — delete the
> `get_snapshot_usage_metrics_deprecated` handler and the
> `_proxy_to_snapshots_metrics` private helper. Drop the
> `deprecated=True` flag. Run grep for external callers; if any
> remain, defer the removal to the next release.
> **Acceptance:** `grep -rn "snapshot-usage-metrics" frontend/ daemon/`
> returns zero matches in production code; only CHANGELOG history
> may reference the path.

**Optional, do NOT do now:** the `SettingsService.getSnapshotUsageMetrics`
method (still called by nobody after FE ships) can be deleted at the
same time as the endpoint removal. Defer until the deprecated endpoint
is gone, otherwise the deprecation cycle is broken.

### 6.4 Green-light gate + **4-GREEN merge gate (PINNED)**

> **This is PLAN-ONLY until the user approves the sequencing in
> this file.** Implementation follows immediately upon approval.
>
> **Conditions to start implementation:**
> 1. User signs off on §1 (the four D-* resolutions are the contract
>    surface; any pushback invalidates downstream phase planning).
> 2. User confirms the §2.3 wall-clock plan (pair mode × 1 overnight
>    per amendment pass 3 blocker #3; single engineer × 2 overnights
>    remains a documented FALLBACK only — pair mode is the declared
>    run mode for the run-brief §7).
> 3. The `feature/unify-spawn-tools` branch owner acknowledges the
>    §3.2 rebase requirement (ack on Discord / PR thread is
>    sufficient; no code change required from them yet). Recorded
>    in the run-brief §7 as INFORMATIONAL (their branch is now
>    2 commits + a dirty tree per pass 4 note #9; no live ack needed
>    before we start).
> 4. Branch protection rules allow the 8-commit split per §6.1
>    (**c0 + c1–c7**; was 7 commits before pass 4 note #12 added c0
>    for the plan docs; the 7 implementation commits c1-c7 are
>    unchanged in shape — only renumbered c0 + c1-c7).
>    (No squash, no rebase-on-merge — preserves the phase boundary.)

**Merge gate (PINNED — 4-GREEN):** PR is mergeable when ALL FOUR
of these pass in CI on the tip of `feature/snapshot-uiux`:

1. **`cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json`** returns 0
   (per fe-plan §8.5 BUILD/TEST DISCIPLINE — never bare `npx tsc`).
2. **`cd frontend && npm test`** returns 0 (covers the 38 new FE
   cases + 3 regression pins from §4.2; no `--bail` failures).
3. **`cd /home/nea/ensemble-src-wt-snapshot-uiux && uv run pytest tests/unit/routers/test_snapshots.py tests/unit/test_snapshot_list_with_filters.py tests/unit/test_snapshot_repository.py tests/unit/test_snapshot_search_service.py`**
   returns 0 (covers the 44 unique BE cases from §4.1; 26 + 10 + 7 + 1). **Pytest gate idiom (pass 4 amendment, blocker #2):** worktree-rooted + `uv run pytest` (project's uv-managed `.venv`; the only CPython 3.13 in the repo; bare `pytest` or main-checkout root runs the wrong tree).
4. **`cd frontend && npx playwright test --config playwright.snapshots.config.ts e2e/snapshots.spec.ts`**
   returns 0 (covers the §4.3 automated Playwright spec — all 10
   steps + 11a-11c; the drawer-error path uses route-interception
   `page.route('**/api/snapshots/*', route => route.abort())` to
   force the failure deterministically). **Playwright gate idiom (pass 4 amendment, blocker #3a + #3c):** invoked from INSIDE `frontend/`; spec path is `e2e/snapshots.spec.ts` (relative to `frontend/`; the base `playwright.config.ts:4 testDir: './e2e'` is the anchor); the dedicated `--config playwright.snapshots.config.ts` (per §4.3.1) launches a worktree-rooted daemon on strict ports 18079/14199 with `reuseExistingServer: false` — NEVER silently e2e the live 8079/4199.

Post-merge manual eyeball is **NON-GATING** (amendment pass 3 blocker #2).

**Once all 4 conditions are met AND the 4-GREEN merge gate is
green on the tip of `feature/snapshot-uiux`:** developer commission
(T1-T10 of be-plan §11 + T1-T26 of fe-plan §10) starts in the P0
→ P1 → ... → P6 order under the pair-mode declaration (run-brief
§7).

---

## 7. Run brief (unattended overnight — pair mode)

> **Purpose.** Compact handoff for the developer commission. Read this BEFORE spinning up the pair. Not a new plan — just the operational anchor.

**Run mode (PINNED, blocker #3; refined pass 4 note #11).** 2 developer agents in parallel — BE lane (P1 → P2) + FE lane (P3 → P4 → P5); P6 (automated Playwright e2e, §4.3) is a serial handoff after both lanes converge. Target **≈13–14.5h wall-clock overnight** (14.5h fits a long night; the §2.3 cut line is the pressure valve if it slips). Serial / 2-overnight is a documented FALLBACK only (see §2.3).

**c0 = plan docs land FIRST (pass 4 note #12).** Before any implementation begins, the giter agent commits the 3 planning files (`be-plan.md` + `fe-plan.md` + `sequencing.md` — the amendment pass 4 final state) as commit `c0` on `feature/snapshot-uiux`. **No developer commission starts until c0 is on the branch.** Rationale: protects the planning corpus from being raced by a dev who opened the worktree; c0 is a no-op from a runtime / build standpoint (it only adds the plan files to the branch); the dev's first action after c0 lands is `git pull` to get the latest plan-package SHA, then P2.5 toolchain bootstrap.

**Toolchain bootstrap (P2.5 — runs ONCE before P1+P3 split, pass 4 blockers #2 + #3b).** From a clean shell, in this order:

```bash
# 1. Sync the worktree's uv-managed .venv (3.13; only CPython that
#    matches the lazy-annotation target). MANDATORY before any pytest
#    gate. Skipping this runs the main-checkout's .venv against the
#    worktree's source — silent wrong-tree failure.
cd /home/nea/ensemble-src-wt-snapshot-uiux && uv sync

# 2. Install FE devDependencies from the locked package.json.
#    `npm ci` is lockfile-exact (vs `npm install` which may update
#    the lockfile). Hard constraint: no new dependencies permitted.
cd frontend && npm ci

# 3. Install Playwright browser binaries (one-time per host).
cd frontend && npx playwright install --with-deps chromium
```

The `uv sync` line is the **gate from pass 4 amendment blocker #2** — every pytest invocation that follows MUST use `uv run pytest` (the uv-managed `.venv`; bare `pytest` or main-checkout root runs the wrong tree). The `npm ci` line is the **gate from pass 4 amendment blocker #3b** — no new dependencies permitted; the e2e spec uses ONLY existing `playwright/test` and `@angular/material/*` modules. The P2.5 row in §2.1 pins the 0.25h estimate and the entry-criterion verify (the `uv run python -c "import daemon; print(daemon.__file__)"` resolution check + `npm ls playwright` + `ls ~/.cache/ms-playwright/chromium-*`).

**Pytest gate idiom (PINNED, pass 4 blocker #2).** Every pytest gate command MUST be worktree-rooted and use `uv run pytest`. The sanctioned form:

```bash
cd /home/nea/ensemble-src-wt-snapshot-uiux && \
  uv run pytest tests/unit/routers/test_snapshots.py \
                  tests/unit/test_snapshot_list_with_filters.py \
                  tests/unit/test_snapshot_repository.py \
                  tests/unit/test_snapshot_search_service.py
```

**FORBIDDEN patterns** (silently run the wrong tree, or fetch rogue npm packages):

```bash
# FORBIDDEN — main-checkout root, wrong tree:
cd /home/nea/ensemble-src && pytest tests/unit/...

# FORBIDDEN — bare pytest, no .venv activation:
pytest tests/unit/...

# FORBIDDEN — bare npx outside a package dir (cf. fe-plan §8.5):
npx playwright test e2e/snapshots.spec.ts
```

**Playwright gate idiom (PINNED, pass 4 blockers #3a + #3c).** Every Playwright invocation MUST be from inside `frontend/` AND load the dedicated config (so the worktree's daemon is launched on strict ports, not the live 8079/4199):

```bash
cd frontend && npx playwright test \
  --config playwright.snapshots.config.ts e2e/snapshots.spec.ts
```

The dedicated config is `frontend/playwright.snapshots.config.ts` (new file per §4.3.1; launches worktree-rooted backend on port 18079 + frontend on 14199 with `reuseExistingServer: false`; API target overridden via env). Path `e2e/snapshots.spec.ts` is relative to `frontend/` (the base `playwright.config.ts:4 testDir: './e2e'` is the anchor).

**Flaky-test rule.** **1 retry then halt with a handoff note.** Any test that fails twice across the 4-GREEN merge gate (§6.4) after a clean re-run halts the commission; the agent writes a handoff note into `agents/shared/handoff/` (project standard) and surfaces it on completion.

**§6.4 gate acks:** (a) §1 contract reconciliation — user signed off; (b) §2.3 wall-clock — user confirmed pair mode (refined pass 4 note #11: ≈13–14.5h wall-clock, 14.5h fits a long night); (c) **§3.2 rebase handshake with `feature/unify-spawn-tools` is INFORMATIONAL only** — their branch is now 2 commits on `ac399874` (`62c33c40` + `2fa92fa8` per pass 4 note #9) plus a residual dirty tree; we do NOT block on their ack, the §3.4 side-by-side table is enough when they eventually carry commits, AND the §3.5 mirrored contingency (rebase-onto-them if they land first) covers the inverse order; (d) §6.1 commit split — now **c0 (plan docs) + c1–c7** (was 7 commits before; pass 4 note #12 adds c0 for plan docs as the first commit, BEFORE any implementation begins — giter task; no dev races it) — branch protection must allow it (no squash, no rebase-on-merge).

**Digest-fetch ownership pin (resolves fe-plan §5.4 vs §6.4).** **The DRAWER component (`SnapshotDetailDrawerComponent`) owns the lazy digest fetch** — it fires `getById(id, { includeDigest: true })` ONLY on the explicit user "Show digest" click (`onToggleDigest()` handler per fe-plan §5.4; "lazy on explicit user action" rule per §6.4). **The host (`SnapshotsComponent`) NEVER pre-fetches the digest** — no `getById(..., { includeDigest: true })` on `ngOnInit` / row-click; only the drawer's user-driven toggle does. Pinned by drawer spec case (h) "lazy digest — NO digest request fires on drawer open" (fe-plan §8.1 row 3).

**P6 + 4-GREEN merge gate.** P6 = `frontend/e2e/snapshots.spec.ts` (Playwright spec plan, 10 numbered steps + 11a-11c; §4.3; **path corrected in pass 4 amendment blocker #3a** — the base `playwright.config.ts:4 testDir: './e2e'` is relative to `frontend/`, so the spec lives at `frontend/e2e/snapshots.spec.ts`, NOT `tests/e2e/...`). Step 11c forces the drawer-error path deterministically via `page.route('**/api/snapshots/*', route => route.abort())`. Invocation is `cd frontend && npx playwright test --config playwright.snapshots.config.ts e2e/snapshots.spec.ts` (dedicated config per §4.3.1 — port hygiene, worktree-rooted daemon, strict ports 18079/14199 with `reuseExistingServer: false`). Merge gate = **tsc + jest + pytest + Playwright e2e ALL GREEN** (§6.4; pytest gate idiom per blocker #2: worktree-rooted + `uv run pytest`); post-merge manual eyeball is **NON-GATING** (blocker #2).

---

**End of sequencing.md.** Coordination document only. The two
specialist plans (`be-plan.md`, `fe-plan.md`) remain the source of
truth for their respective lanes; this file is the bridge.
etById(..., { includeDigest: true })` on `ngOnInit` / row-click; only the drawer's user-driven toggle does. Pinned by drawer spec case (h) "lazy digest — NO digest request fires on drawer open" (fe-plan §8.1 row 3).

**P6 + 4-GREEN merge gate.** P6 = `frontend/e2e/snapshots.spec.ts` (Playwright spec plan, 10 numbered steps + 11a-11c; §4.3; **path corrected in pass 4 amendment blocker #3a** — the base `playwright.config.ts:4 testDir: './e2e'` is relative to `frontend/`, so the spec lives at `frontend/e2e/snapshots.spec.ts`, NOT `tests/e2e/...`). Step 11c forces the drawer-error path deterministically via `page.route('**/api/snapshots/*', route => route.abort())`. Invocation is `cd frontend && npx playwright test --config playwright.snapshots.config.ts e2e/snapshots.spec.ts` (dedicated config per §4.3.1 — port hygiene, worktree-rooted daemon, strict ports 18079/14199 with `reuseExistingServer: false`). Merge gate = **tsc + jest + pytest + Playwright e2e ALL GREEN** (§6.4; pytest gate idiom per blocker #2: worktree-rooted + `uv run pytest`); post-merge manual eyeball is **NON-GATING** (blocker #2).

---

**End of sequencing.md.** Coordination document only. The two
specialist plans (`be-plan.md`, `fe-plan.md`) remain the source of
truth for their respective lanes; this file is the bridge.
only. The two
specialist plans (`be-plan.md`, `fe-plan.md`) remain the source of
truth for their respective lanes; this file is the bridge.
