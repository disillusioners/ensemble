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
| `daemon/manager.py` | +1 entry in `_ensure_postgres_columns` (PG column-ensure path, `IF NOT EXISTS`) |
| `daemon/repositories/task/repository.py` | +2 methods: `find_auto_continue_candidates(boot_epoch)`, `mark_task_auto_continued(task_id, boot_epoch)` |
| `daemon/services/auto_continue_boot_pass.py` | NEW — the boot pass (MUST carry `from __future__ import annotations` — venv is CPython 3.13) |
| `daemon/api.py` | +~20 lines at `:1522`: envelope-wrapped boot-pass call + INFO log |
| `tests/unit/services/test_auto_continue_boot_pass.py` | NEW — unit/integration corpus |
| `tests/unit/repositories/test_auto_continue_candidates.py` | NEW — selection/CAS SQL tests |
| `test/packs/auto_continue_boot_pass_unit_test.sh` | NEW — pack wrapper (mirror `post_restart_arm_notify_sweep_unit_test.sh`) |
| `test/packs/auto_continue_interleaving_unit_test.sh` | NEW — AC4 pinning pack wrapper |

No changes to: `instance_messaging.py`, `dependency_bus.py`, `stale_task_recovery.py`, `task_processor.py`, `graph.py`, `upgrade_journal*.py` (consumed read-only).

## Phases

| Phase | Name | Objective | Tasks | Coupling | ACs | Status |
|-------|------|-----------|-------|----------|-----|--------|
| 1 | Schema + CAS column & repository | `task.auto_continued_at` exists on both drivers; selection + CAS repo methods land behind tests | 6 | tight with P2 (repo method signatures are P2's contract) | AC5 | pending |
| 2 | Boot continue-pass service + wiring + kill-switch | The pass exists, is wired at `api.py:1522`, kill-switch works, three-layer never-wedge | 7 | tight with P1 (repo calls), tight with P3 (placement is the ordering mechanism) | AC1, AC2, AC6, AC9 | pending |
| 3 | Coexistence ordering + pinning | AC4 interleaving proven: wake FIFO-behind continued turn; wrong-order lock-out test | 5 | tight with P2 (consumes the real placement + real claim-guard) | AC4, AC1 (PAUSED carve-out) | pending |
| 4 | Tests, packs, gates | Full unit/integration matrix packed; pack SPECS realized as pack files; full-dir gate + cascade e2e green | 6 | loose with P3 (wraps its tests into packs), independent of P5 | AC7 | pending |
| 5 | Demo E2E + evidence | AC8 real-environment proof on demo (port 7979) with captured evidence bundle | 7 | depends on P1-P4 complete; loose coupling (black-box + logs) | AC8, AC3 (E2E leg) | pending |

**AC traceability:** AC1→P2 (T2.2/T2.4, T2.6) + P3 (T3.4); AC2→P2 (T2.1-T2.3 — orchestration-only audit); AC3→decisions.md D8 + P5 (T5.5-T5.6 E2E proof); AC4→P3 (all); AC5→P1 (T1.4/T1.5) + P2 (T2.5 reboot-loop test); AC6→P2 (T2.6 three-layer isolation tests); AC7→P4 (all); AC8→P5 (all); AC9→P2 (T2.7).

## Boot-Ordering Summary (verified anchors @ cf8efbef)

New pass = boot step 27b′, inserted at `daemon/api.py:1522`:

```
24. init_dependency_bus (api.py:1306 → dependency_bus.py:1499)   ← AC3 wakes re-arm
26. job_processor.start (api.py:1371)                            ← claims QUEUED only
27a. reconcile_pending_op (api.py:1510)                          ← journal self-heal
27b. sweep_wake_records (api.py:1521)                            ← pending_wakes delivered (AC4 leg 1)
27b'.★ NEW continue_running_instances_after_restart              ← THIS FEATURE
27c. upgrade_journal_sweep.start() (api.py:1538)                 ← periodic tick
28. HTTP listener up                                             ← pass completes BEFORE first user message
```

Why this slot (each constraint verified in source):
- **After bus (24):** a continued turn may spawn children; the bus must already be tracking.
- **After wake sweep (27b):** the wake's `enqueue_message` lands its PENDING Task row BEFORE the pass schedules the resume → claim-guard (`task.status='running'` orphan) blocks the wake claim deterministically → wake FIFO-behind (AC4). Also matches arm-notify UX: outcome report AFTER the turn finishes.
- **Before periodic start (27c):** the tick sees a consistent post-pass state.
- **Before listener (28):** no user message can race the pass for the same instance's claim-guard; a user message that arrives later simply queues behind per the existing claim path.
- **NOT inside StaleTaskRecovery (5c):** age-gated steady-state backstop; different blast radius (G8).

## Durable-Idempotency Summary (Option a′ — analysis §A refinement)

In-process dedup (`_graph_tasks` `manager.py:506`; `_execution_gate._locks` `execution_gate.py:108-144`) is empty at boot, so a reboot loop is stopped by durable structures only:

1. **Selection** — `find_auto_continue_candidates(boot_epoch)`: `task.status='running' AND task_type IN ('process_message','process_report') AND (auto_continued_at IS NULL OR auto_continued_at < :boot_epoch)` + instance-status exclusion subqueries (PAUSED/terminal/WAITING_CHILDREN excluded). Predicate style mirrors the claim-guard's folded single-statement convention (`repository.py:2230-2294` anti-starvation invariant).
2. **Per candidate, strictly ordered**: `_has_checkpoint(instance_id)` (`instance_messaging.py:1438`) → `_schedule_explicit_handle_resume(silent=True, target_work_id=work_id, handle_work_id=work_id, selected_suspension_reason=None, route_outcome="boot_continue")` (`manager.py:10915`) → ONLY on `{"status": "resuming"}` → CAS stamp `mark_task_auto_continued(task_id, boot_epoch)`: `UPDATE task SET auto_continued_at=:boot_epoch WHERE id=:id AND status='running' AND (auto_continued_at IS NULL OR auto_continued_at < :boot_epoch)`.
3. **A′ ordering rationale**: stamping AFTER the resume actually scheduled means "marked" ≡ "continued" (no marked-but-never-resumed window). The crash gap that remains (die between schedule and stamp) re-schedules on next boot — safe: `astream(None)` on an advanced checkpoint is idempotent at the LangGraph level, and the ExecutionGate per-instance lock serializes.
4. **Backstop**: any miss (no checkpoint, schedule refused, CAS failed) is caught by StaleTaskRecovery at boot+10 min (age-gated force-cancel+retry) — unchanged.
5. **Continue-in-place**: the orphan Task row STAYS `status='running'` — it is the durable proof one driver owns the turn, and it keeps the claim-guard blocking sibling claims (AC4/AC5).

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

Drop the column (DOWN migration + remove the `_ensure_postgres_columns` entry), delete the new module, remove the `api.py:1522` block, unset the env var. 4-file change; no data semantics depend on the column (it is advisory-only bookkeeping); behavior reverts to today's StaleTaskRecovery-only backstop. Kill-switch `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART=0` disables without a code change.
