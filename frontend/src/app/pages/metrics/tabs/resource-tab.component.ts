import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import {
  EMPTY_METRICS_TAB_STATE,
  MetricsTabState,
  MetricsTabStateReporter,
} from '../metrics-state';
import { metricsCustomTab } from '../tabs.manifest';

/**
 * The RESOURCES module tab — the `custom` slot (T1 phase 2, task 2.4).
 *
 * The resource module is built in PHASE 5 (the six `/api/resources/*`
 * support endpoints). Until then this body renders a PLACEHOLDER — it
 * is removed in phase 5 and never reaches a release.
 *
 * The slot is the SEAM of the per-source state model (AM-1): this tab
 * renders ITS OWN source's state, so a failing central door never
 * blanks it, and the central door's fail-closed state never reaches
 * it.
 */
@Component({
  selector: 'app-metrics-resource-tab',
  standalone: true,
  template: `
    <div class="resource-tab" data-test="resource-tab-placeholder">
      <p class="tab-hint">
        The resource module lands here — it reads its own six
        <code>/api/resources/*</code> endpoints, not the central door.
      </p>
    </div>
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .resource-tab {
        display: block;
        padding: 0.75rem 0;
      }

      .tab-hint {
        color: #64748b;
        font-size: 0.8rem;
      }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ResourceTabComponent {
  /** The manifest entry — the custom slot's own vocabulary. */
  readonly entry = metricsCustomTab();

  /** This tab's state — written by its OWN source (AM-1). */
  readonly state = input<MetricsTabState>(EMPTY_METRICS_TAB_STATE);

  /** The frame's per-tab state reporter. */
  readonly onState = input<MetricsTabStateReporter | null>(null);
}
