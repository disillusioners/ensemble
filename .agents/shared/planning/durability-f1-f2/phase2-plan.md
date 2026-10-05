# Phase 2: F-2 Fix — RDRS Lane-2 Extension (W-4 PIVOT, REVISION CYCLE 2)

Date: 2026-10-04
Worktree: `/home/nea/ensemble-src-wt-durability` @ `18827dbd` (branch `feature/durability-f1-f2`)
Author: planner via plan-creation worker (amendment cycle)
Status: Draft

> **⚠ REVISION CYCLE 2 — MAJOR REWRITE.** The prior cycle's
> `wc_wedge_sweep.py` module is **ELIMINATED** by the W-4
> pivot (see `decisions.md §12`). The F-2 architecture is the
> **RDRS lane-2 extension** at
> `daemon/repositories/report_injection/repository.py:1039-1253`.
> The previously-planned kill-switch
> `ENSEMBLE_WC_WEDGE_SWEEP_ON_BOOT` is **RETIRED**. RDRS has
> its own per-lane kill switches in
> `daemon/config.py:1302-1378`. Do NOT implement anything from
> the prior cycle's phase 2 design — it is SUPERSEDED.
>
> **Scope reminder.** This phase closes the F-2 wedge: a
> child that completes just before restart leaves
> wake-rows (PENDING `process_report` Tasks + their backing
> `internal_report:` `message_queue` rows) which the
> `discard_on_startup` wipe at the `InstanceManager`
> constructor deletes; the parent, waiting in
> `status='waiting_children'`, never receives a wake and
> stays wedged. Three sub-windows all wedge: W-A
> (terminal-write → wake-rows-commit); W-B (commit →
> `enqueued_at` stamp); W-C (stamp → wake-task claim). All
> three converge to the same post-wipe shape and are
> covered by RDRS lane 2 (no-row backstop) once the
> anchor-required filter is removed.
>
> **Re-anchor notice.** All anchors in this phase plan are
> verified on the worktree at `18827dbd`. The lane-2 query
> lives at `report_injection/repository.py:1039-1253`. The
> per-row pass at `report_delivery_recovery.py:865-1016`
> drives the existing deferred-marker chain
> `ensure_deferred → transition_deferred_to_pending →
> _reconcile_deferred_report → _create_subshape_a_artifacts →
> [TRANSITIVE] _process_child_completion_and_notify_parent`
> (`manager.py:8487` SYNC seam → `:8389` async twin;
> the `:2773` child-status guard returns
> `idempotency_skip` on re-entry — no exception, no
> erase, no duplicate; the parent wakes via
> `claim_pending_task` ranked FIRST at
> `task/repository.py:2648-2650` with the WAITING_CHILDREN
> exception at `:2789-2830`). The transitive re-entry hop
> is **SANCTIONED** through the RDRS chain — it is NOT
> a direct call into the public entry; the chain
> materializes artifacts at `manager.py:9178-9331` BEFORE
> re-entry, and the `:2773` guard short-circuits the
> transitive hop. Implementation occurs in this worktree;
> the main workdir is read-only.
>
> **Repo gotchas (binding).** All NEW Python files carry
> `from __future__ import annotations` at the top of the
> module. Multi-edit batches need grep / read-back
> verification (known silent-failure mode). SQLModel
> `select` import convention must be consistent within
> each touched file (sharpened per W-5: the lane-2 query
> uses `sqlmodel.select`; the implementing developer
> declares the convention explicitly in the commit
> message and the file header). New terminal status tokens
> must NEVER be added — the lane-2 extension does NOT
> introduce any.

---

## Objective

Extend RDRS lane 2 (`daemon/repositories/report_injection/repository.py:1039-1253`)
to admit anchor-less completed children of non-terminal parents
(the wedge children), and have the per-row pass at
`report_delivery_recovery.py:865-1016` drive the existing
deferred-marker chain (`ensure_deferred → transition →
_reconcile_deferred_report → _create_subshape_a_artifacts`).
After the fix, the F-2 straddle scenario heals:

* RDRS lane 2 (boot `recover_on_startup` at
  `pool_orchestrator.py:407-412` + periodic 300s) admits
  anchor-less completed children of non-terminal parents.
