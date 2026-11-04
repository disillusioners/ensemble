import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

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
import {
  Anomaly,
  HistoryPayload,
} from '../../../models/resources.models';
import { ResourcesService } from '../../../services/resources.service';
import { LiveTabComponent } from '../resource/live/live-tab.component';
import { TimelineTabComponent } from '../resource/timeline/timeline-tab.component';
import { AnomaliesPanelComponent } from '../resource/anomalies/anomalies-panel.component';

/**
 * The RESOURCES module tab — the `custom` slot (T1 phase 2, task 2.4;
 * rebuilt FLAT in phase 5, tasks 5.2/5.4/5.11/5.12).
 *
 * ── THE FLAT COLUMN (IMPL-03) ──────────────────────────────────────────
 *
 * Tab 1's body is ONE scrollable column: **Live stat strip → Timeline
 * panel → anomalies panel** — in that order, with NO nested tab group
 * (a nested `mat-tab-group` inside a tab is a collision surface; the
 * audit found a 900 px banner/button collision in the old shell). The
 * phase-2 placeholder is gone (5.12): this body IS the resource module.
 *
 * ── THE PANEL'S OWN RANGE CONTROL (3.1 / AM-19) ───────────────────────
 *
 * The custom module's ladder is its OWN (15m / 1h / 24h / 7d — a subset
 * of the frozen `ResourcesWindow` union), and the frame's central-sourced
 * control does NOT govern it. The body carries the module's own labelled
 * control — "Timeline range" — whose option set is the frame's union with
 * the options this ladder cannot serve DISABLED. **The control's scope is
 * PER-PANEL (AM-19):** it drives the timeline panel + the anomalies
 * window ONLY — `live`, `self-cost` and the anomaly drill-down are
 * WINDOW-INDEPENDENT.
 *
 * ── THE DATA OWNERSHIP (5.4 / 5.11 — AM-16) ────────────────────────────
 *
 * The body owns THREE request classes:
 *
 *   1. the LIVE strip's 8 s poll — the moved `LiveTabComponent` keeps
 *      its own cadence and its `self-cost` chip (5.3); it is NOT gated
 *      by the window generation;
 *   2. the WINDOW load — `/api/resources/history`, superseded by a
 *      monotonic token on every window change;
 *   3. the ANOMALIES load — `/api/resources/anomalies`, its own token,
 *      invalidated by a window change as well.
 *
 * A window change invalidates classes 2 and 3 and NEVER class 1 (5.11).
 * A range change issues EXACTLY ONE `/history` request and NONE to
 * `/live` or `/self-cost` (5.4).
 *
 * ── THE PER-SOURCE STATE (AM-1 / 5.10) ─────────────────────────────────
 *
 * This tab renders ITS OWN source's state and NEVER the central door's:
 * it injects ONLY `ResourcesService` (the fail-open `/api/resources/*`
 * door). Under the default install posture (the central door answering
 * 503 `auth_not_configured`) tab 1 stays lit with real data — the
 * central door's state never reaches this body.
 */
