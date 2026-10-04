# Decisions: auto-continue RUNNING instances after daemon restart

Every decision below adopts the technical-analysis.md recommendation as the plan's working decision (per the G1-G8 adjudication rule), with rationale + the rejected alternative. Nothing the analysis already decided was re-decided. Sources: technical-analysis.md §§Architecture, A-F, G; investigation.md §C; planner's own verification hop for G4.

---

## D1. Continue-in-place via `_schedule_explicit_handle_resume(silent=True)` — NOT enqueue a synthetic "continue" message

**Decision:** The boot pass continues the interrupted turn in place: `find_paused_or_cancellable_turn`-class selection → `_has_checkpoint` → `_schedule_explicit_handle_resume(silent=True, target_work_id=<orphan work_id>, handle_work_id=<orphan work_id>, selected_suspension_reason=None, route_outcome="boot_continue")` → background `_resume_processing_background(is_retry=True, silent=True, message_source="cascade_resume")` → `graph_input=None` → `astream(None)` (pure checkpoint continuation, no HumanMessage injected; `instance_messaging.py:4267-4269`).

**Rationale:** Reuses the most-exercised resume primitive in the codebase (answer-gate + report_or_external_resume routes already drive it); zero new messaging mechanics (AC2); no spurious user-visible message; no new Task/MessageQueue rows.

**Rejected alternative:** the WC-wake precedent's `enqueue_message("continue", source="system:resume_wake", priority=0)` (`manager.py:10837-10860`) — creates a fresh Task + MessageQueue row that would race the existing orphan RUNNING task and inject a spurious HumanMessage — exactly what the user's "no parallel messaging/continuation path" directive forbids.

## D2. Durable idempotency = Option a′ — boot CAS on `task.auto_continued_at`, stamped AFTER the resume schedules

**Decision:** New nullable column `task.auto_continued_at TIMESTAMP`. Per candidate: `_has_checkpoint` pass → resume scheduled with return `{"status": "resuming"}` → ONLY THEN stamp via CAS `UPDATE task SET auto_continued_at=:boot_epoch WHERE id=:id AND status='running' AND (auto_continued_at IS NULL OR auto_continued_at < :boot_epoch)`. Candidate selection uses the same predicate shape folded into one statement (claim-guard convention, `repository.py:2230-2294` anti-starvation invariant). StaleTaskRecovery backstop catches any miss at boot+10 min.

**Rationale (a′ vs plain a):** stamping after the resume actually scheduled makes "marked" ≡ "continued" — eliminates the marked-but-never-resumed window that plain Option (a) (CAS-first-RETURNING) has when the resume then fails (e.g. no checkpoint). The residual crash gap (die between schedule and stamp) re-schedules on next boot, which is safe: `astream(None)` on an advanced checkpoint is idempotent at the LangGraph level and the ExecutionGate per-instance lock serializes.

**Rejected alternatives:** (b) `instances.auto_continued_at` — the per-instance CAS would be a Python pre-check, structurally weaker than a task-row atomic UPDATE, duplicating a predicate the claim-guard already owns; also pollutes the instance row with task-scoped bookkeeping. (c) `idempotency_key` partial-UNIQUE / wake-journal CAS / `uq_job_locks_slot` — wrong substrates (the feature creates NO new row to dedup). (B: extend StaleTaskRecovery with threshold=0) — rejected: burns `max_retries=3` on reboot loops (retry budget is for transient failures, not process death), changes StaleTaskRecovery's blast radius for both boot and steady-state loops, and cannot correctly exclude WAITING_CHILDREN-with-RUNNING-task races.

## D3. Boot placement: `daemon/api.py:1522` — after `sweep_wake_records` (:1521), before `upgrade_journal_sweep.start()` (:1538)

**Decision:** Insert the pass inside the existing boot envelope try/except as its own inner try/except, between the wake sweep block and `upgrade_journal_sweep.start()`.

**Rationale:** After the bus (`init_dependency_bus`, `api.py:1306`) so child spawns from continued turns are tracked; after the wake sweep so the wake's PENDING Task row exists before the resume schedules (deterministic FIFO, AC4); before the periodic tick (consistent state); before the HTTP listener (no user-message race; later user messages simply queue behind per the existing claim path). Anchors verified at cf8efbef.

**Rejected alternatives:** before the wake sweep — breaks FIFO determinism and the arm-notify UX (wake would arrive mid-continued-turn); inside StaleTaskRecovery — conflates age-gated steady-state backstop with boot-corrective (different blast radius; G8 keeps them orthogonal and complementary); after listener-up — invites user-message races during the pass.

