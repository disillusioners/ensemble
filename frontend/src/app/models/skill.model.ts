// Skill Models — Skills management page (Phase 5).
//
// Shared type definitions for the Skills surface. Keeps the model layer
// free of Angular / HTTP concerns so the Skills components, the Search
// panel, and the Detail / Lineage views can all depend on the same
// shapes without circular imports on the service.
//
// The shape mirrors the backend ``SkillRecord`` dataclass (see
// daemon/services/skill_service.py) with three frontend-only additions:
//
// * ``SkillDetail`` adds the Markdown ``content`` body, a ``lineage``
//   projection, and a ``metrics`` bundle — these are only loaded for
//   the detail view to keep the list endpoint fast.
// * ``SkillMetrics`` is the pre-computed analytics bundle the backend
//   returns for ``GET /api/skills/{id}/metrics``.
// * ``SkillLineage`` is the skinny lineage view returned by
//   ``GET /api/skills/{id}/lineage`` (the full ``SkillDetail.lineage``
//   embeds the same shape but also re-exposes ``generation`` /
//   ``origin`` for convenience).
//
// Timestamps are always ISO-8601 strings — the frontend never parses
// datetimes into ``Date`` in the model layer (see work.model.ts for
// the same convention).
//
// All interfaces use snake_case to match the backend verbatim — no
// HTTP interceptor transforms field names (verified against
// ``app.config.ts`` which calls ``provideHttpClient()`` with no
// arguments). See planning doc [S1].
//
// Phase 2 (skill-evolution-ui): interfaces were tightened to match
// the verified backend payload shapes from
// ``daemon/routers/skills.py`` and
// ``daemon/services/skill_metrics_service.py``. The most important
// changes:
//
// * ``SkillMetrics`` field names fixed — backend returns
//   ``selected`` / ``applied`` / ``completions`` / ``fallbacks``,
//   NOT ``total_selections`` / ``total_applied`` / etc. The old
//   names are GONE. Template bindings that referenced them are
//   updated in the same change set (see skill-detail.component.html).
// * New ``total`` / ``avg_iterations`` / ``avg_duration`` fields added
//   so the FE can render the per-skill averages without a second
//   call.
// * ``SkillLineage.parents`` / ``children`` upgraded to
//   ``SkillLineageNode[]`` so consumers can read edge metadata
//   (``change_summary`` / ``content_diff`` / ``edge_created_at``)
//   directly off the node, not a sibling side-channel.
// * New interfaces for usage records, A/B test stats, and trigger
//   CRUD — each is wired to a single backend endpoint.

// ── Core record ──────────────────────────────────────────────────────────

/**
 * Core skill record returned by ``GET /api/skills``.
 *
 * The list page card and the table cells consume exactly this shape —
 * the detail page extends it with ``SkillDetail`` below. All counters
 * are integers maintained by the backend (the Metrics endpoint exposes
 * derived ratios on top).
 *
 * Field additions vs the previous version (verified against
 * ``daemon/repositories/skill/models.py:Skill.to_dict``):
 *
 * * ``auto_load`` — clone-side counterpart of the skill_bank template
 *   flag. ``true`` means the skill is loaded into the system prompt
 *   before every task.
 * * ``source_skill_bank_id`` — the bank template this row was cloned
 *   from (``null`` for manually-created or evolved skills).
 */
export interface Skill {
  id: string;
  project_id: string | null;
  name: string;
  description: string;
  category: string;
  is_active: boolean;
  status: string;
  lineage_origin: string;
  generation: number;
  ab_test_group: string | null;
  auto_load: boolean;
  source_skill_bank_id: string | null;
  total_selections: number;
  total_applied: number;
  total_completions: number;
  total_fallbacks: number;
  consecutive_failures: number;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
}

// ── Detail / extended shapes ─────────────────────────────────────────────

