# Plan Overview: Durability F-1 + F-2

Date: 2026-10-04
Worktree: `/home/nea/ensemble-src-wt-durability` @ `18827dbd` (branch `feature/durability-f1-f2`)
Author: planner via plan-creation worker
Status: Draft

> **Re-anchor notice (binding).** All line numbers in this plan
> family are verified on the worktree at `18827dbd` (cut from
> `latest` after the auto-continue merge `86ea8d69`). Anchors
> pre-dating the durability worktree — including the
> 86ea8d69-era explorer table in
> `.agents/tester/RESULTS/2026-10-04-auto-continue-merge-gate.md`
> and the `wc-wake-report-integrity` plan folder on `main` — are
> SUPERSEDED where the worktree-anchored
> `research-findings.md §6` table records drift. Implementation
> occurs on `feature/durability-f1-f2`; the boot-pass call
> sites in particular differ from 86ea8d69's original shape
> (the call now passes `boot_epoch`). The main workdir
> `/home/nea/ensemble-src` is occupied by a sibling commission
> and is read-only to this one.
>
> **Repo gotchas (binding).** `.venv` is CPython 3.13 while
> code targets 3.14 — all new Python files in this plan MUST
> carry `from __future__ import annotations` at the top of the
> module (or use no module-level string forward-refs).
> Multi-edit batches need grep / read-back verification
> (known silent-failure mode). SQLModel `select` import
> convention must be consistent within each touched file
> (`child_reports.py` mixes both — live trap). New terminal
> status tokens must NEVER be added without the observer
> accepted-set update (wedge hazard).

---

## Objective

**F-1:** Close the DependencyBus None-error → `_parent_errored=True`
wedge that strands the auto-continue boot pass at boot N+1 (candidates
== 0) when a child is mid-`discard_on_startup` wipe. After the fix,
the double-restart probe must observe `candidates == 1` for the
straddled child, `already_resuming == 0` across all boots, and zero
unintended `_parent_errored=True` flips from the None path.

**F-2:** Heal the WC-wake straddle wedge — child completes just
before restart, backlog-clear deletes wake rows before parent-wake
delivers, parent wedged in `waiting_children` forever. After the
fix, the straddle scenario must heal at next boot: the parent
leaves `waiting_children` and receives a synthesized child report
with zero manual pings, exactly ONE `internal_report:<child>`
injection in parent history, and the child is not re-executed.

**Outcome-focused definition of "done":** the F-1 / F-2 wedges are
both closed; the keep-green auto-continue test + pack inventory
stays green; the boot-sequence ownership contract is documented;
the new kill-switch is honoured; the F-2 demo E2E lands durable
evidence in the repo (not `/tmp`) — UNCONDITIONAL per C-2
(see `plan-overview.md:136-138`, S19 :375, and
`phase3-plan.md:21-22/:50-53/:277-286` for the matching
UNCONDITIONAL framing). The F-1 demo E2E remains OPTIONAL per
tester judgment (see `decisions.md §9` for the F-1 skip
condition, now satisfiable via the Issue-2 real two-boot test).

**Load-bearing wipe-site premise (binding):** the F-2
wedge is healable by RDRS lane 2 precisely because the
`discard_on_startup` wipe at `manager.py:771-822` clears
`MessageQueue` + `Task` ONLY, never `report_injections` —
that is WHY lane 2's evidence exclusions (`has_delivery_row`,
`has_injection_row`, `has_fired_watcher`) pass post-wipe
and the marker-minted lanes 1/3/4/5 survive the wipe. The
F-2 architecture's viability depends on this scope of
the wipe.

---

## Scope

### In Scope

* **F-1 bus-side fix.** Truthy-error condition applied to the two
  DependencyBus gates `dependency_bus.py:671` and
  `dependency_bus.py:859`. Defensive `WARNING` log on the None
  path (so any future regression is loud in operator logs).
  No producer-contract change.
* **F-1 wipe-side extension.** `capture_boot_epoch(engine)`
  invoked inside the `InstanceManager` constructor BEFORE the
  `discard_on_startup` wipe runs; preserve predicate on BOTH
  `TaskRepository.clear_all` and `MessageQueueRepository.clear_all`
  restructured to a **2-arm disjunction** (arm 1 `status IN
  ('running','paused')` + arm 3 `auto_continued_at IS NOT
  NULL` with the `EXISTS instances` instance-non-terminal
  co-condition per §13b). Arm 2 is **DROPPED per W-3**; the
  arm-3 leak is bounded by the marker-clearing at the
  terminalizer call site (decision §13b).
* **F-1 ownership documentation.** Ownership-contract block
  added to `daemon/services/instance_lifecycle.py` (alongside
  the Pause-First Then Quiesce convention); short inline
  comments at `dependency_bus.py:671` and
  `repository.py:4380` referencing the block.
* **F-1 kill-switch (W-3 / §2c).** Env-direct
  `ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE` (default ON; `=0`
  disables ONLY arm 3 of the 2-arm disjunction — arm 1
  `status IN ('running','paused')` stays active; off = exact
  pre-fix wipe predicate). Renamed from the prior cycle's
  `ENSEMBLE_BOOT_EPOCH_PRESERVE_HEARTBEAT` (which governed
  the now-DROPPED arm 2 per W-3). Mirrors the auto-continue
  kill-switch convention at
  `auto_continue_boot_pass.py:145-156`. Recorded in
  `decisions.md §2c / §13c`.
