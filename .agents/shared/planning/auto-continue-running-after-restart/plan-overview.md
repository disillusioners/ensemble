# Plan Overview: auto-continue RUNNING instances after daemon restart

Date: 2026-10-04T05:00:00Z
Author: planner[v2] via plan-creation worker
Status: Ready for Review
Feature branch: `feature/auto-continue-running-after-restart` @ `cf8efbef`
Inputs: `investigation.md` (wanderer, verified reuse inventory + empirical gap), `technical-analysis.md` (900-line design AUTHORITY — its recommendations are this plan's working decisions)

---

## Objective

After a daemon restart, every instance that was mid-turn in `RUNNING` state automatically continues its interrupted turn from its last LangGraph checkpoint — with zero user action and zero injected text — while `PAUSED` instances stay parked, terminal instances are never touched, `WAITING_CHILDREN` parents remain owned by the existing `DependencyBus` boot re-arm, and a restart storm can never double-continue a turn or wedge the boot.

Testable completion sentence: a RUNNING instance whose daemon dies mid-turn resumes its turn from checkpoint on the next boot with no manual ping, exactly once per boot epoch, with the boot never blocked by its failure — proven by the AC8 demo E2E.

## Scope

### In Scope

- **One new boot pass** (`daemon/services/auto_continue_boot_pass.py`): selection → per-candidate `_has_checkpoint` → `_schedule_explicit_handle_resume(silent=True, route_outcome="boot_continue")` → post-schedule CAS stamp. Orchestration only (AC2).
- **One new nullable column** `task.auto_continued_at TIMESTAMP NULL` + CAS repository method, following the `last_heartbeat_at` precedent exactly (SQLite migration file + PG `_ensure_postgres_columns` entry + SQLModel field).
- **api.py lifespan wiring** at the verified placement: after `sweep_wake_records` (`api.py:1521`), before `upgrade_journal_sweep.start()` (`api.py:1538`), inside a three-layer never-wedge envelope copied verbatim from the wake sweep.
- **Kill-switch** `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART` — env-direct, default ON, `=0` disables, read per boot; mirrors `ENSEMBLE_POST_RESTART_ARM_NOTIFY` (`upgrade_journal.py:886-896`, `upgrade_journal_sweep.py:376-384`).
- **AC4 coexistence pinning**: continue-in-place keeps the orphan Task `status='running'` so the claim-guard (`repository.py:2230-2294`) blocks the pending_wakes claim → wake lands FIFO-behind; pinning test for the exact interleaving + negative lock-out test.
- **AC3 documentation + E2E proof** that the `WAITING_CHILDREN` wake path is bus-owned and restart-durable (no code change to the bus).
- **Tests**: unit + integration per repo conventions, pack SPECs registered in this plan (PACKS.md edit DEFERRED to implementation lane — tester-owned), full-dir gate + cascade e2e per the 2026-10-03 precedent.
- **AC8 demo E2E** (user-mandated, REAL environment): leader-dev workflow with long-sleep+say-hello child task, mid-flight demo-daemon restart (port 7979, `~/agents-ensemble-demo/`, NON-live, user-sanctioned), evidence capture.

### Out of Scope

- **WAITING_CHILDREN re-drive logic** — `DependencyBus.start()` (`dependency_bus.py:1499-1560`) already owns it (`_warm_cache` → `_recover_fired_unsent` with `enqueued_at IS NULL` C1 dedup → `_sweep_orphan_watchers`). Adding a pass-side WC handler would race the bus. (AC3 = skip + prove.)
- **RAM-only `_parent_error_message` defect** (`dependency_bus.py:466-472`) — pre-existing, documented "known limitation"; E2E happy path NOT touched by it. Follow-up ticket, not this feature.
- **StaleTaskRecovery changes** — it stays as the age-gated (boot+10 min, `max_retries=3`) steady-state backstop. No threshold flip, no amnesty removal, no code change. Orthogonal triggers + mechanisms (G8).
- **`idle`/`queued` instance states** — they have no interrupted in-flight turn; strict `status='running'` task predicate (G3).
- **Terminal-instance revival/reaping** — a RUNNING task whose instance row is terminal is SKIPPED (no revive, no reap; StaleTaskRecovery backstop owns it) (G7).
- **Retry-budget consumption** — the pass never increments `task.retry_count` (G2).
- **PACKS.md edit** — tester-owned; this plan registers pack SPECS only.
- **Batched/parallel checkpoint probes** (`abulk_get`-style) — v2 optimization; v1 is sequential (G6).
- **`reasoning`/`attestation` gate changes** — none needed; verified the silent-resume path cannot reset attestation state (G4, see decisions.md).

### Modules / files touched (complete list)

| File | Nature of change |
|---|---|
| `daemon/repositories/task/models.py` | +1 nullable `datetime` field `auto_continued_at` |
| `daemon/migrations/versions/20261004_000001_add_task_auto_continued_at.sql` | NEW — SQLite migration (template: `20260606_000001_add_task_last_heartbeat_at.sql`) |
| `daemon/manager.py` | +1 entry in `_ensure_postgres_columns` (PG column-ensure path, `IF NOT EXISTS`) — existing. **(Δ1 / D18) NEW:** surgical terminalizer call in `_resume_processing_background`'s success branch (verified anchor: `:11666-11697`, the orphan-success-branch docstring); calls `complete_task` by work_id via a new helper (`find_task_id_by_work_id` or extended `find_paused_or_cancellable_turn`); guarded by `WHERE status='running'` (`repository.py:2803`) — no-op for cascade/worker shapes |
| `daemon/repositories/task/repository.py` | +2 methods: `find_auto_continue_candidates(boot_epoch)` (Δ4 / D21: full instance-status exclusion set + `cancel_requested=False`), `mark_task_auto_continued(task_id, boot_epoch)` |
| `daemon/services/auto_continue_boot_pass.py` | NEW — the boot pass (MUST carry `from __future__ import annotations` — venv is CPython 3.13). **(Δ2 / D19)** epoch-None SKIP + WARNING branch. **(Δ4 / D21)** >1-candidate log-skip defense. **(Δ5 / D22)** 5-resumes / 2s stagger + counter/histogram metrics. **(Δ1 / D18)** module docstring documents the guard-release mechanism (fail_task for failure, terminalizer for success) and the no-heartbeat window (D24). **(Δ7 / D20)** worktree gate is upstream (P0), NOT in this file |
| `daemon/api.py` | +~20 lines at `:1522`: envelope-wrapped boot-pass call + INFO log |
| `tests/unit/services/test_auto_continue_boot_pass.py` | NEW — unit/integration corpus (M18 terminalizer, M19 epoch-None skip, M21 >1-candidate log-skip, M22 stagger+metrics) |
| `tests/unit/repositories/test_auto_continue_candidates.py` | NEW — selection/CAS SQL tests (now with explicit `cancel_requested=False` + full exclusion set per D21) |
| `tests/unit/services/test_auto_continue_interleaving.py` | NEW — 6-row interleaving matrix (architecture-recommendation.md Focus 3, cited by row); Δ1 complement test in 3.3 |
| `test/packs/auto_continue_boot_pass_unit_test.sh` | NEW — pack wrapper (mirror `post_restart_arm_notify_sweep_unit_test.sh`) |
| `test/packs/auto_continue_interleaving_unit_test.sh` | NEW — AC4 pinning pack wrapper |
| `tests/conftest_worktree.py` (or extension to existing conftest) | NEW (Δ7 / D20) — pytest fixture that fails LOUDLY if `import daemon` resolves outside the worktree |
| `.agents/shared/planning/auto-continue-running-after-restart/worktree-claim.txt` | NEW (Δ7 / D20) — worktree path + branch receipt from Phase 0 task 0.4 |

No changes to: `instance_messaging.py`, `dependency_bus.py` (start `:1499`; helpers `:1804/:1839/:1896` per D23), `stale_task_recovery.py`, `task_processor.py`, `graph.py`, `upgrade_journal*.py` (consumed read-only). **`manager.py`'s resume internals** were originally listed here; **D18 amends** that — the surgical terminalizer touch is load-bearing for AC4 success-path coverage (D18 / R19).

## Phases

| Phase | Name | Objective | Tasks | Coupling | ACs | Status |
|-------|------|-----------|-------|----------|-----|--------|
| **0** | **Dedicated worktree + fresh uv venv + import-resolution gate (Δ7 / D20)** | Implementation + E2E lanes run in `../ensemble-src-wt-auto-continue` with `import daemon` resolving INSIDE the worktree | 4 | Precondition for P1-P5; independent of code | AC7 (gate), AC9 (lane discipline) | pending |
| 1 | Schema + CAS column & repository | `task.auto_continued_at` exists on both drivers; selection (Δ4 hardened) + CAS repo methods land behind tests | 7 (was 6; +1.7 Δ4 hardening) | tight with P2 (repo method signatures are P2's contract) | AC5, AC1 (Δ4 full exclusion + `cancel_requested`) | pending |
| 2 | Boot continue-pass service + wiring + kill-switch | The pass exists, is wired at `api.py:1522`, kill-switch works, three-layer never-wedge, **(Δ1)** `manager.py:11666-11697` surgical terminalizer, **(Δ2)** epoch-None SKIP + WARNING, **(Δ5)** 5/2s stagger + counter/histogram metrics | 8 (was 7; +2.8 terminalizer; Δ2/Δ5 fold into existing 2.2/2.3) | tight with P1 (repo calls), tight with P3 (placement), **NEW: tight with `manager.py` resume internals (D18 surgical touch)** | AC1, AC2, AC4 (Δ1 extends), AC6 (Δ2 extends), AC9, AC5 | pending |
| 3 | Coexistence ordering + pinning | AC4 **6-row interleaving matrix** pinned (rows 1-6 per architecture-recommendation.md Focus 3): WS→CP, CP→late-WS, WS→CP→fail, WS→CP→**success** (Δ1 row), STR mid-flight (Δ3 doc row), epoch-None (Δ2 row); wrong-order lock-out test; Δ1 complement regression test | 5 | tight with P2 (consumes the real placement + real claim-guard + Δ1 terminalizer) | AC4, AC1 (PAUSED carve-out + `cancel_requested` per Δ4), AC5 (Δ1 complement), AC6 (Δ2 row 6) | pending |
| 4 | Tests, packs, gates | Full unit/integration matrix packed; pack SPECS realized as pack files; full-dir gate + cascade e2e per the 2026-10-03 precedent; **(Δ7)** worktree-gate fixture (M23) | 7 (was 6; +4.7 worktree-gate assertion) | loose with P3 (wraps its tests into packs), independent of P5, **precondition from P0** | AC7 | pending |
| 5 | Demo E2E + evidence | AC8 real-environment proof on demo (port 7979) with captured evidence bundle; **(Δ7)** deploy from worktree; **(Δ3)** escalation observation (5.8) | 8 (was 7; +5.8 escalation flip) | depends on P0-P4 complete; loose coupling (black-box + logs) | AC8, AC3 (E2E leg), AC7 (worktree-gate re-verification) | pending |

**AC traceability (Δ-applied):** AC1→P1 (T1.4 Δ4 hardened predicate) + P2 (T2.2/T2.4, T2.6) + P3 (T3.4 carve-outs incl. `cancel_requested`); AC2→P2 (T2.1-T2.3 orchestration-only audit + structural grep-proof); AC3→decisions.md D8 + P5 (T5.5-T5.6 E2E proof); AC4→P3 (T3.2 6-row matrix — row 4 IS the Δ1 success path) + P2 (T2.6 placement) + P2 (T2.8 Δ1 terminalizer); **AC4 extension (Δ1):** the success path is now covered by T3.2 row 4 + T2.8 terminalizer + T3.3 complement regression test; AC5→P1 (T1.4/T1.5/T1.6/T1.7) + P2 (T2.5 reboot-loop test) + P3 (T3.2 row 1 no-double-delivery + T3.3 Δ1 complement); AC6→P2 (T2.6 three-layer isolation tests + T2.2 Δ2 epoch-None SKIP) + P3 (T3.2 row 6); AC7→P4 (all) + P0 (Δ7 worktree gate, precondition row) + P5 (5.1 worktree-gate re-verification); AC8→P5 (all); AC9→P2 (T2.7).

## Boot-Ordering Summary (verified anchors @ ce148ad2)

New pass = boot step 27b′, inserted at `daemon/api.py:1522`:

```
24. init_dependency_bus (api.py:1306 → dependency_bus.py:1499)   ← AC3 wakes re-arm (helpers: _warm_cache :1804 / _recover_fired_unsent :1839 / _sweep_orphan_watchers :1896, D23)
26. job_processor.start (api.py:1371)                            ← claims QUEUED only
27a. reconcile_pending_op (api.py:1510)                          ← journal self-heal
27b. sweep_wake_records (api.py:1521)                            ← pending_wakes delivered (AC4 leg 1)
27b'.★ NEW continue_running_instances_after_restart              ← THIS FEATURE
27c. upgrade_journal_sweep.start() (api.py:1538)                 ← periodic tick
28. HTTP listener up                                             ← pass completes BEFORE first user message
```

Why this slot (each constraint verified in source):
- **After bus (24):** a continued turn may spawn children; the bus must already be tracking. Bus helpers re-pinned to `_warm_cache :1804` / `_recover_fired_unsent :1839` / `_sweep_orphan_watchers :1896` per D23.
- **After wake sweep (27b):** the wake's `enqueue_message` lands its PENDING Task row BEFORE the pass schedules the resume → claim-guard (verified `repository.py:2230-2310` per D23) blocks the wake claim deterministically → wake FIFO-behind (AC4). Also matches arm-notify UX: outcome report AFTER the turn finishes.
- **Before periodic start (27c):** the tick sees a consistent post-pass state.
- **Before listener (28):** no user message can race the pass for the same instance's claim-guard; a user message that arrives later simply queues behind per the existing claim path.
- **StaleTaskRecovery thread (STR):** starts at `pool_orchestrator.py:264-294` (D23-verified), the EARLIEST boot subsystem — NOT a separate ordering constraint. The structural guard is the `boot_epoch` amnesty (`repository.py:3161, :4501`, D23-verified). While the epoch holds, the selection→schedule window has zero STR interference; after the threshold, a continued turn is either terminal (invisible to STR's `status='running'` predicate) or heartbeating (alive) — the miss case is exactly STR's designed job (retry task = checkpoint continuation). **(Δ2 / D19)** on `boot_epoch=None` the pass SKIPS — STR's amnesty clamp vanishes but the pass never started scheduling, so there is no orphan resume to be reaped mid-pass (R22). **(Δ3 / D24)** continued turns are STR-reapable at boot+10 min because the resume path writes no heartbeats; reap = checkpoint retry (idempotent), `retry_count` burn = explicit D6 exception.

## Durable-Idempotency Summary (Option a′ — analysis §A refinement + Δ-applied)

In-process dedup (`_graph_tasks` `manager.py:506`; `_execution_gate._locks` `execution_gate.py:108-144`) is empty at boot, so a reboot loop is stopped by durable structures only:

1. **Selection** — `find_auto_continue_candidates(boot_epoch)` (Δ4 / D21 hardened): `task.status='running'` AND `task.task_type IN ('process_message','process_report')` AND `task.cancel_requested = :cancel_requested_false` AND `(auto_continued_at IS NULL OR auto_continued_at < :boot_epoch)` + instance NOT IN ('paused','terminated','completed','error','failed','waiting_children') (explicit full set, not "PAUSED/terminal/WC"). Predicate style mirrors the claim-guard's folded single-statement convention (`repository.py:2230-2310` anti-starvation invariant, D23-verified). **>1-candidate log-skip** (D21): enforced in the boot service (P2 T2.3), NOT in the repo method — pure-read preservation.
2. **Per candidate, strictly ordered**: `_has_checkpoint(instance_id)` (`instance_messaging.py:1438`) → `_schedule_explicit_handle_resume(silent=True, target_work_id=work_id, handle_work_id=work_id, selected_suspension_reason=None, route_outcome="boot_continue")` (`manager.py:10915`, D23-verified) → ONLY on `{"status": "resuming"}` → CAS stamp `mark_task_auto_continued(task_id, boot_epoch)`: `UPDATE task SET auto_continued_at=:boot_epoch WHERE id=:task_id AND status='running' AND (auto_continued_at IS NULL OR auto_continued_at < :boot_epoch)`.
3. **A′ ordering rationale**: stamping AFTER the resume actually scheduled means "marked" ≡ "continued" (no marked-but-never-resumed window). The crash gap that remains (die between schedule and stamp) re-schedules on next boot — safe: `astream(None)` on an advanced checkpoint is idempotent at the LangGraph level, and the ExecutionGate per-instance lock serializes.
4. **(Δ2 / D19)** on `boot_epoch=None`: pass SKIPS with WARNING, no DB writes, no scheduling — kills the STR mid-pass reap race (R22); STR's normal age-gated backstop continues to own the orphan.
5. **(Δ1 / D18)** success-path terminalizer: in `_resume_processing_background`'s success branch (`manager.py:11666-11697`, D23-verified), after `_process_resume_finalize`, call `complete_task` by work_id → `WHERE status='running'` guard (`repository.py:2803`, D23-verified) flips the orphan to COMPLETED → claim-guard opens → pending wake claims FIFO immediately (no STR reap delay on the success path). The guard makes this a **no-op for cascade/worker shapes** (cascade rows are PENDING by then; worker shapes never reach this branch directly).
6. **(Δ5 / D22)** stagger 5/2s + pass metrics (counter `auto_continue_boot_pass_resumes_total` + histogram `auto_continue_boot_pass_duration_seconds`) bounds the LLM-stampede cost at observed N=33 (~14 s total).
7. **Backstop**: any miss (no checkpoint, schedule refused, CAS failed, STR mid-flight reap on a >10-min turn) is caught by StaleTaskRecovery at boot+10 min (age-gated force-cancel+retry) — unchanged. Continued turns >10 min are STR-reapable (R20 / D24); reap = checkpoint retry, `retry_count` burn = explicit D6 exception.
8. **Continue-in-place**: the orphan Task row STAYS `status='running'` — it is the durable proof one driver owns the turn, and it keeps the claim-guard blocking sibling claims (AC4/AC5).

## Research Insights (shaped this plan)

- **The gap is real and measured**: 33 `is alive (running)` + 171 `is alive (waiting_children)` recovery lines, ZERO auto-resumes across Sep 27→Oct 4 LIVE logs; instance `5b4c47a4` frozen through 32 consecutive restarts, f1-DEAD'd, manually deleted. `JobRecoveryService` alive-branch logs *"leave as PROCESSING, the observer will resume pickup"* (`job_recovery_service.py:683-689`) but the observer is event-driven and a dead process emits no events.
- **All primitives exist**: `find_paused_or_cancellable_turn` (`repository.py:743`), `_has_checkpoint` (`instance_messaging.py:1438`), `_schedule_explicit_handle_resume` (`manager.py:10915`), pure checkpoint resume `graph_input=None → astream(None)` (`instance_messaging.py:4267-4269`).
- **Double-fire with pending_wakes is NOT real today** — the claim-guard's per-instance `NOT IN (SELECT … status='running')` blocks a sibling claim; it becomes real ONLY if a pass terminalizes the orphan early (Interleaving Y, rejected).
- **G4 resolved during planning** (attestation hop): silent checkpoint resume cannot reset `attestation_denied_count`/`completion_gate_escalated` — the only reset site (`instance_messaging.py:1977-2015`) is keyed on revive-from-terminal via a NEW top-level HUMAN message (`enqueue_message` path), which the boot pass never takes; the attestation gate itself is a graph node (`graph.py:5338`) that re-evaluates normally inside the resumed turn.
- **Migration template exists**: `20260606_000001_add_task_last_heartbeat_at.sql` (SQLite) + `_ensure_postgres_columns` (`manager.py:5834`) — the exact dual-driver pattern for `auto_continued_at`.
- **Repo traps carried to implementation lane**: CPython 3.13 venv (new modules need `from __future__ import annotations`; `api.py` and `manager.py` currently LACK it), multi-edit read-back verification, execution-lane citation drift (grep-verify at use time; corrected anchors: `task_processor.py:267`, `message_processing_pipeline.py` replaced deleted `message_job_handler.py`, `claim_pending_task` at `task/repository.py:1733+` with guard at `:2230-2294`), PAUSED = `resume_instance_cascade` lane (never touched), terminal-token contract (observer accepts only `completed/error/failed` — this feature introduces NO new terminal token).

## Open Questions

None that block implementation. The analysis's G1-G8 are all adjudicated as working decisions (decisions.md D5-D12). Two bounded items tracked as follow-ups, NOT scope:

1. **`_parent_error_message` RAM-only defect** (`dependency_bus.py:466-472`) — parent may finalize COMPLETED instead of ERROR if the daemon dies in the narrow child-error→finalize window. Pre-existing, documented as accepted limitation; suggest follow-up ticket (persist to `dependency_watchers.error_message` or onto the MessageQueue row at delivery).
2. **Sequential `_has_checkpoint` latency at N>100 instances** — wall-clock ~50-200 ms per probe; fine for observed N≤33; batched probe is a v2 optimization (G6).

## Reversibility

Drop the column (DOWN migration + remove the `_ensure_postgres_columns` entry), delete the new module, remove the `api.py:1522` block, **revert the Δ1 surgical touch in `manager.py:11666-11697`**, unset the env var. The worktree (`../ensemble-src-wt-auto-continue`) is removable via `git worktree remove`; the `worktree-claim.txt` is removable by deletion. 5-file code change plus planning artifacts; no data semantics depend on the column (it is advisory-only bookkeeping); behavior reverts to today's StaleTaskRecovery-only backstop. **With Δ1 reverted** the orphan success-path returns to its pre-D18 "stranded at success-path" behavior (R19) — STR reaps at boot+10 min instead of immediate FIFO wake. Kill-switch `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART=0` disables without a code change.

## Plan Revisions (Δ-applied, additive)

This plan was REVISED at 2026-10-04 to fold the architect's validated deltas (architecture-recommendation.md @ `ce148ad2`) into the existing implementation plan, per leader ratifications:

- **Δ1 — Success-path orphan terminalizer (🔴)**: surgical `complete_task` in `_resume_processing_background`'s success branch (`manager.py:11666-11697`); `WHERE status='running'` guard makes it a no-op for cascade/worker shapes. Phase 2 task 2.8 + Phase 3 task 3.2 row 4 + Phase 3 task 3.3 complement regression test. Decisions D18; risk R19.
- **Δ2 — `boot_epoch=None` SKIP-pass (🔴)**: on capture failure, the pass SKIPS with WARNING (no fallback to aware-datetime — frame mismatch). Phase 2 task 2.2 step (2). Decisions D19; risk R22.
- **Δ3 — No-heartbeat window documented + optional `last_heartbeat_at` stamp (🟡, SHOULD-DEFERRED)**: module docstring + AC4 narrative rewrite; escalation flip-condition observed in Phase 5 task 5.8. Decisions D24; risk R20.
- **Δ4 — Selection hardening (🟡)**: full instance-status exclusion set + `cancel_requested=False`; >1-candidate log-skip in service. Phase 1 task 1.4 + 1.7; Phase 2 task 2.3. Decisions D21.
- **Δ5 — Stagger 5/2s + pass metrics (🟡)**: per-candidate scheduling cadence + counter/histogram. Phase 2 task 2.3. Decisions D22; risk R23.
- **Δ6 — Anchor re-pins (🟡)**: every execution-lane citation in the plan re-verified at `ce148ad2`; D23 is the verification snapshot. Decisions D23.
- **Δ7 — Dedicated worktree MANDATE (🔴)**: implementation + E2E lanes run in `../ensemble-src-wt-auto-continue` with fresh uv venv + import-resolution gate. Phase 0 (NEW, precondition); Phase 4 task 4.7 worktree-gate assertion. Decisions D20; risk R21.

Existing D1-D17, R1-R18, and AC traceability are unchanged; D18-D24 and R19-R23 are addenda (ADR/D numbering stable).
