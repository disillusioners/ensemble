# Phase 4: Testing — E2E Tests, testing-guide.md Update, Regression Run

## Objective
Create comprehensive E2E integration tests that validate the full injection flow (including WAITING_CHILDREN — W3), update testing-guide.md (C5 — renamed from ensure.md), and run the full test suite to confirm no regressions. Use long-response prompts with tool calls (S9) to keep instances RUNNING long enough for reliable injection.

**Critical changes from original plan**:
- `ensure.md` → `testing-guide.md` (C5)
- 5 existing E2E tests, not 4 (W7)
- No PAUSED rejection test (C4 — PAUSED auto-resume unchanged)
- Add WAITING_CHILDREN injection test (W3)
- Fix race condition in pause-clear test (W6)
- Use long-response + tool-call prompts (S9)

## Coupling
- **Depends on**: Phase 1, Phase 2, Phase 3 (all phases must be complete)
- **Coupling type**: tight
- **Shared files with other phases**: None directly — tests exercise the full stack
- **Shared APIs/interfaces**: Tests call the HTTP API and verify SSE events
- **Why this coupling**: E2E tests validate the complete feature end-to-end. All backend and frontend changes must be in place before meaningful E2E testing.

## Context
- **E2E test location**: `tests/e2e/test_e2e_workflows.py`
- **Test marker**: `@pytest.mark.integration` (opt-in, excluded from default CI)
- **Test runner**: `.venv/bin/python -m pytest tests/e2e/test_e2e_workflows.py -v --tb=short --override-ini="addopts=" -m integration`
- **Existing tests (5 — W7)**:
  1. `test_parent_child_workflow_happy_path`
  2. `test_pause_after_spawn_then_resume`
  3. `test_terminate_after_spawn_then_revive`
  4. `test_wave_spawn_with_defer_queue`
  5. `test_pause_blocks_defer_queue`
- **Test characteristics**: Real LLM calls, live HTTP API at localhost:8079, ~45-60s per test
- **Test guide**: `testing-guide.md` at project root (renamed from `ensure.md` — C5)
- **Database**: PostgreSQL (primary dev/test DB)

## Tasks

### 4.1 — E2E Tests

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | `test_injection_into_running_instance` | Flow: (1) Create instance with a long-running task that generates a detailed response AND uses tool calls (S9) — e.g., "Search the web for recent AI news, then write a detailed 500-word summary of each article you find"; (2) Poll instance status until RUNNING; (3) POST message to `/api/instances/{id}/messages` while RUNNING; (4) Verify 202 response with `status: "injected"`; (5) Poll `/api/instances/{id}/injection` — verify pending=true initially; (6) Wait and poll until pending=false (injection consumed) — timeout 60s; (7) Verify the injected message content appears in the conversation history (GET messages) — this validates C2 (checkpoint persistence); (8) Verify instance eventually reaches COMPLETED — timeout 120s. | `tests/e2e/test_e2e_workflows.py` |
| 2 | `test_injection_replacement` | Flow: (1) Create running instance with long-response prompt (S9); (2) Send message A via injection (202); (3) Immediately send message B via injection (202); (4) Poll injection status — verify content is B (not A); (5) Wait for consumption; (6) Verify B appears in conversation (not A, or A was never consumed). | `tests/e2e/test_e2e_workflows.py` |
| 3 | `test_injection_cleared_on_pause` (W6 race condition fix) | Flow: (1) Create running instance with long-response prompt (S9); (2) Send message via injection (202); (3) **Verify pending=true BEFORE pausing** (W6 fix); (4) Pause instance; (5) Poll injection status — verify pending=false (cleared); (6) Resume instance; (7) Verify the injected message does NOT appear in conversation (was cleared, not consumed — W6 fix). | `tests/e2e/test_e2e_workflows.py` |
| 4 | `test_injection_into_waiting_children` (W3 — NEW) | Flow: (1) Create parent instance that spawns a child (use existing parent-child workflow pattern); (2) Poll parent status until WAITING_CHILDREN; (3) POST message to parent via injection (202); (4) Verify pending=true; (5) Wait for child to complete + parent to resume; (6) Poll until pending=false (injection consumed when parent agent node runs again); (7) Verify injected message content appears in parent conversation; (8) Verify parent reaches COMPLETED. | `tests/e2e/test_e2e_workflows.py` |
| 5 | `test_normal_enqueue_when_idle` | Flow: (1) Create instance (IDLE); (2) POST message — verify normal 200 response (not 202 injected); (3) Verify instance starts processing (transitions to RUNNING/QUEUED); (4) Verify message appears in conversation. | `tests/e2e/test_e2e_workflows.py` |
| 6 | `test_paused_auto_resume_unchanged` (C4 regression test) | Flow: (1) Create instance, start it; (2) Pause instance; (3) POST message to PAUSED instance — verify 200 response (NOT 409 — C4); (4) Verify instance auto-resumes (transitions to RUNNING); (5) Verify message appears in conversation. This test guards against accidentally breaking the PAUSED auto-resume path. | `tests/e2e/test_e2e_workflows.py` |

