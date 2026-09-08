// Instances-primary job-queue tree (2026-09-08, user-locked design V1,
// ``feature/job-queue-instance-tree``).
//
// Users think in INSTANCES (conversations), not missions. The tree is
// ROOT instances on top → child instances beneath → job receipts at
// leaves. This module holds the pure, Angular-free model layer:
//
//   * ``InstanceRow``     — the wire row of ``GET /api/instances``
//     (root-paginated; ALL descendants of the current root page are
//     included in the FLAT response list, each row carrying its child
//     ids in ``children`` — see ``daemon/routers/instances.py``
//     ``list_instances``: "Pagination is root-based … ALL descendants
//     of each root in the current page are loaded via BFS and included
//     in the flat result list").
//   * ``InstanceNode``    — the tree node (row + child nodes + the
//     jobs the builder attached).
//   * ``buildInstanceNodes``  — flat wire rows → nested roots (the
//     instance-list Map pattern).
//   * ``buildInstanceTree``   — roots + jobs → the four buckets the
//     panel renders (liveRoots / queued / recentRoots / recentFlat).
//   * ``shouldAutoExpandInstanceTree`` — FIRST 2 live roots
//     auto-expand (user-specified: exactly 2); further live roots
//     collapsed; terminal roots always start collapsed.
//
// Pure helpers, no Angular deps — exercised by
// ``instance-node.model.spec.ts``.

import type { Job } from './job.model';
import { isTerminalStatus } from './job.model';

// ─────────────────────────────────────────────────────────────────────────
// Instance status domain
// ─────────────────────────────────────────────────────────────────────────

/**
 * Full authoritative BE ``InstanceStatus`` value space — quoted verbatim
 * from ``daemon/repositories/instance/models.py`` (single source of
 * truth, re-exported by ``daemon/models/instance.py``):
 *
 *   IDLE             = "idle"
 *   RUNNING          = "running"
 *   WAITING          = "waiting"            # Active but no in-flight work
 *   PAUSED           = "paused"
 *   COMPLETED        = "completed"
 *   ERROR            = "error"
 *   TERMINATED       = "terminated"
 *   QUEUED           = "queued"             # Idle but has queued messages
 *   WAITING_CHILDREN = "waiting_children"   # Parent waiting for child reports
 *   FAILED           = "failed"             # Task-level failure
 *
 * The legacy FE ``InstanceStatus`` (models/index.ts) predates ``waiting``
 * and misses it; this type carries the FULL BE set so the tree builder
 * can classify every wire value (no status falls between buckets).
 */
export type InstanceNodeStatus =
  | 'idle'
  | 'running'
  | 'waiting'
  | 'paused'
  | 'completed'
  | 'error'
  | 'terminated'
  | 'queued'
  | 'waiting_children'
  | 'failed';

/**
 * Terminal instance statuses — the conversation has ENDED. Mirrors the
 * terminal cluster of the BE enum: ``completed`` (finished normally),
 * ``error`` / ``failed`` (error terminal states), ``terminated``
 * (operator-stopped). Everything else — including ``idle``, ``waiting``
 * (active, awaiting next user input), ``queued`` (has queued messages),
 * ``paused`` (suspended, resumable) and ``waiting_children`` (still
 * working as a parent) — is NON-terminal, i.e. live/open.
 *
 * The split is TOTAL by construction (``isLiveInstanceStatus`` is the
 * complement), so the tree builder can never drop a row between
 * buckets — the instance-side analogue of the jobs NEVER-hide rule.
 */
export function isTerminalInstanceStatus(status: InstanceNodeStatus): boolean {
  return (
    status === 'completed' ||
    status === 'error' ||
    status === 'terminated' ||
    status === 'failed'
  );
}

/** Non-terminal (live/open) instance statuses — complement of the terminal set. */
export function isLiveInstanceStatus(status: InstanceNodeStatus): boolean {
  return !isTerminalInstanceStatus(status);
}

