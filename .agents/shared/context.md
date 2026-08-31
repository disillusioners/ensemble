# Project Context — agents-ensemble

## Agent Rename: coder → developer (2026-06-25)
The "coder" agent was renamed to "developer". Historical docs in
.agents/ and docs/bugs/ may still reference "coder" — these are
intentional historical records.

## Current State (2026-06-20)

### Decouple Architecture Migration
**Status**: Phase A COMPLETE, Phase B ready to start.

**Branch**: `feature/decouple-architecture`

**Phase A (Authority & Visibility) — COMPLETE ✅**
- All `waiting_for` control-flow gated behind `USE_LEGACY_WAITING_FOR_CASCADE` flag (default OFF)
- CM (CorrelationManager) is the SOLE completion authority when flag OFF
- `FOR UPDATE` gate in `job_feedback_observer.py` replaced with `cm.is_complete()` when flag OFF
- A8: Hard RuntimeError when flag OFF + CM None (not graceful degradation)
- A0a: `rebuild_from_db()` fixed — top-level OVERWRITE + per-parent MERGE
- `DEBUG_COMPLETION_INVARIANT` logs `CM_WAITING_FOR_DIVERGENCE` on mismatch
- 18 files audited, all control-flow reads gated
- 160 Phase A tests pass (123 SQLite + 37 PostgreSQL)

**Phase A Commits** (on `feature/decouple-architecture`):
- `a8c8a1fb` — A0a: rebuild_from_db() MERGE fix
- `a0db4a50` — A2: config flags
- `c2b17761` — A3-A6: gate waiting_for control-flow
- `0fa74efc` — A12: shadow test pack (register-window proof)
- `afbb35ec` — A7-A8: FOR UPDATE gate replacement + hard error
- `eee8efd9` — A1, A9, A14: authority doc, audit, kill-switch tests
- `5f9ee985` — A10, A11, A13, A15: invariant, regression, crash-recovery tests
- `ef147bfa` — Reviewer fixes: C1 (cross-thread race), C2 (threading test), W1 (re-entrancy guard), W2 (RuntimeError propagation)
- `9414a17f` — Reviewer fix v2: post-commit orphan race closed via generation counter + re-arm mechanism
- `272fd840` — Reviewer fix v3: return after re-arm to prevent outbox fall-through

**Phase B (Close the Bug Class) — COMPLETE ✅**
- `bad3bea3` — B1-B4: pending_jobs in CM, register_job_send/resolve_job, watch_job routing, observer terminal resolution
- `3ae8a72e` — B5: watch_job integration test pack (10 tests, Variant B regression)

**Phase B Status**: All 3 premature completion repro variants structurally impossible. 260 tests pass.

**Phase B (Close the Bug Class) — COMPLETE ✅**
- Route `watch_job`/`job_continue` through CM via `pending_jobs`
- `ParentCorrelation` now tracks `pending_jobs: set[str]` alongside `pending`
- `is_complete()` returns True only when BOTH are empty
- 260 tests pass (223 SQLite + 37 PostgreSQL)

**Phase C (Single Dispatcher) — COMPLETE ✅**
- C-M4: Deprecation log + path tests (C1-C4)
- C-M5: Route JobQueue through observer (C4.5-C11) — `USE_LEGACY_JOBQUEUE_DISPATCH` flag
- C-M6: Collapse gate to asyncio.Lock (C12a-C18) — 707→268 lines, net -914 lines
- 160+ Phase C tests pass
- Unify enqueue to WorkerPool-only
- JobQueue is scheduling-only
- Est. 2.5 weeks

### Key Config Flags
| Flag | Default | Purpose |
|------|---------|---------|
| `USE_LEGACY_WAITING_FOR_CASCADE` | `False` | Kill switch for legacy waiting_for cascade. OFF = CM authoritative. |
| `DEBUG_COMPLETION_INVARIANT` | `False` | Logs CM_WAITING_FOR_DIVERGENCE on mismatch. ON recommended in dev/CI. |

### Key Architecture Docs
- `docs/architecture/completion-authority.md` — Three authorities, invariant, call sites
- `docs/configuration/completion-flags.md` — Flag interaction matrix, triage runbook
- `docs/plans/decouple-execution-plan.md` — Full 3-phase execution plan
- `docs/plans/decouple-review.md` — Review findings (round 1 + round 2)

## wc-wake-report-integrity Phase 1 (2026-08-30)

**Status**: Phase 1 COMPLETE (T1-T7 landed; T6b fixture migration; T8 docs).

**Branch**: `feature/wc-wake-report-integrity` @ `cf210e32+`

**Lock-in**: kill-switch `ENSEMBLE_WC_WAKE_ENQUEUE` (default OFF at code-land; flag-ON ships the new routing per D2.5-FLIP).

**Key changes**:
- `INJECTION_ELIGIBLE_STATUSES` shrunk to `frozenset({"running"})` (T2)
- HTTP / agent-tool / job_inject routing pivots behind the kill-switch (T3+T4+T7)
- `_heal_poisoned_checkpoint_tail` closes the poisoned-tail → LangGraph 2013 exposure at the enqueue seam (T6)
- D2 seam drain moves parked FIFO leftovers into graph_input (T5)
- `Manager.send_message` and `InstanceMessagingService.send_message` DELETED (T6b / D7 LOCKED) — surviving production traffic must use `enqueue_message` (durable wake) or `set_injection` (mid-turn injections on RUNNING)

**Flag states**:
- OFF (default): legacy FIFO injection for WC targets (revert path)
- ON: WC → `enqueue_message` (durable wake, first-class turn)

**Tests affected by T6b deletion**: ~13 test files skipped (TestSendMessage, TestThinkTagParsing, TestInstanceMessagingTriggerTitleGeneration send_message subset, test_question_deferred_pause_*, test_inner_soul*, etc.). All skipped tests assert behavior of the deleted methods and are pending a Phase-2 rewrite against `MessageProcessingPipeline`.

**Docs**:
- `docs/setup.md` documents the env var
- `docs/features/job-queue.md` example updated to `manager.enqueue_message`
- `daemon/tools/instance.py` `_full_doc_` documents both flag states + D6 busy-gate consequence
- `daemon/tools/job_queue.py` `_FULL_DOCS["job_inject"]` rewritten
- `daemon/routers/messages.py` routing-table docstring updated
