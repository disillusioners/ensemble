# Snapshot-UIUX — Plan Overview

> **Synthesis document** (planner-aggregated from three worker-authored plans).
> The specialist files are the source of truth for their lanes; this overview
> indexes them, locks the cross-cutting decisions, and states the delivery gate.
>
> - **Feature:** Dedicated global Snapshots page (`/snapshots`) with gear-menu
>   entry, relocated R15 toggle + R16 metrics, new filterable snapshot list +
>   detail drawer, and the supporting read-only BE surface.
> - **Worktree:** `/home/nea/ensemble-src-wt-snapshot-uiux` — branch
>   `feature/snapshot-uiux` (design commits `2ca69147` spec, `0aba9924` mockup;
>   base `latest` @ `ac399874`).
> - **Status:** PLAN-ONLY. No source files modified; the three plan files below
>   are the entire output of this commission. Implementation starts only on
>   user green-light (§8).
> - **Date:** 2026-10-05 (planner synthesis; worker plans authored same day).
> - **Amended:** 2026-10-05 — review verdict **APPROVED-WITH-NOTES**: BLOCKING #1–#2 +
>   SHOULD-FIX #3–#11 + trivial 🟢 applied to the three worker files (amendment pass,
>   worker `b1dc2c63`); this overview synced (D-6/D-7, atomicity, menu order, touch-set, counts).
> - **Amended (iter 002):** 2026-10-05 — approver REJECTED iteration 001; BLOCKING §4.4 toggle-contradiction
>   + 4 test-breaking notes + wall-clock reconciliation applied (worker `9ed80d2e`); overview synced
>   (44-unique BE gate incl. existing repo suite, serial figure of that iteration —
>   superseded to 21.75h in iter 004 by P2.5, line counts).
> - **Amended (iter 003, FINAL CAP):** approver iteration-002 blockers (7) + run-brief applied
>   (worker `6d761301`); CLASS sweeps: 44-unique count rollup + edit-list completeness
>   (`daemon/routers/__init__.py` re-export); P6 → automated Playwright e2e; **pair mode declared**;
>   overview synced (counts, P6, run-mode, mockup banner).
> - **Amended (iter 004, ESCALATED FINAL — approver 3-cap; leader rulings applied):**
>   worker `9cfa6793` — uv/pytest worktree gates, Playwright dedicated-config +
>   `frontend/e2e/snapshots.spec.ts`, skeleton imports, `Response` import, proxy
>   placement ruling, drawer-ownership + page-owned-list rulings (2 new spec cases),
>   §3.5 mirrored contingency (their 2 commits), 13–14.5h pair figures, c0 plan-docs
>   commit; overview synced (44/38/41 counts, 21.75h serial, P2.5, informational gate 3).

---

## 1. Objective

Give snapshots a first-class home: a global `/snapshots` page (peer of
`/settings`, `/schedules`) hosting the snapshot-creation toggle (relocated
from Settings), the usage-metrics strip (relocated), and a NEW server-paginated
snapshot list with filters (project, agent, tags all|any, status multi, age
preset, sort) plus a detail drawer with lazy digest loading. BE adds a
read-only HTTP surface (`GET /api/snapshots`, `GET /api/snapshots/{id}`,
`GET /api/snapshots/metrics`) following existing router conventions — zero
schema work, writes stay tool-only.

## 2. Reading order (implementation)

1. **`sequencing.md` §1 — contract reconciliation (binding)** — resolves the
   seven BE↔FE/spec deltas (D-1…D-7) and adds two implementation addenda (A-1, A-2).
   Read FIRST; it overrides conflicting text in the sibling plans.
2. `be-plan.md` — BE lane (API contract, repo method spec, router skeleton,
   44 unique test cases, tasks T1–T10 ≈ 12h).
3. `fe-plan.md` — FE lane (file-by-file changes, signals/methods, UX states,
   38 spec cases + 3 pins, tasks ≈ 8.25h / ~11h padded).
4. `sequencing.md` §2–§6 — phases, coupling, consolidated tests, PR/CHANGELOG.
5. `design/design-spec.md` (pre-existing, pinned SHA `2ca69147…`) + mockup —
   visual language + AC numbering. **Mockup banner:** the mockup predates the
   amendment — implementation reads fe-plan + the amendment; the mockup is a
   review artifact only.
6. `design/design-spec-amendment-contract-reconciliation.md` (designer's lane,
   commit `5cce3be3`) — sanctioned spec-side amendment reconciling the
   design-spec with sequencing §1 D-1…D-7. Reference only.

## 3. Deliverables map

