import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import {
  Anomaly,
  formatUtcLabel,
} from '../../../../models/resources.models';

/**
 * The standing PROCESS-LOCAL DISCLOSURE on the anomalies panel
 * (T1 phase 5, task 5.6 — ADJ-v, binding honesty requirement).
 *
 * The journal behind `/api/resources/anomalies` is an in-RAM
 * `deque(maxlen=256)` (`daemon/services/resource_anomaly.py:913` —
 * `JOURNAL_MAXLEN = 256`) that RESETS on every daemon restart. The
 * panel therefore states, in ONE header line, that it can never span
 * overnight BY CONSTRUCTION — the copy is written so it can NEVER be
 * read as a durability claim ("resets", "never spans a restart").
 */
export const ANOMALIES_PROCESS_LOCAL_DISCLOSURE =
  'Process-local journal — resets on daemon restart and keeps only the last 256 entries; it never spans a restart.';

/**
 * The ANOMALIES PANEL (T1 phase 5, task 5.5 — ADJ-v).
 *
 * A table of `/api/resources/anomalies` rows — `id`, `kind`,
 * `severity`, `peak`, `t_start`, `t_end` — the facts that previously
 * existed only as anonymous timeline dots (and hover-only titles).
 * A row click hands the wire anomaly to the tab body, which opens the
 * EXISTING drill-down (evidence + traceback, with the padded window
 * from `anomalyDrilldownWindow`) on the timeline panel.
 *
 * The panel is PRESENTATIONAL: the tab body owns the anomalies load
 * (its own request class + supersede token, 5.11) and hands the rows
 * and the error branch down here.
 */
@Component({
  selector: 'app-metrics-anomalies',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="anomalies-panel" data-test="anomalies-panel">
      <header class="anomalies-header">
        <h3>Anomalies</h3>
        <p class="anomalies-disclosure" data-test="anomalies-disclosure">
          {{ disclosure }}
        </p>
      </header>

      @if (error(); as message) {
        <div class="anomalies-error" data-test="anomalies-error" role="status">
          <span>{{ message }}</span>
          <button
            type="button"
            class="anomalies-retry"
            (click)="onRetry()"
            data-test="anomalies-retry"
          >
            Retry
          </button>
        </div>
      }

      <table class="anomalies-table" data-test="anomalies-table">
        <thead>
          <tr>
            <th scope="col">id</th>
            <th scope="col">kind</th>
            <th scope="col">severity</th>
            <th scope="col">peak</th>
            <th scope="col">t_start (UTC)</th>
            <th scope="col">t_end (UTC)</th>
          </tr>
        </thead>
        <tbody>
          @for (anomaly of anomalies(); track anomaly.id) {
            <tr
              class="anomaly-row"
              [attr.data-test]="'anomaly-row-' + anomaly.id"
              [attr.data-severity]="anomaly.severity"
              tabindex="0"
              (click)="open(anomaly)"
              (keydown.enter)="open(anomaly)"
            >
              <td class="anomaly-id">{{ anomaly.id }}</td>
              <td data-test="anomaly-cell-kind">{{ anomaly.kind }}</td>
              <td data-test="anomaly-cell-severity">{{ anomaly.severity }}</td>
              <td data-test="anomaly-cell-peak">{{ anomaly.peak }}</td>
              <td>{{ utcLabel(anomaly.t_start) }}</td>
              <td>{{ utcLabel(anomaly.t_end) }}</td>
            </tr>
          } @empty {
            <tr>
              <td colspan="6" class="anomalies-empty" data-test="anomalies-empty">
                @if (loading()) {
                  Loading anomalies…
                } @else {
                  no anomalies in this window
                }
              </td>
            </tr>
          }
        </tbody>
      </table>
    </section>
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .anomalies-panel {
        display: block;
      }

      .anomalies-header {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: 0.4rem 0.9rem;
        margin-bottom: 0.5rem;
      }

      h3 {
        margin: 0;
        font-size: 0.95rem;
        color: #f1f5f9;
      }

      .anomalies-disclosure {
        margin: 0;
        color: #64748b;
        font-size: 0.74rem;
      }

      .anomalies-error {
        display: flex;
        align-items: center;
        gap: 0.7rem;
        margin: 0 0 0.5rem;
        color: #f87171;
        font-size: 0.78rem;
      }

      .anomalies-retry {
        background: transparent;
        border: 1px solid #334155;
        border-radius: 4px;
        color: #cbd5e1;
        font-size: 0.74rem;
        padding: 0.1rem 0.5rem;
        cursor: pointer;
      }

      .anomalies-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.78rem;
        color: #cbd5e1;
      }

      th {
        text-align: left;
        color: #64748b;
        font-weight: 500;
        padding: 0.3rem 0.6rem;
        border-bottom: 1px solid #1e293b;
      }

      td {
        padding: 0.3rem 0.6rem;
        border-bottom: 1px solid #172033;
      }

      .anomaly-row {
        cursor: pointer;
      }

      .anomaly-row:hover,
      .anomaly-row:focus-visible {
        background: #172033;
        outline: none;
      }

      .anomaly-id {
        color: #94a3b8;
        font-family: monospace;
      }

      td[data-test='anomaly-cell-severity'][data-severity='high'] {
        color: #ef4444;
      }

      td[data-test='anomaly-cell-severity'][data-severity='medium'] {
        color: #f59e0b;
      }

      td[data-test='anomaly-cell-severity'][data-severity='low'] {
        color: #38bdf8;
      }

      .anomalies-empty {
        color: #64748b;
      }
    `,
  ],
})
export class AnomaliesPanelComponent {
  /** The window's anomaly rows — the body owns the load (5.11). */
  readonly anomalies = input<readonly Anomaly[]>([]);

  /** True while the body is waiting for the first anomalies response. */
  readonly loading = input(false);

  /** The body's anomalies error branch — last-good rows are retained. */
  readonly error = input<string | null>(null);

  /** A row click — the body opens the existing drill-down for it (5.5). */
  readonly rowClick = output<Anomaly>();

  /** The panel's Retry CTA — the body re-kicks its own anomalies load. */
  readonly retryRequested = output<void>();

  /** The standing process-local disclosure (5.6). */
  readonly disclosure = ANOMALIES_PROCESS_LOCAL_DISCLOSURE;

  open(anomaly: Anomaly): void {
    this.rowClick.emit(anomaly);
  }

  onRetry(): void {
    this.retryRequested.emit();
  }

  readonly utcLabel = formatUtcLabel;
}
