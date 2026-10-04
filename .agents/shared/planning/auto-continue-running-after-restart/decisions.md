# Decisions: auto-continue RUNNING instances after daemon restart

Every decision below adopts the technical-analysis.md recommendation as the plan's working decision (per the G1-G8 adjudication rule), with rationale + the rejected alternative. Nothing the analysis already decided was re-decided. Sources: technical-analysis.md §§Architecture, A-F, G; investigation.md §C; planner's own verification hop for G4.

---

## D1. Continue-in-place via `_schedule_explicit_handle_resume(silent=True)` — NOT enqueue a synthetic "continue" message

**Decision:** The boot pass continues the interrupted turn in place: `find_paused_or_cancellable_turn`-class selection → `_has_checkpoint` → `_schedule_explicit_handle_resume(silent=True, target_work_id=<orphan work_id>, handle_work_id=<orphan work_id>, selected_suspension_reason=None, route_outcome="boot_continue")` → background `_resume_processing_background(is_retry=True, silent=True, message_source="cascade_resume")` → `graph_input=None` → `astream(None)` (pure checkpoint continuation, no HumanMessage injected; `daemon/services/instance_messaging.py:4267-4269`, r2 path).

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

**Decision:** No attestation handling in the pass. Planner hop (2026-10-04) verified: the only reset site for `attestation_denied_count`/`completion_gate_escalated` is `daemon/services/instance_messaging.py:1977-2015` (r2 path) — keyed on revive-from-terminal via a NEW top-level user/mission message (`priority==1` AND `msg_type==HUMAN`), a path reachable ONLY through `enqueue_message`, which the boot pass never calls. The attestation gate itself is a graph node (`graph.py:5338`) that re-evaluates normally inside the resumed turn; the silent resume inherits the original turn's session state (user-origin window: silent is the explicit skip, `manager.py:8101-8112`).

**Rationale:** closes investigation Unverified #3 with evidence; no Open-Question escalation needed because the verification produced a definitive answer that does not change scope. **Rejected:** treating it as an open question — unnecessary once verified.

## D9. G5 — JobFeedbackObserver: no follow-up needed; NO new terminal tokens

**Decision:** The resume routes through the same `complete_task`/`fail_task` flow as every other resume; the observer's terminal-token contract (accepts only `completed`/`error`/`failed`, `job_feedback_observer.py:316`) is unchanged. The feature introduces NO new terminal status string — pinned in phase-2 review (AC2 audit) and covered by the terminal-token repo trap in risk-register R8.

**Rationale:** structurally identical to cascade-resume, which already settles jobs correctly. **Rejected:** re-verifying observer keying — off critical path, already argued by the analysis.

## D10. G6 — sequential v1

**Decision:** The per-candidate loop is sequential; no `asyncio.gather` over checkpoint probes. Wall-clock target <5 s scheduling is met for observed N (≤33 in the worst observed crash loop). Batched probes = v2 follow-up. **Rejected:** parallel probes — log-linearity and determinism beat a latency win we don't need yet.

## D11. G7 — RUNNING task with terminal/missing instance row: SKIP (no revive, no reap)

