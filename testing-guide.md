# Testing Guide

## Quick Test Commands

```bash
# Default test suite (unit tests, excludes integration/postgres/e2e)
.venv/bin/pytest

# Parallel mode (3x speedup)
.venv/bin/pytest -n auto -m 'not postgres'

# PostgreSQL tests (requires live PG)
.venv/bin/pytest --override-ini="addopts=" -m postgres

# Integration tests (requires live OpenCode server)
.venv/bin/pytest --override-ini="addopts=" -m integration

# E2E tests (ONLY for big changes / explicit requirement)
.venv/bin/pytest --override-ini="addopts=" tests/e2e/ -v
```

## E2E Test Policy

> ⚠️ **E2E tests should ONLY run when there's a big change or explicit requirement. They are NOT part of the default test run.**

- E2E tests make **REAL daemon HTTP API calls** and **REAL LLM calls**
- E2E tests take **~45-60 seconds EACH** (~200s total)
- E2E tests should ONLY be run when:
  1. Making **big architectural changes**
  2. **Explicitly required** for verification
  3. **Before major releases**
- E2E tests are **NOT part of the default suite or CI**

### E2E Prerequisites
- `OPENAI_API_KEY` set in `.env`
- Daemon running via `./dev.sh`
- Valid LLM credits/budget (these tests cost real money)

## Test Markers

| Marker | What it covers | Default | When to run |
|--------|---------------|---------|-------------|
| (none) | Unit tests | ✅ Included | Always |
| `integration` | Live OpenCode server tests | ❌ Excluded | Explicitly |
| `postgres` | PostgreSQL-specific tests | ❌ Excluded | Explicitly |
| `e2e` | End-to-end workflow tests | ❌ Excluded | Big changes only |

Default run excludes integration, postgres, and e2e tests via `addopts = "-m 'not integration and not postgres'"` in `pyproject.toml`.

> **Note**: E2E tests are excluded from default runs because they carry the `integration` marker, not a separate `e2e` marker. To run them: `pytest -m integration`.

## 30-Second Stability Check

```bash
timeout 30 bash ./dev.sh
# Expected: exit code 124 (timeout = daemon ran clean for 30s)
```

## Known Issues

- **`test_message_queue_e2e.py` sys.modules pollution**: This file mutates `sys.modules` at module import time. If collected alongside non-integration tests, it breaks langgraph mocks for the entire session. Must run in isolation.
- **`test_api_router_extraction.py` ordering pollution**: Shows cascading errors when run after other tests due to test-ordering pollution. Must run in isolation. Root cause TBD.
- **4 tests skipped** due to production bug in `repository.py:295,322` (`.contains()` double-escaping). This is a known production bug — do not attempt to fix in tests.