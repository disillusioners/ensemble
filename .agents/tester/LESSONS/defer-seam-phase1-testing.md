# Defer Seam Bugfix Phase 1 — Testing Notes

## Key Findings

### Phase 1 Bug Fixes (P1, P2, F11, F17) — ALL VERIFIED PASS

The implementation correctly addresses all 4 targeted bugs:
- **P1**: `message_id` stamping + NULL-safe cross-system guard prevents self-deadlock
- **P2**: `has_active_non_deferred_work` predicate counts Tasks, not just JobItems
- **F11**: Same NULL-safe guard applied to `has_pending_tasks_blocked_by_busy_instance`
- **F17**: 13 SQLite invariant tests verify seam contracts

### C1 Startup Crash Fix — VERIFIED
- Commit `180607cb` moved `set_task_repository()` from `initialize()` to `setup_worker_pool()` after `_task_repo` assignment
- Smoke test passes when run with `-m integration` marker

### Pre-existing Failures (3 in job_queue suite)
1. **Concurrent SQLite tests** (2 tests): `InvalidTransitionError` and `InterfaceError` — SQLite in-memory threading limitation, not Phase 1
2. **dev.sh RAG test** (1 test): `RAGRequiredError` — environment config issue (`RAG_IS_REQUIRED` set but binary missing)

### Test Suite Performance
- Job queue suite: 1279 tests in 26.47s ✅ Fast
- Full suite: >40 min ⚠️ Too slow for single-run completion
- Use pytest-xdist (`-n auto`) for full suite

## Gotchas

### Integration Test Marker
`test_daemon_startup_smoke.py` is marked `@pytest.mark.integration` and excluded by default `addopts = "-m 'not integration and not postgres'"`. Run with explicit `-m integration` flag.

### Concurrent SQLite Tests
Tests using `StaticPool` with `:memory:` SQLite have inherent threading issues. The `test_job_repository_atomic_transition.py` concurrent tests fail due to SQLite's single-connection limitation, not application bugs.

### RAG_IS_REQUIRED Env Var
The `test_ensure_dev_sh_still_works` test fails if `RAG_IS_REQUIRED` env var is set but RAG binary is not available. This is an environment configuration issue, not a code bug.

## Review Warnings (from review-phase1.md)
- W1: `_looks_like_mock` heuristic ships test-detection code to production (code smell)
- W2: Legacy fallback in `_defer_idle_check` re-introduces dual-predicate drift risk
- W3: `is_deferred` missing on 2 non-dispatch `enqueue_message` call sites (functionally correct)
- W4: Stale message_id edge case in NULL-safe guard (corner case)

These warnings should be tracked as follow-ups but do not block Phase 1 merge (after C1 fix).
