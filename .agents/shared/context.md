# Project Context — agents-ensemble

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

**Phase C (Single Dispatcher) — PENDING**
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