@Component({
  selector: 'app-metrics-resource-tab',
  standalone: true,
  imports: [LiveTabComponent, TimelineTabComponent, AnomaliesPanelComponent],
  template: `
    <div class="resource-tab">
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

      <section class="resource-live-section" data-test="resource-live-strip">
        <app-resources-live />
      </section>

      <section class="resource-timeline-section" data-test="resource-timeline-panel">
        <app-resources-timeline
          [history]="history()"
          [anomalies]="anomalies() ?? []"
          [loading]="timelineLoading()"
          [error]="historyError()"
          (retry)="loadWindowData()"
        />
      </section>

      <section class="resource-anomalies-section" data-test="resource-anomalies-panel">
        <app-resource-anomalies
          [anomalies]="anomalies() ?? []"
          [loading]="anomaliesLoading()"
          [error]="anomaliesError()"
          (rowClick)="openAnomaly($event)"
          (retryRequested)="loadAnomalies()"
        />
      </section>
    </div>
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .resource-tab {
        display: flex;
        flex-direction: column;
        gap: 1.1rem;
        padding: 0.75rem 0;
      }

      .resource-range {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 0.4rem 0.75rem;
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

      .resource-live-section,
      .resource-timeline-section,
      .resource-anomalies-section {
        display: block;
      }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ResourceTabComponent {
  private readonly resources = inject(ResourcesService);
  private readonly destroyRef = inject(DestroyRef);

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
   * toggle disabled with its reason. The body never acts on the flag.
   */
  readonly baseline = input(false);

  /** The frame's range-change reporter — the body's own control edits it. */
  readonly onRangeChange = input<(option: MetricsRangeOption) => void>(() => undefined);

  /** The option set — the frame's union, with this ladder's disabled set. */
  readonly rangeOptions = METRICS_RANGE_OPTIONS;

  /** The retention-tier copy — the tiers BACK the ladder, they are not options. */
  readonly retentionTiersCopy = RESOURCES_LADDER_RETENTION_TIERS_COPY;

  /**
   * The window THIS body asks its own door for.
   *
   * `null` when the frame's value is not on the resources ladder — the
   * mapping refuses rather than carrying a value the door would 400 on
   * (3.6: no `/api/resources/*` request ever carries a window outside
   * `ResourcesWindow`).
   */
  readonly resourcesWindow = computed(() => metricsResourcesWindow(this.window()));

  // ── The body-owned data (5.4 / 5.11) ───────────────────────────────
  /** The window's history payload — `null` until the first good fetch. */
  readonly history = signal<HistoryPayload | null>(null);
  /** The window's anomaly rows — `null` until the first anomalies fetch. */
  readonly anomalies = signal<readonly Anomaly[] | null>(null);
  /** The history error branch — last-good chart retained underneath. */
  readonly historyError = signal<string | null>(null);
  /** The anomalies error branch — last-good rows retained underneath. */
  readonly anomaliesError = signal<string | null>(null);

  /** Skeleton renders only before the first successful history fetch. */
  readonly timelineLoading = computed(
    () => this.history() === null && this.historyError() === null,
  );

  /** The anomalies strip is loading before its first (good or failed) answer. */
  readonly anomaliesLoading = computed(() => this.anomalies() === null);

  /**
   * The supersede tokens (5.11 — AM-16). The window load and the
   * anomalies load each own a token; a window change invalidates BOTH.
   * The live strip's 8 s poll owns NO token here — it lives entirely in
   * the moved `LiveTabComponent` and is never gated by the window.
   */
  private windowSeq = 0;
  private anomaliesSeq = 0;

  /** The timeline panel child — the anomalies row click hands the anomaly to it. */
  private readonly timelinePanel = viewChild(TimelineTabComponent);

  constructor() {
    // The window loads follow the body's OWN window value (5.4). The
    // mapping guard decides: a refused window issues NO request at all.
    effect(() => {
      const windowValue = this.resourcesWindow();
      if (windowValue === null) {
        return;
      }
      this.loadWindowData();
      this.loadAnomalies();
    });
  }

  /**
   * The window load — class 2. One `/history` request per window value;
   * a newer value supersedes the in-flight response (it never lands).
   */
  loadWindowData(): void {
    const windowValue = this.resourcesWindow();
    if (windowValue === null) {
      return;
    }
    const token = ++this.windowSeq;
    this.historyError.set(null);
    this.resources
      .getHistory(windowValue)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (payload) => {
          if (token !== this.windowSeq) {
            return; // superseded by a newer window — stale payload never lands (5.11)
          }
          this.history.set(payload);
        },
        error: (err) => {
          if (token !== this.windowSeq) {
            return; // a newer load owns the outcome
          }
          // Retain last-good chart; only the error branch flips.
          this.historyError.set(
            'Unable to load resource history — showing the last known window.',
          );
          console.warn('[ResourceTab] history load failed:', err);
        },
      });
  }

  /**
   * The anomalies load — class 3. Its OWN token: invalidated by a window
   * change (5.11), and re-kickable from the panel's Retry CTA without
   * touching the history load's token.
   */
  loadAnomalies(): void {
    const windowValue = this.resourcesWindow();
    if (windowValue === null) {
      return;
    }
    const token = ++this.anomaliesSeq;
    this.anomaliesError.set(null);
    this.resources
      .getAnomalies(windowValue)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (payload) => {
          if (token !== this.anomaliesSeq) {
            return; // superseded — stale rows never land (5.11)
          }
          this.anomalies.set(payload.anomalies);
        },
        error: (err) => {
          if (token !== this.anomaliesSeq) {
            return;
          }
          this.anomaliesError.set('Unable to load anomalies — showing the last known list.');
          console.warn('[ResourceTab] anomalies load failed:', err);
        },
      });
  }

  /**
   * The anomalies panel's row click → the EXISTING drill-down (5.5).
   * The drill-down lives on the timeline panel; the body forwards the
   * wire anomaly to it.
   */
  openAnomaly(anomaly: Anomaly): void {
    this.timelinePanel()?.openDrilldown(anomaly);
  }

  /** Whether this ladder can serve an option (the visible enforcement). */
  optionEnabled(option: MetricsRangeOption): boolean {
    return metricsResourcesWindow(option) !== null;
  }

  /** The body's own control change handler. */
  onRangeSelected(value: string): void {
    const option = METRICS_RANGE_OPTIONS.find((candidate) => candidate === value);
    if (!option || !this.optionEnabled(option)) {
      return;
    }
    this.onRangeChange()(option);
  }
}
