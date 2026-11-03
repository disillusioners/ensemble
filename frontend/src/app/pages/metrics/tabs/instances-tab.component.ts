import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { MetricsCentralTabComponent } from './central-tab.component';
import {
  EMPTY_METRICS_TAB_STATE,
  MetricsTabState,
  MetricsTabStateReporter,
} from '../metrics-state';
import { metricsCentralTab } from '../tabs.manifest';

/**
 * The Instances module tab — the instances census (F13) and the worker-pool gauges (F11).
 *
 * The body is the shared central implementation (the six central tabs
 * differ only in their manifest entry); the per-kind+unit-group panel
 * COMPOSITION arrives with the renderer registry in phase 4 (task 4.15).
 */
@Component({
  selector: 'app-metrics-instances-tab',
  standalone: true,
  imports: [MetricsCentralTabComponent],
  template: `<app-metrics-central-tab
    [entry]="entry"
    [state]="state()"
    [onState]="onState()"
  ></app-metrics-central-tab>`,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class InstancesTabComponent {
  /** The manifest entry — this module's identity and its binding. */
  readonly entry = metricsCentralTab('instances');

  /** This tab's state — written by its OWN source (AM-1). */
  readonly state = input<MetricsTabState>(EMPTY_METRICS_TAB_STATE);

  /** The frame's per-tab state reporter. */
  readonly onState = input<MetricsTabStateReporter | null>(null);
}
