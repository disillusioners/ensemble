import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import {
  EMPTY_METRICS_TAB_STATE,
  MetricsTabState,
  MetricsTabStateReporter,
} from '../metrics-state';
import {
  METRICS_RANGE_OPTIONS,
  MetricsRangeOption,
  metricsResourcesWindow,
} from '../metrics-range';
import { metricsCustomTab } from '../tabs.manifest';
import { RESOURCES_LADDER_RETENTION_TIERS_COPY } from '../../../models/resources.models';

/**
 * The RESOURCES module tab — the `custom` slot (T1 phase 2, task 2.4).
 *
 * The resource module is built in PHASE 5 (the six `/api/resources/*`
 * support endpoints). Until then this body renders the PHASE-3 frame
 * surface — the module's own labelled range control — over a placeholder.
 * The placeholder is removed in phase 5 and never reaches a release.
 *
 * The slot is the SEAM of the per-source state model (AM-1): this tab
 * renders ITS OWN source's state, so a failing central door never blanks
 * it, and the central door's fail-closed state never reaches it.
 *
 * ── THE PANEL'S OWN RANGE CONTROL (3.1 / AM-19) ───────────────────────
 *
 * The custom module's ladder is its OWN (15m / 1h / 24h / 7d — a subset
 * of the frozen `ResourcesWindow` union), and the frame's central-sourced
 * control does NOT govern it. The panel therefore carries its own
 * labelled control — "Timeline range" — whose option set is the frame's
 * union with the options this ladder cannot serve DISABLED (the visible
 * enforcement; the door would 400 on anything else).
 *
 * The control edits the FRAME's value (one value, two vocabularies) and
 * the panel maps it onto its own door.
 */
@Component({
  selector: 'app-metrics-resource-tab',
  standalone: true,
  template: `
    <div class="resource-tab" data-test="resource-tab-placeholder">
      <div class="resource-range" role="group" aria-label="Timeline range">
        <label class="resource-range-label" for="resource-range-select">Timeline range</label>
        <select
          id="resource-range-select"
          class="resource-range-select"
          data-test="resource-range-select"
          [value]="window()"
          [attr.aria-label]="'Select the resources timeline range'"
          (change)="onRangeSelected($any($event.target).value)"
        >
          @for (option of rangeOptions; track option) {
            <option
              [value]="option"
              [selected]="option === window()"
              [disabled]="!optionEnabled(option)"
              [attr.data-test]="'resource-range-option-' + option"
            >
              {{ option }}
            </option>
          }
        </select>
        <p class="resource-range-help" data-test="resource-range-retention-tiers">
          {{ retentionTiersCopy }}
        </p>
      </div>

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

      .resource-range {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 0.4rem 0.75rem;
        margin-bottom: 0.6rem;
      }

      .resource-range-label {
        color: #cbd5e1;
        font-size: 0.78rem;
      }

      .resource-range-select {
        background: #0b1220;
        color: #e2e8f0;
        border: 1px solid #1e293b;
        border-radius: 4px;
        padding: 0.15rem 0.35rem;
        font-size: 0.78rem;
      }

      .resource-range-help {
        flex-basis: 100%;
        margin: 0;
        color: #64748b;
        font-size: 0.72rem;
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

  /** The frame's window VALUE (the union) — one value, two vocabularies. */
  readonly window = input<MetricsRangeOption>('1h');

  /**
   * Whether the previous-window comparison is ON.
   *
   * The custom slot has NO baseline (3.6): the resources door serves a
   * fixed ladder with no previous-window offset, so the frame renders the
   * toggle disabled with its reason. The panel never acts on the flag.
   */
  readonly baseline = input(false);

  /** The frame's range-change reporter — the panel's own control edits it. */
  readonly onRangeChange = input<(option: MetricsRangeOption) => void>(() => undefined);

  /** The option set — the frame's union, with this ladder's disabled set. */
  readonly rangeOptions = METRICS_RANGE_OPTIONS;

  /** The retention-tier copy — the tiers BACK the ladder, they are not options. */
  readonly retentionTiersCopy = RESOURCES_LADDER_RETENTION_TIERS_COPY;

  /**
   * The window THIS panel asks its own door for.
   *
   * `null` when the frame's value is not on the resources ladder — the
   * mapping refuses rather than carrying a value the door would 400 on
   * (3.6: no `/api/resources/*` request ever carries a window outside
   * `ResourcesWindow`).
   */
  readonly resourcesWindow = computed(() => metricsResourcesWindow(this.window()));

  /** Whether this ladder can serve an option (the visible enforcement). */
  optionEnabled(option: MetricsRangeOption): boolean {
    return metricsResourcesWindow(option) !== null;
  }

  /** The panel's own control change handler. */
  onRangeSelected(value: string): void {
    const option = METRICS_RANGE_OPTIONS.find((candidate) => candidate === value);
    if (!option || !this.optionEnabled(option)) {
      return;
    }
    this.onRangeChange()(option);
  }
}
