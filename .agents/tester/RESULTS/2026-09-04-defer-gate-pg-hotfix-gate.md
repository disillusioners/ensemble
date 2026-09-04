# RESULTS — Defer-Gate PG Hotfix Full Gate (2026-09-04)

**Branch:** `hotfix/defer-gate-pg-ambiguous-param` — evidence @ `693a4ffc`; final HEAD `348a6edc` (gate-owned test-infra commit, test-file only). Base `2f80d45b`.
**Worktrees:** `/private/tmp/hotfix-defer-gate-pg` (HEAD), `/private/tmp/hotfix-defer-gate-base` (base, detached). Main worktree occupied — read-only, untouched.
**Change under test (4 files):** `daemon/repositories/job_queue/_idle_predicate_sql.py` (un-collapse + 2-body split), `daemon/repositories/job_queue/repository.py` (fail-CLOSED in `has_active_non_deferred_work` / `has_active_non_background_work`), `docs/job-queue.md`, `tests/job_queue/test_defer_gate_post_settle_window.py` (3 new tests: 2 default-suite pins + 1 PG pin).
**Incident fixed:** prod PG `AmbiguousParameter` on a collapsed-scope param; old `except Exception → return False` (fail-OPEN) admitted a defer job wrongly.

## VERDICT: ✅ FINAL PASS — CLEARED FOR MERGE
Zero caused regressions. Incident-shape pins red/green proven both halves. PG leg executed on a fresh disposable schema (no silent skips).

---

## 1. Acceptance (independent re-runs, all rev-parse gated)

| Set | Expected | Result @ `693a4ffc` |
|---|---|---|
| post_settle | 13 incl. 2 new pins | ✅ **13P/0F/2desel** (0.76s) — at final HEAD `348a6edc`: **14P/0F/2desel** (1.17s, incl. gate-authored wrapper pin) |
| probe | 2 | ✅ **2P/0F** (0.24s) |
| phase2 | 31 | ✅ **31P/0F** (0.19s) |
| seam set 68+16+12+13 | all candidates covered | ✅ **178 collected / 177P / 1F = ledgered pre-existing** (2.08s): seam_invariants 68✓, select_next_eligible 16✓, orphan_reaper 12✓, phase2_feedback_verify 12 (11P+1 known-F), system_default_project_backfill 12✓, dead_letter 45 (ledger said 13 — upstream growth, all pass), observer_hardening 13✓ |
| deadlock-fix | 24 | ✅ **24P/0F** (0.18s) |
| runtime matrix S1–S5 | 5/5 | ✅ **5/5 PASS** (0.4s): S1 BLOCKED / S2 ADMITTED / S3 PAUSED-blocked / S4 two-leg layering / S5 self-deadlock exclusion. (Pack banner branch string is a baked header from authoring commit `ab567195`; rev-parse is authoritative.) |
| drift pack + census | 24 + census 23 | ✅ **24/24 PASS** (5.13s), BRANCH-CHECK armed (expected == got). Census verbatim via `regenerate_sets()`: **23 KNOWN_ADMISSION_STATE_WRITERS / 1 KNOWN_JOBITEM_CREATOR / 6 KNOWN_MINT_SITES** (mint set is subset-only by design; 84 source-minus-known candidates listed, none requiring registration). |

