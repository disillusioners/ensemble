import {
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  DOCUMENT,
  effect,
  HostListener,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatChipsModule } from '@angular/material/chips';
import { MatDividerModule } from '@angular/material/divider';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { Clipboard } from '@angular/cdk/clipboard';
import { CdkTrapFocus } from '@angular/cdk/a11y';
import { MatSnackBar } from '@angular/material/snack-bar';

import { SnapshotService } from '../../services/snapshot.service';
import {
  SnapshotDetailResponse,
  SnapshotStatus,
} from '../../models/snapshot.model';

/** 200KB digest guard (brief §4(e)). */
const DIGEST_GUARD_BYTES = 200 * 1024;

/**
 * STATEFUL detail drawer (pass 4 amendment #7 — leader ruling).
 *
 * The drawer component OWNS:
 *
 * * the detail fetch on `snapshotId` change (no digest — the
 *   digest is lazy, see `onToggleDigest`);
 * * the `detail / detailLoading / detailError` signals;
 * * the digest fetch + the 200KB guard + the digest error state;
 * * the `showDigest` toggle (collapsed by default).
 *
 * The host page (`SnapshotsComponent`) only owns the id-swap signal
 * that hands the drawer its new snapshot to load; the page does
 * NOT pre-fetch the detail (so a list-page error doesn't lose the
 * user the drawer-on-click affordance).
 *
 * The seven sections (D-5 — the warm-spawn-count section is omitted
 * in v1; no `project_name` join):
 *
 *  1. Task summary
 *  2. Git anchor
 *  3. Runtime / Model
 *  4. Supersedes chain
 *  5. Tags
 *  6. Timestamps
 *  7. Context (project + agent)
 *
 * `formatRelative` is duplicated locally (vs. imported) to keep the
 * drawer's self-contained rendering logic out of the table's
 * template-helper surface — the two are the same algorithm and both
 * are pure.
 */
@Component({
  selector: 'app-snapshot-detail-drawer',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CommonModule,
    MatButtonModule,
    MatChipsModule,
    MatDividerModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    CdkTrapFocus,
  ],
  templateUrl: './snapshot-detail-drawer.component.html',
  styleUrl: './snapshot-detail-drawer.component.scss',
})
export class SnapshotDetailDrawerComponent {
  // ── Inputs (v1 pass 4 #7) ───────────────────────────────────────
  /** The id of the snapshot to load. The drawer fetches its own detail. */
  readonly snapshotId = input.required<string>();
  /** Reserved for future full-page reuse (defaults to drawer mode). */
  readonly isDrawerMode = input<boolean>(true);
  /**
   * Warmed-spawn count for the open snapshot (v2 AC-4.7). When the
   * value is non-null, the Timestamps section adds a 'Last warmed'
   * kv-row showing the count. When null the row shows '—'.
   * Sourced from `SnapshotUsageMetrics.spawn_counts_per_snapshot` by
   * the page host; the drawer's own detail fetch does NOT carry the
   * metric (the v1 BE contract — D-5).
   */
  readonly warmedSpawnCount = input<number | null>(null);

  // ── Outputs ──────────────────────────────────────────────────
  readonly close = output<void>();
  readonly navigateToPredecessor = output<string>();
  /** Emits when the user clicks the Copy ID button (host shows snackbar). */
  readonly copyId = output<string>();

  // ── Injected ─────────────────────────────────────────────────
  private readonly snapshotService = inject(SnapshotService);
  private readonly clipboard = inject(Clipboard);
  private readonly snackBar = inject(MatSnackBar);
  private readonly document = inject(DOCUMENT);
  private readonly destroyRef = inject(DestroyRef);

  // ── Focus restoration (AC-A11Y-3, B2 conformance r1) ───────────
  // The row the user clicked had focus when the drawer opened. When
  // the drawer closes, focus must return to that row. We capture
  // `document.activeElement` SYNCHRONOUSLY in the constructor —
  // BEFORE `cdkTrapFocus` activates (the trap shifts focus into the
  // drawer to the `cdkFocusInitial`-marked close button). The
  // captured element survives in `previouslyFocusedElement` until
  // either the user closes the drawer (`onClose` emits, the host
  // tears down the component) or the component is otherwise
  // destroyed. We restore focus on `DestroyRef.onDestroy` so all
  // close paths (Esc, close-button click, host onCloseDrawer) recover.
  private previouslyFocusedElement: HTMLElement | null = null;

  // ── Detail state (drawer-owned) ─────────────────────────────
  readonly detail = signal<SnapshotDetailResponse | null>(null);
  readonly detailLoading = signal(false);
  readonly detailError = signal<string | null>(null);
  // Stale-response guard (deep-review 🟡#3) — mirrors digestRequestId
  // below. The detail fetch fires on every `snapshotId` input swap; a
  // slow response for snapshot A must NOT overwrite the drawer after
  // the user has navigated to snapshot B. Increment-and-capture, then
  // compare on resolve; a stale response is silently discarded.
  private detailRequestId = 0;

