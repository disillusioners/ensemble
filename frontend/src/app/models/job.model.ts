// Job Queue Models for Frontend

// M3 (mission-class, 2026-09-03, ``feature/mission-class``) — the
// transport-receipt terminal for mirror rows (``job_type='message'``)
// is ``settled`` (ADR-MISSION-01 §6.6 I3 amendment; ADR §6.7 vocabulary
// table). Task rows (``job_type='task'``) keep ``completed`` unchanged
// — a task job IS its own mission and ``completed`` is the work
// outcome, not a transport signal. ``settled`` is DISJOINT from the
// mission-side ``MissionLiveness`` vocabulary (which still carries
// ``completed`` for a terminal instance).
export type JobStatus = 'pending' | 'processing' | 'paused' | 'completed' | 'settled' | 'failed' | 'cancelled' | 'dead_letter';

export type JobSource = 'api' | 'telegram' | 'scheduler' | 'webhook';

/**
 * JobItem-side kind discriminator (Fix C read-model split,
 * docs/job-task-system.md §8.2).
 *
 * * ``'task'``    — mission row: the JobItem IS the mission; its
 *   ``status`` is the lifecycle answer (one answer, no split).
 * * ``'message'`` — mirror row: a receipt proving the message was
 *   handled; its ``status`` is the receipt answer and
 *   ``mission_liveness`` carries the parent-mission answer.
 *
 * ``undefined``/``null`` means the wire did not carry the field
 * (Task-backed records — e.g. report rows synthesised from
 * ``/api/work`` — have no JobItem, hence no job_type).
 */
export type JobJobType = 'task' | 'message';

/**
 * Canonical liveness of the linked instance behind a mirror row.
 *
 * Value space is exactly the canonical projection of InstanceStatus
 * (``canonicalize_status``): pending, processing, paused,
 * completed, failed, cancelled. Use values verbatim — the FE never
 * invents a state for ``null`` (mission row / degraded lookup /
 * no linked instance are indistinguishable by design; all ``null``s
 * render nothing extra and fall back to receipt-only semantics).
 */
export type MissionLiveness = 'pending' | 'processing' | 'paused' | 'completed' | 'failed' | 'cancelled';

/**
 * WorkKind subset that may appear on a Job record.
 *
 * Only ``'job'`` is meaningful for jobs surfaced through
 * ``JobService``; ``'report'`` is reserved for the unified Work
 * surface so a Job-shaped object synthesised from a ``Work`` record
 * can carry the kind forward without re-typing the ``work.model``
 * namespace everywhere. ``'turn'`` was removed in Phase 4 partial
 * collapse (2026-07-06) — message turns are now JobItems.
 */
export type JobWorkKind = 'job' | 'report';

export interface Job {
  job_id: string;
  agent_id: string;
  message?: string;
  source?: JobSource;
  project_id: string | null;
  priority: number; // 1-10
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  instance_id: string | null;
  error_message: string | null;
  result_summary: string | null;
  job_metadata?: Record<string, any> | null;
  queue_id?: string | null; // queue this job belongs to
  cancelled_at: string | null;
  deleted_at?: string | null;
  position?: number; // queue position if pending
  // Dead Letter Queue fields
  dlq_reason?: string | null; // reason for moving to DLQ
  retry_count?: number; // number of retries before going to DLQ
  moved_to_dlq_at?: string | null; // timestamp when moved to DLQ
  // Virtual Job Management Surface (Phase 4): work kind discriminator.
  // Optional for backward compatibility — existing JobService responses
  // omit it, in which case the card treats the record as a real
  // queued job (kind === 'job') and shows the queue badge as before.
  kind?: JobWorkKind;
  // Fix C read-model split (§8.2): JobItem-side discriminator.
  // 'task' = mission row, 'message' = mirror/receipt row, null/
  // undefined = Task-backed record (no JobItem) or legacy payload.
  job_type?: JobJobType | null;
  // Fix C read-model split (§8.2): canonical status of the linked
  // instance, populated ONLY for mirror rows. null means mission
  // row / degraded lookup / no linked instance — by design; render
  // nothing extra rather than inventing a state.
  mission_liveness?: MissionLiveness | null;
  // Mission-tree panel (2026-09-07, ``feature/job-queue-mission-tree``):
  // BE ships ``mission_id`` (== the instance the job is grouped under;
  // equals ``instance_id`` for mirror rows and the task row's own
  // instance for task rows) and ``mission_ref`` (the cross-reference
  // payload the BE added in M2) on EVERY job payload — FE consumes
  // them verbatim and uses ``mission_id`` as the grouping key for
  // the new tree panel. Both optional for backward compatibility.
  mission_id?: string | null;
  mission_ref?: { mission_id: string; agent_id: string; liveness: string } | null;
}