## D4. Terminalize-early REJECTED (AC4) — the orphan Task stays `status='running'`

**Decision:** Continue-in-place; the orphan RUNNING row is the durable proof one driver owns the turn. The pass never force-cancels, never reaps, never writes task status.

**Rationale:** The claim-guard (`instance_id NOT IN (SELECT instance_id FROM task WHERE status='running')`, atomic inside `claim_pending_task`) blocks the wake's PENDING claim while the orphan is RUNNING → wake lands FIFO-behind the continued turn → matches arm-notify UX (outcome report AFTER the turn finishes). Terminalizing early opens the claim window → wake + retry/continue compete (Interleaving Y — the double-fire). Pinned by phase-3 tests incl. the structural no-reaper guard.

**Rejected alternative:** `force_cancel_and_schedule_retry` at boot (Interleaving Y / Option B mechanics) — creates the double-turn window and breaks the wake UX.

## D5. G1 — task-level column (`task.auto_continued_at`), per analysis §A

**Decision:** Column on `task`, not `instances`. **Rationale:** atomicity (single-statement CAS mirroring the claim-guard) beats instance-level greppability; strictly smaller patch; the "which instances were continued this boot" question remains answerable by joining. **Rejected:** `instances.auto_continued_at` (Option b) — see D2.

## D6. G2 — do NOT increment `task.retry_count`

**Decision:** The pass never touches the retry budget; `is_retry=True` is forced via the resume kwargs (`is_retry` derivation `task_processor.py:521-527` accepts `original_resume_mode`), so checkpoint resume fires regardless.

**Rationale:** the budget is for transient failures; a reboot loop is recurrent process death — burning budget would permanently fail healthy turns after 3 restarts. Documented in the module docstring. **Rejected:** incrementing (budget symmetry argument) — conflates failure modes.

## D7. G3 — strict `status='running'` task predicate only (no `idle`/`queued`)

