# F-2 Demo E2E Findings (G4 REDO) — Durability F-1/F-2 Merge Gate

**Date:** 2026-10-04 to 2026-10-05 UTC
**Branch:** `feature/durability-f1-f2` @ `fb0f4655` (base `18827dbd`)
**Worktree:** `/home/nea/ensemble-src-wt-durability`
**Ruling:** Evidence-only, no code changes/fixes, bounded polls, SELECT-only DB.

---

## ⚠️ DEVIATION FROM BRIEF: TERM → KILL

**Disclosed prominently per Leader instruction.** The original brief
specified `kill -TERM <pid>`. In the first L1 attempt, `kill -TERM`
caused a graceful drain that delivered the pending wake to the
parent BEFORE the daemon died. This is the **normal production
behavior** of TERM (graceful shutdown with work draining).

For the F-2 demo, we need the wake to be **undelivered** when the
daemon dies. The correct signal for crash-durability testing is
`kill -KILL` (SIGKILL), which is the crash-durability model that
F-1/F-2 targets (per the F-1 plan §1: "daemon dies mid-claim").

**R1, R1b, R2, R3 all use `kill -KILL` (SIGKILL) after SIGSTOP.**
The SIGSTOP pauses the daemon in the same iteration the poller
observes the wedge, allowing DB verification of the precondition
before the hard kill.

---

## R0 — Crash Attribution (PRE-EXISTING-AT-BASE)

**Verdict:** The daemon crash at 00:38:29 with
`RuntimeError: DependencyBus is not initialized for instance=f165c8f1...`
is a **pre-existing race condition**, NOT caused by the F-1/F-2 branch.

### Evidence
- Raise site: `daemon/services/child_reports.py:2841` in `_process_child_completion_db_sync`
- The raise is an intentional **A8 HARD ERROR** (not graceful degradation)
- `git log 18827dbd..HEAD -- daemon/services/child_reports.py` → empty (NOT TOUCHED)
- `git log 18827dbd..HEAD -- daemon/services/message_processing_pipeline.py` → empty (NOT TOUCHED)
- F-1 branch touched: `daemon/services/dependency_bus.py` (F-1 gates at ~:702 and ~:907)
- The crash site is in a DIFFERENT file, NOT adjacent to the F-1 gates
- Full traceback in `db-assertions/r0-crash-attribution.md`

**Recommendation:** Track as separate issue, not an F-1/F-2 regression.

---

## R0.5 — Clean State

- Booted durability daemon via wrapper
- Enumerated 22 L1-L5 instances, DELETE all via API
- Verified task table: 0 rows; message_queue: 0 rows
- 8088 actual state: **UNBOUND** (recorded per Leader note; never touched)
- Full record in `db-assertions/r05-clean-state.md`

---

## Per-Leg Results (REDO)

