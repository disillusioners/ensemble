import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatChipsModule } from '@angular/material/chips';
import { MatIconModule } from '@angular/material/icon';
import { MatPaginatorModule, PageEvent } from '@angular/material/paginator';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTableModule } from '@angular/material/table';
import { MatTooltipModule } from '@angular/material/tooltip';
import { SkillService } from '../../services/skill.service';
import { SkillUsageRecord, SkillUsageRecordsResponse } from '../../models/skill.model';

/**
 * Paginated usage-history table for a single skill (Phase 5 Part A).
 *
 * Renders the records returned by `GET /api/skills/{id}/usage-records`
 * (see `SkillService.getUsageRecords`) with server-side pagination,
 * row expansion for the detail fields, and per-row tinting that
 * reflects the record's success / fallback / superseded state.
 *
 * State management follows the queue-list pattern (see
 * `frontend/src/app/components/queue-list/queue-list.component.ts`):
 * `signal()` for reactive state, `effect()` to refetch when the
 * `skillId` input changes, and `inject()` for DI. The component is
 * intentionally **read-only on data** — it never mutates a record,
 * never calls a write endpoint, and never assumes the parent owns
 * refresh logic. The owning page (Phase 6) is responsible for
 * re-rendering the table by changing the `skillId` input.
 *
 * Edge cases:
 *
 * * `records.length === 0`           → "No usage history yet."
 * * `records.length > 0` and ALL are `superseded` → extra note
 *                                       "All records superseded (worker reuse)."
 * * `task_succeeded === true`        → green-tinted row (`.row-success`)
 * * `task_succeeded === false`       → muted text (`.row-failure`)
 * * `fallback === true`              → amber-tinted row (`.row-fallback`)
 * * `superseded === true`            → muted + line-through (`.row-superseded`)
 *
 * Expansion is local — only one row can be expanded at a time.
 * Clicking a row toggles its detail panel; clicking the same row
 * again collapses it.
 *
 * Integration (self-fetching — Option A):
 *
 * This component owns its own data and pagination. The parent only
 * provides `skillId` via the required input signal; `SkillService
 * .getUsageRecords()` is called internally on `skillId` change
 * (constructor `effect()`) and on paginator `pageIndex` / `pageSize`
 * change (`onPageChange`). There is no `loadMore` output, no event
 * to subscribe to, and no fetch-state to wire up. The external read
 * surface for parents is `records()`, `total()`, `pageIndex()`,
 * `pageSize()`, `loading()`, `error()`. To force a refresh, change
 * the `skillId` input (or call `onRetry()` programmatically).
 *
 * Phase 6 integrators: drop this component into skill-detail, bind
 * `[skillId]="someId"`, and it handles its own paging.
 */
@Component({
  selector: 'app-skill-usage-table',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatChipsModule,
    MatIconModule,
    MatPaginatorModule,
    MatProgressSpinnerModule,
    MatTableModule,
    MatTooltipModule,
  ],
  templateUrl: './skill-usage-table.component.html',
  styleUrl: './skill-usage-table.component.scss',
})
export class SkillUsageTableComponent {
  private readonly skillService = inject(SkillService);

  // ── Inputs ────────────────────────────────────────────────────────

  /** Skill whose usage records to display. Required. */
  readonly skillId = input.required<string>();

  // ── State signals ─────────────────────────────────────────────────

  /** Current page of records from the backend. */
  readonly records = signal<SkillUsageRecord[]>([]);

  /** Total unfiltered row count (drives the paginator). */
  readonly total = signal(0);

  /** True while a `getUsageRecords` request is in flight. */
  readonly loading = signal(false);

  /** Last error message (cleared on the next successful fetch). */
  readonly error = signal<string | null>(null);

  /** Page size — defaults to 25, can be 10/25/50 via the paginator. */
  readonly pageSize = signal(25);

  /** Server-side offset — derived from `pageIndex * pageSize`. */
  readonly offset = signal(0);

