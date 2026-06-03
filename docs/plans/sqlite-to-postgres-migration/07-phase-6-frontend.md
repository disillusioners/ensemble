# Phase 6: Frontend Settings Sub-page

> **Effort**: 4-6 hours
> **Priority**: Medium
> **Risk**: Low (UI-only, can be redesigned without data risk)

## Goal

Add a settings sub-page with the migration UI. Component connects to the backend via SSE for real-time progress. Conditional visibility based on current database type.

## Decisions

- **Location**: New `/settings` route with sub-navigation
- **Component pattern**: Standalone + `inject()` + `DestroyRef` + `takeUntilDestroyed()` (matches existing)
- **SSE**: Reuse `JobSseService` pattern with exponential backoff
- **Conditional visibility**: Show migration UI only when `database === "sqlite"`

## Changes

### 1. Settings Route

**File**: `frontend/src/app/app.routes.ts`

```typescript
export const routes: Routes = [
  // ... existing routes ...
  {
    path: 'settings',
    loadComponent: () =>
      import('./settings/settings.component').then((m) => m.SettingsComponent),
    children: [
      { path: '', redirectTo: 'general', pathMatch: 'full' },
      { path: 'general', loadComponent: () => import('./settings/general/general.component').then((m) => m.GeneralComponent) },
      { path: 'database', loadComponent: () => import('./settings/database/database.component').then((m) => m.DatabaseComponent) },
    ],
  },
];
```

### 2. Settings Component

**File**: `frontend/src/app/settings/settings.component.ts` (NEW)

```typescript
import { Component, ChangeDetectionStrategy, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { MatIconModule } from '@angular/material/icon';
import { MatListModule } from '@angular/material/list';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [RouterLink, RouterLinkActive, RouterOutlet, MatIconModule, MatListModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="settings-container">
      <nav class="settings-nav">
        <mat-nav-list>
          <a mat-list-item routerLink="general" routerLinkActive="active">
            <mat-icon matListItemIcon>settings</mat-icon>
            <span matListItemTitle>General</span>
          </a>
          <a mat-list-item routerLink="database" routerLinkActive="active">
            <mat-icon matListItemIcon>storage</mat-icon>
            <span matListItemTitle>Database</span>
          </a>
        </mat-nav-list>
      </nav>
      <main class="settings-content">
        <router-outlet />
      </main>
    </div>
  `,
  styles: [`
    .settings-container {
      display: grid;
      grid-template-columns: 240px 1fr;
      gap: 24px;
      padding: 24px;
      height: 100%;
    }
    .settings-nav {
      border-right: 1px solid rgba(255, 255, 255, 0.1);
    }
    .settings-content {
      overflow-y: auto;
    }
    .active {
      background: rgba(16, 167, 247, 0.1);
      border-left: 3px solid #10a7f7;
    }
  `],
})
export class SettingsComponent {}
```

### 3. Database Component

**File**: `frontend/src/app/settings/database/database.component.ts` (NEW)

```typescript
import {
  Component,
  ChangeDetectionStrategy,
  inject,
  signal,
  DestroyRef,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatIconModule } from '@angular/material/icon';

import { MigrationService } from './migration.service';
import { ConfigService } from '../../core/config.service';