* **F-2 RDRS lane-2 extension (W-4 PIVOT — REVISION CYCLE 2).**
  The F-2 architecture is the RDRS lane-2 (no-row backstop)
  extension at
  `daemon/repositories/report_injection/repository.py:1039-1253`.
  The previously-planned `daemon/services/wc_wedge_sweep.py`
  module is ELIMINATED. The lane-2 query is extended to admit
  anchor-less completed children (the wedge children) by
  deriving `child_message_id` from the surviving child checkpoint
  via the `completion_content / serialize_message` chain
  (decision §14a — W-1 LOCKED to fallback (ii)). The per-row
  pass drives the existing RDRS chain
  `ensure_deferred → transition_deferred_to_pending →
  _reconcile_deferred_report → _create_subshape_a_artifacts`
  (decision §12a, §12d — W-2 guardrails). RDRS's own per-lane
  kill switches (config.py:1302-1378) gate the active behavior.
* **F-2 test matrix + regression gates.** Seven F-1 unit tests +
  pack (incl. F-1 kill-switch test S7 per W-3 rescoping);
  F-2 unit tests EXTEND the existing RDRS test suites
  (`tests/unit/test_report_delivery_recovery_service.py`,
  `tests/postgres/test_report_delivery_recovery_pg.py`,
  `tests/integration/test_report_delivery_double_delivery_pg.py`,
  `tests/unit/test_report_delivery_self_heal_zero_row.py`,
  `tests/job_queue/test_report_delivery_bug_family_pins.py`)
  rather than create a new `test_wc_wedge_sweep.py` file
  (per C-2 — harmonize file naming with existing suites,
  the `wc_wedge_sweep` module is ELIMINATED). The
  integration test on the REAL delivery seam is MANDATORY
  (per C-2: ≥1 integration test on the real seam;
  mock-only F-2 tests no longer qualify as window-proof).
  The four existing auto-continue unit-test files + two
  packs stay green.
* **F-2 demo E2E.** **UNCONDITIONAL** (per C-2 leader call) — the
  F-2 demo runs on every F-2 landing. Evidence lands in
  `.agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/`.
  The F-1 demo E2E remains OPTIONAL per tester judgment (see
  `decisions.md §9`).

### Out of Scope

* **Live deploy / merge to `latest`.** Per the commission brief:
  "no live deploy (merge to latest handled separately —
  feature-branch work only)". The branch is
  `feature/durability-f1-f2`; merging to `latest` is handled by
  the ARI worker, not by this commission.