| File | Author | Lines | Contents |
|------|--------|-------|----------|
| `be-plan.md` | plan-worker-snapshot-be (`plan-creation`) | 1157 | 10 decisions (D1–D10), API contract tables, `list_with_filters` repo spec + compat wrapper, router skeleton, 44 unique BE test cases, task breakdown ~12h |
| `fe-plan.md` | plan-worker-snapshot-fe (`plan-creation`) | 883 | 5 new + 5 edited files, dangling-reference checklist, component/service structure (page-owned list fetch, presentational table, stateful drawer), UX states, 38 FE spec cases + 3 regression pins, task breakdown ~8.25h |
| `sequencing.md` | plan-worker-snapshot-seq (`roadmap-strategy`) | 833 | Binding contract reconciliation (D-1…D-7, OK-1…OK-4, A-1/A-2), phase plan + wall-clock math + P2.5 toolchain bootstrap, §3.5 mirrored rebase contingency, consolidated risks, automated Playwright e2e plan (§4.3 + §4.3.1 dedicated config), §7 run brief (pair mode), c0+c1–c7 commit slicing + CHANGELOG |

## 4. Locked decisions (cross-cutting)

| # | Decision | Source |
|---|----------|--------|
| 1 | Global `GET /api/snapshots` with optional `project_id` filter (not per-project path) | be-plan D1 |
| 2 | `{items, total}` envelope; list items = `to_dict()` minus `digest` **and** minus `task_summary` (addendum A-2); detail `?include=digest` opt-in, default `digest: {}` | be-plan §4 + seq D-1/D-4 |
| 3 | Filters: `agent` (exact match), repeat-param `tags` + `tag_mode=all\|any`, multi `status` (default = all 5), `created_before/after` (ISO-validated), 8-key pattern-validated `sort` (default `created_at_desc`), canon pagination w/ double-clamp | be-plan §4.1 |
| 4 | Metrics relocated to `GET /api/snapshots/metrics`; legacy `/api/settings/snapshot-usage-metrics` kept as `deprecated=True` re-export for one release | be-plan D8, seq OK-1 |
| 5 | `list_active_by_project` → 3-line compat wrapper around new `list_with_filters` (spawn-hot WARM path byte-compatible) | be-plan D2 |
| 6 | FE: one page host + self-fetching table sub-component + dedicated drawer (NO section registry); gear-menu entry directly after Settings, BEFORE the conditional Database/Maintenance appends (`app.ts:747`/`:792`); `SettingsService` snapshot methods stay (page re-injects) | fe-plan §5.5, §4 |
| 7 | Relocation deletes settings blocks `html:242-321` + `:323-358`, ts signals `:12,107-142,278-279,715-810`, spec `:1345-1533` + the 3 mock lines `:1566-1570` (Timezone suite `:1535-1996` UNTOUCHED — amendment #1); settings keeps everything else; deletion is ATOMIC with the functional page (same commit) | fe-plan §4.2–4.3 |
| 8 | Stub-free v1: NO per-agent `snapshot_enabled` surface anywhere (field lands with `feature/unify-spawn-tools`) | both plans + seq §3.3 |
| 9 | FE sort offers 4 of BE's 8 keys (`created_at_desc/asc`, `title_asc`, `status_asc`); `warm_desc` dropped (no BE join in v1) | seq D-3 |
| 10 | URL query-param filter sync skipped in v1 (design-spec AC-5.2) | fe-plan §6.5 |
| 11 | Pagination + age presets (seq D-6/D-7): BE `limit` default 50 (ge=1, le=200), FE paginator pageSize 25 with [10,25,50]; age presets 24h/7d/30d/all, default `all`; design-spec's 25/50 + 30d-default superseded, 90d dropped — spec-side amended by the designer's reconciliation file | seq §1 D-6/D-7 |

## 5. Phases & effort

| Phase | Objective | Hours |
|-------|-----------|-------|
| P0 | Contract freeze (read seq §1; both lanes) | 0.5 |
| P1 | BE foundation: router + schemas + repo method + registration + metrics relocation | 7.0 |
| P2 | BE tests green (**44 unique** — incl. RUNNING the EXISTING `test_snapshot_repository.py`, which carries the 7 repo-ext cases) | 4.5 |
| P2.5 | Toolchain bootstrap (`uv sync` + `cd frontend && npm ci` + playwright chromium — **no new deps, hard constraint**) | 0.25 |
| P3 | FE scaffold + service + route + menu (parallel with P1+P2) | 2.5 |
| P4 | FE table + drawer + filters + toggle + relocation (**atomic**: the Settings deletion rides the SAME commit as the functional page — P4 owns it) | 4.5 |
| P5 | FE specs + tsc/jest green (38 cases + 3 regression pins = 41) | 1.5 |
| P6 | Automated Playwright e2e — `frontend/e2e/snapshots.spec.ts` via dedicated `playwright.snapshots.config.ts` (strict ports, `reuseExistingServer: false`); steps 1–10 + 11a–11c; 11c via route-interception | 1.0 |

**Wall-clock verdict (seq §2.3):** single engineer serial = **21.75h (phase-sum
0.5+0.25+7.0+4.5+2.5+4.5+1.5+1.0 incl. P2.5) — does NOT
fit one overnight** — serial = 2 overnights (P0–P3 night 1, P4–P6 night 2; P3
overlapping P1+P2 saves at most 2.5h → 19h best case, still 2 nights);
pair (BE ∥ FE) ≈ **13–14.5h — fits one long overnight** (P2.5 absorbed in
the P0 joint block).

**RUN MODE (declared, leader ruling): PAIR** — 2 developer agents in parallel
(BE lane + FE lane), 1 overnight, ≈13–14.5h wall-clock — the wall-clock gate is
thereby ticked.
Serial/2-overnight remains a documented FALLBACK only (seq §2.3 + §7 run brief).

Cut line if time runs short:
the metrics strip may slip to its own commit ONLY PRE-relocation (once the relocation commit lands, the strip must be in it — no state where the
metrics UI exists nowhere); everything else is merge-blocking.

## 6. Coupling — `feature/unify-spawn-tools` (job `7e6a62db`)

- **BE overlap: ONE shared file** — `daemon/routers/settings.py` (their committed
  hunks `@@ -649` doc + `@@ -713` in the metrics handler we deprecate; auto-merge
  expected, **re-verify pre-merge**). `repository.py`, `api.py`, and our new router
  files stay disjoint (their tree also touches `models.py` /
  `snapshot_metrics_service.py` / `snapshot_search_service.py` — files our plan
  does not edit). (seq §3.1/§3.4 as amended)
- **FE overlap: `settings.component.html` only** — their committed branch
  (2 commits `62c33c40`+`2fa92fa8` on `ac399874`, residual dirty tree) carries
  three hunks (`:249`, `:293`, `:330`): first two inside our deleted `:242-321`
  toggle block, the third inside our deleted `:323-358` metrics block →
  **they MUST rebase onto `feature/snapshot-uiux` after we land** (verbatim
  coordination note in seq §3.2 — paste into PR description). **Mirrored
  contingency (seq §3.5):** if THEY merge to `latest` first, WE rebase — expected
  conflict surface `settings.py` (merge BOTH hunks + our proxy) +
  `settings.component.html` (our deletion wins; their per-agent copy re-derives
  onto `/snapshots` per the standing merge-order invariant).
- **Merge-order invariant (binding):** snapshot-uiux lands → unify-spawn-tools
  rebases + lands → a third commission adds the per-agent toggle UI (couples
  to their `daemon/registry.py` field).

## 7. Test & acceptance summary

- **BE:** **44 unique cases** (be-plan §8) = 26 router (case 23 IS the §8.6
  settings-toggle pin — counted ONCE, not additionally) + 10 new-repo + **7 repo-ext
  onto the EXISTING `test_snapshot_repository.py`** + 1 search-ext; incl. PG `@>` drift
  pin, SQLite tag parity, `task_summary`-exclusion, metrics fail-soft + manual
  Deprecation/Sunset headers, ISO 1s-apart sort pin, case-21 422 semantics.
  **Overnight gate runs BOTH new files AND the existing repo suite** (new-file counts
  alone undercount).
- **FE:** 4 new spec files — **38 cases** (page-host 13 incl. the 2 new
  agent-filter-population cases / table 9 / drawer 10 / service 6, per fe-plan
  §8.1) + **3 regression pins** = 41; `tsc --noEmit` + jest via frontend-local
  binaries ONLY.
- **Automated Playwright e2e (P6, replaces manual browser steps):** 13 steps
  (1–10 + 11a Escape-close, 11b backdrop-close, 11c drawer-error via
  route-interception) as automated assertions + design-spec AC citations
  (seq §4.3) — run via `frontend/playwright.snapshots.config.ts` (dedicated
  worktree backend+frontend pair, strict ports, `reuseExistingServer: false` —
  never the 8079 main-checkout daemon) — gear-menu load, per-filter wire-param verification, filtered-empty
  CTA, drawer + lazy digest + copy, metrics strip, toggle persistence,
  settings-clean, legacy deprecation headers. **Merge gate = 4-GREEN: tsc + jest +
  pytest (`uv run pytest`, worktree-rooted after `uv sync`) + Playwright e2e**; post-merge manual eyeball optional,
  NON-GATING.

## 8. Green-light gate (seq §6.4)

1. User signs off on the D-1…D-7 contract resolutions.
2. Wall-clock: **TICKED — pair mode declared** (leader ruling; 2 developer agents,
   1 overnight, ≈13–14.5h; seq §2.3 + §7 run brief). Fallback: serial × 2 overnights.
3. `unify-spawn-tools` rebase handshake — **INFORMATIONAL: do NOT block** (their
   branch is 2 commits + a dirty tree; the seq §3.5 mirrored contingency covers
   either merge order).
4. c0+c1–c7 phase-sliced PR plan accepted (no squash-merge; the relocation
   rides ONE atomic commit per seq §6.1 as amended). **c0 = plan docs commit,
   lands FIRST (giter task; no dev races it).**

**Top risks:** RX-1 contract deltas unread (mitigated: seq §1 read-first rule),
RX-2 overnight overrun (cut line above), RX-3 rebase conflict (coordination
note), RX-5 digest weight (list strips digest+task_summary; detail opt-in),
RX-6 tag encoding (repeat-param pinned by tests both sides).

---

*Plan inventory: `be-plan.md`, `fe-plan.md`, `sequencing.md` are untracked
worktree files (plan-only, no commits made by this commission). Pre-existing
design artifacts: `design/design-spec.md` + `design/mockups/snapshots-page.html`
(committed `2ca69147` / `0aba9924`).*
