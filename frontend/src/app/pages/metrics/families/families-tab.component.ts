import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
  signal,
} from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatTooltipModule } from '@angular/material/tooltip';

import {
  MetricsFamily,
  MetricsFamilyName,
  formatAvailabilityReason,
  formatMetricsDeclaredLabels,
} from '../../../models/metrics.model';

/** The sortable columns of the catalog table. */
export type MetricsCatalogColumn = 'id' | 'role' | 'availability' | 'series' | 'names';

/**
 * The FAMILY CATALOG — the frame-level drawer's body (T1 phase 2,
 * task 2.7).
 *
 * The catalog is loaded ONCE by the page shell (the shell owns the
 * availability projection the empty state depends on), and this
 * component renders it: every family with its role, its availability
 * and the reason it is dark. A dark family NEVER shows a fabricated
 * zero — it shows WHY it has no observation.
 *
 * The affordances the audit found missing are here:
 *
 *   * the columns are SORTABLE and FILTERABLE (the dark families must
 *     be findable);
 *   * every `<th>` carries `aria-sort` and a click handler (today they
 *     are inert);
 *   * the EMPTY COPY is ERROR-GATED — "No families are registered."
 *     must never render under a failed load, because the payload is
 *     UNKNOWN then, not empty.
 */
@Component({
  selector: 'app-metrics-family-catalog',
  standalone: true,
  imports: [MatButtonModule, MatTooltipModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  // The shared tab-surface styles (banner/badge/hint/meta) live in the
  // shared partial — the A-5 consolidation; the palette/geometry
  // authority is metrics-door-state.component.ts (see the partial's
  // header). Only this tab's OWN rules (.tab-error, the table) stay
  // inline below.
  styleUrls: ['../shared/tab-surface.scss'],
  template: `
    <div class="families">
      <div class="families-toolbar">
        <span class="tab-hint" data-test="families-meta">
          {{ families().length }} famil{{ families().length === 1 ? 'y' : 'ies' }} ·
          {{ liveCount() }} live · {{ darkCount() }} dark
        </span>
        <button mat-stroked-button type="button" (click)="reloadCatalog()" data-test="families-reload">
          Reload
        </button>
      </div>

      <label class="families-filter">
        <span class="tab-hint">Filter</span>
        <input
          type="search"
          class="families-filter-input"
          [value]="filter()"
          (input)="onFilter($event)"
          data-test="families-filter"
        />
      </label>

      <table class="families-table" data-test="families-table">
        <thead>
          <tr>
            <th scope="col" [attr.aria-sort]="ariaSort('id')" (click)="sortBy('id')" data-test="families-th-id">
              Family
            </th>
            <th scope="col" [attr.aria-sort]="ariaSort('role')" (click)="sortBy('role')" data-test="families-th-role">
              Role
            </th>
            <th
              scope="col"
              [attr.aria-sort]="ariaSort('availability')"
              (click)="sortBy('availability')"
              data-test="families-th-availability"
            >
              Availability
            </th>
            <th
              scope="col"
              [attr.aria-sort]="ariaSort('series')"
              (click)="sortBy('series')"
              data-test="families-th-series"
            >
              Series
            </th>
            <th scope="col" [attr.aria-sort]="ariaSort('names')" (click)="sortBy('names')" data-test="families-th-names">
              Names
            </th>
          </tr>
        </thead>
        <tbody>
          @for (family of visibleFamilies(); track family.id) {
            <tr [attr.data-family]="family.id" [attr.data-availability]="family.availability.state">
              <td class="cell-id">{{ family.id }}</td>
              <td>{{ family.role }}</td>
              <td class="cell-availability">
                @if (family.availability.state === 'live') {
                  <span class="pill pill-live" data-test="family-live">live</span>
                } @else {
                  <span class="pill pill-dark" data-test="family-dark">dark</span>
                  <span class="reason" [attr.data-reason]="family.availability.reason">
                    {{ reasonOf(family) }}
                  </span>
                }
              </td>
              <td class="cell-count">{{ family.live_series_count }}</td>
              <td class="cell-names">
                @for (name of family.names; track name.name) {
                  <span class="name-chip" [matTooltip]="name.help || name.name">
                    {{ name.name }}<span class="name-kind">{{ name.kind }}</span>
                  </span>
                  @if (declaredLabelsOf(family, name); as declared) {
                    <span class="declared-labels" [attr.data-test]="'declared-labels-' + name.name">{{
                      declared
                    }}</span>
                  }
                }
              </td>
            </tr>
          }
        </tbody>
      </table>

      @if (error()) {
        <!--
          THE CATALOG COULD NOT BE READ (task 8.3).

          The drawer states the FACT and POINTS at the failure — it does
          NOT re-render the door-state panel: the page renders that state
          ONCE (the frame's panel), and a second copy of the same
          failure on the same page is the duplicate banner the audit
          found. The empty copy stays ERROR-GATED either way: an
          unread catalog is UNKNOWN, never empty.
        -->
        <p class="tab-error" data-test="families-error">
          The family catalog could not be read — the failure is stated above.
        </p>
      } @else if (!families().length) {
        <p class="tab-hint" data-test="families-empty">No families are registered.</p>
      } @else if (!visibleFamilies().length) {
        <p class="tab-hint" data-test="families-no-match">No families match the filter.</p>
      }
    </div>
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .families {
        display: block;
        padding: 0.75rem 0;
      }

      .families-toolbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.75rem;
        margin-bottom: 0.5rem;
      }

      .families-filter {
        display: flex;
        align-items: center;
        gap: 0.4rem;
        margin-bottom: 0.5rem;
      }

      .families-filter-input {
        flex: 1 1 auto;
        background: #0f172a;
        border: 1px solid #1e293b;
        border-radius: 4px;
        color: #cbd5e1;
        font-size: 0.78rem;
        padding: 0.2rem 0.4rem;
      }

      .tab-error {
        color: #fca5a5;
        font-size: 0.8rem;
      }

      .families-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.78rem;
      }

      th {
        text-align: left;
        color: #64748b;
        font-weight: 500;
        border-bottom: 1px solid #1e293b;
        padding: 0.25rem 0.4rem;
        cursor: pointer;
      }

      td {
        padding: 0.3rem 0.4rem;
        border-bottom: 1px solid #111c33;
        color: #cbd5e1;
        vertical-align: top;
      }

      .cell-id {
        color: #f1f5f9;
      }

      .cell-count {
        font-variant-numeric: tabular-nums;
      }

      .pill {
        display: inline-block;
        padding: 0.05rem 0.35rem;
        border-radius: 4px;
        font-size: 0.68rem;
        margin-right: 0.35rem;
      }

      .pill-live {
        border: 1px solid #14532d;
        background: #052e16;
        color: #4ade80;
      }

      .pill-dark {
        border: 1px solid #3f3f46;
        background: #18181b;
        color: #a1a1aa;
      }

      .reason {
        color: #fbbf24;
        font-size: 0.72rem;
      }

      .cell-names {
        max-width: 28rem;
      }

      .name-chip {
        display: inline-block;
        margin: 0 0.25rem 0.2rem 0;
        padding: 0.05rem 0.35rem;
        border: 1px solid #1e293b;
        border-radius: 4px;
        color: #94a3b8;
        font-size: 0.7rem;
        word-break: break-all;
      }

      .declared-labels {
        display: block;
        margin: 0 0 0.3rem 0.25rem;
        color: #475569;
        font-size: 0.66rem;
        word-break: break-all;
      }

      .name-kind {
        color: #475569;
        margin-left: 0.3rem;
      }
    `,
  ],
})
export class MetricsFamilyCatalogComponent {
  /** The catalog — the shell owns the load and hands the projection over. */
  readonly families = input<readonly MetricsFamily[]>([]);

