# Lesson: pytest-timeout Plugin Missing from Venv

**Date**: 2026-07-25
**Discovered during**: Governor Council-Manager independent verification
**Severity**: Low (pre-existing environment issue, NOT a regression)
**Affects**: ALL pytest packs in the project (not feature-specific)

## Problem
The `pytest-timeout` plugin is declared as a dependency in `pyproject.toml` but is NOT installed
in the active `.venv`:

- `pyproject.toml:43` — `"pytest-timeout>=2.3"` (declared dependency)
- `pyproject.toml:71-72` — `timeout = 30`, `timeout_method = "thread"` (ini config)
- `.venv/bin/python -c "import pytest_timeout"` → `ModuleNotFoundError`

## Symptoms
1. Any pytest invocation with `--timeout=N` fails with exit code 4
   (`unrecognized arguments: --timeout=N`).
2. pytest emits `PytestConfigWarning: Unknown config option: timeout` and `timeout_method`
   warnings on every run.
3. The dual-layer timeout contract (Layer 2 = script-internal per-test timeout) cannot be
   applied — only Layer 1 (command-level `timeout 300`) works.

## Impact on Governor Verification
- 4 packs run; all completed in <2s each (well within budget).
- Layer 1 (`timeout 300`) alone was sufficient — no practical impact on this verification.
- But the dual-layer contract was technically violated on all 4 packs.

## Root Cause
The venv was likely built without the dev/test dependencies fully installed, OR `pytest-timeout`
was removed/never installed despite the `pyproject.toml` declaration. The config keys
(`timeout`, `timeout_method`) remain in `pyproject.toml`, producing the warnings.

## Resolution Options
1. **Install the plugin** (recommended): `.venv/bin/pip install pytest-timeout`
   - Restores Layer 2 dual-layer timeout across all packs.
   - Silences the `PytestConfigWarning`s.
   - Makes the `timeout = 30` ini config effective (30s per-test default).
2. **Remove the stale config** (if plugin is intentionally absent):
   - Remove `pytest-timeout>=2.3` from `pyproject.toml` dependencies.
   - Remove `timeout`/`timeout_method` from `[tool.pytest.ini_options]`.
   - Test packs must then rely on Layer 1 (`timeout 300`) only — acceptable for fast unit packs,
     riskier for integration/E2E.

## Tester Action
- Flagged in governor verification report (RESULTS/2026-07-25-governor-verification.md).
- Reported to leader/user as a pre-existing environment issue (not a governor regression).
- No test-code change made (out of scope for verification run).
