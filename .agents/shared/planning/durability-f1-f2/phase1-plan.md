# Phase 1: F-1 Fix — DependencyBus None-error + Wipe-side Preserve Extension

Date: 2026-10-04
Worktree: `/home/nea/ensemble-src-wt-durability` @ `18827dbd` (branch `feature/durability-f1-f2`)
Author: planner via plan-creation worker
Status: Draft

> **Scope reminder.** This phase closes the F-1 wedge: a child
> that is mid-`discard_on_startup` wipe causes
> `DependencyBus` to flip `_parent_errored=True` from a
> `Outcome(status="error", error=None)`, and the next boot's
> wipe deletes the flipped-terminal row before the auto-continue
> boot pass can see it (candidates==0). Outcome: parent
> permanently wedged in `waiting_children`.
>
> **Re-anchor notice.** All anchors in this phase plan are
> verified on the worktree at `18827dbd`. The two bus gates
> are at `:671` (emit_terminal) and `:859`
> (emit_terminal_for_child_instance). Implementation occurs
> in this worktree; the main workdir is read-only.
>
> **Repo gotchas (binding).** All NEW Python files carry
> `from __future__ import annotations` at the top of the
> module. Multi-edit batches need grep / read-back
> verification (known silent-failure mode). No new terminal
> status tokens (none are added by this phase).

---

## Objective

Close the F-1 wedge with two coupled changes plus
documentation:

1. **Write-side truthy-error gate** at the two DependencyBus
   gates (`:671`, `:859`).
2. **Wipe-side preserve-predicate extension (2-arm disjunction
   per W-3, REVISION CYCLE 2):** terminal rows with an
   `auto_continued_at` marker AND an existing, non-terminal
   instance row survive the `discard_on_startup` wipe
   (closing the boot N+1 candidate == 0 condition). Arm 2
   (`last_heartbeat_at >= boot_epoch`) is **DROPPED per W-3**
   — vacuous at the wipe seam.
3. **Arm-3 lifecycle bound (composition per W-3):** marker-
   clearing at the terminalizer call site + instance-non-
   terminal co-condition on arm 3 (closes the unbounded
   `auto_continued_at` leak — see `decisions.md §13b`).
4. **Boot-sequence ownership contract** documented in
   `daemon/services/instance_lifecycle.py` (alongside — as the
   FIRST labelled block in the file — the Pause-First Then
   Quiesce convention per the W-5 re-anchor) with inline
   comments at the two boundary lines.

**Issue-1 / Issue-2 evidence bar (REVISION CYCLE 2,
ITERATION-002).** The S1-S7 success criteria (see
`plan-overview.md`) are **plan-level obligations** for
the developer's separate test-rework dispatch; the
landed test file `tests/unit/repositories/test_task_auto_continued_lifecycle.py`
is being reworked by the developer. The plan encodes the
obligations; the plan does NOT encode test code. The
evidence bar requires:
* **S1 (double-restart) — REAL file-backed-SQLite two-boot
  test** using the F9-parity harness
  `tests/unit/repositories/test_task_auto_continued_lifecycle.py`
  (already on the branch per dispatcher-verified). The
  test executes real SQL (real `clear_all` preserve on
  BOTH repositories) + real persistence across TWO
  manager constructions. The S1 measures (no double-
  continue, `already_resuming==0`, boot-pass CAS stamp
  actually invoked) are exercised by this test. The
  Issue-2 two-boot test also satisfies the Issue-1
  real-SQL requirement for the task-side predicate.
* **S4-S7 (preserve + kill-switch) — REAL SQL** on
  BOTH repositories, including the arm-3 `EXISTS
  instances` join and kill-switch gating. NOT a Python
  re-implementation of the predicate.
