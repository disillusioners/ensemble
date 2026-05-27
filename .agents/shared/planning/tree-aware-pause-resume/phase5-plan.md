# Phase 5: Frontend Verification

## Objective

Verify that the frontend correctly handles tree-level status changes when pause/resume operates on the entire tree. No code changes are expected — this is a verification pass.

## Coupling

- **Depends on**: Phase 3 (router changes complete)
- **Coupling type**: loose — frontend only consumes the API
- **Shared files with other phases**: None

## Context

The frontend already handles pause/resume via instance-level API calls. The key question is: when the backend pauses/resumes the entire tree, does the UI update correctly for all visible nodes?

### Frontend components involved

| Component | File | Role |
|-----------|------|------|
| Chat page | `frontend/src/app/pages/chat/chat.component.ts` | Pause/resume button handlers |
| Message input | `frontend/src/app/components/message-input/` | 3-way toggle (send/pause/resume) |
| Instance list | `frontend/src/app/components/instance-list/` | Instance tree UI with status indicators |
| Live hub (WebSocket) | `frontend/src/app/services/` | Real-time status updates |

### How status updates propagate to frontend

1. Backend calls `live_hub.stream_status_change(instance_id, status, agent_id)` for each paused/resumed node
2. WebSocket pushes status change to connected clients
3. Frontend receives event and updates instance status in UI

### Key verification points

1. **Multiple status change events**: When tree is paused, the backend sends a separate `stream_status_change` for EACH node. The frontend should handle receiving multiple rapid status updates.

2. **`target_id` in response**: The API response now includes `target_id`. Verify the frontend doesn't break on unexpected fields in the response.

3. **Resume message targeting**: The resume message goes to the selected instance only. The frontend already sends `POST /instances/{instance_id}/resume` targeting the selected instance. Verify this still works for child instances.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Verify pause button works for child instances | Select a child instance, click pause. ALL instances in tree should show PAUSED status. | chat.component.ts, instance-list |
| 2 | Verify resume button works for child instances | After pausing, select any child, click resume. ALL instances should show RUNNING status. | chat.component.ts, message-input |
| 3 | Verify status indicators update for entire tree | When tree is paused, check that the instance tree shows PAUSED for all nodes, not just the clicked one. | instance-list |
| 4 | Verify 3-way toggle state | After resuming, the message input should switch from resume mode back to send mode. | message-input |
| 5 | Check WebSocket event handling | Verify frontend handles rapid-fire status change events (one per node in tree) without dropping any. | live hub service |
| 6 | Verify no console errors | Check browser console for any errors from unexpected response fields (`target_id`). | — |

## Expected Outcome

**No code changes needed.** The frontend is already designed to react to per-instance status change events via WebSocket. Since the backend now sends events for all nodes in the tree, the UI should update correctly for all visible nodes.

If issues are found, they would likely be:
- Race condition with rapid WebSocket events (unlikely but possible)
- UI state not resetting properly after tree-level resume
- Instance list not refreshing for nodes that weren't in the visible viewport

## Deliverables

- [ ] Manual test: pause child → verify all nodes show PAUSED
- [ ] Manual test: resume child → verify all nodes show RUNNING
- [ ] Manual test: resume root → verify all nodes show RUNNING
- [ ] No console errors
- [ ] Document any issues found (or confirm no changes needed)