/**
 * Detail view shape returned by ``GET /api/skills/{id}``.
 *
 * Adds the Markdown ``content`` body, a pre-computed ``metrics``
 * bundle, and a nested ``lineage`` projection. The detail endpoint
 * populates ``lineage`` by calling
 * :meth:`SkillStoreService.view_skill`, which embeds the raw
 * :meth:`SkillLineage.to_dict` rows (parent → child edges) — NOT
 * enriched lineage nodes with Skill fields. For the enriched
 * lineage view (with ``name`` / ``generation`` / ``category`` …)
 * call ``GET /api/skills/{id}/lineage`` and consume the standalone
 * :class:`SkillLineage` shape instead.
 */
export interface SkillDetail extends Skill {
  content: string;
  lineage: { parents: SkillLineageEdge[]; children: SkillLineageEdge[] };
  metrics: SkillMetrics;
}

/**
 * Metrics bundle returned by ``GET /api/skills/{id}/metrics``.
 *
 * **Breaking change (C2) — field renames**: the backend returns
 * ``selected`` / ``applied`` / ``completions`` / ``fallbacks`` (see
 * ``daemon/services/skill_metrics_service.py:get_skill_stats``),
 * NOT ``total_selections`` / ``total_applied`` /
 * ``total_completions`` / ``total_fallbacks``. The old names were
 * always ``undefined`` against the live API. Templates are updated
 * alongside this change.
 *
 * Ratios are floats in ``0.0..1.0``; ``formatSuccessRate`` below
 * converts them to the integer-percent strings the UI renders.
 */
export interface SkillMetrics {
  total: number;
  selected: number;
  applied: number;
  completions: number;
  fallbacks: number;
  avg_iterations: number;
  avg_duration: number;
  completion_rate: number; // 0.0–1.0
  applied_rate: number;    // 0.0–1.0
  fallback_rate: number;   // 0.0–1.0
  consecutive_failures: number;
}

// ── Filters / list params ────────────────────────────────────────────────

/**
 * Filter shape accepted by ``SkillService.list``.
 *
 * All fields are optional; ``null`` / empty values are stripped before
 * the request so the backend does not see ``?project_id=``.
 *
 * ``is_active`` is always serialised as ``true`` or ``false`` (never
 * as a bare token) so the FastAPI ``bool`` coercion does not have to
 * guess — same pattern as ``WorkService``'s ``root_only``.
 */
export interface SkillFilters {
  category?: string;
  project_id?: string;
  is_active?: boolean;
  search?: string;
}

// ── Search response ──────────────────────────────────────────────────────

/**
 * Two-bucket search response returned by ``POST /api/skills/search``.
 *
 * * ``injected`` — skills the LLM was successfully asked about.
 * * ``low_match`` — skills retrieved from the index but ranked below
 *   the relevance threshold for the query.
 *
 * Each item in either bucket is a ``{skill, score}`` pair so the
 * Skills page can render the matched skill with its relevance score
 * (the ``low_match`` bucket is dimmed on the page).
 */
export interface SearchResultItem {
  skill: Skill;
  score: number;
}

export interface SearchResults {
  injected: SearchResultItem[];
  low_match: SearchResultItem[];
}

// ── Mutation payloads ────────────────────────────────────────────────────

/**
 * Create payload for ``POST /api/skills``.
 *
 * ``project_id`` is optional; ``null`` means "shared/global" — the
 * backend stores it with ``project_id IS NULL``.
 */
export interface SkillCreate {
  name: string;
  description: string;
  content: string;
  category: string;
  project_id?: string | null;
}

/**
 * Update payload for ``PATCH /api/skills/{id}``.
 *
 * Only the fields the caller wants to change need to be present.
 * ``null`` is never sent for optional fields — callers omit the key.
 */
export interface SkillUpdate {
  name?: string;
  description?: string;
  content?: string;
  category?: string;
  is_active?: boolean;
}

// ── Enums ────────────────────────────────────────────────────────────────

/**
 * Skill category — keep in sync with the backend
 * ``SkillCategory`` enum. New categories must be added in three
 * places (this type, ``SKILL_CATEGORIES``, and the helper icons /
 * colors below) for the chips to keep rendering correctly.
 */