**Decision:** The selection subqueries exclude PAUSED/terminal/WC instance rows, so such tasks are invisible to the pass. No revival (user's "terminal never touched" directive; revival requires a fresh user/child message per `daemon/services/instance_messaging.py:1486-1510`, r2 path). No reaping — StaleTaskRecovery's age-gated backstop owns them. Documented in the module docstring. **Rejected:** revive-from-terminal at boot (injects an unsolicited turn into a dead instance — the exact behavior arm-notify refuses for dead arm-targets); pass-side dead-lettering (duplicates StaleTaskRecovery).

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

## D18. Δ1 (architect, r3 REVISED) — Success-path orphan terminalizer, call-site gating (not shared-SQL conjunct)

**Decision:** In `_resume_processing_background`'s success branch (verified at `manager.py:11393` function def; success-branch docstring block at `manager.py:11615-11645` — the comment that REMOVED `complete_task` from the resume path; the failure `except Exception as e:` handler starts at `manager.py:11647`, NOT `:11699` as the r2 scheme cited — see D29 §"anchor corrections"), after `_process_resume_finalize` returns successfully, call `complete_task` by `work_id`. The **call is gated at the TERMINALIZER CALL SITE** (Option (b) per the r3 approver), NOT inside the shared `complete_task` SQL:

```python
# inside _resume_processing_background success branch, after _process_resume_finalize
if task is not None and task.auto_continued_at is not None:
    # CAS-stamped by boot pass → this row IS a direct-resume orphan.
    # Call-site gate scopes the terminalizer to direct-resume orphans only.
    await asyncio.to_thread(
        self._task_repo.complete_task, task.id,
        {"resume_outcome": "boot_continue_succeeded"},
    )
```

where `task` comes from the **existing `get_by_work_id(work_id)` lookup** already performed in the failure branch (REUSE verbatim; currently `daemon/repositories/task/repository.py:410` — D29 §"anchor corrections" notes the r2 cite of `:484` is stale, verify-at-use). **The shared `complete_task` SQL keeps byte-identical semantics to pre-feature** (no `auto_continued_at IS NOT NULL` conjunct added). The terminalizer fires ONLY for rows this feature CAS-stamped (Option (b) per the r3 approver).

**Rationale (r3 revised):** The shared `complete_task` SQL is reached by the entire worker-pool / task-processor completion surface (`worker_pool.py:810`; `task_processor.py:291/1080/1530/1576/1633` — verify-at-use, D23-extension); NONE of those callers set `auto_continued_at`. The r2 fold's `WHERE status='running' AND auto_continued_at IS NOT NULL` conjunct inside the SHARED `complete_task` would silently make every other caller a no-op (no row would match → `complete_task` returns None → the worker pool could not complete any task → worker pool dead-lock). The pre-r3 spec's "r2 scope: `WHERE status='running' AND auto_continued_at IS NOT NULL`" was a **scope mismatch** between the terminalizer feature and the shared-complete-task call surface. Option (b) — gate at the terminalizer call site, NOT inside shared SQL — closes this gap: the shared method remains semantically byte-identical (worker-pool / task-processor callers keep working); the terminalizer fires ONLY for CAS-stamped rows (the rows the feature actually owns); the FM-1 worker-claimed RUNNING row is naturally declined (its `auto_continued_at IS NULL` per the boot pass's CAS-only-on-orphan-rows semantics). The `WHERE status='running'` guard INSIDE `complete_task` itself (verified live-tree: `daemon/services/turn_transitions.py:355` `CompleteTurn.run` `WHERE work_id = :work_id AND status = 'running'`, AND `daemon/repositories/task/repository.py:2553` wrapper) remains the safety net for cascade/worker-shape rows (cascade rows are PENDING by the time a resume finishes; worker shapes never reach this branch directly). Status guard asserted in tests (M18 — r3-revised wording).

**Composition with claim-guard (the FM-1 rationale rewrite per approver):** the r2 fold's "terminalizer early-completing a worker-claimed RUNNING row" race concern is handled by the COMPOSITION of (i) the claim-guard's `instance_id NOT IN (SELECT instance_id FROM task WHERE status='running')` blocking (`daemon/repositories/task/repository.py:2230-2294`, D23-verified) and (ii) the call-site gate scoping the terminalizer to CAS-stamped rows only (this D18 r3 decision). The two are independent and additive; neither alone is sufficient. **`manager.py:11115-11140` (the r2 cite for the FM-1 worker-claimed shape) is the resume-cascade phantom-completion message-skip guard, NOT a "worker-claimed shape in the success branch"** (per approver's note: that code is the resume-cascade phantom-completion message-skip guard, and the claim-guard at `repository.py:2230-2294` already prevents the posited race). The original r2 race concern is therefore a non-issue under the call-site-gate design; M18 wording reflects this (test-strategy.md M18 r3).

**Rejected alternative (Option C):** Accept STR-mediated success-path recovery, rewrite AC4/D6 language to say so. Rejected because (i) ~10-min wake delay contradicts AC4's deterministic-FIFO framing, (ii) `retry_count` burn on success contradicts D6, (iii) the call-site gate is a one-line surgical fix — no need to accept the workaround.

**Source:** architecture-recommendation.md Focus 1 (Δ1) + leader ratification 2026-10-04 + r3 approver rejection of shared-SQL scope (Option (b) adopted) + D29 (r3 approver-rejection record).

**Anchor corrections (D29 cross-ref):** the r2 cite of `:11666-11697` was a stale drift snapshot; at `cf95a1a5` the success-branch docstring ENDS at `manager.py:11645` (blank `:11646`) with the `except Exception as e:` handler at `:11647` (NOT `:11699` as the r2 fold cited). Anchor verification: `grep -n "Phase 4b/4c (2026-08-12, pause/resume redesign) — the"` returns `manager.py:11615` (docstring start). See D29 for the full anchor-correction table and verify-at-use rule.

---

## D29. (r3) Approver-rejection record — Δ1 call-site gate (Option (b) ADOPTED, r2 shared-SQL scope REJECTED)

**Decision:** The r3 approver rejected the r2 fold's Δ1 scope (extending the shared `complete_task` SQL with `AND auto_continued_at IS NOT NULL`). Option (b) — gate at the terminalizer CALL SITE — is ADOPTED. The r3 sweep touches only the listed anchors + ride-alongs; no new scope. See D18 r3, M18 r3, risk-register R19 r3, phase2-plan.md T2.8 r3, plan-overview.md reversibility note (r3).

**Rationale (rejection, per approver):** the r2 fold added `auto_continued_at IS NOT NULL` to the SHARED `complete_task` SQL guard (`repository.py:2803` in r2). That breaks shared-path semantics: `complete_task`'s callers include the normal worker-pool completion path (`worker_pool.py:810`; `task_processor.py:291/1080/1530/1576/1633` — D23-extension anchor set, verify-at-use) — NONE of these set `auto_continued_at` → every such call becomes a no-op; the worker pool could not complete any task. It also contradicts D18's original rationale (decisions.md D18 r2:111 — the existing `WHERE status='running'` guard was sufficient). Option (b) preserves the shared `complete_task` semantics byte-identical to pre-feature AND closes the FM-1 race via the COMPOSITION with the claim-guard (see D18 r3 "Composition with claim-guard" paragraph).

**Anchor corrections (re-verified at `cf95a1a5`, this r3 fold):**

| Plan-cited (r2) | Live-tree anchor @ cf95a1a5 | Verifier |
|---|---|---|
| `manager.py:11666-11697` (success-branch tail — the comment block that REMOVED `complete_task`) | `manager.py:11615-11645` (docstring ENDS at `:11645`, blank `:11646`); `except Exception as e:` at `:11647` (NOT `:11699`); `failed_task = None` block at `:11682+` | `grep -n "Phase 4b/4c (2026-08-12, pause/resume redesign) — the"` |
| `manager.py:11699` (the actual `except Exception`) | `manager.py:11647` | `grep -n "except Exception as e:"` adjacent to the docstring |
| `manager.py:11115-11140` (FM-1 worker-claimed shape "in the success branch") | `manager.py:11115-11140` is the **resume-cascade phantom-completion message-skip guard**, NOT a "worker-claimed shape in the success branch" (per approver). The posited race is already blocked by the claim-guard `repository.py:2230-2294`. | `grep -n "phantom"` |
| `repository.py:2803` (`complete_task` def, the WHERE-status guard) | `repository.py:2553` (`complete_task` wrapper) → `daemon/services/turn_transitions.py:355` `CompleteTurn.run` SQL `WHERE work_id = :work_id AND status = 'running'` (the actual guard) | `grep -n "def complete_task"` |
| `repository.py:2932` (`fail_task` def) | `repository.py:2682` (`fail_task` wrapper) → `daemon/services/turn_transitions.py:394` `AbortTurn.run` SQL `WHERE work_id = :work_id AND status IN ('running', 'pending', 'paused')` | `grep -n "def fail_task"` |
| `repository.py:484` (`get_by_work_id` helper) | `repository.py:410` (`get_by_work_id` def) | `grep -n "def get_by_work_id"` |
| `worker_pool.py:810`; `task_processor.py:291/1080/1530/1576/1633` (shared `complete_task` callers) | verify-at-use at `cf95a1a5` (r3 drift on `cite-not-re-verify`); D23-extension captures the set | `grep -rn "complete_task("` task IDs |

**Drift rule (r3):** every execution-lane citation grep-verify at use time at the current HEAD. The r2 anchor table is a `ce148ad2` snapshot; the r3 anchors carry `cf95a1a5` line numbers as the latest verified snapshot, with explicit "verify-at-use" annotations on drift-prone entries (per approver's implementer-facing notes).

**Cross-ref (existing numbering stable):**
- **D18 r3** — Δ1 success-path terminalizer: rewritten to call-site-gate, with the "Composition with claim-guard" paragraph replacing the r2 "FM-1 worker-claimed RUNNING row" rationale (the approver's FM-1 conflation fix).
- **M18 (test-strategy.md) r3** — wording shift from SQL-guard to call-site-gate; the three-way test setup (direct-resume fires / cascade no-op / worker-claimed untouched) STAYS — only the MECHANISM wording shifts.
- **R19 (risk-register.md) r3** — mitigation rewritten to "**Δ1 (D18) MITIGATES (r3 scope): call-site gate in `_resume_processing_background`'s success branch**"; the `WHERE status='running' AND auto_continued_at IS NOT NULL` r2 wording is GONE.
- **T2.8 (phase2-plan.md) r3** — the terminalizer call body rewritten to the call-site-gate design (the `if task.auto_continued_at is not None:` gate wraps the `await asyncio.to_thread(self._task_repo.complete_task, task_id, ...)` call).
- **plan-overview.md (reversibility) r3** — the r2 note "revert the Δ1 surgical touch in `manager.py:11666-11697`" is amended to "revert the r3 call-site-gate wrapper at the same site" — the byte-identical shared-SQL invariant is the new reversibility surface.

**Source:** r3 approver rejection (2026-10-04) + leader ratification + D18 r3 + M18 r3 + R19 r3 + T2.8 r3.

## D19. Δ2 (architect) — `boot_epoch=None` → SKIP pass with WARNING (NOT fallback to aware-datetime)

**Decision:** On `boot_epoch is None`, the pass **SKIPS** with a WARNING log (no DB writes, no scheduling). The pre-amnesty clamp (STR uses `boot_epoch` to short-circuit a young daemon, `repository.py:3161, :4501`) vanishes when `boot_epoch` is None, and the just-started 60s STR loop may reap a >10-min-stale orphan mid-pass → resume scheduled against a CANCELLED work_id → two *sequential* drivers (ExecutionGate serializes) = one wasted duplicate continuation. SKIP avoids the race; the orphan stays `status='running'`, STR's normal age-gated backstop continues to own it (next boot retries, or STR reaps at +10 min as designed).

**Rationale:** The plan's T2.2 step (2) fallback `datetime.now(timezone.utc)` is timezone-**aware** while the entire comparison frame is naive-UTC (`_to_naive_utc` `boot_epoch.py:88,114,136`; `now_utc_naive()` `timestamps.py:51`; `_default_created_at_naive_utc` `models.py:273`; amnesty comment `repository.py:3155-3156`). An aware stamp silently corrupts epoch comparisons on SQLite (lexicographic ISO with `+00:00`) and errors on PG TIMESTAMP. If any fallback timestamp is ever retained anywhere in the pass, it MUST be `now_utc_naive()` — but the cleanest fix is to NOT have a fallback at all (epoch-None is a DB-clock capture failure; the pass must not pretend to know what time it is). M19 pins SKIP + WARNING, no fallback.

**Rejected alternative:** fallback to `datetime.now(timezone.utc)` (aware). Rejected because of the frame mismatch and the residual race. UTC fallback with frame stripping (`.replace(tzinfo=None)`) — also rejected, hides the capture failure and adds a code path that future maintainers will not understand (epoch-None is an exceptional condition that deserves a WARNING, not a silent default).

**Source:** architecture-recommendation.md Focus 1 (Δ2) + leader ratification 2026-10-04.

## D20. Δ7 (architect) — Dedicated worktree MANDATE for implementation + E2E lanes

**Decision:** The implementation lane and the demo-E2E lane run in a dedicated worktree at `../ensemble-src-wt-auto-continue` with:
1. **A fresh uv venv inside the worktree**, gated by `python -c "import daemon; print(daemon.__file__)"` resolving INSIDE the worktree before any test run — the editable-install trap (a venv inherited from the main checkout resolves `daemon` to the main checkout path, silently testing the wrong tree; verified, see blueprint: "Repo & Dev Environment Conventions → Worktree editable-install trap").
2. **Explicit env exports** for any daemon run from the worktree (prod-defaults trap, `agents/developer/rule.md:164` — "Assigned wt_path? cd into the worktree before any git op; **never commit on the main checkout**"). DB pinning must override a `POSTGRES_*` **part** (e.g. `POSTGRES_PORT`), not `POSTGRES_URL` — the checkpointer honors `POSTGRES_URL` but repositories read `POSTGRES_*` parts only (F-DR1-2 split-brain, `daemon/persistence.py:79-89` vs `daemon/repositories/factory.py:189-198`); a part override moves BOTH.
3. The demo E2E (port 7979, `~/agents-ensemble-demo/`) itself targets the demo install, not the worktree — evidence bundle captured under the worktree's plan dir.

**Rationale:** `agents/giter/workflow.md:77-83` (Worktree Mode) — sibling worktrees live at `../<repo>-wt-<slug>/` with a KV claim protocol; the worktree MUST be created BEFORE Phase 1 starts (otherwise Phase 1 commits land on the shared main checkout — exactly the hazard the worktree exists to prevent). Phase 0 (NEW) is the worktree+venv gate; P1-P5 all assume it has passed. The plan's existing refs (P1-P5 task numbers, AC table) are unchanged; P0 is a precondition phase that does not renumber them.

**Rejected alternative:** work on the shared main checkout. Rejected because concurrent commissions switch the shared checkout's branch mid-flight (the hazard that motivated this focus area; observed residue already present in the working tree, see architecture-recommendation.md Focus 6).

**Source:** architecture-recommendation.md Focus 6 (Δ7) + leader ratification 2026-10-04.

## D21. Δ4 (architect) — Selection hardening: full exclusion set + `cancel_requested=False` + >1-candidate log-skip

**Decision:** `find_auto_continue_candidates` predicate is **explicit** on the complete instance-status exclusion set — `instances.status NOT IN ('paused','terminated','completed','error','failed','waiting_children')` — AND on `task.cancel_requested = False` (verified field at `daemon/repositories/task/models.py:227`, r2 path-qualified; boolean type with default `False`, used by `find_cancellable_tasks` at `:4470-4520`). On an instance with **>1 RUNNING task candidates**, the pass **logs WARNING with both task_ids and SKIPS the instance** (no resume). This is **NOT** the count-then-select raising pattern from `find_paused_or_cancellable_turn` (`:825-832`, which RAISES on >1 and would abort the boot for unrelated candidates) — a single SELECT + a count check is the correct shape (the invariant is convention enforced by the claim-guard SQL, not a DB constraint; bug residue could violate it, and the pass must defensively skip).

**Rationale (architect-verified):** `cancel_requested=False` prevents the pass from double-picking a task that an in-flight STR cancel has already targeted. The full exclusion set is explicit (not "PAUSED/terminal/WC") to remove ambiguity at code-review time and to match the exclusion subquery in `find_cancellable_tasks` (`:4470-4520`, verified). The >1-candidate log-skip is conservative: re-claiming an already-claimed task would corrupt the graph (the guard-release mechanism = `fail_task` for failure + D18 terminalizer for success — both write a single terminal status; a >1-candidate instance means the invariant is broken and we must NOT pick a continuation for any of them).

**Rejected alternative:** resume-newest on >1 (some picks could be wrong); raise on >1 (aborts boot for unrelated candidates); widen the predicate (scope creep).

**Source:** architecture-recommendation.md Focus 7 (Δ4) + leader ratification 2026-10-04.

## D22. Δ5 (architect) — Stagger 5 resumes / 2s + pass metrics

**Decision:** Per-candidate scheduling is staggered: after every 5 resumes scheduled, `await asyncio.sleep(2)` (one cadence). This bounds the boot-time LLM-stampede cost (worst observed N=33 → ~14 s total to schedule all; well inside the lifespan startup window). Add pass metrics: counter `auto_continue_boot_pass_resumes_total{route_outcome="boot_continue"}` + histogram `auto_continue_boot_pass_duration_seconds` (observed duration; ignore suspend/resume tail). Loop stays sequential in terms of *probe* ordering (the per-candidate loop body remains single-flight, no `asyncio.gather` over checkpoint probes — D10 holds); only the SCHEDULING cadence is staggered.

**Rationale (architect-verified):** ExecutionGate is per-instance only (`execution_gate.py:108-150`); `WORKER_POOL_SIZE=5` / `CHAT_WORKER_POOL_SIZE=2` (`constants.py:70/:78`) bound **claim** pools, not graph execution; GII throttle is per-turn (`graph.py:65-72`); LLM failover is per-turn. The only real cross-instance bound is the supervisor proxy's RPM/TPM. Worst observed N=33 → 33 concurrent `astream` LLM calls within the first seconds of boot; cost = N × P99 first-token latency added to boot completion + N × token spend; 429s trip the failover path. Stagger at 5/2s bounds both: ~14 s total at N=33; metric gives empirical grounding to revisit cadence. Flip condition (from architect, leader-ratified): if the proxy is shown to absorb 33 concurrent calls without 429s, drop the stagger and keep the metrics — i.e. metrics are permanent, cadence is revisable.

**Rejected alternative:** unbounded v1 (architect verdict: high risk of LLM 429s at observed N; the cadence is the cheap mitigation); semaphore over execution (requires touching private `_resume_processing_background` — blast radius contamination, no ExecutionGate coupling, and a scheduling-bound semaphore is a no-op for execution).

**Source:** architecture-recommendation.md Focus 7 (Δ5) + leader ratification 2026-10-04.

## D23. Δ6 (architect) — Anchor re-pins (verified at ce148ad2)

**Decision:** All execution-lane citations in this plan re-pinned against the live tree at `ce148ad2` and re-verified by the planner before writing. Verified anchors:

| Plan-cited | Live-tree anchor | Verifier |
|---|---|---|
| `claim_pending_task` `:1733+` | `repository.py:1934` (function def) | grep `^    def claim_pending_task` |
| Claim-guard `:2230-2294` | `repository.py:2230-2294` (the `AND instance_id NOT IN (...)` block; SELECT subquery closes at `:2293-2294`) — the broader `:2230-2310` range in earlier drafts INCLUDES the S3-PAUSED carve-out COMMENT block, which is documentation not the guard itself. **r2 (item 15f):** pin the guard span to `:2230-2294`; the `:2230-2310` cite is the broader comment range, not the guard | grep `instance_id NOT IN` |
| Bus `start()` `:1499-1560` | `dependency_bus.py:1499` (`start` def); helpers at `:1804` (`_warm_cache`), `:1839` (`_recover_fired_unsent`), `:1896` (`_sweep_orphan_watchers`) | grep `def start\b\|async def _warm_cache\|async def _recover_fired_unsent\|async def _sweep_orphan_watchers` |
| STR "boot step 5c" | `pool_orchestrator.py:264-294` (the `StaleTaskRecovery(...)` constructor + `recover_on_startup()` + `.start()` block — the thread starts here, **earliest** of all boot subsystems) | grep `StaleTaskRecovery` |
| Amnesty `:3155-3162` | `repository.py:3161` (the `if boot_epoch is not None and boot_epoch >= threshold: return []` line in `find_stale_running_tasks`); `:4501` (same in `find_cancellable_tasks`) | grep `boot_epoch` |
| Resume success branch `:10915` | `manager.py:10915` (`_schedule_explicit_handle_resume`); `:11445` (`_resume_processing_background`); `:11666-11697` (the orphan-success-branch docstring — **Δ1 surgical site**) | grep `_schedule_explicit_handle_resume\|_resume_processing_background` |
| Resume failure branch `:11735-11746` | `manager.py` failure handler runs `fail_task` by work_id (architect cite) — verified to match `repository.py:2932` (`fail_task` def) | grep `fail_task` |
| `complete_task` guard `:2803, :2948` | `repository.py:2803` (`complete_task` def), `:2932` (`fail_task` def); `complete_task` returns `None` when the `WHERE status='running'` guard declines | grep `def complete_task\|def fail_task` |
| SQLite second-granularity epoch | `CURRENT_TIMESTAMP` in SQLite returns second-precision (verified); PG `now()` returns microsecond — same-second double boot shares an epoch → second boot skips → safe (STR owns) | architecture-recommendation.md Focus 1 |
| `_resume_processing_background` `:11479` | `manager.py:11479` is the docstring opener (function def at `:11445`); cite the def line for consistency | grep `async def _resume_processing_background` |

**Rationale:** Anchor drift has caused multiple pre-existing incidents (critical note: lane-site citation drift). Every plan citation must grep-verify at use time. This D23 addendum is the snapshot of the verification pass; future use requires re-verification at the current HEAD.

**Rejected alternative:** carry the original `:1733+` / `:2230-2294` / "boot step 5c" / `:10915` etc. anchors unchanged. Rejected because they are provably wrong against `ce148ad2`.

**Source:** architecture-recommendation.md Focus 1 + Focus 2 + Focus 7 (Δ6 + Δ7 evidence), re-verified by planner before write.

## D24. Δ3 (architect) — Document the no-heartbeat window + optional `last_heartbeat_at` stamp

**Decision:** Document in the module docstring of `auto_continue_boot_pass.py` (and in the AC4/AC5 narrative): **continued turns are STR-reapable at boot+10 min** because the resume path (`_resume_processing_background`, `manager.py:11445`) does not write heartbeats (heartbeat writers are per-worker-thread, `daemon/services/worker_pool.py:60-152` (r2-corrected span — `class TaskHeartbeat` through `update_heartbeat`); zero heartbeat writes on the manager resume path — architect-verified). **STR threshold (r2, item 15i):** the **CONFIG default** (`daemon/config.py:1271` → `stale_task_recovery_threshold_minutes: int = Field(default=10)`) is the effective 10 min — operator config wins. The **CODE default** (`daemon/services/stale_task_recovery.py:25` → `DEFAULT_STALE_THRESHOLD_MINUTES = 15`) applies only when the config field is unset (or when StaleTaskRecovery is constructed with an explicit override). **A knob flip rescales the reap window** — operators on the CODE default see 15 min, not 10 min; D24's "boot+10 min" framing assumes the CONFIG default. The 10/15-min split is documented in the module docstring and in R20; the operational default is 10 min (config wins) and the planning reasoning is sound under that default. STR reap = checkpoint-continuation retry (safe; `astream(None)` from advanced checkpoint is idempotent). `retry_count` burn applies to >10-min turns — this is an **explicit D6 exception**: a successful continued turn that's then STR-reaped at +10 min IS a retry-budget consumer; the >10-min turn is, by definition, a long-turn that the prior boot was attempting. The no-heartbeat window also means `has_instance_busy` stays True until the terminalizer (D18) fires OR until STR reaps.

**Optional mitigation — `last_heartbeat_at` stamp SHOULD-include only if it rides the existing CAS transaction cheaply (leader ratification):** extend `mark_task_auto_continued` to ALSO stamp `last_heartbeat_at = :boot_epoch` in the same `UPDATE … WHERE … AND status='running'` (same atomic CAS — no second statement, no second transaction). This buys exactly one threshold window of immunity (heartbeat is fresh at boot, so STR's `COALESCE(last_heartbeat_at, started_at) < threshold` predicate excludes it for one threshold). If the heartbeat stamp is added later, a follow-up D-addendum records the implementation. As of `6303ba44`, the SHOULD-include decision is **DEFERRED**: implementation lane may adopt if the developer verifies the single-statement extension is truly zero-cost on both drivers (SQLite + PG).

**Escalation flip-condition (architect, leader-ratified):** if the demo E2E or LIVE observation shows **continued turns routinely exceed 10 min**, the heartbeat stamp escalates from 🟡 (document-only) to 🔴 (mandatory), and the stale-reap mid-turn behavior (R20) becomes the dominant failure mode → the heartbeat stamp becomes mandatory. Phase 5 (5.8, NEW) records the observation in the evidence file.

**Rationale:** The plan's R4/R1 narrative ("a continued turn is either terminal or genuinely stuck") is **false for long turns** (architect-verified). Documenting the window is the minimum that ships honest behavior; the optional stamp is the cheap one-statement mitigation.

**Rejected alternative:** write heartbeats from `_resume_processing_background` directly (requires reaching into worker-thread-only writer logic — blast radius contamination, threading concerns); drop the STR reaping for continued turns (changes STR blast radius for unrelated paths); use `is_deferred`/status-only liveness signals (does not match STR's actual predicate).

**Source:** architecture-recommendation.md Focus 3 (Δ3) + leader ratification 2026-10-04.

---

## D25. (r2) Arm-notify wake interaction — verified-safe-in-source

**Decision:** The post-restart arm-notify feature (`feature/post-restart-arm-notify`) and the auto-continue-running-after-restart feature interact at the per-instance level ONLY through the durable structures both features already respect. The interaction is verified-safe in source at the following anchors (all verified at `4f50c34d`):

- **Wake delivery (READY)**: `daemon/services/instance_messaging.py:1777` — the wake's `enqueue_message` creates a MessageQueue row with `status=MessageStatus.READY.value`. The READY row is **outside** `_schedule_explicit_handle_resume`'s stale-message kill set (`manager.py:11000-11010`, PENDING/PROCESSING/RETRYING), so the boot pass's cleanup cannot eat the wake. Architect-verified.
- **Wake Task creation (PENDING)**: `daemon/services/instance_messaging.py:1900-1908` — the wake's PENDING Task row is created in the same `enqueue_message` transaction. The Task is `status=TaskStatus.PENDING.value`; the claim-guard (`daemon/repositories/task/repository.py:2230-2294`, the per-instance `instance_id NOT IN (SELECT … status='running')`) blocks the wake's claim while a continued-turn orphan is RUNNING. Architect-verified.
- **`status_change` event for a RUNNING target**: `daemon/services/instance_messaging.py:2204-2208` — the wake's `enqueue_message` does NOT emit a `status_change` for a RUNNING instance (`ctx.status_changed_to_running` is False on this path; the event fires only on an actual change). Consequence: the continued turn's instance status is NOT touched by the wake; the claim-guard stays deterministic. Architect-verified.
- **Upgrade journal sweep wake primitive**: `daemon/services/upgrade_journal_sweep.py:1092-1093` — the wake sweep's call site uses `self._manager.enqueue_message(instance_id, body, source, priority, metadata)` (DIRECT call, NOT through the deleted `_deliver_wake` helper). Architect-verified.

**Result:** arm-notify wakes and auto-continue boot-pass schedules target the same instance only through (a) the READY message row (out of the pass's kill set) and (b) the PENDING Task row (blocked by the claim-guard while the orphan is RUNNING). No new race is introduced; the arm-notify UX (outcome report AFTER the turn finishes) is preserved on the auto-continue path. **No code change is required to the arm-notify side.**

**Rejected alternative:** a coordinated mutex between the two features' boot passes. Rejected because (i) the durable structures (claim-guard + READY message out-of-kill-set) already do the work, and (ii) a mutex would couple two features that intentionally evolved independently.

**Source:** reviewer item 7 (r2 fold, 2026-10-04) + architect verification at `4f50c34d`.

**r3 anchor verification (per approver's implementer-facing notes):** the four D25 anchors above were verified at `4f50c34d`. Approver marked them as drift-prone; the r3 sweep re-verifies them at `cf95a1a5` and re-records their current line snapshots (verify-at-use at every subsequent consume). See D29 §"anchor corrections" for the cf95a1a5 line snapshots and the verify-at-use rule.

---

## D26. (r2) At-least-once replay semantics — non-idempotent tool families

**Decision:** The boot pass provides at-least-once replay semantics on the resume path: `astream(None)` continues from the last committed checkpoint; uncommitted interrupted nodes re-execute with their partial tool effects (LangGraph commits at node boundaries — the standing exposure of every checkpoint-continuation consumer). For the small set of **non-idempotent tool families**, the committed-pair audit (T5.4b in `phase5-plan.md`, r2) bounds the exposure: a tool call is "committed" iff both an `AIMessage.tool_call_id` AND its matching `ToolMessage.tool_call_id` exist in the post-resume history; the audit asserts no COMMITTED pair is duplicated.

**Non-idempotent tool families (the exposure):**

- **`enqueue_message`** (`daemon/manager.py:7935` → `daemon/services/instance_messaging.py:2108`): a re-execution of a previously-committed `enqueue_message` tool call would create a second MessageQueue + Task row pair for the same payload. The committed-pair audit (T5.4b) catches this. The per-instance claim-guard (D1/D3) prevents a sibling claim, so a re-execution would be visible as a duplicate message in the transcript; the bus and the journal are designed to tolerate this class (the wake is idempotent at the PENDING Task level). **Audit (T5.4b) is the tripwire.**
- **`system_restart`** (or any tool that triggers a daemon restart): a re-execution would trigger a second restart loop. The ExecutionGate per-instance lock (`daemon/services/execution_gate.py:108-155`, r2-corrected span) serializes a re-execution on the same instance behind the first call; the second restart sees the same boot_epoch (same-second SQLite) and skips the same orphan (CAS excludes already-stamped rows for the same epoch). **No double-restart loop in practice.** The audit (T5.4b) is a backstop.

**Idempotent-by-construction tool families (no audit concern):** `read_file`, `grep_files`, `glob_files`, `list_directory`, `read_context`, `list_context` — these return immutable views; a re-execution produces the same response. No audit needed for these.

**Why the committed-pair audit (T5.4b) is the right bounding mechanism:** uncommitted interrupted nodes legitimately re-execute (at-least-once is the standing exposure — not a new class introduced by this feature). The T5.4b audit asserts that the at-least-once exposure is bounded to UNCOMMITTED work; COMMITTED work is not re-executed. The audit is a single, focused, testable assertion — exactly the shape a follow-up commission or a regression-catcher needs.

**Source:** reviewer item 8 (r2 fold, 2026-10-04) + at-least-once semantics in architecture-recommendation.md:96.

---

## D27. (r2) D21 exclusion list — task-level join, **10-value InstanceStatus enum (live-tree verified at `cf95a1a5`, r3)**

**Decision:** The exclusion list in D21 — `instances.status NOT IN ('paused','terminated','completed','error','failed','waiting_children')` — is a TASK-LEVEL JOIN exclusion: the candidates are TASK rows, and the instance status enters the predicate via a subquery. **The full live-tree InstanceStatus enum (verified at `cf95a1a5` via `daemon/repositories/instance/models.py:20-30`) carries 10 values**: `IDLE, RUNNING, WAITING, PAUSED, COMPLETED, ERROR, TERMINATED, QUEUED, WAITING_CHILDREN, FAILED` (`WAITING` is the "active but no in-flight work (e.g. awaiting next user input)" state — NOT a legacy cosmetic value; `QUEUED` is the "idle but has queued messages" state). The D21 NOT-IN set captures 6 of 10 (`paused, terminated, completed, error, failed, waiting_children`); the unexcluded set is `IDLE, RUNNING, WAITING, QUEUED` — i.e. the "live, possibly in-flight" subset. The NOT IN set is correct: it captures every NON-RUNNING non-CONTINUABLE state — `RUNNING` itself MUST remain in the bound (the pass IS the RUNNING case), and `IDLE`/`WAITING`/`QUEUED` are covered by the task-level predicate `task.status='running'` (an IDLE/WAITING/QUEUED instance cannot have a `status='running'` task by definition — the instance status would lag the task status only in the orphan-crash scenario, where the orphan Task row's `status='running'` is the durable record).

**Why the full set is explicit (not "PAUSED/terminal/WC"):** the plan under-modeled the `error` and `failed` instance states in the prose. `error` and `failed` are both terminal-at-the-instance level (an instance in `error` or `failed` is a dead instance from the user's perspective; the boot pass must not inject a turn into it — same as the `terminated` carve-out). Spelling them out removes ambiguity at code-review time and matches the exclusion subquery in `find_cancellable_tasks` (`daemon/repositories/task/repository.py:4469-4500`, D23-verified).

**Why `IDLE` is not excluded (it appears in the enum but is NOT in the NOT IN set):** the boot pass's TASK-LEVEL predicate `task.status='running'` already excludes IDLE tasks (no task on an IDLE instance can have `status='running'` — IDLE means no in-flight task). The instance-level NOT IN set is a SAFETY NET for the case where the instance status lagged the task status (e.g. a previous turn's status='running' row was orphaned by a crash and the instance status was never updated). The instance-level NOT IN must NOT exclude `running` itself (the pass is the RUNNING case). The full set as written is correct.

**Source:** reviewer item 11 (r2 fold, 2026-10-04) + the full InstanceStatus enum in `daemon/repositories/instance/models.py:20-30` (**r3 verified at `cf95a1a5`**: 10 values — `IDLE, RUNNING, WAITING, PAUSED, COMPLETED, ERROR, TERMINATED, QUEUED, WAITING_CHILDREN, FAILED` — the r2 cite of `daemon/services/instance_status.py` is STALE; the enum lives in `instance/models.py` now).

---

## D28. (r2) Δ3 ratification closure — leader decisions locked in

**Decision:** The leader ratifications for the architect's deltas (architecture-recommendation.md, "Decisions pending (leader/developer ratification)" section) are recorded as follows for r2 closure:

1. **Δ1 terminalizer (DECIDED in D18 r3, call-site gate per r3 approver)**: ADOPT the success-path terminalizer in `_resume_processing_background` (`manager.py:11615-11645` success-branch docstring, with `except Exception as e:` at `:11647` — see D29 anchor corrections); the call-site gate (`if task.auto_continued_at is not None:` wrapping `complete_task`) keeps shared `complete_task` SQL byte-identical to pre-feature. **r2 shared-SQL conjunct `WHERE status='running' AND auto_continued_at IS NOT NULL` is REJECTED (D29)**: it would silently make worker-pool / task-processor completion a no-op (those callers don't set `auto_continued_at`). **Option C (accept STR-mediated success-path recovery) is REJECTED** because ~10-min wake delay contradicts AC4's deterministic-FIFO framing AND `retry_count` burn on success contradicts D6.
2. **Δ5 stagger cadence (DECIDED in D22)**: ADOPT 5 resumes / 2s cadence (NOT unbounded v1). Flip condition (metrics-driven): if the supervisor proxy is shown to absorb 33 concurrent calls without 429s, drop the stagger and keep the metrics. The post-E2E 429 measurement (P5 task 5.9, r2) is the live evidence for the flip.
3. **Δ2 epoch-None handling (DECIDED in D19)**: ADOPT skip-pass with WARNING (NOT `datetime.now(timezone.utc)` aware-datetime fallback, which corrupts the naive-UTC comparison frame on SQLite lexicographic ISO with `+00:00` and errors on PG TIMESTAMP).
4. **Δ7 worktree MANDATE (DECIDED in D20)**: ADOPT dedicated worktree at `../ensemble-src-wt-auto-continue` for implementation + E2E lanes. P0 + P4 T4.7 are the precondition gate; M23 pins it as a pytest-level assertion. **MERGE-CHECKLIST (item 6, r2):** the worktree gate is REMOVED/NEUTRALIZED in the MERGE commit so it does not falsely fail on main/remote VMs post-merge.
5. **Optional `last_heartbeat_at` stamp in the CAS transaction (Δ3 mitigation, D24 SHOULD-conditional)**: DEFERRED in implementation; the leader explicitly marked it as SHOULD-conditional — if the implementation lane verifies the single-statement extension is truly zero-cost on both drivers (SQLite + PG), the stamp may be adopted; if not, it stays DEFERRED. **Escalation flip-condition (5.8, 5.9) is the live trigger** to promote the stamp from SHOULD to MUST.

**Rationale:** every architect-pending decision is now either ADOPTED with explicit guard + escalation, or DEFERRED with explicit trigger. The plan carries no "dangling phrasing" — every ratification is binary and recorded.

**Source:** reviewer item 14 (r2 fold, 2026-10-04) + architecture-recommendation.md "Decisions pending" section.

## Decision-to-AC index (updated)

D1→AC1/AC2 · D2→AC5 · D3→AC4/AC6 · D4→AC4/AC5 · D5→AC5 · D6→AC5 (with D24 explicit-exception for >10-min turns) · D7→AC1 · D8→(gate interaction, no AC) · D9→AC7 (terminal-token trap) · D10→(scalability) · D11→AC1 · D12→AC5/AC6 · D13→AC9 · D14→AC3 · D15→AC7 · D16→(observability) · D17→(perf) · D18→AC4/AC5 (success-path terminalizer — extends AC4/AC5 coverage to the success path; **r3 scope: call-site gate `if task.auto_continued_at is not None:` wrapping `complete_task`**; shared `complete_task` SQL byte-identical to pre-feature) · D19→AC6 (epoch-None skip — extends AC6 never-wedge to the capture-failure case) · D20→AC7 (worktree precondition — gate for all execution-lane work; r2: merge-checklist removal) · D21→AC1/AC5 (selection hardening — strengthens AC1 carve-outs, prevents double-pick; r2: full D27 paragraph on task-level join + enum grounding; r3: D27 prose aligned to live-tree enum, see D27 r3) · D22→AC2/AC6 (stagger + metrics — bounds LLM-stampede cost, observability; r2: 5.9 429 measurement is the flip-condition evidence) · D23→(anchor hygiene — D3/D12/R4/R11 carry the verified numbers; r2: claim-guard span :2230-2294 reconciles to D23's :2230-2310; **r3: D29 captures the `cf95a1a5` anchor-correction table — `:11666-11697` → `:11615-11645`; `:2803` → `:2553`; `:2932` → `:2682`; `:484` → `:410`**) · D24→AC4/AC5 (no-heartbeat window documented; optional heartbeat stamp; escalation flip-condition) · D25→AC4 (arm-notify interaction verified-safe-in-source; r2; **r3: anchors marked verify-at-use, see D25 r3 anchor table below**) · D26→AC5 (at-least-once replay semantics for non-idempotent tool families; T5.4b committed-pair audit is the bounding mechanism; r2) · D27→(D21 explanatory; r2 — task-level join + enum grounding, no new AC; **r3: prose aligned to live-tree 10-value enum per D27 r3**) · D28→(ratification closure; r2 — all Δ1/Δ2/Δ5/Δ7/Δ3 decisions ADOPTED or DEFERRED with explicit trigger; **r3: D29 records the r3 approver rejection of the r2 shared-SQL scope — D1 Δ1 wording updated to call-site gate**) · **D29→(r3 approver-rejection record; new — Δ1 call-site gate ADOPTED over shared-SQL scope)**.
