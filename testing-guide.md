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
- E2E tests take **~45-140 seconds EACH** (~660s total, ~11 min) for **11 E2E tests** (5 existing + 6 new injection tests)
- E2E tests should ONLY be run when:
  1. Making **big architectural changes**
  2. **Explicitly required** for verification
  3. **Before major releases**
- E2E tests are **NOT part of the default suite or CI**

### E2E Prerequisites
- `OPENAI_API_KEY` set in `.env`
- Daemon running via `./dev.sh`
- Valid LLM credits/budget (these tests cost real money)

### E2E Test Inventory

| # | Test Name | What It Validates | Duration |
|---|-----------|-------------------|----------|
| 1 | `test_parent_child_workflow_happy_path` | Parent→child happy path + Phase 2 reuse | ~60s |
| 2 | `test_pause_after_spawn_then_resume` | Pause/resume after spawn | ~50s |
| 3 | `test_terminate_after_spawn_then_revive` | Terminate/revive after spawn | ~50s |
| 4 | `test_wave_spawn_with_defer_queue` | Wave spawn + defer queue + cross-system | ~140s |
| 5 | `test_pause_blocks_defer_queue` | Pause blocks defer queue | ~45s |
| 6 | `test_injection_consumed_by_running_instance` | Injection into RUNNING → consumed by agent_node, marker in history | ~60s |
| 7 | `test_injection_cleared_on_pause` | Pause clears injection slot (W6 fix) — content NOT consumed | ~50s |
| 8 | `test_injection_replacement` | Second injection replaces first; only second appears in history | ~60s |
| 9 | `test_injection_into_waiting_children` | Injection into WAITING_CHILDREN consumed on parent resume (W3) | ~90s |
| 10 | `test_paused_auto_resume_unchanged` | PAUSED auto-resume returns 200, not 202/409 (C4 guard) | ~50s |
| 11 | `test_injection_query_endpoint` | GET /injection lifecycle: pending→true→false | ~60s |

## Injection E2E Tests (User Message Injection Feature)

The injection feature allows posting a message to a RUNNING or WAITING_CHILDREN
instance without enqueueing it as a fresh user turn. The message is stored in a
RAM injection slot and consumed by the agent_node on its next LLM step.

### API Endpoints Used
- `POST /api/instances/{id}/messages` — state-aware routing:
  - RUNNING/WAITING_CHILDREN → 202 (injection slot)
  - PAUSED → 200 (auto-resume, C4)
  - IDLE/terminal → 200 (normal enqueue)
- `GET /api/instances/{id}/injection` — query pending injection status

### SSE Events
- `injection_pending` — message injected into slot
- `injection_consumed` — agent_node consumed the injection
- `injection_cleared` — injection cleared (pause, replacement, or terminate)

### Test Flow
1. Spawn instance with a long-running prompt (S9 — generates 500+ word response + tool calls)
2. Poll until RUNNING status
3. POST injection message → verify 202
4. GET /injection → verify pending=true
5. Poll until pending=false (consumed)
6. GET /messages → verify injected content appears in conversation history
7. Wait for terminal status

### Key Decisions Validated
- C2: Injected HumanMessage persisted to checkpoint (visible in conversation history)
- C4: PAUSED auto-resume unchanged (returns 200, not 409)
- W3: WAITING_CHILDREN injection consumed on parent resume
- W6: Pause CLEARS (not consumes) the injection slot
- S9: Long-response prompts keep instance RUNNING long enough for injection

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