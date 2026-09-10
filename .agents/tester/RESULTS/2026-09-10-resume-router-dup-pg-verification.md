# Pre-Merge Verification — RESUME_ROUTER Duplicate-Report Fix (PG gate)

**Date:** 2026-09-10
**Branch:** `feature/fix-resume-router-report-dup` @ `004b4a26eebef1aae5d1216786da17b50cdf8935` (4 commits ahead of `latest` `a28ba9a5`)
**Verdict:** ✅ **PASS FOR MERGE** — PostgreSQL (prod truth) gap closed; original symptom closed out on BOTH engines; zero branch-caused failures anywhere in scope.

Workers: 10 instances (1 infra + 9 execution), 6 single re-dispatches (all caused by one verifier-scratch authoring defect — see §6), 0 nodes incomplete, 0 production changes, 0 commits.

---

## 1. Per-Suite × Per-Engine Results

### Core suites (the gate)

| Suite | SQLite | PostgreSQL | Notes |
|---|---|---|---|
| `tests/unit/test_resume_router_report_dup.py` | **16/16 PASS** (1.19s) | **17/17 PASS** (16 + dialect canary, 7.20s) | Original-symptom suite; shadow-module technique, same test code on PG engine |
| `tests/repositories/test_report_injection.py` | **36/36 PASS** (0.71s) | **37/37 PASS** (36 + canary, 8.82s) | Repository suite of modified `report_injection/repository.py` |
| `tests/unit/test_streaming_none_node_update.py` | **4/4 PASS** (0.16s) | **4/4 PASS** under PG env context (0.26s) | File is engine-inert (no DB seam — recon-verified); PG run = env-context proof |
| **Core totals** | **56/56** (matches dev exactly) | **58/58** (56 + 2 canaries) | 0 FAIL / 0 TIMEOUT everywhere |

### Adjacent sweep (SQLite, time-boxed)

| Pack | Result | Notes |
|---|---|---|
| `report_injection_regression` targets (repo suite + `test_report_injection_migration_parity.py`) | **62/62 PASS** (0.82s) | +1 vs prior-gate baseline 61 — branch added 1 migration-parity test (25→26), delta fully accounted |
| `report_delivery_recovery_regression` targets (`test_report_delivery_recovery_service.py` + `test_report_delivery_self_heal_zero_row.py`) | **27/27 PASS** (0.88s) | Baseline-exact, no growth |

### ensure.md scoped validation

| Requirement | Status | Evidence |
|---|---|---|
| Critical #1 — no regressions in changed packs | ✅ PASS | all 9 scoped packs green |
| Critical #2 — deadlock/concurrency integrity | ✅ PASS | `concurrency_atomic_unit_test`: 98P/0F/74S in 7.17s, exact prior-gate baseline |
| Critical #3 — no sync DB calls on event loop | ✅ PASS | same pack (thread-identity tests) |
| Critical #4 — dev.sh `--timeout-graceful-shutdown 10` | ✅ PASS | grep found at dev.sh:99 (comment) + :102 (flag) |
| Important #1 — async-await callers | N/A-scope | named functions untouched by this branch; the streaming guard is a sync `isinstance` check inside the async consumer — no new async surface |
| Important #2 — original deadlock scenario | ✅ PASS | covered by concurrency pack |
| Release Gate | NOT TRIGGERED | scoped bugfix (2 prod files + 2 test files, single subsystem); not big/critical/architecture |

No ensure.md contradictions found this gate. No `pytest -x`; quarantine-deselects honored (74 skips = pack-deselected known families; the 5× TestAccessMemoryArchive quarantine noise was not encountered — out of scope).

---

## 2. PostgreSQL Execution — the Critical Gap (CLOSED)

**The reviewers' blocker, resolved.** Their role lacked `CREATE` on schema `public` of a shared DB. This gate created **fresh disposable databases owned by role `ensemble`** on the local PG 14.22 cluster (localhost:5432, Path A):

- `ensemble_test_resdup_rr_004b4a26` / `ensemble_test_resdup_ri_004b4a26` — `CREATE DATABASE ... OWNER ensemble`; CREATE-on-public probe passed on both (verbatim probe evidence in infra worker report)
- `ensemble_prod` (LIVE on the same cluster) never contacted: wholesale `POSTGRES_*` scrub in every PG pack, hard URL guards (`abort if any URL contains ensemble_prod`), admin operations via `ensemble_test` admin DB only
- Both disposables force-dropped by EXIT traps, rc=0, zero orphans; port 8088 untouched

**Technique:** the suites' engine fixtures are SQLite-hardcoded (in-module file-backed SQLite / conftest StaticPool), so PG execution used the established **engine-shadow + dialect-canary** pattern (cf. LCA live-descendants gate 2026-09-06): shadow modules star/explicit-import the original test classes and define a module-local PG `engine` fixture (per-test dedicated schema `resdup_rr_test` / `resdup_ri_test` via libpq connect-options `search_path`), plus `TestDialectCanary::test_engine_is_postgresql` proving non-vacuous PG execution. **Same test code, same assertions — PG engine.**

**PG-specific dialect findings: NONE.** Explicitly watched for and clean: prefix-LIKE behavior, correlated subqueries, RETURNING clauses, state-guarded UPDATE rowcounts, timestamp/uuid comparisons. Behavior identical to SQLite baseline on every node.

---

## 3. Original-Symptom Close-Out (prod incident ca14e233 ×4 duplicate reports)

Post-fix PASS on **both engines** (dev already demonstrated the scenario FAILS pre-fix on SQLite). Evidence trail — test nodes + the assertions that pin the fix:

