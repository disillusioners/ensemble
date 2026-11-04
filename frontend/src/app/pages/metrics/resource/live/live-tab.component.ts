import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnDestroy,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { forkJoin } from 'rxjs';
import {
  LivePayload,
  SelfCostPayload,
  parseNaiveUtc,
} from '../../../../models/resources.models';
import { ResourcesService } from '../../../../services/resources.service';

/**
 * Live tab — gated 8 s poll of `/api/resources/live` + `/self-cost`.
 *
 * Semantics (D6 / D7, proven by `live-tab.component.spec.ts`):
 *  - ONE `forkJoin({ live, selfCost })` per tick (job-queue-indicator
 *    precedent) — the snapshot stays internally consistent.
 *  - The skeleton fires ONLY before the first successful fetch;
 *    background refreshes retain the last good data and never flash
 *    the skeleton.
 *  - A failing tick flips the SINGLE error branch (retry CTA). Retained
 *    data keeps rendering underneath it — a transient poll failure
 *    must not blank a page the user is reading. While a refresh is
 *    in flight with last-known data retained, the banner is SUPPRESSED
 *    (no flicker); a failed-first fetch (no data) keeps the branch up.
 *  - A tick whose predecessor is still in flight is SKIPPED, never
 *    queued (R4 — a slow/hung endpoint cannot pile up ticks).
 *  - The tick lives in a Material-lazy-mounted tab body, so leaving
 *    the tab destroys the component and `ngOnDestroy` clears the
 *    interval — no polling while the tab is inactive.
 */
