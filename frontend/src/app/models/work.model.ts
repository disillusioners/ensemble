// Work Models — Virtual Job Management Surface (Phase 4 partial collapse)
//
// The Work type is the unified view-model that collapses the two remaining
// backend concepts onto one shape:
//
// * ``job`` — queued work backed by the ``job_queue_items`` table
//   (the "real queue" surface — has a ``queue_id``, lives in the queue
//   sidebar, shows a queue badge in the card). Message turns now surface
//   here too (Phase 4 partial collapse: turns are JobItems).
// * ``report`` — a Task row whose payload is a child process report
//   flowing up to the parent (worker-pool backed, no queue badge).
//
// Post-collapse the only Task-side kind is ``report``. Turn Tasks are
// gone — message-driven work is JobItems. Both ``job`` and ``report``
// are surfaced via the kind chip; only ``job`` additionally shows a
// queue badge. The UI does not lie about which backing table the row
// came from.

import { Job, MissionLiveness } from './job.model';

/**
 * The kind of work record.
 *
 * * ``'job'``    — real queued work (queue badge shown). Message
 *   turns are JobItems post-collapse.
 * * ``'report'`` — child process report (no queue badge, kind chip only).
 */
export type WorkKind = 'job' | 'report';

/**
 * Unified work record returned by ``GET /api/work``.
 *
 * Mirrors the backend ``WorkRecord`` dataclass shape with two
 * adjustments:
 *
 * * ``work_id`` is the cross-system UUID4 handle (the same value the
 *   backend SSE endpoint resolves against).
 * * ``created_at`` is an ISO-8601 string (frontend never parses
 *   datetimes into Date in the model layer).
 */
export interface Work {
  work_id: string;
  kind: WorkKind;
  status: string;
  instance_id: string | null;
  project_id: string | null;
  agent_id: string | null;
  result_summary: string | null;
  error: string | null;
  created_at: string;
  /**
   * Fix C read-model split (docs/job-task-system.md §8.2):
   * JobItem-side discriminator — ``'task'`` (mission) or
   * ``'message'`` (mirror/receipt). ``null`` for Task-backed
   * records (e.g. reports), which carry no JobItem.
   */
  job_type?: 'task' | 'message' | null;
  /**
   * Canonical status of the linked instance, populated ONLY for
   * mirror rows. ``null`` means mission row / degraded lookup /
   * no linked instance — indistinguishable by design; render
   * nothing extra rather than inventing a state.
   */
  mission_liveness?: MissionLiveness | null;
  /**
   * P1 row-parity fix (jobs-page-improvement arc) — execution-timing
   * fields the backend ``WorkRecord`` already ships (ISO-8601 strings;
   * ``Instance.last_activity_at`` / ``Instance.updated_at`` proxies or
   * the JobItem mirror columns). The pre-P1 ``workToJob`` mapping
   * hard-nulled both, permanently breaking the Timeline on all-work
   * rows even though the data was on the wire. Optional so legacy
   * fixtures and older wire payloads stay type-valid; absent maps to
   * ``null`` on the Job shape (honest "never started/finished"),
   * never to a fabricated timestamp.
   */
  started_at?: string | null;
  completed_at?: string | null;
  /**
   * Mission-projection identity pair (jobs-page-improvement all-work
   * title fix). The backend ``WorkRecord.to_dict()`` ships BOTH keys
   * on every ``GET /api/work`` row (daemon/services/work_resolver.py):
   * ``mission_id`` — the linked mission (== ``instance_id`` per the
   * mission-class spec §3 identity; ``null`` only for queue-stage
   * rows with no instance) — and ``mission_ref`` — the M2
   * cross-reference payload ``{mission_id, agent_id, liveness}``
   * (``null`` on degraded instance lookups). The pre-fix ``workToJob``
   * dropped both, so every all-work group built ``missionId: null``
   * (jobs-grouping.model.ts) and the P3 title-enrichment gate
   * (``missionId != null``, jobs-enrichment.model.ts) skipped it —
   * all-work groups never fetched titles. Optional so legacy fixtures
   * and older wire payloads stay type-valid; absent maps to ``null``
   * on the Job shape (child-bound semantics), never to a fabricated
   * identity. ``outcome`` (ALWAYS ``null`` on the transport surface
   * per the M2 contract §3.2) is deliberately NOT mirrored.
   */
  mission_id?: string | null;
  mission_ref?: { mission_id: string; agent_id: string; liveness: string } | null;
}

/**
 * Filter shape accepted by ``WorkService.getWork``.
 *
 * All fields are optional; ``null``/empty values are stripped before
 * being sent as query params so the backend does not see
 * ``?project_id=``.
 *
 * ``root_only`` (P-A of the Virtual Job Tool Completeness plan):
 * when ``true``, the backend ``GET /api/work`` excludes work whose
 * backing instance is a child of another instance. The Jobs page
 * "All Work" view deliberately passes ``false`` so the user sees
 * every row the resolver can find — the view name is a contract.
 * When ``undefined`` the param is omitted and the backend default
 * (root-scoped) applies; callers that want the default should leave
 * the field unset rather than passing ``true`` explicitly.
 */
