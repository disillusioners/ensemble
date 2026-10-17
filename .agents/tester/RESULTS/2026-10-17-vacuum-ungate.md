# RESULTS: vacuum-ungate — checkpoint-cleanup Step-2 Continue ungate @ 31bedca78

**Date:** 2026-10-17
**Commission:** TEST-ONLY certification of commit `31bedca78` ("fix(maintenance): ungate checkpoint-cleanup step-2 continue on zero-reclaimable dry run")
**Worktree:** `/home/ensemble-worktrees/vacuum-ungate-20261017` · branch `feature/maintenance-vacuum-ungate` · base `030f87ce1` (= latest)
**Change shape:** 3 files, all in `frontend/src/app/pages/maintenance/checkpoint-cleanup/` (component.ts, component.spec.ts, component.html) +37/−15. `canContinueFromStep2()` return flipped `(dry.would_delete_count ?? 0) > 0` → `true`; guards preserved (`!dry || dryRunning || executing | isRunInFlight → false`). Backend untouched.
**VERDICT: 🟢 PASS** — all commissioned lanes green; original user scenario validated dynamically at DEPTH-2 in a real browser.

## Scope Decision
Small/isolated change: 1 commit, FE-only, single wizard component family, no architecture impact, BE already pinned by existing integration test. Scoped run = FE wizard-family specs + typecheck + 1 BE integration node + 1 focused e2e lane. Full suite NOT warranted. No QUARANTINE.md rows exist for this family (verified — zero matches).

## Original user scenario — DYNAMIC validation (the commission's MUST)
"Dry run completes reporting ZERO reclaimable bytes/rows → next-step Continue is NOT disabled → user can proceed to Step 3 and trigger the VACUUM FULL execution flow."
→ Proven at THREE layers: component spec (:2654 zero-everything → true; new mixed-projection pin), focused e2e w8 (browser: zero fixture → `ck-continue-btn` ENABLED → Step 3 visible), e2e w9 depth-2 (vacuum-full checked → confirm → execute POST `expected_bytes: 0`, `vacuum_full: true` → "Run completed" without error), and BE integration (execute with zero reclaimable accepted end-to-end on real PG16).

## What ran (exact commands, key verbatim lines)

### 1. FE jest — checkpoint-cleanup family + maintenance module (worker 66945f4e, skill test-pack-execution)
```
cd /home/ensemble-worktrees/vacuum-ungate-20261017/frontend && timeout 120 ./node_modules/.bin/jest --testPathPatterns='checkpoint-cleanup' --no-coverage
→ Test Suites: 2 passed, 2 total / Tests: 174 passed, 174 total (6.412s) — component 123 + service 51
cd ... && timeout 120 ./node_modules/.bin/jest src/app/pages/maintenance --no-coverage
→ Test Suites: 4 passed, 4 total / Tests: 230 passed, 230 total (6.311s)
```
Developer's 123/123 on the component spec CONFIRMED within View A/B. 230/230 matches the 2026-10-12 `fe_jest_maintenance_4suite` baseline exactly — zero regressions in the wizard family.

