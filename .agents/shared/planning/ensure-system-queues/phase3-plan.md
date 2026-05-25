# Phase 3: Frontend Ensure System Queues Button

## Objective

Add an "Ensure System Queues" button to the frontend job queue list header that calls the ensure API and provides visual feedback about what was found/created.

## Coupling

- **Depends on**: Phase 2 (needs the `POST /projects/{project_id}/queues/ensure-system` endpoint)
- **Coupling type**: loose — only needs the API URL and response shape, not implementation
- **Shared files with other phases**: None
- **Shared APIs/interfaces**: Consumes the ensure endpoint from Phase 2
- **Why this coupling**: Frontend calls backend API; only contract dependency is the response schema

## Context

- Frontend queue list component: `frontend/src/app/components/queue-list/`
- Queue service: `frontend/src/app/services/queue.service.ts`
- Queue model: `frontend/src/app/models/job-queue.model.ts`
- Header has `.header-actions` div with pause toggle and refresh button
- No "ensure" or "repair" button currently exists

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Add ensureSystemQueues() to queue service** | Add method: `ensureSystemQueues(projectId: string): Observable<EnsureSystemQueuesResponse>`. Maps to `POST /api/projects/${projectId}/queues/ensure-system`. Define the response interface. | `frontend/src/app/services/queue.service.ts`, `frontend/src/app/models/job-queue.model.ts` |
| 2 | **Add Ensure button to queue-list header** | Add a Material icon button (e.g., `build` or `verified_user` icon) to `.queue-list-header .header-actions`. Place before the pause toggle. Use `matTooltip="Ensure system queues"`. | `frontend/src/app/components/queue-list/queue-list.component.html` |
| 3 | **Implement handler + visual feedback** | Add `onEnsureSystemQueues()` method in component. Show loading spinner on button while request is in-flight. On success: show snackbar with result ("All system queues exist" or "Created 2 missing queues"). On error: show error snackbar. Refresh queue list after ensure. | `frontend/src/app/components/queue-list/queue-list.component.ts` |
| 4 | **Style the button** | Add appropriate styling for the ensure button — consistent with existing icon buttons in the header. | `frontend/src/app/components/queue-list/queue-list.component.scss` |

## Key Files

- `frontend/src/app/services/queue.service.ts` — Add `ensureSystemQueues()` method
- `frontend/src/app/models/job-queue.model.ts` — Add `EnsureSystemQueuesResponse` interface
- `frontend/src/app/components/queue-list/queue-list.component.html` — Add button in header
- `frontend/src/app/components/queue-list/queue-list.component.ts` — Add handler
- `frontend/src/app/components/queue-list/queue-list.component.scss` — Style button

## Button Placement

```html
<div class="queue-list-header">
  <div class="header-title">
    <mat-icon>queue</mat-icon>
    <span>Queues</span>
  </div>
  <div class="header-actions">
    <!-- ADD: Ensure System Queues button (before pause toggle) -->
    <button mat-icon-button 
            (click)="onEnsureSystemQueues()" 
            matTooltip="Ensure system queues"
            [disabled]="ensuring()">
      <mat-icon>{{ ensuring() ? 'hourglass_empty' : 'build' }}</mat-icon>
    </button>
    
    <!-- EXISTING: Pause toggle + Refresh -->
    <mat-slide-toggle ...>
    <button mat-icon-button (click)="onRefresh()" ...>
  </div>
</div>
```

## User Feedback (Snackbar Messages)

| Scenario | Message | Type |
|----------|---------|------|
| All 4 queues exist | "All system queues are present" | info |
| Some created | "Created {N} missing system queues" | success |
| Error | "Failed to ensure system queues: {error}" | error |

## Constraints

- Button should be disabled while request is in-flight (prevent double-click)
- Must refresh queue list after successful ensure to show newly created queues
- Use Material icons consistent with existing UI patterns
- Snackbar for feedback (consistent with other actions in the app)

## Deliverables

- [ ] `ensureSystemQueues()` method in queue service
- [ ] `EnsureSystemQueuesResponse` TypeScript interface
- [ ] Ensure button in queue-list header
- [ ] Loading state + snackbar feedback
- [ ] Queue list refresh after ensure