* **No new messaging paths.** Reuse existing primitives only.
  F-2 is the RDRS lane-2 extension (per the W-4 pivot) — an
  in-place edit of the existing
  `report_injection/repository.py:1039-1253` query plus
  per-row-pass additions in `report_delivery_recovery.py`.
  The sanctioned chain RDRS drives
  (`ensure_deferred` → `transition_deferred_to_pending` →
  `manager._handle_recover_deferred_report` (the
  `SYNC` seam at `manager.py:8487`, "Sweep-side entry point"
  docstring) → `_reconcile_deferred_report`
  (`manager.py:8685`) → `_create_subshape_a_artifacts`
  (`manager.py:9157-9340`)) is the only path. Content is
  derived from the surviving child checkpoint via
  `completion_content.get_last_assistant_message`
  (`completion_content.py:22-56`) and the per-row
  parent-history ledger check uses the new
  `parent_history_has_internal_report` helper in
  `daemon/services/report_delivery_ledger.py`. The
  `wc_wedge_sweep.py` module (prior cycle's design) is
  ELIMINATED; no such service was ever created.
* **F-2 design (a).** Explicitly DEFERRED
  (`decisions.md §3a`). The predicate-extension approach that
  preserves PENDING wake rows across restarts is recorded as a
  follow-up commission; this release closes F-2 with design (b)
  only.
* **Producer-contract tightening.** Explicit no-action
  (`decisions.md §6`). The bus-side gate is the narrower fix;
  the producer side keeps its None tolerance for the legitimate
  terminated branch at `instance_lifecycle.py:256`.
* **A boot-hook registry.** The current straight-line boot is
  a load-bearing architectural choice; this commission adds two
  inline-boundary comments and an `instance_lifecycle.py` block
  instead of a registry.
* **A typed-config field for the new kill-switch.** Per the
  auto-continue precedent, the switch is env-direct
  (`decisions.md §2c`).
* **`PACKS.md` introduction.** Deferred; packs discovered by
  directory listing in this worktree.
* **The previously-planned `wc_wedge_sweep.py` module and its
  `ENSEMBLE_WC_WEDGE_SWEEP_ON_BOOT` kill-switch.** **ELIMINATED
  per W-4 pivot** (REVISION CYCLE 2, see `decisions.md §12`).
  The F-2 architecture is the RDRS lane-2 extension.

### Adjacent features deliberately excluded

* **A new wake-row re-stamping service** (re-stamping the
  `enqueued_at` of a preserved wake row to mark it as "should
  not be deleted"). This would be a third ownership step in
  the boot sequence; the RDRS lane-2 extension covers the same
  user-visible behaviour without the third step.
* **Replacing the PREFIX ledger check with a content-hash
  dedup.** Content-keyed idempotency does NOT exist in
  `child_reports.py` (grep-verified); introducing it would be a
  wider change than this commission's evidence justifies. The
  PREFIX ledger (queue-side `source LIKE 'internal_report:{child}:%'`
  + parent-history-side `serialized.get("source","").startswith(...)`)
  is the operative cross-path dedup under the W-4 pivot.
* **A dedicated `wc_wedge` repair service for the live case.**
  The wedge is a one-shot straddle-residue; RDRS's periodic
  300s loop covers it. The boot-time seam is RDRS's
  `recover_on_startup` at `pool_orchestrator.py:407-412`
  (immediately post-wiring, off-loop, post-wipe).
* **The `wc_wedge_sweep.py` module (proposed prior cycle).**
  ELIMINATED per W-4 pivot. The RDRS lane-2 extension reuses
  the existing deferred-marker chain and inherits the existing
  write-guard + write-once triple gate; the new-module path
  would have to independently re-derive these protections
  (decision §12c — rejected items, frozen).

---

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | F-1 fix | Close the DependencyBus None-error wedge + extend the wipe-side preserve predicate (arm-3-only with instance-non-terminal co-condition) + marker-clearing at terminalizer + document boot-sequence ownership | **17** (1.1-1.17, see `phase1-plan.md`) | tight with Phase 2 (F-1 marker-clearing at terminalizer shares `manager.py:11660-11721` with the F-2 path) | **LANDED — IN VERIFICATION** (code pre-landed in the UNCOMMITTED working tree atop `18827dbd`; verification per Issue-1 + Issue-2 evidence bar — see S1-S7 in the S-matrix) |
| 2 | F-2 fix | Extend RDRS lane 2 to admit anchor-less completed children of non-terminal parents; per-row pass drives the existing deferred-marker chain | 9 implementation tasks (2.1-2.9) + 3 verification tasks (2.10-2.12) = **12 tasks** (see `phase2-plan.md`) | tight with Phase 1 (F-1 arm-3 lifecycle bound §13b must be in place before the lane-2 query evaluates terminal-instance co-conditions on the same instance rows) | pending (in implementation) |
| 3 | Tests + demo E2E | Test packs per conventions; regression gates; integration test on real delivery seam (C-2); F-2 demo E2E UNCONDITIONAL; F-1 demo optional | 8 (see `phase3-plan.md`) | loose with Phase 1 + Phase 2 (each phase contributes its own test files / pack) | pending (in implementation; test rework dispatched separately) |

**Phase ordering rationale.** Phase 1 first because the F-1 fix
is on the cold path (bus startup) and is simpler / lower-risk
than the F-2 lane-2 extension; an F-1 review will surface any
drift in the arm-3 lifecycle bound that the F-2 lane-2 query
depends on (the lane-2 query joins `instances` for the
non-terminal co-condition — **a RELATED but DISTINCT join
from F-1 arm 3**; both anchor on `instances` for the same
instance-row-wipe-immunity property but are not the same
join; see N-4 anchor-conflation fix in `revision-summary.md`).
Phase 2 second because the F-2 lane-2 query reads
the same `instances` rows that the F-1 arm-3 predicate joins
on. Phase 3 last because the test matrix is per-phase
(integration test on real delivery seam per C-2; Phase 3
references Phase 1 + Phase 2 test files; no test runs in
Phase 1 or Phase 2 against Phase 3 artifacts).

### Coupling Map

| | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| Phase 1 | — | tight (shared boot order + shared ownership block) | independent |
| Phase 2 | tight | — | independent |
| Phase 3 | independent | independent | — |
| Parallel claim-gate sibling-deadlock fix (separate commission, project note) | independent: different site (`task/repository.py:2587-2609` cross-system guard) — F-1 touches `:4380-4394` (clear_all preserve DELETE) | independent | independent |

**Observation (architect A-4):** F-1 arm 3 (`auto_continued_at IS NOT
NULL`) increases the number of preserved rows visible to the
claim-gate fix; the two changes compose (no semantic conflict, no
merge conflict), but both touch `task/repository.py` and reviewers
must read the diff as a set.

**Tight coupling (Phase 1 ↔ Phase 2):** Phase 1 inserts the
`capture_boot_epoch(self.engine)` call into the
`InstanceManager.__init__` constructor (task 1.7 — RETAINED
per W-3 / §13a for the auto-continue consumer, NOT for F-1
arm 2 which is dropped) and introduces the FIRST
`EXISTS instances` instance-join in `clear_all` SQL
(arm-3 co-condition at `task/repository.py:4380-4394`).
Phase 2 is an **IN-PLACE EDIT** at
`report_injection/repository.py:1039-1253` (the lane-2 query
extension) with **NO new boot wiring** — RDRS's
`recover_on_startup` at `pool_orchestrator.py:407-412`
already runs immediately post-wiring, off-loop, post-wipe.
The shared `EXISTS instances` join in `clear_all`
(Phase 1) and the pre-existing `FROM instances c JOIN
instances p` at `report_injection/repository.py:1216-1220`
(Phase 2) are DIFFERENT joins serving DIFFERENT predicates
— they are NOT the same join (see N-4 anchor-conflation fix
in `revision-summary.md`). The ownership block in
`instance_lifecycle.py` is shared (Phase 1 adds it, Phase 2
cites step 11 from it — step 11 is now RDRS lane-2 boot
pass, NOT the eliminated sweep). The
`from __future__ import annotations` discipline is shared (per
the binding repo gotcha). Multi-edit batches across both files
require the read-back verification discipline (per the binding
repo gotcha).

**Loose coupling (Phase 1 ↔ Phase 2 within their own files):**
Phase 1's bus-gate changes and Phase 2's sweep service do not
share a file; Phase 1's predicate change and Phase 2's sweep
service are independent. A regression in one does not break
the other.

**Independent (Phase 3 ↔ others):** Phase 3 only references
Phase 1 and Phase 2 test files and pack conventions; it does
not touch production code.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| R1 | F-1 producer contract has a legitimate None path at `instance_lifecycle.py:256` (terminated branch — the `(terminated, n/a)` Outcome lane, NOT an error lane per W-5-style re-anchor) — a careless producer-tightening would break it. | High | Low (mitigated by §6 decision) | Bus-side truthy-error gate is the narrower fix; producer side left unchanged; defensive `WARNING` log makes any future regression visible. |
| R2 | Epoch capture inside the `InstanceManager` constructor may race with `manager.initialize()` for engine availability. Arm 2 is DROPPED per W-3, so the constructor capture is RETAINED only for downstream consumers (auto-continue pass at `api.py:1553` + `stale_task_recovery.py:766/:407` + `repository.py:3363`). | Medium | Low | `capture_boot_epoch` is best-effort / idempotent / first-wins; the `None` case falls back to the auto-continue pass's degraded behavior, not to any F-1 predicate arm. |
| R3 | Wipe-side predicate extension (arm 3 with instance-non-terminal co-condition) is hot-path (every daemon restart exercises it); a wrong predicate could over-preserve queue entries. | High | Medium | Seven F-1 unit tests pin the predicate; pack runs on every commit; `decisions.md §2` records the `None` boot_epoch fallback (still applies to the auto-continue consumer). |
| R4 | F-2 lane-2 double-injects a child report if the PREFIX ledger check is wrong. The check is the operative cross-path dedup under the W-4 pivot (queue-side `source LIKE 'internal_report:{child}:%' AND status IN ('ready','processing','completed')` + parent-history-side `serialized.get("source","").startswith(f"internal_report:{child}")`). | High | Low | `additional_kwargs.source` IS surfaced at `daemon/utils.py:264-266` (verified prior cycle); `_is_child_report_message` precedent at `attestation_resolver_activation.py:543-564`; the RDRS reconcile chain has its own source-diff dedup via the obligation-triple unique index (migration `20260819_000001:114-120`); the per-row pass checks the prefix BEFORE minting (decision §12a). |
| R5 | F-2 lane-2 extension anchors on a checkpoint-derived `child_message_id` (W-1 fallback (ii)) that is stable-but-different from the natural path's `MessageQueue.message_id` (different UUID4 spaces — see §14a). If a cross-path collision occurs, a report could be double-delivered. | Medium | Low | The PREFIX ledger check is the operative cross-path dedup, NOT exact-id equality. The ca14e233/c3ac30f7 anchor-blindness incident is documented at `repository.py:1053-1099`; the new anchor fallback is `EXISTS instances` (the only wipe-immune anchor). |
| R6 | Multi-edit batches on `report_injection/repository.py` + `report_delivery_recovery.py` + `manager.py` are a known silent-failure mode (binding gotcha). | Medium | Medium | Read-back verification after every multi-edit; the lane-2 query change is ~10-30 lines in ONE query (`repository.py:1039-1253`), not in-place edits scattered across the codebase. |
| R7 | The new kill-switch is mis-set by an operator who expected it to govern arm 2 (the prior cycle's intent). The rename `ENSEMBLE_BOOT_EPOCH_PRESERVE_HEARTBEAT` → `ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE` is announced in the post-merge-gate file; a one-cycle break for any operator who set the old name. | Low | Low | Variable name `ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE` is semantically accurate (governs arm 3 / `auto_continued_at`); the kill-switch OFF path is the exact pre-fix predicate (arm 1 only). The new name is in the post-merge-gate file. |
| R8 | Demo E2E runs on stale data and produces false-positive passes. | Medium | Low | The dev boot.env in the R18 recipe uses `QUEUE_DISCARD_ON_STARTUP=true` which directly exercises the wipe lane; the data-dir copy is verbatim from the prior tag; the kill-switch control legs are the canary. **F-2 demo E2E is UNCONDITIONAL for first landing per C-2 (leader call).** |
| R9 | The lane-2 extension slip past the L1 dispatcher timeout during the demo E2E. | Low | Low | RDRS's `recover_on_startup` fires once immediately post-boot (off-loop, `pool_orchestrator.py:407-412`); the periodic 300s loop is the canary. F-2 demo is UNCONDITIONAL per C-2. |
| R10 | The F-1 reconciliation (only `:671` + `:859` need the fix) is wrong; a third site exists. | High | Low | Explorer A Q6 surprise scan is HIGH confidence; the planner can re-run the scan at the start of Phase 1 if desired. The 🟢 Q6 re-scan mandate is added to task 1.1. |
| R11 | The design (a) deferred recommendation turns out to be load-bearing and a regression surfaces in production. The W-4 pivot does NOT change this deferral. | Medium | Low | `decisions.md §3a` records the deferred recommendation with a follow-up verification task; RDRS lane 2 covers the same wedge so the user-visible behaviour is healed. |
| R12 | The arm-3 lifecycle bound (clearing at terminalizer + instance-non-terminal co-condition) is incomplete: STR-terminalized rows or race-stamped rows could still leak. | Medium | Low | The composition of (i) co-condition + (ii) clearing closes the primary leak path. The residual (STR backstop at `stale_task_recovery.py:262/329/468/514/583` + race stamps) is bounded by the periodic STR pass and the `complete_task` symmetric single-writer discipline. |
| R13 | Calling the wrong seam (per W-2 guardrails): direct call to `_process_child_completion_and_notify_parent` with parent_id, or wrap-in-parent-lock deadlock. | High | Low | `decisions.md §12d` enumerates the do-not-call seams. The RDRS chain is the ONLY sanctioned path. The phase2-plan.md W-2 guardrails section repeats the do-not-call list. |

**Rollback story per phase.** Each phase is independently
revertable. **Note:** the `capture_boot_epoch` ctor call
in Phase 1 is **LOAD-BEARING and must NOT be reverted**
(see `decisions.md §13a:936-943` — four retained consumers:
the auto-continue boot pass at `api.py:1553`, the
`queue_freshness` readiness probe at `api.py:2361`, the
StaleTaskRecovery pass at `stale_task_recovery.py:766` and
`:407`, and the task-repository stamp filter at
`repository.py:3363`; reverting the ctor call breaks the
auto-continue consumer's epoch availability during the
constructor's wipe).

* **Phase 1 rollback:** revert the bus-gate conditions to
  `if outcome.status == "error":` (no truthy check); revert
  the predicate extension to its single-arm form (drop the
  arm-3 `EXISTS instances` join); remove the marker-clearing
  at the terminalizer call site; revert the kill-switch
  rename (or accept the new name as a one-cycle break).
  **DO NOT** revert the `capture_boot_epoch` ctor call (see
  `decisions.md §13a`; the four retained consumers above
  depend on it). Feature goes back to the F-1 wedge state
  for the arm-2-era predicate only; no data loss.
* **Phase 2 rollback:** revert the lane-2 query change at
  `report_injection/repository.py:1039-1253` to its prior
  filter (anchor-required); revert any per-row pass
  additions in `report_delivery_recovery.py:865-1016`.
  Feature goes back to the F-2 wedge state; no data loss.
  **No `wc_wedge_sweep` file or call site exists** (the
  module was ELIMINATED by the W-4 pivot per `decisions.md
  §12a`; nothing to remove on the F-2 side beyond the
  in-place edit revert).
* **Phase 3 rollback:** delete the new test files + pack
  (or revert the developer's test-rework commits — the
  test rework is dispatched separately per the approver's
  notes). No production code touched.

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| S1 | F-1: double-restart probe passes (REAL file-backed-SQLite two-boot test, per Issue-2) | **Issue-2 (binding):** a real file-backed-SQLite two-boot test using the existing F9-parity harness `tests/unit/repositories/test_task_auto_continued_lifecycle.py` (landed on the branch per dispatcher-verified). The test seeds a straddled child → runs the boot pass (`mark_task_auto_continued` CAS stamp actually invoked) → real `clear_all` on BOTH repositories (task-side + queue-side, with the arm-3 `EXISTS instances` join) → asserts `candidates==1` after the first boot → kills the daemon → re-constructs the manager → runs the second boot → asserts `already_resuming==0` and no double-continue. The Issue-2 two-boot test ALSO satisfies Issue-1's real-SQL requirement for the task-side predicate. The F-1 demo E2E remains OPTIONAL per `decisions.md §9` (the Issue-2 two-boot test is sufficient to constitute the F-1 evidence bar for the wipe seam). | Test passes deterministically on three consecutive local runs of the two-boot test. |
| S2 | F-1: bus-side None-error does not flip `_parent_errored` | New test `test_none_error_does_not_flip_parent_error` injects `Outcome(status="error", error=None)` at the two bus gates (`dependency_bus.py:702` + `:907` per the LANDED state) and asserts `bus._parent_errored` is NOT set for any target. | Test passes. |
| S3 | F-1: real-error still flips `_parent_errored` | New test `test_real_error_flips_parent_error` injects `Outcome(status="error", error="boom")` and asserts the flip happens at the two LANDED bus gates. | Test passes. |
| S4 | F-1: terminal + `auto_continued_at` survives `clear_all` (REAL SQL, per Issue-1) | **Issue-1 (binding):** a NEW test `test_terminal_auto_continued_survives_clear` executes the real `TaskRepository.clear_all(preserve_in_flight=True)` SQL on a real DB (file-backed SQLite at minimum, PG-preferred) — NOT a Python re-implementation of the predicate. Seeds a task with `status='failed'` and `auto_continued_at=<now>`, runs `clear_all`, asserts the row survives. The test must exercise the actual `EXISTS instances` subquery + the kill-switch OFF branch (when `ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE=0`). | Test passes on real DB. |
| S5 | F-1: terminal-no-marker is deleted by `clear_all` (REAL SQL, per Issue-1) | **Issue-1 (binding):** a NEW test `test_terminal_no_marker_deleted_by_clear` executes the real `TaskRepository.clear_all(preserve_in_flight=True)` SQL on a real DB. Seeds a task with `status='failed'` and `auto_continued_at IS NULL`, runs `clear_all`, asserts the row is deleted. The test must exercise the actual `EXISTS instances` subquery + the kill-switch ON branch. **PLUS** a NEW queue-side S-test (currently ZERO coverage per the approver's Issue-1 observation): `test_message_queue_clear_all_2arm_disjunction` exercises the real `MessageQueueRepository.clear_all(preserve_in_flight=True)` SQL on a real DB with the 2-arm disjunction (arm 1 status + arm 3 `EXISTS instances` co-condition). | Test passes on real DB. |
| S6 | F-1: boot-sequence candidates==1 (REAL two-boot test, per Issue-2) | **Issue-2 (binding):** the S1 two-boot test covers this criterion (the post-first-boot `candidates==1` assertion is exactly the S6 measure). The prior "mocked boot envelope" framing is **REJECTED** per Issue-2; the real two-boot test IS the S6 evidence. | Test passes (covered by S1's two-boot test). |
| S7 | F-1: kill-switch gate (`ENSEMBLE_BOOT_AUTO_CONTINUED_PRESERVE`, per W-3) | NEW test `test_boot_auto_continued_preserve_kill_switch` exercises the real `TaskRepository.clear_all(preserve_in_flight=True)` SQL with the env var set to `"1"` and `"0"` in sequence. Asserts: arm 3 ACTIVE (terminal-stamped row of non-terminal instance survives) AND arm 3 DISARMED (terminal-stamped row is deleted; arm 1 stays active in both cases). The test must use the real SQL, NOT a Python re-implementation. | Test passes on real DB. |
| S8 | F-2: already-reported skip | **⚠ SUPERSEDED by W-4 pivot.** Replaced by S22 (PREFIX-ledger skip in lane-2 per-row pass). | n/a |
| S9 | F-2: preserved-wake skip | **⚠ SUPERSEDED by W-4 pivot.** The preserved-wake class is now owned by RDRS lanes 1/3/4, not the F-2 lane-2 path. | n/a |
| S10 | F-2: child RUNNING skip | **⚠ SUPERSEDED by W-4 pivot.** Lane 2 scans `c.status=='completed'` only (`repository.py:1222`); RUNNING children are excluded by the query itself. Replaced by S23. | n/a |
| S11 | F-2: multi-child mixed (synthesize only straddled) | **⚠ SUPERSEDED by W-4 pivot.** Replaced by S24 (real-DB multi-child mixed). | n/a |
| S12 | F-2: sweep-twice single injection | **⚠ SUPERSEDED by W-4 pivot.** Replaced by S25 (RDRS sweep-twice idempotency). | n/a |
| S13 | F-2: kill-switch OFF → wedge persists, kill-switch ON → wedge heals | **⚠ SUPERSEDED by W-4 pivot.** The `ENSEMBLE_WC_WEDGE_SWEEP_ON_BOOT` env var is RETIRED. RDRS's per-lane kill switches in `config.py:1302-1378` gate the lane-2 path. Replaced by S26. | n/a |
| S14 | F-2: non-straddle restart still passes (no regression) | **⚠ SUPERSEDED by W-4 pivot.** Replaced by S27 (real-DB regression on real delivery seam). | n/a |
| S15 | Keep-green: the four auto-continue unit files + two packs stay green | All four files + two packs pass on the worktree after the Phase 1 + Phase 2 changes are applied. | All green on three consecutive local runs. |
| S16 | F-1: full-dir pytest sweep stays green | The project-level full-dir pytest sweep (per the post-merge-gate lesson — never the unit subset alone) is green after the Phase 1 + Phase 2 changes. | Green. |
| S17 | Boot-sequence ownership block is present | `daemon/services/instance_lifecycle.py` carries the numbered list mapping 1:1 to the steps in `decisions.md §5`. | The block is present and matches the 13-step list. |
| S18 | The two inline comments at the boundary lines are present | `dependency_bus.py:671` and `repository.py:4380` carry short (≤5 line) inline comments referencing the `instance_lifecycle.py` block. | Both comments are present. |
| S19 | F-2 demo E2E lands durable evidence in the repo (UNCONDITIONAL per C-2) | `.agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/` carries the boot logs + per-leg findings + recipe pointer; not just `/tmp` artifacts. The F-1 demo E2E is OPTIONAL per tester judgment; see `decisions.md §9` for the skip condition (now satisfiable via the Issue-2 two-boot test). | All listed artifacts present. |
| S20 | The deferred design (a) recommendation is recorded for the next commission | `decisions.md §3a` carries the in-scope/deferred rationale + the follow-up verification task. | Section is present and complete. |
| S21 | F-2: lane-2 admits anchor-less completed children of non-terminal parents | New test (in the existing `tests/unit/test_report_delivery_recovery_service.py` or a new module-level test): seed a child in `status='completed'`, NO `message_queue` anchor row (post-wipe state); run RDRS lane 2; assert the child is ADMITTED. | Test passes. |
| S22 | F-2: PREFIX-ledger check skips already-delivered children | New test: pre-stamp the parent's `message_queue` with `source LIKE 'internal_report:{child}:%'` (READY/PROCESSING/COMPLETED); run lane 2; assert NO new ensure_deferred. | Test passes. |
| S23 | F-2: child RUNNING is excluded by the lane-2 query (not a skip) | New test: seed a child in `status='running'`; run lane 2; assert the child is NOT in the result (excluded by `c.status=='completed'` at `repository.py:1222`). | Test passes. |
| S24 | F-2: multi-child mixed (lane-2 processes the straddle child only) | **Real-DB integration test on the real delivery seam** (C-2 — MANDATORY): seed a parent with three children (one straddle, one RUNNING, one already-delivered); run RDRS lane 2; assert exactly one `ensure_deferred` for the straddle child. | Integration test passes against PG. |
| S25 | F-2: lane-2 run twice → single injection (idempotent) | New test: run RDRS lane 2 twice on the same wedge child; assert exactly one `ensure_deferred` (the obligation-triple unique index enforces idempotency). | Test passes. |
| S26 | F-2: RDRS lane-2 kill-switch gate | Verify the lane-2 kill-switch (per `config.py:1302-1378`) gates the lane-2 path; OFF → lane 2 produces zero work; ON → lane 2 runs. | Test passes. |
| S27 | F-2: non-straddle restart still passes (no regression) | **Real-DB integration test on the real delivery seam** (C-2): non-straddle restart; assert the lane-2 extension does not produce spurious ensure_deferred markers on non-wedge children. | Integration test passes against PG. |
| S28 | F-2: live delivery between lane-2 scan and per-row ledger re-check → lane must skip (renamed S15 per C-2) | New test: between the lane-2 scan and the per-row pass's prefix-ledger re-check, a live delivery inserts a fresh `internal_report:<child>` message into the parent's `MessageQueue` (or stamps a new `source` on the parent's checkpoint); the lane-2 per-row pass MUST skip (increment skip-already-reported counter) and NOT double-inject. Test code: extends the existing RDRS test suite. | Test passes. |

**Reviewer checklist** (operational; for the F-1/F-2 review
pass under REVISION CYCLE 2):

* [ ] `research-findings.md` — worktree-anchored evidence
  base; the §6 drift table is present and reviewed; the
  REVISION CYCLE 2 section (appended) carries the three
  explorer reports.
* [ ] `decisions.md` — every design choice has rationale +
  alternatives + citation. Load-bearing: §1 (F-1 bus-side
  fix), §2 + §2c + §13 (F-1 wipe-side extension + kill-switch
  + arm-3 lifecycle bound), §12 (F-2 PIVOT + W-2 guardrails),
  §14 (W-1 final choice + W-5 correction note), §15
  (post-dispatch verifications, includes prior cycle's §10
  content). §3 and §4 carry SUPERSEDED notes.
* [ ] `plan-overview.md` (this file) — scope in/out is
  explicit; the success criteria are testable (S1-S6, S15,
  S20, S21-S28); the risk register includes the F-1
  reconciliation, the W-4 pivot risks (R12, R13), and design
  (a) deferred items; the file inventory reflects the
  eliminated `wc_wedge_sweep` files.
* [ ] `phase1-plan.md` — the two bus gates + arm-3-only
  predicate extension (arm 2 DROPPED per W-3) + marker-
  clearing at terminalizer + kill-switch rename + the
  seven-test matrix (incl. F-1 kill-switch S7) + the pack
  shape.
* [ ] `phase2-plan.md` — the lane-2 query extension at
  `report_injection/repository.py:1039-1253`; the per-row
  PREFIX-ledger check; the `child_message_id` derivation
  from checkpoint (W-1 fallback ii); the line-walk narrative
  (marker → transition → reconcile → materialization → claim
  → wake); the W-2 do-not-call guardrails; the real-DB
  integration test (C-2) + S21-S28 assertions.
* [ ] `phase3-plan.md` — the F-2 demo E2E is UNCONDITIONAL
  per C-2; the integration test gate is in place; test
  counts and "six/six" wording are corrected.

---

## File Inventory Summary (worktree-anchored, REVISION CYCLE 2)

### Files to MODIFY (Phase 1, F-1 fix per W-3)

| Path | Change | Anchor verified |
|---|---|---|
| `daemon/services/dependency_bus.py` | Truthy-error condition at `:671`; same at `:859`; defensive `WARNING` log on None path; inline comment at `:671` referencing `instance_lifecycle.py` ownership block. | yes |
| `daemon/repositories/task/repository.py` | Preserve DELETE restructure at `:4380-4394` (2-arm disjunction: arm 1 status + arm 3 with `EXISTS instances` co-condition — arm 2 DROPPED per W-3); JOURNAL mirror at `:4337-4364`; inline comment at `:4380` referencing ownership block; NEW `clear_task_auto_continued` method (mirroring `mark_task_auto_continued` at `:988-1067`). | yes |
| `daemon/repositories/message_queue/repository.py` | Preserve DELETE restructure at `:1002-1008` (2-arm disjunction); JOURNAL mirror at `:978-994`. | yes |
| `daemon/manager.py` | `capture_boot_epoch(self.engine)` call inside the wipe block at `:771-873` (BEFORE the wipe; idempotent on second call) — RETAINED for the auto-continue consumer at `api.py:1553` (not for F-1 arm 2, which is DROPPED); terminalizer call-site gate at `:11660-11721` invokes marker-clearing after successful `complete_task`. | yes |
| `daemon/services/instance_lifecycle.py` | New ownership-contract block (13-step list) alongside the Pause-First Then Quiesce convention. The block is the FIRST labelled block in the file (beside ≠ after; per W-5-style re-anchor on §5's prior "beside" claim). | yes (file exists, no current block; add) |
| `daemon/services/job_feedback_observer.py` | No change (the observer's own fallback at `:122, :171-174` is the safety net; per `decisions.md §1`). | yes |

### Files to MODIFY (Phase 2, F-2 RDRS lane-2 extension per W-4 PIVOT)

| Path | Change | Anchor verified |
|---|---|---|
| `daemon/repositories/report_injection/repository.py` | Lane-2 query extension at `:1039-1253` (~10-30 lines): admit anchor-less completed children of non-terminal parents; the wipe-immune anchor is the pre-existing `FROM instances c JOIN instances p ON p.instance_id=c.parent_id` at `:1216-1220` (DIFFERENT from F-1 arm 3's `EXISTS instances` subquery in `clear_all` SQL at `task/repository.py:4380-4394` — see N-4 anchor-conflation fix in `revision-summary.md`). | yes |
| `daemon/services/report_delivery_recovery.py` | Per-row pass additions at `:865-1016` (and the lane-2 call site at `:1016-1021`): PREFIX-ledger check BEFORE minting; checkpoint-derived `child_message_id` (W-1 fallback ii). | yes |
| `daemon/repositories/task/repository.py` | (Already in Phase 1; no additional changes for F-2.) | yes |
| `daemon/manager.py` | (Already in Phase 1; no additional changes for F-2.) | yes |
| `daemon/services/instance_lifecycle.py` | (Already in Phase 1.) | yes |
| `daemon/services/child_reports.py` | REUSE only — no in-place edits. The phase-2 plan NEVER plans a direct call into `ChildReportsService` public methods (W-2 guardrails in `decisions.md §12d`). | yes (REUSE ONLY) |

### Files to CREATE (Phase 3)

| Path | Change | Anchor verified |
|---|---|---|
| `.agents/tester/EVIDENCE/2026-10-04-durability-f1f2-demo/` (UNCONDITIONAL for F-2) | NEW DIR. Durable evidence per `decisions.md §9`. F-2 demo E2E is UNCONDITIONAL for first landing per C-2; F-1 demo stays optional per tester judgment. Contains: `logs/` (boot logs, F-2 mandatory), `findings.md` (per-leg findings, F-2 mandatory), `recipe-pointer.md` (R18 recipe pointer, F-2 mandatory). | new dir (F-2 unconditional) |
| `.agents/tester/RESULTS/2026-10-04-durability-f1f2-merge-gate.md` | NEW FILE. Per-pack + full-dir results + UNCONDITIONAL F-2 demo results + (if applicable) the F-1 EVIDENCE pointer. | new file |
| `/tmp/<tag>-wc-wedge-demo-boot.sh` | NEW FILE (outside the worktree, in `/tmp` per the R18 convention; created by the F-2 demo run). UNCONDITIONAL — the F-2 demo runs on every F-2 landing per C-2. | new file (outside worktree) |

**No new test files in Phase 3.** The F-1 unit tests
(`tests/unit/services/test_discard_on_startup_dependency_bus_race.py`
+ `tests/unit/repositories/test_task_auto_continued_lifecycle.py`
+ `tests/unit/repositories/test_message_queue_clear_all_2arm_disjunction.py`)
and the F-1 pack (`test/packs/discard_on_startup_dependency_bus_race_unit_test.sh`)
were created in Phase 1 and are reworked in-place
per the approver's Issue-1 + Issue-2 evidence-bar
mandate; the F-2 unit/integration tests are EXTENDED
in Phase 2 (task 2.11) from the existing RDRS suites.
Phase 3 only RUNS these tests as verification gates
(tasks 3.1, 3.2, 3.4) — it does not create new test
files. The previously-planned
`tests/unit/services/test_wc_wedge_sweep.py` and
`test/packs/wc_wedge_sweep_unit_test.sh` are
**NEVER CREATED** (per W-4 pivot, REVISION CYCLE 2)
— the `wc_wedge_sweep.py` module was also never
created (per `decisions.md §12a` + §12c). The
F-2 architecture is the RDRS lane-2 extension
(`daemon/repositories/report_injection/repository.py:1039-1253`),
not a new sweep module.

### Files NOT touched (binding, per scope)

* `daemon/services/stale_task_recovery.py` — off the wedge
  path; per `decisions.md §1` and Explorer A Q1.
* `daemon/services/child_reports.py` — Phase 2 REUSES the
  service via the RDRS chain; the producer contracts at
  `:420, :653` are NOT modified (per `decisions.md §6`).
  The phase-2 plan NEVER plans a direct call into
  ChildReportsService public methods from any recovery lane
  (W-2 guardrails in `decisions.md §12d`).
* `daemon/services/auto_continue_boot_pass.py` — no change;
  the existing kill-switch convention is the precedent.
* `daemon/config.py` — no new field; RDRS's per-lane kill
  switches in `:1302-1378` already exist.
* `tests/unit/services/test_auto_continue_boot_pass.py` and
  the other three auto-continue unit files — no change; the
  keep-green list is a regression gate (per S15).
* `daemon/services/wc_wedge_sweep.py` — **never created**;
  ELIMINATED per W-4 pivot.