/** Badge colour per instance status — mirrors the instance-list palette
 *  (``instance-list.component.ts`` ``statusColors``) for the shared
 *  values and extends it with the terminal/queued members that page
 *  never renders. */
export function getInstanceStatusColor(status: InstanceNodeStatus): string {
  switch (status) {
    case 'idle':
      return '#c5c5d2';
    case 'running':
      return '#10b981';
    case 'waiting':
      return '#f59e0b';
    case 'waiting_children':
      return '#3b82f6';
    case 'queued':
      return '#9CA3AF';
    case 'paused':
      return '#8b5cf6';
    case 'completed':
      return '#22C55E';
    case 'error':
    case 'failed':
      return '#f43f5e';
    case 'terminated':
      return '#6e6e80';
    default:
      return '#c5c5d2';
  }
}

// ─────────────────────────────────────────────────────────────────────────
// Wire row + tree node
// ─────────────────────────────────────────────────────────────────────────

/**
 * One row of the ``GET /api/instances`` response. Mirrors the BE
 * ``InstanceInfo`` wire fields the panel consumes (see
 * ``daemon/models/instance.py``); ``children`` carries child INSTANCE
 * IDS (the wire field is ``list[str]`` — the nested node form is
 * ``InstanceNode`` below).
 *
 * Structurally compatible with the legacy FE ``InstanceInfo``
 * (models/index.ts) plus ``initiative_message`` and the full status
 * value space, so a raw ``api.listInstances()`` row is assignable
 * without a mapping layer.
 */
export interface InstanceRow {
  instance_id: string;
  agent_id: string;
  agent_tag?: string | null;
  status: InstanceNodeStatus;
  parent_id: string | null;
  title?: string | null;
  /** First real user message (BE captures it on IDLE → RUNNING). */
  initiative_message?: string | null;
  /** Child instance ids (wire shape — NOT nested objects). */
  children: string[];
  created_at: string;
  updated_at: string | null;
  project_id: string | null;
  pinned?: boolean | null;
  color_tag?: string | null;
  icon_tag?: string | null;
  pinned_at?: string | null;
}

/**
 * A node in the instance tree — its wire row, nested child nodes, and
 * the jobs ``buildInstanceTree`` attached to it (``attachedJobs`` is
 * ``[]`` until the builder runs; nodes it produces are ANNOTATED
 * CLONES — the builder never mutates its inputs).
 */
export interface InstanceNode {
  instance: InstanceRow;
  children: InstanceNode[];
  /** Jobs attached to THIS node (``mission_id === instance_id``). */
  attachedJobs: Job[];
}

/**
 * Build the nested root list from the FLAT ``GET /api/instances`` page
 * (roots + all their descendants, BFS order). Same pattern as the
 * instance-list page's ``instanceTree`` computed: Map-based node
 * lookup, parent-child attachment via ``parent_id``; a row whose
 * parent is NOT in the page (paginated-away ancestor, data skew)
 * degrades to a ROOT so it still renders — NEVER hide an instance.
 *
 * CYCLE GUARD (W4) — a row whose ``parent_id`` chains back to itself
 * (self-loop) OR lands on a node we already placed as our descendant
 * degrades to a ROOT (NEVER-hide preserved — the row still renders,
 * just not under a cyclic parent). Without this guard, a mutual cycle
 * in the wire data would build a tree the recursive helpers
 * (``collectSubtreeJobs``, ``sortInstanceNodes``,
 * ``visibleInstanceTreeItems`` walk, ``findNodeById``) cannot traverse
 * without infinite recursion. BE defends too; FE must not stack-overflow
 * on data skew.
 */