export interface JobCreate {
  agent_id: string;
  message: string;
  project_id?: string;
  priority?: number;
  source?: JobSource;
  queue_id?: string;
  metadata?: Record<string, any>;
  image_urls?: string[]; // base64 data URIs for vision support
}

export interface JobFilters {
  status?: JobStatus[];
  source?: JobSource;
  agent_id?: string;
  project_id?: string;
  queue_id?: string;
  include_deleted?: boolean;
}

export interface JobEventPayload {
  job_id: string;
  status?: JobStatus;
  previous_status?: JobStatus;
  instance_id?: string;
  result_summary?: string;
  error_message?: string;
  queue_id?: string | null;
  image_urls?: string[]; // base64 data URIs for vision support
  // Fix C split-semantics SSE payloads (_ResolvedWork) also carry the
  // discriminator + liveness pair. Optional — legacy payloads omit them.
  job_type?: JobJobType | null;
  mission_liveness?: MissionLiveness | null;
}

export interface JobEvent {
  event: 'connected' | 'status_update' | 'completed' | 'error' | 'keepalive';
  data: JobEventPayload | null;
  image_urls?: string[]; // base64 data URIs for vision support
}

// Helper Functions

// M3 (mission-class, 2026-09-03) — ``settled`` is now a terminal
// value (mirror-receipt terminal). Task rows still carry ``completed``.
// Both are terminal; the per-kind split is the whole point of the
// rename.
export function isTerminalStatus(status: JobStatus): boolean {
  return status === 'completed' || status === 'settled' || status === 'failed' || status === 'cancelled' || status === 'dead_letter';
}

/**
 * Paused jobs are non-terminal but suspended (instance paused). The
 * Jobs UI treats ``paused`` as an active state — a paused job can be
 * resumed (via its instance) or cancelled, so it must stay visible in
 * the default list and be selectable as a status filter.
 */
export function isPausedStatus(status: JobStatus): boolean {
  return status === 'paused';
}

/**
 * Whether a job is in an active (non-terminal) state the operator may
 * want to monitor: pending, processing, or paused. Used to decide
 * whether a status filter selection should keep the row visible.
 */
export function isActiveStatus(status: JobStatus): boolean {
  return status === 'pending' || status === 'processing' || status === 'paused';
}

export function isJobDeleted(job: Job): boolean {
  return !!job.deleted_at;
}

// ── Fix C read-model split helpers (§8.2) ────────────────────────────────

/**
 * True when the row is a mirror/receipt row (JobItem kind
 * ``'message'``). Only mirror rows carry the split semantics —
 * receipt chip + mission-liveness indicator. Mission rows
 * (``'task'``) and Task-backed records (no job_type) render
 * nothing extra: a mission row's own ``status`` IS the liveness
 * signal.
 */
export function isReceiptRow(job: Pick<Job, 'job_type'>): boolean {
  return job.job_type === 'message';
}

