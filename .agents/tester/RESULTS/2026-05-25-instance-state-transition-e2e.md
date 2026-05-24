# E2E Test: Instance State Transition Fixes

**Date:** 2026-05-25
**Dev Server:** http://localhost:8079 (healthy, uptime 458s+)
**Project:** agents-ensemble (83da04de-a410-4fb5-9e92-251a99d28a52)

## Bugs Under Test

1. **Bug 1**: Parent instance stuck in `waiting_children` after children complete
2. **Bug 2**: Simple agent stays `running` (or wrong state) after processing completes

## Test Results

### Test 1: Simple Agent State Transition (Bug 2)
| Detail | Value |
|--------|-------|
| Instance | `96c659cd-68ed-480f-a535-20c9c0f7d6c6` |
| Agent | coder |
| Message | "Say the exact phrase: Hello world test passed. Say nothing else." |
| Initial Status | idle |
| Final Status | **waiting_children** |
| Children | [] (empty) |
| **Result** | **FAIL** — stuck in `waiting_children` with no children |

**Expected:** `idle` or `completed`
**Actual:** `waiting_children` — the instance never transitioned out after processing.

### Test 2: Agent-to-Agent Communication (Bug 1)
| Detail | Value |
|--------|-------|
| Leader Instance | `5d50681a-1648-40e4-babc-65c46a6516bd` |
| Leader Agent | leader |
| Leader Message | "Spawn a coder instance and send it: 'Reply with: child test passed'. Wait for the coder to respond, then report what it said." |
| Child Instance | `15c7091e-35ca-43b5-aefb-2ed78aee70dc` |
| Child Agent | coder |
| Child Status | **terminated** (completed correctly) |
| Leader Final Status | **waiting_children** |
| Leader Children | [] (empty — child already terminated) |
| **Result** | **FAIL** — leader stuck in `waiting_children` after child completed |

**Expected:** Leader transitions to `idle`/`completed` after child terminates
**Actual:** Leader stuck in `waiting_children` even though child already terminated and `children=[]`

## Additional Stuck Instances Found

From the full instance list, several older instances are also stuck:

| Instance | Agent | Status | Issue |
|----------|-------|--------|-------|
| `1dc8c7f4` | leader | `running` | Stuck with terminated child `1a64d9cd` still in children list |
| `768c7b22` | explorer | `running` | Stuck in running |
| `621f896f` | explorer | `running` | Stuck in running |
| `f2ab8665` | explorer | `running` | Stuck in running |

## Root Cause Analysis

### Bug 1: Parent stuck in `waiting_children`
- **Root Cause**: `MessageJobHandler.handle()` (HTTP API path) doesn't call `_process_child_completion_and_notify_parent()` after processing completes
- **Evidence**: Leader `5d50681a` has `children=[]` (child removed) but status still `waiting_children`
- **Fix Location**: The HTTP API message handling path needs to check for child completion and transition the parent

### Bug 2: Simple agent stuck in wrong state
- **Root Cause**: After processing completes via HTTP API path, the instance status isn't properly transitioned back to `idle`/`completed`
- **Evidence**: Coder `96c659cd` has `children=[]` and `parent=None` but stuck in `waiting_children`
- **Note**: The coder entering `waiting_children` is unexpected — it shouldn't enter this state at all since it has no children. This suggests the state machine is transitioning to `waiting_children` incorrectly.

## Overall Status

| Test | Expected | Actual | Result |
|------|----------|--------|--------|
| Test 1: Simple Agent | idle/completed | waiting_children | **FAIL** |
| Test 2: Agent-to-Agent | idle/completed | waiting_children | **FAIL** |

**Testing Status: ❌ NOT READY — Both bugs confirmed still present**

### Action Needed
- [ ] Fix Bug 1: Add `_process_child_completion_and_notify_parent()` call in `MessageJobHandler.handle()` path
- [ ] Fix Bug 2: Fix state transition logic for instances that complete processing without children
- [ ] Re-run E2E tests after fixes
