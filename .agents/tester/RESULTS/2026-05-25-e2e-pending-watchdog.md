# E2E Test Results: pending_count + Watchdog Auto-Complete

**Date**: 2026-05-25  
**Session**: ses_1a4dd794affe8jZGWv3ALzLFFj  
**Project**: agents-ensemble (83da04de-a410-4fb5-9e92-251a99d28a52)

## Summary

All E2E tests **PASSED**. Both fixes verified working correctly.

## Test 1: pending_count Fix (3 Messages to Coder)

- **Instance ID**: 37bd8982-e237-4053-9436-2109d4f03d11
- **Agent**: coder
- **Messages sent**: 3 ("Say hello", "Say goodbye", "Say thanks")
- **All messages**: completed successfully
- **Final instance status**: `completed` ✅ (not stuck in `running`)
- **Jobs stuck in processing**: 0 ✅
- **pending_count behavior**: No continuous increase. One expected `pending_count=1 but waiting_for=0` log entry during message processing — this is correct behavior (message in-flight, no children expected, instance proceeds to complete)
- **No "pending_count keeps increasing" pattern** ✅
- **Result**: **PASS**

## Test 2: Agent-to-Agent (Leader → Coder)

- **Leader Instance ID**: 0e2cd1ab-11ab-4c3e-b26c-2169716e632d
- **Leader Agent**: leader
- **Child instances**: 1 (fcb4d9ef-11bf-4ecc-a0a1-9c654b850baf)
- **Leader final status**: `completed` ✅
- **All jobs completed**: yes (0 stuck in processing) ✅
- **No orphan warnings**: clean flow ✅
- **No false orphan detection**: ✅
- **Result**: **PASS**

## ensure.md Validation

- **dev.sh stable 30s**: PASS ✅

## Fixes Verified

1. **pending_count leak** — No longer keeps increasing after messages are consumed. Counter correctly tracks messages in-flight without leaking to 1, 2, 3...
2. **Watchdog auto-complete** — Jobs stuck in `processing` are properly handled when instance reaches `completed`/`terminated`
3. **Agent-to-agent communication** — Leader successfully spawns coder, sends message, receives child report, and completes without false orphans

## Overall Status: ✅ PASS
