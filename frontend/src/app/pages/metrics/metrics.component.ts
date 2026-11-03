import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { NgComponentOutlet } from '@angular/common';
import { ActivatedRoute, Router } from '@angular/router';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatTabsModule } from '@angular/material/tabs';

import { MetricsService } from '../../services/metrics.service';
import {
  METRICS_REGISTRY_DEGRADED_COPY,
  MetricsFamily,
  decodeMetricsDoorError,
} from '../../models/metrics.model';
import {
  METRICS_DEFAULT_TAB_ID,
  MetricsTabEntry,
  metricsTabById,
} from './tabs.manifest';
import { METRICS_TAB_BINDINGS } from './tabs/tabs.components';
import {
  EMPTY_METRICS_TAB_STATE,
  MetricsTabState,
  MetricsTabStateReporter,
} from './metrics-state';
import { MetricsFamilyCatalogComponent } from './families/families-tab.component';
import {
  METRICS_BASELINE_CUSTOM_SLOT_REASON,
  METRICS_BASELINE_FIDELITY_DISCLOSURE,
  METRICS_BASELINE_MAX_WINDOW_S,
  METRICS_BASELINE_RANGE_REASON,
  METRICS_RANGE_CUSTOM_SLOT_NOTE,
  METRICS_RANGE_OPTIONS,
  MetricsRangeOption,
  metricsCentralWindowSeconds,
  metricsRangeEnabledFor,
  metricsSnapNote,
  resolveWindowOnTabChange,
} from './metrics-range';

/**
 * The ROUTE-CHUNK MARKER (T1 phase 2, task 2.13 — B3).
 *
 * The bundle-baseline measurement tool locates the metrics route chunk
 * by THIS KEY — a lane-controlled constant — and never by a content
 * marker (`'metrics-empty-panel'`, which phase 8's empty-state rewrite
 * invalidates, or `'anomaly-marker'`, which phase 7's route retirement
 * moves). The literal is unique to the metrics page, so the route-fetch
 * set stays measurable across the lane's own edits.
 *
 * **NO LATER PHASE MAY CHANGE ITS VALUE.** The measurement tool
 * resolves it to EXACTLY ONE chunk; an unresolvable key is reported
 * ABSENT, never mis-resolved.
 */
export const METRICS_ROUTE_CHUNK_MARKER = 'ensemble-metrics-route-v1';

/**
 * The copy of the availability-driven empty panel (P5 task 5.7).
 *
 * The panel appears EXACTLY when no family is live. It is NOT the
 * kill-switch state — the door is never switch-gated, so the panel
 * never says "disabled" (that was the false banner D-8 forbids) and
 * never says "error" (a 200-empty response is not an error).
 */
export const METRICS_EMPTY_PANEL_COPY = 'No metrics available yet';

/**
 * The frame's INITIAL window — the default tab's OWN default.
 *
 * The frame never invents a default: the manifest owns it (the custom
 * module opens on the window its retired panel always opened on).
 */
const initialRangeOption: MetricsRangeOption =
  (metricsTabById(METRICS_DEFAULT_TAB_ID)?.defaultWindowS as MetricsRangeOption) ?? '1h';

/**
 * Metrics page shell — the consolidated `/metrics` surface.
 *
 * The shell owns:
 *
 *   * the TAB LIST — the manifest drives it (`tabs.manifest.ts`); the
 *     manifest id is the single slug source, and `?tab=<id>` deep
 *     links read and write it;
 *   * the FAMILY CATALOG — one load, not a poll — rendered in a
 *     FRAME-LEVEL DRAWER (the old Families tab is retired);
 *   * the PER-TAB STATE — the state is per SOURCE (AM-1): a `custom`
 *     slot renders its own source's state, a `central` slot renders the
 *     central door's, so a failing source never blanks another one.
 */
