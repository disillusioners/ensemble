# Concurrency Remediation Testing — Key Findings

**Date:** 2026-06-19
**Branch:** feature/concurrency-fixes (8 commits, 54 findings fixed)

## Critical Finding: Tree-Aware Pause/Resume Regression
- 18 tests in `tests/unit/test_tree_aware_pause_resume.py` PASS on `latest` but FAIL on `feature/concurrency-fixes`
- Root cause: pause_cascade/resume_cascade no longer calls `instance_repository.update` for cascaded children
- This is the single biggest regression introduced by the concurrency fixes
- The tree-build step is also missing the 'root' key

## Pre-Existing Failures Baseline
- `latest` branch has **46 known failures** (fixture bugs, config drift, mock incompatibility)
- Key pre-existing: test_progressive_dispatch (12), test_manager (13), test_spawn_limit_edge_cases (9), test_constants (1), test_config (1), test_innate_skills (3), test_exponential_backoff (1), test_stale_recovery (3+)
- **Always verify against latest before attributing failures to a branch**

## Concurrency Test Quality Patterns
### Strong Patterns (REAL race tests)
- `threading.Barrier(n)` + `threading.Thread` — forces synchronized fire, verifies loser's data not written
- `ThreadPoolExecutor` + file-backed SQLite/WAL — true cross-thread with real DB isolation
- File-backed engine setup with `time.sleep(0.001)` to widen race window

### Weak Patterns (Happy-path only)
- `asyncio.gather` — in-process, serialized by event loop, NOT true concurrency
- `patch.object(side_effect=...)` — tests error path of ONE caller, not two racing
- `inspect.getsource()` + `assert` — verifies code SHAPE, not runtime behavior
- Tests named "concurrent" but using sequential setup

## Session Management Lesson
- Opencode sessions sometimes modify files despite explicit "DO NOT modify" instructions
- ALWAYS run `git status --short` after read-only tasks before trusting results
- Unauthorized modifications can invalidate entire test runs
- Recovery: `git checkout -- .` then re-run cleanly
