// Work Models — Virtual Job Management Surface (Phase 4)
//
// The Work type is the unified view-model that collapses three distinct
// backend concepts onto one shape:
//
// * ``job`` — queued work backed by the ``job_queue_items`` table
//   (the "real queue" surface — has a ``queue_id``, lives in the queue
//   sidebar, shows a queue badge in the card).
// * ``turn`` — a Task row whose lifecycle is tied to one instance turn
//   (worker-pool backed, lives in ``task`` table, no queue badge).
// * ``report`` — a Task row whose payload is a child process report
//   flowing up to the parent (worker-pool backed, no queue badge).
//
// Both turn and report kinds are surfaced ONLY via the kind chip —
// never via a queue badge — so the UI does not lie about which backing
// table the row came from. This is the guardrail that keeps the queue
// metaphor honest: real queues stay real, task rows stay tasks.

/**
 * The kind of work record.
 *
 * * ``'job'``    — real queued work (queue badge shown).
 * * ``'turn'``   — instance turn task (no queue badge, kind chip only).
 * * ``'report'`` — child process report (no queue badge, kind chip only).
 */
export type WorkKind = 'job' | 'turn' | 'report';

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
    case 'turn':
      return '#22C55E'; // green-500
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
    case 'turn':
      return 'Turn';
    case 'report':
      return 'Report';
    default:
      return 'Unknown';
  }
}

/**
 * True if the kind is a task-backed work record (turn or report).
 *
 * Task-backed records do NOT show a queue badge — they are surfaced
 * only via the kind chip. This is the guardrail the Phase 4 spec
 * calls out.
 */
export function isTaskBackedKind(kind: WorkKind | undefined | null): boolean {
  return kind === 'turn' || kind === 'report';
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
    case 'turn':
      return 'forum';
    case 'report':
      return 'description';
    default:
      return 'help_outline';
  }
}