export interface WorkFilters {
  status?: string;
  project_id?: string;
  instance_id?: string;
  kind?: string;
  root_only?: boolean;
}

// ── Helper functions ─────────────────────────────────────────────────────

/**
 * Single-row ``Work`` → ``Job`` mapper (pure, exported so specs can
 * drive the REAL construction — it used to live as a private method
 * on ``JobsComponent``, where the all-work pipeline bypassed filters
 * and hard-nulled the timeline fields).
 *
 * Row-parity contract (jobs-page-improvement P1):
 *
 * * ``started_at`` / ``completed_at`` — carried from the wire
 *   (``?? null``), NOT hard-nulled. ``JobCardComponent``'s Timeline
 *   renders on all-work rows exactly as it does on queue rows.
 * * ``mission_id`` / ``mission_ref`` — carried (``?? null``), NOT
 *   dropped. Grouping derives ``missionId`` from ``mission_id`` and
 *   the P3 title-enrichment picker requires a populated
 *   ``missionId``; the pre-fix drop made every all-work group
 *   permanently enrichment-ineligible (no titles, ever).
 * * ``result_summary`` — carried (``message`` stays ``undefined``:
 *   no BE surface carries report message content today — honest gap,
 *   drawer copy is addressed in Phase 5).
 * * ``source`` — stays ``undefined``: the ``/api/work`` wire carries
 *   no source concept (gap-e5). The source FILTER is therefore
 *   queues-view-scoped (control hidden in all-work with honest copy).
 * * ``priority`` — ``0`` placeholder (no BE priority on work rows).
 * * ``queue_id`` — pinned ``null`` for every kind so a stale value
 *   cannot re-enable the queue badge after the kind guardrail runs
 *   in ``JobCardComponent`` (non-job kinds show no queue badge).
 *
 * The mapping is deliberately one-way and lossy in the direction
 * Work→Job (the card template stays type-stable on ``Job``); the
 * ``kind`` field carries the backing-table semantics.
 */
export function workToJob(work: Work): Job {
  return {
    job_id: work.work_id,
    agent_id: work.agent_id ?? '',
    message: undefined,
    source: undefined,
    project_id: work.project_id,
    priority: 0,
    // Defensive fallback covers null/undefined AND empty-string
    // statuses (an empty string would render a blank status chip;
    // the pre-P1 ``??`` alone missed it).
    status: (work.status || 'pending') as Job['status'],
    created_at: work.created_at,
    // P1 row-parity fix — see the docstring: carry the wire timings
    // through instead of nulling them.
    started_at: work.started_at ?? null,
    completed_at: work.completed_at ?? null,
    instance_id: work.instance_id,
    error_message: work.error,
    result_summary: work.result_summary,
    queue_id: null,
    cancelled_at: null,
    kind: work.kind,
    // Fix C read-model split (§8.2) — pass the discriminator +
    // liveness pair through so JobCardComponent can render the
    // receipt chip and the mission-liveness indicator. Task-backed
    // records carry null for both and render nothing extra.
    job_type: (work.job_type ?? null) as Job['job_type'],
    mission_liveness: work.mission_liveness ?? null,
    // All-work title fix — carry the mission-projection identity pair
    // through (see the Work docblock). Grouping derives ``missionId``
    // from it and the title-enrichment gate requires a populated
    // ``missionId``; dropping it here kept every all-work group
    // permanently enrichment-ineligible.
    mission_id: work.mission_id ?? null,
    mission_ref: work.mission_ref ?? null,
  };
}

/**
 * Canonical chip colour for a WorkKind.
 *
 * Colours deliberately diverge from the Job status palette so a user
 * can tell the kind chip and the status chip apart at a glance.
 */
export function getKindColor(kind: WorkKind | undefined | null): string {
  switch (kind) {
    case 'job':
      return '#3B82F6'; // blue-500
    case 'report':
      return '#7C3AED'; // purple-600
    default:
      return '#6B7280'; // gray-500 — unknown / undefined
  }
}

/**
 * Display label for a WorkKind chip.
 *
 * Capitalised as a single word so the chip stays compact.
 */
export function getKindLabel(kind: WorkKind | undefined | null): string {
  switch (kind) {
    case 'job':
      return 'Job';
    case 'report':
      return 'Report';
    default:
      return 'Unknown';
  }
}

/**
 * True if the kind is a task-backed work record (report only — Phase 4
 * partial collapse, 2026-07-06).
 *
 * Task-backed records do NOT show a queue badge — they are surfaced
 * only via the kind chip. Post-collapse the only Task-side kind is
 * ``"report"`` (``"turn"`` is gone — message turns are now JobItems).
 */
export function isTaskBackedKind(kind: WorkKind | undefined | null): boolean {
  return kind === 'report';
}

/**
 * Material icon for a WorkKind chip.
 *
 * Defaults to ``help_outline`` so an unexpected server-side kind value
 * still renders something readable.
 */
export function getKindIcon(kind: WorkKind | undefined | null): string {
  switch (kind) {
    case 'job':
      return 'work_outline';
    case 'report':
      return 'description';
    default:
      return 'help_outline';
  }
}

