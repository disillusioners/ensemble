# Phase 5: FE — UI Components (Delete Button & Show Deleted Filter)

## Objective
Add a delete button to each job card for terminal-status jobs, a "Show Deleted" checkbox in the filter bar, and visual distinction for deleted jobs.

## Coupling
- **Depends on**: Phase 4 (service methods and model must be available)
- **Coupling type**: tight — calls `JobService.softDeleteJob()` and uses `Job.deleted_at`
- **Shared files with other phases**: Multiple component files
- **Shared APIs/interfaces**: `Job` interface, `JobService`, output events
- **Why this coupling**: UI components directly call service methods and read model fields.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add delete output to `JobCardComponent` | Add `delete = output<void>()` and `canDelete` computed (terminal status + not already deleted). Add delete button in the actions area. | `frontend/src/app/components/job-card/job-card.component.ts`, `job-card.component.html` |
| 2 | Style deleted jobs in `JobCardComponent` | Add CSS class `job-deleted` when `job().deleted_at` is set. Apply: reduced opacity, strikethrough on message, "Deleted" badge with timestamp. | `frontend/src/app/components/job-card/job-card.component.html`, `job-card.component.scss` |
| 3 | Handle `onDeleteJob` in `JobsComponent` | Wire `(delete)="onDeleteJob(job)"` on `<app-job-card>`. Call `jobService.softDeleteJob()`. Show snackbar confirmation. Remove job from list (or update state). | `frontend/src/app/pages/jobs/jobs.component.ts`, `jobs.component.html` |
| 4 | Add "Show Deleted" checkbox to filter bar | Add a `MatCheckbox` in the filter bar area. Controls `filters().include_deleted`. When toggled, reloads jobs with the updated filter. | `frontend/src/app/pages/jobs/jobs.component.html`, `jobs.component.ts` |
| 5 | Add `include_deleted` signal to `JobsComponent` | Add `readonly showDeleted = signal(false)` and update `onToggleShowDeleted()` handler. Update `loadJobs()` to pass `include_deleted`. | `frontend/src/app/pages/jobs/jobs.component.ts` |
| 6 | Add "Deleted" status badge styling | In the job card, when `deleted_at` is set, show a "Deleted" badge alongside the status chip. Show relative time since deletion. | `frontend/src/app/components/job-card/job-card.component.html` |
| 7 | Hide action buttons for deleted jobs | When job is deleted, hide Cancel/Retry buttons. Optionally show a "Restore" button. | `frontend/src/app/components/job-card/job-card.component.html`, `job-card.component.ts` |

## Key Files
- `frontend/src/app/components/job-card/job-card.component.ts` — Add delete output, canDelete computed, canRestore computed
- `frontend/src/app/components/job-card/job-card.component.html` — Delete button, deleted badge, conditional actions
- `frontend/src/app/components/job-card/job-card.component.scss` — Deleted styling
- `frontend/src/app/pages/jobs/jobs.component.ts` — onDeleteJob handler, showDeleted signal, filter toggle
- `frontend/src/app/pages/jobs/jobs.component.html` — "Show Deleted" checkbox, wire delete event

## Constraints
- Follow existing Angular Material patterns
- Use signals for state management (no NgRx or RxJS subjects for component state)
- Maintain dark theme compatibility
- The delete button should use `mat-icon` with `delete` icon and be styled with `color="warn"` (red accent)
- Deleted job cards should still be interactive (expand details, view) but not actionable (no cancel/retry)

## Detailed Implementation Reference

### `JobCardComponent` additions

```typescript
// New outputs
delete = output<void>();
restore = output<void>();

// New computed values
canDelete = computed(() => {
  const job = this.job();
  return isTerminalStatus(job.status) && !job.deleted_at;
});

isDeleted = computed(() => !!this.job().deleted_at);

canRestore = computed(() => !!this.job().deleted_at);

protected onDelete(): void {
  this.delete.emit();
}

protected onRestore(): void {
  this.restore.emit();
}
```

### Delete button in template (in `mat-card-actions`)

```html
@if (canDelete()) {
  <button mat-button color="warn" (click)="onDelete()">
    <mat-icon>delete</mat-icon>
    Delete
  </button>
}
@if (canRestore()) {
  <button mat-button color="primary" (click)="onRestore()">
    <mat-icon>restore</mat-icon>
    Restore
  </button>
}
```

### "Show Deleted" checkbox in filter bar

```html
<div class="filter-row">
  <!-- existing filters ... -->
  <mat-checkbox 
    [checked]="showDeleted()"
    (change)="onToggleShowDeleted($event.checked)">
    Show Deleted
  </mat-checkbox>
</div>
```

### `JobsComponent` handler

```typescript
readonly showDeleted = signal(false);

protected onToggleShowDeleted(checked: boolean): void {
  this.showDeleted.set(checked);
  this.filters.update(filters => ({
    ...filters,
    include_deleted: checked || undefined
  }));
  this.loadJobs();
}

protected onDeleteJob(job: Job): void {
  this.jobService.softDeleteJob(job.job_id).subscribe({
    next: () => {
      this.snackBar.open('Job deleted', 'Undo', { duration: 5000 })
        .onAction().subscribe(() => {
          this.jobService.restoreJob(job.job_id).subscribe({
            next: () => this.loadJobs(),
            error: () => {}
          });
        });
      if (!this.showDeleted()) {
        // Remove from local list
        this.jobs.update(jobs => jobs.filter(j => j.job_id !== job.job_id));
      } else {
        // Update the job in place (show as deleted)
        this.jobs.update(jobs =>
          jobs.map(j => j.job_id === job.job_id ? { ...j, deleted_at: new Date().toISOString() } : j)
        );
      }
    },
    error: (err) => {
      this.snackBar.open(err.message || 'Failed to delete job', 'Dismiss', {
        duration: 5000,
        panelClass: 'error-snackbar'
      });
    }
  });
}
```

### Deleted job card styling

```scss
.job-deleted {
  opacity: 0.6;
  
  .message-preview {
    text-decoration: line-through;
  }
  
  .deleted-badge {
    background-color: rgba(156, 163, 175, 0.2);
    color: #9CA3AF;
    // badge styling similar to paused-badge
  }
}
```

## Deliverables
- [ ] Delete button visible on terminal-status job cards
- [ ] "Show Deleted" checkbox in filter bar
- [ ] Deleted jobs display with visual distinction (reduced opacity, strikethrough)
- [ ] "Deleted" badge with timestamp on deleted job cards
- [ ] Cancel/Retry buttons hidden for deleted jobs
- [ ] Optional "Restore" button on deleted jobs
- [ ] Undo snackbar after delete action
