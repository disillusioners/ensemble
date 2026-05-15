# SSE Real-Time Status Update — E2E Test Findings

## Date: 2026-05-15

## Key Finding: Frontend Bug — Stop Button Never Appears

### Problem
Backend emits `status_change` SSE events correctly (confirmed: 7ms latency after message send), but the frontend **never shows the Stop button** in the UI.

### Evidence
- Backend: Instance status changes to "running" within 7ms
- Frontend: Stop button never appears, even after 15 seconds
- Test 5 timing: `Backend idle→Send = 8ms ✅` but `Send→Stop = 15005ms ❌`
- SSE streaming works fine (no regression — 3 messages visible)

### Root Cause (Suspected)
When navigating directly to `/instances/{id}`, the `ChatComponent.currentInstance` computed signal returns `null` because the instance isn't in `instanceService.instances()` initially. Even after SSE events update the instances list, the computed may not re-evaluate properly.

### Fix Required (NOT quick-fixable)
Options:
1. ChatComponent maintains a local signal updated from SSE status changes
2. Ensure `currentInstance` re-evaluates when `instanceService.instances()` is updated
3. Subscribe directly to `SseService.statusChange` in ChatComponent

### Test File
- `frontend/e2e/send-stop-button.spec.ts` — rewritten for SSE timing verification
- Commit: `9250a52`

### What Works
- ✅ Send button visible for idle instances
- ✅ SSE streaming (no regression)
- ✅ Backend SSE events emitted correctly
- ❌ Stop button does not appear for running instances (frontend bug)
