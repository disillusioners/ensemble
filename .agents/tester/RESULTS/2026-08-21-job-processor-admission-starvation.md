# JobProcessor Admission Starvation Fix — Verification Report

**Branch:** `fix/job-processor-admission-starvation` @ `cc35959a` (base `75cc0170` = latest; test-infra commit `678709d3` adds 3 pack scripts only — daemon/ byte-identical to `cc35959a`)
**Fix commits:** `42844090` (work-driven queue scan) → `f9c4ac06` (review nits #1,2,4-7) → `cc35959a` (MIN/MAX discrimination + PG EOF)
**Change set (production):** `daemon/services/job_processor.py` (+ `daemon/repositories/job_queue/queue_repository.py` — new `list_queues_with_admittable_work`, joins job_queue_items, `ACTIVE_ADMISSION_STATES` default, `MIN(created_at) ASC`, limit 1000) + per-iteration project-pause cache (fail-open) + ValueError on empty states
**Date:** 2026-08-21 · **Tested by:** Tester (leader) + 8 worker instances

## VERDICT: ⏳ PENDING — gate run in progress

### Scope Decision
Change touches job_processor + claim-guard semantics → ensure.md critical note (job/task/queue → e2e MANDATORY) honored in full: named subsystem suites + Release Gate on the ORIGINAL failing context (ensemble_dev, 338 projects). Unit scope = job-queue subsystem packs, NOT all 274 packs (2-file focused production change; same scoping precedent as 2026-08-20 job-tools arc).

### 1. Unit / regression packs (branch, local) — ALL PASS

| Pack | Result | Counts | Runtime | Notes |
|---|---|---|---|---|
| `admission_starvation_unit_test` (NEW, commit 678709d3) | ✅ PASS | 6/6 in 0.22s | <1 min | 120-project regression (core starve case), dead/deleted exclusion, admittable-only, limit cap, MIN(created_at) ASC ordering ×2 |
| `admittable_work_pg_test` (NEW, commit 678709d3) | ✅ PASS | 5/5 in 0.67s | <1 min | **Real PG engine proven** (psql probe: `ensemble_test`/ensemble/PG 14.22; skip-on-unreachable fixture → 0 skips = engine reached): admission filter, DEAD exclusion, soft-delete exclusion, 4-shapes combined, ACTIVE_ADMISSION_STATES constant wiring |
| `job_queue_unit_test` (full sweep) | ✅ PASS | **1530P/38sk/0F** in 34s | ~1 min | EXACT match to dev expectation; +12P/−1sk vs 1518P/39sk baseline = 6 admission tests + ~6 expanded sibling tests (delta reconciled per-file) |
| `turn_transitions_reconciler_unit_test` | ✅ PASS | 46P/1 deselected in 1.55s | <1 min | ensure.md critical-note suite; 1 quarantine-deselect per QUARANTINE.md |
| `concurrency_atomic_unit_test` (Core R2) | ✅ PASS | 91P/74sk/0F in 7.08s | <1 min | Deadlock/concurrency + asyncio.to_thread thread-identity (DB helpers off event loop — covers new scan path) |
| `claim_guard_locks_unit_test` (NEW, commit 678709d3) | ✅ PASS | 168P/0F/0sk in 1.77s | <1 min | claim_pending_task NOT EXISTS guard (66) + lock repository (36) + seam invariants (66) — claim side agrees with admission side |

### 2. ensure.md Core (scoped) — 4/4 PASS
- R1 changed packs PASS ✅ (all 6 above)
- R2 concurrency_atomic ✅ 91/0
- R3 dev.sh `--timeout-graceful-shutdown 10` ✅ (active arg at dev.sh:102)
- R4 async-await statics ✅ (5 non-await matches = def lines/docstrings only; all real call sites awaited)

### 3. Release Gate on ensemble_dev (Phase-5 closure) — ✅ PASS
Fresh daemon, main repo `.env` (no worktree — direct route to ensemble_dev):
- **DB identity (engine log):** `Creating PostgreSQL engine: localhost:5432/ensemble_dev` ✓ (the /api/health trap avoided — it reports type only)
- **Confound held pre-run:** system-default `71931ae0-0f25-5fbf-853b-2a78cc978d7e` at **rank #189 of 338 projects** (updated_at DESC) — same context as the archived base failure
- **Boot health:** 0 ERROR / 0 Traceback in boot window; pre-test pending jobs = 0
- **Gate pack** `e2e_workflows_ensure_test` (log /tmp/ens_gate_dev_fix.log): **RESULT: PASS** — 3 passed / 1 deselected (quarantine preserved) in 231.8s
  | Test (originally failing set) | Result | Duration |
  |---|---|---|
  | test_parent_child_workflow_happy_path | ✅ PASS | ~58s |
  | test_terminate_after_spawn_then_revive | ✅ PASS | ~43s |
  | test_three_level_cascade_reports | ✅ PASS | ~127s (3-worker staggered wave, reports delivered) |
  | test_pause_after_spawn_then_resume | ➖ deselected (QUARANTINE.md); run separately — see §4 |

**Signature evidence vs archived baseline (original failing observations, 2026-08-20, branch-tip ×3 on this same DB; decisive base-on-dev run /tmp/ens_db_confound.log = F/F/F/P with failing leaders at 0 admissions/0 processing):**
| Signal | Baseline (failing, pre-fix code) | This run (fix @ cc35959a) |
|---|---|---|
| `[GUARD] ... queue-admission` storm | 243 lines (original 2026-08-20 storm); decisive confound run had 0 GUARD but 3/4 FAIL from 0 admissions | **5 lines**, single 3-second window (12:34:57-12:35:00) at run tail |
| `found PENDING job` (admission) | 0 in failing leaders | **5** — leaders' PENDING jobs found by _process_next_job |
| task `Processing` events | 0 in failing leaders | **42** (incl. 19 message-task processing) |
| spawn/tool activity | 0 | **50 spawn events** (incl. 6 explicit spawn_instance tool calls) |

**Original symptom reproduces? NO.** The never-claimed signature (0 admission / 0 processing / 0 LLM) is dead; the gate that failed 3/4 on base now passes 3/3 on the identical DB context.

### 3a. Residual 5 GUARD lines — attribution verification ✅ BENIGN (source-quoted + task-fate traced)
Read-only forensics (instance c7841560):
- **Source:** `daemon/repositories/task/repository.py:1473-1480` — the parenthetical `(pause/running/queue-admission/defer/background)` is a **hardcoded literal documenting the 5 SQL WHERE-clause guards**; the line carries only `%d` = COUNT of guard-blocked eligible tasks, **no task_id, no per-task attribution** (comment at :1440-1457: the guard chain is pure SQL, "no Python branch to log the reason").
- **Per-line fate:** all 5 lines are worker-pool polling retries (≈700ms cadence, single 3s window 12:34:57-12:35:00) while task 167 (process_report on leader b18abb2a) was held by the **per-instance RUNNING guard** (task 166 in flight on same instance). Task 167 claimed at 12:35:03 by worker-0, completed 12:35:10. **Zero orphan PENDING tasks** in the entire log — claimed−completed = {task 170 only}, the deliberate pause-test pause→stale-cancel→resume scenario.
- **Worker-pool shutdown tally:** w0 5/5, w1 8/6 (170 by design), w2 4/4, w3 5/5, 0 failed — clean.
- **Pause window (12:36-12:38):** 0 GUARD lines.
- **Literal-criterion note:** string-match count is 5 (vs 243 in the original storm), but the string is a static list; mechanistically **no task was blocked by queue-admission** — admission counter (5 found-PENDING) and processing counter (42) prove the starvation path dead. Follow-up (non-blocking): enhance guard diagnostic to log candidate task_id + matched guard (select instance_id instead of COUNT-only).

### 4. Quarantined pause test (informational) — ✅ PASS (bonus, non-un-quarantining)
`test_pause_after_spawn_then_resume` run standalone on the same fresh daemon: **1 passed in 49.6s** (log /tmp/ens_gate_dev_pause.log). Signature does NOT match either the reconciliation-gap flake family (no WAIT_COMPLETE stall, no last_status=running) or the never-claimed family (PROCESSING>0). Per QUARANTINE.md, un-quarantine still requires 3× clean re-run on base — this is +1 clean run evidence, recorded in QUARANTINE.md.

### 5. Cleanup discipline
Daemon stopped via verified-PID SIGTERM (cmdline checked = uvicorn daemon.api:app :8079 before kill; :8088 untouched), graceful shutdown logged ("claimed=8, completed=6, failed=0"), `:8079` confirmed free, final pending jobs = 0, no repo mutation from the gate task, /tmp logs preserved.

### Quick Fixes / Commits (test-infra only)
- `678709d3` — 3 new pack scripts (admission_starvation_unit, admittable_work_pg, claim_guard_locks)

### Logs
- /tmp/admission_starvation_unit_test.log · /tmp/admittable_work_pg_test_output.log (+_verbose.log) · /tmp/job_queue_unit_test_run.log · /tmp/turn_transitions_reconciler_pack.log · /tmp/concurrency_atomic_unit_test.log · /tmp/claim_guard_locks_unit_test.log
- Gate logs (pending): /tmp/ens_gate_dev_fix.log · /tmp/ens_gate_dev_pause.log