* The per-row pass derives `child_message_id` from the
  surviving child checkpoint via
  `completion_content.get_last_assistant_message` +
  `serialize_message` chain (W-1 LOCKED to fallback (ii)
  per `decisions.md §14a`).
* The PREFIX ledger check (queue-side
  `source LIKE 'internal_report:{child}:%' AND status IN
  ('ready','processing','completed')` + parent-history-side
  `serialized.get("source","").startswith(f"internal_report:{child}")`)
  prevents double-injection.
* The existing chain materializes a `MessageQueue` row
  (READY, `COMPLETION_REPORT` type) + a `PROCESS_REPORT`
  Task (PENDING, NO `work_id`) — the same artifacts the
  natural completion path produces, minus the report_injection
  row (which lane 2 mints first).
* The `PROCESS_REPORT` Task is claimable by
  `claim_pending_task` at `task/repository.py:2148` (ranked
  FIRST at `:2198-2203`; "PROCESS_REPORT claim IS the parent
  wake" at `:2612-2620`); the parent wake fires.

The auto-continue feature's existing tests + packs
(per `decisions.md §8`) stay green. The **five** existing
RDRS test suites
(`tests/unit/test_report_delivery_recovery_service.py`,
`tests/postgres/test_report_delivery_recovery_pg.py`,
`tests/integration/test_report_delivery_double_delivery_pg.py`,
`tests/unit/test_report_delivery_self_heal_zero_row.py`,
`tests/job_queue/test_report_delivery_bug_family_pins.py`)
are EXTENDED (per C-2) — no new `test_wc_wedge_sweep.py`
file.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 2.1 | Read `decisions.md §12, §12a, §12b, §12c, §12d, §14a` end-to-end before any edit. Internalize: (i) the W-4 pivot ELIMINATES `wc_wedge_sweep.py`; (ii) the W-2 do-not-call guardrails (no direct call into `ChildReportsService._process_child_completion_and_notify_parent`; no wrap-in-`bus._get_parent_lock`); (iii) the operative cross-path dedup is the PREFIX ledger (NOT exact-id equality); (iv) RDRS materializes THROUGH the deferred-marker path. | none | Reviewer can confirm the implementing developer has internalized the four design inputs. |
| 2.2 | **Lane-2 query extension** at `daemon/repositories/report_injection/repository.py:1039-1253` (`find_completed_children_without_delivery`). The current query has an anchor-required filter at `:1241` (`.where(anchor_subq.is_not(None))` where `anchor_subq` is the child's latest COMPLETED `message_queue` row at `:1197-1208`); the wipe deletes this anchor. **Extend the query to admit anchor-less completed children of non-terminal parents:** (a) add a LEFT JOIN to surface anchor-presence as a flag (e.g., `has_anchor = anchor_subq IS NOT NULL`); (b) drop the `.where(anchor_subq.is_not(None))` filter; (c) keep the existing exclusions (already-delivered, already-injected, fired-watcher — these are wiped-aware per the wipe analysis in Explorer B Q2); (d) flag anchor-less children in the result row so the per-row pass knows to derive `child_message_id` from the checkpoint. The query is ~10-30 lines of change. The existing instance-anchored join (`FROM instances c JOIN instances p ON p.instance_id=c.parent_id` at `:1216-1220`) stays; the `c.status=='completed'` filter at `:1222` stays; the parent-terminal exclusion at `:1229-1233` stays (waiting_children is NOT in `_PARENT_TERMINAL_STATUSES` at `repository.py:262-269` = `{completed, error, terminated, failed}`). **SQLModel `select` import convention:** the file uses `sqlmodel.select` (verify with `grep -n "^from sqlmodel" daemon/repositories/report_injection/repository.py` and document the choice in the file header comment per W-5). | 2.1 | Lane-2 query admits anchor-less completed children; the change is a single query at `:1039-1253`; existing exclusions preserved; SQLModel select convention declared in header. |
| 2.3 | **New repository method** `find_wake_already_delivered_evidence(parent_id, child_id)` at `daemon/repositories/message_queue/repository.py` (sibling of the existing query methods). Returns `bool` — `True` if any `MessageQueue` row exists with `instance_id = :parent_id AND source LIKE 'internal_report:{child_id}:%' AND status IN ('ready','processing','completed')`. This is the queue-side PREFIX ledger check. **STATE EXPLICITLY** in the docstring: this is the operative cross-path dedup; the exact-id equality at `child_reports.py:3498-3507` is the natural-path-only check. Precedent commits: `dfac6ff0` and `000f39db` ("prefix-match internal_report:{child}:% for delivery evidence"). | 2.1 | Method exists; uses `sqlmodel.select` (or `sqlalchemy.text` — declare in commit message per W-5); docstring cites the precedent. |
| 2.4 | **New helper** `parent_history_has_internal_report(checkpointer, parent_id, child_id)` at a NEW module `daemon/services/report_delivery_ledger.py` (per N-10 — the helper does NOT belong in `completion_content.py`; it is a delivery-recovery concern, not a content-extraction concern; the new module home also avoids name-collision with `MessageMetadataRepository.get_for_thread` at `daemon/repositories/message_metadata/repository.py:123`). Reads the parent's `get_instance_messages` serialized dicts (the manager is passed in to skip the synthetic system-prompt injection — the sweep does not need it, per the W-5 `manager=` kwarg polarity check at `daemon/persistence.py:312-330`: the `manager=None` default skips the injection) and returns `True` if any message has `dict.get("source","").startswith(f"internal_report:{child_id}")`. The `source` key is surfaced at `daemon/utils.py:264-266` (the same `additional_kwargs.source` surface verified in the prior cycle). **STATE EXPLICITLY** in the docstring: the pre-migration parent's absence of `source` field degrades gracefully to "not yet reported" (no false-positive skip). Signature: `def parent_history_has_internal_report(checkpointer, parent_id: str, child_id: str, manager: Any | None = None) -> bool`. Import convention per W-5: the new module uses `from __future__ import annotations` at the top and `sqlmodel.select` / `sqlalchemy.text` declared in the file header (one consistent convention per the W-5 sharpening). | 2.1 | Helper exists in `daemon/services/report_delivery_ledger.py`; uses `daemon/utils.py:264-266` source surfacing; degrades gracefully on pre-migration parents; the prior cycle's `get_for_thread_message` name (which collided with `MessageMetadataRepository.get_for_thread`) is RETIRED. |
| 2.5 | **Per-row pass additions** at `daemon/services/report_delivery_recovery.py:865-1016`. The current per-row pass drives `ensure_deferred` (`:959`) → `transition_deferred_to_pending` (`:1000-1004`) → `manager._handle_recover_deferred_report` (`:1016-1021`). **Add the following guard BEFORE `ensure_deferred`:** (a) call the new `find_wake_already_delivered_evidence` from 2.3 — if `True`, increment `skipped_already_reported` and `continue`; (b) call the new `parent_history_has_internal_report` from 2.4 (in the new `daemon/services/report_delivery_ledger.py`) — if `True`, increment `skipped_already_reported` and `continue`; (c) only if BOTH checks return `False`, proceed to the existing chain. **STATE EXPLICITLY** in the docstring + commit message: the PREFIX ledger is the cross-path dedup; the obligation-triple unique index (migration `20260819_000001:114-120`) handles within-path idempotency. | 2.3, 2.4 | Per-row pass runs the two PREFIX-ledger checks before `ensure_deferred`; both branches increment the result counter; the existing chain is preserved. |
| 2.6 | **`child_message_id` derivation** at the per-row pass (insertion point: between the PREFIX-ledger checks at 2.5 and the `ensure_deferred` call at `:959`). For anchor-less children (flag set in 2.2), the per-row pass derives `child_message_id` from the surviving child checkpoint via the `completion_content.get_last_assistant_message(checkpointer, child_id)` chain (returns `(content, created_at)` at `completion_content.py:22-56`; the `message_id` is read from the underlying serialized dicts via `get_instance_messages` at `daemon/persistence.py:312/:521/:524`). If the content is empty or the message_id is missing, log `WARNING` and `continue` (the child stays in the wedge for this pass; the periodic 300s loop retries). **STATE EXPLICITLY** in the docstring: the derived id is `BaseMessage.id` (UUID4) — STABLE-BUT-DIFFERENT from the natural path's `MessageQueue.message_id` (per W-1 confirmation). The PREFIX ledger (2.3 + 2.4) is the operative cross-path dedup; the id mismatch is by design. | 2.2, 2.5 | `child_message_id` derivation works on the surviving child checkpoint; failure modes log and skip; the periodic 300s loop is the retry mechanism. |
| 2.7 | **Window-to-lane map (binding).** Add an inline comment at the top of `report_delivery_recovery.py:_run_all_lanes_sync` (`:441-498`) mapping the three F-2 sub-windows to the 5 RDRS lanes: (a) W-A (terminal-write→wake-rows-commit), W-B (commit→`enqueued_at` stamp), W-C (stamp→wake-task claim) — all converge to the SAME post-wipe shape (PENDING wake rows deleted regardless of stamp state) — **all three → LANE 2** (the no-row backstop, with the 2.2 anchor extension); (b) marker-minted pre-restart cases (where a `report_injection` row was written before the restart) → LANES 1/3/4 (per their existing logic); (c) orphan-deferred-of-terminal-parents → LANE 5. The map is a 1-screen comment + a small markdown table; no new code. | 2.1 | Inline comment + table present at `:441-498`; maps all 3 F-2 sub-windows to lane 2; no behavior change. |
| 2.8 | **RDRS timing verification.** Verify that RDRS's `recover_on_startup` at `pool_orchestrator.py:407-412` (boot, off-loop, post-wiring, post-wipe) runs BEFORE any user-facing recovery path; the periodic 300s loop is the canary. No new boot wiring is needed — lane 2 already runs on boot. Add a one-line log at the lane-2 entry point that says "RDRS lane 2: post-wipe recovery" so the boot log carries the marker. | 2.2 | RDRS boot timing verified; one-line log at lane-2 entry. |
| 2.9 | **W-2 guardrails (do-not-call section)** — DOCUMENT only. Add a "W-2 DO NOT CALL" comment block at the top of `report_delivery_recovery.py` (above the `_run_all_lanes_sync` def at `:441-498`) that lists the four prohibited seams: (a) **DIRECT** call into `_process_child_completion_and_notify_parent(self, instance_id, completed_message_id)` at `child_reports.py:2458` — takes CHILD id first; passing parent id silently no-ops on root branch (`deferred_waiting_children` → SSE-only at `:2828-2854, :4481-4495`) or mints a report to the wrong grandparent. **The prohibition is on DIRECT calls; the transitive re-entry into `_process_child_completion_and_notify_parent` POST-materialization, through the sanctioned RDRS chain, is NOT prohibited** — the artifacts (MessageQueue READY + PROCESS_REPORT PENDING) are minted in one txn at `manager.py:9178-9331` BEFORE re-entry, and the `:2773-2787` child-status guard returns `idempotency_skip` (no exception, no erase, no duplicate); the parent wakes via `claim_pending_task` (PROCESS_REPORT ranked FIRST at `task/repository.py:2648-2650`; WAITING_CHILDREN exception at `:2789-2830` "prevents child-report deadlock"); (b) wrap any public `ChildReportsService` entry in `bus._get_parent_lock` — `_get_parent_lock` at `dependency_bus.py:1627-1665` constructs a **plain non-reentrant** `asyncio.Lock()` (`:1664`); same-task re-acquire blocks forever with no timeout; the public entry self-acquires the same key at `child_reports.py:2563`; wrapping deadlocks deterministically; (c) call the async twin `_handle_recover_deferred_report_async` (`manager.py:8393`) from the sweep thread — it is loop-only; the sweep-side seam is the SYNC `_handle_recover_deferred_report` (`manager.py:8487`, "Sweep-side entry point" docstring at `:8497`); (d) call `_dispatch_post_commit_side_effects` (`child_reports.py:4435-4440`) standalone — its input is a `_ChildCompletionDbResult` produced only by the db-sync helper; standalone caller must synthesize the object + hand-pick an outcome. The RDRS chain is the ONLY sanctioned path. | 2.1 | Comment block present; lists all 4 prohibited seams with anchors; explicitly distinguishes DIRECT-call prohibition from SANCTIONED transitive re-entry; references `decisions.md §12d` as the source of truth. |
| 2.10 | (Verification) Run the keep-green pack: `bash test/packs/auto_continue_boot_pass_unit_test.sh`. All four unit-test files + this pack must be green. If anything is red, the Phase 2 change is the cause. | 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9 | Pack exits 0; `RESULT: PASS`. |
| 2.11 | **F-2 unit + integration tests** (EXTEND the existing RDRS suites, per C-2): (a) `tests/unit/test_report_delivery_recovery_service.py` — extend with the new ledger-check logic; tests for `find_wake_already_delivered_evidence` (S22), `parent_history_has_internal_report` (S22 ledger-side, in the new `tests/unit/services/test_report_delivery_ledger.py`), `child_message_id` derivation from checkpoint (the "stable-but-different" assertion per W-1), anchor-less child admission (S21), RUNNING exclusion (S23), idempotent run-twice (S25), kill-switch gate (S26), **live-delivery race (S28 / renamed S15 per C-2)**; (b) `tests/postgres/test_report_delivery_recovery_pg.py` + `tests/integration/test_report_delivery_double_delivery_pg.py` — extend with the **MANDATORY integration test on the REAL delivery seam** (per C-2): real service + real DB; seeded terminal child in straddle shape; assert a report row is actually minted AND the parent-wake path is exercised end-to-end. S24 (multi-child mixed) and S27 (no-regression) MUST run on the real seam. **No new test_wc_wedge_sweep.py file** (C-2 — harmonize with existing suites). | 2.10 | 7 F-2 unit tests + BOTH MANDATORY integration tests (S24 multi-child-mixed + S27 no-regression, NON-DROPPABLE per Issue-7) pass; existing RDRS suites continue to pass; no new test file. |
| 2.12 | (Verification) Run the full-dir pytest sweep at the project level (same invocation as Phase 1 task 1.16). Confirm zero new failures vs. baseline. | 2.11 | Full-dir sweep is green; no regressions. |

**Total Phase 2 tasks: 12** (including verification steps).
Tasks 2.10, 2.12 are verification gates; 2.11 is the
test-extension work. The implementation count is **9**
(**2.1-2.9 — all 9 are production-code tasks; 2.11 is
test extension, not new production code**). The
total task count is 12 (9 implementation + 3
verification/test-extension: 2.10, 2.11, 2.12).

---

## Coupling

* **Tight with Phase 1 (F-1 fix):** Phase 1's task 1.8
  introduces the FIRST instance-join in `clear_all` SQL
  (the arm-3 `EXISTS instances` co-condition per W-3).
  **NOTE:** Phase 2's lane-2 query uses a DIFFERENT join —
  the pre-existing `FROM instances c JOIN instances p` at
  `report_injection/repository.py:1216-1220` (not the
  `EXISTS instances` subquery from 1.8). The two joins are
  RELATED (both anchor on `instances` for the same
  instance-row-wipe-immunity property) but are NOT the
  same join — see N-4 anchor-conflation fix in
  `revision-summary.md`. A regression in 1.8 (the F-1
  arm-3 join) does NOT break Phase 2's anchor; Phase 2's
  anchor is pre-existing and unchanged. Phase 2's lane-2
  query extension (task 2.2) reuses the pre-existing
  `FROM instances c JOIN instances p` join as the
  wipe-immune anchor (decision §12a — B2).
  Phase 1's task 1.17 (marker-clearing at the terminalizer
  call site) shares `manager.py:11660-11721` with the F-2
  path: F-1 clears the `auto_continued_at` marker; F-2's
  terminalizer gate conjunct at `:11700-11702` accepts
  only CAS-stamped rows. A regression in 1.17 affects
  both F-1 (residual leak) and F-2 (terminalizer
  acceptance).
* **Tight with `decisions.md §12, §14a`:** the W-4 pivot
  decision + the W-1 final choice + the W-2 guardrails
  are all referenced from this phase plan. A drift
  between the phase plan and the decisions file is a
  documentation defect — fix the phase plan, not the
  decisions file (or update both with a clear note).
* **Loose with the RDRS layer (per C-1 line-walk):** Phase 2
  REUSES the existing RDRS chain; it does not modify the
  chain. The chain `ensure_deferred → transition →
  _reconcile_deferred_report → _create_subshape_a_artifacts`
  is the load-bearing machinery; the per-row pass (2.5)
  drives the chain. The triple index (migration
  `20260819_000001:114-120`) handles within-path
  idempotency; the PREFIX ledger (2.3 + 2.4) handles
  cross-path dedup.
* **N-8 — RDRS path is structurally independent of
  `auto_continued_at`:** the lane-2 extension does NOT
  consult the F-1 `auto_continued_at` marker. The
  terminalizer (`manager.py:11660-11721`) is NEVER the
  parent-wake path under lane 2 — the wake is the
  PROCESS_REPORT Task claim at `task/repository.py:2648-2650`
  (ranked FIRST; WAITING_CHILDREN exception at
  `:2789-2830`). The terminalizer's `auto_continued_at`
  CAS stamp acceptance (gate conjunct at `:11700-11702`)
  is for the auto-continue feature's own wake path, not
  for the F-2 lane-2 chain. The F-2 chain materializes a
  fresh `PROCESS_REPORT` task (PENDING, NO `work_id`) at
  `manager.py:9157-9340` regardless of the F-1
  `auto_continued_at` lifecycle.
* **Independent of:** the F-2 design (a) follow-up
  (`decisions.md §3a`) — that work is deferred and does
  not touch Phase 2's changes. Design (a) widens the
  clear_all predicate to preserve PENDING wake tasks;
  under the W-4 pivot, the wedge is healed by the lane-2
  extension, not by preserving the wake rows.

---

## Risks (Phase 2-specific; see `plan-overview.md` for the consolidated register)

* **R-P2-1: lane-2 double-injects a child report if the
  PREFIX ledger check is wrong.** **Mitigation:** the
  PREFIX ledger is implemented twice (queue-side
  `source LIKE 'internal_report:{child}:%'` at 2.3 +
  parent-history-side `serialized.get("source","").startswith(...)`
  at 2.4); the obligation-triple unique index (migration
  `20260819_000001:114-120`) is the within-path
  idempotency backstop. The integration test (2.11) on
  the real delivery seam is the window-proof.
* **R-P2-2: the `child_message_id` derivation fails on
  metadata absence.** **Mitigation:** task 2.6 logs
  `WARNING` and `continue`s; the periodic 300s RDRS
  loop is the retry. The wedge for that one child
  persists for one RDS cycle (≤300s) — acceptable per
  the same risk profile as the natural path's retry.
* **R-P2-3: the W-2 guardrails are violated by a future
  contributor.** **Mitigation:** task 2.9's "W-2 DO NOT
  CALL" comment block at the top of
  `report_delivery_recovery.py` is a permanent
  documentation marker. The integration test (2.11) on
  the real seam is the regression gate (any deadlocking
  call would hang the test).
* **R-P2-4: SQLModel `select` import convention drift in
  the touched files.** **Mitigation:** task 2.2 declares
  the convention in the file header comment per W-5;
  task 2.11's test file uses the same convention; the
  implementing developer records the choice in the
  commit message.
* **R-P2-5: the lane-2 query change is hot-path (every
  daemon restart + every 300s loop).** **Mitigation:**
  the change is ~10-30 lines in ONE query
  (`repository.py:1039-1253`); the integration test
  (2.11) on the real seam is the regression gate. RDRS's
  own per-lane kill switches in `config.py:1302-1378`
  gate the active behavior — if production over-loads
  the lane, an operator can disable lane 2 with no
  code-ship.
* **R-P2-6: multi-edit on `report_injection/repository.py`
  + `report_delivery_recovery.py` + `completion_content.py`
  is a known silent-failure mode.** **Mitigation:**
  read-back verification after every multi-edit; the
  changes are isolated to the lane-2 entry point +
  two new repository methods + a per-row guard, not
  scattered in-place edits.

---

## Exit Criterion

This phase is DONE when:

* All 9 implementation tasks (2.1-2.9) are complete and
  each task's acceptance criterion is met.
* The keep-green pack (`auto_continue_boot_pass_unit_test.sh`)
  is green (task 2.10).
* The F-2 unit tests + the MANDATORY integration tests
  on the real delivery seam are all green (task 2.11).
  **F-2 test inventory (7 unit tests + 2 MANDATORY
  integration tests on the real PG seam = 9 total):**
  1. S21 — `test_lane2_admits_anchor_less_completed_child` (**unit**)
  2. S22 — `test_find_wake_already_delivered_evidence_returns_true_on_prefix_match` (**unit**) + `test_parent_history_has_internal_report_returns_true_on_marker` (**unit**; the §14a / N-10 helper renamed in `daemon/services/report_delivery_ledger.py`)
  3. S23 — `test_lane2_excludes_running_child` (**unit**, by `c.status=='completed'` filter)
  4. S24 — `test_lane2_processes_straddle_child_only_multi_child_mixed` (**INTEGRATION** on real PG seam per C-2)
  5. S25 — `test_lane2_run_twice_single_ensure_deferred` (**unit**, triple index idempotency)
  6. S26 — `test_lane2_kill_switch_gate` (**unit**, RDRS per-lane config)
  7. S27 — `test_lane2_no_regression_on_non_straddle` (**INTEGRATION** on real PG seam per C-2) — **NON-DROPPABLE** (see Issue-7 rationale below)
  8. S28 (renamed from S15 per C-2) — `test_lane2_skips_on_live_delivery_between_scan_and_ledger_recheck` (**unit**)
  9. The `child_message_id` derivation assertion (the W-1 stable-but-different verification, via the `serialize_message` chain) — counted as a **unit** assertion (covered by the §14a / N-10 derivation logic; not enumerated as a separate S).

  **Total: 7 unit tests + 2 MANDATORY integration tests = 9 tests.**

  **Issue-7 rationale (S27 NON-DROPPABLE):** the prior
  cycle's "8 unit + 1 integration" framing undercounted
  the integration test count — S24 AND S27 are BOTH
  real-PG integration tests per C-2, not unit. S27 is
  the no-duplicate-execution regression test, the
  load-bearing correctness invariant the RDRS chain
  provides. Dropping S27 would mean the gate claims
  green on unit tests alone, which C-2 explicitly
  rejects. S24 and S27 are both NON-DROPPABLE from the
  gate.

  Prior cycle's "12 F-2 unit tests" claim was based on
  a pre-pivot test list that included the now-ELIMINATED
  sweep module's unit matrix (S7-S14). The real F-2
  inventory under W-4 is **7 unit + 2 integration = 9
  tests total**.

  The keep-green list is the
  existing RDRS suites continue to pass; no new
  `test_wc_wedge_sweep.py` file is created (it was NEVER
  created — the W-4 pivot cancelled the sweep module before
  its file was written; nothing to remove).
* The full-dir pytest sweep is green with zero new failures
  vs. baseline (task 2.12).
* The reviewer can read `decisions.md §12, §14a` and see
  each design choice reflected in the code (2.2, 2.3,
  2.4, 2.5, 2.6, 2.7, 2.8, 2.9).
* The "W-2 DO NOT CALL" comment block at the top of
  `report_delivery_recovery.py` (task 2.9) is present
  and references `decisions.md §12d`.

The phase is independently revertable: see
`plan-overview.md` Risks section for the per-phase rollback
story. **The previously-planned `wc_wedge_sweep.py` module
was never created — no rollback needed for that.**

---

## File Inventory (Phase 2)

| Path | Change | Verified anchor |
|---|---|---|
| `daemon/repositories/report_injection/repository.py` | Task 2.2 — lane-2 query extension at `:1039-1253` (~10-30 lines): admit anchor-less completed children of non-terminal parents via a `has_anchor` result flag; the wipe-immune anchor VALUE for such children is derived per-row from the surviving child checkpoint (task 2.6). The instance join at `:1216-1220` is pre-existing and unchanged (F-1 arm 3's `EXISTS instances` co-condition is a separate, `clear_all`-side change). | yes |
| `daemon/repositories/message_queue/repository.py` | Task 2.3 — new `find_wake_already_delivered_evidence(parent_id, child_id)` method: queue-side PREFIX ledger check. | yes |
| `daemon/services/report_delivery_ledger.py` (NEW) | Task 2.4 — new module home for the `parent_history_has_internal_report(checkpointer, parent_id, child_id, manager=None)` helper: parent-history-side ledger scan via `get_instance_messages` + `serialized.get("source","").startswith(...)`. The module is a sibling of `report_delivery_recovery.py` (both are delivery-recovery concerns); the name `report_delivery_ledger` follows the existing naming convention. `from __future__ import annotations` at the top. | new file (per N-10) |
| `daemon/services/report_delivery_recovery.py` | Task 2.5 — per-row pass additions at `:865-1016` + the lane-2 call site at `:1016-1021`: PREFIX-ledger check BEFORE minting; checkpoint-derived `child_message_id` (W-1 fallback ii). Task 2.7 — inline comment at `:441-498` mapping the 3 F-2 sub-windows to the 5 RDRS lanes. Task 2.8 — one-line log at lane-2 entry. Task 2.9 — "W-2 DO NOT CALL" comment block at the top of the file. | yes |
| `daemon/manager.py` | (No change; the existing `_handle_recover_deferred_report` at `:8487` is the sweep-side seam.) | yes (REUSE ONLY) |
| `daemon/services/child_reports.py` | (No change; REUSE only for DIRECT calls. **N-9 — the transitive re-entry hop into `_process_child_completion_and_notify_parent` POST-materialization, through the sanctioned RDRS chain, is NOT a direct call and is NOT prohibited** (see W-2 guardrail #1 in `decisions.md §12d` + `phase2-plan.md` task 2.9): the artifacts (MessageQueue READY + PROCESS_REPORT PENDING) are minted in one txn at `manager.py:9178-9331` BEFORE re-entry, and the `:2773-2787` child-status guard returns `idempotency_skip` on re-entry. The DIRECT-call prohibition is preserved; the transitive re-entry is the 5th hop of the chain and is the only path by which the parent-wake claim reaches the WAITING_CHILDREN exception at `task/repository.py:2789-2830`.) | yes (REUSE ONLY) |
| `tests/unit/test_report_delivery_recovery_service.py` (EXTEND) | Task 2.11 — F-2 unit tests: `find_wake_already_delivered_evidence` (S22), `parent_history_has_internal_report` (S22 ledger-side), `child_message_id` derivation (W-1 stable-but-different), anchor-less child admission (S21), RUNNING exclusion (S23), idempotent run-twice (S25), kill-switch gate (S26), live-delivery race (S28 / renamed S15 per C-2). | existing file |
| `tests/unit/services/test_report_delivery_ledger.py` (NEW) | Task 2.11 — F-2 unit tests for the new `parent_history_has_internal_report` helper in isolation (mocks `checkpointer` + parent history; asserts the prefix-match + pre-migration graceful-degrade behavior). | new file (per N-10) |
| `tests/postgres/test_report_delivery_recovery_pg.py` (EXTEND) | Task 2.11 — F-2 PG integration tests. | existing file |
| `tests/integration/test_report_delivery_double_delivery_pg.py` (EXTEND) | Task 2.11 — C-2 MANDATORY integration test on the REAL delivery seam: real service + real DB; seeded terminal child in straddle shape; assert a report row is actually minted AND the parent-wake path is exercised end-to-end. S24 (multi-child mixed) and S27 (no-regression) MUST run on the real seam. | existing file |
| `tests/unit/test_report_delivery_self_heal_zero_row.py` (EXTEND) | Task 2.11 — F-2 self-heal zero-row tests. | existing file |
| `tests/job_queue/test_report_delivery_bug_family_pins.py` (EXTEND) | Task 2.11 — F-2 bug family pins. | existing file |

**No changes to:** `daemon/services/child_reports.py`
(REUSE only — no in-place edits, no producer-contract
changes); `daemon/services/auto_continue_boot_pass.py`
(no change); `daemon/services/dependency_bus.py` (Phase 1
already touched this file; no Phase 2 changes); the four
auto-continue unit-test files (regression gates only);
`daemon/services/wc_wedge_sweep.py` (ELIMINATED — never
created).