export function buildInstanceNodes(rows: ReadonlyArray<InstanceRow>): InstanceNode[] {
  if (!rows.length) return [];
  const nodeMap = new Map<string, InstanceNode>();
  for (const row of rows) {
    nodeMap.set(row.instance_id, { instance: row, children: [], attachedJobs: [] });
  }
  const rootNodes: InstanceNode[] = [];
  // `parentInDescendants`: true when `row.parent_id` resolves to a node
  // that is already a descendant of `row` in the built tree (i.e. we'd
  // close a cycle). Walked recursively; cheap given the small
  // root-paginated page size and bounded by row count.
  const parentInDescendants = (parentNode: InstanceNode, childId: string): boolean => {
    if (parentNode.instance.instance_id === childId) return true;
    for (const c of parentNode.children) {
      if (parentInDescendants(c, childId)) return true;
    }
    return false;
  };
  for (const row of rows) {
    const node = nodeMap.get(row.instance_id)!;
    const parentId = row.parent_id;
    if (
      parentId &&
      parentId !== row.instance_id &&
      nodeMap.has(parentId) &&
      !parentInDescendants(node, parentId)
    ) {
      nodeMap.get(parentId)!.children.push(node);
    } else {
      rootNodes.push(node);
    }
  }
  return rootNodes;
}

// ─────────────────────────────────────────────────────────────────────────
// Tree builder
// ─────────────────────────────────────────────────────────────────────────

/**
 * Defensive cap for the Recent section — the same 10-row cap class the
 * legacy mission tree used (``MAX_RECENT_JOBS``). The Recent band is
 * CAPPED at JOB-row granularity (leader-decided, 2026-09-08 review
 * fold):
 *
 *   (a) JOB-ROW BAND ≤ MAX_RECENT_INSTANCE_ROWS — only JOB rows
 *       (receipts attached anywhere in a recent root's subtree, plus
 *       orphan flat rows) count toward the cap. INSTANCE HEADERS ride
 *       along (headers are structure, not content): a recent root's
 *       own header AND every intermediate child-instance header in
 *       its subtree renders for free — no capacity consumed.
 *   (b) EVERY JOB SURFACES — partial-fit keeps the fitting jobs and
 *       overflows the remainder into ``recentFlat`` (NEVER-hide).
 *       Total rendered rows may therefore EXCEED MAX by design when a
 *       recent root pushes many job receipts AND the orphan flat is
 *       also large; the cap is a JOB-row budget, not a hard row gate.
 *
 * Empty-subtree node rule: a recent candidate with zero subtree jobs
 * pushes UNCONDITIONALLY (zero-capacity-consumed structure). A recent
 * candidate with jobs fits ``min(subtreeJobs.length, MAX - rowCount)``
 * into the band; the remainder overflows to ``recentFlat``. Because
 * headers are free, the band can hold at most MAX jobs even when many
 * structural headers render underneath.
 */
export const MAX_RECENT_INSTANCE_ROWS = 10;

/** Output of ``buildInstanceTree`` — the four buckets the panel renders. */
export interface InstanceTree {
  /** Roots that are live themselves OR have ANY live descendant. */
  liveRoots: InstanceNode[];
  /** Non-terminal jobs whose ``mission_id`` matched NO node. */
  queued: Job[];
  /** Terminal roots (themselves terminal AND no live descendant). */
  recentRoots: InstanceNode[];
  /** Terminal jobs that matched no node (recent-flat rows). */
  recentFlat: Job[];
}

/**
 * Sort comparator for instance nodes — the instance-list pinned-first
 * pattern (``sortNodesPinnedFirst``) with ``updated_at`` as the
 * activity key:
 *
 *   1. pinned rows first (``pinned === true``), most recently pinned
 *      first (``pinned_at`` desc);
 *   2. then activity desc (``updated_at`` desc — the BE bumps it on
 *      every status change);
 *   3. ``instance_id`` asc tiebreak (deterministic).
 */
