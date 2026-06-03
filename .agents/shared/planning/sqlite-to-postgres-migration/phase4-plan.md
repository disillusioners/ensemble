# Phase 4: Frontend Migration UI

## Objective

Create an Angular migration component in the settings menu that shows migration status, provides a start button, displays real-time progress with streaming logs, supports cancellation, and is only visible when Postgres ENV is configured and current DB is SQLite.

## Coupling

- **Depends on**: Phase 3 (needs migration API endpoints + API contract)
- **Coupling type**: loose
- **Shared files with other phases**: None (frontend-only phase)
- **Shared APIs/interfaces**: Consumes the 5 API endpoints defined in Phase 3's API Contract section
- **Why this coupling**: Only depends on the API contract (JSON schemas + SSE events), not implementation. Could be developed against a mock API in parallel with Phase 3.

## Context

- **Settings menu**: Data-driven via `settingsMenuItems` array in `app.ts` — add `{ label: 'Database Migration', icon: 'storage', route: '/migration' }`
- **Component pattern**: Standalone + `inject()` + `DestroyRef` + `takeUntilDestroyed()` + service signals
- **SSE pattern**: Native `EventSource` + `ngZone.run()` (see `JobSseService` for reference — exponential backoff, connection state machine)
- **Service pattern**: Domain service with `signal()` state + `HttpClient`
- **Styling**: Custom SCSS, dark theme (`$bg-primary: #0f172a`, `$accent-cyan: #10a7f7`)
- **Angular 17+ control flow**: `@if`, `@for` (no `*ngIf`, `*ngFor`)

## API Contract Reference

All types are defined in **Phase 3 → API Contract** section. Key interfaces for frontend:

- `MigrationAvailability` — controls menu visibility and start button state
- `MigrationStatusResponse` — current migration state
- `MigrationSSEEvent` — streaming events (progress, log, complete, error, cancelled)
- `MigrationStartResponse` — response from POST /start
- `MigrationCancelResponse` — response from POST /cancel

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `MigrationService` | Domain service with: `availability()` signal, `status()` signal, `logs()` signal, `progressEvents()` signal. HTTP methods: `checkAvailability()`, `fetchStatus()`, `startMigration()`, `cancelMigration()`. SSE: `connectEvents()`, `disconnectEvents()`. | `frontend/src/app/services/migration.service.ts` (NEW) |
| 2 | Create migration component | Standalone component:OnInit loads availability + status. Shows: info section, start/cancel buttons, progress bar, table checklist, log viewer. Uses Angular 17+ `@if`/`@for` control flow. | `frontend/src/app/components/migration/migration.component.ts` (NEW) |
| 3 | Create migration template | Layout: status badge (idle/running/completed/failed/cancelled), current vs target DB, start button (disabled when not eligible or running), cancel button (visible only when running), progress bar with percentage, table checklist with row counts, auto-scrolling log viewer. | `frontend/src/app/components/migration/migration.component.html` (NEW) |
| 4 | Create migration styles | Dark theme SCSS. Progress bar with cyan accent. Log viewer monospace. Status badges: idle=gray, running=cyan+spinner, completed=green, failed=red, cancelled=amber. Restart prompt on completion. | `frontend/src/app/components/migration/migration.component.scss` (NEW) |
| 5 | Add route | Add `/migration` route with lazy-loaded `MigrationComponent` | `frontend/src/app/app.routes.ts` |
| 6 | Conditional settings menu item | Fetch `/api/migration/availability` on app init. If `migration_available === true`, push `{ label: 'Database Migration', icon: 'storage', route: '/migration' }` to `settingsMenuItems`. | `frontend/src/app/app.ts` |
| 7 | Export service from barrel | Add `MigrationService` to `services/index.ts` barrel export | `frontend/src/app/services/index.ts` |

## Key Files

### New Files
- `frontend/src/app/services/migration.service.ts` — Migration API + SSE client
- `frontend/src/app/components/migration/migration.component.ts` — Component logic
- `frontend/src/app/components/migration/migration.component.html` — Template
- `frontend/src/app/components/migration/migration.component.scss` — Styles

### Modified Files
- `frontend/src/app/app.ts` — Conditional menu item in `settingsMenuItems`
- `frontend/src/app/app.routes.ts` — Add `/migration` route
- `frontend/src/app/services/index.ts` — Export `MigrationService`

## MigrationService Design