@Component({
  selector: 'app-metrics-live',
  standalone: true,
  imports: [CommonModule, MatButtonModule, MatIconModule, MatTooltipModule],
  templateUrl: './live-tab.component.html',
  styleUrl: './live-tab.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LiveTabComponent implements OnInit, OnDestroy {
  private readonly resources = inject(ResourcesService);
  private readonly destroyRef = inject(DestroyRef);

  /** Poll interval, in milliseconds (D6). */
  readonly POLL_INTERVAL_MS = 8000;

  readonly live = signal<LivePayload | null>(null);
  readonly selfCost = signal<SelfCostPayload | null>(null);
  readonly error = signal<string | null>(null);

  /** Skip-if-in-flight guard (R4) — read by the tick before fetching. */
  private readonly fetchInFlight = signal(false);

  /** Skeleton renders only before the first successful fetch (D7). */
  readonly showSkeleton = computed(() => this.live() === null && this.error() === null);

  /**
   * Exactly ONE error branch — banner, never per-widget spinners.
   * Suppressed ONLY while a refresh is in flight with last-known data
   * retained (anti-flicker); a failure with NO data (failed first
   * fetch / no-data retry) keeps the branch up unconditionally.
   */
  readonly showError = computed(
    () => this.error() !== null && !(this.fetchInFlight() && this.live() !== null),
  );

  /** Retained last-good snapshot (also survives background failures). */
  readonly showContent = computed(() => this.live() !== null);

  private pollHandle: ReturnType<typeof setInterval> | null = null;

  ngOnInit(): void {
    this.fetchTick();
    this.pollHandle = setInterval(() => this.fetchTick(), this.POLL_INTERVAL_MS);
  }

  ngOnDestroy(): void {
    if (this.pollHandle !== null) {
      clearInterval(this.pollHandle);
      this.pollHandle = null;
    }
  }

  /** Retry CTA — re-kicks the tick immediately, not at the next interval. */
  onRetry(): void {
    this.fetchTick();
  }

  private fetchTick(): void {
    if (this.fetchInFlight()) {
      return;
    }
    this.fetchInFlight.set(true);
    // NOTE: the error message is intentionally NOT cleared here — the
    // banner is suppressed while in flight via `showError`, so a failed
    // tick can re-show the SAME message without a flicker gap.
    forkJoin({
      live: this.resources.getLive(),
      selfCost: this.resources.getSelfCost(),
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ live, selfCost }) => {
          this.fetchInFlight.set(false);
          this.live.set(live);
          this.selfCost.set(selfCost);
          this.error.set(null); // success retires the banner
        },
        error: (err) => {
          this.fetchInFlight.set(false);
          // Retain last-good data; only the error branch flips.
          this.error.set(
            'Unable to reach the resource monitor — showing the last known snapshot.',
          );
          console.warn('[ResourcesLive] poll tick failed:', err);
        },
      });
  }

  // ── Display helpers ─────────────────────────────────────────────

  /** Computed '—' or numeric MB label for the cgroup memory card.
   *
   * The template writes ``{{ fmt(cgroup?.memory?.xxx_mb, 0) }}``
   * directly — works for plain scalars because ``?.`` returns
   * ``undefined`` for missing paths and ``fmt`` returns '—' for
   * any non-finite value.
   */
  /** Computed '—' or number label for `cgroup.cpu.usage_usec`. The
   * per-tick division has to happen INSIDE the helper: a literal
   * ``payload.cgroup?.cpu?.usage_usec / 1_000_000`` in the template
   * coerces ``null / number`` to ``0`` (JS spec) before ``fmt``
   * sees it, surfacing as '0s' instead of '—'. The TS-side null
   * check short-circuits the division. */
  readonly cgroupUsageSec = computed(() => {
    const usec = this.live()?.cgroup?.cpu?.usage_usec;
    return usec === null || usec === undefined ? '—' : this.fmt(usec / 1_000_000, 0);
  });

  /** Companion helper for `cgroup.cpu.throttled_usec`. */
  readonly cgroupThrottledSec = computed(() => {
    const usec = this.live()?.cgroup?.cpu?.throttled_usec;
    return usec === null || usec === undefined ? '—' : this.fmt(usec / 1_000_000, 2);
  });

  readonly selfCostBuffersMb = computed(() => {
    const selfCost = this.selfCost();
    return selfCost === null ? '' : (selfCost.buffers_bytes / 1_048_576).toFixed(1);
  });

  // ── The attribution card's honesty helpers (phase 6, 6.4 / 6.5 / 6.6) ──

  /**
   * The payload's ``coverage_pct`` VERBATIM (phase 6, task 6.5 / AM-13).
   *
   * **THE FE COMPUTES NO COVERAGE PERCENTAGE.** The daemon computes
   * ``coverage_pct = min(1.0, attributed / anon_bytes)`` against the
   * ANON POOL (``snap.daemon.anon_kb``) — a basis the card's displayed
   * components (attributed + unattributed) do NOT sum to — so any
   * FE-side recomputation, rescaling (×100) or rounding would be an
   * invented basis, not a display. The card therefore renders the wire
   * value AS SERVED: a string conversion only, no arithmetic, and '—'
   * for a non-finite wire (the §2.8 degraded-shape guard, same as
   * ``fmt``). The old ``fmt(coverage_pct, 0) + '%'`` rounded a 0–1
   * fraction to "0 %" — the false statement the audit caught.
   */
  readonly coverageVerbatim = computed(() => {
    const coverage = this.live()?.attribution.coverage_pct;
    return coverage === null || coverage === undefined || !Number.isFinite(coverage)
      ? '—'
      : String(coverage);
  });

  /**
   * Whether the payload's own fields DISAGREE (phase 6, task 6.4).
   *
   * The card shows the aggregate split (attributed + unattributed); the
   * daemon's RSS reading sits on the SAME strip. The split is the anon
   * pool's partition, so a sum EXCEEDING the daemon's own RSS reading
   * is impossible under sane physics — that is the audit's exact shape
   * (71 + 1,119 = 1,190 MB against an RSS of 633 MB). The check is a
   * COMPARISON of payload fields (allowed); it never computes a
   * coverage figure (banned). A 2 MB slack covers the card's own
   * integer-MB renders. Either field missing ⇒ no claim.
   */
  readonly attributionDiscrepancy = computed(() => {
    const attribution = this.live()?.attribution;
    const rssMb = this.live()?.daemon.rss_mb;
    if (!attribution || rssMb === null || rssMb === undefined || !Number.isFinite(rssMb)) {
      return false;
    }
    const displayedSum = attribution.attributed_mb + attribution.unattributed_mb;
    return displayedSum > rssMb + 2;
  });

  /** Naive-UTC wire `ts` → explicit UTC label (D10). */
  tsLabel(ts: string): string {
    const date = parseNaiveUtc(ts);
    return Number.isNaN(date.getTime())
      ? ts
      : date.toISOString().replace('T', ' ').slice(0, 19);
  }

  uptimeLabel(uptimeS: number | null | undefined): string {
    if (uptimeS === null || uptimeS === undefined || !Number.isFinite(uptimeS)) {
      return '—';
    }
    const total = Math.max(0, Math.floor(uptimeS));
    const days = Math.floor(total / 86_400);
    const hours = Math.floor((total % 86_400) / 3_600);
    const minutes = Math.floor((total % 3_600) / 60);
    const seconds = total % 60;
    const clock = `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
    return days > 0 ? `${days}d ${clock}` : clock;
  }

  /**
   * Numeric display formatter with a degraded-wire guard.
   *
   * The §2.8 /live endpoint is a 200 with a valid-but-degraded body
   * on monitor-absent / first-tick / non-cgroup-v2 paths (D11). Scalars
   * arrive as literal ``None`` — the previous `value.toFixed(digits)`
   * would throw a TypeError during change detection and blank the
   * page. This helper survives ``null`` / ``undefined`` / ``NaN`` /
   * ``±Infinity`` by returning '—' (the §2.8 placeholder glyph),
   * keeping the single error branch reserved for genuine unreachability.
   */
  fmt(value: number | null | undefined, digits = 1): string {
    if (value === null || value === undefined || !Number.isFinite(value)) {
      return '—';
    }
    return value.toFixed(digits);
  }
}
