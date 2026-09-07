import { Component, input, output, computed, signal, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { getStatusColor as modelGetStatusColor, Job, JobStatus, MissionSummary, MissionLiveness, missionLivenessChip, buildQueueTree, shouldAutoExpand, missionDisplayTitle } from '../../models/job.model';
import { MissionLivenessChipComponent } from '../mission-liveness-chip/mission-liveness-chip.component';

/**
 * Presentational panel that surfaces the current job queue state as
 * a MISSION TREE (2026-09-07, ``feature/job-queue-mission-tree``):
 *
 *   * LIVE MISSIONS section — mission nodes (liveness in
 *     processing/pending/paused) with their attached jobs as
 *     expandable children. Collapsed by default; auto-expands when
 *     exactly one live mission exists.
 *   * QUEUED section — non-terminal unattached jobs (mission_id
 *     null OR mission not in the missions list). Always falls back
 *     so a job never silently vanishes.
 *   * RECENT section — terminal mission nodes + terminal jobs that
 *     map to no listed mission (recentFlat).
 *
 * This is a DUMB component — all data is pushed in via inputs and
 * click events flow out via the ``jobClick`` output. The parent
 * (job-queue-indicator) is responsible for fetching jobs + missions,
 * resolving any project/instance name maps, and re-fetching on
 * every 8s tick.
 *
 * Styling mirrors the notification-bell dropdown (dark slate panel,
 * 560px max width per the mission-tree brief, monospace badges,
 * status-coloured left border).
 */
@Component({
  selector: 'app-job-queue-panel',
  standalone: true,
  imports: [
    CommonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    MissionLivenessChipComponent,
  ],
  templateUrl: './job-queue-panel.component.html',
  styleUrl: './job-queue-panel.component.scss',
})
export class JobQueuePanelComponent {
  /**
   * All non-terminal jobs (running + pending + paused) — the panel's
   * primary input. Replaces the prior ``runningJobs`` input which
   * filtered out pending/queued statuses and silently starved the
   * panel's QUEUED section (C1 fix). The downstream ``buildQueueTree``
   * helper splits this into attached (live mission node) vs unattached
   * (``tree().queued``) — the NEVER-hide invariant keeps every job
   * visible exactly once.
   */
  activeJobs = input<Job[]>([]);

  /** Recently completed/failed/cancelled jobs (already trimmed by parent). */
  recentJobs = input<Job[]>([]);

  /** project_id → display name. Keys may be null for unassigned jobs. */
  projectNameMap = input<Map<string | null, string>>(new Map());

  /**
   * Fix C (§8.2) — last-known live-mission count from the parent
   * (badge). Widened to ``number | null`` per the C3 fix: ``null``
   * means the badge has never received a good count (pre-data
   * state); once a good number arrives the parent retains it across
   * degraded/null ticks, so the panel's empty-state subtitle never
   * flips to "Queue is currently idle" during an outage. ``0`` is a
   * legitimate healthy tick with no live missions.
   */
  liveMissionCount = input<number | null>(null);

  /**
   * Mission-tree panel (2026-09-07) — the full missions list from
   * the latest successful poll. The panel feeds it through the
   * pure ``buildQueueTree`` helper to derive the live + terminal
   * mission nodes plus the unattached-fallback buckets.
   *
   * Default ``[]`` so the panel degrades to its legacy flat layout
   * (running + recent) when no missions have arrived yet (or the
   * missions leg is degraded — the parent retains the last good
   * payload so the tree never flashes empty).
   */
  missions = input<MissionSummary[]>([]);

  /** Emitted when the user clicks any job row. */
  jobClick = output<Job>();

  /**
   * Per-mission-id expansion state for LIVE MISSIONS. Survives data
   * refreshes by being keyed on mission_id (not array index) — a
   * poll refresh that re-orders the live missions does NOT collapse
   * the user's expanded state. ``Set<string>`` keeps it cheap.
   */
  private readonly expandedLiveMissions = signal<Set<string>>(new Set());

  /**
   * Per-mission-id expansion state for RECENT mission nodes. Same
   * survival guarantee as ``expandedLiveMissions``.
   */
  private readonly expandedRecentMissions = signal<Set<string>>(new Set());

  /**
   * Tree derivation — wraps the pure ``buildQueueTree`` model
   * helper with the panel's input signals so the template binds to
   * the structured tree rather than two flat lists.
   *
   * Passes ALL non-terminal jobs (running + pending + paused) as the
   * "active" input. ``buildQueueTree`` splits them into attached
   * (live mission node) vs unattached (``queued`` bucket); the
   * NEVER-hide contract keeps every job visible exactly once.
   */
  readonly tree = computed(() => {
    return buildQueueTree(this.activeJobs(), this.recentJobs(), this.missions());
  });

  /**
   * Capped list of terminal jobs (defence-in-depth slice). Replaces
   * the legacy ``recentCapped`` — kept for backwards compatibility
   * with the existing spec mirror; the tree handles the actual cap.
   */
  readonly recentCapped = computed(() => this.recentJobs().slice(0, 10));

  /**
   * Total Recent rows shown to the user (mission node headers + their
   * child jobs + flat rows) — mirrors ``buildQueueTree``'s internal
   * cap so the header stats strip ties to the visible content.
   */
  readonly recentRowCount = computed(() => {
    const t = this.tree();
    return (
      t.recent.reduce((sum, n) => sum + 1 + n.jobs.length, 0) +
      t.recentFlat.length
    );
  });

  /**
   * True when the panel has nothing to show — live missions empty,
   * queued empty, recent empty, AND the parent's liveMissionCount
   * (legacy receipt-derived count) is zero. This is the "everything
   * is idle" branch — the empty-state renders "Queue is currently
   * idle".
   *
   * C3 fix: ``liveMissionCount() === 0`` is a healthy tick with no
   * live missions; ``liveMissionCount() === null`` means the badge
   * has never received a good value (pre-data state). Both read as
   * "no live missions" for the empty-state gate. A degraded tick
   * that retains a positive last-known count does NOT trip the
   * empty state — the subtitle reads "Queue is idle · N live
   * missions still working" instead.
   */
  readonly isEmpty = computed(() => {
    const t = this.tree();
    const liveCount = this.liveMissionCount();
    return (
      t.liveMissions.length === 0 &&
      t.queued.length === 0 &&
      t.recent.length === 0 &&
      t.recentFlat.length === 0 &&
      (liveCount === null || liveCount === 0)
    );
  });

  /** Convenience active count used in the header (matches the legacy surface). */
  readonly activeCount = computed(() => this.activeJobs().length);

  /**
   * Auto-expand the LIVE MISSIONS section when exactly one live
   * mission exists. Pure derivation from the tree; the auto-seed
   * effect (in the constructor) syncs this into the expansion set.
   */
  readonly shouldAutoExpandLive = computed(() =>
    shouldAutoExpand(this.tree().liveMissions)
  );

  constructor() {
    // Auto-seed the LIVE MISSIONS expansion set when the set of live
    // mission ids changes AND the auto-expand rule says we should.
    // Tracked by a join-key so unrelated input changes don't fight
    // the user's manual toggles.
    let seededForKey = '';
    let lastSeenSet: Set<string> = new Set();
    const liveIds = computed(() =>
      this.tree()
        .liveMissions.map((n) => n.mission.mission_id ?? '')
        .filter((id) => id.length > 0)
    );
    const liveKey = computed(() => liveIds().slice().sort().join('|'));

    // Sync effect: runs whenever the live-mission SET changes (not
    // whenever the auto-expand decision flips on/off). When a new
    // single-mission state is observed AND the rule says yes, seed
    // the set to {that-mission-id}; otherwise leave user state alone.
    effect(() => {
      const key = liveKey();
      if (key === seededForKey) return;
      seededForKey = key;
      const ids = liveIds();
      const current = lastSeenSet;
      // Detect a TRUE set change (size or membership differs from
      // last seen). If just the auto-expand boolean flipped without
      // a set change, don't clobber user toggles.
      const sameSet =
        current.size === ids.length && ids.every((id) => current.has(id));
      if (sameSet) return;
      lastSeenSet = new Set(ids);
      if (shouldAutoExpand(this.tree().liveMissions) && ids.length === 1) {
        this.expandedLiveMissions.set(new Set(ids));
      }
    });
  }

  /** Toggle a live mission's expansion state. */
  toggleLiveMission(missionId: string): void {
    this.expandedLiveMissions.update((s) => {
      const next = new Set(s);
      if (next.has(missionId)) next.delete(missionId);
      else next.add(missionId);
      return next;
    });
  }

  /** Toggle a recent mission's expansion state. */
  toggleRecentMission(missionId: string): void {
    this.expandedRecentMissions.update((s) => {
      const next = new Set(s);
      if (next.has(missionId)) next.delete(missionId);
      else next.add(missionId);
      return next;
    });
  }

  /** True iff a live mission node is expanded. */
  isLiveExpanded(missionId: string | null | undefined): boolean {
    if (!missionId) return false;
    return this.expandedLiveMissions().has(missionId);
  }

  /** True iff a recent mission node is expanded. */
  isRecentExpanded(missionId: string | null | undefined): boolean {
    if (!missionId) return false;
    return this.expandedRecentMissions().has(missionId);
  }

  /**
   * Fix C (§8.2) — mission-liveness chip for a row, or null when the
   * row renders nothing extra (mission rows, Task-backed records,
   * degraded lookups, no linked instance — all null by design).
   * Terminal receipt + live mission is exactly the pair that must
   * NOT read as bare "completed".
   */
  missionChip(job: Job) {
    return missionLivenessChip(job);
  }

  /**
   * Resolves the best available title for a job. Priority chain:
   * 1. job_metadata.instance_name (if truthy)
   * 2. agent_id (if truthy)
   * 3. shortenId of instance_id (or job_id) as a last resort
   */
  resolveTitle(job: Job): string {
    const meta = job.job_metadata;
    if (meta && typeof meta === 'object' && meta['instance_name']) {
      return String(meta['instance_name']);
    }

    if (job.agent_id) {
      return job.agent_id;
    }

    return this.shortenId(job.instance_id ?? job.job_id);
  }

  /** Project display label — falls back to a shortened id when unknown. */
  projectLabel(job: Job): string {
    const id = job.project_id;
    if (id === null || id === undefined) return '—';
    return this.projectNameMap().get(id) ?? this.shortenId(id);
  }

  /**
   * Truncate an id to its first 8 characters followed by an ellipsis.
   * Matches the convention used by ``job-queue-indicator`` and
   * ``notification-bell`` so the same id looks consistent across the
   * header. Returns an em-dash for null/empty input.
   */
  shortenId(id: string | null | undefined): string {
    if (!id) return '—';
    return id.length > 8 ? id.substring(0, 8) + '...' : id;
  }

  /**
   * Human-readable "X ago" formatter for completed/created timestamps.
   * Mirrors the logic in ``notification-bell.component.ts`` and
   * ``skill-card.component.ts`` — no shared util exists, so this
   * keeps the duplication intentional and local.
   */
  timeAgo(dateString: string | null | undefined): string {
    if (!dateString) return '';
    const now = new Date();
    const date = new Date(dateString);
    const diffMs = now.getTime() - date.getTime();
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHour = Math.floor(diffMin / 60);
    const diffDay = Math.floor(diffHour / 24);

    if (diffSec < 60) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHour < 24) return `${diffHour}h ago`;
    if (diffDay < 7) return `${diffDay}d ago`;
    return date.toLocaleDateString();
  }

  /** Material icon name for a terminal job status. */
  getStatusIcon(status: JobStatus): string {
    switch (status) {
      case 'completed':
        return 'check_circle';
      case 'failed':
        return 'error';
      case 'cancelled':
        return 'cancel';
      case 'dead_letter':
        return 'inventory_2';
      default:
        return 'info';
    }
  }

  /** Delegate to the shared util so the template binding stays the same. */
  readonly getStatusColor = modelGetStatusColor;

  /** Emits the clicked job up to the parent for navigation. */
  onRowClick(job: Job): void {
    this.jobClick.emit(job);
  }

  /**
   * Mission title — delegates to the model helper with this
   * component's own ``timeAgo`` so the rendered "X ago" wording is
   * identical across the panel surface (no half-built strings from
   * two different formatters).
   */
  missionTitle(m: MissionSummary): string {
    return missionDisplayTitle(m, (d) => this.timeAgo(d));
  }

  /**
   * Mission meta line for collapsed node — ``agent_id · N jobs ·
   * timeAgo(last_activity_at)``. Single-line, 2-line clamped in CSS.
   */
  missionMeta(m: MissionSummary, jobCount: number): string {
    const agent = m.agent_id ?? '—';
    const ago = this.timeAgo(m.last_activity_at) || 'idle';
    return `${agent} · ${jobCount} job${jobCount === 1 ? '' : 's'} · ${ago}`;
  }

  /**
   * Mission node click — toggles expansion. ``stopPropagation`` is
   * NOT needed here because the mission chip's own (click) handler
   * also calls ``$event.stopPropagation()`` in the template, and the
   * row click only fires once per event target.
   */
  onLiveMissionClick(missionId: string | null | undefined): void {
    if (!missionId) return;
    this.toggleLiveMission(missionId);
  }

  onRecentMissionClick(missionId: string | null | undefined): void {
    if (!missionId) return;
    this.toggleRecentMission(missionId);
  }

  /** Convenience: chevron glyph based on expansion state. */
  liveChevron(missionId: string | null | undefined): string {
    return this.isLiveExpanded(missionId) ? 'expand_more' : 'chevron_right';
  }

  recentChevron(missionId: string | null | undefined): string {
    return this.isRecentExpanded(missionId) ? 'expand_more' : 'chevron_right';
  }

  /**
   * Mission liveness → material icon for the header badge.
   * Keeps the brief's "reuse mission-liveness-chip where its input
   * shape fits" rule — for the tree nodes we have ``MissionSummary``
   * directly (not a Job), so we build a tiny ``MissionLivenessChip``
   * inline rather than force-fitting.
   */
  livenessIcon(liveness: MissionLiveness | null): string {
    switch (liveness) {
      case 'processing':
        return 'sync';
      case 'pending':
        return 'schedule';
      case 'paused':
        return 'pause_circle';
      case 'completed':
        return 'check_circle';
      case 'failed':
        return 'error';
      case 'cancelled':
        return 'cancel';
      default:
        return 'help_outline';
    }
  }

  /** Human-readable liveness label for the mission node badge. */
  livenessLabel(liveness: MissionLiveness | null): string {
    return liveness ?? 'unknown';
  }

  /**
   * Color for the mission node badge — delegates to the canonical
   * ``getMissionLivenessColor`` so the colour palette matches the
   * existing mission-liveness-chip component (no new colour table).
   */
  livenessColor(liveness: MissionLiveness | null): string {
    // getMissionLivenessColor covers the canonical space; fall back
    // to a neutral grey for the degraded null branch.
    switch (liveness) {
      case 'pending':
        return '#9CA3AF';
      case 'processing':
        return '#3B82F6';
      case 'paused':
        return '#F59E0B';
      case 'completed':
        return '#22C55E';
      case 'failed':
        return '#EF4444';
      case 'cancelled':
        return '#F59E0B';
      default:
        return '#9CA3AF';
    }
  }
}