@Component({
  selector: 'app-database-settings',
  standalone: true,
  imports: [
    MatCardModule,
    MatButtonModule,
    MatProgressBarModule,
    MatIconModule,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './database.component.html',
  styleUrl: './database.component.scss',
})
export class DatabaseComponent {
  private readonly migrationService = inject(MigrationService);
  private readonly configService = inject(ConfigService);
  private readonly destroyRef = inject(DestroyRef);

  // Current database type
  readonly currentDatabase = this.configService.database;

  // Migration state
  readonly migrationStatus = this.migrationService.status;
  readonly migrationProgress = this.migrationService.progress;
  readonly migrationError = this.migrationService.error;

  readonly isPostgres = this.currentDatabase === 'postgres';
  readonly isSqlite = this.currentDatabase === 'sqlite';

  startMigration(): void {
    if (!confirm(
      'This will migrate your database to PostgreSQL. ' +
      'The process cannot be interrupted. Continue?'
    )) {
      return;
    }

    this.migrationService
      .startMigration()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe();
  }
}
```

### 4. Database Template

**File**: `frontend/src/app/settings/database/database.component.html` (NEW)

```html
<div class="database-settings">
  <mat-card>
    <mat-card-header>
      <mat-card-title>Database Backend</mat-card-title>
      <mat-card-subtitle>Current: {{ currentDatabase() }}</mat-card-subtitle>
    </mat-card-header>

    <mat-card-content>
      @if (isSqlite()) {
        <div class="info-section">
          <mat-icon>info</mat-icon>
          <p>You're using SQLite. Migrate to PostgreSQL for better performance and concurrent access.</p>
        </div>

        @if (migrationStatus() === 'idle') {
          <button mat-raised-button color="primary" (click)="startMigration()">
            <mat-icon>upgrade</mat-icon>
            Migrate to PostgreSQL
          </button>
        }

        @if (migrationStatus() === 'running') {
          <div class="migration-progress">
            <h3>{{ migrationProgress().phase }}</h3>
            @if (migrationProgress().table) {
              <p>Migrating: <strong>{{ migrationProgress().table }}</strong></p>
            }
            <mat-progress-bar
              mode="determinate"
              [value]="getProgressPercent()"
            ></mat-progress-bar>
            <p>
              {{ migrationProgress().rows_migrated }} / {{ migrationProgress().rows_total }} rows
            </p>
            <p class="message">{{ migrationProgress().message }}</p>
          </div>
        }

        @if (migrationStatus() === 'completed') {
          <div class="success-section">
            <mat-icon color="primary">check_circle</mat-icon>
            <p>Migration completed successfully!</p>
            <button mat-button (click)="reload()">Reload Page</button>
          </div>
        }

        @if (migrationStatus() === 'failed') {
          <div class="error-section">
            <mat-icon color="warn">error</mat-icon>
            <p>Migration failed: {{ migrationError() }}</p>
            <button mat-button (click)="startMigration()">Retry</button>
          </div>
        }
      }

      @if (isPostgres()) {
        <div class="info-section">
          <mat-icon color="primary">check_circle</mat-icon>
          <p>You're using PostgreSQL. No migration needed.</p>
        </div>
      }
    </mat-card-content>
  </mat-card>
</div>
```

### 5. Migration Service

**File**: `frontend/src/app/settings/database/migration.service.ts` (NEW)

```typescript
import { Injectable, inject, signal, DestroyRef } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, Subject, filter, map, takeUntil, timer } from 'rxjs';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

export interface MigrationProgress {
  phase: string;
  table: string | null;
  status: 'idle' | 'running' | 'completed' | 'failed';
  rows_total: number;
  rows_migrated: number;
  message: string;
  timestamp: string;
}

@Injectable({ providedIn: 'root' })
export class MigrationService {
  private readonly http = inject(HttpClient);
  private readonly destroyRef = inject(DestroyRef);
  private readonly apiUrl = '/api/migration';
  private eventSource: EventSource | null = null;

  // Reactive state
  readonly status = signal<'idle' | 'running' | 'completed' | 'failed'>('idle');
  readonly progress = signal<MigrationProgress>({
    phase: '',
    table: null,
    status: 'idle',
    rows_total: 0,
    rows_migrated: 0,
    message: '',
    timestamp: '',
  });
  readonly error = signal<string | null>(null);

  startMigration(): Observable<{ status: string }> {
    this.status.set('running');
    this.error.set(null);

    // Connect to SSE for progress updates
    this.connectSSE();

    return this.http.post<{ status: string }>(`${this.apiUrl}/start`, {});
  }

  private connectSSE(): void {
    if (this.eventSource) {
      this.eventSource.close();
    }

    this.eventSource = new EventSource(`${this.apiUrl}/events`);

    this.eventSource.addEventListener('progress', (event) => {
      const data = JSON.parse((event as MessageEvent).data);
      this.progress.set(data);

      if (data.status === 'completed') {
        this.status.set('completed');
        this.disconnectSSE();
      } else if (data.status === 'failed') {
        this.status.set('failed');
        this.error.set(data.message);
        this.disconnectSSE();
      }
    });

    this.eventSource.addEventListener('error', () => {
      this.status.set('failed');
      this.error.set('Connection lost');
      this.disconnectSSE();
    });
  }

