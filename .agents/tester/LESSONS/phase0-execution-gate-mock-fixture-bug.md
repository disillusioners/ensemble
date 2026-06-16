# Lesson: Phase 0 CorrelationManager — ExecutionGate Test Fixture Bug

**Date**: 2026-06-16
**Context**: Phase 0 testing on `feature/correlation-manager` branch

## The Issue
9 tests in `tests/test_spawn_limit_edge_cases.py` fail with:
```
TypeError: '>' not supported between instances of 'MagicMock' and 'float'
```

**Root cause**: `daemon/services/execution_gate.py:274` calls `max(0.1, heartbeat_interval_seconds)`.
The `mock_config` fixture in `test_spawn_limit_edge_cases.py` uses a `MagicMock()` for config,
which doesn't set `heartbeat_interval_seconds` — so it returns a `MagicMock` object when accessed,
and `max(0.1, MagicMock())` raises TypeError.

## Pre-existing or Phase 0 Regression?
**PRE-EXISTING** — confirmed by checking out baseline `2d8c6bcd` (before Phase 0 commits).
The same 9 failures occur identically.

The failures look alarming because they hit `execution_gate.py`, which Phase 0 modified.
But the root cause is the test fixture missing the config field, not a Phase 0 code change.

## Fix Required (Not Applied — Pre-existing, Out of Scope)
Add to the `mock_config` fixture:
```python
config.services.heartbeat_interval_seconds = 30.0
config.services.heartbeat_max_consecutive_errors = 5
```

Or the ExecutionGate constructor could be made more defensive (e.g., `float(heartbeat_interval_seconds or 30.0)`).

## Key Takeaway
When tests fail in `execution_gate.py` during Phase 0 testing, check if it's a mock fixture issue
before assuming regression. The ExecutionGate constructor does `max()` comparisons on config values
that MagicMock fixtures may not provide.