function compareInstanceNodes(a: InstanceNode, b: InstanceNode): number {
  const aPinned = a.instance.pinned === true;
  const bPinned = b.instance.pinned === true;
  if (aPinned !== bPinned) return aPinned ? -1 : 1;
  if (aPinned) {
    const aP = a.instance.pinned_at ?? '';
    const bP = b.instance.pinned_at ?? '';
    if (aP !== bP) return bP.localeCompare(aP);
  }
  const aU = a.instance.updated_at ?? '';
  const bU = b.instance.updated_at ?? '';
  if (aU !== bU) return bU.localeCompare(aU);
  return a.instance.instance_id.localeCompare(b.instance.instance_id);
}

/** Recursive pinned-first + activity-desc sort (on the builder's own
 *  working copy — inputs are never mutated). */
function sortInstanceNodes(nodes: InstanceNode[]): void {
  nodes.sort(compareInstanceNodes);
  for (const node of nodes) {
    if (node.children.length > 0) sortInstanceNodes(node.children);
  }
}

/** True iff the node itself or ANY descendant is live (non-terminal). */
function subtreeIsLive(node: InstanceNode): boolean {
  if (isLiveInstanceStatus(node.instance.status)) return true;
  return node.children.some(subtreeIsLive);
}

/**
 * Pure tree builder for the instances-primary panel.
 *
 * Inputs:
 * - ``roots`` — nested root nodes (``buildInstanceNodes`` output).
 * - ``activeJobs`` — non-terminal jobs (running/pending/paused).
 * - ``recentJobs`` — terminal jobs.
 *
 * Rules (user-locked design V1):
 * - Each job attaches to its instance node AT ANY DEPTH via
 *   ``job.mission_id === instance_id`` (mission_id IS instance_id —
 *   the grouping key).
 * - Jobs matching NO node: non-terminal → ``queued``, terminal →
 *   ``recentFlat`` (NEVER-hide preserved — orphans always surface).
 * - A root is LIVE if the root itself is live OR ANY descendant is
 *   live; otherwise (root terminal, no live descendant) it is a
 *   recentRoot.
 * - ``liveRoots`` sorted pinned-first then activity desc (the
 *   instance-list pattern), applied recursively to children too.
 * - ``recentRoots`` = terminal roots, newest activity first, capped at
 *   ``MAX_RECENT_INSTANCE_ROWS`` JOB ROWS (headers ride along — see
 *   ``MAX_RECENT_INSTANCE_ROWS`` docstring). A node whose subtree
 *   jobs exceed the remaining JOB-row budget keeps a partial fit; the
 *   remainder OVERFLOWS into ``recentFlat`` so every job still surfaces
 *   (partial-fit + overflow semantics, same as legacy mission tree).
 *
 * Purity: the output nodes are fresh annotated clones (structure +
 * row references shared, ``attachedJobs`` filled per node) — the
 * input nodes are never mutated, so the builder is safe to re-run
 * over the same nodes whenever the job inputs change.
 */
