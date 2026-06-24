# Test System Optimization — Validation Lessons

**Date:** 2026-06-25
**Branch:** `feature/test-optimization`

---

## Key Findings

### 1. `test_ensure_dev_sh_still_works` Process Leak (Pre-Existing)
**What:** The test `tests/job_queue/test_jober_watch_integration.py::TestJoberWatchIntegration::test_ensure_dev_sh_still_works` spawns `dev.sh` → `uvicorn --reload` via `subprocess.run()` without:
- `timeout=` parameter
- `start_new_session=True` / process group isolation
- Cleanup fixture

**Impact:** Process leaks to port 8079 between runs. Run 2 hangs because Run 1's uvicorn still holds the port.

**Also:** The test's natural runtime (~30s) races with the global `pytest-timeout = 30` setting. Outcome flips between runs.

**Fix recommendation (not applied — read-only):** Add `subprocess.run(..., timeout=45)` and `start_new_session=True` + `os.killpg()` in `try/finally`. Or mock subprocess to not launch real server.

### 2. Thread-Based pytest-timeout Can't Interrupt C-level select()
**What:** `timeout_method = "thread"` cannot interrupt blocking C-level syscalls like `select.select()` (used by `asyncio` event loop and `subprocess.communicate()`).

**Impact:** Tests that block in C-level select() are killed by pytest-timeout but the kill is a hard process abort, not a graceful interrupt. The entire pytest run terminates.

**Trade-off:** `timeout_method = "thread"` is REQUIRED for `asyncio_mode = "auto"` (signal-based timeout doesn't work with asyncio). So this is an inherent limitation.

### 3. Missing Integration Markers Cause Spurious Failures
**What:** 5 integration test files lack `@pytest.mark.integration`:
- `tests/integration/test_mcp_lifecycle.py`
- `tests/integration/test_migration.py`
- `tests/integration/test_multi_turn_resume.py` ← 3 tests fail
- `tests/integration/test_dlq_project_normalization.py`
- `tests/integration/test_compaction_e2e.py`

**Impact:** These run in the default suite (because `-m 'not integration'` only excludes marked tests), hit the 30s timeout, and fail.

**Fix:** Add `@pytest.mark.integration` to these files. Quick fix eligible (< 5 lines each).

### 4. Serial Full Run Exceeds 4-Minute Target
**What:** The serial default suite takes 6:07 (367s) with `--timeout=300` to let slow tests complete.

**Mitigation:** Parallel mode (`-n 4`) completes in 2:00 (120s) — 3.05× speedup. The optimization's parallel execution is the primary mechanism for staying under 4 minutes.

### 5. Parallel Mode Has Fewer Failures Than Serial
**What:** Parallel (-n 4): 10 failures. Serial: 16 failures.

**Why:** Per-worker isolation avoids shared-state races that occasionally flake serial runs. Concurrency/timing-sensitive tests pass more reliably when isolated to separate processes.

---

## Pre-Existing Flaky Tests (Document for Future Reference)

| Test | Category | Root Cause |
|------|----------|------------|
| `test_ensure_dev_sh_still_works` | Process leak | Spawns real dev.sh, no cleanup |
| `test_atomic_retry_concurrent_calls_only_one_succeeds` | Threading race | SQLite StaticPool atomic_retry race |
| `test_concurrent_terminal_writes_only_one_succeeds` | Threading race | SQLite StaticPool concurrent writes |
| `test_resume_after_llm_failure_preserves_state` | Missing marker | test_multi_turn_resume.py lacks @pytest.mark.integration |
| `test_warmup_registers_enabled_builtin` | Environment | MCP warmup requires specific runtime |
| `test_health_endpoint_returns_ensemble_config_fields` | Environment | Health endpoint config-dependent |
| `test_bootstrap_creates_webfetch_server` | Environment | WebFetch bootstrap requires setup |
| `test_acquire_and_execute_timeout` | Timing | Slack rate limiter timing-sensitive |

---

## Quick Fix Opportunities (Not Applied — Read-Only Validation)

1. **Add `@pytest.mark.integration` to 5 files** — Eliminates 3+ spurious failures (< 5 lines each)
2. **Fix `test_ensure_dev_sh_still_works` subprocess cleanup** — Add timeout + process group reaping (~10 lines)
3. **Consider deselecting `test_ensure_dev_sh_still_works` by default** — Or mark it as integration
