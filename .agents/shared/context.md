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

**Phase B (Close the Bug Class) — READY**
- Route `watch_job`/`job_continue` through CM
- Add `pending_jobs` to CM
- All 3 repro variants structurally impossible
- Est. 2.5 days

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
