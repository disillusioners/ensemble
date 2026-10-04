# Test Strategy: auto-continue RUNNING instances after daemon restart

Conventions: pytest under `.venv/bin/pytest` (CPython 3.13.15); unit tests in `tests/unit/{services,repositories}/`; new `.py` files MUST open with `from __future__ import annotations` (repo trap #1); packs are transparent bash wrappers under `test/packs/` with the Layer-2 watchdog (`timeout 110s`) and `RESULT: PASS/FAIL/TIMEOUT` protocol; PACKS.md rows are TESTER-OWNED (this document registers SPECS; the implementation lane creates the wrapper scripts; the tester lane edits PACKS.md).

---

## 1. Unit / Integration Matrix

| # | Area | Test file (NEW unless noted) | Cases | AC |
|---|------|------------------------------|-------|----|
| M1 | CAS column schema | `tests/unit/repositories/test_auto_continue_candidates.py` | Column present on model; NULL default; old rows unaffected | AC5 |
| M2 | Candidate selection | same as M1 | Matches dormant RUNNING `process_message`/`process_report`; excludes `auto_continued_at >= boot_epoch`; excludes PAUSED/TERMINAL/COMPLETED/ERROR/FAILED instance rows; excludes WAITING_CHILDREN instance rows; excludes other task_types; deterministic `created_at ASC` order; read-only (no mutation) | AC1, AC5 |
| M3 | CAS stamp | same as M1 | First stamp True; same-epoch restamp False; older-epoch after newer False; stamps nothing when row left running-set; `rowcount==1` contract | AC5 |
| M4 | Pass orchestration (happy) | `tests/unit/services/test_auto_continue_boot_pass.py` | Candidate → `_has_checkpoint` True → resume called with EXACT kwargs (`silent=True`, `target_work_id`, `handle_work_id`, `selected_suspension_reason=None`, `route_outcome="boot_continue"`) → stamp called → `scheduled` counted; zero `enqueue_message` calls (structural) | AC1, AC2 |
| M5 | No-checkpoint skip | same as M4 | `_has_checkpoint` False → skip, NO stamp, `skipped_no_checkpoint` counted, next-boot retry implied | AC5, AC6 |
| M6 | Resume-refused skip | same as M4 | Resume returns None / non-`resuming` status (incl. `already_resuming`) → skip, NO stamp, `skipped_resume_refused` counted | AC5 |
| M7 | Per-instance isolation | same as M4 | Candidate 1 raises (injected) → candidate 2 still processed; `errors` counted; loop never raises | AC6 |
| M8 | Sweep-level guard | same as M4 | Selection/loop raises → WARNING logged, result with errors returned, no exception escapes | AC6 |
| M9 | Lifespan envelope | same as M4 (seam: monkeypatch the pass to raise) | `api.py` boot continues; WARNING logged; listener-start path unaffected | AC6 |
| M10 | Kill-switch | same as M4 | `=0` → zero repo/resume calls, `skipped_kill_switch` counted, OFF log line; unset/`=1`/other → ON; per-boot read (no cache) | AC9 |
| M11 | Boot-epoch fallback | same as M4 | `boot_epoch=None` → WARNING + `datetime.now(utc)` fallback, pass proceeds | AC6 |
| M12 | Reboot loop | same as M4 | boot1 stamp epoch-1 → crash → boot2 epoch-2 re-selects + re-schedules (safe); terminal-between-boots → NOT selected; no double-stamp without intervening schedule | AC5 |
| M13 | AC4 interleaving (THE pin) | `tests/unit/services/test_auto_continue_interleaving.py` | See §2 — full interleaving X sequence with wake journal + claim-guard + deterministic LLM stub; log contract (1×`[BOOT_CONTINUE]`, 1×wake, 0×`already_resuming`) | AC4 |
| M14 | Negative lock-out (Y rejected) | same as M13 | Terminalize-early simulation documents the double-turn window; structural source-scan: pass contains NO `force_cancel`/`find_stale_running_tasks`/task-status writes | AC4 |
| M15 | Carve-outs | same as M13 | PAUSED instance never selected; PAUSED task for RUNNING instance never selected (instance-subquery); terminal instance zero-candidates + zero writes; WC zero-candidates + bus-owns comment | AC1 |
| M16 | Boot-order placement pin | same as M13 | Source-order assertion: pass call between `sweep_wake_records()` await and `upgrade_journal_sweep.start()` in `api.py` | AC4 |
| M17 | PG/SQLite predicate parity | `tests/unit/repositories/test_auto_continue_candidates.py` (parametrized where in-memory testable) | Timestamp comparison semantics identical; `IF NOT EXISTS` PG leg covered by boot smoke (PG dev DB when reachable) | AC5, AC7 |

Integration (not unit): M13 uses the REAL `claim_pending_task` + REAL `UpgradeJournalSweepService` against a temp journal — that is the integration core. The cascade e2e pack (G3 below) and the demo E2E (§4) cover the rest.

## 2. AC4 Interleaving Pinning Test Design (executable form of analysis §C)

**Name:** `test_boot_continue_x_wake_sweep_interleaving_does_not_double_fire` (+ `test_terminalize_early_opens_claim_window_is_rejected`, + carve-outs + placement pin).

**Fixture state (one instance, both hazards):**
- `instances.status='running'`, orphan `Task(status='running', task_type='process_message', work_id=W1)`
- pending_wakes journal record (temp `install_dir`) targeting the same instance
- deterministic LLM stub that completes the checkpoint on resume

**Sequence + assertions (Interleaving X):**

| Step | Action | Assert |
|------|--------|--------|
| 1 | Run `sweep_wake_records()` (real service, temp journal) | wake Task row exists PENDING; orphan W1 still RUNNING; instance status unchanged; wake journal record marked delivered |
| 2 | Run `continue_running_instances_after_restart(manager, boot_epoch)` | resume scheduled for W1 (NOT the wake's task); CAS stamped on W1; wake Task STILL PENDING (claim-guard held); `ContinueResult.scheduled==1` |
| 3 | Complete W1's turn via the stub | W1 → terminal; instance → natural terminal flow |
| 4 | Call `claim_pending_task` | wake Task claimed NOW (guard unblocked by W1 terminal) |
| 5 | Log contract | exactly 1×`[BOOT_CONTINUE]` for the instance; 1×wake delivery; 0×`already_resuming` |

**Negative test (Y):** force-cancel W1 BEFORE the wake claims → wake claimable immediately → demonstrates the double-turn window that shipped code must not create; structural source-scan pins the absence (`no force_cancel / no reaper / no task-status writes` in `auto_continue_boot_pass.py`).

**Mutation checks (run once in scratch, then revert):** (a) reorder pass before wake sweep → M13/M16 fail; (b) add a reaper to the pass → M14 structural fails.

## 3. Pack SPECS (PACKS.md edit DEFERRED to tester lane)

| SPEC field | Pack 1 | Pack 2 |
|---|---|---|
| **Name** | `auto_continue_boot_pass_unit_test` | `auto_continue_interleaving_unit_test` |
| **Script** | `test/packs/auto_continue_boot_pass_unit_test.sh` | `test/packs/auto_continue_interleaving_unit_test.sh` |
| **Tested files** | `tests/unit/services/test_auto_continue_boot_pass.py` + `tests/unit/repositories/test_auto_continue_candidates.py` | `tests/unit/services/test_auto_continue_interleaving.py` |
| **Scope** | Boot pass orchestration (selection→checkpoint→resume→CAS stamp), kill-switch, boot-epoch fallback, per-instance/sweep/lifespan isolation, reboot-loop idempotency, candidate-selection + CAS SQL | AC4 interleaving X pin, terminalize-early rejection (negative + structural), PAUSED/terminal/WC carve-outs, api.py boot-order placement pin |
| **Trigger** | Any change to: `daemon/services/auto_continue_boot_pass.py`, `daemon/api.py` lifespan block, `daemon/repositories/task/repository.py` (M2/M3 methods), `daemon/repositories/task/models.py` (column), `daemon/migrations/versions/20261004_*`, `daemon/manager.py` `_ensure_postgres_columns` | Any change to the above PLUS any change to `claim_pending_task` guard, `sweep_wake_records`, `api.py` boot ordering, or `UpgradeJournalSweepService` |
| **Expected evidence** | `RESULT: PASS`, N/N cases, <110 s | `RESULT: PASS`, N/N cases, <110 s |
| **Wrapper template** | `test/packs/post_restart_arm_notify_sweep_unit_test.sh` (verbatim structure) | same |

**PACKS.md hand-off note (implementation lane must carry):** "Register `auto_continue_boot_pass_unit_test` + `auto_continue_interleaving_unit_test` rows from the SPECs in `.agents/shared/planning/auto-continue-running-after-restart/test-strategy.md` §3."

## 4. Gate Plan (execution-lane intersection — LIKELY NON-EMPTY: resume = re-dispatch)

Per the 2026-10-03 precedent (v0.13.10 targeted-pack gate missed full-dir reds; lesson: full-dir per merge):

1. **Pack gates:** both packs green (§3).
2. **Full-dir gate:** `.venv/bin/pytest tests/unit/job_queue tests/unit/services tests/unit/repositories -q` — green, or pre-existing reds triaged with A/B evidence (base-commit vs feature-commit, the `.agents/tester/RESULTS/` format).
3. **Cascade e2e gate:** run the current canonical parent/child cascade e2e pack — grep-verify the canonical name at execution time (`test/packs/` candidates: `child_parent_lifecycle_regression_test.sh`, `completion_regression_test.sh`; anchors drift — trap #3). Green required.
4. **Lane-site drift rule:** every execution-lane citation grep-verified at use time. Corrected anchors at cf8efbef: `task_processor.py:267` (re-evaluation signal), `message_processing_pipeline.py` (replaced deleted `message_job_handler.py`), `claim_pending_task` at `task/repository.py:1733+` (guard at `:2230-2294`), alt sighting `task_processor.py:1797`.
5. **Terminal-token trap discharge:** the feature adds NO new terminal token (observer accepts only `completed/error/failed`, `job_feedback_observer.py:316`) — asserted by M4's exact-kwargs resume check + cascade e2e gate.

## 5. AC8 Demo E2E Procedure (runnable scenario outline)

**Environment:** REAL demo daemon — port 7979, `~/agents-ensemble-demo/`, NON-live target; mid-work restart explicitly sanctioned by the user. Feature-branch code deployed to demo. Confirm exact stop/start commands at run time (drift rule — inspect `launcher.sh` + `releases/` layout first).

**Steps:**

```
0. PRE-FLIGHT
   - P4 gates green; feature branch deployed to demo (commit recorded)
   - demo .env does NOT set ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART=0
   - capture before-state: instance list/statuses, log tail with boot marker
   - (safety) cp -a demo data dir → /tmp/demo-e2e-backup-<ts>/
1. DISPATCH (demo chat or HTTP :7979)
   - mission to a delegation-capable leader:
     "Delegate a child task: sleep 120 seconds, then say hello. Wait for it."
2. ARM THE SCENARIO (poll; no interaction)
   - parent: WAITING_CHILDREN (authority: bus count >0; status string cosmetic)
   - child: RUNNING, mid-sleep; Task row status='running'
   - record timestamps
3. RESTART MID-WORK (user-sanctioned)
   - t_stop; stop demo daemon; start demo daemon; t_boot
   - boot log MUST show: "AutoContinue boot pass: ContinueResult(candidates>=1, ...)"
   - exactly ONE "[BOOT_CONTINUE] instance=<child id8>"; ZERO for the parent (WC skip)
4. VERIFY CONTINUE-FROM-CHECKPOINT
   - "[RESUME] instance=<child id8> has_checkpoint=True" in log
   - child completes REMAINING sleep (not full 120 s from scratch) → hello arrives
   - ZERO manual pings between t_stop and hello (transcript audit)
5. AC3 LEG — parent wake from child report
   - child terminal → dependency_watchers FIRED → report enqueued → parent
     processes PROCESS_REPORT → parent wakes + synthesizes, no manual message
   - log evidence: boot "bus start: warmed=... recovered=... swept=..." line
6. AUDIT (no lost / no duplicated)
   - hellos=1; child completion reports processed by parent=1
   - BOOT_CONTINUE lines=1; already_resuming=0
   - duplicate MessageQueue internal_report rows=0; parent double-wake=0
7. EVIDENCE BUNDLE → .agents/tester/RESULTS/<date>-auto-continue-demo-e2e.md
```

**Evidence-capture checklist:** deployed commit hash · boot-pass ContinueResult line (t_boot) · `[BOOT_CONTINUE]` child-only lines · pre-restart state table (parent WC/bus-count, child RUNNING) · `has_checkpoint=True` line · hello delivery proof + timestamp · zero-manual-ping transcript audit · bus boot + wake lines · count-audit table · t_stop/t_boot/hello/parent-wake timeline · explicit PASS/FAIL verdict per AC1/AC3/AC8 with artifact refs.

**Failure handling:** any deviation → capture forensics, triage against risk-register (R1/R2/R5), do NOT iterate blindly; demo rollback via its releases/ layout if needed.

---

## Coverage summary

AC1: M2/M4/M15 (+E2E) · AC2: M4 structural grep-proof · AC3: E2E step 5 (+M15 WC skip) · AC4: §2 all · AC5: M1/M3/M12 (+E2E audit) · AC6: M7/M8/M9/M11 · AC7: §3/§4 · AC8: §5 · AC9: M10.