  /** Paginator's `pageIndex` (0-based). */
  readonly pageIndex = signal(0);

  /** ID of the currently-expanded row, or `null` if none. */
  readonly expandedRowId = signal<string | null>(null);

  // ── Display columns ───────────────────────────────────────────────

  protected readonly displayedColumns: readonly string[] = [
    'timestamp',
    'agent',
    'task',
    'selected',
    'applied',
    'success',
    'iterations',
    'duration',
    'fallback',
  ];

  // ── Derived signals ───────────────────────────────────────────────

  /**
   * True when the records list is non-empty AND every record is
   * superseded. Drives the "All records superseded (worker reuse)."
   * banner under the paginator header.
   */
  readonly allSuperseded = computed<boolean>(() => {
    const recs = this.records();
    return recs.length > 0 && recs.every((r) => r.superseded);
  });

  constructor() {
    // Refetch whenever the skillId input changes — also resets
    // pagination and clears any expanded row so a different skill
    // never inherits the previous skill's page state.
    effect(() => {
      const id = this.skillId();
      this.offset.set(0);
      this.pageIndex.set(0);
      this.expandedRowId.set(null);
      this.loadRecords(id, this.pageSize(), 0);
    });
  }

  // ── Template-bound helpers (public for spec access) ───────────────

  /**
   * Compute the server-side offset for a paginator `PageEvent`.
   * Pure function — exposed publicly so tests can verify the math
   * independently of the surrounding state transitions.
   */
  computeOffset(pageIndex: number, pageSize: number): number {
    return pageIndex * pageSize;
  }

  /** True if the row's id matches the currently-expanded row. */
  isExpanded(record: SkillUsageRecord): boolean {
    return this.expandedRowId() === record.id;
  }

  /** Toggle the row's expansion state. Collapses if already open. */
  toggleExpand(record: SkillUsageRecord): void {
    this.expandedRowId.update((current) => (current === record.id ? null : record.id));
  }

  /** Paginator handler — recomputes offset and refetches the page. */
  onPageChange(event: PageEvent): void {
    const newOffset = this.computeOffset(event.pageIndex, event.pageSize);
    this.pageIndex.set(event.pageIndex);
    this.pageSize.set(event.pageSize);
    this.offset.set(newOffset);
    this.expandedRowId.set(null);
    this.loadRecords(this.skillId(), event.pageSize, newOffset);
  }

  /** Retry button on the error state — re-issues the current page. */
  onRetry(): void {
    this.loadRecords(this.skillId(), this.pageSize(), this.offset());
  }

  /** `trackBy` for the table to keep DOM nodes stable across pages. */
  trackByRecordId(_index: number, record: SkillUsageRecord): string {
    return record.id;
  }

  /**
   * Row-class map used by the template via `[ngClass]="rowClass(row)"`.
   * Pure function — exposed publicly so tests can verify the
   * tinting / line-through logic without instantiating the DOM.
   */
  rowClass(record: SkillUsageRecord): Record<string, boolean> {
    return {
      'row-success': record.task_succeeded === true,
      'row-failure': record.task_succeeded === false,
      'row-fallback': record.fallback === true,
      'row-superseded': record.superseded === true,
    };
  }

  // ── Loading ───────────────────────────────────────────────────────

  /**
   * Internal fetch — invoked on `skillId` change (constructor
   * `effect()`) and on paginator change (`onPageChange` / `onRetry`).
   * Never called by parents; the component is self-fetching.
   */
  private loadRecords(skillId: string, limit: number, offset: number): void {
    this.loading.set(true);
    this.error.set(null);

    this.skillService.getUsageRecords(skillId, limit, offset).subscribe({
      next: (response: SkillUsageRecordsResponse) => {
        this.records.set(response.records);
        this.total.set(response.total);
        this.loading.set(false);
      },
      error: (err: { message?: string }) => {
        this.error.set(err?.message || 'Failed to load usage records');
        this.loading.set(false);
      },
    });
  }
}