  // ── Digest state (drawer-owned; lazy) ───────────────────────
  readonly digest = signal<Record<string, unknown> | null>(null);
  readonly digestLoading = signal(false);
  readonly digestError = signal<string | null>(null);
  readonly showDigest = signal(false);
  private digestRequestId = 0;

  // ── Constructor effect: re-fetch on snapshotId change ───────
  constructor() {
    effect(
      (onCleanup) => {
        const id = this.snapshotId();
        if (!id) {
          this.resetDetailState();
          return;
        }
        // Reset every per-snapshot signal so the drawer shows the
        // loading state immediately when the id swaps.
        this.detailLoading.set(true);
        this.detailError.set(null);
        this.digest.set(null);
        this.digestError.set(null);
        this.showDigest.set(false);

        // Increment-and-capture the request id BEFORE subscribing so a
        // late-resolving response for an older snapshotId is discarded
        // (deep-review 🟡#3 — mirrors digestRequestId below).
        const detailReqId = ++this.detailRequestId;
        this.snapshotService
          .getById(id, { includeDigest: false })
          .subscribe({
            next: (resp) => {
              if (this.detailRequestId !== detailReqId) {
                return; // stale — a newer request has already started
              }
              this.detail.set(resp);
              this.detailLoading.set(false);
            },
            error: (err: { message?: string }) => {
              if (this.detailRequestId !== detailReqId) {
                return; // stale
              }
              this.detailError.set(
                this.toMessage(err?.message || 'Failed to load snapshot details'),
              );
              this.detailLoading.set(false);
            },
          });

        onCleanup(() => {
          // The HTTP observable completes on its own; nothing to do.
        });
      },
      // forward-compat nit (confiler): signal writes inside an effect
      // require `allowSignalWrites: true` in modern Angular. The
      // detail/digest signals reset above + onCleanup would otherwise
      // log an `NG0600` warning at runtime.
      { allowSignalWrites: true },
    );

    // ── B2 (AC-A11Y-3): capture the row that opened the drawer so
    //    we can restore focus when the drawer closes. We do this
    //    SYNCHRONOUSLY in the constructor (before `cdkTrapFocus`
    //    activates and shifts focus into the drawer to the
    //    `cdkFocusInitial`-marked close button). queueMicrotask
    //    would land AFTER cdkTrapFocus, by which time the row is no
    //    longer `document.activeElement`.
    const active = this.document.activeElement;
    if (active instanceof HTMLElement && active !== this.document.body) {
      this.previouslyFocusedElement = active;
    }

    // ── B2 (AC-A11Y-3): restore focus on drawer teardown. All close
    //    paths (Esc HostListener, close-button click, host
    //    `onCloseDrawer`) ultimately destroy this component, so a
    //    single `DestroyRef.onDestroy` covers them.
    this.destroyRef.onDestroy(() => {
      const el = this.previouslyFocusedElement;
      if (el && typeof el.focus === 'function') {
        // The element may have been removed from the DOM (e.g. a
        // table row that was filtered out). `isConnected` guards
        // against the DOMException for disconnected subtrees.
        if (el.isConnected) {
          el.focus();
        }
      }
    });
  }

  /**
   * B2 (AC-A11Y-3): Escape closes the drawer. `cdkTrapFocus` provides
   * the focus trap (Tab cycling inside the drawer); Esc is the only
   * standard way out of a trap. Only act when this component is
   * embedded in a drawer (not when it is rendered as a full-page
   * view, where the host handles its own Esc routing).
   */
  @HostListener('document:keydown.escape')
  onEscapeKey(): void {
    if (this.isDrawerMode()) {
      this.close.emit();
    }
  }

  // ── Computed helpers (template-facing) ──────────────────────

  /** JSON-stringified digest, with 2-space indent. */
  digestJsonString = computed<string>(() => {
    const d = this.digest();
    if (d === null || d === undefined) return '';
    try {
      return JSON.stringify(d, null, 2);
    } catch {
      return String(d);
    }
  });

  /** True when the pretty-printed digest exceeds the 200KB guard. */
  digestTooLarge = computed<boolean>(() => {
    return this.digestJsonString().length > DIGEST_GUARD_BYTES;
  });

  truncatedSnapshotId = computed<string>(() => this.truncateId(this.detail()?.id));

  truncatedTargetInstanceId = computed<string>(() =>
    this.truncateId(this.detail()?.target_instance_id),
  );

  /**
   * Stable id for the drawer title (used by `aria-labelledby`).
   * Material's drawer also wraps the panel, but our header is the
   * accessible label target for AC-A11Y-1.
   */
  readonly drawerTitleId = computed<string>(() => 'drawer-title-snapshot');