/**
 * Style split for ``mission_liveness`` values, used verbatim from
 * the wire. Live = the parent mission is still working (pending /
 * processing / paused — the non-terminal cluster). Settled = the
 * parent mission reached a terminal canonical state (completed /
 * failed / cancelled).
 *
 * ``dead_letter`` is deliberately absent: it exists in the job-row
 * admission domain but is unreachable from the instance-status
 * domain ``mission_liveness`` reads (see §8.2 value space).
 */
export function isLiveMissionLiveness(value: MissionLiveness): boolean {
  return value === 'pending' || value === 'processing' || value === 'paused';
}

/**
 * Chip colour for a ``mission_liveness`` value. Mirrors the Job
 * status palette for the overlapping names so a live mission reads
 * like an active job and a terminal mission reads like its canonical
 * end status. ``pending`` has no InstanceStatus source today (forward-
 * compat member of the ratified value space) and maps to gray.
 */
export function getMissionLivenessColor(value: MissionLiveness): string {
  switch (value) {
    case 'pending':
      return '#9CA3AF'; // gray-400
    case 'processing':
      return '#3B82F6'; // blue-500
    case 'paused':
      return '#F59E0B'; // amber-500
    case 'completed':
      return '#22C55E'; // green-500
    case 'failed':
      return '#EF4444'; // red-500
    case 'cancelled':
      return '#F59E0B'; // amber-500
    default:
      return '#9CA3AF'; // gray-400
  }
}

/**
 * Render decision for the mission-liveness indicator on a row.
 *
 * Returns ``null`` — render NOTHING extra — for every case the
 * contract keeps silent: mission rows (``job_type='task'``),
 * Task-backed records (no ``job_type``), degraded lookups, and
 * rows with no linked instance. All of those arrive as
 * ``mission_liveness`` absent or ``null`` and are
 * indistinguishable by design (§8.2); the FE never invents a state
 * for them.
 *
 * For mirror rows with a non-null ``mission_liveness`` it returns
 * the verbatim value plus the derived live/terminal styling split
 * (encoded in the ``live`` boolean — true for the non-terminal
 * cluster pending/processing/paused, false for completed/failed/
 * cancelled).
 */
export interface MissionLivenessChip {
  value: MissionLiveness;
  live: boolean;
  label: string; // "mission: processing" — canonical value verbatim
  color: string;
}

export function missionLivenessChip(
  job: Pick<Job, 'job_type' | 'mission_liveness'>
): MissionLivenessChip | null {
  if (!isReceiptRow(job)) return null;
  const value = job.mission_liveness;
  if (!value) return null;
  return {
    value,
    live: isLiveMissionLiveness(value),
    label: `mission: ${value}`,
    color: getMissionLivenessColor(value),
  };
}

/**
 * Tooltip wording for a mission-liveness chip. One implementation,
 * used by every render surface so the wording cannot drift between
 * card / panel / drawer.
 *
 * Two reads:
 *
 * * ``live`` — message receipt handled; parent mission is still
 *   working. The canonical status is appended verbatim.
 * * ``terminal`` — message receipt handled; parent mission reached a
 *   terminal canonical state. Same append.
 *
 * Both keep the canonical ``chip.value`` (not a recased / fabricated
 * string) so the user can trust the parenthetical answer.
 *
 * M3 (mission-class, 2026-09-03) — mission-side prose MUST NOT use
 * the word ``settled``; ``settled`` is a transport-receipt
 * vocabulary word that now belongs only to mirror rows. The
 * mission-side vocabulary is the canonical ``MissionLiveness`` set
 * (pending / processing / paused / completed / failed / cancelled)
 * — a terminal mission liveness reads as ``completed``, ``failed``,
 * or ``cancelled``, NOT ``settled``. The tooltip prose is reworded
 * to use the work-outcome terminal wording (the parenthetical
 * carries the canonical value verbatim).
 */
export function missionLivenessChipTooltip(chip: MissionLivenessChip): string {
  return chip.live
    ? `Message receipt handled. Parent mission still working (canonical status: ${chip.value}).`
    : `Message receipt handled. Parent mission finished (canonical status: ${chip.value}).`;
}

