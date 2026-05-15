# Send/Stop Button UX Fix — E2E Test Plan

## Date: 2026-05-15

## What Changed
The send/stop button now reflects the **selected instance's status** instead of SSE connection state.

### New Behavior (instance-status-based)
- `running`, `waiting_children`, `queued` → **Stop** button (red)
- `idle`, `error`, `terminated`, `completed`, `paused`, `failed` → **Send** button (arrow)

### Old Behavior (SSE-based — NO LONGER CORRECT)
- SSE connected → Stop button
- SSE disconnected → Send button

## Key Implementation Details
1. `MessageInputComponent` has `@Input() instanceStatus: InstanceStatus | null`
2. `isInstanceRunning` computed getter checks: `running`, `waiting_children`, `queued`
3. Template uses `@if (isInstanceRunning)` for stop button, `@else` for send button
4. Status comes from `currentInstance()?.status` via `InstanceService` polling (every 10s)
5. `InstanceStatus` type: `'idle' | 'running' | 'paused' | 'completed' | 'error' | 'terminated' | 'queued' | 'waiting_children' | 'failed'`

## Test Cases
1. Page load with idle instance → Send button visible
2. Send message → Stop button appears when running
3. Response completes → Send button returns
4. Click Stop during processing → stops, Send button reappears
5. SSE streaming still works (no regression)
6. Visual check — Stop button renders correctly

## Files
- Test file: `frontend/e2e/send-stop-button.spec.ts` (UPDATE existing)
- Playwright config: `frontend/playwright.config.ts`
- Backend: dev.sh (port 8079)
- Frontend: Angular dev server (port 4199)
- Helpers: `frontend/e2e/fixtures/test-helpers.ts`, `frontend/e2e/fixtures/cleanup.ts`
