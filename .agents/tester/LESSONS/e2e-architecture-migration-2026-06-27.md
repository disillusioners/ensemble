# E2E Architecture Migration — Findings & Quick Fixes

**Date**: 2026-06-27
**Branch**: `feature/migration-followups`
**Session**: `e2e-architecture-validation`

## PostgreSQL Compatibility Bugs Found (Quick-Fixed)

### 1. Row/tuple adapt error — `.scalars()` missing
- **File**: `daemon/services/instance_lifecycle.py:757`
- **Bug**: `select(SomeModel.col).where(...).all()` returns Row objects on PostgreSQL, not plain values. Passing these to subsequent queries causes `psycopg.ProgrammingError: cannot adapt type 'Row'`.
- **Fix**: Always use `.scalars().all()` for single-column selects.
- **Commit**: `0917449b`
- **PATTERN TO REMEMBER**: When selecting a single column, ALWAYS use `.scalars()`. SQLite is lenient (returns plain values), PostgreSQL returns Row objects.
- **Known remaining instance**: `daemon/repositories/instance/repository.py:265` — same pattern, not blocking but should be fixed.

### 2. AmbiguousParameter — shared param across VARCHAR and TIMESTAMP columns
- **File**: `daemon/services/instance_lifecycle.py:2457`
- **Bug**: One `now_iso` parameter bound to both `cancel_requested_at` (VARCHAR) and `completed_at` (TIMESTAMP). PostgreSQL can't deduce type. SQLite is lenient.
- **Fix**: Separate params for each column type — `now_iso` (string) for VARCHAR, `now_dt` (datetime) for TIMESTAMP.
- **Commit**: `036d09b7`
- **PATTERN TO REMEMBER**: Never reuse a single parameter for columns of different SQL types. PostgreSQL infers type from the first usage and rejects mismatches.

## Architectural Regression (NOT Quick-Fixed)

### Pause→Resume→Final-Response chain broken
- **Test**: `test_pause_after_spawn_then_resume`
- **Symptom**: Leader stuck at `waiting_children` status after resume + child completion
- **Root cause**: DependencyBus terminal hook marks parent's pending message as completed without re-queuing parent for final LLM turn
- **Old behavior**: CorrelationManager re-queued parent for final response after child completion
- **New behavior**: DependencyBus appears to skip this re-queue path
- **Scope**: Architectural fix needed (~investigation + multi-line change)
- **Impact**: Only affects pause→resume→child-completion flow. Happy path, terminate, and wave-spawn all work correctly.

## Environment Gotchas

### SSL_CERT_FILE pollution
- Prior sessions left stale `SSL_CERT_FILE` pointing to non-existent PyInstaller temp dir
- Fix: `SSL_CERT_FILE=$(.venv/bin/python -c 'import certifi; print(certifi.where())')`

### uvicorn --reload kills E2E tests
- `./dev.sh` runs uvicorn with `--reload`, which restarts the daemon on ANY file change
- During E2E tests, 13 reloads happened, orphaning in-flight leaders
- Fix: Run daemon without `--reload` for E2E tests