export function buildInstanceTree(
  roots: ReadonlyArray<InstanceNode>,
  activeJobs: ReadonlyArray<Job>,
  recentJobs: ReadonlyArray<Job>
): InstanceTree {
  // 1) Working copy, sorted pinned-first + activity desc (every level).
  const sortedRoots = roots.map(cloneNode);
  sortInstanceNodes(sortedRoots);

  // 2) Index EVERY node in the subtree (any depth) by instance_id.
  const nodesById = new Map<string, InstanceNode>();
  const indexSubtree = (node: InstanceNode): void => {
    nodesById.set(node.instance.instance_id, node);
    for (const child of node.children) indexSubtree(child);
  };
  for (const root of sortedRoots) indexSubtree(root);

  // 3) Attach each job to its node AT ANY DEPTH; unmatched jobs fall
  //    back to queued (non-terminal) / recentFlat (terminal) — a job
  //    never silently vanishes.
  const jobsByNode = new Map<string, Job[]>();
  const queued: Job[] = [];
  const orphanRecentFlat: Job[] = [];
  const route = (job: Job): void => {
    const mid = job.mission_id ?? null;
    const node = mid ? nodesById.get(mid) : undefined;
    if (node) {
      const list = jobsByNode.get(node.instance.instance_id);
      if (list) list.push(job);
      else jobsByNode.set(node.instance.instance_id, [job]);
    } else if (isTerminalStatus(job.status)) {
      orphanRecentFlat.push(job);
    } else {
      queued.push(job);
    }
  };
  for (const job of activeJobs) route(job);
  for (const job of recentJobs) route(job);
  const stash = (node: InstanceNode): void => {
    node.attachedJobs = jobsByNode.get(node.instance.instance_id) ?? [];
    for (const child of node.children) stash(child);
  };
  for (const root of sortedRoots) stash(root);

  // 4) Partition roots: live (self or any descendant live) vs recent.
  const liveRoots: InstanceNode[] = [];
  const recentCandidates: InstanceNode[] = [];
  for (const root of sortedRoots) {
    if (subtreeIsLive(root)) liveRoots.push(root);
    else recentCandidates.push(root);
  }

  // 5) Cap Recent at MAX_RECENT_INSTANCE_ROWS JOB ROWS (W1 leader-
  //    decided: only JOB rows count; instance headers ride along).
  //    For each recent candidate: ALWAYS push the structural clone
  //    (its header is free — a node with zero subtree jobs is
  //    pure structure, no capacity consumed), fit
  //    min(subtreeJobs.length, MAX - rowCount) jobs into the band,
  //    overflow the rest to recentFlat (NEVER-hide). Empty-subtree
  //    nodes (no jobs anywhere) push with visibleJobs=[]; the cap
  //    sees them as zero consumption so the next candidate still gets
  //    its full MAX allotment.
  const recentRoots: InstanceNode[] = [];
  const overflowFlat: Job[] = [];
  let rowCount = 0;
  for (const node of recentCandidates) {
    const subtreeJobs: Job[] = [];
    collectSubtreeJobs(node, subtreeJobs);
    const capacity = Math.max(0, MAX_RECENT_INSTANCE_ROWS - rowCount);
    const fitCount = Math.min(subtreeJobs.length, capacity);
    const visibleJobs = subtreeJobs.slice(0, fitCount);
    const overflowJobs = subtreeJobs.slice(fitCount);
    recentRoots.push(cloneWithJobs(node, visibleJobs));
    rowCount += fitCount;
    overflowFlat.push(...overflowJobs);
  }

  // 6) Fill remaining JOB-row capacity from the orphan flat list, then
  //    append everything that overflowed AFTER the capped rows. Same
  //    JOB-row-cap + never-hide contract as step 5 — only JOB rows
  //    count toward MAX; orphan flat rows surface unconditionally via
  //    recentFlat (in-band up to the cap, overflow after).
  const recentFlat: Job[] = [];
  for (const job of orphanRecentFlat) {
    if (rowCount >= MAX_RECENT_INSTANCE_ROWS) {
      overflowFlat.push(job);
      continue;
    }
    recentFlat.push(job);
    rowCount += 1;
  }
  recentFlat.push(...overflowFlat);

  return { liveRoots, queued, recentRoots, recentFlat };
}

function cloneNode(node: InstanceNode): InstanceNode {
  return {
    instance: node.instance,
    children: node.children.map(cloneNode),
    attachedJobs: [],
  };
}

/** Clone a subtree carrying the VISIBLE jobs on its root (deeper
 *  clones carry none — see buildInstanceTree step 5). */
function cloneWithJobs(node: InstanceNode, visibleJobs: Job[]): InstanceNode {
  const clone = cloneNode(node);
  clone.attachedJobs = visibleJobs;
  return clone;
}

/** DFS-collect the jobs attached anywhere in a node's subtree, in
 *  display order (children first — receipts are the subtree LEAVES —
 *  then the node's own attached jobs). */
function collectSubtreeJobs(node: InstanceNode, out: Job[]): Job[] {
  for (const child of node.children) {
    collectSubtreeJobs(child, out);
  }
  out.push(...node.attachedJobs);
  return out;
}

