# Snapshots v2 Redesign — DAEMON-LANE E2E (v1 12-test pack vs shipped v2) — VERDICT: ❌ PACK FAIL 7/12 (lane healthy; 5 failures = v1-contract divergence vs Design A)

Date: 2026-10-10 · Commission: execute the 12-test v1 e2e spec pack at FULL daemon depth against shipped v2 @ `c7b467444` (merged --no-ff to origin/latest, pushed). This closes **Pending item #1** of RESULTS/2026-10-10-snapshots-v2-reverification.md (the only unrun depth).

Worker instance: `c60c890f-ab5d-4243-b3bb-fb2654847f33` (e2e-test skill, feedback 7/10 recorded). Evidence retained at `/tmp/ens-snapv2-e2e-evidence/` (report.json, html/, run-stdout.log, daemon-myrun.log, boot.log, canary.json, daemon-pg-lane.log, test-results/ 5 failure dirs).

### Scope Decision
Single pack only: `snapshots_playwright_e2e` (registered, PACKS.md; spec `frontend/e2e/snapshots.spec.ts`, 12 tests; config `frontend/playwright.snapshots.config.ts`). No jest suites, no other playwright specs, no backend packs — this commission was the pending v1 daemon-lane depth, nothing else.

### Summary
- Total: 12 | Passed: 7 | Failed: 5 | Flaky: 0 | Skipped: 0
- Playwright: `5 failed, 7 passed (2.5m)`, 150.6s, single invocation within `timeout 300`
- All 5 failures diagnosed as **v1-spec contract obsolescence under Design A** — NOT lane defects, NOT v2 product bugs (discrimination evidence below)
- Quick fixes applied: none (failure protocol — product untouched; worktree clean at removal)
- Quarantined: 0 (failures are deterministic contract divergences, not flaky — quarantine class does not apply)

### Lane record (full daemon depth — REAL daemon + REAL PG + REAL API, no stubs)
- Worktree: `/tmp/ens-snapv2-e2e-wt`, detached HEAD = `c7b467444c6a63745499f97026abbcba85c8f3c8` ("feat(snapshots): Snapshots page v2 redesign per frozen Design A spec"); harness commits 5d411a395 (spec+config) and f19b4f705 (11a/11b rewrite) verified ancestors. Worktree removed post-run.
- Daemon: banner `Starting Ensemble v0.18.5` (worktree `daemon/__init__.py`; merge is post-v0.18.5 on latest, no bump yet), 127.0.0.1:18279, pid 2925641/2925652, graceful shutdown.
- PG: native PG16 (`/usr/lib/postgresql/16/bin`), port 15532, cluster `/tmp/pg_e2e_snap_2925596` (initdb -A trust), DB `ensemble_e2e_snap_2925596` — booted by the pack's own `frontend/scripts/boot-e2e-snapshots-daemon.sh`. Canary `/readyz`: `{"status":"ready","components":{database,queue_freshness,services,checkpoint_saver all true}}`.
- Frontend: `ng serve` dev-server :14199 (per config; no prod build), proxying /api+/ws → 18279. `npm ci` 897 pkgs; chromium-1223 cached (playwright 1.60.0).
- Env fence: `env -u POSTGRES_* -u DATABASE_URL`, `ENSEMBLE_SELF_ENV=dev`, `OPENAI_API_KEY=e2e-placeholder` (lifespan-only; zero LLM evidence in logs — never invoked). Seed: spec's own beforeAll psql INSERTs → 15532 only; effective (DOM "4 snapshots", "4 captures").
- Isolation: main checkout only `fetch origin` (no checkout/reset/stash/pull); protected ports 5432/5433/8088/9797/7979/8079 never bound; teardown 3-layer (boot-script traps + globalTeardown + verification) left ZERO listeners on 15532/18279/14199; `/tmp/pg_e2e_snap_*` + `data_e2e_snapshots/` removed. Leftovers: `/tmp/e2e_snapshots_logs/` (pack-canonical shared dir, predates run) + `/tmp/ens-snapv2-e2e-evidence/` (retained evidence).

