# Phase 5: Usage History + Trigger Management (Component Build)

## Objective
Build two new standalone capabilities: (1) a paginated usage history table showing individual skill invocations with outcomes, and (2) a trigger management interface for viewing/creating/editing/deleting skill evolution triggers. **This phase builds standalone components only — integration into `skill-detail.component.html` and routes happens in Phase 6.**

## Coupling
- **Depends on**: Phase 2 (tight — imports `SkillUsageRecord`, `SkillTrigger` interfaces)
- **Coupling type**: tight
- **Shared files with other phases**: New component files only. **Does NOT modify `skill-detail.component.html` or `app.routes.ts`** (deferred to Phase 6).
- **Shared APIs/interfaces**: Consumes `SkillUsageRecord`, `SkillTrigger` from Phase 2
- **Why this coupling**: Must have the typed interfaces from Phase 2 before components can be built.
- **Parallel safety**: This phase creates only NEW files. Phases 3 and 4 also create only NEW files. All three can run in parallel without file conflicts.

## Context
- Phase 1 added `GET /api/skills/{id}/usage-records` endpoint
- Phase 2 added `SkillUsageRecord`, `SkillTrigger` interfaces + service methods
- Backend trigger endpoints already exist: `GET/POST/PUT/DELETE /api/skills/triggers`
- **[S3]** Trigger model uses `condition_type` and `condition_json` — NOT `trigger_type`/`trigger_config` (verified from source)

## [S3] Trigger Config Schemas (verified from backend)

The backend `SkillTrigger` model (`daemon/repositories/skill/models.py:447-512`) uses:
- `condition_type` (str) — discriminator for config shape
- `condition_json` (dict/JSONB) — free-form config, validated only at evaluation time
- `action` (str) — free-form action token

### Valid Condition Types (5 built-in evaluators)

| `condition_type` | Fires when | Config Schema (`condition_json`) | Engine Line |
|---|---|---|---|
| `low_completion_rate` | `completion_rate < threshold` AND `total_selections >= min_selections` | `{ threshold: float = 0.3, min_selections: int = 5 }` | `skill_trigger_engine.py:343-377` |
| `high_fallback_rate` | `fallback_rate > threshold` AND `total_selections >= min_selections` | `{ threshold: float = 0.5, min_selections: int = 5 }` | `skill_trigger_engine.py:379-410` |
| `consecutive_failures` | `skill.consecutive_failures >= threshold` | `{ threshold: int = 3 }` | `skill_trigger_engine.py:412-435` |
| `task_count_scan` | `skill.total_selections >= threshold` | `{ threshold: int = 20 }` | `skill_trigger_engine.py:437-462` |
| `periodic_scan` | `last_used_at` older than `interval_days` | `{ interval_days: int = 7 }` | `skill_trigger_engine.py:464-507` |

### Valid Action Values
| Action | Effect |
|---|---|
| `"analyze"` | Enqueues `skill_analysis` job |
| `"evolve_fix"` | Enqueues `skill_evolution` job |
| Any other string | Forwarded as opaque token (no engine action) |

**Note**: The router accepts ANY `condition_type` string and ANY `condition_json` dict (no validation). Unknown types store fine but are silently skipped by the engine. The FE form should restrict selection to the 5 built-in types above.

## Tasks

### Part A: Usage History Table

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `SkillUsageTableComponent` | Standalone component. Input: `skillId: input.required<string>()`. Fetches paginated records via `skill.service.getUsageRecords()`. Renders Material table with pagination. | `frontend/src/app/components/skill-usage-table/skill-usage-table.component.ts/html/scss` — **NEW** |
| 2 | Define table columns | Columns: Timestamp, Agent, Task (truncated), Selected ✓/✗, Applied ✓/✗, Success ✓/✗, Iterations, Duration, Fallback ✓/✗. | Inside component template |
| 3 | Implement pagination | Use `MatPaginator` with `[length]`, `[pageSize]`, `[pageSizeOptions]`. On page change, fetch new offset. Show total count from `total` field. | Inside component |
| 4 | Add row detail expansion | Clickable row expands to show full details: `task_message`, `feedback_applied`, `feedback_note`, `ab_test_group`, `instance_id`. | Inside component |

