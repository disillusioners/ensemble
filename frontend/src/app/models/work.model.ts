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
