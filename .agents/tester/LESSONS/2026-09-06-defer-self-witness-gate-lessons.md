# Lessons — Full Gate: fix/defer-self-witness-and-cleanup (2026-09-06)

Gate: `fix/defer-self-witness-and-cleanup` @ `f9ee8cc0` (base `afd7c387`) → `26a7e625` (test-ledger pin only).
23 dispatched workers + 4 attribution workers + 1 quick-fix. VERDICT: PASS (merge green-lit).

## 1. Distribution-name ≠ import-name (venv probe false blocker)
Ground-truth probe `import pytest_xdist` → ModuleNotFoundError ⇒ "venv broken" was a MISDIAGNOSIS.
`pytest-xdist` (distribution) imports as `xdist` (module). The venv was healthy all along; a full
gate wave was blocked ~5 min on a bad probe. **Rule: probe installed distributions via
`uv pip list` / `pytest --version` plugin line, or import by MODULE name.**

## 2. Worker reports can misread the quarantine ledger — leader must adjudicate against the ledger text
The P11 partition worker classified 7 failures as "BRANCH-SUSPECT ... NONE match the mission
settled-rename family (that family lives in mission-class tests)". QUARANTINE.md row 11 names the
EXACT 7 nodes in the exact partition (`tests/job_queue/`, regression_job_queue partition). The
node-for-node match was only visible by reading the row carefully at the leader level.
**Rule: when a worker claims "no quarantine match", re-verify against the QUARANTINE.md row text
yourself before dispatching attribution; the family description's file list is authoritative.**
(Sealed anyway by dual-commit A/B: 7F @ HEAD ≡ 7F @ base afd7c387, normalized-identical signatures.)

## 3. send_message burst > 15 saturates the daemon QueuePool — errors are FALSE NEGATIVES
A 19-way parallel `send_message` burst hit `QueuePool limit of size 5 overflow 10` on 4 sends; the
tool returned Error but the messages HAD queued (retry returned "already has a message in progress").
**Rule: on QueuePool TimeoutError from send bursts, do NOT immediately re-send — check whether the
message landed (the retry error text reveals it). Fan out waves ≤ 15 or accept one retry round.**

## 4. Pack EXPECTED_BRANCH defaults go stale — pin per dispatch
`fe_static_typecheck_build_test.sh` (and `constitution_drift_test.sh`) default EXPECTED_BRANCH to
the branch they were built for (feature/mission-class lineage). Run-1 false-DRIFT until pinned via
the pack's own env knob. **Rule: always pass `EXPECTED_BRANCH=<branch>` to EXPECTED_BRANCH-gated packs.**

## 5. Web automation pattern that worked (Playwright + Angular dev server)
Destructive-surface verification in a real browser on this repo:
- BE: `nohup ./dev.sh` (8079); FE: `cd frontend && nohup npm start` (4199, proxies api/ws → 8079).
- Driver: `npx playwright install` chromium, headless.
- Real component drives: `window.ng.getComponent(...)` via page.evaluate → call real component
  methods (e.g. `JobQueueIndicator.onForceCompleteHolder()`), route-intercept only the specific
  POST when the scenario is hard to seed.
- Discriminated-copy assertions: assert the full verbatim string AND assert the sibling branch's
  string is ABSENT (paused-note vs stalled-note zero cross-leak proof).
- Destructive discipline: verify RENDER then Cancel; refusal arms are non-destructive by design;
  never EXECUTE system cleanup; prefix all seeded entities (`webauto-`) and delete only those.
- Screenshots to /tmp/webauto-shots/ for the report.

## 6. Attribution arithmetic — baseline ledgers age fast on `latest`
P9 flagged 13 "NEW" failures with a provenance anomaly: their code sites pre-date the 2026-09-03
baseline ledger, yet the ledger arithmetic left no room. Cause: the ledger was minted at a
2026-09-03 commit; base `afd7c387` (7 days of `latest` movement later) already carried 13 more
failures. **Rule: partition baselines older than ~3 days on a fast-moving `latest` under-count;
treat "NEW but root-cause-pre-baseline" as latest-lineage drift and settle with one base A/B —
which is cheap (whole-file invocations, ~7s).**

## 7. Route-count ledger pins are branch-coupled churn — fix pattern
Branch added 2 WS4 routes ⇒ `tests/unit/test_phase5_jobs_router.py` route-count pin 10→12 failed.
Deterministic, mechanism-consistent, PASS-at-base. Fix = pin bump + comment naming routes + branch
(commit `26a7e625`, 5+/3−, test-only). Same class as the historical "count grew 9→10" precedent
in that docstring. Not a behavioral regression; do not block merge on it — land the pin.

## 8. N3 meta-test portability defect (found during attribution, pre-existing)
`tests/test_migration_api_comprehensive.py::test_manager_tests_pass` hardcodes
`cwd="/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble"` — inside a worktree the
inner pytest runs against the MAIN repo, so the test can never A/B across worktrees. Fix direction:
derive cwd from `__file__`. (Filed here; not branch-caused — empty branch diff on both files.)

## 9. Orphaned disposable PG DB (hygiene)
`ensemble_blob_prune_3c04971bc4c7` orphaned on the test-stack PG — consistent with a
hard-timeout-killed worker during a loaded partition run (cells drop their DB in `finally`, which a
SIGKILL bypasses). Safe to drop manually. Related: see
`2026-09-06-perf-matrix-run-unit-trap.md` for why that pack gets timeout-killed under load.