### Part B: Trigger Management

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 5 | Create `SkillTriggerListComponent` | Standalone component. Fetches all triggers via `skill.service.listTriggers()`. Renders list of trigger cards. | `frontend/src/app/components/skill-trigger-list/skill-trigger-list.component.ts/html/scss` — **NEW** |
| 6 | **[S3] Create trigger form dialog with dynamic config schema** | Material dialog for creating/editing triggers. Fields: `name`, `condition_type` (select restricted to 5 built-in types), `condition_json` (dynamic form fields based on selected type — see schema table above), `action` (select: analyze/evolve_fix), `is_enabled`. | `frontend/src/app/components/skill-trigger-form/skill-trigger-form.component.ts/html/scss` — **NEW** |
| 7 | Implement trigger CRUD | Wire up: Create (open dialog → POST), Edit (open dialog pre-filled → PUT), Delete (confirm dialog → DELETE), Toggle enabled (PUT with `is_enabled` flipped). | Inside `SkillTriggerListComponent` |

> **Note**: Route addition (`/skills/triggers`) and integration into skills page / skill-detail page are deferred to Phase 6.

## Key Files

### New Components
- `frontend/src/app/components/skill-usage-table/skill-usage-table.component.ts/html/scss` — **NEW**
- `frontend/src/app/components/skill-trigger-list/skill-trigger-list.component.ts/html/scss` — **NEW**
- `frontend/src/app/components/skill-trigger-form/skill-trigger-form.component.ts/html/scss` — **NEW** (dialog)

### Reference Files
- `frontend/src/app/services/skill.service.ts` — Uses methods from Phase 2

> **Note**: `skill-detail.component.html`, `skills.component.html`, and `app.routes.ts` are NOT modified in this phase. Integration happens in Phase 6.

## Component Design

### SkillUsageTableComponent
```typescript
@Component({
  selector: 'app-skill-usage-table',
  standalone: true,
  imports: [
    MatTableModule, MatPaginatorModule, MatIconModule,
    MatExpansionModule, MatChipsModule, MatProgressSpinnerModule,
  ],
  template: `
    <div class="usage-table-container">
      @if (loading()) {
        <mat-spinner diameter="36" />
      } @else if (records().length === 0) {
        <p class="no-records">No usage records yet.</p>
      } @else {
        <mat-table [dataSource]="records()">
          <ng-container matColumnDef="timestamp">
            <mat-header-cell *matHeaderCellDef>When</mat-header-cell>
            <mat-cell *matCellDef="let r">{{ r.created_at | date:'short' }}</mat-cell>
          </ng-container>
          <ng-container matColumnDef="agent">
            <mat-header-cell *matHeaderCellDef>Agent</mat-header-cell>
            <mat-cell *matCellDef="let r">{{ r.agent_id }}</mat-cell>
          </ng-container>
          <ng-container matColumnDef="task">
            <mat-header-cell *matHeaderCellDef>Task</mat-header-cell>
            <mat-cell *matCellDef="let r">{{ r.task_message | slice:0:50 }}…</mat-cell>
          </ng-container>
          <ng-container matColumnDef="applied">
            <mat-header-cell *matHeaderCellDef>Applied</mat-header-cell>
            <mat-cell *matCellDef="let r">
              <mat-icon [class.applied-icon]="r.applied">
                {{ r.applied ? 'check_circle' : 'cancel' }}
              </mat-icon>
            </mat-cell>
          </ng-container>
          <ng-container matColumnDef="success">
            <mat-header-cell *matHeaderCellDef>Success</mat-header-cell>
            <mat-cell *matCellDef="let r">
              <mat-icon [class.success-icon]="r.task_succeeded">
                {{ r.task_succeeded ? 'check' : 'close' }}
              </mat-icon>
            </mat-cell>
          </ng-container>
          <ng-container matColumnDef="iterations">
            <mat-header-cell *matHeaderCellDef>Iters</mat-header-cell>
            <mat-cell *matCellDef="let r">{{ r.iterations }}</mat-cell>
          </ng-container>
          <ng-container matColumnDef="duration">
            <mat-header-cell *matHeaderCellDef>Duration</mat-header-cell>
            <mat-cell *matCellDef="let r">{{ r.duration_seconds.toFixed(1) }}s</mat-cell>
          </ng-container>
          <mat-header-row *matHeaderRowDef="displayedColumns"></mat-header-row>
          <mat-row *matRowDef="let r; columns: displayedColumns;"
                   (click)="toggleExpand(r)"></mat-row>
        </mat-table>

        <mat-paginator [length]="total()" [pageSize]="pageSize"
                       [pageSizeOptions]="[10, 25, 50]"
                       (page)="onPageChange($event)" />
      }
    </div>
  `,
})
export class SkillUsageTableComponent {
  skillId = input.required<string>();

  records = signal<SkillUsageRecord[]>([]);
  total = signal(0);
  loading = signal(false);
  expandedRow = signal<SkillUsageRecord | null>(null);

  displayedColumns = ['timestamp', 'agent', 'task', 'applied', 'success', 'iterations', 'duration'];
  pageSize = 25;
  offset = 0;

  constructor(private skillService: SkillService) {}

  ngOnInit() { this.loadData(); }

  loadData() {
    this.loading.set(true);
    this.skillService.getUsageRecords(this.skillId(), this.pageSize, this.offset).subscribe({
      next: (res) => {
        this.records.set(res.records);
        this.total.set(res.total);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  onPageChange(event: PageEvent) {
    this.offset = event.pageIndex * event.pageSize;
    this.pageSize = event.pageSize;
    this.loadData();
  }

  toggleExpand(record: SkillUsageRecord) {
    this.expandedRow.set(
      this.expandedRow()?.id === record.id ? null : record
    );
  }
}
```

