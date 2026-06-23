# Phase 3 Cleanup — USE_LEGACY_WAITING_FOR_CASCADE Removal

## Context
Phase 3 of `feature/cleanup-old-architecture` removed the `USE_LEGACY_WAITING_FOR_CASCADE` kill-switch flag and all ~27 gated code paths. CM (CorrelationManager) became the SOLE completion authority.

## Key Pattern: CM Mock Wiring for Tests

When tests exercise code paths that call `get_correlation_manager()`, they MUST wire a CM mock. The pattern:

```python
from daemon.services.correlation_manager import set_correlation_manager

_CM_PENDING = [0]

@pytest.fixture(autouse=True)
def _wire_cm_mock():
    cm_mock = MagicMock()
    cm_mock.get_pending_count = lambda iid: _CM_PENDING[0]
    cm_mock.is_complete = lambda iid: _CM_PENDING[0] == 0
    set_correlation_manager(cm_mock)
    yield
    set_correlation_manager(None)
    _CM_PENDING[0] = 0

def set_cm_pending(n: int) -> None:
    _CM_PENDING[0] = n
```

Tests then call `set_cm_pending(n)` before exercising the code path under test.

## RuntimeError Re-raise (A8/A9 Invariants)

Phase 3 code raises `RuntimeError` when CM is None at:
- `JobProcessor._emit_in_progress_if_children_pending` (A9)
- `ChildReportsService._update_parent_on_child_complete` (A8)
- `_finalize_job_db_sync` (CM check)

The `except RuntimeError: raise` block in `_finalize_job` re-raises to prevent W3 fail-safe from silently converting misconfiguration into per-job FAILED.

**Test fix**: Use `OSError` or other non-RuntimeError exception to test the W3 fail-safe path.

## Files Fixed (8 test files, 7 commits)

1. `test_in_progress_guard.py` — Added autouse CM mock fixture
2. `test_finalize_job_h15.py` — Added autouse CM mock + W3 RuntimeError→OSError
3. `test_cascade_integration.py` — Deleted CM=None legacy tests, rewrote 1 hook test
4. `test_l14_resume_from_child` — Updated waiting_for assertion (preserved=0)
5. `test_pause_instance_cascade.py` — Updated waiting_for assertions
6. `test_deadlock_fix.py` — Removed CM=None legacy fallback tests
7. `test_phase4_deprecation.py` — Removed CM=None legacy fallback tests
8. `test_config.py` — Fixed max_instance_history assertion (300→500)

## Verification Commands

```bash
# PostgreSQL tests (CRITICAL)
.venv/bin/python -m pytest tests/postgres/ -v --override-ini="addopts=" -m postgres

# All key unit tests
.venv/bin/python -m pytest tests/test_dependency_bus.py tests/test_correlation_manager.py \
  tests/unit/test_tree_aware_pause_resume.py tests/unit/test_paused_instance_ttl.py \
  tests/test_resume_gate.py -v --tb=short

# E2E workflows (requires running daemon)
.venv/bin/python -m pytest tests/e2e/test_e2e_workflows.py -v --override-ini="addopts=" -m integration
```
