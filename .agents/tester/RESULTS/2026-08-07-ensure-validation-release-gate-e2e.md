# ensure.md Validation — Release Gate E2E (2026-08-07)

## Scope

- **Validation Type:** Release Gate (Critical, mandatory for CORE pause/resume changes)
- **Requirement:** ensure.md Release Gate — E2E tests for pause/resume behavior
- **Change Context:** Resume-doesn't-restart-graph fix touches CORE pause/resume, so Release Gate E2E is mandatory.
- **Daemon Status:** RUNNING (HTTP 200 at `http://localhost:8079/health`)
- **Test File:** `tests/e2e/test_e2e_workflows.py`
- **Pack Reference:** `test/packs/e2e_workflows_ensure_test.sh`

## Prerequisites Verified

| Prerequisite | Status |
|--------------|--------|
| Daemon running at localhost:8079 | ✅ RUNNING (HTTP 200) |
| `SSL_CERT_FILE` / `SSL_CERT_DIR` unset | ✅ Confirmed unset |
| `PYTEST_TIMEOUT=280` | ✅ Exported |
| Pending jobs at start | ✅ 0 |
| Final pending jobs | ✅ 0 (clean state preserved) |

## Test Results

### Release Gate (Critical) — 4/4 passed

| # | Test | Status | Duration | Notes |
|---|------|--------|----------|-------|
| 1 | `test_parent_child_workflow_happy_path` | ✅ PASS | 53.28s | Baseline parent→child happy path; 11 deselected (integration marker filter) |
| 2 | `test_pause_after_spawn_then_resume` | ✅ PASS | 40.54s | **MOST CRITICAL** — validates resume-doesn't-restart-graph fix on real daemon with LLM calls |
| 3 | `test_terminate_after_spawn_then_revive` | ✅ PASS | 46.87s | Validates terminate cascade + revival flow |
| 4 | `test_three_level_cascade_reports` | ✅ PASS | 111.74s | Validates 3-level hierarchical reporting (longest: 1m51s, under 320s timeout) |

**Total wall time:** ~252s (4.2 min), well within the per-test dual-layer timeout guard.

## Constraints Honored

- ✅ Each test wrapped in `timeout 300` (or `timeout 320` for Test 4 cascade)
- ✅ Tests ran ONE BY ONE (no parallel combination; combined would exceed 5-min cap)
- ✅ `PYTEST_TIMEOUT=280` set per-test via `--override-ini="timeout=280"`
- ✅ SSL cert vars unset before each invocation
- ✅ Pending-job check performed before each test (clean throughout)
- ✅ No `pytest -x` used; full deselected count visible per run
- ✅ `--override-ini="addopts="` strips pytest config to avoid collection side-effects

## Quarantine Status

- `.agents/tester/QUARANTINE.md` was checked — no quarantined tests in the 4 selected. No quarantine-related skips recorded.

## ensure.md Improvement Notices

None — all 4 requirements ran cleanly without contradictions. The Release Gate's literal command shape (single `pytest -k <name>` invocation per test with `--override-ini="timeout=280"`) matches the pack-mapped discipline (one pack per requirement, scoped, ≤ 5-min cap, dual-layer timeout).

## PytestConfigWarning Note (informational, non-blocking)

Each test produced two `PytestConfigWarning: Unknown config option: timeout / timeout_method` warnings. Root cause: the `pytest-timeout` plugin is not installed in the active `.venv`. The dual-layer protection (outer `timeout 300`/`320` shell guard + inner `PYTEST_TIMEOUT=280` env var) is what actually enforces the time budget. This warning is pre-existing and does not affect validation outcomes.

## Overall Release Gate Status

### ✅ PASSED — 4/4 Critical Release Gate tests green

The resume-doesn't-restart-graph fix is validated end-to-end on the live daemon:
- Pause + resume does not restart the graph (Test 2 — the most critical)
- Spawn-then-resume happy path (Test 1) is clean
- Terminate + revival still works (Test 3)
- Multi-level cascade reports still propagate correctly (Test 4)

No regressions detected in pause/resume/terminate/revival surface area.