/** All receipt jobs attached anywhere in a node's subtree (display order). */
export function instanceSubtreeJobs(node: InstanceNode): Job[] {
  return collectSubtreeJobs(node, []);
}

/**
 * Auto-expand decision — FIRST 2 live roots auto-expand (user-specified:
 * exactly 2); further live roots stay collapsed; terminal roots ALWAYS
 * start collapsed.
 *
 * Returns the IDS to seed into the panel's expansion set (not a bare
 * boolean) because the expansion state is keyed by ``instance_id`` —
 * a boolean cannot express "which two". Empty array when there are no
 * live roots.
 */
export function shouldAutoExpandInstanceTree(liveRoots: ReadonlyArray<InstanceNode>): string[] {
  return liveRoots
    .slice(0, 2)
    .map((n) => n.instance.instance_id)
    .filter((id) => id.length > 0);
}

// ─────────────────────────────────────────────────────────────────────────
// Display helpers (pure — components inject their own timeAgo)
// ─────────────────────────────────────────────────────────────────────────

/**
 * Display title for an instance node — honest fallback chain:
 *   1. ``title`` (server-authoritative instance title).
 *   2. ``${agent_id} · ${timeAgo(created_at)}`` — the agent label plus
 *      when the conversation started (mirrors the legacy
 *      ``missionDisplayTitle`` fallback philosophy).
 *
 * A non-null agent_id always wins over an empty timestamp; both null
 * returns '' so the caller decides the placeholder.
 */
export function instanceDisplayTitle(
  row: InstanceRow,
  timeAgoFn?: (d: string | null | undefined) => string
): string {
  if (row.title) return row.title;
  const agent = row.agent_id ?? '';
  const formatter = timeAgoFn ?? defaultInstanceTimeAgo;
  const ts = formatter(row.created_at);
  if (agent && ts) return `${agent} · ${ts}`;
  if (agent) return agent;
  if (ts) return ts;
  return '';
}

/**
 * Meta line for a collapsed instance node —
 * ``agent · N jobs · M agents · timeAgo(updated_at)``. Zero-count
 * segments are dropped so a childless, jobless node reads
 * ``agent · timeAgo`` instead of ``agent · 0 jobs · 0 agents · …``.
 *
 * W3 (2026-09-08 review fold): "M agents" counts the ACTUAL nested
 * child-instance NODES the tree builder produced
 * (``node.children.length``), NOT the wire ``row.children`` field. The
 * wire field is the pre-KB-strip child-id list — it can over-report
 * children that the tree builder degraded to roots (cyclic / orphan
 * parent) or filtered out for any reason. The TREE shape is what the
 * user sees; the meta line must mirror it. The function takes the
 * built ``InstanceNode`` so the caller doesn't have to thread the
 * derived child count in.
 */
export function instanceMetaLine(
  node: InstanceNode,
  jobCount: number,
  timeAgoFn?: (d: string | null | undefined) => string
): string {
  const row = node.instance;
  const agent = row.agent_id ?? '—';
  const formatter = timeAgoFn ?? defaultInstanceTimeAgo;
  const ago = formatter(row.updated_at) || formatter(row.created_at) || 'idle';
  const parts: string[] = [agent];
  if (jobCount > 0) parts.push(`${jobCount} job${jobCount === 1 ? '' : 's'}`);
  // W3: count the BUILT nested children (what the tree actually shows),
  // not the wire ``row.children`` field (pre-KB-strip).
  const childCount = node.children.length;
  if (childCount > 0) {
    parts.push(`${childCount} agent${childCount === 1 ? '' : 's'}`);
  }
  parts.push(ago);
  return parts.join(' · ');
}