  private disconnectSSE(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
  }
}
```

### 6. Add Settings Menu Item

**File**: `frontend/src/app/app.component.ts`

```typescript
// Add to settingsMenuItems array
{
  label: 'Settings',
  icon: 'settings',
  route: '/settings',
},
```

## Testing

### Component Test: Database Settings

```typescript
// frontend/src/app/settings/database/database.component.spec.ts
import { TestBed } from '@angular/core/testing';
import { DatabaseComponent } from './database.component';
import { MigrationService } from './migration.service';
import { ConfigService } from '../../core/config.service';

describe('DatabaseComponent', () => {
  let component: DatabaseComponent;
  let migrationService: jasmine.SpyObj<MigrationService>;

  beforeEach(() => {
    const spy = jasmine.createSpyObj('MigrationService', ['startMigration'], {
      status: signal('idle'),
      progress: signal({ /* ... */ }),
      error: signal(null),
    });

    TestBed.configureTestingModule({
      imports: [DatabaseComponent],
      providers: [
        { provide: MigrationService, useValue: spy },
        { provide: ConfigService, useValue: { database: signal('sqlite') } },
      ],
    });

    component = TestBed.createComponent(DatabaseComponent).componentInstance;
    migrationService = TestBed.inject(MigrationService) as jasmine.SpyObj<MigrationService>;
  });

  it('shows migration button when database is sqlite', () => {
    expect(component.isSqlite()).toBe(true);
  });

  it('calls migration service on button click', () => {
    spyOn(window, 'confirm').and.returnValue(true);
    component.startMigration();
    expect(migrationService.startMigration).toHaveBeenCalled();
  });
});
```

### Service Test: SSE Connection

```typescript
describe('MigrationService', () => {
  let service: MigrationService;
  let httpClient: jasmine.SpyObj<HttpClient>;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        MigrationService,
        { provide: HttpClient, useValue: jasmine.createSpyObj('HttpClient', ['post']) },
      ],
    });
    service = TestBed.inject(MigrationService);
    httpClient = TestBed.inject(HttpClient) as jasmine.SpyObj<HttpClient>;
  });

  it('starts migration and connects SSE', () => {
    httpClient.post.and.returnValue(of({ status: 'started' }));
    service.startMigration().subscribe();
    expect(service.status()).toBe('running');
  });
});
```

## Acceptance Criteria

- [ ] `/settings` route with sub-navigation
- [ ] `DatabaseComponent` shows current database type
- [ ] Migration button visible when `database === "sqlite"`
- [ ] Progress bar shows real-time migration progress via SSE
- [ ] Success/failure states handled correctly
- [ ] Retry button on failure
- [ ] Reload button on completion
- [ ] SSE connection with exponential backoff
- [ ] Conditional visibility (no PostgreSQL options for PG users)
- [ ] Component tests pass
- [ ] Service tests pass
- [ ] No console errors
- [ ] Responsive design (works on mobile)

## Rollback Plan

If UI issues arise:
1. Revert frontend changes
2. Backend migration API still works (can be triggered via curl)
3. No data risk

## Estimated Diff Size

- 1 file modified: `frontend/src/app/app.routes.ts` (+15 lines)
- 1 file modified: `frontend/src/app/app.component.ts` (+5 lines)
- 1 file new: `frontend/src/app/settings/settings.component.ts` (+50 lines)
- 1 file new: `frontend/src/app/settings/database/database.component.ts` (+80 lines)
- 1 file new: `frontend/src/app/settings/database/database.component.html` (+40 lines)
- 1 file new: `frontend/src/app/settings/database/migration.service.ts` (+100 lines)
- 1 file new: `frontend/src/app/settings/database/database.component.spec.ts` (+50 lines)

**Total**: 2 files modified, 5 files new, ~340 lines

## Next Phase

[Phase 7: Integration Testing](./08-phase-7-integration-testing.md)