export type SkillCategory =
  | 'workflow'
  | 'coding'
  | 'debugging'
  | 'analysis'
  | 'communication'
  | 'review'
  | 'research'
  | 'other';

/**
 * Canonical, ordered list of supported categories. The Skills page
 * category filter and the create / edit form menu both iterate this
 * array so adding a new category only requires editing the helper
 * file once.
 */
export const SKILL_CATEGORIES: SkillCategory[] = [
  'workflow',
  'coding',
  'debugging',
  'analysis',
  'communication',
  'review',
  'research',
  'other',
];

/**
 * Skill lifecycle status.
 *
 * * ``active``       — normal use, eligible for selection.
 * * ``ab_testing``   — participating in an A/B test; the line below
 *                      shows the matched group chip.
 * * ``deactivated``  — present in the project but not selectable; can
 *                      be re-activated by editing ``is_active``.
 * * ``archived``     — read-only; not selectable, not surfaced in
 *                      default lists. Only ``archive``-flagged users
 *                      can view it.
 */
export type SkillStatus = 'active' | 'ab_testing' | 'deactivated' | 'archived';

/**
 * Canonical ordered list of statuses — order matches the chip
 * ordering on the Skills page filter row.
 */
export const SKILL_STATUSES: SkillStatus[] = [
  'active',
  'ab_testing',
  'deactivated',
  'archived',
];

// ── A/B test ─────────────────────────────────────────────────────────────

// ── Feedback / fix requests ──────────────────────────────────────────────

/**
 * Feedback payload for ``POST /api/skills/{id}/feedback``.
 *
 * Body fields:
 *
 * * ``applied`` — ``true`` if the skill was applied, ``false`` if
 *   recorded-but-not-applied, ``undefined`` if unknown.
 * * ``note`` — optional free-form note.
 *
 * ``instance_id`` / ``agent_id`` are intentionally NOT on this body —
 * the backend surfaces them as query parameters (originating context,
 * not caller input) so ``submitFeedback`` passes them separately.
 */
export interface SkillFeedback {
  applied?: boolean;
  note?: string;
}

/**
 * Fix request payload for ``POST /api/skills/{id}/fix``.
 *
 * ``issue_description`` is required; ``suggested_fix`` is an optional
 * proposed change the skill-keeper agent can splice into its rewrite
 * direction. The backend returns ``202 Accepted`` with ``{job_id}``
 * so the caller can poll the job queue for completion.
 */
export interface SkillFixRequest {
  issue_description: string;
  suggested_fix?: string;
}

export interface SkillFixResponse {
  job_id: string;
}

// ── Lineage ──────────────────────────────────────────────────────────────

/**
 * Raw lineage-edge row — a single ``(skill_id, parent_skill_id)``
 * tuple plus the change metadata recorded when the edge was
 * inserted.
 *
 * This is the shape produced by
 * ``daemon/repositories/skill/models.py:SkillLineage.to_dict`` and
 * embedded (without enrichment) into ``SkillDetail.lineage`` by
 * ``GET /api/skills/{id}`` via
 * ``SkillStoreService.view_skill``. Notably this row does NOT
 * carry Skill fields (no ``name``, ``generation``, ``category``,
 * …) — only edge metadata. For an enriched view that merges in
 * Skill fields use :class:`SkillLineageNode` from the standalone
 * ``GET /api/skills/{id}/lineage`` endpoint.
 *
 * Field set verified against
 * ``daemon/repositories/skill/models.py:SkillLineage.to_dict``
 * (5 fields). ``change_summary`` / ``content_diff`` default to
 * the empty string when the edge row was inserted without
 * metadata.
 *
 * The ``skill_id`` field is the descendant (child) and
 * ``parent_skill_id`` is the ancestor. For a given skill, the
 * same descendant id appears on every row in both ``parents``
 * and ``children`` — the relationship direction is what differs.
 */
