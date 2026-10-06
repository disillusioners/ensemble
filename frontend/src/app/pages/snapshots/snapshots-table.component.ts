import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  output,
  viewChild,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatPaginator, MatPaginatorModule, PageEvent } from '@angular/material/paginator';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatTableModule } from '@angular/material/table';
import { MatTooltipModule } from '@angular/material/tooltip';
import { SnapshotStatus, SnapshotRow } from '../../models/snapshot.model';

/**
 * PRESENTATIONAL (pass 4 amendment #8 — leader ruling) — the
 * `SnapshotsTableComponent` is a pure render surface; it does NOT
 * inject any service, does NOT call the BE, and does NOT own
 * pagination state. The host page (`SnapshotsComponent`) owns:
 *
 * * the list fetch + `records` / `total` / `listLoading` / `listError`
 *   signals,
 * * the `pageIndex` / `pageSize` paginator state and the
 *   host-owned reset on every filter change (amendment #10),
 * * the `seenAgents` session-accumulated set (amendment #5/#8).
 *
 * The table receives rows via input and emits `rowClick`,
 * `pageChange`, and `retry` outputs ONLY.
 *
 * R11 (stale-response race) lives in the page — the table simply
 * re-renders whatever rows the host provides.
 *
 * The displayed columns are:
 *
 * | # | column    | source field            | notes |
 * |---|-----------|-------------------------|-------|
 * | 1 | title     | `SnapshotRow.title`     | truncated + tooltip |
 * | 2 | project   | `SnapshotRow.project_id`| truncated UUID (D-5) |
 * | 3 | agent     | `SnapshotRow.created_by_agent_id` | full id |
 * | 4 | status    | `SnapshotRow.status`    | chip via `statusClass` |
 * | 5 | tags      | `SnapshotRow.domain_tags` | first 2 + overflow count |
 * | 6 | created   | `SnapshotRow.created_at`| `formatRelative` (with absolute tooltip) |
 * | 7 | warm      | (always `—` in v1, D-5) | column slot kept for Phase-2 |
 * | 8 | actions   | (the row click target)  | copy-id button |
 *
 * The table is a standalone component; `OnPush` change detection
 * keeps it cheap on filter / page swaps.
 */
@Component({
  selector: 'app-snapshots-table',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CommonModule,
    MatButtonModule,
    MatIconModule,
    MatPaginatorModule,
    MatProgressBarModule,
    MatTableModule,
    MatTooltipModule,
  ],
  templateUrl: './snapshots-table.component.html',
  styleUrl: './snapshots-table.component.scss',
})
export class SnapshotsTableComponent {
  // ── E2E hook stamping (review pass 1 FIX 1) ──────────────────────
  // Material's paginator has NO per-page buttons — only first/prev/
  // next/last navigation + range label + page-size selector. The e2e
  // hook `data-test="paginator-page-1"` is therefore stamped onto the
  // REAL Material-rendered first-page button after view init: it is
  // genuinely visible, clickable (returns to page 1), and disabled by
  // Material exactly when `pageIndex === 0` (total > 0), which gives
  // the e2e a true pageIndex-reset signal.
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  /** The Material paginator instance — resolves once the loaded
   *  branch (`@else`) renders; re-resolves when the block toggles. */
  private readonly paginator = viewChild(MatPaginator);

  constructor() {
    effect(() => {
      if (!this.paginator()) return;
      // Stable hook: Material's first-page button class. Guarded — if
      // Material's internal DOM drifts, no-op instead of crashing.
      this.host.nativeElement
        .querySelector<HTMLButtonElement>(
          'mat-paginator button.mat-mdc-paginator-navigation-first',
        )
        ?.setAttribute('data-test', 'paginator-page-1');
    });
  }

  // Paginator inputs — HOST-OWNED (amendment #10). The table never
  // resets pageIndex; it only re-emits the user's paginator click to
  // the host.
  readonly pageSize = input<number>(25);
  readonly pageIndex = input<number>(0);

  // List-state inputs (PASS 4 #8 — presentational, fed by host).
  readonly rows = input.required<SnapshotRow[]>();
  readonly total = input.required<number>();
  readonly loading = input<boolean>(false);
  readonly error = input<string | null>(null);
  readonly hasActiveFilters = input<boolean>(false);

  // ── Outputs (table is purely presentational) ─────────────────────
  readonly rowClick = output<SnapshotRow>();
  readonly pageChange = output<PageEvent>();
  readonly retry = output<void>();

  // ── Display columns (per plan §5.3) ──────────────────────────────
  protected readonly displayedColumns: readonly string[] = [
    'title',
    'project',
    'agent',
    'status',
    'tags',
    'created',
    'warm',
    'actions',
  ];

  // ── Derived state (template-friendly computeds) ──────────────────
  readonly isEmpty = computed<boolean>(
    () => this.rows().length === 0 && !this.loading() && !this.error(),
  );
  readonly isFilteredEmpty = computed<boolean>(
    () =>
      this.rows().length === 0 &&
      this.total() > 0 &&
      this.hasActiveFilters() &&
      !this.loading() &&
      !this.error(),
  );

  // ── Template helpers (public for test access) ───────────────────

  /** Map a status to its SCSS class hook (`.status-<value>`). */
  statusClass(s: SnapshotStatus): string {
    return `status-${s}`;
  }

  /** First 2 tags — overflow handled by `overflowCount`. */
  visibleTags(row: SnapshotRow): string[] {
    return (row.domain_tags ?? []).slice(0, 2);
  }

  /** `tags.length - 2` for rows with >2 tags, else 0. */
  overflowCount(row: SnapshotRow): number {
    const len = row.domain_tags?.length ?? 0;
    return len > 2 ? len - 2 : 0;
  }

  /**
   * Format an ISO-8601 timestamp as a relative string.
   *
   * Buckets:
   *  - `< 60s`           → "just now"
   *  - `< 60m`           → "Nm ago"
   *  - `< 24h`           → "Nh ago"
   *  - `< 30d`           → "Nd ago"
   *  - otherwise         → ISO date "YYYY-MM-DD"
   *
   * `now` injection for testability.
   */
  formatRelative(iso: string, now: Date = new Date()): string {
    if (!iso) return '';
    const then = new Date(iso).getTime();
    if (Number.isNaN(then)) return iso;
    const diffMs = now.getTime() - then;
    const sec = Math.floor(diffMs / 1000);
    if (sec < 60) return 'just now';
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h ago`;
    const day = Math.floor(hr / 24);
    if (day < 30) return `${day}d ago`;
    // Fall back to the absolute date
    const d = new Date(iso);
    return d.toISOString().slice(0, 10);
  }

  /** Absolute UTC timestamp for tooltip use. */
  formatAbsolute(iso: string): string {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    // YYYY-MM-DD HH:MM UTC
    return d.toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
  }

  /** Stable id for `@for track`. */
  trackById(_: number, row: SnapshotRow): string {
    return row.id;
  }

  /** Truncate a UUID-like project id to `first4…first4` for table cell. */
  truncateId(id: string | null | undefined): string {
    if (!id) return '—';
    if (id.length <= 12) return id;
    return `${id.slice(0, 4)}…${id.slice(-4)}`;
  }

  // ── Event handlers (bound from template) ────────────────────────

  onRowClick(row: SnapshotRow): void {
    this.rowClick.emit(row);
  }

  onPageChange(event: PageEvent): void {
    this.pageChange.emit(event);
  }

  onRetry(): void {
    this.retry.emit();
  }
}