### SkillTriggerListComponent
```typescript
@Component({
  selector: 'app-skill-trigger-list',
  standalone: true,
  imports: [
    MatCardModule, MatButtonModule, MatIconModule, MatDialogModule,
    MatSlideToggleModule, MatProgressSpinnerModule,
  ],
  template: `
    <div class="trigger-list">
      <div class="header">
        <h2>Evolution Triggers</h2>
        <button mat-flat-button color="primary" (click)="openCreateDialog()">
          <mat-icon>add</mat-icon> New Trigger
        </button>
      </div>

      @if (loading()) {
        <mat-spinner diameter="36" />
      } @else {
        @for (trigger of triggers(); track trigger.id) {
          <mat-card class="trigger-card">
            <mat-card-header>
              <mat-card-title>{{ trigger.name }}</mat-card-title>
              <mat-card-subtitle>{{ trigger.condition_type }}</mat-card-subtitle>
            </mat-card-header>
            <mat-card-content>
              <div class="config-display">
                <span class="label">Config:</span>
                <pre>{{ trigger.condition_json | json }}</pre>
              </div>
              <div class="action-display">
                <span class="label">Action:</span>
                <code>{{ trigger.action }}</code>
              </div>
            </mat-card-content>
            <mat-card-actions>
              <mat-slide-toggle [checked]="trigger.is_enabled"
                (change)="toggleEnabled(trigger)">Enabled</mat-slide-toggle>
              <button mat-button (click)="openEditDialog(trigger)">Edit</button>
              <button mat-button color="warn" (click)="deleteTrigger(trigger)">Delete</button>
            </mat-card-actions>
          </mat-card>
        } @empty {
          <p class="no-triggers">No triggers configured.</p>
        }
      }
    </div>
  `,
})
export class SkillTriggerListComponent {
  triggers = signal<SkillTrigger[]>([]);
  loading = signal(false);

  constructor(
    private skillService: SkillService,
    private dialog: MatDialog,
  ) {}

  ngOnInit() { this.loadTriggers(); }

  loadTriggers() {
    this.loading.set(true);
    this.skillService.listTriggers().subscribe({
      next: (triggers) => {
        this.triggers.set(triggers);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  openCreateDialog() {
    const ref = this.dialog.open(SkillTriggerFormComponent);
    ref.afterClosed().subscribe(result => {
      if (result) this.loadTriggers();
    });
  }

  openEditDialog(trigger: SkillTrigger) {
    const ref = this.dialog.open(SkillTriggerFormComponent, {
      data: { trigger },
    });
    ref.afterClosed().subscribe(result => {
      if (result) this.loadTriggers();
    });
  }

  toggleEnabled(trigger: SkillTrigger) {
    this.skillService.updateTrigger(trigger.id, {
      ...trigger,
      is_enabled: !trigger.is_enabled,
    }).subscribe(() => this.loadTriggers());
  }

  deleteTrigger(trigger: SkillTrigger) {
    // Show confirmation dialog, then delete
    if (confirm(`Delete trigger "${trigger.name}"?`)) {
      this.skillService.deleteTrigger(trigger.id).subscribe(() => this.loadTriggers());
    }
  }
}
```