export interface SkillLineageEdge {
  skill_id: string;
  parent_skill_id: string;
  change_summary: string;
  content_diff: string;
  created_at: string;
}

/**
 * A lineage node — a ``Skill`` plus edge metadata.
 *
 * Returned only by the standalone ``GET /api/skills/{id}/lineage``
 * endpoint. ``SkillDetail.lineage`` (the lineage embedded inside
 * the ``GET /api/skills/{id}`` detail payload) carries the
 * narrower :class:`SkillLineageEdge` shape instead — raw edge
 * rows without Skill fields. Always consume ``SkillLineageNode``
 * via the dedicated lineage endpoint, not via the detail
 * payload. The backend merges the linked ``Skill.to_dict()``
 * payload with the edge's ``change_summary`` / ``content_diff``
 * / ``created_at`` so the FE can render a sibling tile without
 * a per-row detail fetch.
 *
 * * ``change_summary`` — one-line description of what changed.
 * * ``content_diff`` — unified diff of the content body (text).
 * * ``edge_created_at`` — ISO timestamp of when the lineage edge
 *   was created (distinct from the skill's own ``created_at`` —
 *   a skill can be much older than the edge that links it into
 *   the DAG). Optional because orphaned-edge fallbacks may
 *   omit it.
 *
 * Defined here as ``extends Skill`` so consumers that only cared
 * about the underlying ``Skill`` fields keep type-checking without
 * changes (the Skill fields are still all present at runtime).
 */
export interface SkillLineageNode extends Skill {
  change_summary: string;
  content_diff: string;
  edge_created_at?: string;
}

/**
 * Skinny lineage shape returned by ``GET /api/skills/{id}/lineage``.
 *
 * Used by the lineage view on the detail page. ``SkillDetail.lineage``
 * mirrors this but excludes ``generation`` / ``origin`` because those
 * are already on the parent record itself.
 *
 * ``parents`` / ``children`` are ``SkillLineageNode[]`` so consumers
 * can read ``change_summary`` / ``content_diff`` off each entry
 * without a second fetch.
 */
export interface SkillLineage {
  skill_id: string;
  parents: SkillLineageNode[];
  children: SkillLineageNode[];
  generation: number;
  origin: string;
}

// ── Usage records ────────────────────────────────────────────────────────

/**
 * Per-event usage record returned by
 * ``GET /api/skills/{id}/usage-records``.
 *
 * Field set verified against
 * ``daemon/repositories/skill/models.py:SkillUsageRecord.to_dict``.
 * ``task_message`` is optional — the agent may not always forward
 * the originating prompt. ``feedback_applied`` is a three-state
 * nullable boolean: ``null`` = no feedback yet, ``true`` =
 * recorded-and-applied, ``false`` = recorded-but-not-applied.
 * ``superseded`` flags rows that should be excluded from
 * standard completion-rate aggregation (worker reuse, hot-swap).
 */
export interface SkillUsageRecord {
  id: string;
  skill_id: string;
  project_id: string | null;
  instance_id: string;
  agent_id: string;
  task_message: string | null;
  selected: boolean;
  applied: boolean;
  task_succeeded: boolean;
  iterations: number;
  duration_seconds: number;
  fallback: boolean;
  feedback_applied: boolean | null;
  feedback_note: string | null;
  ab_test_group: string | null;
  superseded: boolean;
  created_at: string;
}

/**
 * Paginated usage-record response from
 * ``GET /api/skills/{id}/usage-records?limit=&offset=``.
 *
 * The backend returns ``{skill_id, records, total, limit, offset}``
 * (see ``daemon/routers/skills.py:get_usage_records``). ``total``
 * is the unfiltered row count so callers can render pagination
 * without a second ``COUNT(*)`` call.
 */
export interface SkillUsageRecordsResponse {
  skill_id: string;
  records: SkillUsageRecord[];
  total: number;
  limit: number;
  offset: number;
}

// ── A/B comparison stats ─────────────────────────────────────────────────