* **Task-1.9 (queue-side 2-arm disjunction) — NEW
  S-test** added (currently zero coverage per the
  approver's observation). Exercises the
  `message_queue/repository.py:1002-1008` predicate
  under the same real-SQL discipline.
* **Arm 3 keep-green coverage** — the existing
  `auto_continued_at IS NOT NULL` arm-3 test is
  preserved across the reworked test file.
* **PG-parity note (carried, not obligation):** where
  the harness supports it, the developer notes
  PG-parity evidence for the arm-3 SQL (e.g. the
  `clear_all` predicate's `EXISTS instances` subquery
  is PG-compatible — verified by the worktree's existing
  test schema).

Acceptance: the S1-S7 success criteria from `plan-overview.md`
are green; the F-1 kill-switch test (W-3 / S7 — added in
task 1.14 below) is green; the four keep-green auto-continue
unit-test files + two packs (`decisions.md §8`) stay green;
the F-1 marker-clearing at the terminalizer call site is
green; the 🟢 Q6 re-scan mandate is added to task 1.1.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1.1 | Read `research-findings.md §1` and `decisions.md §1, §1a, §2, §2a, §2b, §2c, §5, §6, §13` end-to-end before any edit. **🟢 Q6 re-scan mandate:** before the first edit, re-run Explorer A's Q6 surprise scan (`status == "error"` consumers — only `:671` / `:859` of `dependency_bus.py` should match) to confirm no new error sites have been added since the prior cycle. The re-scan is a `grep -n 'status == "error"' daemon/ daemon/services/` + a manual pass over the matches; the result is recorded in the implementer's commit message. | none | Reviewer can confirm the implementing developer has internalized: (i) the two gate sites only, (ii) the helper extraction rationale, (iii) the arm-3-only disjunction (arm 2 DROPPED per W-3), (iv) the producer-contract no-action decision, (v) the arm-3 lifecycle bound (clearing + co-condition composition per W-3 / §13b), (vi) the W-5 re-anchor that the `:256` anchor is the `(terminated, n/a)` Outcome lane, not an error lane. |
| 1.2 | **🟢 LANDED 2026-10-04.** Add `_has_truthy_error(outcome: Outcome) -> bool` helper at module top of `daemon/services/dependency_bus.py` (right after the imports; before any class defs). Use `from __future__ import annotations` at the top of the file (the file may not currently carry it; if not, add it). The helper body: `return outcome.status == "error" and bool(outcome.error)`. Unit-tested at the helper level by `test_discard_on_startup_dependency_bus_race.py`. **Read-back verification (per ITERATION-002):** the helper def landed at `dependency_bus.py:98` (reviewer-cited `:111` was wrong; re-grep before writing); the surrounding module-top imports + the `Outcome` class are unchanged. | 1.1 | Helper exists at `:98`; file compiles; existing bus tests stay green. |
| 1.3 | **🟢 LANDED 2026-10-04 (concurrent landing in the UNCOMMITTED working tree atop `18827dbd`).** Read-back VERIFICATION task: verify the landed gate edits match the plan's spec. **DISPATCHER-VERIFIED LANDED ANCHORS** (re-grep before writing — the developer may have advanced further): (a) `_has_truthy_error` def at `dependency_bus.py`**:98** (reviewer cited `:111` — landed at `:98`); (b) truthy-error gate calls at `dependency_bus.py`**:702** (first gate, `emit_terminal`) and **`:907`** (second gate, `emit_terminal_for_child_instance`); (c) None-path defensive `WARNING` log references at `dependency_bus.py`**:697** (first gate) and **`:901`** (second gate). **Read-back checklist:** (i) gate at `:702` is `if _has_truthy_error(outcome):`; (ii) gate at `:907` is `if _has_truthy_error(outcome):`; (iii) the inside block at both gates (`if outcome.error: ... else: setdefault(...)`) is unchanged; (iv) the None-path `WARNING` log does NOT execute when `outcome.status != "error"` (no log noise on success). The pre-landing anchors `:671` / `:859` are SUPERSEDED by the landed state. | 1.2 | Verification passes; landed gates match the spec; the helper is byte-exact; no log noise on success. |
| 1.4 | **🟢 LANDED.** Mirror the gate change at the second gate (`emit_terminal_for_child_instance`) at `dependency_bus.py`:907` (per the dispatcher-verified landing at `:907`; the prior cycle's pre-landing anchor `:859` is SUPERSEDED). Read-back: confirm the change is byte-exact and the surrounding block is otherwise unchanged. | 1.2 | Gate at `:907` is `if _has_truthy_error(outcome):`; the inside block is unchanged. |
| 1.5 | **🟢 LANDED.** The None-path defensive `WARNING` log was added at `dependency_bus.py:697` (first gate) and `:901` (second gate) per the dispatcher-verified landing (the prior cycle's pre-landing anchor `:692`/`:895` is SUPERSEDED). Read-back: confirm the warning does NOT execute when `outcome.status != "error"` (no log noise on success). | 1.3, 1.4 | Both gates log on the None path; no log noise on the success path. |
| 1.6 | **🟢 LANDED.** The inline comment at the first gate (`dependency_bus.py:702` per the dispatcher-verified landing) references the boot-sequence ownership block in `daemon/services/instance_lifecycle.py` and states the gate's role in the F-1 contract. The comment includes the literal string `instance_lifecycle.py` so a reader can navigate in two hops. | 1.3 | Comment present; reader can navigate to the ownership block. |
| 1.7 | **🟢 LANDED.** `capture_boot_epoch(self.engine)` invocation at the top of the `InstanceManager.__init__` wipe block in `daemon/manager.py:771-873` (BEFORE the queue `clear_all(preserve_in_flight=True)` at `:772`). The call is wrapped in a `try/except` that swallows the `boot_epoch.capture_boot_epoch` failure. The import is a lazy import inside the wipe block to avoid any module-load-time cycle. **Why RETAINED even though arm 2 is dropped:** `boot_epoch` is also consumed by the auto-continue boot pass at `api.py:1553` (in Phase 1 scope) and by `stale_task_recovery.py:766/:407` + `repository.py:3363`. Removing the constructor capture would break the auto-continue pass's epoch availability during the constructor's wipe. The F-1 `None` boot_epoch fallback in `decisions.md §2a` still applies to the auto-continue pass's degraded behavior. Read-back: confirm the call is BEFORE the wipe and that the existing wipe block (`:771-873`) is otherwise unchanged. | 1.1 | `capture_boot_epoch(self.engine)` runs before the wipe; `api.py:412` retains its call (the function is first-wins / idempotent); arm-3 predicate's `EXISTS instances` join does NOT depend on `boot_epoch` (the arm-3 co-condition is on `instances.status` only). |
| 1.8 | **🟢 LANDED.** At `daemon/repositories/task/repository.py:4380-4394`, the 2-arm disjunction (arm 1 + arm 3 with `EXISTS instances` co-condition per W-3) is in place per the dispatcher-verified landing. Read-back: confirm the existing FP1 `NOT EXISTS` JobItem-anchor clause is preserved; the kill-switch `ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE` (per `decisions.md §2c` / §13c) gates arm 3. | 1.7 | 2-arm disjunction in place (arm 2 DROPPED); `EXISTS instances` join is the FIRST instance-join in `clear_all` SQL; arm 3 is gated by `ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE`; existing FP1 NOT EXISTS clause preserved. |
| 1.9 | **🟢 LANDED.** At `daemon/repositories/message_queue/repository.py:1002-1008`, the queue-side 2-arm disjunction (symmetric to task-side per W-3) is in place per the dispatcher-verified landing. Read-back: confirm the existing task-side status-only subquery shape is preserved. | 1.7, 1.8 | Queue-side 2-arm disjunction in place; symmetric to task-side. |
| 1.10 | **🟢 LANDED.** The numbered ownership-contract block is in `daemon/services/instance_lifecycle.py` (as the FIRST labelled block in the file per W-5 re-anchor). The block is a 13-step list mapping 1:1 to `decisions.md §5` (InstanceManager ctor → manager.initialize → capture_boot_epoch → critical-notes probe → execution-gate stale-lease recovery → setup_worker_pool → init_dependency_bus → finalization recovery loop → upgrade journal boot reconcile → auto-continue boot pass → **RDRS lane-2 boot pass (already wired per W-4 pivot, no new boot step; see `decisions.md §12a` B2 + §5 step 11 RETIRED note)** → upgrade journal start → service reconciliation). Read-back: confirm step 11 reflects the post-pivot reality (RDRS lane-2 boot pass, NOT the eliminated `wc_wedge_sweep.py`). | 1.7 | Block present; matches the 13-step list in `decisions.md §5`; step 11 reflects the post-pivot reality. |
| 1.11 | **🟢 LANDED.** At `daemon/repositories/task/repository.py:4380` (the preserve DELETE — already touched in 1.8), the inline comment that names the `instance_lifecycle.py` ownership block and states the wipe-side role is in place. The comment includes the literal string `instance_lifecycle.py` so a reader can navigate in two hops. | 1.10 | Comment present; reader can navigate to the ownership block. |
| 1.12 | (Verification) Run the keep-green pack: `bash test/packs/auto_continue_boot_pass_unit_test.sh`. All four unit-test files + this pack must be green. If anything is red, the F-1 change is the cause; investigate before proceeding to 1.13. | 1.3, 1.4, 1.7, 1.8, 1.9 | Pack exits 0; `RESULT: PASS` line at the end. |
| 1.13 | (Verification) Run the new F-1 unit pack: `bash test/packs/discard_on_startup_dependency_bus_race_unit_test.sh`. The pack runs the seven F-1 unit tests from `tests/unit/services/test_discard_on_startup_dependency_bus_race.py` (created in 1.14). All seven must be green (S1-S7, including the F-1 kill-switch test). | 1.12 | Pack exits 0; `RESULT: PASS` line. |
| 1.14 | Create `tests/unit/services/test_discard_on_startup_dependency_bus_race.py` (NEW FILE). The file uses `from __future__ import annotations`. The seven tests are: `test_none_error_does_not_flip_parent_error` (S2); `test_real_error_flips_parent_error` (S3); `test_terminal_auto_continued_survives_clear` (S4); `test_terminal_no_marker_deleted_by_clear` (S5); `test_boot_sequence_mock_candidates_one` (S6); `test_double_restart_no_double_continue` (S1); `test_boot_auto_continued_preserve_kill_switch` (W-3 / S7 — NEW, RENAMED from prior cycle's `test_boot_epoch_preserve_heartbeat_kill_switch`). The kill-switch test pins BOTH the ON path (arm 3 active: terminal-stamped row of non-terminal instance survives `clear_all`) AND the OFF path (arm 3 disarmed: terminal-stamped row is deleted; arm 1 (status) stays active in BOTH cases). The test sets `os.environ["ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE"] = "1"` and `"0"` in sequence (renamed from the prior cycle's `ENSEMBLE_BOOT_EPOCH_PRESERVE_HEARTBEAT`), runs the predicate, and asserts the expected keep-set in each case. **Per ITERATION-002 Issue-1: the wipe-seam tests (S1, S4, S5, S6, S7 + the FP1 keep-green pin) execute the REAL `TaskRepository.clear_all(preserve_in_flight=True)` SQL on a real DB session (file-backed SQLite, F9-parity harness mirroring `tests/unit/repositories/test_task_auto_continued_lifecycle.py`) — NOT a Python re-implementation of the predicate. The bus-logic tests (S2, S3) stay mock-level per ITERATION-002 (the `DependencyBus` does not require SQL). Per ITERATION-003: S1 also executes the REAL `find_auto_continue_candidates(boot_epoch=boot)` selection against the real engine and asserts the straddled row is in the candidate set (the `candidates==1` measure).** | 1.13 | Test file compiles; all seven tests are deterministic; full pack is green. |
| 1.15 | Create `test/packs/discard_on_startup_dependency_bus_race_unit_test.sh` (NEW FILE). The pack follows the transparent-wrapper convention: `set -u`; `cd` to repo root; `PACK_NAME=discard_on_startup_dependency_bus_race`; inner `timeout 110s .venv/bin/pytest tests/unit/services/test_discard_on_startup_dependency_bus_race.py --tb=short -q`; exit-code mapping `0=PASS / 1=FAIL / 124=TIMEOUT`; trailing `RESULT: PASS/FAIL/TIMEOUT` line. | 1.14 | Pack runs and exits 0; `RESULT: PASS`. |
| 1.16 | (Verification) Run the full-dir pytest sweep at the project level: `bash test/packs/auto_continue_boot_pass_unit_test.sh && bash test/packs/discard_on_startup_dependency_bus_race_unit_test.sh && .venv/bin/pytest tests/ -q --tb=line` (or the project's standard full-dir invocation). Confirm zero new failures vs. baseline. **Note (carried, ITERATION-002):** task 1.17 (marker-clearing at terminalizer call site) ships BEFORE 1.16 closes — the `Depends On` column reflects this (1.8, 1.10, **1.17**); the full-dir sweep at 1.16 must include the marker-clearing. | 1.13, 1.15, 1.17 | Full-dir sweep is green; no regressions; includes marker-clearing test (task 1.17). |
| 1.17 | **(NEW — W-3 / §13b marker-clearing at terminalizer call site.)** Add a new repository method `clear_task_auto_continued(task_id)` at `daemon/repositories/task/repository.py`, mirroring the existing `mark_task_auto_continued` at `:1017-1067` (single-writer stamp method — per the reviewer's 🟢 correction: def line at `:1017`, not `:988`). Method body: `UPDATE task SET auto_continued_at=NULL WHERE id=:task_id AND status='completed'`. The gate is at the terminalizer call site in `daemon/manager.py:11660-11721` (gate conjunct at `:11700-11702`): AFTER successful `complete_task`, invoke the new `clear_task_auto_continued` method. The shared `complete_task` SQL stays byte-identical per the D18 r3 / D29 RATIFIED pattern (per the `manager.py:11673-11683` comment: "the r2 fold's … conjunct inside the shared SQL was REJECTED in D29"). The new method has a unit test in a new test file `tests/unit/repositories/test_task_auto_continued_lifecycle.py` (mirrors the auto-continue test conventions). Implementation order: 1.17 ships BEFORE the verification gate at 1.16 closes (i.e., the full-dir sweep at 1.16 must include the marker-clearing). The kill-switch from 1.8 does NOT gate the marker-clearing — clearing is unconditional once the terminalizer commits; the kill-switch governs only the WIPE-SIDE preserve predicate. | 1.8, 1.10 | `clear_task_auto_continued` method exists; terminalizer calls it after `complete_task`; shared `complete_task` SQL unchanged; new test file passes; integration with the terminalizer is verified. |

**Total Phase 1 tasks: 17** (including verification steps).
Tasks 1.12, 1.13, 1.16 are verification gates, not implementation;
task 1.17 (NEW marker-clearing) is implementation that must
ship BEFORE the 1.16 verification closes. The implementation
count is 11 (1.1-1.11, with 1.14, 1.15, 1.17 being test-file /
repository-method creation).

**Implementation order note (W-3 / §13b).** Despite the task
numbering, the runtime implementation order is:
1.1-1.11 (read + bus gates + predicate extension + ownership
block) → 1.17 (marker-clearing at terminalizer) → 1.12-1.16
(verification gates, including the full-dir sweep at 1.16
which must include the marker-clearing). The verification
gates 1.12-1.15 are the F-1 unit-pack gates; 1.16 is the
project-level full-dir sweep. Task 1.17's `Depends On` column
makes the order explicit (1.8, 1.10).

---

## Coupling

* **Tight with Phase 2 (F-2 fix under W-4 PIVOT):** Phase 1
  inserts the epoch capture into the constructor (1.7 — RETAINED
  per §13a for the auto-continue consumer); Phase 1 introduces
  the FIRST instance-join in `clear_all` SQL (1.8 — the arm-3
  `EXISTS instances` co-condition per §13b). **NOTE:** Phase 2's
  RDRS lane-2 query uses a DIFFERENT join — the pre-existing
  `FROM instances c JOIN instances p` at
  `report_injection/repository.py:1216-1220` (not the
  `EXISTS instances` subquery from 1.8). The two joins are
  RELATED (both anchor on `instances` for the same
  instance-row-wipe-immunity property) but are NOT the
  same join — see N-4 anchor-conflation fix in
  `revision-summary.md`. A regression in 1.8 (the F-1 arm-3
  join) does NOT break Phase 2's anchor; Phase 2's anchor
  is pre-existing and unchanged. **The marker-clearing at
  the terminalizer (1.17) shares
  the `manager.py:11660-11721` call site with the F-2 path:**
  F-1 clears the `auto_continued_at` marker; F-2's terminalizer
  gate conjunct at `:11700-11702` accepts only CAS-stamped
  rows. A regression in 1.17's terminalizer integration
  affects both F-1 (residual leak) and F-2 (terminalizer
  acceptance).
* **Tight with `decisions.md §5`:** the ownership block in
  1.10 is the source of truth for the 13-step boot order; the
  inline comments in 1.6 and 1.11 reference it. A drift
  between the block and `decisions.md §5` is a
  documentation defect, not a code defect — fix the
  block, not `decisions.md`. The W-5 re-anchor (Pause-First
  Then Quiesce convention is the FIRST labelled block in
  `instance_lifecycle.py` — beside ≠ after) is documented in
  the block's preamble.
* **Loose with the auto-continue feature:** the keep-green
  list (per `decisions.md §8`) is the cross-feature regression
  gate; Phase 1 does not modify any auto-continue code, but
  Phase 1's bus-side fix (1.3, 1.4) is in a file
  (`dependency_bus.py`) that the auto-continue feature also
  reads from. Phase 1's terminalizer integration (1.17)
  touches `manager.py:11660-11721` which is in the auto-
  continue feature's manager.py surface — read-back
  verification is required per the binding repo gotcha.
* **Independent of:** the F-2 design (a) follow-up
  (`decisions.md §3a`) — that work is deferred and does not
  touch Phase 1's changes.

---

## Risks (Phase 1-specific; see `plan-overview.md` for the consolidated register)

* **R-P1-1: producer contract regression.** A future change
  in `child_reports.py:420, :653` that adds a new
  None-capable producer call site would silently bypass the
  gate. **Mitigation:** task 1.5's defensive `WARNING` log
  surfaces the regression in operator logs.
* **R-P1-2: epoch capture inside the constructor races with
  `manager.initialize()`.** **Mitigation:** task 1.7 wraps
  the capture in `try/except` and the predicate's `None`
  fallback (1.8 / 1.9 / `decisions.md §2a`) keeps the system
  booting in the absence of an epoch.
* **R-P1-3: the 2-arm disjunction over-preserves queue rows
  on a misconfigured `auto_continued_at` or `instances` join.**
  **Mitigation:** task 1.8's predicate requires the
  `EXISTS instances WHERE status NOT IN TERMINAL_INSTANCE_STATUSES`
  co-condition (per W-3), and the unit tests in 1.14 pin the
  behaviour across both kill-switch states (ON = arm 3
  active; OFF = arm 3 disarmed).
* **R-P1-3a: the marker-clearing at the terminalizer call
  site (task 1.17) is bypassed by the STR-terminalized
  residual.** **Mitigation:** the instance-non-terminal
  co-condition on arm 3 (task 1.8) covers the residual; the
  composition of (clearing + co-condition) per §13b closes
  the primary leak path. The unit test for the new
  `clear_task_auto_continued` method in
  `tests/unit/repositories/test_task_auto_continued_lifecycle.py`
  pins the clearing behaviour.
* **R-P1-4: multi-edit batch on `dependency_bus.py` and
  `repository.py` has a known silent-failure mode.**
  **Mitigation:** every multi-edit step in 1.3, 1.4, 1.8
  is followed by a read-back per the binding repo gotcha.

---

## Exit Criterion

This phase is DONE when:

* All 12 implementation tasks (1.1-1.11 + 1.17) are complete
  and each task's acceptance criterion is met.
* The keep-green pack (`auto_continue_boot_pass_unit_test.sh`)
  is green (task 1.12).
* The new F-1 unit pack
  (`discard_on_startup_dependency_bus_race_unit_test.sh`) is
  green with all seven tests passing, including the
  F-1 kill-switch test (tasks 1.13-1.15).
* The full-dir pytest sweep is green with zero new failures
  vs. baseline (task 1.16) — and the sweep INCLUDES the new
  marker-clearing test (task 1.17).
* The reviewer can read `decisions.md §1, §1a, §2, §2a, §2c,
  §5, §6, §13` and see each design choice reflected in the
  code (1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.17).

The phase is independently revertable: see
`plan-overview.md` Risks section for the per-phase rollback
story.

---

## File Inventory (Phase 1)

| Path | Change | Verified anchor |
|---|---|---|
| `daemon/services/dependency_bus.py` | Tasks 1.2, 1.3, 1.4, 1.5, 1.6 — helper extraction, gate changes at `:671` and `:859`, defensive log, inline comment. | yes |
| `daemon/manager.py` | Task 1.7 — `capture_boot_epoch(self.engine)` call in the wipe block (`:771-873`, BEFORE the queue `clear_all` at `:772`) — RETAINED per W-3 / §13a for the auto-continue consumer; Task 1.17 — terminalizer call-site gate at `:11660-11721` invokes the new `clear_task_auto_continued` method after successful `complete_task`. | yes |
| `daemon/repositories/task/repository.py` | Tasks 1.8, 1.11 — predicate extension at `:4380-4394` (2-arm disjunction with arm-3 `EXISTS instances` co-condition per W-3) + JOURNAL mirror at `:4337-4364`; inline comment at `:4380`; Task 1.17 — NEW `clear_task_auto_continued(task_id)` method mirroring the existing `mark_task_auto_continued` at `:1017-1067` (per the reviewer's 🟢 correction: def line at `:1017`, not `:988`). | yes |
| `daemon/repositories/message_queue/repository.py` | Task 1.9 — predicate extension at `:1002-1008` (2-arm disjunction, symmetric to task-side) + JOURNAL mirror at `:978-994`. | yes |
| `daemon/services/instance_lifecycle.py` | Task 1.10 — new ownership-contract block (as the FIRST labelled block in the file, beside the Pause-First Then Quiesce convention per W-5 re-anchor). | file exists; block to be added |
| `tests/unit/services/test_discard_on_startup_dependency_bus_race.py` | Task 1.14 — NEW FILE. Seven F-1 unit tests (incl. F-1 kill-switch S7 with renamed env var `ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE`). **Per ITERATION-002 Issue-1: S1, S4, S5, S6, S7 + the FP1 keep-green pin execute the REAL `TaskRepository.clear_all` SQL on a real DB session (file-backed SQLite, F9 parity). S2/S3 stay mock-level (bus logic). Per ITERATION-003: S1 also executes the REAL `find_auto_continue_candidates` selection.** | new file |
| `tests/unit/repositories/test_task_auto_continued_lifecycle.py` | Task 1.17 — NEW FILE. Unit tests for the new `clear_task_auto_continued` method (mirrors the auto-continue test conventions). | new file |
| `tests/unit/repositories/test_message_queue_clear_all_2arm_disjunction.py` | **Task 1.9 — NEW FILE (added per ITERATION-002 Issue-1, plan-overview S5).** Real-SQL coverage for the queue-side 2-arm disjunction on `MessageQueueRepository.clear_all` (file-backed SQLite, F9 parity). Backs the (a) arm-3 `EXISTS instances` join, (b) terminal-no-marker DELETE, (c) kill-switch ON preserves / OFF yields the pre-fix predicate, (e) Arm 1 (running/paused) preserved in BOTH kill-switch states. The queue side is intentionally asymmetric (no JobItem-anchor clause, per `research-findings.md §1 Q3`). | new file |
| `test/packs/discard_on_startup_dependency_bus_race_unit_test.sh` | Task 1.15 — NEW FILE. Transparent-wrapper pack. | new file |

**No changes to:** `daemon/services/stale_task_recovery.py`
(off the wedge path); `daemon/services/child_reports.py`
(producer side intentionally untouched); the four
auto-continue unit-test files + two packs (regression
gates only).