### 2. FE typecheck (worker 49a38094)
```
cd .../frontend && timeout 120 node ./node_modules/typescript/bin/tsc --noEmit -p tsconfig.app.json
→ exit 0, no output (6.73s)
```
Direct zero-fetch idiom (this worktree's node_modules lacks the `.bin/tsc` symlink; `node_modules/typescript/bin/tsc` present — no npx needed). ✅ PASS.

### 3. Edge/guard cases — component level (worker 041f5ba0)
Matrix vs commission list:
- (a) zero count → Continue ENABLED: pre-existing test ~:2654 'is true when dry-run reports would_delete_count == 0 (vacuum-ungate, 2026-10-17)' ✅ (dev-added in commit) — PASSED in lane 1
- (b) zero count w/ non-zero nested (and inverse): was MISSING → **added 1 it()** 'is true on mixed projections: zero count with non-zero nested blobs/bytes, and non-zero count with zero nested (vacuum-ungate edge, 2026-10-17)' — mutation-meaningful (zero-count+non-zero-nested FAILS under old `>0` gate). Re-run: **124/124 PASS** (5.0s)
- (c) no dry-run on record → disabled: pre-existing ~:2645 ✅ PASSED
- (d) dry-run in progress → disabled: pre-existing composite ~:2669 (AC-16) ✅ PASSED
- (e) execute in flight → disabled: same composite + sister-gate test ~:2693 ✅ PASSED
- (f) staleness re-check blocks stale dry-run: enforcement lives on the execute-dialog path (NOT in canContinueFromStep2, before or after this commit); pinned by pre-existing ~:1074 'stale dry-run short-circuits: no dialog opens' ✅ PASSED (unchanged by diff — single return flip, staleness logic untouched). Documented delegation: freshness window enforced by BE echo gate at execute time (per commit docstring).

### 4. BE dynamic proof (worker 885cb4cd) — DYNAMIC, not static
```
.venv/bin/pytest "tests/integration/test_maintenance_checkpoint_cleanup_api.py::TestExecuteIntegration::test_execute_zero_refs_no_mass_delete" --override-ini="addopts=" -m integration -q --tb=short
→ PASSED [100%] / 1 passed, 1 warning in 5.19s (exit 0)
```
On disposable PG16 scratch (mktemp `/tmp/vu-pg-*`, initdb -A trust @127.0.0.1:15435, full lifecycle: start → run → stop → port-freed-verified → dir removed). Fixture contract discovered: reads **`PG_TEST_*`** env (file :136), NOT `POSTGRES_*`; creates per-test `ensemble_blob_prune_<uuid>` DBs and self-asserts `"ensemble_prod" not in dsn`.
**FENCE CATCH (record as standing lesson):** parent shell inherited LIVE `POSTGRES_HOST=10.44.0.2 / POSTGRES_DB=ensemble_prod`; first attempt failed the fence assertion; full `env -u POSTGRES_* -u PG* -u DATABASE_URL*` scrub applied before the real run. Live PG never touched.

### 5. Web UI check — focused e2e, DEPTH-2 (worker 4273756d, skill e2e-test)
New spec `frontend/e2e/maintenance-vacuum-ungate-zero.spec.ts` (w8/w9) run via the dedicated harness:
```
cd frontend && timeout 300 env -u SSL_CERT_FILE -u SSL_CERT_DIR ENSEMBLE_SELF_ENV=dev \
  ENSEMBLE_DB_DSN=postgresql://…127.0.0.1:15432/ensemble_e2e_maint_vacuum_ungate \
  DISPOSABLE_DB=ensemble_e2e_maint_vacuum_ungate OPENAI_API_KEY=e2e-placeholder-key-not-used \
  PATH="/usr/lib/postgresql/16/bin:$HOME/.local/bin:$PATH" \
  npx playwright test --config playwright.maintenance.config.ts --project maintenance -g "vacuum-ungate"
→ 2 passed (1.1m), PW_EXIT=0
```
- **w8 (core regression):** ZERO fixture → Step-2 zero display unchanged (`0` / `0 B` still rendered) → `ck-continue-btn` **ENABLED** → click → Step-3 `ck-execute-btn` visible.
- **w9 (depth-2):** vacuum-full checkbox default-checked → confirm dialog → execute POST captured with `expected_bytes: 0` + `dry_run_run_id` echo + `vacuum_full: true` → poll → "Run completed" banner, no error.
- Boot/teardown: harness-owned ports (PG 15432 / daemon **8099** — registered-pack design, not dev 8079 / FE 4299); disposable DB asserted ≠ ensemble_prod (spec beforeAll also refuses any /ensemble_prod/i DSN); globalTeardown ran; 8099/4299/15432 verified released, cluster dir + data dir removed, zero daemon processes left.

## Test artifacts added (committed centrally on the branch)
1. `frontend/src/app/pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.component.spec.ts` — +1 mixed-projections edge `it()` (19 lines, inside "step gating — canContinueFromStep2 (spec §2.4)")
2. `frontend/e2e/maintenance-vacuum-ungate-zero.spec.ts` — NEW focused spec (w8/w9)
3. `frontend/e2e/maintenance-checkpoint-cleanup.spec.ts` — stale-comment fix only (advanceToStep3 doc no longer claims the removed `would_delete_count > 0` gate; points to the new zero spec)
4. `.agents/tester/RESULTS/2026-10-17-vacuum-ungate.md` (this file) + `.agents/tester/PACKS.md` ledger rows
No product code modified anywhere. Commit hash in the tester's final report.

## Environment / gotchas recorded
- Worktree `.venv` provisioned per convention: `env -u SSL_CERT_FILE -u SSL_CERT_DIR ~/.local/bin/uv sync` (124 pkgs from uv.lock; `import daemon` resolves INSIDE worktree; langgraph 1.0.9 / langchain-core 1.2.16 per ratified pin policy). Ambient `SSL_CERT_FILE` from a dead PyInstaller extraction breaks uv fetches — `env -u` fence mandatory (also recorded via experience()).
- Parent-shell LIVE `POSTGRES_*` leak (10.44.0.2/ensemble_prod) — BE lanes MUST `env -u` scrub both `POSTGRES_*` and `PG*` layers; fence assertion before every run.
- This worktree's `frontend/node_modules/.bin` has NO `tsc` symlink — safe idiom is `node ./node_modules/typescript/bin/tsc` (direct, zero-fetch).
- `.venv/` is gitignored — worktree tracked-file purity maintained through all lanes.

## Final worktree state
Branch `feature/maintenance-vacuum-ungate`; product HEAD `31bedca78` untouched; test artifacts committed in one centralized commit (hash in final report); working tree clean post-commit.

## Verdict
**🟢 PASS** — FE specs 174/174 + 230/230 (+124/124 with new edge pin), tsc exit 0, BE integration 1/1 dynamic on real PG16, browser e2e 2/2 at depth-2 including the exact commissioned user scenario. Zero failures, zero timeouts, zero re-dispatches (7 workers). No ensure.md contradictions encountered (Core items out of blast radius — FE-only diff; scoped-pack rule followed throughout).