/**
 * Distinct live-mission ids across a flat job list (Fix C §8.2).
 *
 * A mirror row (``job_type === 'message'``) whose
 * ``mission_liveness`` is live (pending / processing / paused)
 * proves its parent mission is still working — even when the
 * mirror's own receipt status is terminal. Rows are de-duplicated
 * by ``instance_id`` (many receipts per mission, one mission); a
 * null ``instance_id`` falls back to ``job_id`` so the row still
 * counts rather than silently vanishing.
 *
 * ``instance_id`` is `string | null` (required, not optional) on
 * message rows; the ``job_id`` fallback is defensive-only for the
 * pathological case where the wire carries an unexpected null.
 *
 * Round-1 badge rewiring moved the badge N to ``GET /api/missions`` —
 * retained (NOT dead): spec-covered canonical receipt-window
 * derivation, mirrored by e2e/fe_liveness_badge.spec.ts; the chip
 * surfaces stay on the sibling ``missionLivenessChip`` family.
 */
export function liveMissionIds(jobs: ReadonlyArray<Pick<Job, 'job_type' | 'mission_liveness' | 'instance_id' | 'job_id'>>): Set<string> {
  const ids = new Set<string>();
  for (const j of jobs) {
    if (j.job_type === 'message' && j.mission_liveness && isLiveMissionLiveness(j.mission_liveness)) {
      ids.add(j.instance_id ?? j.job_id);
    }
  }
  return ids;
}

export function getStatusColor(status: JobStatus): string {
  switch (status) {
    case 'pending':
      return '#9CA3AF'; // gray-400
    case 'processing':
      return '#3B82F6'; // blue-500
    case 'paused':
      return '#F59E0B'; // amber-500 — suspended, non-terminal
    case 'completed':
      return '#22C55E'; // green-500 — task terminal
    // M3 (mission-class, 2026-09-03) — mirror-receipt terminal
    // (``settled``). Distinct colour from ``completed`` so a settled
    // mirror reads as transport-handled (teal) rather than work-done
    // (green) — keeps the transport/work vocabulary split visible in
    // the badge.
    case 'settled':
      return '#14B8A6'; // teal-500
    case 'failed':
      return '#EF4444'; // red-500
    case 'cancelled':
      return '#F59E0B'; // amber-500
    case 'dead_letter':
      return '#7C3AED'; // purple-600
    default:
      return '#9CA3AF'; // gray-400
  }
}

export function getPriorityColor(priority: number): string {
  if (priority >= 8) return '#EF4444'; // red-500 - high priority
  if (priority >= 5) return '#F59E0B'; // amber-500 - medium-high
  if (priority >= 3) return '#3B82F6'; // blue-500 - medium
  return '#22C55E'; // green-500 - low priority
}

// Dead Letter Queue Models

export interface DeadLetterItem {
  dlq_id: string;
  job_id: string;
  agent_id: string;
  agent_dir: string;
  message: string;
  source: string;
  project_id: string;
  queue_id: string | null;
  error_message: string | null;
  retry_count: number;
  failed_at: string | null;
  moved_to_dlq_at: string;
  reason: string;
  metadata?: Record<string, any> | null;
}

export interface RetryAllResult {
  replayed: number;
  failed: number;
  errors: { dlq_id: string; error: string }[];
}

// DLQ Replay Response (from /api/projects/{projectId}/dlq/{dlqId}/replay)
export interface DLQReplayResponse {
  job_id: string;
  status: string;
  message: string;
}

// DLQ List Response wrapper
export interface DLQListResponse {
  items: DeadLetterItem[];
  total: number;
}

// ── Mission-tree panel (2026-09-07, ``feature/job-queue-mission-tree``) ───

