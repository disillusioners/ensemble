# SSE Stop Button Fix — Three Quick Fixes

## Date: 2026-05-15

## Problem
SSE status_change events arrived in 7ms from backend but the Stop button never appeared in the UI. On direct navigation to `/instances/{id}`, the Stop button was especially broken.

## Root Causes (3 separate issues)

### Issue 1: SSE status updates silently dropped for unknown instances
- **File**: `frontend/src/app/services/instance.service.ts` — `updateInstanceStatus()`
- **Cause**: When SSE status_change arrived for an instance not in the local instances list, `findIndex` returned -1 and the update was dropped
- **Fix**: Create a minimal InstanceInfo entry when instance not found
- **Commit**: `751dd43`

### Issue 2: Fetched instance never added to local list
- **File**: `frontend/src/app/pages/chat/chat.component.ts` — `handleInstanceIdChange()`
- **Cause**: `startPolling()` clears `instances.set([])`, then `handleInstanceIdChange()` fetches via API but never adds the result to `instanceService.instances()`
- **Fix**: Add `instanceService.instances.update()` after API fetch
- **Commit**: `0ed06e5`

### Issue 3: @Input() not reactive with computed() signals
- **File**: `frontend/src/app/components/message-input/message-input.component.ts`
- **Cause**: `@Input()` decorator creates plain properties, not signals. `computed()` cannot reactively track plain properties, so `isInstanceRunning` never updated
- **Fix**: Convert `@Input()` to Angular `input()` function signals, update template to call signals as functions
- **Commit**: `cfed61b`

## Key Insight
Three layers of the fix were needed:
1. **Service layer**: Handle unknown instances in SSE updates
2. **Component layer**: Add fetched instances to service list
3. **Template layer**: Use reactive signals instead of plain @Input()

## Timing Impact
- Before fix: Stop button **never appeared** (15+ seconds, gave up)
- After fix: Stop button appears in **~114ms** (SSE real-time)

## Test File
`frontend/e2e/send-stop-button.spec.ts` — 6 test cases with timing measurements