**Decision:** The feature is RUNNING-only. `idle`/`queued` instances have no interrupted in-flight turn (no checkpoint worth resuming; an idle instance's last checkpoint is from an already-terminal turn). **Rejected:** widening the predicate — scope creep beyond the user-verbatim feature core.

## D8. G4 — attestation gate: RESOLVED by planner verification hop; no scope change

**Decision:** No attestation handling in the pass. Planner hop (2026-10-04) verified: the only reset site for `attestation_denied_count`/`completion_gate_escalated` is `instance_messaging.py:1977-2015` — keyed on revive-from-terminal via a NEW top-level user/mission message (`priority==1` AND `msg_type==HUMAN`), a path reachable ONLY through `enqueue_message`, which the boot pass never calls. The attestation gate itself is a graph node (`graph.py:5338`) that re-evaluates normally inside the resumed turn; the silent resume inherits the original turn's session state (user-origin window: silent is the explicit skip, `manager.py:8101-8112`).

**Rationale:** closes investigation Unverified #3 with evidence; no Open-Question escalation needed because the verification produced a definitive answer that does not change scope. **Rejected:** treating it as an open question — unnecessary once verified.

## D9. G5 — JobFeedbackObserver: no follow-up needed; NO new terminal tokens

**Decision:** The resume routes through the same `complete_task`/`fail_task` flow as every other resume; the observer's terminal-token contract (accepts only `completed`/`error`/`failed`, `job_feedback_observer.py:316`) is unchanged. The feature introduces NO new terminal status string — pinned in phase-2 review (AC2 audit) and covered by the terminal-token repo trap in risk-register R8.

**Rationale:** structurally identical to cascade-resume, which already settles jobs correctly. **Rejected:** re-verifying observer keying — off critical path, already argued by the analysis.

## D10. G6 — sequential v1

**Decision:** The per-candidate loop is sequential; no `asyncio.gather` over checkpoint probes. Wall-clock target <5 s scheduling is met for observed N (≤33 in the worst observed crash loop). Batched probes = v2 follow-up. **Rejected:** parallel probes — log-linearity and determinism beat a latency win we don't need yet.

## D11. G7 — RUNNING task with terminal/missing instance row: SKIP (no revive, no reap)

**Decision:** The selection subqueries exclude PAUSED/terminal/WC instance rows, so such tasks are invisible to the pass. No revival (user's "terminal never touched" directive; revival requires a fresh user/child message per `instance_messaging.py:1486-1510`). No reaping — StaleTaskRecovery's age-gated backstop owns them. Documented in the module docstring. **Rejected:** revive-from-terminal at boot (injects an unsolicited turn into a dead instance — the exact behavior arm-notify refuses for dead arm-targets); pass-side dead-lettering (duplicates StaleTaskRecovery).

## D12. G8 — StaleTaskRecovery co-existence: orthogonal, no race, no changes

**Decision:** StaleTaskRecovery unchanged. It runs earlier (boot step 5c) and is a boot-time no-op (amnesty clamp short-circuits a young daemon, `repository.py:3155-3181`); it excludes PAUSED/TERMINATED ("recovery must not auto-resume such tasks"); its 60 s steady-state loop is age-gated at 10 min. The new pass is immediate-at-boot; StaleTaskRecovery is the miss-backstop. **Rejected:** any coupling (threshold flips, shared selectors) — blast-radius contamination.

## D13. AC9 — kill-switch: `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART`, env-direct, default ON, `=0` disables, per-boot read

**Decision:** Mirrors `ENSEMBLE_POST_RESTART_ARM_NOTIFY` exactly (constant + `os.environ.get(ENV, "1") != "0"` at the pass entry; NOT cached; no Pydantic field on `ServicesConfig`; no settings-router surface).

**Rationale:** same feature family (corrective restart-recovery), same empirical-gap motivation (32 frozen restarts), same operator escape-hatch semantics: flip env + restart → previous behavior (StaleTaskRecovery backstop) resumes. Default-ON because the current frozen behavior is the bug; R15 default-OFF is for opt-in rollouts (wrong requirement); typed-config is for intervals (explicitly "no kill-switch" HARD POLICY, `config.py:1633-1634`). **Rejected:** R15 metadata toggle (wrong surface: per-project storage + UI; wrong default; instant-flip is meaningless for a boot pass); default-OFF (delays the corrective behavior the user explicitly demanded).

## D14. AC3 — WAITING_CHILDREN skipped; `DependencyBus.start()` owns it; gate concept = bus count

**Decision:** The pass never re-drives WC parents (selection excludes them). The bus's boot re-arm (`_warm_cache` → `_recover_fired_unsent` with the `enqueued_at IS NULL` C1 dedup → `_sweep_orphan_watchers`, `dependency_bus.py:1499-1560`) covers both child-completed-before-death and child-completes-after-reboot orderings. Authoritative gate for any WC reasoning is `bus.count_pending_for_target_sync` (`dependency_bus.py:1069`) — the `instances.status='waiting_children'` string is cosmetic/deprecated. E2E must PROVE the parent wake (phase 5, task 5.5). RAM-only `_parent_error_message` (`dependency_bus.py:466-472`) = follow-up ticket, out of scope, happy path unaffected.

**Rejected alternative:** pass-side WC handling — races `_recover_fired_unsent`/`_sweep_orphan_watchers`, no semantic gain.

## D15. AC7 — PACKS.md is tester-owned; pack SPECS registered in-plan, PACKS.md edit deferred

**Decision:** The implementation lane creates the pack wrapper scripts; `.agents/tester/PACKS.md` rows are added by the tester lane from the SPECs in test-strategy.md. Full-dir gates + cascade e2e per the 2026-10-03 precedent (the v0.13.10 lesson: targeted packs missed a full-dir red).

**Rationale:** role boundary (PACKS.md is the tester's gate registry); the precedent failure mode was under-scoped gates, which the full-dir requirement addresses directly.

## D16. Route-outcome telemetry value `"boot_continue"`

**Decision:** New `route_outcome` value on the `_schedule_explicit_handle_resume` call (existing values: `answer_gate_existing_turn`, `report_or_external_resume` at `manager.py:10616,10657`). Free-form structured string flows to logs; no registry change required (verified: the parameter is a plain string kwarg).

**Rationale:** one grep answers "what did the boot pass continue" in production logs; consistent with the existing telemetry convention.

## D17. No new index on `auto_continued_at`

**Decision:** No index. Selection is gated on `status='running'` (served by `idx_task_status_type_created`); stamp CAS is PK-scoped. Revisit only if boot-pass profiling at N>100 shows need.

---

## Decision-to-AC index

D1→AC1/AC2 · D2→AC5 · D3→AC4/AC6 · D4→AC4/AC5 · D5→AC5 · D6→AC5 · D7→AC1 · D8→(gate interaction, no AC) · D9→AC7 (terminal-token trap) · D10→(scalability) · D11→AC1 · D12→AC5/AC6 · D13→AC9 · D14→AC3 · D15→AC7 · D16→(observability) · D17→(perf)