  /**
   * Tooltip text for the 'Last warmed' '—' placeholder (v2 AC-4.7).
   * The BE does not yet surface per-snapshot warm timestamps (D-5);
   * when the host's metrics carry a non-null count for the open
   * snapshot, the value is shown directly.
   */
  readonly warmTooltip =
    'Warmed-spawn timestamps are not yet surfaced in v2 — the count ' +
    'shown elsewhere comes from the global metrics rollup.';

  /** Material icon for a status chip (AC-6.1; mirrors the table). */
  statusIcon(s: SnapshotStatus): string {
    switch (s) {
      case 'active':
        return 'check_circle';
      case 'running':
        return 'autorenew';
      case 'superseded':
        return 'history';
      case 'failed':
        return 'error';
      case 'interrupted':
        return 'warning';
    }
  }

  // ── Template handlers ───────────────────────────────────────

  onCopyId(): void {
    const id = this.detail()?.id;
    if (!id) return;
    this.clipboard.copy(id);
    this.copyId.emit(id);
  }

  onSupersedesClick(): void {
    const id = this.detail()?.supersedes_snapshot_id;
    if (id) this.navigateToPredecessor.emit(id);
  }

  onClose(): void {
    this.close.emit();
  }

  /**
   * Lazy digest fetch — only fires on the first "Show digest" click
   * for the current snapshotId. Subsequent shows are cache hits
   * against the drawer-owned `digest` signal.
   */
  onToggleDigest(): void {
    if (this.showDigest()) {
      // collapse
      this.showDigest.set(false);
      return;
    }
    this.showDigest.set(true);
    if (this.digest() !== null) {
      return; // cache hit
    }
    const id = this.snapshotId();
    const reqId = ++this.digestRequestId;
    this.digestLoading.set(true);
    this.digestError.set(null);
    this.snapshotService.getById(id, { includeDigest: true }).subscribe({
      next: (resp) => {
        if (this.digestRequestId !== reqId) {
          return; // stale
        }
        // The detail response carries the full `digest` field when
        // include=digest was sent (per OK-4 / amendment §4).
        this.digest.set(resp.digest ?? {});
        this.digestLoading.set(false);
      },
      error: (err: { message?: string }) => {
        if (this.digestRequestId !== reqId) {
          return;
        }
        this.digestError.set(
          this.toMessage(err?.message || 'Failed to load digest'),
        );
        this.digestLoading.set(false);
      },
    });
  }

  onRetryDetail(): void {
    // Re-trigger the constructor effect by swapping the input.
    // We simulate the swap by re-firing the request directly.
    const id = this.snapshotId();
    if (!id) return;
    this.detailLoading.set(true);
    this.detailError.set(null);
    // Same staleness guard as the constructor effect (deep-review 🟡#3):
    // a rapid retry on snapshot A followed by a navigation to B must
    // not let A's late response overwrite B.
    const detailReqId = ++this.detailRequestId;
    this.snapshotService.getById(id, { includeDigest: false }).subscribe({
      next: (resp) => {
        if (this.detailRequestId !== detailReqId) {
          return; // stale
        }
        this.detail.set(resp);
        this.detailLoading.set(false);
      },
      error: (err: { message?: string }) => {
        if (this.detailRequestId !== detailReqId) {
          return; // stale
        }
        this.detailError.set(
          this.toMessage(err?.message || 'Failed to load snapshot details'),
        );
        this.detailLoading.set(false);
      },
    });
  }

  onRetryDigest(): void {
    this.digest.set(null); // bust the cache so onToggleDigest re-fetches
    this.digestError.set(null);
    this.onToggleDigest();
  }

  /** Copy works EVEN when the 200KB guard triggers (render-only guard). */
  onCopyDigest(): void {
    const json = this.digestJsonString();
    if (!json) return;
    this.clipboard.copy(json);
    this.snackBar.open('Digest copied', 'Close', {
      duration: 2000,
      panelClass: 'success-snackbar',
    });
  }

  // ── Private helpers ─────────────────────────────────────────

  private resetDetailState(): void {
    this.detail.set(null);
    this.detailError.set(null);
    this.detailLoading.set(false);
    this.digest.set(null);
    this.digestError.set(null);
    this.digestLoading.set(false);
    this.showDigest.set(false);
  }

  private toMessage(raw: string): string {
    return `Failed to load snapshot details: ${raw}`;
  }

  private truncateId(id: string | null | undefined): string {
    if (!id) return '—';
    if (id.length <= 12) return id;
    return `${id.slice(0, 4)}…${id.slice(-4)}`;
  }

  // Exposed for the template's date formatting — local to keep the
  // drawer self-contained (no shared util import).
  formatCreatedAt(iso: string | null | undefined): string {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
  }
}
