# Test Report: fix/hide-polling-access-logs — verification (focused tests + live smoke)

Date: 2026-09-04
Instance IDs: worker f65a6aed-acd0-4ca8-8035-5c8c67b6d691 (verify-hide-logs), skill `test-pack-execution`
Worktree: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-hide-logs` @ `673270ec` + uncommitted `daemon/api.py` (+13) + untracked `tests/unit/test_selective_access_log_middleware.py` (~188 lines)
Mode: READ-ONLY verification — no source/test files modified, no stash/checkout/commit.

## Summary
- Focused pack: **6/6 PASS** (exit 0, 1.20s)
- Base attribution (api.py 2047 lines @ `673270ec`): **CONFIRMED** → `test_api_module_is_small` failure at base is plausible/pre-existing
- Live smoke (worktree daemon, 127.0.0.1:8090, disposable PG in /tmp): **PASS** — suppression + non-suppression both proven
- Quick fixes applied: 0 (not authorized — read-only run)
- Quarantined: 0 touched

## Scope Decision
> Small, isolated change (1 production file +13 lines, 1 new test file) → scoped verification only: focused unit pack + cheap base spot-check + live smoke. Full suite not warranted. Main worktree / port 8079 / port 8088 untouched per mandate.

## Step 1 — Focused pack
`timeout 300 uv run pytest tests/unit/test_selective_access_log_middleware.py -v`
→ **6 passed in 1.20s, exit 0.** Tests covered: missions-200 suppressed, defer-blocked-200 suppressed, missions-500 logged, defer-blocked-500 logged, unrelated-200 logged, query-string-200 suppressed.

## Step 2 — Base attribution spot-check
`git show 673270ec:daemon/api.py | wc -l` → **2047** (matches developer claim; threshold 1600 ⇒ pre-existing failure premise confirmed). Working tree = 2060 (+13, consistent with diff).

## Step 3 — Live smoke (real acceptance)
- Boot: uvicorn `daemon.api:app` from the worktree on **127.0.0.1:8090** (dev.sh bypassed — it hardcodes PORT=8079).
- DB: disposable homebrew PG@14 cluster in /tmp (port 55432, db `ensemble_smoke`) — fresh-SQLite boot is broken at migration `20260714_000001` (see Gap 1). `POSTGRES_*` inheritance neutralized via `/tmp/smoke-data/ensemble.json`.
- Curls (all exit 0): `/api/missions` ×2 → 200, 200; `/api/queues/defer-blocked` ×2 → 200, 200; `/docs` → 200; `/api/missions/not-a-uuid` → 404.
- Log evidence (6 curls → exactly 2 access-log lines):
  ```
  01:30:43 - daemon.api - INFO - [127.0.0.1:61298] GET /docs 200
  01:30:43 - daemon.api - INFO - [127.0.0.1:61299] GET /api/missions/not-a-uuid 404
  ```
  → Zero lines for the four 2xx polls (suppression ✓); control + 4xx-on-suppressed-path still logged ✓. Raw log preserved at `/tmp/smoke-hide-logs.log`.
- Cleanup: listener PID verified via `lsof -i :8090` (16612, cwd = worktree) before kill → 8090 released; temp PG stopped → 55432 released.

## Gap list
1. **Fresh-SQLite boot broken (pre-existing, not caused by this branch):** migration `20260714_000001` executes PG-only SQL (`ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS …`); SQLite raises `OperationalError near "CONSTRAINT"` (SQLite has no DROP CONSTRAINT). The migration header wrongly claims SQLite 3.35+ support. Smoke routed around via disposable PG — change under test is DB-agnostic (middleware in `create_app`), so verified semantics are what ships.

## Drift / env discipline
Branch + SHA re-verified before pytest, before Step 2, and after shutdown — never diverged from `fix/hide-polling-access-logs` @ `673270ec`. `daemon.__file__` resolved inside the worktree after `uv sync`.

## Documentation Updated
- [x] RESULTS/2026-09-04-hide-polling-access-logs-verification.md (this file)
- [x] LESSONS/2026-09-04-fresh-sqlite-boot-migration-20260714-pg-only.md
- [x] Worker created reusable skill `62958be5` "Isolated Live-Smoke Boot for agents-ensemble"

## Overall Status
- Focused tests: ✅ PASS
- Base attribution: ✅ CONFIRMED (pre-existing)
- Live smoke: ✅ PASS
- **Verdict: PASS-WITH-GAPS** (1 environment gap, pre-existing, routed around; no coverage of the goal lost)
