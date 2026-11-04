import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnDestroy,
  OnInit,
  computed,
  effect,
  inject,
  input,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { DecimalPipe } from '@angular/common';

import { MetricsService } from '../../../services/metrics.service';
import {
  METRICS_NO_LAST_GOOD_SUFFIX,
  METRICS_STALE_SUFFIX_VALUES,
  MetricsSnapshotPayload,
  MetricsSnapshotSeries,
  decodeMetricsDoorError,
  formatMetricsLabels,
  formatMetricsValue,
} from '../../../models/metrics.model';

/**
 * Overview tab — the CURRENT published values (`GET /api/metrics/snapshot`).
 *
 * Polling discipline (P5 task 5.5):
 *   - a NAMED `POLL_INTERVAL_MS` (10 s) — never a bare literal;
 *   - `setInterval` + `clearInterval` — the interval is torn down in
 *     `ngOnDestroy` (the bindings pin greps for both);
 *   - the poll STOPS when the tab is not the selected one (the shell
 *     passes `active`) and when the browser tab is hidden;
 *   - a SKIP-IF-IN-FLIGHT guard — a slow door never piles up ticks;
 *   - MONOTONIC SUPERSEDE TOKENS — a slower earlier response can never
 *     overwrite a newer one;
 *   - ONE error banner + a retry CTA that RETAINS the last-good data
 *     (a transient failure must not blank a page the operator reads);
 *   - the STALE degradation — an unreachable door (or an expired
 *     session) leaves the last-known view on screen, MARKED STALE. It
 *     never blanks and never polls harder.
 */
@Component({
  selector: 'app-metrics-overview',
  standalone: true,
  imports: [DecimalPipe, MatButtonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  // The shared tab-surface styles (banner/badge/hint/meta) live in the
  // shared partial — the A-5 consolidation; the palette/geometry
  // authority is metrics-door-state.component.ts (see the partial's
  // header). Only this tab's OWN rules stay inline below.
  styleUrls: ['../shared/tab-surface.scss'],
  template: `
    <div class="overview">
      @if (error(); as message) {
        <div class="tab-banner" data-test="overview-error">
          <span class="tab-banner-text">{{ message }}</span>
          <button mat-stroked-button type="button" (click)="refresh()" data-test="overview-retry">
            Retry
          </button>
        </div>
      }

      @if (stale()) {
        <span class="stale-badge" data-test="overview-stale">STALE — last known values</span>
      }

      @if (!snapshot() && !error()) {
        <p class="tab-hint" data-test="overview-loading">Loading the current values…</p>
      }

      @if (snapshot(); as payload) {
        <p class="tab-meta" data-test="overview-meta">
          schema v{{ payload.schema_version }} · vocabulary v{{ payload.vocabulary_version }} ·
          boot {{ payload.restart_epoch }}
          @if (payload.truncated) {
            · page bounded — more series remain on the door
          }
        </p>
        <div class="stat-grid" data-test="overview-grid">
          @for (card of cards(); track card.key) {
            <div class="stat-card" [attr.data-series]="card.name">
              <div class="stat-name">{{ card.name }}</div>
              @if (card.labels) {
                <div class="stat-labels">{{ card.labels }}</div>
              }
              <div class="stat-value">{{ card.value }}</div>
              <div class="stat-meta">
                {{ card.kind }} · published {{ card.age | number: '1.0-1' }}s ago
              </div>
            </div>
          }
        </div>
        @if (cards().length === 0) {
          <p class="tab-hint" data-test="overview-empty">No series published yet.</p>
        }
      }
    </div>
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .overview {
        display: block;
        padding: 0.75rem 0;
      }

      .stat-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr));
        gap: 0.6rem;
      }

      .stat-card {
        border: 1px solid #1e293b;
        border-radius: 6px;
        padding: 0.55rem 0.7rem;
        background: #0f172a;
      }

      .stat-name {
        font-size: 0.78rem;
        color: #cbd5e1;
        word-break: break-all;
      }

      .stat-labels {
        font-size: 0.7rem;
        color: #64748b;
        word-break: break-all;
      }

      .stat-value {
        font-size: 1.25rem;
        color: #f1f5f9;
        font-variant-numeric: tabular-nums;
      }

      .stat-meta {
        font-size: 0.68rem;
        color: #64748b;
      }
    `,
  ],
})
export class OverviewTabComponent implements OnInit, OnDestroy {
  private readonly metrics = inject(MetricsService);
  private readonly destroyRef = inject(DestroyRef);

