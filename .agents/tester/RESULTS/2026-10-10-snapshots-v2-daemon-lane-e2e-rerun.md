# Snapshots v2 Redesign — DAEMON-LANE E2E RE-RUN (rebased spec @ 64b351b8b) — VERDICT: ❌ GREEN CRITERIA NOT MET — 11/12 PASS; sole red = TEST-SIDE response-arming race (§2.3 wire contract PROVEN CORRECT — not a product regression)

Date: 2026-10-10 · Re-run after spec rebase commit `64b351b8b` ("test(e2e): rebase 5 v1 snapshots legs to Design A v2 contracts (pin 53fa39ec)", sole file `frontend/e2e/snapshots.spec.ts`, +212/−65) on branch `test/snapshots-v2-spec-rebase`, executed in the developer's worktree `/tmp/ens-wt-snapv2-rebase` (reused; left intact + clean; HEAD `64b351b8b49accf0ff5905e62cf41c0f44f484f3` verified).

Worker instance: `c60c890f-ab5d-4243-b3bb-fb2654847f33` (e2e-test, feedback 8/10 re-recorded). Evidence: `/tmp/ens-snapv2-e2e-evidence-rerun/` (report.json, html/, run-stdout.log, daemon-myrun.log, boot.log, canary.json, test-results/, **leg3-trace.zip + leg3-trace/**). Run #1 evidence untouched at `/tmp/ens-snapv2-e2e-evidence/`.

### Summary
- Total: 12 | Passed: 11 | Failed: 1 | Flaky: 0 | Skipped: 0 — `1 failed, 11 passed (1.5m)`, 89.4s, single invocation within `timeout 300`
- **All 5 rebased legs of interest: 4 GREEN outright (#2, #4, #6, #7 — incl. the real PUT round-trip), #3 green-on-merits but red-by-race (see below)**
- **Regression check: 7/7 previously-green legs (1, 5, 8, 9, 11a, 11b, 11c) stayed GREEN** ✓
- GREEN CRITERIA (12/12): **NOT met** — one spec-side predicate fix away

### Lane record
Daemon banner `Starting Ensemble v0.18.5` @ 127.0.0.1:18279; native PG16 :15532 (`/tmp/pg_e2e_snap_2933144` main, `…2933993` focused diagnostic); ng serve :14199; canary `status:'ready'` all-true; env fenced (`env -u POSTGRES_* -u DATABASE_URL`, `ENSEMBLE_SELF_ENV=dev`, placeholder `OPENAI_API_KEY`, 0 LLM activity); ports pre-checked free; teardown verified (0 listeners on 15532/18279/14199; protected ports untouched; `/tmp/pg_e2e_snap_*` removed; worktree `status --porcelain` empty at end, HEAD unchanged, no commits/pushes).

### Per-test results (12/12 executed)
| # | Test | Result | Evidence |
|---|------|--------|----------|
| 1 | gear menu → /snapshots; title, no page errors | ✅ PASS 7.3s | report.json expected |
| 2 | control row (H1+info+toggle pill+metrics+refresh), filter row, stats strip, 7-col table, paginator | ✅ PASS 2.3s | rebased leg green — v2 pill-button census (AC-2.1/2.3/5.1/5.2) |
| 3 | each filter drives v2 widget + fires GET w/ right param, 200, pageIndex reset, URL mirror (AC-6.3) | ❌ FAIL 3.2s | **false-negative** — see analysis |
| 4 | filter-to-empty → Clear filters; click drops every param + URL | ✅ PASS 2.8s | rebased leg green ("Clear all filters" contract) |
| 5 | drawer 7 sections, lazy digest, copy-id | ✅ PASS 2.7s | held |
| 6 | metrics popover: capture list ≥1 (coder), empty-state hidden, warmed 0 in headline | ✅ PASS 2.3s | rebased leg green (popover contract) |
| 7 | toggle pill ON→OFF: dirty → second click PUT {"enabled":false} 200, reload persists | ✅ PASS 3.3s | **census: `17:11:39 PUT /api/settings/snapshot-create 200`** |
| 8 | /settings no snapshots sections | ✅ PASS 2.4s | held |
| 9 | legacy endpoint Deprecation parity | ✅ PASS 0.2s | held |
| 11a | Esc closes drawer | ✅ PASS 3.5s | held |
| 11b | side-mode, no backdrop | ✅ PASS 4.8s | held (`.mat-drawer-backdrop` `toHaveCount(0)` intact) |
| 11c | aborted fetch → Retry recovery | ✅ PASS 3.7s | held |

### Leg #3 analysis — THIRD outcome: (a)-on-merits, red-by-spec-race (NOT §2.3 product regression)
The commission's two-outcome watch assumed (a) pass or (b) product regression. Actual: **wire behavior CORRECT, assertion helper racy.**

- **Trace ground truth** (`leg3-trace/` `0-trace.network`, focused `--grep "step 3" --trace on` diagnostic run): every filter write fires the list GET **twice** ~19ms apart (project `.050`/`.069`, agent `.486`/`.503`, tags `.731`/`.950`); the tags pair both carry `?project_id=…&agent=coder&tags=kind:implementation&limit=25&offset=0 200`. Per-filter refetch with correct params and debounce IS present — §2.3 satisfied.
- **Failure mechanism**: leg (c)'s `nextListResponse` uses a pathname-only predicate (`pathname === '/api/snapshots'`), so it resolved on the **agent sub-leg's echo** (`.503`, no `tags` param) → `getAll('tags')` received `[]` → red at spec :387. Deterministic 2/2 at the identical point (race, not flake).
- **Verbatim error**: `Error: expect(received).toContain(expected) // indexOf — Expected value: "kind:implementation" — Received array: []` @ `snapshots.spec.ts:387:71` (error-context `test-results/…a5045…/`).
- **Fix direction (developer's call, one predicate)**: make `nextListResponse` race-immune — full-query-param predicate (as the (d) second-chip wait already is) or arm the wait post-commit. No product change needed for §2.3.
- **Non-gating product observation**: the v2 page double-fetches the list per filter write (URL-mirror navigate re-triggers the fetch effect; same params, `listRequestId` dedup makes it harmless to correctness, but 2× list traffic). Also: the (d) status first-chip wait shares the same race exposure (second-chip wait is race-immune via param-count predicate).
- **Census-method limitation (labeled)**: daemon access log strips query strings — param fidelity evidence is browser-side trace; server census corroborates method+status only.

### Harness actions (all labeled harness)
`uv sync` in worktree (.venv absent, gitignored, tree stayed clean); focused leg-3 `--grep --trace on` diagnostic re-run (CLI artifact knob, zero test-content change); transient `test-results/` + `playwright-report/` gitignored outputs removed to restore as-found. No product/spec fixes applied (failure protocol).

### Action Needed
- [ ] Developer: one-predicate spec fix on `test/snapshots-v2-spec-rebase` (race-immune `nextListResponse`; consider the (d) first-chip wait too) → re-run should be 12/12
- [ ] Optional (developer judgment): the double-fetch-per-filter-write observation — non-gating, cosmetic traffic

### Documentation Updated (all unstaged in main checkout for the closing giter pass)
- [x] RESULTS/2026-10-10-snapshots-v2-daemon-lane-e2e-rerun.md — this report
- [x] PACKS.md — re-run commission section
- [x] LESSONS/2026-10-10-playwright-waitforresponse-arming-race.md — the predicate-race pattern + census limitation

### Code Changes Summary
None by this lane. Spec fix `64b351b8b` was the developer's (verified sole-file +212/−65). Worktree clean at close.

### Overall Status
- Pack @ 64b351b8b: ❌ 11/12 (GREEN CRITERIA not met) — sole red is spec-side race; §2.3 product behavior PROVEN correct
- Regression check: ✅ 7/7 held
- **READY assessment: one predicate fix from GREEN — product exonerated on the focus watch**
