import { Component, input, output, computed, signal, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import {
  getStatusColor as modelGetStatusColor,
  Job,
  JobStatus,
  missionLivenessChip,
  RECEIPT_LONG_GLYPH,
} from '../../models/job.model';
import {
  InstanceNode,
  InstanceRow,
  InstanceTreeItem,
  buildInstanceTree,
  shouldAutoExpandInstanceTree,
  getInstanceStatusColor,
  instanceDisplayTitle,
  instanceMetaLine,
  instanceSubtreeJobs,
  visibleInstanceTreeItems,
  nextInstanceTreeItem,
  instanceTreeItemId,
  InstanceNodeStatus,
} from '../../models/instance-node.model';
import { MissionLivenessChipComponent } from '../mission-liveness-chip/mission-liveness-chip.component';

/**
 * Presentational panel that surfaces the job queue as an
 * INSTANCES-PRIMARY tree (2026-09-08, user-locked design V1,
 * ``feature/job-queue-instance-tree``): users think in INSTANCES
 * (conversations), not missions.
 *
 *   ROOT instances on top → child instances beneath → job receipts
 *   at leaves.
 *
 *   * LIVE CONVERSATIONS section — roots that are live themselves OR
 *     have ANY live descendant (first 2 auto-expand; further live
 *     roots collapsed). Child instance rows indent beneath; receipt
 *     jobs indent under their node.
 *   * QUEUED section — non-terminal jobs whose grouping key
 *     (``mission_id ?? instance_id``) matched NO instance node.
 *     Always falls back so a job never silently vanishes (only
 *     rendered when non-empty).
 *   * RECENT section — terminal roots (newest first, capped at 10
 *     TOTAL rows across node headers + their jobs + flat rows; jobs
 *     that don't fit overflow to ``recentFlat``) + orphan terminal
 *     jobs as flat rows.
 *
 * This is a DUMB component — all data is pushed in via inputs and
 * click events flow out via outputs. The parent
 * (job-queue-indicator) owns ALL fetching (jobs + the instances
 * page) and the navigation handlers:
 *
 *   * ``jobClick``       — a job receipt row was activated (job nav).
 *   * ``instanceClick``  — an instance node row was activated — ROW
 *     CLICK = NAVIGATE (``/projects/<key>/instances/<id>``). The
 *     node's chevron is the ONLY expand toggle: its own click
 *     handler stops propagation so a chevron tap never navigates.
 *   * ``footerClick``    — the "Open full queue →" footer activation.
 *
 * Tree derivation is the pure ``buildInstanceTree`` model helper
 * (models/instance-node.model.ts); keyboard navigation flattens the
 * two trees via ``visibleInstanceTreeItems`` and moves REAL DOM focus
 * (``document.getElementById``) — the same machinery contract as the
 * legacy mission tree. Expansion state is a single ``Set<string>``
 * keyed by ``instance_id`` so it survives poll refreshes.
 *
 * Styling mirrors the notification-bell dropdown (dark slate panel,
 * 560px max width, monospace badges, status-coloured left border).
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
   * Nested instance roots from the indicator (``buildInstanceNodes``
   * over the root-paginated instances page). The panel feeds them
   * through the pure ``buildInstanceTree`` helper together with the
   * job lists to derive the live/queued/recent buckets.
   */
  instances = input<InstanceNode[]>([]);

  /**
   * All non-terminal jobs (running + pending + paused). Jobs whose
   * grouping key (``mission_id ?? instance_id`` — the BE list wire
   * ships ``mission_id: null`` for child-bound rows, the raw
   * ``instance_id`` column is always populated) resolves to an
   * instance node (at ANY depth) attach to that node; the rest reach
   * ``tree().queued`` — the NEVER-hide contract keeps every job
   * visible exactly once.
   */
  activeJobs = input<Job[]>([]);

  /** Terminal jobs (already trimmed by the parent). Same attach-or-flat routing. */
  recentJobs = input<Job[]>([]);

  /** project_id → display name. Keys may be null for unassigned jobs. */
  projectNameMap = input<Map<string | null, string>>(new Map());

  /**
   * Last-known live-mission count from the parent (badge). Widened to
   * ``number | null``: ``null`` = never received a good count
   * (pre-data state); the parent retains the last good value across
   * degraded ticks, so the panel's empty-state subtitle never flips
   * to "Queue is currently idle" during an outage. ``0`` is a
   * legitimate healthy tick with no live missions.
   */
  liveMissionCount = input<number | null>(null);

  /** Emitted when the user clicks any job row. */
  jobClick = output<Job>();

  /**
   * Emitted when the user activates an INSTANCE node row (root OR
   * child) — ROW CLICK = NAVIGATE. The indicator closes the menu and
   * routes to ``/projects/<key>/instances/<id>``. The node's chevron
   * never emits this (its own handler stops propagation — the
   * chevron is the ONLY expand toggle).
   */
  instanceClick = output<InstanceNode>();

  /** Emitted when the user activates the footer's "Open full queue →". */
  footerClick = output<void>();

  /**
   * Per-instance expansion state, keyed by ``instance_id`` (ids are
   * globally unique so ONE set serves both the LIVE and RECENT
   * trees). Survives data refreshes — a poll that re-orders the
   * roots does NOT collapse the user's expanded nodes.
   */
  private readonly expandedInstances = signal<Set<string>>(new Set());

  /**
   * G1 (2026-09-08 review fold) — ids the user has MANUALLY toggled
   * (expanded OR collapsed via chevron / ArrowRight / ArrowLeft /
   * row click — i.e. anything but the auto-seed). Auto-seed only
   * seeds UNTOUCHED first-2-live ids; once an id is touched, the
   * auto-seed effect NEVER un-toggles or re-expands it, regardless
   * of how the live-root set drifts across polls. The user's choice
   * wins.
   */
  private readonly userTouchedInstances = signal<Set<string>>(new Set());

  /**
   * Id of the row that currently owns keyboard focus inside the
   * trees (LIVE + RECENT). Drives the ``.focused`` CSS class so
   * sighted users can see where the arrow keys would land;
   * Enter/Space still fire on the focused row's own handler.
   *
   * ``null`` = no row owns focus yet. The id is the stable
   * ``instanceTreeItemId`` string — see models/instance-node.model.ts.
   */
  private readonly focusedItemId = signal<string | null>(null);

  /**
   * Tree derivation — wraps the pure ``buildInstanceTree`` model
   * helper with the panel's input signals so the template binds to
   * the structured tree rather than flat lists.
   */
  readonly tree = computed(() =>
    buildInstanceTree(this.instances(), this.activeJobs(), this.recentJobs())
  );

  /**
   * Flattened list of items the keyboard handler can land on (LIVE +
   * RECENT trees), in display order. Collapsed nodes' descendants are
   * omitted entirely so ↑/↓ skip them. The TEMPLATE renders from this
   * SAME list (per-section filtered views below), so keyboard order
   * and visual order cannot drift.
   */
  readonly visibleItems = computed<InstanceTreeItem[]>(() =>
    visibleInstanceTreeItems(
      this.tree().liveRoots,
      this.tree().recentRoots,
      this.expandedInstances()
    )
  );

  /** LIVE CONVERSATIONS section items (display order). */
  readonly liveItems = computed<InstanceTreeItem[]>(() =>
    this.visibleItems().filter((it) => it.tree === 'live')
  );

  /** RECENT section items (display order, flat rows excluded). */
  readonly recentItems = computed<InstanceTreeItem[]>(() =>
    this.visibleItems().filter((it) => it.tree === 'recent')
  );

  /**
   * Total Recent rows shown to the user — instance headers (at ALL
   * depths when the user has expanded the root) + their subtree jobs +
   * flat rows. Mirrors ``buildInstanceTree``'s internal structured-
   * band + never-hide contract: the in-band row count is bounded by
   * MAX_RECENT_INSTANCE_ROWS, but the panel renders overflow jobs
   * unconditionally (NEVER-hide) — total rendered rows may therefore
   * EXCEED MAX by design.
   */
  readonly recentRowCount = computed(() => {
    const t = this.tree();
    return (
      t.recentRoots.reduce((sum, n) => sum + 1 + instanceSubtreeJobs(n).length, 0) +
      t.recentFlat.length
    );
  });

  /** Convenience active count used in the header (matches the legacy surface). */
  readonly activeCount = computed(() => this.activeJobs().length);

  /**
   * True when the panel has nothing to show — no live roots, no
   * queued jobs, no recent roots/flat rows, AND the parent's
   * liveMissionCount is zero/null. The empty-state branch renders
   * "Queue is currently idle".
   */
  readonly isEmpty = computed(() => {
    const t = this.tree();
    const liveCount = this.liveMissionCount();
    return (
      t.liveRoots.length === 0 &&
      t.queued.length === 0 &&
      t.recentRoots.length === 0 &&
      t.recentFlat.length === 0 &&
      (liveCount === null || liveCount === 0)
    );
  });

  constructor() {
    // Auto-seed the expansion set when the set of LIVE root ids
    // changes: the FIRST 2 live roots auto-expand (user-locked: exactly
    // 2); further live roots and ALL terminal roots stay collapsed.
    // Tracked by a sorted-join-key so unrelated input changes don't
    // fight the user's manual toggles.
    //
    // G1 (2026-09-08 review fold): only seed ids the user has NOT
    // manually touched (see userTouchedInstances). NEVER un-toggle or
    // re-expand a user-decided id; the auto-seed MERGES onto the
    // existing expansion set instead of replacing it. The user's
    // choice survives any subsequent live-set drift.
    let seededForKey = '';
    let lastSeenSet: Set<string> = new Set();
    const liveIds = computed(() =>
      this.tree()
        .liveRoots.map((n) => n.instance.instance_id)
        .filter((id) => id.length > 0)
    );
    const liveKey = computed(() => liveIds().slice().sort().join('|'));

    effect(() => {
      const key = liveKey();
      if (key === seededForKey) return;
      seededForKey = key;
      const ids = liveIds();
      const current = lastSeenSet;
      // Detect a TRUE set change (size or membership differs from
      // last seen). If just the job lists changed underneath the same
      // roots, don't clobber user toggles.
      const sameSet =
        current.size === ids.length && ids.every((id) => current.has(id));
      if (sameSet) return;
      lastSeenSet = new Set(ids);
      // G1: filter out ids the user has already touched — auto-seed
      // only acts on UNTOUCHED first-2-live.
      const touched = this.userTouchedInstances();
      const seedIds = shouldAutoExpandInstanceTree(this.tree().liveRoots).filter(
        (id) => !touched.has(id)
      );
      if (seedIds.length === 0) return;
      // G1: MERGE onto the existing expansion set — never overwrite.
      const currentExpanded = this.expandedInstances();
      let changed = false;
      const next = new Set(currentExpanded);
      for (const id of seedIds) {
        if (!next.has(id)) {
          next.add(id);
          changed = true;
        }
      }
      if (changed) this.expandedInstances.set(next);
    });
  }

  /**
   * Toggle an instance node's expansion state (chevron / arrow nav).
   * Marks the id as user-touched so the auto-seed effect will NEVER
   * re-expand or un-collapse it across subsequent live-set drifts.
   */
  toggleInstance(instanceId: string): void {
    this.userTouchedInstances.update((s) => {
      if (s.has(instanceId)) return s;
      const next = new Set(s);
      next.add(instanceId);
      return next;
    });
    this.expandedInstances.update((s) => {
      const next = new Set(s);
      if (next.has(instanceId)) next.delete(instanceId);
      else next.add(instanceId);
      return next;
    });
  }

  /** True iff an instance node is expanded. */
  isExpanded(instanceId: string | null | undefined): boolean {
    if (!instanceId) return false;
    return this.expandedInstances().has(instanceId);
  }

  /** Chevron glyph for an instance node based on expansion state. */
  chevron(instanceId: string | null | undefined): string {
    return this.isExpanded(instanceId) ? 'expand_more' : 'chevron_right';
  }

  /** Stable DOM id for a flattened tree item (track/focus/class binding). */
  itemId(item: InstanceTreeItem): string {
    return instanceTreeItemId(item);
  }

  /** Depth indentation in px — 16px per level on top of the row padding. */
  indentPx(depth: number): number {
    return 8 + depth * 16;
  }

  /**
   * CHEVRON click — the ONLY expand toggle. Stops propagation so the
   * row's own click (navigate) never fires: chevron click does NOT
   * navigate. The template passes ``$event`` explicitly. The toggle
   * marks the id as user-touched (G1), so the auto-seed effect won't
   * re-expand or un-collapse it across subsequent live-set drifts.
   * Native ``<button type="button">`` activation also fires click on
   * Enter/Space — no separate ``(keydown.enter)`` handler is needed.
   */
  onChevronClick(event: Event, instanceId: string): void {
    event.stopPropagation();
    this.toggleInstance(instanceId);
  }

  /** Instance row activation — ROW CLICK = NAVIGATE (emit to parent). */
  onInstanceRowClick(node: InstanceNode): void {
    this.instanceClick.emit(node);
  }

  /** Job row activation — emit to the parent for navigation. */
  onRowClick(job: Job): void {
    this.jobClick.emit(job);
  }

  /** Footer activation — emit to the parent (panel stays DUMB). */
  onFooterClick(): void {
    this.footerClick.emit();
  }

  /** True iff the given item id is the row that owns keyboard focus. */
  isFocusedItem(id: string | null | undefined): boolean {
    if (!id) return false;
    return this.focusedItemId() === id;
  }

  /**
   * Handler bound from the template to each row's ``(focus)``. The
   * row's ``(focus)`` event is the SINGLE writer of ``focusedItemId``:
   * arrow keys move real DOM focus via ``document.getElementById(id)?.focus()``
   * and the resulting focus event mirrors it back into the signal —
   * single source of truth so Enter / Space activate the row the
   * user is looking at.
   */
  onRowFocus(id: string): void {
    this.focusedItemId.set(id);
  }

  /**
   * Arrow-key handler bound to the panel-list container. REAL DOM
   * focus moves (``document.getElementById``); the focused row's own
   * (focus) binding mirrors it back into ``focusedItemId``.
   *
   *   * ArrowDown / ArrowUp  → move focus by ±1 in the flat visible
   *     list. Boundary behaviour is CLAMP (not wrap — wrap in a
   *     two-tree layout reads as a glitch).
   *   * ArrowRight           → expand a collapsed instance node. On a
   *     job child or an already-expanded node: no-op.
   *   * ArrowLeft            → collapse an expanded instance node. On
   *     a child row (job or child instance), collapse the nearest
   *     ancestor (WAI-ARIA tree pattern) AND move real focus to that
   *     ancestor so the next press behaves naturally.
   *
   * Enter / Space / Esc are NOT handled here — those stay on the
   * individual rows: Enter/Space = ACTIVATE (on instance rows that is
   * NAVIGATE via ``instanceClick``; on job rows ``jobClick``), Esc
   * already closes the mat-menu from the trigger's own binding.
   */
  onTreeKeydown(event: KeyboardEvent): void {
    const items = this.visibleItems();
    if (items.length === 0) return;
    const key = event.key;
    if (
      key !== 'ArrowDown' &&
      key !== 'ArrowUp' &&
      key !== 'ArrowRight' &&
      key !== 'ArrowLeft'
    ) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();

    const currentId = this.focusedItemId();
    const currentIndex = currentId
      ? items.findIndex((it) => instanceTreeItemId(it) === currentId)
      : -1;

    if (key === 'ArrowDown' || key === 'ArrowUp') {
      const delta: -1 | 1 = key === 'ArrowUp' ? -1 : 1;
      const nextIndex = nextInstanceTreeItem(items, currentIndex, delta);
      if (nextIndex < 0) return;
      const nextId = instanceTreeItemId(items[nextIndex]);
      // Move REAL DOM focus so Enter / Space activate the right row;
      // the row's (focus) handler mirrors the id back (idempotent).
      this.focusedItemId.set(nextId);
      document.getElementById(nextId)?.focus();
      return;
    }

    // ArrowRight / ArrowLeft target the focused row (no focus yet →
    // no-op so we don't surprise the user with an unintended toggle).
    if (currentIndex < 0) return;
    const current = items[currentIndex];

    if (key === 'ArrowRight') {
      if (current.kind !== 'instance') return; // job children: no-op
      const id = current.node.instance.instance_id;
      if (!this.isExpanded(id)) this.toggleInstance(id);
      return;
    }

    // ArrowLeft: collapse if expanded; on a child row (job OR child
    // instance), collapse the nearest ancestor instance (WAI-ARIA tree
    // pattern) and move real focus to it — the child is removed from
    // the DOM by the collapse, so focus must land on a row that still
    // exists.
    if (key === 'ArrowLeft') {
      const selfId =
        current.kind === 'instance' ? current.node.instance.instance_id : null;
      const targetId =
        current.kind === 'instance'
          ? (current.parentInstanceId ?? selfId ?? '')
          : (current.parentInstanceId ?? '');
      if (!targetId) return;
      if (!this.isExpanded(targetId)) return;
      this.toggleInstance(targetId);
      // Refocus when the collapse removed the CURRENT row's slot
      // (child rows) — the row the user was on no longer exists.
      if (selfId === null || targetId !== selfId) {
        const ancestor = this.findNodeById(targetId);
        if (!ancestor) return; // defensive: tree changed under us
        const parentId = instanceTreeItemId({
          kind: 'instance',
          tree: current.tree,
          depth: 0,
          parentInstanceId: null,
          node: ancestor,
        });
        this.focusedItemId.set(parentId);
        document.getElementById(parentId)?.focus();
      }
    }
  }

  /** Find a node by id in the current tree (defensive for refocus). */
  private findNodeById(id: string): InstanceNode | undefined {
    const walk = (nodes: readonly InstanceNode[]): InstanceNode | undefined => {
      for (const n of nodes) {
        if (n.instance.instance_id === id) return n;
        const found = walk(n.children);
        if (found) return found;
      }
      return undefined;
    };
    return walk(this.tree().liveRoots) ?? walk(this.tree().recentRoots);
  }

  // ── Display helpers ─────────────────────────────────────────────────

  /**
   * Fix C (§8.2) — mission-liveness chip for a receipt row, or null
   * when the row renders nothing extra. UNCHANGED by the
   * instances-primary redesign: chips render from ``job.mission_ref``
   * as today.
   */
  missionChip(job: Job) {
    return missionLivenessChip(job);
  }

  /**
   * Resolves the best available title for a JOB row. Priority chain:
   * 1. job_metadata.instance_name  2. agent_id  3. shortened id.
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

  /**
   * Instance node display title — delegates to the model helper's
   * fallback chain (title → ``agent · timeAgo``) with this
   * component's own ``timeAgo`` so the wording matches the rest of
   * the panel.
   */
  instanceTitle(row: InstanceRow): string {
    return instanceDisplayTitle(row, (d) => this.timeAgo(d));
  }

  /**
   * Instance node meta line — ``agent · N jobs · M agents · timeAgo``
   * (zero-count segments dropped by the model helper). Job count =
   * the receipts attached anywhere in the node's subtree. "M agents"
   * counts the BUILT nested children (W3 — what the tree actually
   * shows), not the wire ``row.children`` pre-KB-strip field.
   */
  instanceMeta(node: InstanceNode): string {
    return instanceMetaLine(
      node,
      instanceSubtreeJobs(node).length,
      (d) => this.timeAgo(d)
    );
  }

  /** Status badge colour for an instance node (model palette). */
  instanceStatusColor(status: InstanceNodeStatus): string {
    return getInstanceStatusColor(status);
  }

  /** Material icon per instance status — presentation-only mapping. */
  instanceStatusIcon(status: InstanceNodeStatus): string {
    switch (status) {
      case 'running':
        return 'sync';
      case 'waiting':
      case 'queued':
        return 'schedule';
      case 'waiting_children':
        return 'account_tree';
      case 'paused':
        return 'pause_circle';
      case 'completed':
        return 'check_circle';
      case 'error':
      case 'failed':
        return 'error';
      case 'terminated':
        return 'cancel';
      default:
        return 'radio_button_unchecked';
    }
  }

  /** Project display label — falls back to a shortened id when unknown. */
  projectLabel(job: Job): string {
    const id = job.project_id;
    if (id === null || id === undefined) return '—';
    return this.projectNameMap().get(id) ?? this.shortenId(id);
  }

  /**
   * Truncate an id to its first 8 characters followed by an ellipsis.
   * Matches the convention used across the header surfaces.
   */
  shortenId(id: string | null | undefined): string {
    if (!id) return '—';
    return id.length > 8 ? id.substring(0, 8) + '...' : id;
  }

  /**
   * Human-readable "X ago" formatter for timestamps. Mirrors the
   * logic in ``notification-bell.component.ts`` — no shared util
   * exists, so the duplication stays intentional and local.
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

  /** Material icon name for a job status. */
  getStatusIcon(status: JobStatus): string {
    switch (status) {
      case 'completed':
        return 'check_circle';
      case 'settled':
        // Receipt-style glyph — a settled mirror row IS a delivery receipt,
        // not a completed mission. `receipt_long` (Material Icons codepoint
        // ef6e) is visually distinct from completed's check_circle.
        //
        // P3 review — promoted to the ``RECEIPT_LONG_GLYPH`` named
        // export (single source of truth shared with the job card
        // + receipt chip); the literal lives on the model so a
        // future glyph swap stays in one place.
        return RECEIPT_LONG_GLYPH;
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
}