| Leg | Description | Result | Key Finding | Log File |
|-----|-------------|--------|-------------|----------|
| R0 | Crash attribution | **PRE-EXISTING-AT-BASE** | `child_reports.py:2841` not touched by F-1/F-2 | n/a (evidence in r0-crash-attribution.md) |
| R0.5 | Clean state | DONE | 0 tasks, 0 message_queue, 8088 UNBOUND | n/a |
| R1 | True straddle (SIGSTOP) | **MIXED** | Wedge captured (inj_state=PENDING, wake in 'ready'). Post-reboot: parent STUCK (lane 2 correctly skips, watchdog correctly doesn't fire, worker pool per-instance guard blocks) | r1-wedge-capture.log, r1-reboot.log, r1-reboot-final.log |
| R1b | Idempotency reboot | **PASS** | Lane 2 did 0 new work. Parent completed via auto-continue. int_reports count unchanged. | r1b-reboot.log |
| R2 | Kill-switch OFF→ON | **INCOMPLETE** | Kill-switch OFF boot verified. Wedge consistently missed (3 attempts) — wake transitions too quickly. | r2-boot.log, r2-poller.log, r2r-poller.log, r2f-poller.log |
| R3 | Multi-child mixed | **INCOMPLETE** | All 3 children completed, parent healed naturally. Wedge missed (same root cause as R1/R2). | r3-boot.log, r3-poller.log |

---

## R1 PASS Criteria Assessment

| Criterion | Status | Notes |
|-----------|--------|-------|
| a. Parent leaves waiting_children + reply synthesizes helloR1 | **FAIL** | Parent stuck in waiting_children (anchor exists, watchdog doesn't fire, worker pool blocked by per-instance guard) |
| b. ZERO manual pings | **FAIL** | Sent manual wake-up message to verify heal (documented as deviation) |
| c. EXACTLY ONE internal_report row | **PASS** | 1 row in message_queue (status='ready'), 1 row in report_injections (state='PENDING') |
| d. Child NOT re-executed | **PASS** | Child has 1 task row, completed. Checkpoint unchanged. |
| e. Lane-2 boot-log marker + counters | **PARTIAL** | Marker present, no per-row processing (anchor exists, lane 2 correctly skips) |

### R1 root cause analysis

The SIGSTOP technique captured the TRUE PENDING state:
- `inj_state=PENDING` (not TASK_DELIVERED) ✓
- `wake_msg=ready` (in message_queue) ✓
- `wake_task=running` (claimed by worker pool) ✓

But the post-reboot state is STUCK because:
1. **Lane 2 correctly skips**: anchor exists (message_queue row + report_injections row)
2. **Watchdog correctly doesn't fire**: carrier exists (message_queue row in 'ready')
3. **Worker pool per-instance guard**: won't claim 'ready' message while parent is 'running' on another task

The system has no mechanism to process a 'ready' wake that was preserved
across a crash. This is a **design gap**, not an F-2 code bug.

---

## Adjudicated Interpretation of L1/L5 (from Leader)

**Leader ruling:** The straddle was NOT achieved in L1/L5.
- `report_injections` TASK_DELIVERED + WaitingChildrenWatchdog heal means
  the wake DELIVERED BEFORE daemon death
- `kill -TERM` gracefully drains and the drain delivered the pending wake
- Lane-2 skipping was CORRECT (marker-minted case belongs to lanes 1/3/4
  per decisions §12a window-map)
- The demo's purpose — lane 2 healing a TRUE no-row straddle (pending
  wake wiped by backlog-clear, no delivery evidence) — is still UNPROVEN
- Criterion (c)=0 rows was a scenario-harness miss, not an F-2 code result

**R1 (with SIGSTOP+KILL) captured the TRUE PENDING state** but the
post-reboot state is stuck (anchor preserved, not wiped). The F-2 wedge
as defined (anchor-less) is essentially impossible to catch in a dev
environment because the anchor (message_queue row + report_injections
row) is created within ~70ms of the child's completion, and the wake
transitions to 'processing' within ~1s.

---

## Key Anomalies

1. **F-2 wedge window is <100ms in dev env** — the report_injections row
   is created at the same time as the child's completion. The wake
   transitions to 'processing' within ~1s. Lane 2's anchor-less filter
   skips because the anchor exists.

2. **SIGSTOP technique creates a stuck state** — the wake in 'ready' is
   preserved by F-1, not wiped. Lane 2 correctly skips (anchor exists).
   Watchdog correctly doesn't fire (carrier exists). Worker pool per-instance
   guard blocks the 'ready' message while parent is on another task.

3. **Pre-existing DependencyBus crash** — NOT caused by F-1/F-2 branch.
   `child_reports.py:2841` not touched by the branch. See R0.

4. **Kill-switch OFF boot verified** — `lanes=[no_row_backstop=False]`
   in boot log. Service is constructed but lane 2 is disabled.

---

## Port-Safety Evidence

| Port | Owner at START (pid) | Owner at END (pid) | Untouched? |
|------|---------------------|-------------------|------------|
| 8088 | UNBOUND | UNBOUND (re-verified per Leader note) | YES — never touched |
| 9797 | ensemble-prod (3321986) | (re-verify at cleanup) | YES — live prod |
| 7979 | ensemble-prod (3457886) | (re-verify at cleanup) | YES — demo service |
| 8079 | v0.16.11 dev (3567513) → SIGTERM'd | (restore at cleanup) | N/A (test target) |

---

## Preflight Evidence

- HEAD: `fb0f4655fafaad1385d0551a82e5c04348c01d3c` ✓
- daemon.__file__: `/home/nea/ensemble-src-wt-durability/daemon/__init__.py` ✓ (with PYTHONPATH override for symlinked .venv)
- Factory engine line: `Creating PostgreSQL engine: 127.0.0.1:5432/ensemble_dev` ✓
- /livez version: `0.16.13` (worktree, not 0.16.11) ✓
- /readyz components: all true ✓
- /proc/<pid>/environ: dev-clean ✓

---

## Recipe Pointer

- R18 recipe: `/home/nea/ensemble-src/.agents/tester/LESSONS/2026-10-04-r18-dev-e2e-boot-recipe-and-pairing-gotchas.md` (READ-ONLY)
- Wrapper: `/tmp/durability-f1f2-wc-wedge-demo-boot.sh`
- Data dir: `/home/nea/dev-daemon-8079-durability-f1f2/data/`
- Logs: `/home/nea/dev-daemon-8079-durability-f1f2/logs/`
- Poller: `/tmp/f2-r1-poller.py` (psycopg, SIGSTOP technique)
- Per-leg env override: `/tmp/l2-lane2-off.env` (L2 kill-switch OFF)
- Lane-2 kill-switch env var: `SERVICES_REPORT_DELIVERY_RECOVERY_LANE_NO_ROW_BACKSTOP`
- Lane-2 kill-switch config: `daemon/config.py:1350` (`report_delivery_recovery_lane_no_row_backstop: bool`)
- Lane-2 sweep log marker: `RDRS lane 2: post-wipe recovery (no-row backstop; F-2 wedge closure per durability-f1-f2 / phase2 task 2.8)`
- Lane-2 per-row markers: `sweep no_row_backstop ...` (only when rows processed)
- F-2 wedge poller technique: SIGSTOP in same iteration as observe, then DB-verify, then SIGKILL

---

## Commits

- Previous run: `b24cab33 test: durability F-1/F-2 demo E2E evidence (G4)`
- This run: (to be added) `test: durability F-1/F-2 demo E2E evidence (G4 redo)`
