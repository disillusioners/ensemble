import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { MetricsCentralTabComponent } from './central-tab.component';
import {
  EMPTY_METRICS_TAB_STATE,
  MetricsTabState,
  MetricsTabStateReporter,
} from '../metrics-state';
import { MetricsRangeOption } from '../metrics-range';
import { metricsCentralTab } from '../tabs.manifest';

/**
 * The DB Pool module tab — the DB pool gauges and counters (F4, F5) plus the PostgreSQL census
 * (F6), which is bound NOW and dark until R-i fixes the census polarity.
 *
 * The body is the shared central implementation (the six central tabs
 * differ only in their manifest entry); the per-kind+unit-group panel
 * COMPOSITION arrives with the renderer registry in phase 4 (task 4.15).
 */
@Component({
  selector: 'app-metrics-db-pool-tab',
  standalone: true,
  imports: [MetricsCentralTabComponent],
  template: `<app-metrics-central-tab
    [entry]="entry"
    [state]="state()"
    [onState]="onState()"
    [window]="window()"
    [baseline]="baseline()"
    [onRangeChange]="onRangeChange()"
  ></app-metrics-central-tab>`,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DbPoolTabComponent {
  /** The manifest entry — this module's identity and its binding. */
  readonly entry = metricsCentralTab('db-pool');

  /** This tab's state — written by its OWN source (AM-1). */
  readonly state = input<MetricsTabState>(EMPTY_METRICS_TAB_STATE);

  /** The frame's per-tab state reporter. */
  readonly onState = input<MetricsTabStateReporter | null>(null);

  /** The frame's window VALUE (the union) — the frame owns the window (3.3). */
  readonly window = input<MetricsRangeOption>('1h');

  /** Whether the previous-window comparison is ON (3.4) — the central tabs only. */
  readonly baseline = input(false);

  /** The frame's range-change reporter (AM-19) — the panel's own control edits the value. */
  readonly onRangeChange = input<(option: MetricsRangeOption) => void>(() => undefined);
}