  /**
   * The frame's catalog error.
   *
   * The empty copy is gated on it: under a failed load the payload is
   * UNKNOWN, so "No families are registered." would be a false claim.
   */
  readonly error = input<string | null>(null);

  /**
   * The shell's catalog reload. The component is mounted through the
   * dynamic outlet, which binds INPUTS only — so the reload is handed
   * over as a callback rather than as an output binding.
   */
  readonly onReload = input<(() => void) | null>(null);

  /** The filter text — the families are findable by id or role. */
  readonly filter = signal('');

  /** The column the table is sorted by. */
  readonly sortColumn = signal<MetricsCatalogColumn>('id');

  /** The sort direction. */
  readonly sortDirection = signal<'asc' | 'desc'>('asc');

  /** The visible rows — filtered, then sorted. */
  readonly visibleFamilies = computed<readonly MetricsFamily[]>(() => {
    const needle = this.filter().trim().toLowerCase();
    const rows = this.families().filter(
      (family) =>
        !needle ||
        family.id.toLowerCase().includes(needle) ||
        family.role.toLowerCase().includes(needle),
    );
    const column = this.sortColumn();
    const direction = this.sortDirection() === 'asc' ? 1 : -1;
    return [...rows].sort((left, right) => direction * this.compare(left, right, column));
  });

  readonly liveCount = computed(
    () => this.families().filter((family) => family.availability.state === 'live').length,
  );

  readonly darkCount = computed(
    () => this.families().filter((family) => family.availability.state !== 'live').length,
  );

  /** The filter input handler. */
  onFilter(event: Event): void {
    const target = event.target as HTMLInputElement | null;
    this.filter.set(target?.value ?? '');
  }

  /** Sort by a column — a second click on the same column flips it. */
  sortBy(column: MetricsCatalogColumn): void {
    if (this.sortColumn() === column) {
      this.sortDirection.update((direction) => (direction === 'asc' ? 'desc' : 'asc'));
      return;
    }
    this.sortColumn.set(column);
    this.sortDirection.set('asc');
  }

  /** The `aria-sort` value of a column header. */
  ariaSort(column: MetricsCatalogColumn): 'ascending' | 'descending' | 'none' {
    if (this.sortColumn() !== column) {
      return 'none';
    }
    return this.sortDirection() === 'asc' ? 'ascending' : 'descending';
  }

  /** Invoke the shell's reload callback (a signal input is unwrapped). */
  reloadCatalog(): void {
    const reload = this.onReload();
    if (reload) {
      reload();
    }
  }

  /** The human reason of a dark family (an unknown reason → unknown(...)). */
  reasonOf(family: MetricsFamily): string {
    return formatAvailabilityReason(family.availability.reason);
  }

  /**
   * The DECLARED label value sets of one name, rendered as a set
   * (W-6) — `op∈{aget, aget_tuple}` — never as a concrete
   * `key=value` pair. An empty rendering (no declared set) renders
   * nothing at all.
   */
  declaredLabelsOf(family: MetricsFamily, name: MetricsFamilyName): string {
    return formatMetricsDeclaredLabels(name.labels);
  }

  /** The comparison of two families on one column. */
  private compare(left: MetricsFamily, right: MetricsFamily, column: MetricsCatalogColumn): number {
    switch (column) {
      case 'id':
        return left.id.localeCompare(right.id);
      case 'role':
        return left.role.localeCompare(right.role);
      case 'availability':
        return left.availability.state.localeCompare(right.availability.state);
      case 'series':
        return left.live_series_count - right.live_series_count;
      case 'names':
        return left.names.length - right.names.length;
      default:
        return 0;
    }
  }
}