/**
 * A/B comparison stats returned by
 * ``GET /api/skills/{id}/ab-test/stats``.
 *
 * Field set verified against
 * ``daemon/services/skill_metrics_service.py:get_ab_comparison_stats``.
 *
 * **Field naming**: the on-the-wire skill ids are ``skill_id_a`` /
 * ``skill_id_b``. The backend maps them from the test row's
 * ``skill_id_old`` (incumbent) → ``skill_id_a`` and
 * ``skill_id_new`` (candidate) → ``skill_id_b``. The a/b suffix is
 * kept on the wire so the FE does not have to guess which side is
 * the old variant.
 *
 * ``difference`` is the absolute composite-score difference
 * (``abs(composite_score_a - composite_score_b)``). ``sample_size``
 * is the configured threshold (NOT a hardcoded constant) — the
 * actual comparisons-so-far counter is ``comparisons``.
 *
 * Per-variant rates (``*_a`` / ``*_b``) cover completion, applied,
 * fallback, average iterations and average duration; the composite
 * score blends five signals via configurable weights (see
 * ``SkillEvolutionConfig``).
 */
export interface SkillAbTestStats {
  skill_id_a: string | null;
  skill_id_b: string | null;
  completion_rate_a: number;
  completion_rate_b: number;
  applied_rate_a: number;
  applied_rate_b: number;
  fallback_rate_a: number;
  fallback_rate_b: number;
  avg_iterations_a: number;
  avg_iterations_b: number;
  avg_duration_a: number;
  avg_duration_b: number;
  composite_score_a: number;
  composite_score_b: number;
  difference: number;
  comparisons: number;
  extension_count: number;
  sample_size: number;
  ready_to_resolve: boolean;
  needs_more_data: boolean;
}

/**
 * A/B stats response envelope from
 * ``GET /api/skills/{id}/ab-test/stats``.
 *
 * ``ab_test_group`` is ``null`` when the skill is not enrolled in
 * a test; ``stats`` is ``null`` in that same case (rather than
 * returning a zero-stats dict) so the FE can distinguish "no test"
 * from "test exists but stats unavailable".
 */
export interface SkillAbTestStatsResponse {
  skill_id: string;
  ab_test_group: string | null;
  stats: SkillAbTestStats | null;
}

// ── Triggers ─────────────────────────────────────────────────────────────

/**
 * Skill trigger — declarative condition → action rule.
 *
 * Returned by ``GET /api/skills/triggers`` and the corresponding
 * create / update / delete endpoints.
 *
 * **Field naming**: the backend uses ``condition_type`` and
 * ``condition_json`` (see
 * ``daemon/repositories/skill/models.py:SkillTrigger``). The older
 * ``trigger_type`` / ``trigger_config`` pair is NOT used anywhere on
 * the wire — do not introduce those names here.
 *
 * ``project_id`` is nullable: ``null`` = global trigger (applies
 * to every project). ``condition_json`` is a free-form ``Record``
 * whose shape depends on ``condition_type`` (e.g.
 * ``{keyword: 'deploy'}``, ``{regex: '^run\\s+tests?$'}``, …).
 */
export interface SkillTrigger {
  id: string;
  project_id: string | null;
  name: string;
  condition_type: string;
  condition_json: Record<string, unknown>;
  action: string;
  is_enabled: boolean;
  created_at: string;
}

/**
 * Create payload for ``POST /api/skills/triggers``.
 *
 * ``project_id`` is optional; ``null`` / omitted creates a global
 * trigger. ``condition_json`` is a free-form dict (see
 * ``SkillTrigger.condition_json``).
 */
export interface SkillTriggerCreate {
  name: string;
  condition_type: string;
  condition_json: Record<string, unknown>;
  action: string;
  is_enabled?: boolean;
  project_id?: string | null;
}

/**
 * Update payload for ``PUT /api/skills/triggers/{id}``.
 *
 * All fields optional — partial updates are sent with omitted keys
 * rather than ``null`` tokens (the backend uses
 * ``exclude_none=True`` and treats ``null`` as a real clear).
 */