### 4.2 — Documentation & Regression

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 7 | Update testing-guide.md (C5) | Add new E2E test entries to `testing-guide.md` (NOT `ensure.md` — renamed, C5). Document: test names, what they verify, how to run them. Add note about injection feature validation. | `testing-guide.md` |
| 8 | Run full E2E test suite | Execute: `.venv/bin/python -m pytest tests/e2e/test_e2e_workflows.py -v --tb=short --override-ini="addopts=" -m integration`. Verify all tests pass (5 existing + 6 new = 11 total). Document any failures and root-cause them. | — |
| 9 | Run unit test suite (regression check) | Execute: `.venv/bin/python -m pytest tests/ -v --tb=short` (excluding integration tests). Verify all existing tests pass + new unit tests from Phase 1 and Phase 2 pass. Target: 0 regressions. | — |
| 10 | Manual frontend verification | Start dev environment (`./dev.sh` + `cd frontend && npm start`). Manually test: (1) Send message to running instance — verify pending message appears in chat UI; (2) Verify pending message clears when consumed; (3) Verify message input allows typing + sending while running (canInject); (4) Verify pause button still works alongside send; (5) Verify pending message clears on pause; (6) Verify QUEUED still shows Pause-only (no Send button — C6). | — |

## Key Files
- `tests/e2e/test_e2e_workflows.py` — 6 new E2E test functions
- `testing-guide.md` — Updated with new test entries (C5)

## E2E Test Details

### test_injection_into_running_instance

```python
@pytest.mark.integration
def test_injection_into_running_instance():
    """Test that a message sent to a running instance is injected into the LLM conversation."""
    # 1. Create instance with long-running task + tool calls (S9)
    #    Prompt: "Search the web for recent AI news, then write a detailed 
    #            500-word summary of each article you find"
    # 2. Poll until RUNNING (1s interval, 30s timeout)
    # 3. POST /api/instances/{id}/messages with injection content
    # 4. Assert 202 + status="injected"
    # 5. GET /api/instances/{id}/injection → assert pending=true
    # 6. Poll until pending=false (2s interval, 60s timeout)
    # 7. GET messages → assert injection content in conversation (validates C2)
    # 8. Wait for COMPLETED (2s interval, 120s timeout)
```

### test_injection_cleared_on_pause (W6 fix)

```python
@pytest.mark.integration
def test_injection_cleared_on_pause():
    """Test that pause clears the injection slot (NOT consumed)."""
    # 1. Create running instance with long-response prompt (S9)
    # 2. Send message via injection (202)
    # 3. GET /api/instances/{id}/injection → assert pending=true  ← BEFORE pausing (W6)
    # 4. Pause instance
    # 5. GET /api/instances/{id}/injection → assert pending=false (cleared)
    # 6. Resume instance
    # 7. GET messages → assert injected content NOT in conversation (cleared, not consumed) (W6)
```

### test_injection_into_waiting_children (W3)

```python
@pytest.mark.integration
def test_injection_into_waiting_children():
    """Test injection into WAITING_CHILDREN instance consumed on parent resume."""
    # 1. Create parent that spawns child (existing parent-child pattern)
    # 2. Poll parent until WAITING_CHILDREN
    # 3. POST message to parent → 202 injected
    # 4. GET injection → assert pending=true
    # 5. Wait for child completion + parent resume (poll status, 120s timeout)
    # 6. Poll until pending=false (consumed when parent agent node runs)
    # 7. GET messages → assert injected content in parent conversation
    # 8. Verify parent reaches COMPLETED
```

### test_paused_auto_resume_unchanged (C4 regression guard)