@Component({
  selector: 'app-metrics',
  standalone: true,
  imports: [
    NgComponentOutlet,
    MatButtonModule,
    MatTabsModule,
    MetricsFamilyCatalogComponent,
  ],
  templateUrl: './metrics.component.html',
  styleUrl: './metrics.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class MetricsComponent implements OnInit {
  private readonly metrics = inject(MetricsService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);

  /** The manifest-driven tab list — the label + id come from the manifest. */
  readonly tabs = METRICS_TAB_BINDINGS;

  /** The catalog — the family projection the door serves. */
  readonly families = signal<readonly MetricsFamily[]>([]);
  readonly catalogError = signal<string | null>(null);
  readonly catalogStale = signal(false);
  readonly catalogLoaded = signal(false);
  /**
   * The W-4 bound flag of the LAST catalog load — true when the door
   * served a bounded page (more families remain). Surfaced, never
   * silently dropped.
   */
  readonly catalogTruncated = signal(false);
  /**
   * Whether the LAST catalog load came from a FAILED registry read.
   *
   * The door answers a failed registry read with a **200 and an EMPTY
   * body** — the byte-identical shape of a legitimately empty registry —
   * so the failure is visible ONLY through `activation.registry_read_ok`.
   * The predicate is `=== false` (absent reads as healthy, mirroring the
   * backend's default-safe `getattr(provider, "registry_read_ok", True)`),
   * never a truthiness check.
   *
   * The state is PER-LOAD, never sticky: a subsequent healthy load
   * clears it.
   */
  readonly catalogRegistryDegraded = signal(false);

  /** The selected tab id — the manifest id is the single slug source. */
  readonly activeTabId = signal<string>(METRICS_DEFAULT_TAB_ID);

  /**
   * The frame's window VALUE — the union of the two vocabularies (3.1).
   *
   * ONE value, two vocabularies: the central tabs map it onto the door's
   * `window_s`, the custom panel maps it onto its own ladder. The frame
   * never owns a second list of the windows — the option set is DERIVED
   * from the manifest (`METRICS_RANGE_OPTIONS`).
   */
  readonly window = signal<MetricsRangeOption>(initialRangeOption);

  /**
   * Whether the previous-window comparison is ON.
   *
   * The baseline is a CENTRAL-tabs-only capability (AM-14): the custom
   * slot renders the toggle disabled with its reason, and the state is
   * cleared when the operator switches to it.
   */
  readonly baselineOn = signal(false);

  /**
   * The one-line meta note — the snap announcement (3.2).
   *
   * A snap is NEVER silent: the operator is told that the range moved.
   */
  readonly rangeNote = signal<string | null>(null);

  /**
   * The baseline's own note — why it was cleared or refused (3.5a).
   */
  readonly baselineNote = signal<string | null>(null);

  /** The frame-level disclosure — the baseline's fidelity limits (3.8). */
  readonly baselineDisclosure = METRICS_BASELINE_FIDELITY_DISCLOSURE;

  /** The option set — the union of the manifest's per-tab ladders. */
  readonly rangeOptions = METRICS_RANGE_OPTIONS;

  /** The frame-level family catalog drawer. */
  readonly drawerOpen = signal(false);

  /**
   * The PER-TAB state (AM-1).
   *
   * One entry per tab; each entry is written by that tab's OWN source.
   * There is deliberately NO global state — a global one is what let a
   * failing source blank the only lit tab.
   */
  readonly tabStates = signal<Record<string, MetricsTabState>>({});

  /** The empty panel's copy (never "disabled" — D-8 / AM-19(d)). */
  readonly emptyCopy = METRICS_EMPTY_PANEL_COPY;

  /** The degraded-registry badge's copy (the fact, never a cause). */
  readonly registryDegradedCopy = METRICS_REGISTRY_DEGRADED_COPY;

  /** The monotonic supersede token of the catalog load. */
  private catalogSeq = 0;

  /** The per-tab state reporters — one stable callback per tab. */
  private readonly reporters = new Map<string, MetricsTabStateReporter>();

  readonly liveFamilies = computed(() =>
    this.families().filter((family) => family.availability.state === 'live'),
  );

  readonly darkFamilies = computed(() =>
    this.families().filter((family) => family.availability.state !== 'live'),
  );

  /** Whether ANY family is live — the empty state's ONLY condition. */
  readonly anyFamilyLive = computed(() => this.liveFamilies().length > 0);

  /** The selected tab's index — the tab group follows the manifest id. */
  readonly activeIndex = computed(() =>
    this.tabs.findIndex((tab) => tab.entry.id === this.activeTabId()),
  );

  /** The SELECTED tab's manifest entry — the vocabulary's owner. */
  readonly activeTab = computed<MetricsTabEntry | null>(() =>
    metricsTabById(this.activeTabId()),
  );

  /**
   * Whether the ACTIVE slot is the central door's.
   *
   * The baseline and the frame's central-sourced range control are scoped
   * to it (AM-1 / AM-14 / AM-19).
   */
  readonly activeIsCentral = computed(() => this.activeTab()?.slot === 'central');

  /**
   * Whether the SELECTED tab can serve an option — the VISIBLE enforcement
   * (3.1).
   *
   * The door would answer a longer window with a 200 and a clamped value
   * and no marker (`daemon/routers/metrics.py:778`), so the control
   * DISABLES what the tab cannot serve instead of letting the operator
   * pick it.
   */
  rangeOptionEnabled(option: MetricsRangeOption): boolean {
    const tab = this.activeTab();
    return tab ? metricsRangeEnabledFor(option, tab) : false;
  }

  /**
   * The frame's range control is INERT on the custom slot (AM-19): the
   * custom panel carries its own labelled control, and a frame control that
   * appeared to govern it would be a lie.
   */
  readonly rangeControlDisabled = computed(() => !this.activeIsCentral());

  /** Why the frame's range control is inert on the custom slot. */
  readonly rangeControlReason = computed<string | null>(() =>
    this.activeIsCentral() ? null : METRICS_RANGE_CUSTOM_SLOT_NOTE,
  );

  /** The baseline's disabled reason on the custom slot (3.6). */
  readonly baselineDisabledReason = computed<string | null>(() =>
    this.activeIsCentral() ? null : METRICS_BASELINE_CUSTOM_SLOT_REASON,
  );

  /**
   * Whether the current window can carry a baseline.
   *
   * The baseline fetches 2W, so W must be at most 1 h — the substrate
   * ceiling (2 h) admits no 2W fetch.
   */
  readonly baselineAllowedForWindow = computed(() => {
    const seconds = metricsCentralWindowSeconds(this.window());
    return seconds !== null && seconds <= METRICS_BASELINE_MAX_WINDOW_S;
  });

  /**
   * The empty panel appears exactly when no family is live — driven by
   * the AVAILABILITY block, never by the kill-switch (D-8 / D-11b). A
   * failed catalog load is NOT an empty state: the door being
   * unreachable is an error, and the error banner says so.
   */
  readonly showEmptyPanel = computed(
    () =>
      this.catalogLoaded() &&
      this.catalogError() === null &&
      // A FAILED registry read is NOT an empty state: the payload is
      // UNKNOWN, not empty, so the empty panel must not fire (the
      // degraded badge carries the fact instead).
      !this.catalogRegistryDegraded() &&
      !this.anyFamilyLive(),
  );

  /** The per-tab inputs handed to each tab body by the outlet. */
  readonly tabInputs = computed<Record<string, Record<string, unknown>>>(() => {
    const states = this.tabStates();
    const out: Record<string, Record<string, unknown>> = {};
    for (const tab of this.tabs) {
      out[tab.entry.id] = {
        state: states[tab.entry.id] ?? EMPTY_METRICS_TAB_STATE,
        onState: this.tabStateReporter(tab.entry.id),
        // The frame owns the window (3.3): every slot maps it onto its own
        // door — the central tabs as `window_s`, the custom panel as its
        // own ladder name.
        window: this.window(),
        baseline: this.baselineOn(),
        onRangeChange: this.rangeChangeReporter,
      };
    }
    return out;
  });

  ngOnInit(): void {
    this.loadCatalog();
    // The deep link: the `?tab=` parameter selects the tab. The
    // subscription is the ONLY reader — the router replays the current
    // parameter on subscribe, so a separate snapshot read would apply
    // the same parameter twice (and write the URL twice).
    this.route.queryParamMap.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((params) => {
      this.applyDeepLink(params.get('tab'));
    });
  }

  /**
   * Apply the `?tab=` parameter.
   *
   * A KNOWN id selects that tab. An UNKNOWN (or legacy — `overview`,
   * `charts`, `families`) id falls back to the default tab, and the
   * URL is rewritten ONCE (`replaceUrl`, no history entry) so the URL
   * and the selected tab never disagree — without a redirect loop: the
   * rewrite makes the parameter known, and a known parameter writes
   * nothing.
   */
  private applyDeepLink(requested: string | null): void {
    const known = metricsTabById(requested);
    if (known) {
      this.selectTab(known);
      return;
    }
    const fallback = metricsTabById(METRICS_DEFAULT_TAB_ID);
    if (fallback) {
      this.selectTab(fallback);
    }
    if (requested !== null && requested !== METRICS_DEFAULT_TAB_ID) {
      this.writeTabParam(METRICS_DEFAULT_TAB_ID);
    }
  }

  /**
   * Select a tab and apply the keep-or-snap window rule.
   *
   * Re-selecting the SAME tab is a no-op: the rule must not fire twice
   * (a second snap would clear the announcement the operator just read).
   */
  private selectTab(tab: MetricsTabEntry): void {
    if (tab.id === this.activeTabId()) {
      return;
    }
    this.activeTabId.set(tab.id);
    this.applyWindowForTab(tab);
  }

  /** Write the selected tab back to the URL (the query is preserved). */
  private writeTabParam(id: string): void {
    this.router.navigate([], {
      queryParams: { tab: id },
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  }

  /** The tab group's selection changed — the URL follows it. */
  onTabChange(index: number): void {
    const tab = this.tabs[index];
    if (!tab) {
      return;
    }
    const id = tab.entry.id;
    if (id === this.activeTabId()) {
      // Already selected — writing again would be a navigation storm.
      return;
    }
    this.selectTab(tab.entry);
    this.writeTabParam(id);
  }

  /** Open or close the family-catalog drawer. */
  toggleDrawer(): void {
    this.drawerOpen.update((open) => !open);
  }

  /** Close the family-catalog drawer. */
  closeDrawer(): void {
    this.drawerOpen.set(false);
  }

  /**
   * Apply a state patch to ONE tab.
   *
   * A tab body can therefore never write another tab's state — the
   * per-source isolation is structural, not a convention.
   */
  applyTabState(tabId: string, patch: Partial<MetricsTabState>): void {
    this.tabStates.update((states) => ({
      ...states,
      [tabId]: { ...EMPTY_METRICS_TAB_STATE, ...states[tabId], ...patch },
    }));
  }

  /**
   * The operator picked a window in the frame's control (3.1/3.3).
   *
   * An option the SELECTED tab cannot serve is NEVER applied: the control
   * disables it, and the guard refuses it if the event is forged.
   */
  selectWindow(option: MetricsRangeOption): void {
    const tab = this.activeTab();
    if (!tab || !metricsRangeEnabledFor(option, tab)) {
      return;
    }
    if (option === this.window()) {
      return;
    }
    this.window.set(option);
    this.rangeNote.set(null);
    // The baseline rides the range: crossing 1 h clears it (3.5a).
    this.applyBaselineRangeGuard();
  }

  /** The frame's control change handler — the option comes from our own list. */
  onRangeChange(value: string): void {
    const option = METRICS_RANGE_OPTIONS.find((candidate) => candidate === value);
    if (!option) {
      return;
    }
    this.selectWindow(option);
  }

  /**
   * The baseline toggle (3.4/3.5a).
   *
   * The toggle is refused — with its reason — on the custom slot and
   * beyond the 1 h guard. It is never silently ignored.
   */
  setBaseline(on: boolean): void {
    if (!this.activeIsCentral()) {
      this.baselineNote.set(METRICS_BASELINE_CUSTOM_SLOT_REASON);
      return;
    }
    if (on && !this.baselineAllowedForWindow()) {
      this.baselineNote.set(METRICS_BASELINE_RANGE_REASON);
      this.baselineOn.set(false);
      return;
    }
    this.baselineOn.set(on);
    if (!on) {
      this.baselineNote.set(null);
    }
  }

  /** The frame's toggle change handler. */
  onBaselineChange(checked: boolean): void {
    this.setBaseline(checked);
  }

  /**
   * The range crossed the baseline's guard — clear the baseline and SAY
   * why (3.5a).
   */
  private applyBaselineRangeGuard(): void {
    if (!this.baselineOn()) {
      return;
    }
    if (this.baselineAllowedForWindow()) {
      return;
    }
    this.baselineOn.set(false);
    this.baselineNote.set(METRICS_BASELINE_RANGE_REASON);
  }

  /**
   * The tab changed — keep the window when the new tab can serve it,
   * otherwise snap to that tab's default AND announce the snap (3.2).
   */
  private applyWindowForTab(tab: MetricsTabEntry): void {
    const resolution = resolveWindowOnTabChange(this.window(), tab);
    if (resolution.snapped) {
      this.window.set(resolution.window);
      this.rangeNote.set(metricsSnapNote(resolution.window, tab.label));
    }
    if (tab.slot !== 'central') {
      // The baseline is a central-tabs-only capability (AM-14).
      this.baselineOn.set(false);
    }
    this.applyBaselineRangeGuard();
  }

  /** The stable per-tab state reporter. */
  private tabStateReporter(tabId: string): MetricsTabStateReporter {
    let reporter = this.reporters.get(tabId);
    if (!reporter) {
      reporter = (patch: Partial<MetricsTabState>) => this.applyTabState(tabId, patch);
      this.reporters.set(tabId, reporter);
    }
    return reporter;
  }

  /**
   * The stable range-change reporter — the custom panel's own labelled
   * control edits the frame's value through it (AM-19).
   */
  private readonly rangeChangeReporter = (option: MetricsRangeOption): void => {
    this.selectWindow(option);
  };

  /**
   * Publish the CENTRAL door's outcome to the central tabs ONLY.
   *
   * The custom slot is never touched here — that is the AM-1 seam: a
   * failing central source cannot blank the custom tab's state.
   */
  private publishCentralSource(patch: {
    doorState: MetricsTabState['doorState'];
    errorClass: MetricsTabState['errorClass'];
    families?: Record<string, readonly MetricsFamily[]>;
  }): void {
    for (const tab of this.tabs) {
      const entry = tab.entry;
      if (entry.disposition !== 'bound' || entry.slot !== 'central') {
        continue;
      }
      this.applyTabState(entry.id, {
        doorState: patch.doorState,
        errorClass: patch.errorClass,
        ...(patch.families ? { families: patch.families[entry.id] ?? [] } : {}),
      });
    }
  }

  /** The bound families of every central tab, projected from the catalog. */
  private boundFamiliesFor(
    all: readonly MetricsFamily[],
  ): Record<string, readonly MetricsFamily[]> {
    const out: Record<string, readonly MetricsFamily[]> = {};
    for (const tab of this.tabs) {
      const entry = tab.entry;
      if (entry.disposition !== 'bound' || entry.slot !== 'central') {
        continue;
      }
      out[entry.id] = entry.familyIds
        .map((id) => all.find((family) => family.id === id))
        .filter((family): family is MetricsFamily => family !== undefined);
    }
    return out;
  }

  /** Load (or reload) the family catalog. */
  loadCatalog(): void {
    const token = ++this.catalogSeq;
    this.metrics
      .getFamilies()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (payload) => {
          if (token !== this.catalogSeq) {
            return;
          }
          const hadPreviousCatalog = this.families().length > 0;
          this.families.set(payload.families);
          this.catalogError.set(null);
          // A FAILED registry read answers 200 with an EMPTY body — the
          // degraded flag is the ONLY way to tell it from an empty
          // registry. The predicate is `=== false` (absent = healthy).
          const degraded = payload.activation?.registry_read_ok === false;
          this.catalogRegistryDegraded.set(degraded);
          // The stale badge means "last known catalog" — it fires only
          // when a PREVIOUS good catalog exists.
          this.catalogStale.set(degraded && hadPreviousCatalog);
          this.catalogLoaded.set(true);
          // The door may serve a BOUNDED page (W-4) — surfaced, never
          // silently dropped.
          this.catalogTruncated.set(payload.truncated === true);
          this.publishCentralSource({
            doorState: 'ok',
            errorClass: null,
            families: this.boundFamiliesFor(payload.families),
          });
        },
        error: (err: unknown) => {
          if (token !== this.catalogSeq) {
            return;
          }
          // A-5: decode the BODY and branch the copy — a 403 scope miss
          // or a 503 fail-closed door is NOT an unreachable door. The
          // last-good catalog is retained in EVERY branch.
          const decoded = decodeMetricsDoorError(err);
          this.catalogError.set(decoded.copy);
          this.catalogStale.set(this.families().length > 0);
          this.catalogLoaded.set(true);
          // The bound families are NOT cleared — the last-good catalog
          // is retained in every branch.
          this.publishCentralSource({ doorState: decoded.code, errorClass: decoded.code });
        },
      });
  }

  /** The catalog drawer asked for a fresh catalog. */
  readonly onReloadCatalog = (): void => {
    this.loadCatalog();
  };
}