/**
 * Full mission row consumed by the new tree panel — mirrors the BE
 * ``MissionResponse`` wire shape exactly (see ``daemon/routers/schemas.py``
 * class ``MissionResponse``, see also ``MissionSummary`` in
 * ``models/mission.model.ts`` which is the badge's minimal subset).
 *
 * All fields nullable to mirror the BE degraded-lookup contract (§8.2:
 * 200 with None-fields, never 500). The FE never invents a value for
 * a null field — the tree builder routes null-bearing missions into the
 * fallback path ("NEVER hide a job").
 */
export interface MissionSummary {
  mission_id: string | null;
  agent_id: string | null;
  parent_mission_id: string | null;
  liveness: MissionLiveness | null;
  terminal_reason: string | null;
  epoch: number | null;
  linked_jobs: string[];
  started_at: string | null;
  last_activity_at: string | null;
  title: string | null;
  initiative_preview: string | null;
}

/**
 * Display title for a mission node — honest fallback chain:
 *
 *   1. ``title`` (server-authoritative ``instance_metadata['title']``).
 *   2. ``${agent_id} · ${timeAgo(last_activity_at)}`` (mirrors the
 *      existing ``resolveTitle`` fallback philosophy — agent_id is
 *      the human-meaningful label and timeAgo tells the operator when
 *      the mission last did anything).
 *
 * Empty / null guards: a null ``agent_id`` AND null ``last_activity_at``
 * returns an empty string so the caller can decide whether to render
 * the placeholder; a non-null ``agent_id`` ALWAYS wins even with a null
 * timestamp (the agent_id alone is more useful than "unknown time").
 *
 * ``timeAgoFn`` is an OPTIONAL injection point — defaults to an
 * internal ISO-string formatter so the helper is usable end-to-end
 * without depending on any component class. The component's own
 * ``timeAgo`` (with "just now" / "Xm ago" / "Xh ago" wording) is the
 * production wiring; passing ``undefined`` lets tests pin deterministic
 * output.
 *
 * Pure helper, no Angular deps — exercised by ``job.model.spec.ts``.
 */
export function missionDisplayTitle(
  m: MissionSummary,
  timeAgoFn?: (d: string | null | undefined) => string
): string {
  if (m.title) return m.title;
  const agent = m.agent_id ?? '';
  const formatter = timeAgoFn ?? defaultMissionTimeAgo;
  const ts = formatter(m.last_activity_at);
  if (agent && ts) return `${agent} · ${ts}`;
  if (agent) return agent;
  if (ts) return ts;
  return '';
}

