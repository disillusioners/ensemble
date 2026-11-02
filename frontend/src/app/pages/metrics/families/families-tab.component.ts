import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
} from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatTooltipModule } from '@angular/material/tooltip';

import {
  MetricsFamily,
  formatAvailabilityReason,
  formatMetricsLabels,
} from '../../../models/metrics.model';

/**
 * Families tab — the family catalog AS SERVED (`GET /api/metrics/families`).
 *
 * The catalog is loaded ONCE by the page shell (the shell owns the
 * availability projection the empty state depends on), and this tab
 * renders it: every family with its role, its availability and the
 * reason it is dark. A dark family NEVER shows a fabricated zero — it
 * shows WHY it has no observation.
 *
 * The load is MANUAL (the plan's third tab): the catalog is stable
 * (a family appears when it is wired), so it is not polled. The Reload
 * button asks the shell for a fresh catalog.
 */
@Component({
  selector: 'app-metrics-families',
  standalone: true,
  imports: [MatButtonModule, MatTooltipModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
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

      <table class="families-table" data-test="families-table">
        <thead>
          <tr>
            <th>Family</th>
            <th>Role</th>
            <th>Availability</th>
            <th>Series</th>
            <th>Names</th>
          </tr>
        </thead>
        <tbody>
          @for (family of families(); track family.id) {
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
                }
              </td>
            </tr>
          }
        </tbody>
      </table>

      @if (families().length === 0) {
        <p class="tab-hint" data-test="families-empty">No families are registered.</p>
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

      .tab-hint {
        color: #64748b;
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

      .name-kind {
        color: #475569;
        margin-left: 0.3rem;
      }
    `,
  ],
})
export class FamiliesTabComponent {
  /** The catalog — the shell owns the load and hands the projection over. */
  readonly families = input<readonly MetricsFamily[]>([]);

  /**
   * Whether THIS tab is the selected one (the shell's visibility gate).
   */
  readonly active = input(false);

  /**
   * The shell's catalog reload. The tab body is mounted through the
   * dynamic outlet, which binds INPUTS only — so the reload is handed
   * over as a callback rather than as an output binding.
   */
  readonly onReload = input<(() => void) | null>(null);

  /** Invoke the shell's reload callback (a signal input is unwrapped). */
  reloadCatalog(): void {
    const reload = this.onReload();
    if (reload) {
      reload();
    }
  }

  readonly liveCount = computed(
    () => this.families().filter((family) => family.availability.state === 'live').length,
  );

  readonly darkCount = computed(
    () => this.families().filter((family) => family.availability.state !== 'live').length,
  );

  /** The human reason of a dark family (an unknown reason → unknown(...)). */
  reasonOf(family: MetricsFamily): string {
    return formatAvailabilityReason(family.availability.reason);
  }

  /** The label set of a family's first name (the table's detail column). */
  labelsOf(family: MetricsFamily): string {
    return formatMetricsLabels(family.names[0]?.labels ?? null);
  }
}
