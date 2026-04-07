# Phase 4: Frontend — Schema Alignment & Cleanup

## Objective
Align the frontend TypeScript types with the updated backend API schema (Phase 2), fix type mismatches, remove dead code (duplicate methods, unused fields and interfaces), and ensure the frontend correctly displays job data including source, metadata, and cancellation timestamps.

## Coupling
- **Depends on**: Phase 2 (needs final API contract with new fields)
- **Coupling type**: loose — depends on the schema interface defined in Phase 2, not its implementation
- **Shared files with other phases**: None (frontend files only)
- **Why loose**: Phase 2 defines which fields the API returns. This phase aligns the frontend to match. Can start before Phase 2 is fully merged if the interface is agreed upon.

## Context

### Current Mismatches (Post Phase 2)

After Phase 2 adds `source`, `job_metadata`, `cancelled_at` to the backend API response:

| Issue | File | Fix |
|-------|------|-----|
| `source: JobSource` expected but was never returned | `job.model.ts` | ✅ Now returned by API — no frontend model change needed |
| `job_metadata: Record<string, any>` expected but was never returned | `job.model.ts` | ✅ Now returned as `job_metadata` — verify name match |
| `cancelled_at: string \| null` expected but was never returned | `job.model.ts` | ✅ Now returned — no frontend model change needed |
| `message: string` required but API returns optional | `job.model.ts` | Make optional: `message?: string` |
| `agent_dir` returned by API but not in frontend | N/A | ✅ OK — implementation detail, frontend doesn't need it |
| `setQueuePaused()` dead code in project service | `project.service.ts` | Remove dead method |
| `currentObserver` unused field + `Observer<T>` interface in SSE service | `job-sse.service.ts` | Remove both the field and the interface |

### What Works (No Changes Needed)
- `job.service.ts` — HTTP methods match API endpoints correctly
- `job-sse.service.ts` — SSE handling works (just remove unused code)
- `jobs.component.ts` — Main page logic correct
- All component templates display existing fields correctly

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Fix `message` field type | Change from required `string` to optional to match backend `Optional[str]` | `frontend/src/app/models/job.model.ts` |
| 2 | Remove `setQueuePaused()` dead method | Delete the unused method from ProjectService (only `pauseJobQueue()` and `resumeJobQueue()` are used) | `frontend/src/app/services/project.service.ts` |
| 3 | Remove `currentObserver` field AND `Observer<T>` interface | Delete the unused private field at line ~21 and the `Observer<T>` interface at lines ~256-260 that only exists for it | `frontend/src/app/services/job-sse.service.ts` |
| 4 | Verify field name alignment | Confirm `job_metadata` matches between frontend and backend (no change expected) | `frontend/src/app/models/job.model.ts` |
| 5 | Display new fields in job components | Show `source` badge, `cancelled_at` timestamp, and `job_metadata` in job detail drawer | `frontend/src/app/components/job-detail-drawer/` |

## Detailed Implementation

### Task 1: Fix `message` Field Type

**File**: `frontend/src/app/models/job.model.ts`

```typescript
// Before:
message: string;

// After:
message?: string;
```

This aligns with the backend `Optional[str]` type in `JobResponse`. The backend may return `null` for `message` in some cases.

### Task 2: Remove Dead `setQueuePaused()` Method

**File**: `frontend/src/app/services/project.service.ts`

Remove the `setQueuePaused(projectId: string, paused: boolean)` method entirely. Keep `pauseJobQueue()` and `resumeJobQueue()` which are actually used by `jobs.component.ts`.

**Before removing, verify no callers exist**:
```bash
grep -r "setQueuePaused" frontend/src/
```
Expected result: only the method definition itself, no callers.

### Task 3: Remove `currentObserver` AND `Observer<T>` Interface

**File**: `frontend/src/app/services/job-sse.service.ts`

**Remove two things**:

1. **The unused private field** (~line 21):
```typescript
// Remove:
private currentObserver: Observer<JobEvent> | null = null;
```

2. **The `Observer<T>` interface** (~lines 256-260) — this interface only exists to type `currentObserver`. With `currentObserver` removed, the interface is also dead code:
```typescript
// Remove the entire interface:
interface Observer<T> {
  next: (value: T) => void;
  error: (err: any) => void;
  complete: () => void;
}
```

**Note**: The service uses local `observer` variables inside methods (typed inline or inferred), not this `currentObserver` field. No other code references the `Observer<T>` interface.

### Task 4: Verify Field Name Alignment

Check that `job_metadata` in the frontend model matches the backend response field name.

**Backend** (Phase 2 adds): `job_metadata: Optional[dict[str, Any]]`
**Frontend** (existing): `job_metadata: Record<string, any>`

✅ Names match — no change needed. Just verify after Phase 2 is complete.

### Task 5: Display New Fields in Job Detail Drawer

**File**: `frontend/src/app/components/job-detail-drawer/job-detail-drawer.component.html`

Add display for `source`, `cancelled_at`, and `job_metadata`:

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

## Key Files
- `frontend/src/app/models/job.model.ts` — Type fix for `message` field
- `frontend/src/app/services/project.service.ts` — Remove dead `setQueuePaused()` method
- `frontend/src/app/services/job-sse.service.ts` — Remove `currentObserver` field AND `Observer<T>` interface
- `frontend/src/app/components/job-detail-drawer/` — Display new fields (`source`, `cancelled_at`, `job_metadata`)

## Constraints
- All changes must be backward compatible with existing Angular version
- No new npm dependencies
- Component changes should follow existing patterns in the codebase
- SCSS styling should match existing design language
- Use function/method names as primary references (line numbers are approximate)

## Deliverables
- [ ] `message` field type fixed to optional in `job.model.ts`
- [ ] `setQueuePaused()` dead code removed from `project.service.ts`
- [ ] `currentObserver` field removed from `job-sse.service.ts`
- [ ] `Observer<T>` interface removed from `job-sse.service.ts`
- [ ] `source`, `cancelled_at`, and `job_metadata` displayed in job detail drawer
- [ ] No TypeScript compilation errors
