# Cross-Project Job Tool Access — Verification Report

**Branch:** `feature/job-tools-cross-project-access` @ `656b61fe` (parent `39f76dc7` on latest)
**Feature commits:** `0c692463` (helper extraction + system-default allow tier) + `656b61fe` (W1 polish: deny-branch audit `logger.warning`; logic otherwise stable)
**Change set:** `daemon/tools/job_queue.py` only (+ new test file `tests/unit/tools/test_job_visibility_tools.py`)
**Date:** 2026-08-20 · **Tested by:** Tester (leader) + 8 worker instances

## VERDICT: ✅ SHIP — feature verified; zero regressions attributed to the branch

### Scope Decision
Small, isolated change (1 production file, tool-ACL layer only, no queue/processor/task code). Scoped run per blast radius:
- **Ran:** new-feature unit pack, tool-layer baseline pack, job-system internals pack, turn-transition/reconciler pack, concurrency gate, ensure.md Core statics, full functional AC validation (real stack), live-daemon Release Gate e2e.
- **Skipped:** postgres/* suites (94 tests — no migration/repo-layer change), message_queue_redesign, frontend, full non-integration suite sweep (not a big/critical change; Release Gate "full suite" not triggered).
- ensure.md critical note (job/task/queue touch → e2e) honored by judgment: surface touched is the tool ACL, so the named subsystem suites were run in-process (`tests/job_queue/` 470→1518 executed, `tests/property/`, `tests/repositories/`, `tests/e2e/test_full_chain_turn_reconciler.py`) PLUS the live-daemon Release Gate. Full-scope note over-reading (running all ~1,837 matched tests) rejected as scope expansion.

### 1. Unit Tests
| Pack | Result | Counts | Notes |
|---|---|---|---|
| `job_visibility_tools_unit_test` (NEW, commit b215327c) | ✅ PASS | 40/40 in 1.65s | Covers TestJobMessages/Tree/Progress/Inject + TestSystemDefaultProjectCrossProjectAccess + registration |
| `job_queue_tools_unit_test` (NEW, commit d30684e4) | ✅ PASS-after-quarantine | 73 passed / 4 failed | All 4 = pre-existing job_continue baseline (KeyError 'instance_id' family), EXACT match; deselected per QUARANTINE.md |
| `job_queue_unit_test` (pre-existing) | ✅ PASS | 1518 / 0 / 39 skipped, 23s | job_processor, task_queue_service, locks, defer queues |

### 2. ensure.md Critical-Note Compliance (job/task/queue e2e)
| Pack | Result | Counts | Notes |
|---|---|---|---|
| `turn_transitions_reconciler_unit_test` (NEW, commit c43302c4) | ✅ PASS-after-quarantine | 46 / 1 | 1 pre-existing stale assert (test_state_machine, base-evidenced on 6bb99d5f); deselected per QUARANTINE.md |
| `concurrency_atomic_unit_test` (Core R2) | ✅ PASS | 91 / 0 / 74 skipped | Deadlock/concurrency integrity |
| `e2e_workflows_ensure_test` (live daemon :8079) | ⚠️ 3/4 (see Anomaly) | 1 PASS / 3 FAIL | See §Anomaly — environment, not branch |

### 3. Functional Validation (real stack) — ALL 6 PASS
Script: `/tmp/func_xproject_job_tools.py` (repo untouched). Layer: real tool callables + real SQLite repo stack (real `SQLModelInstanceRepository`, real `JobRepository`/`TaskRepository`/`LockRepository`, real `JobQueueService` + `WorkResolverService` resolving genuine DB `JobItem` rows; only the manager handle a thin facade). End-to-end daemon path NOT exercised (no system-default agent spawn) — tool/service layer with real repo writes, stated per task item 4.

| AC | Verdict | Evidence |
|---|---|---|
| AC-1 system-default caller → project-X job | ✅ | All 4 tools succeed: job_messages full payload; job_tree total=1 active=1; job_progress running; job_inject status=injected pending_count=1 |
| AC-2 project-A caller → project-B job | ✅ | All 4 return `Access denied: job does not belong to caller's project` (exact unit-test wording) |
| AC-3 audit log on deny | ✅ | `WARNING job access denied: caller=… caller_project=… job=… job_project=…` on `daemon.tools.job_queue` |
| AC-4 pre-bootstrap (constant None) | ✅ | Strict deny, no crash. Nuance: `caller.project_id=None` variant stays fail-open (documented legacy branch — legacy NULL-project rows rely on it) |
| AC-5 job_list/job_get/job_create unchanged | ✅ | job_get cross-project resolves via resolver (ACL never applied); job_list project-filter intact; job_create real enqueue OK |
| AC-6 same-project control | ✅ | X→X and sys→sys allowed |

### 4. ensure.md Core (scoped) — 4/4 PASS
- R1 changed packs PASS ✅ (re-verified 40/40) · R2 concurrency_atomic ✅ 91/0 · R3 dev.sh `--timeout-graceful-shutdown 10` ✅ (dev.sh:102) · R4 async-await statics ✅ (17 hits analyzed, 0 bare calls)

### ⚠️ Anomaly → 🚨 MERGE BLOCKER → ✅ EXONERATED (final, 2026-08-21: DB-confound CONFIRMED by reproduction)
**Branch is NOT the cause of the never-claimed F/F/F/P. The determinant is the daemon's DATABASE (project count vs the job_processor scan window), not the diff. The 2026-08-20 bisect that blamed 656b61fe was INVALID — code-state and DB-context were aliased.**

**Complete measurement matrix:**

| Code point | DB (projects) | Result | Notes |
|---|---|---|---|
| base `39f76dc7` | ensemble_prod (21) | **4/4 PASS** | system-default rank #1 by updated_at |
| base `39f76dc7` | **ensemble_dev (338)** | **3/4 FAIL F/F/F/P** | 2026-08-21 decisive run — same lines 1322/1690/2348, 0 PROCESSING in failing leaders; system-default rank **#189** (SQL-confirmed); 0 GUARD lines (guard was a secondary symptom) |
| `0c692463` (feature) | ensemble_prod (21) | P/F(flake)/P/P | flake = pause+resume line 1894, now quarantined |
| `656b61fe` (tip) | **ensemble_dev (338)** | **3/4 FAIL F/F/F/P ×3** | original observations — same signature |

**Root cause (pre-existing production bug, confirmed by reproduction + SQL):** `daemon/services/job_processor.py` (~:649) scans `list_projects(limit=100)` ordered `updated_at DESC`. System-default `71931ae0-…` in ensemble_dev ranks #189 of 338 → its jobs invisible to the worker pool until the scan cycles past ~70s, beyond the tests' 60s wait. Test 4 (cascade) survives because it waits longer / its projects rank high. `[GUARD]` lines were incidental noise of the same starvation.

**Why the bisect lied:** worktrees have no `.env` (only `.env.example`); dev.sh sources the MAIN repo's `.env` which sets `POSTGRES_DB=ensemble_dev`. Worktrees silently fell back to `ensemble_prod`. Every passing point = (worktree, prod); every failing point = (main, dev). Perfect aliasing.

**DB selection mechanism (verified):** `POSTGRES_*` env vars override data/ensemble.json + config.yaml (daemon/persistence.py:79-89, ensemble_config.py:82). `/api/health.current_database` reports DB TYPE (postgres), NOT name — resolve the actual name via engine log line `Creating PostgreSQL engine: …/<dbname>` or POSTGRES_DB.

**Branch verdict: EXONERATED.** Feature commit + W1 polish both clean on healthy DB context. Real bug for a separate fix task: job_processor scan window (raise limit / prioritize system-default / explicit ordering).

**Gate re-opened → ✅ CLOSED GREEN (2026-08-21, ship gate):** tip `656b61fe` (daemon/ verified byte-identical at HEAD c15959fa) on `ensemble_prod` (engine-log-verified, 21 projects, system-default rank #1) → **4/4 PASS** (41s/39s/123s + pause+resume 41s passing as QUARANTINED-FLAKE), 0 GUARD lines, 21 PROCESSING / 23 tool calls / 7 spawns, all 4 leaders' PENDING jobs found by job_processor. Log: /tmp/ens_ship_gate.log. NOTE: direct `POSTGRES_DB=ensemble_prod` env override on the main repo FAILS — dev.sh sources `.env` (→ ensemble_dev) AFTER process env; the worktree route (no .env → prod default) was used instead. **MERGE GREEN-LIGHT.**

**Protocol change (mandatory):** every gate/e2e run records DB NAME (engine-log or POSTGRES_DB), project count, system-default rank. Worktree runs: explicitly source the intended DB env.

### Quick Fixes / Commits (this session, all test-infra only)
- `b215327c` job_visibility_tools_unit_test pack · `d30684e4` job_queue_tools_unit_test pack · `c43302c4` turn_transitions_reconciler_unit_test pack · (pending) quarantine-deselect commit — see PACKS.md.

### Gaps
- ✅ ~~Release Gate blocker~~ — **RESOLVED 2026-08-21: branch EXONERATED (DB-confound confirmed by base+ensemble_dev reproduction).** Remaining for a fully green gate: one 3-test run of `656b61fe` on a healthy DB context (system-default rank <100); `0c692463` already 3/3 on prod after flake quarantine. Separate pre-existing bug filed by evidence: job_processor `list_projects(limit=100)` scan starvation (`/tmp/ens_db_confound.log`).
- Follow-ups: (2) pre-bootstrap `caller.project_id=None` fail-open branch — deliberate but worth a product decision note; (3) commit-message drift: 656b61fe cites api.py:512-528, actual backfills at :520/:542 (cosmetic); (4) pause+resume reconciliation-gap flake — quarantined 2026-08-21 (1F/4P @ 0c692463, 0F/4P @ base), needs root-cause fix under the Phase 4b/4c family + 3× clean re-run to un-quarantine.
