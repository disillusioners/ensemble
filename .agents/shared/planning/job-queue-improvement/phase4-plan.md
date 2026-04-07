# Phase 4: Frontend — Schema Alignment & Cleanup

## Objective
Align the frontend TypeScript types with the updated backend API schema (Phase 2), fix type mismatches, remove dead code (duplicate methods, unused fields), and ensure the frontend correctly displays job data including source, metadata, and cancellation timestamps.

## Coupling
- **Depends on**: Phase 2 (needs final API contract with new fields)
- **Coupling type**: loose — depends on the schema interface defined in Phase 2, not its implementation
- **Shared files with other phases**: None (frontend files only)
- **Why loose**: Phase 2 defines which fields the API returns. This phase aligns the frontend to match. Can start before Phase 2 is fully merged if the interface is agreed upon.

## Context

### Current Mismatches (Post Phase 2)

After Phase 2 adds `source`, `job_metadata`, `cancelled_at` to the backend API response:

| Issue | File | Line | Fix |
|-------|------|------|-----|
| `source: JobSource` expected but was never returned | `job.model.ts` | 11 | ✅ Now returned by API — no frontend change needed |
| `job_metadata: Record<string, any>` expected but was never returned | `job.model.ts` | 21 | ✅ Now returned as `job_metadata` — verify name match |
| `cancelled_at: string \| null` expected but was never returned | `job.model.ts` | 22 | ✅ Now returned — no frontend change needed |
| `message: string` required but API returns optional | `job.model.ts` | 10 | Make optional: `message?: string` |
| `agent_dir` returned by API but not in frontend | N/A | N/A | ✅ OK — implementation detail, frontend doesn't need it |
| `setQueuePaused()` dead code in project service | `project.service.ts` | 34 | Remove dead method |
| `currentObserver` unused field | `job-sse.service.ts` | 27 | Remove unused field |

### What Works (No Changes Needed)
- `job.service.ts` — HTTP methods match API endpoints correctly
- `job-sse.service.ts` — SSE handling works (just remove unused field)
- `jobs.component.ts` — Main page logic correct
- All component templates display existing fields correctly

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Fix `message` field type | Change from required `string` to optional `string` to match backend | `frontend/src/app/models/job.model.ts:10` |
| 2 | Remove `setQueuePaused()` dead method | Delete the unused method from ProjectService | `frontend/src/app/services/project.service.ts:34-46` |
| 3 | Remove `currentObserver` unused field | Delete the unused private field | `frontend/src/app/services/job-sse.service.ts:27` |
| 4 | Verify field name alignment | Confirm `job_metadata` matches between frontend and backend | `frontend/src/app/models/job.model.ts:21` |
| 5 | Display new fields in job components | Show `source` badge, `cancelled_at` timestamp in job detail drawer | `frontend/src/app/components/job-detail-drawer/` |
| 6 | Update job-create-dialog to pass metadata | Ensure the create dialog can optionally pass `metadata` dict | `frontend/src/app/components/job-create-dialog/` |

## Detailed Implementation

### Task 1: Fix `message` Field Type

**File**: `frontend/src/app/models/job.model.ts` line 10

```typescript
// Before:
message: string;

// After:
message?: string;
```

This aligns with the backend `Optional[str]` type in `JobResponse`.

### Task 2: Remove Dead `setQueuePaused()` Method

**File**: `frontend/src/app/services/project.service.ts`

Remove lines 34-46 (the `setQueuePaused()` method). Keep `pauseJobQueue()` and `resumeJobQueue()` which are actually used by `jobs.component.ts`.

**Verify**: Search codebase for any usage of `setQueuePaused()` before removing:
```bash
grep -r "setQueuePaused" frontend/src/
```
Expected: only the definition itself, no callers.

### Task 3: Remove Unused `currentObserver`

**File**: `frontend/src/app/services/job-sse.service.ts` line 27

```typescript
// Remove this line:
private currentObserver: Subscription | null = null;
```

The service uses local `observer` variables inside methods instead. This field was likely left over from a refactor.

### Task 4: Verify Field Names

Check that `job_metadata` in the frontend model matches the backend response field name.

**Backend** (Phase 2 adds): `job_metadata: Optional[dict[str, Any]]`
**Frontend** (existing): `job_metadata: Record<string, any>`

✅ Names match — no change needed.

### Task 5: Display New Fields in Components

**File**: `frontend/src/app/components/job-detail-drawer/job-detail-drawer.component.html`

Add display for `source` and `cancelled_at`:

```html
<!-- Source badge -->
@if (job.source) {
  <div class="detail-row">
    <span class="label">Source</span>
    <span class="value">
      <span class="badge badge-source">{{ job.source }}</span>
    </span>
  </div>
}

<!-- Cancelled timestamp -->
@if (job.cancelled_at) {
  <div class="detail-row">
    <span class="label">Cancelled</span>
    <span class="value">{{ job.cancelled_at | date:'medium' }}</span>
  </div>
}

<!-- Metadata (collapsible) -->
@if (job.job_metadata && Object.keys(job.job_metadata).length > 0) {
  <div class="detail-row">
    <span class="label">Metadata</span>
    <span class="value">
      <pre>{{ job.job_metadata | json }}</pre>
    </span>
  </div>
}
```

### Task 6: Create Dialog Metadata (Optional Enhancement)

**File**: `frontend/src/app/components/job-create-dialog/job-create-dialog.component.ts`

The current create dialog doesn't have a metadata input. This is low priority since metadata is typically set programmatically. If needed, add a collapsible "Advanced Options" section with a key-value metadata editor.

**Recommendation**: Skip this for now. Metadata is used by programmatic API consumers (scheduler, telegram bot), not human users. The dialog already passes `metadata: undefined` which defaults to `null` on the backend.

## Key Files
- `frontend/src/app/models/job.model.ts` — Type fix
- `frontend/src/app/services/project.service.ts` — Remove dead method
- `frontend/src/app/services/job-sse.service.ts` — Remove unused field
- `frontend/src/app/components/job-detail-drawer/` — Display new fields

## Constraints
- All changes must be backward compatible with existing Angular version
- No new npm dependencies
- Component changes should follow existing patterns in the codebase
- SCSS styling should match existing design language

## Deliverables
- [ ] `message` field type fixed to optional
- [ ] `setQueuePaused()` dead code removed
- [ ] `currentObserver` unused field removed
- [ ] `source` and `cancelled_at` displayed in job detail drawer
- [ ] `job_metadata` displayed when present
- [ ] No TypeScript compilation errors