/** Default ISO-string formatter used when no custom ``timeAgoFn`` is passed. */
function defaultMissionTimeAgo(dateString: string | null | undefined): string {
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

/** A mission node in the tree — its mission row plus the jobs attached. */
export interface MissionNode {
  mission: MissionSummary;
  jobs: Job[];
}

/** Output of ``buildQueueTree`` — three buckets the panel renders. */
export interface QueueTree {
  /** Live missions (processing/pending/paused) with their attached jobs. */
  liveMissions: MissionNode[];
  /** Non-terminal jobs whose mission is NOT in the listed missions set. */
  queued: Job[];
  /** Terminal mission nodes + terminal jobs that map to no listed mission. */
  recent: MissionNode[];
  recentFlat: Job[];
}

/** Defensive cap for the Recent section — matches the legacy ``MAX_RECENT_JOBS``. */
export const MAX_RECENT_JOBS = 10;

/**
 * Pure tree builder for the job-queue panel.
 *
 * Inputs:
 * - ``activeJobs`` — non-terminal jobs (running/pending/paused).
 *   The indicator passes the FULL non-terminal set here so queued
 *   jobs reach ``tree().queued`` (C1 fix — the prior ``runningJobs``
 *   filter silently starved the QUEUED section by excluding pending
 *   + queued statuses).
 * - ``recentJobs`` — terminal jobs (completed/settled/failed/cancelled/
 *   dead_letter), typically the indicator's ``recentJobs`` output.
 * - ``missions`` — the missions list from ``GET /api/missions`` (BE
 *   orders by ``last_activity_at DESC NULLS LAST`` with mission_id
 *   tiebreak — §8.4). The grouping key is
 *   ``job.mission_id === mission.mission_id`` (the BE's
 *   ``linked_jobs`` is the reverse index; we use mission_id matching
 *   against the job lists so the FE never has to look up an instance
 *   just to group a row).
 *
 * Rules:
 * - Live missions = liveness in {processing, pending, paused}, sorted
 *   ``last_activity_at`` desc (nulls last), then ``mission_id`` asc
 *   tiebreak (deterministic).
 * - Task jobs whose mission isn't in the missions list STILL render —
 *   they fall into ``queued`` (non-terminal, unattached) so the panel
 *   never silently hides a row.
 * - ``queued`` = non-terminal jobs with no mission linkage (mission_id
 *   null AND not represented by any listed mission node).
 * - ``recent`` = terminal mission nodes (liveness in completed/failed/
 *   cancelled) with their attached terminal jobs.
 * - ``recentFlat`` = terminal jobs that map to no listed mission —
 *   render as today's flat rows. The visible band is capped at
 *   ``MAX_RECENT_JOBS`` total rows (mission node headers + flat
 *   rows); anything that doesn't fit OVERFLOWS into ``recentFlat``
 *   after the cap so every job still surfaces (C4 fix — a big node
 *   must never empty the Recent section nor hide jobs).
 * - NEVER hide a job: every input job ends up in exactly one output
 *   bucket. Unattached → fallback (queued for non-terminal, recentFlat
 *   for terminal).
 *
 * Pure, no Angular deps, plain TS — exercised by ``job.model.spec.ts``.
 */
export function buildQueueTree(
  activeJobs: ReadonlyArray<Job>,
  recentJobs: ReadonlyArray<Job>,
  missions: ReadonlyArray<MissionSummary>
): QueueTree {
  // 1) Filter missions into live vs terminal buckets, sorted deterministically.
  const liveMissionsList = missions
    .filter((m) => m.liveness === 'processing' || m.liveness === 'pending' || m.liveness === 'paused')
    .slice()
    .sort((a, b) => {
      const at = a.last_activity_at ?? '';
      const bt = b.last_activity_at ?? '';
      if (at !== bt) return bt.localeCompare(at); // desc, nulls last (empty < non-empty in string order)
      const aid = a.mission_id ?? '';
      const bid = b.mission_id ?? '';
      return aid.localeCompare(bid);
    });
  const terminalMissionsList = missions
    .filter((m) => m.liveness === 'completed' || m.liveness === 'failed' || m.liveness === 'cancelled')
    .slice()
    .sort((a, b) => {
      const at = a.last_activity_at ?? '';
      const bt = b.last_activity_at ?? '';
      if (at !== bt) return bt.localeCompare(at);
      const aid = a.mission_id ?? '';
      const bid = b.mission_id ?? '';
      return aid.localeCompare(bid);
    });

  // 3) Attach jobs to their live mission OR queue as unattached.
  const liveNodes = new Map<string, MissionNode>();
  for (const m of liveMissionsList) {
    if (m.mission_id) liveNodes.set(m.mission_id, { mission: m, jobs: [] });
  }
  const queued: Job[] = [];
  for (const job of activeJobs) {
    const mid = job.mission_id ?? null;
    const node = mid ? liveNodes.get(mid) : undefined;
    if (node) {
      node.jobs.push(job);
    } else {
      // Unattached non-terminal job (no mission_id OR mission_id not
      // represented in the missions list) → queued bucket. NEVER hide.
      queued.push(job);
    }
  }

  // 4) Attach terminal jobs to terminal mission nodes OR fall back to flat.
  const terminalNodes = new Map<string, MissionNode>();
  for (const m of terminalMissionsList) {
    if (m.mission_id) terminalNodes.set(m.mission_id, { mission: m, jobs: [] });
  }
  const recentFlat: Job[] = [];
  for (const job of recentJobs) {
    const mid = job.mission_id ?? null;
    const node = mid ? terminalNodes.get(mid) : undefined;
    if (node) {
      node.jobs.push(job);
    } else {
      recentFlat.push(job);
    }
  }

  // 5) Cap Recent: mission nodes (each counts as 1 row + its child jobs)
  //    PLUS recentFlat rows = MAX_RECENT_JOBS total rows. A large
  //    mission node MUST NOT empty the Recent section nor hide jobs —
  //    when the node's children + header would overflow the cap, we
  //    render what fits and overflow the remainder to ``recentFlat``
  //    so every job still surfaces (the NEVER-hide invariant holds).
  const finalRecentNodes: MissionNode[] = [];
  const overflowRecentFlat: Job[] = [];
  let rowCount = 0;
  for (const node of terminalNodes.values()) {
    if (rowCount >= MAX_RECENT_JOBS) {
      // Cap reached — spill every remaining job (including from this
      // node's children) into the overflow bucket so the user still
      // sees them after the visible band.
      overflowRecentFlat.push(...node.jobs);
      continue;
    }
    // Each mission node reserves 1 row (the header) + its child jobs
    // as rows. A mission with zero children still costs 1 row — the
    // header — and is included as long as that 1 row fits.
    const children = node.jobs;
    const headerCost = 1;
    const capacityForChildren = Math.max(0, MAX_RECENT_JOBS - rowCount - headerCost);
    const fitCount = Math.min(children.length, capacityForChildren);
    const fitChildren = children.slice(0, fitCount);
    const overflowChildren = children.slice(fitCount);
    finalRecentNodes.push({ mission: node.mission, jobs: fitChildren });
    rowCount += headerCost + fitCount;
    // Spill the remainder into the flat bucket so the user still sees
    // every job — they render as detached Recent rows in the panel
    // rather than vanishing under a strict MAX cap.
    overflowRecentFlat.push(...overflowChildren);
  }

  // 6) Fill remaining capacity from the orphan recentFlat list, then
  //    drain anything that didn't fit into the overflow bucket. The
  //    cap is enforced row-by-row; anything that doesn't fit here
  //    lives in ``overflowRecentFlat`` and is appended AFTER the
  //    capped rows below.
  const finalRecentFlat: Job[] = [];
  for (const job of recentFlat) {
    if (rowCount >= MAX_RECENT_JOBS) {
      overflowRecentFlat.push(job);
      continue;
    }
    finalRecentFlat.push(job);
    rowCount += 1;
  }
  // Anything that overflowed the cap (children + late flat) is
  // appended after the capped rows so the user sees them all. The
  // cap is now a "what renders in the visible band" hint, not a
  // hard hid-everything-else gate.
  finalRecentFlat.push(...overflowRecentFlat);

  return {
    liveMissions: Array.from(liveNodes.values()),
    queued,
    recent: finalRecentNodes,
    recentFlat: finalRecentFlat,
  };
}

/**
 * Auto-expand decision for the LIVE MISSIONS section: true iff exactly
 * ONE live mission is present (single-live focus). Zero missions → no
 * expand; 2+ → leave all collapsed so the user picks. Pure, no Angular deps.
 */
export function shouldAutoExpand(liveMissions: ReadonlyArray<MissionNode>): boolean {
  return liveMissions.length === 1;
}

// ── Tree-keyboard navigation (T3, 2026-09-07, mission-tree final gaps) ──

/**
 * A visible item in the panel's keyboard-navigable trees (LIVE
 * MISSIONS + RECENT). Each item carries the discriminated ``kind``
 * so the arrow-key handler can decide whether to expand/collapse,
 * toggle the mission row, or navigate to a job.
 *
 * Items are FLATTENED in display order: a mission node appears at
 * its slot; when the node is expanded, its child jobs follow the
 * node in the array (one slot per child). Collapsed nodes carry no
 * children — those slots are omitted entirely so arrow-up/down skips
 * over them as the brief asks.
 *
 * Pure, no Angular deps — specable without DOM.
 */
export type VisibleTreeItem =
  | { kind: 'mission'; tree: 'live' | 'recent'; node: MissionNode }
  | { kind: 'job'; tree: 'live' | 'recent'; mission: MissionSummary; job: Job };

/**
 * Flatten the LIVE MISSIONS + RECENT trees into the ordered list of
 * items a keyboard user can land on. Order matches the rendered DOM:
 *   1. Live mission nodes (in tree order); each expanded node's
 *      children follow the parent (one slot per child).
 *   2. Recent mission nodes (in tree order); same flatten rule.
 *
 * The QUEUED section is NOT in scope — it lives outside any
 * ``role="tree"`` and its rows are reached via Tab, not arrows.
 *
 * ``expandedLiveIds`` / ``expandedRecentIds`` are the current per-tree
 * expansion sets so the function can decide whether to include a
 * node's children. ``undefined`` for either set is treated as
 * "nothing expanded" — the safe default before the user has touched
 * anything.
 */
export function visibleTreeItems(
  tree: Pick<QueueTree, 'liveMissions' | 'recent'>,
  expandedLiveIds: ReadonlySet<string> | undefined,
  expandedRecentIds: ReadonlySet<string> | undefined
): VisibleTreeItem[] {
  const items: VisibleTreeItem[] = [];
  for (const node of tree.liveMissions) {
    items.push({ kind: 'mission', tree: 'live', node });
    const id = node.mission.mission_id;
    if (id && expandedLiveIds?.has(id)) {
      for (const job of node.jobs) {
        items.push({ kind: 'job', tree: 'live', mission: node.mission, job });
      }
    }
  }
  for (const node of tree.recent) {
    items.push({ kind: 'mission', tree: 'recent', node });
    const id = node.mission.mission_id;
    if (id && expandedRecentIds?.has(id)) {
      for (const job of node.jobs) {
        items.push({ kind: 'job', tree: 'recent', mission: node.mission, job });
      }
    }
  }
  return items;
}

/**
 * Move from ``currentIndex`` by ``+1`` (down) or ``-1`` (up) in the
 * given ``items`` list. CLAMPS at the ends — the first item stays at
 * index 0 when ↑ is pressed from the top; the last item stays at the
 * tail when ↓ is pressed from the bottom. We chose clamp over wrap
 * because wrap is jarring in a tree (jumping from the last RECENT
 * row back to the first LIVE mission reads as a glitch, not a
 * navigation). Pure.
 *
 * ``currentIndex === -1`` (no current focus) treats ↑ as "go to last"
 * and ↓ as "go to first" — the natural first-focus behaviour when
 * the user opens the menu and presses an arrow before any row is
 * tab-focused.
 */
export function nextVisibleItem(
  items: ReadonlyArray<VisibleTreeItem>,
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
 * Stable string id for a VisibleTreeItem — used by the panel as the
 * ``focusedItemId`` signal so the template can apply the ``.focused``
 * class on exactly one row at a time.
 *
 *   - mission:    "tree:live|mission:m-1" / "tree:recent|mission:m-7"
 *   - job child:  "tree:live|mission:m-1|job:job-abc"
 *
 * Including the tree prefix keeps the LIVE and RECENT trees
 * addressable independently — the two sections never bleed focus.
 */
export function visibleTreeItemId(item: VisibleTreeItem): string {
  if (item.kind === 'mission') {
    const mid = item.node.mission.mission_id ?? '';
    return `tree:${item.tree}|mission:${mid}`;
  }
  const mid = item.mission.mission_id ?? '';
  return `tree:${item.tree}|mission:${mid}|job:${item.job.job_id}`;
}