### [S3] SkillTriggerFormComponent (Dynamic Config Schema)
```typescript
@Component({
  selector: 'app-skill-trigger-form',
  standalone: true,
  imports: [MatDialogModule, MatFormFieldModule, MatSelectModule, MatInputModule, MatSlideToggleModule],
  template: `
    <h2 mat-dialog-title>{{ isEdit() ? 'Edit' : 'Create' }} Trigger</h2>
    <mat-dialog-content>
      <mat-form-field>
        <mat-label>Name</mat-label>
        <input matInput [formControl]="nameControl" />
      </mat-form-field>

      <mat-form-field>
        <mat-label>Condition Type</mat-label>
        <mat-select [formControl]="conditionTypeControl">
          @for (ct of conditionTypes; track ct.value) {
            <mat-option [value]="ct.value">{{ ct.label }}</mat-option>
          }
        </mat-select>
      </mat-form-field>

      <!-- Dynamic config fields based on selected condition_type -->
      @switch (conditionTypeControl.value) {
        @case ('low_completion_rate') {
          <div class="config-fields">
            <mat-form-field><mat-label>Threshold (0-1)</mat-label>
              <input matInput type="number" step="0.1" min="0" max="1" [formControl]="thresholdControl" />
            </mat-form-field>
            <mat-form-field><mat-label>Min Selections</mat-label>
              <input matInput type="number" [formControl]="minSelectionsControl" />
            </mat-form-field>
          </div>
        }
        @case ('high_fallback_rate') {
          <!-- Same fields: threshold + min_selections -->
        }
        @case ('consecutive_failures') {
          <mat-form-field><mat-label>Threshold (failures)</mat-label>
            <input matInput type="number" [formControl]="thresholdControl" />
          </mat-form-field>
        }
        @case ('task_count_scan') {
          <mat-form-field><mat-label>Threshold (task count)</mat-label>
            <input matInput type="number" [formControl]="thresholdControl" />
          </mat-form-field>
        }
        @case ('periodic_scan') {
          <mat-form-field><mat-label>Interval (days)</mat-label>
            <input matInput type="number" [formControl]="intervalDaysControl" />
          </mat-form-field>
        }
      }

      <mat-form-field>
        <mat-label>Action</mat-label>
        <mat-select [formControl]="actionControl">
          <mat-option value="analyze">Analyze</mat-option>
          <mat-option value="evolve_fix">Evolve (Fix)</mat-option>
        </mat-select>
      </mat-form-field>

      <mat-slide-toggle [formControl]="isEnabledControl">Enabled</mat-slide-toggle>
    </mat-dialog-content>
    <mat-dialog-actions>
      <button mat-button (click)="cancel()">Cancel</button>
      <button mat-flat-button color="primary" (click)="save()"
              [disabled]="!nameControl.valid">Save</button>
    </mat-dialog-actions>
  `,
})
export class SkillTriggerFormComponent {
  // [S3] Condition types restricted to the 5 built-in evaluators
  conditionTypes = [
    { value: 'low_completion_rate', label: 'Low Completion Rate' },
    { value: 'high_fallback_rate', label: 'High Fallback Rate' },
    { value: 'consecutive_failures', label: 'Consecutive Failures' },
    { value: 'task_count_scan', label: 'Task Count Scan' },
    { value: 'periodic_scan', label: 'Periodic Scan' },
  ];

  // FormControls for each field
  // On save, build condition_json based on selected type:
  //   low_completion_rate / high_fallback_rate → { threshold, min_selections }
  //   consecutive_failures / task_count_scan → { threshold }
  //   periodic_scan → { interval_days }
}
```

## Constraints
- Usage table must handle empty state ("No usage records yet")
- Trigger form dialog must restrict `condition_type` to the 5 built-in types (other types store but never fire)
- Trigger form must dynamically render config fields based on selected `condition_type` ([S3])
- Delete trigger must show confirmation dialog
- Pagination on usage table must be server-side (using `limit`/`offset` from API)
- All new components use Angular standalone + signals pattern
- Trigger interfaces use `condition_type` / `condition_json` / `is_enabled` — NOT `trigger_type` / `trigger_config` / `is_active`

## Testing Strategy
- Unit test usage table pagination logic (offset calculation)
- Component test: usage table loads data, handles empty/error states
- Component test: trigger CRUD (create dialog, edit, delete confirmation)
- Component test: trigger form dynamic fields appear/disappear based on `condition_type` selection
- Integration test: trigger list renders correctly with triggers from API

## Deliverables
- [ ] `SkillUsageTableComponent` created with pagination + row expansion
- [ ] `SkillTriggerListComponent` created with trigger cards
- [ ] **[S3]** `SkillTriggerFormComponent` (dialog) created with dynamic config schema per condition_type
- [ ] Trigger CRUD fully wired (create, edit, delete, toggle)
- [ ] Empty/loading/error states for all components
- [ ] Component tests passing
- [ ] `ng build` compiles

> **Integration into skill-detail page, routes, and skills page is deferred to Phase 6.**
