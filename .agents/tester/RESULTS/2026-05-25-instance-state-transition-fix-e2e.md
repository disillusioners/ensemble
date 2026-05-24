# E2E Test: Instance State Transition Fixes — Post-Fix Verification

**Date:** 2026-05-25
**Dev Server:** http://localhost:8079 (healthy, uptime 199s at test time)
**Project:** agents-ensemble (83da04de-a410-4fb5-9e92-251a99d28a52)
**Commit:** 3b8fa74 — fix: instance state transition bugs — parent stuck waiting_children + simple agent wrong state

## Bugs Fixed

### Bug 1: Parent stuck in `waiting_children` after child completes
- **Root Cause**: `MessageJobHandler.handle()` (HTTP API path) skipped `_process_child_completion_and_notify_parent()` when `message_id is None`
- **Fix**: Removed the `if message_id is None` skip guard, call the handler unconditionally with try/except
- **File**: `daemon/services/message_job_handler.py`

### Bug 2: Simple agent stuck in `waiting_children` with no children
- **Root Cause**: `child_reports.py:588` set `waiting_children` based on `pending_count > 0` alone, without checking `waiting_for > 0`
- **Fix**: Added `instance.waiting_for > 0` guard to the pending_count check
- **File**: `daemon/services/child_reports.py`

## Test Results

### Test 1: Simple Agent State Transition (Bug 2)
| Detail | Value |
|--------|-------|
| Instance | `4262139e-f510-45e1-9648-1af3c5508eed` |
| Agent | coder |
| Message | "Say the exact phrase: Hello world test passed. Say nothing else." |
| Final Status | **completed** ✅ |
| **Result** | **PASS** |

State transitions: `idle` → `running` → `completed`

### Test 2: Agent-to-Agent Communication (Bug 1)
| Detail | Value |
|--------|-------|
| Leader Instance | `1a554128-f54f-4766-95da-ab1d8bdab9e0` |
| Leader Agent | leader |
| Child Instance | `c1592eee-9837-44de-9895-2d121e3405f1` |
| Child Agent | coder |
| Child Final Status | **terminated** ✅ |
| Leader Final Status | **completed** ✅ |
| **Result** | **PASS** |

State transitions: `idle` → `running` → `waiting_children` → `completed`

### ensure.md Validation
- ✅ dev.sh stable 30s+ (uptime 199s at check time)
- ✅ API healthy

## Changes Summary
| File | Lines Changed | Description |
|------|--------------|-------------|
| `daemon/services/message_job_handler.py` | 15 insertions, 10 deletions | Removed message_id None skip, added try/except |
| `daemon/services/child_reports.py` | 13 insertions, 4 deletions | Added waiting_for > 0 guard + warning log |

## Overall Status

| Test | Before Fix | After Fix |
|------|-----------|-----------|
| Test 1: Simple Agent | FAIL (waiting_children) | **PASS (completed)** |
| Test 2: Agent-to-Agent | FAIL (waiting_children) | **PASS (completed)** |
| ensure.md | PASS | **PASS** |

**Testing Status: ✅ READY — Both bugs fixed and verified**