export interface SkillTriggerUpdate {
  name?: string;
  condition_type?: string;
  condition_json?: Record<string, unknown>;
  action?: string;
  is_enabled?: boolean;
  project_id?: string | null;
}

// ── Helper functions ─────────────────────────────────────────────────────

/**
 * Canonical chip colour for a Skill status. Colours deliberately
 * mirror the rest of the app palette so users can tell at a glance
 * whether they are looking at a Skill status chip or a Job status
 * chip.
 *
 * Defaults to a neutral gray for unknown statuses so an unexpected
 * server-side value still renders something readable.
 */
export function getStatusColor(status: SkillStatus | string): string {
  switch (status) {
    case 'active':       return '#10b981'; // green
    case 'ab_testing':   return '#3b82f6'; // blue
    case 'deactivated':  return '#6b7280'; // gray
    case 'archived':     return '#9ca3af'; // lighter gray
    default:             return '#9ca3af';
  }
}

/**
 * Display label for a Skill status chip.
 *
 * ``ab_testing`` is rendered as ``"A/B Testing"`` (with the slash)
 * so the chip stays wider than ``AbTesting`` would otherwise suggest.
 */
export function getStatusLabel(status: SkillStatus | string): string {
  switch (status) {
    case 'active':      return 'Active';
    case 'ab_testing':  return 'A/B Testing';
    case 'deactivated': return 'Deactivated';
    case 'archived':    return 'Archived';
    default:            return status;
  }
}

/**
 * Material icon for a Skill status chip. Pair with ``getStatusColor``
 * to keep the chip consistent.
 */
export function getStatusIcon(status: SkillStatus | string): string {
  switch (status) {
    case 'active':       return 'check_circle';
    case 'ab_testing':   return 'science';
    case 'deactivated':  return 'block';
    case 'archived':     return 'inventory_2';
    default:             return 'help';
  }
}

/**
 * Material icon for a Skill category. Defaults to ``category`` for
 * unknown categories so an unexpected backend value still renders.
 */
export function getCategoryIcon(category: string): string {
  switch (category) {
    case 'workflow':      return 'account_tree';
    case 'coding':        return 'code';
    case 'debugging':     return 'bug_report';
    case 'analysis':      return 'analytics';
    case 'communication': return 'forum';
    case 'review':        return 'rate_review';
    case 'research':      return 'search';
    case 'other':         return 'category';
    default:              return 'category';
  }
}

/**
 * Category palette. Picks diverge from the job-status palette so the
 * category chip and the status chip do not collide visually.
 */
export function getCategoryColor(category: string): string {
  switch (category) {
    case 'workflow':      return '#8b5cf6'; // purple
    case 'coding':        return '#3b82f6'; // blue
    case 'debugging':     return '#ef4444'; // red
    case 'analysis':      return '#10b981'; // green
    case 'communication': return '#ec4899'; // pink
    case 'review':        return '#f59e0b'; // amber
    case 'research':      return '#06b6d4'; // cyan
    case 'other':         return '#6b7280'; // gray
    default:              return '#6b7280';
  }
}

/**
 * Bucket-aware colour for a success rate. Cutoffs are:
 *
 * * ``>= 0.6`` — green (healthy).
 * * ``>= 0.3`` — amber (warning).
 * * ``<  0.3`` — red (poor).
 *
 * The thresholds match the same buckets the backend uses for the
 * "needs attention" pill so the chip colour lines up with the alert.
 */
export function getSuccessRateColor(rate: number): string {
  if (rate >= 0.6) return '#10b981'; // green
  if (rate >= 0.3) return '#f59e0b'; // amber
  return '#ef4444';                  // red
}

/**
 * Format a 0.0–1.0 success rate as an integer percent string
 * (``0.42 -> "42%"``). Always rounds to the nearest whole percent so
 * the chip does not flicker between ``"42%"`` and ``"43%"`` on
 * tiny backend updates.
 */
export function formatSuccessRate(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}