  /** The poll cadence, in milliseconds (10 s — the contract's cadence). */
  readonly POLL_INTERVAL_MS = 10_000;

  /** Whether THIS tab is the selected one (the shell's visibility gate). */
  readonly active = input(false);

  readonly snapshot = signal<MetricsSnapshotPayload | null>(null);
  readonly error = signal<string | null>(null);
  readonly stale = signal(false);

  /** The browser tab's visibility (a hidden browser tab stops the poll). */
  private readonly browserVisible = signal(true);

  /** The poll tick's skip-if-in-flight guard. */
  private inFlight = 0;

  /** The monotonic supersede token — a newer response always wins. */
  private requestSeq = 0;

  private pollHandle: ReturnType<typeof setInterval> | null = null;

  private readonly onVisibilityChange = (): void => {
    this.browserVisible.set(document.visibilityState !== 'hidden');
  };

  /** The stat cards — the current values, one card per published series. */
  readonly cards = computed(() => {
    const payload = this.snapshot();
    if (!payload) {
      return [];
    }
    return payload.series.map((entry: MetricsSnapshotSeries, index: number) => ({
      key: `${entry.name}-${index}`,
      name: entry.name,
      labels: formatMetricsLabels(entry.labels),
      value: formatMetricsValue(entry.value),
      kind: entry.kind,
      age: entry.last_publish_age_s,
    }));
  });

  constructor() {
    effect(() => {
      if (this.shouldPoll()) {
        this.startPolling();
      } else {
        this.stopPolling();
      }
    });
  }

  ngOnInit(): void {
    document.addEventListener('visibilitychange', this.onVisibilityChange);
  }

  ngOnDestroy(): void {
    document.removeEventListener('visibilitychange', this.onVisibilityChange);
    this.stopPolling();
  }

  /** The retry CTA — a user-initiated refresh always fires. */
  refresh(): void {
    this.fetchTick(true);
  }

  private shouldPoll(): boolean {
    return this.active() && this.browserVisible();
  }

  private startPolling(): void {
    if (this.pollHandle !== null) {
      return;
    }
    this.fetchTick();
    this.pollHandle = setInterval(() => this.fetchTick(), this.POLL_INTERVAL_MS);
  }

  private stopPolling(): void {
    if (this.pollHandle !== null) {
      clearInterval(this.pollHandle);
      this.pollHandle = null;
    }
  }

  private fetchTick(force = false): void {
    if (!force && this.inFlight > 0) {
      return;
    }
    this.inFlight += 1;
    const token = ++this.requestSeq;
    this.metrics
      .getSnapshot()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (payload) => {
          this.inFlight -= 1;
          if (token !== this.requestSeq) {
            return;
          }
          this.snapshot.set(payload);
          this.error.set(null);
          this.stale.set(false);
        },
        error: (err: unknown) => {
          this.inFlight -= 1;
          if (token !== this.requestSeq) {
            return;
          }
          // A-5: the copy branches on the decoded failure class.
          //
          // 8.4 — THE COPY STATES THE TRUTH FOR THE CASE THAT IS
          // HAPPENING: the old sentence claimed "the last known values
          // are kept" UNCONDITIONALLY, so a failure with NO last-good
          // promised a retention that never happened while the page
          // rendered no values at all. The suffix is now chosen by the
          // FACT: a last-good exists ⇒ the STALE badge carries it; no
          // last-good ⇒ the copy says so.
          const decoded = decodeMetricsDoorError(err);
          const hasLastGood = this.snapshot() !== null;
          this.error.set(
            `${decoded.copy} — ${
              hasLastGood ? METRICS_STALE_SUFFIX_VALUES : METRICS_NO_LAST_GOOD_SUFFIX
            }.`,
          );
          this.stale.set(this.snapshot() !== null);
        },
      });
  }
}