/** Default ISO-string formatter (same wording as the legacy mission default). */
function defaultInstanceTimeAgo(dateString: string | null | undefined): string {
  if (!dateString) return '';
  const date = new Date(dateString);
  if (isNaN(date.getTime())) return '';
  const diffMs = Date.now() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return 'just now';
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour}h ago`;
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 7) return `${diffDay}d ago`;
  return date.toLocaleDateString();
}

// ─────────────────────────────────────────────────────────────────────────
// Keyboard navigation (same machinery contract as the legacy mission tree)
// ─────────────────────────────────────────────────────────────────────────

/**
 * A visible item in the panel's keyboard-navigable trees (LIVE
 * CONVERSATIONS + RECENT). Flattened in display order; collapsed
 * nodes' descendants are OMITTED entirely so ↑/↓ skip them.
 *
 * ``depth`` drives indentation; ``parentInstanceId`` (nearest ancestor
 * instance, ``null`` for roots) lets ArrowLeft collapse the owning
 * node from a child row (WAI-ARIA tree pattern).
 */
export type InstanceTreeItem =
  | {
      kind: 'instance';
      tree: 'live' | 'recent';
      node: InstanceNode;
      depth: number;
      parentInstanceId: string | null;
    }
  | {
      kind: 'job';
      tree: 'live' | 'recent';
      instanceId: string;
      depth: number;
      parentInstanceId: string | null;
      job: Job;
    };

/**
 * Flatten the LIVE + RECENT trees in display order. For each node: the
 * node itself, then (when expanded) its child instances recursively,
 * then its attached receipt jobs. Live roots come first, recent roots
 * after — matching the rendered DOM exactly (the panel renders FROM
 * this list, so keyboard order and visual order cannot drift).
 */
export function visibleInstanceTreeItems(
  liveRoots: ReadonlyArray<InstanceNode>,
  recentRoots: ReadonlyArray<InstanceNode>,
  expandedIds: ReadonlySet<string> | undefined
): InstanceTreeItem[] {
  const items: InstanceTreeItem[] = [];
  const walk = (
    node: InstanceNode,
    tree: 'live' | 'recent',
    depth: number,
    parentInstanceId: string | null
  ): void => {
    items.push({ kind: 'instance', tree, node, depth, parentInstanceId });
    const id = node.instance.instance_id;
    if (expandedIds?.has(id)) {
      for (const child of node.children) {
        walk(child, tree, depth + 1, id);
      }
      for (const job of node.attachedJobs) {
        items.push({
          kind: 'job',
          tree,
          instanceId: id,
          depth: depth + 1,
          parentInstanceId: id,
          job,
        });
      }
    }
  };
  for (const root of liveRoots) walk(root, 'live', 0, null);
  for (const root of recentRoots) walk(root, 'recent', 0, null);
  return items;
}

/**
 * Move from ``currentIndex`` by ``+1`` (down) or ``-1`` (up). CLAMPS at
 * the ends (no wrap — identical semantics to the legacy mission-tree
 * helper: wrap in a two-tree layout reads as a glitch). A ``-1`` index
 * (no focus yet) treats ↑ as last / ↓ as first.
 */
export function nextInstanceTreeItem(
  items: ReadonlyArray<InstanceTreeItem>,
  currentIndex: number,
  delta: -1 | 1
): number {
  if (items.length === 0) return -1;
  if (currentIndex < 0 || currentIndex >= items.length) {
    return delta === 1 ? 0 : items.length - 1;
  }
  const next = currentIndex + delta;
  if (next < 0) return 0;
  if (next >= items.length) return items.length - 1;
  return next;
}

/**
 * Stable string id for an item — drives the panel's ``focusedItemId``
 * signal and the real-DOM focus calls:
 *
 *   - instance node: "inst:live|i-1" / "inst:recent|i-7"
 *   - job child:     "inst:live|i-1|job:j-9"
 *
 * The tree prefix keeps LIVE and RECENT focus independent.
 */
export function instanceTreeItemId(item: InstanceTreeItem): string {
  if (item.kind === 'instance') {
    return `inst:${item.tree}|inst:${item.node.instance.instance_id}`;
  }
  return `inst:${item.tree}|inst:${item.instanceId}|job:${item.job.job_id}`;
}