```python
@pytest.mark.integration
def test_paused_auto_resume_unchanged():
    """Verify PAUSED auto-resume still works (C4 — NOT changed to 409)."""
    # 1. Create instance, start it
    # 2. Pause instance
    # 3. POST message → assert 200 (NOT 409)
    # 4. Verify instance auto-resumes (status → RUNNING)
    # 5. Verify message appears in conversation
```

### Test Timing Considerations
- **Running detection**: Poll instance status every 1s, timeout 30s to reach RUNNING
- **WAITING_CHILDREN detection**: Poll parent status every 2s, timeout 60s
- **Consumption wait**: Poll injection status every 2s, timeout 60s for consumption
- **Completion wait**: Poll instance status every 2s, timeout 120s for COMPLETED
- **LLM task design (S9)**: Use prompts that generate long responses AND trigger tool calls to ensure the instance is still RUNNING when the injection is sent. Examples:
  - "Search the web for recent AI news, then write a detailed 500-word summary of each article"
  - "Read the file at /path/to/file, analyze it, and write a comprehensive report with at least 3 sections"

### Test Isolation
- Each test creates its own instance(s) (no shared state)
- Clean up instances after each test (existing pattern in test file)
- Use unique instance names to avoid conflicts

## E2E Test Summary

| # | Test Name | Validates | New/Existing |
|---|-----------|-----------|-------------|
| 1 | `test_parent_child_workflow_happy_path` | Basic parent→child | Existing |
| 2 | `test_pause_after_spawn_then_resume` | Pause/resume | Existing |
| 3 | `test_terminate_after_spawn_then_revive` | Terminate/revive | Existing |
| 4 | `test_wave_spawn_with_defer_queue` | Wave spawn + defer | Existing |
| 5 | `test_pause_blocks_defer_queue` | Pause blocks defer | Existing |
| 6 | `test_injection_into_running_instance` | Core injection flow + C2 | **New** |
| 7 | `test_injection_replacement` | Single-slot replace | **New** |
| 8 | `test_injection_cleared_on_pause` | Pause clears slot (W6) | **New** |
| 9 | `test_injection_into_waiting_children` | WAITING_CHILDREN (W3) | **New** |
| 10 | `test_normal_enqueue_when_idle` | IDLE → normal path | **New** |
| 11 | `test_paused_auto_resume_unchanged` | C4 regression guard | **New** |

**Total: 11 E2E tests** (5 existing + 6 new)

## Constraints
- **testing-guide.md, NOT ensure.md (C5)**: All references updated to `testing-guide.md`.
- **Real LLM calls**: E2E tests use actual LLM API calls. Ensure API keys are configured.
- **PostgreSQL**: Tests run against PostgreSQL. Ensure DB is running and configured.
- **Dev server**: Tests require the daemon to be running (`./dev.sh`). Start it before running tests.
- **Long-response prompts (S9)**: Use prompts that generate long responses + tool calls to keep instance RUNNING long enough for injection.
- **No mocking**: E2E tests must not mock LLM, DB, or HTTP — they test the real system.
- **Integration marker**: All new tests must have `@pytest.mark.integration`.
- **Existing tests**: Do not modify existing 5 E2E tests unless they break (regression).
- **No PAUSED rejection test**: C4 means PAUSED auto-resume is unchanged. Test `test_paused_auto_resume_unchanged` guards against accidentally breaking this — it does NOT test 409 rejection (which was removed from the plan).
- **Race condition fix (W6)**: In `test_injection_cleared_on_pause`, verify pending=true BEFORE pausing, and verify the message does NOT appear in conversation after resume (was cleared, not consumed).

## Deliverables
- [ ] `test_injection_into_running_instance` passes (validates C2 checkpoint persistence)
- [ ] `test_injection_replacement` passes
- [ ] `test_injection_cleared_on_pause` passes (W6 race condition fixed)
- [ ] `test_injection_into_waiting_children` passes (W3)
- [ ] `test_normal_enqueue_when_idle` passes
- [ ] `test_paused_auto_resume_unchanged` passes (C4 regression guard)
- [ ] `testing-guide.md` updated with new test entries (C5)
- [ ] All 5 existing E2E tests still pass (W7 — no regressions)
- [ ] All unit tests pass (no regressions)
- [ ] Manual frontend verification completed (including C6 QUEUED check)