**5a — trigger repro (`TestDeliveredChildNotRecandidate`, test file :359):**
- `test_query_returns_no_candidates_for_delivered_child` — `assert rows == []` ("a child whose terminal report was already delivered must NOT be a Lane-2 candidate")
- `test_lane_produces_no_markers_and_no_redelivery` — `lane.recovered == 0`, `lane.errors == 0`, `manager._handle_recover_deferred_report.call_count == 0`, exactly 1 injection row still `TASK_DELIVERED`, exactly 1 parent report row

**5b — true recovery still works (`TestLostReportRecoveredExactlyOnce`, :461):**
- `test_lost_child_is_candidate_with_single_anchor` — exactly 1 candidate, deterministic anchor (the child's terminal checkpoint message)
- `test_recovery_is_exactly_once_across_sweep_passes` — pass 1: `recovered == 1`, 1 recovery call, row `PENDING` with `DEFERRED_REASON_RESUME_ROUTER`; pass 2: `candidates == []`, `recovered == 0`, total injection rows == 1

**Edge window (a) — no report-LOSS (`TestFreshSecondTurnAfterSweepRecoveryFirstTurnStillClaims`, :1033):**
- drain seam: fresh second-turn row has `recovery_attempted_at IS NULL`, still drains
- task seam: fresh second-turn row still claims (`claim.status == "claimed"`) — closes window (a)

**Edge window (b) — no first-recovery dup (`TestLegacyWrongAnchorDupDeadLettered`, :1192):**
- drain + task seams: legacy wrong-anchor content-duplicate is dead-lettered (`drained == []`, dup row terminal, companion message `COMPLETED`) — closes window (b)

**Defense-in-depth (`TestContentIdentityDedup`, :621, 4 tests):** duplicate (parent, child, content) delivery claims dead-lettered regardless of anchor; distinct content still claims; dedup scoped per child (predicate on dup row `recovery_attempted_at IS NOT NULL` at both claim seams — commit `004b4a26`'s council fix).

**Streaming guard (`test_streaming_none_node_update.py`):**
- Emission contract: `test_empty_return_node_emits_none_update` — langgraph `stream_mode="updates"` emits `{node: None}` (the crash premise)
- Source integrity ×2: `isinstance(node_data, dict)` guard present at BOTH consumption sites (`inspect.getsource` substring pins)
- Behavioral mirror: `None`/non-dict updates skipped (`accumulated == []`)

Full 16-node enumeration with per-class results archived in worker W1's report (all 8 classes listed with file:line).

---

## 4. Scope Decision

> Full suite NOT run. Change touches 4 files (2 prod: `report_injection/repository.py`, `instance_messaging.py`; 2 test), single subsystem (report-delivery recovery + streaming guard), already passed 2 review rounds. Ran: 3 core suites × 2 engines + 2 adjacent report-injection packs + concurrency pack (ensure.md Critical). Skipped: everything else + FE/web automation. Full suite not warranted.

**FE skip justification (per task note):** zero frontend change in this arc — repository + streaming guard only, no API shape changes, message-id invariant preserved per review. Web automation not applicable.

---

## 5. Anomalies & Disclosures

1. **Verifier-scratch authoring defect (not source):** all 6 `/tmp/resdup-gate/packs/*.sh` computed `PROJECT_DIR` via 4×`..` traversal from `/tmp/resdup-gate/packs` → resolves to `/` → drift-pin `git rev-parse` abort (exit 128) before pytest. First dispatches of W1–W6 all ABORTed on it. Recovery: SQLite packs re-delivered ad-hoc (direct invocation, drift pin + dual-layer timeout inline — same delivery shape as W7/W8 which never used the defective scripts); PG scripts patched in scratch (canonical `PROJECT_DIR="${PROJECT_DIR:-<repo>}"` recipe). One re-dispatch per worker (fan-in valve respected), all passed. No worker failed twice.
2. **Shadow-module search_path bug (found + fixed in scratch):** connection-scoped `SET search_path` didn't apply to pooled connections → tables landed in `public` → per-test schema drops were no-ops → cross-test `UniqueViolation`s (W5: 19F before fix). Fixed via libpq connect-options on the URL. Applied prophylactically to W4's shadow (clean first run). **This bug class silently masks PG runs — see LESSONS.**
3. **psycopg2-binary venv install:** `.venv` lacked the PG driver; installed via `uv pip install --python .venv/bin/python psycopg2-binary` (2.9.13). `.venv` is uv-managed with no `pip` module.
4. **Adjacent count growth:** 62 vs baseline 61 = +1 branch-added migration-parity test, accounted.
5. **PG14 `public` schema ownership:** bootstrap-superuser-owned even on fresh DBs → shadows use dedicated test-owned schemas rather than DROP/CREATE `public` (avoids privilege issues without escalation).
6. Quarantine noise (5× TestAccessMemoryArchive) not encountered — out of scope. No flaky manifestations. Zero timeouts.

---

## 6. Repository State

- Working tree: clean except pre-existing untracked `docs/llm-stream-stall-hardening.md` (untouched, out of scope)
- Zero repo writes, zero commits, zero production changes — verify-only arc throughout
- All gate artifacts under `/tmp/resdup-gate/` (6 pack scripts — 3 patched, 3 left as defect record — + 2 shadow modules); both disposable PG DBs dropped

## Overall Status

- Core SQLite: ✅ 56/56 | Core PG: ✅ 58/58 (incl. canaries) | Adjacent: ✅ 89/89 | ensure.md scoped Critical: ✅ 4/4
- **PASS FOR MERGE.**