```typescript
@Injectable({ providedIn: 'root' })
export class MigrationService {
  private readonly http = inject(HttpClient);
  private readonly ngZone = inject(NgZone);
  private readonly API_BASE = '/api/migration';
  private eventSource: EventSource | null = null;

  // State signals (consumed by component)
  readonly availability = signal<MigrationAvailability | null>(null);
  readonly status = signal<MigrationStatusResponse | null>(null);
  readonly logs = signal<MigrationLogEvent[]>([]);
  readonly currentProgress = signal<MigrationProgressEvent | null>(null);
  readonly isMigrating = computed(() =>
    this.status()?.status === 'running'
  );
  readonly isComplete = computed(() =>
    this.status()?.status === 'completed'
  );

  // API methods
  checkAvailability(): Observable<MigrationAvailability> {
    return this.http.get<MigrationAvailability>(`${this.API_BASE}/availability`)
      .pipe(tap(avail => this.availability.set(avail)));
  }

  fetchStatus(): Observable<MigrationStatusResponse> {
    return this.http.get<MigrationStatusResponse>(`${this.API_BASE}/status`)
      .pipe(tap(status => this.status.set(status)));
  }

  startMigration(): Observable<MigrationStartResponse> {
    return this.http.post<MigrationStartResponse>(`${this.API_BASE}/start`, {})
      .pipe(tap(() => this.connectEvents()));
  }

  cancelMigration(): Observable<MigrationCancelResponse> {
    return this.http.post<MigrationCancelResponse>(`${this.API_BASE}/cancel`, {});
  }

  // SSE methods (following JobSseService pattern)
  connectEvents(): void {
    this.disconnectEvents();
    this.eventSource = new EventSource(`${this.API_BASE}/events`);

    this.eventSource.addEventListener('progress', (e: MessageEvent) => {
      this.ngZone.run(() => {
        const data = JSON.parse(e.data) as MigrationProgressEvent;
        this.currentProgress.set(data);
      });
    });

    this.eventSource.addEventListener('log', (e: MessageEvent) => {
      this.ngZone.run(() => {
        const data = JSON.parse(e.data) as MigrationLogEvent;
        this.logs.update(logs => [...logs, data]);
      });
    });

    this.eventSource.addEventListener('complete', (e: MessageEvent) => {
      this.ngZone.run(() => {
        const data = JSON.parse(e.data) as MigrationCompleteEvent;
        this.status.update(s => s ? { ...s, status: 'completed' } : s);
        this.disconnectEvents();
      });
    });

    this.eventSource.addEventListener('error', (e: MessageEvent) => {
      this.ngZone.run(() => {
        const data = JSON.parse(e.data) as MigrationErrorEvent;
        this.status.update(s => s ? { ...s, status: 'failed', error: data.error } : s);
        this.disconnectEvents();
      });
    });

    this.eventSource.addEventListener('cancelled', (e: MessageEvent) => {
      this.ngZone.run(() => {
        this.status.update(s => s ? { ...s, status: 'cancelled' } : s);
        this.disconnectEvents();
      });
    });

    this.eventSource.onerror = () => {
      this.ngZone.run(() => this.disconnectEvents());
    };
  }

  disconnectEvents(): void {
    this.eventSource?.close();
    this.eventSource = null;
  }
}
```

## Component UI Layout

```
┌─────────────────────────────────────────────────────┐
│  Database Migration                                  │
│                                                      │
│  Current: SQLite ●            Target: PostgreSQL ●   │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │  [▶ Start Migration]    [✕ Cancel] (running) │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  ═══════════════════════════════════════════════     │
│  Phase: Migrating tables (12/22)                     │
│  ████████████████████████░░░░░░░░░░░░  54%          │
│                                                      │
│  ☑ projects (245 rows)                              │
│  ☑ instances (1,523 rows)                           │
│  ☑ job_queues (3 rows)                              │
│  ◉ job_queue_items — in progress (500/1,200)        │
│  ○ message_queue                                    │
│  ○ checkpoints                                      │
│                                                      │
│  ═══════════════════════════════════════════════     │
│  Migration Log:                                      │
│  [23:15:01] Starting migration...                    │
│  [23:15:01] Pausing writes...                        │
│  [23:15:01] Creating PostgreSQL schema...            │
│  [23:15:02] Migrating table projects (245 rows)      │
│  [23:15:02] ✓ Completed projects                     │
│  ...                                                 │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │  ⚠ Migration complete. Restart daemon to     │   │
│  │    use PostgreSQL.                            │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

## Conditional Visibility Logic

```typescript
// app.ts
readonly migrationAvailable = signal(false);

ngOnInit(): void {
  this.loadHealth();
  this.checkMigrationAvailability();
}

private checkMigrationAvailability(): void {
  this.http.get<MigrationAvailability>('/api/migration/availability')
    .pipe(takeUntilDestroyed(this.destroyRef))
    .subscribe({
      next: (avail) => {
        this.migrationAvailable.set(avail.migration_available);
        if (avail.migration_available) {
          this.settingsMenuItems = [
            ...this.settingsMenuItems,
            { label: 'Database Migration', icon: 'storage', route: '/migration' }
          ];
        }
      },
      error: () => {} // Silently ignore — feature not available
    });
}
```

Menu is NOT shown when:
- Current DB is already PostgreSQL
- Postgres ENV vars not set
- Migration not available for any reason

## Restart Prompt on Completion

When migration completes (`status === 'completed'`), show a prominent banner:

```
┌────────────────────────────────────────────────────┐
│  ✅ Migration completed successfully!              │
│                                                     │
│  To start using PostgreSQL, restart the daemon:     │
│  1. Stop the daemon process                         │
│  2. Start the daemon again                          │
│  3. Verify it connects to PostgreSQL                │
│                                                     │
│  To rollback: edit ensemble.json to "sqlite"        │
└────────────────────────────────────────────────────┘
```

## Constraints

- Menu item only visible when `migration_available === true`
- Start button disabled during migration and when already on PostgreSQL
- Cancel button only visible when `status === 'running'`
- Log viewer auto-scrolls to bottom on new entries
- Component must handle SSE disconnection gracefully
- No page refresh needed — all state reactive via signals
- Must follow existing dark theme color palette
- Use `<mat-icon>storage</mat-icon>` for the menu icon

## Deliverables

- [ ] `MigrationService` with availability check, status, start, cancel, SSE streaming
- [ ] Migration component with status display, start/cancel buttons, progress, table checklist, logs
- [ ] Settings menu item conditionally visible (only when eligible)
- [ ] Route `/migration` registered
- [ ] SSE auto-connect on migration start, auto-disconnect on terminal state
- [ ] Restart prompt shown on migration completion
- [ ] Dark theme styling consistent with existing UI
- [ ] Export from `services/index.ts` barrel