## 2. PG incident pin — fresh disposable schema EXECUTION proof
- **Pre-flight:** listed `hotfix%` schemas; issued `DROP SCHEMA IF EXISTS hotfix_test_amb_param CASCADE`; **absence proven** before the run (independent of the dev's dropped schema).
- **Run** (`-m postgres -rs`): **`test_pg_project_scoped_incident_shape_pin` PASSED** (`1 passed`) — pin self-provisions `hotfix_test_amb_param`, asserts `has_active_non_deferred_work("proj-pg-incident") == True` (no `AmbiguousParameter`), system-wide call + scoped-filter invariant; **post-run absence proven** (self-cleanup held).
- **Sibling** `test_idle_predicate_pg_sqlite_parity` SKIPPED **loudly** (URL + remedy printed): `docker-compose.test.yml` public-schema stack not provisioned — the identical documented condition accepted at the 2026-09-03 FINAL-PASS gate; its SQLite parity runs in the default suite. **Not a silent skip; not the incident pin.**
- PG reachable throughout: `localhost:5432/ensemble_test` (user `ensemble`).

## 3. Hotfix-specific
- **Static guard runs in-suite:** `test_no_bare_param_is_null_comparison_in_busy_bodies` — default collection, no markers. **Mutation-killed:** collapsed shape re-injected into `JOB_DEFER_BUSY_BODY_PROJECT` → guard FAILED with `[('JOB_DEFER_BUSY_BODY_PROJECT', ':project_id IS NULL')]` → cleanly reverted (porcelain clean, full file re-run green).
- **Wrapper-level fail-closed pin:** branch as-is had a **CONFIRMED GAP** (dev pin injects at `engine.begin()` inside the repository predicates only; service call sites `job_processor.py:274/:396`, `job_queue_service.py:2802/:2865` untested for the admission outcome). **Authored gate-owned** `TestPostSettlePhase2Fix::test_db_error_through_service_layer_defer_job_stays_pending`: drives `JobQueueService._select_next_eligible_job` defer branch with `OperationalError` injected at the engine boundary; asserts candidate NOT admitted + durable `admission_state` stays `queued` + non-vacuous control leg (clean predicate admits the same candidate). Commit **`348a6edc`** (tests/ only). **Red-proof at base `2f80d45b`:** fails with the exact incident semantics — captured WARNING `has_active_non_deferred_work failed … treating as False (fail-OPEN on DB error)` + assertion *"service-layer Gate B admitted a defer candidate after the job predicate's DB error failed open; candidate must stay pending until the next retry tick"* showing the admitted JobItem. Base restored (porcelain empty).

## 4. Focused regression A/B (touched partitions)

| Leg | `tests/job_queue/` partition | repositories subset (3 files) |
|---|---|---|
| HEAD `693a4ffc` | 7F / 1628P / 38S / 2desel (44s) | 112P (2.6s) |
| BASE `2f80d45b` | 7F / 1626P / 38S / 1desel (47s) | 112P (2.8s) |

**Attribution: 7/7 failure node ids + signatures byte-identical across legs → ALL pre-existing; ZERO caused regressions.** The 7 = the ledgered mission settled-rename stale-fixture family (QUARANTINE.md 2026-09-03 row): observer-guard/"StrEnum" ×4 (`test_in_progress_guard` ×2, `test_job_feedback_observer` ×1, `test_phase2_feedback_verify` ×1 — MagicMock-vs-string / `get_job_by_instance` called patterns) + settled-vocabulary ×3 (`test_watcher_repository_concurrent` ×2, `test_jober_watch_integration` ×1 — `'settled' != 'failed'` at index 1). Arithmetic reconciles: 1672→1675 collected (+3 = 2 default pins + 1 PG pin; +1 desel = the PG pin), 1626→1628 passed. No HEAD-fail/base-pass divergence anywhere.

## 5. ensure.md (blast-radius scoped — hotfix discipline)
- **Critical #1** (no regressions in changed packs): **PASS** — all scoped packs green or expected-evidence only.
- **Critical #2/#3** (deadlock/concurrency integrity, sync-DB-off-loop): covered in-scope — `test_idle_gate_deadlock_fix` 24/24, matrix S5 self-deadlock exclusion, full `tests/job_queue/` lock/atomic suites green. Hotfix touches only the two busy-predicate READ paths; no lock/cascade/lifecycle surface → `concurrency_atomic_unit_test` outside this change's blast radius per hotfix scope.
- **Critical #4** (dev.sh flag): out of scope — `dev.sh` untouched by the branch.
- No new quarantines; ledgered family confirmed byte-identical at base.

## 6. Operational notes
- Two dispatched workers (deadlock, reg-base) sat IDLE with 0 queued and never executed; a single kick re-dispatch recovered both (escape-valve max respected; no second failure).
- W1's mutation-kill window ran while the HEAD partition was in flight in the same worktree — no contamination materialized (static-guard test PASSED in the concurrent partition run); hazard recorded in LESSONS.
- Ledger drift: `test_dead_letter_service.py` 13→45 (all pass).
- Pack banner strings can carry stale branch headers baked at authoring time — rev-parse is the authority (recorded in LESSONS).

## 7. Code changes by the gate
- **1 commit, test-file only:** `348a6edc` — `test: gate-owned wrapper-level fail-closed admission pin (DB error through service layer -> defer job stays PENDING)`. Zero production changes.

**Dispatches:** 12 workers (inventory, 8 packs/legs, reg HEAD, reg base, authoring). Worker IDs: d25768c6, 766880f1, 17214ecf, 7d3f55b1, cfa692f2, 8e7915b7, 0382afc4, 9bfc6f1d, dbed81b8, 1579e59f, 7187be62, 20b4ecb0.
