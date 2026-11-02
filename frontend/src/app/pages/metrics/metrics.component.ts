import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  Type,
  computed,
  inject,
  signal,
} from '@angular/core';
import { NgComponentOutlet } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatTabsModule } from '@angular/material/tabs';

import { MetricsService } from '../../services/metrics.service';
import {
  MetricsChartRole,
  MetricsFamily,
  metricsChartRoleForKind,
} from '../../models/metrics.model';
import { OverviewTabComponent } from './overview/overview-tab.component';
import { ChartsTabComponent, MetricsChartSeriesSpec } from './charts/charts-tab.component';
import { FamiliesTabComponent } from './families/families-tab.component';

/**
 * A single section registered in the Metrics page shell's LOCAL section
 * registry — the `resources.component.ts:35-49` house pattern. Adding a
 * tab later is a one-line append here; no template change.
 */
export interface MetricsSection {
  readonly id: string;
  readonly label: string;
  readonly component: Type<unknown>;
}

/** The door's selector ceiling — an over-ceiling request is rejected. */
export const METRICS_SELECTOR_CEILING = 50;

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
 * Metrics page shell — the developer-facing charts surface on the
 * central-metrics query door (P5 task 5.4).
 *
 * The shell owns the FAMILY CATALOG (one load, not a poll): the
 * availability projection the empty state depends on lives here, and
 * the Families tab renders the same projection.
 *
 * The three tab bodies are MOUNTED LAZILY (Material's tab group only
 * instantiates the selected body) and the shell passes each body the
 * `active` flag — a body that is not the selected tab STOPS its poll.
 */
@Component({
  selector: 'app-metrics',
  standalone: true,
  imports: [
    NgComponentOutlet,
    MatButtonModule,
    MatTabsModule,
    OverviewTabComponent,
    ChartsTabComponent,
    FamiliesTabComponent,
  ],
  templateUrl: './metrics.component.html',
  styleUrl: './metrics.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class MetricsComponent implements OnInit {
  private readonly metrics = inject(MetricsService);
  private readonly destroyRef = inject(DestroyRef);

  /** The LOCAL section registry — a one-line append adds a tab. */
  readonly sections: readonly MetricsSection[] = [
    { id: 'overview', label: 'Overview', component: OverviewTabComponent },
    { id: 'charts', label: 'Charts', component: ChartsTabComponent },
    { id: 'families', label: 'Families', component: FamiliesTabComponent },
  ];

  /** The catalog — the family projection the door serves. */
  readonly families = signal<readonly MetricsFamily[]>([]);
  readonly catalogError = signal<string | null>(null);
  readonly catalogStale = signal(false);
  readonly catalogLoaded = signal(false);

  /** The selected section — the visibility gate the tab bodies poll on. */
  readonly activeTabId = signal<string>('overview');

  /** The empty panel's copy (never "disabled" — D-8 / AM-19(d)). */
  readonly emptyCopy = METRICS_EMPTY_PANEL_COPY;

  /** The monotonic supersede token of the catalog load. */
  private catalogSeq = 0;

  readonly liveFamilies = computed(() =>
    this.families().filter((family) => family.availability.state === 'live'),
  );

  readonly darkFamilies = computed(() =>
    this.families().filter((family) => family.availability.state !== 'live'),
  );

  /** Whether ANY family is live — the empty state's ONLY condition. */
  readonly anyFamilyLive = computed(() => this.liveFamilies().length > 0);

  /**
   * The empty panel appears exactly when no family is live — driven by
   * the AVAILABILITY block, never by the kill-switch (D-8 / D-11b). A
   * failed catalog load is NOT an empty state: the door being
   * unreachable is an error, and the error banner says so.
   */
  readonly showEmptyPanel = computed(
    () => this.catalogLoaded() && this.catalogError() === null && !this.anyFamilyLive(),
  );

  /**
   * The chart-primary series — the roster's families (the catalog's
   * `role === 'chart'`) with their rendering mode. The door REJECTS an
   * over-ceiling selector, so the list is capped at the ceiling.
   */
  readonly chartSpecs = computed<readonly MetricsChartSeriesSpec[]>(() => {
    const out: MetricsChartSeriesSpec[] = [];
    for (const family of this.families()) {
      if (family.role !== 'chart') {
        continue;
      }
      for (const name of family.names) {
        const chartRole: MetricsChartRole =
          name.chart_role ?? metricsChartRoleForKind(name.kind);
        out.push({ name: name.name, chartRole });
      }
    }
    return out.slice(0, METRICS_SELECTOR_CEILING);
  });

  /** The per-section inputs handed to each tab body by the outlet. */
  readonly tabInputs = computed<Record<string, Record<string, unknown>>>(() => {
    const activeId = this.activeTabId();
    const specs = this.chartSpecs();
    const families = this.families();
    const out: Record<string, Record<string, unknown>> = {};
    for (const section of this.sections) {
      if (section.id === 'charts') {
        out[section.id] = { active: activeId === section.id, chartSpecs: specs };
      } else if (section.id === 'families') {
        out[section.id] = {
          active: activeId === section.id,
          families,
          onReload: this.onReloadCatalog,
        };
      } else {
        out[section.id] = { active: activeId === section.id };
      }
    }
    return out;
  });

  ngOnInit(): void {
    this.loadCatalog();
  }

  /** The tab group's selection changed — the visibility gate follows it. */
  onTabChange(index: number): void {
    const section = this.sections[index];
    if (section) {
      this.activeTabId.set(section.id);
    }
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
          this.families.set(payload.families);
          this.catalogError.set(null);
          this.catalogStale.set(false);
          this.catalogLoaded.set(true);
        },
        error: () => {
          if (token !== this.catalogSeq) {
            return;
          }
          this.catalogError.set('The metrics door is unreachable.');
          this.catalogStale.set(this.families().length > 0);
          this.catalogLoaded.set(true);
        },
      });
  }

  /** The Families tab asked for a fresh catalog. */
  readonly onReloadCatalog = (): void => {
    this.loadCatalog();
  };
}