### Per-test results (12/12 executed)
| # | Test | Result | Evidence |
|---|------|--------|----------|
| 1 | step 1 — gear menu → /snapshots; title + no page errors | ✅ PASS 8.2s | report.json expected |
| 2 | step 2 — header, toggle, metrics strip, filter bar, 8-col table, paginator | ❌ FAIL 7.6s | `getByRole('radio',{name:'Enabled'})` not found 5s — v2 has ZERO mat-radio (template grep=0); toggle is a button (`test-results/…976e5…/error-context.md`) |
| 3 | step 3 — each filter fires GET /api/snapshots with right param, 200, pageIndex reset | ❌ FAIL 22.9s | `waitForResponse` 20s timeout @ spec:254; census: 13 list GETs ≈ 1/page-load, none attributable to filter interactions (daemon-myrun.log) |
| 4 | step 4 — filter-to-empty shows Clear filters; click drops every filter param | ❌ FAIL 7.6s | `getByRole('button',{name:/Clear filters/i})` not found — v2 accessible name is "Clear all filters" (aria-label, component :277); DOM at failure shows button present + 0-snapshot state |
| 5 | step 5 — drawer 7 sections, lazy digest fetch, copy-id | ✅ PASS 3.0s | 7× `GET /api/snapshots/aaaa…0001 200` in census |
| 6 | step 6 — capture card ≥1 row; warmed card hidden-when-empty | ❌ FAIL 7.4s | `[data-test="metrics-capture-card"] ul.metrics-list li` expected 1 received 0 — data-test hook EXISTS (component :59) but `ul.metrics-list` markup removed (grep=0); metrics now header button "4 captures · 0 warmed" |
| 7 | step 7 — toggle Enabled→Disabled: dirty hint, PUT {"enabled":false}, 200, reload persists | ❌ FAIL 17.3s | radio precondition dead at 15s; **zero PUT /api/settings/snapshot-create** in census (never reached); v2 polls GET /api/settings/snapshot-create 200 ×10 |
| 8 | step 8 — /settings renders NO snapshots sections | ✅ PASS 2.6s | report.json expected |
| 9 | step 9 — legacy metrics endpoint 200 + Deprecation header + body parity | ✅ PASS 0.2s | `GET /api/settings/snapshot-usage-metrics 200` + `GET /api/snapshots/metrics 200` in census |
| 11a | step 11a — Escape closes drawer (post-D1 rewrite) | ✅ PASS 3.6s | rewritten test @ spec:548 present and green |
| 11b | step 11b — side-mode drawer, no backdrop, Esc + close | ✅ PASS 4.7s | `.mat-drawer-backdrop` `toHaveCount(0)` @ :590 held |
| 11c | step 11c — aborted detail fetch → drawer error + Retry recovery | ✅ PASS 3.8s | report.json expected |

### Failure analysis — v1-contract divergence map (developer fix input)
All 5 failures are the SAME class as the earlier D2 (backdrop contract, fixed by the 11a/11b rewrite): v1 spec legs encode v1 widget/interaction contracts that Design A replaced. Discrimination evidence that the lane is healthy: canary all-true; seed effective; all API 200s; **7 tests pass on the identical stack** including the drawer/side-mode/retry flows (5, 8, 9, 11a, 11b, 11c); each failure corroborated at source level:

1. **#2 + #7 — mat-radio toggle → button.** v1 asserted `getByRole('radio', {name:'Enabled'})`; v2 snapshots components contain ZERO mat-radio (template grep=0) — the toggle is a button ("Snapshot creation: ON", component :40/:44). #7 died at this precondition, never reaching its PUT assertion (census: zero PUT /api/settings/snapshot-create; v2 polls GET ×10 instead).
2. **#4 — "Clear filters" → "Clear all filters".** v1 regex `/Clear filters/i` cannot match the non-contiguous accessible name "Clear all filters" (aria-label, component :277). DOM at failure proves the button exists and the 0-snapshot filtered state is correct — assertion contract is stale, behavior is right.
3. **#6 — `ul.metrics-list` markup removed.** `[data-test="metrics-capture-card"]` hook kept (component :59) but the list markup is gone (grep=0); metrics surface is now the header button "4 captures · 0 warmed".
4. **#3 — per-filter refetch contract changed.** v1 expected each filter interaction to fire GET /api/snapshots with the right param; v2 census shows 13 list GETs ≈ one per page-load, none attributable to filter interactions — v2 applies filters without a refetch-per-filter (client-side or batched). The v1 behavioral contract is superseded; whether Design A specifies refetch-on-filter needs the developer's spec check before rewriting the leg.

**Verdict: lane + shipped v2 validated wherever v1 contracts still align (7/7 of the aligned legs PASS). The 5 failing legs need a developer decision: rebase onto Design A contracts or retire as superseded** — per failure protocol, nothing was fixed here.

### Harness notes
- No bash wrapper exists in `test/packs/` for this daemon-lane pack; PACKS.md registers the DIRECT playwright invocation — the config's webServer chain (boot script + ng serve) IS the wrapper. Reporter flags `--reporter=line,json,html` added for evidence capture (test content untouched).
- Config has `screenshot` off — per-test UI evidence is Playwright accessibility-DOM error-context snapshots (richer than PNGs); noted rather than re-run.
- Ports 18279/14199/15532 pre-checked free; never killed any occupant.

### Action Needed
- [ ] Developer: decide rebase-vs-retire for the 5 obsolete v1 legs (#2, #3, #4, #6, #7) — divergence map above with source lines; then a re-run commission of this same pack at c7b467444+fix
- [ ] Optional: comment-nit from reverification Pending #3 (11a mechanism attribution) can ride the same spec-fix commit

### Documentation Updated
- [x] PACKS.md — new commission section (daemon-lane run, pack status FAIL 7/12)
- [x] RESULTS/2026-10-10-snapshots-v2-daemon-lane-e2e.md — this report
- [x] LESSONS/2026-10-10-snapshots-v2-v1-e2e-contract-divergences.md — divergence map + lane recipe
- [ ] rules/ensure.md — no changes (user-maintained); no ensure.md Core items in blast radius (FE e2e lane)

### Code Changes Summary
None. Zero product/test-code modifications; worktree clean at removal; no commits.

### Overall Status
- Pack `snapshots_playwright_e2e` @ c7b467444: ❌ FAIL 7/12 (deterministic; 5 v1-contract divergences)
- Lane: ✅ proven healthy (first full-browser-depth run of this pack — prior 2026-10-06 attempt halted at FE compile)
- Reverification Pending #1: ✅ CLOSED (executed, verdict recorded)
- **Testing Complete: ❌ NOT READY — awaiting developer decision on the 5 obsolete legs (rebase vs retire), then re-run**
