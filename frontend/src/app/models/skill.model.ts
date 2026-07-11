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

// ── Core record ──────────────────────────────────────────────────────────

/**
 * Core skill record returned by ``GET /api/skills``.
 *
 * The list page card and the table cells consume exactly this shape —
 * the detail page extends it with ``SkillDetail`` below. All counters
 * are integers maintained by the backend (the Metrics endpoint exposes
 * derived ratios on top).
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
 * bundle, and a nested ``lineage`` projection that already resolves
 * the immediate parents and children into full ``Skill`` records so
 * the detail page does not have to round-trip twice.
 */
export interface SkillDetail extends Skill {
  content: string;
  lineage: { parents: Skill[]; children: Skill[] };
  metrics: SkillMetrics;
}

/**
 * Metrics bundle returned by ``GET /api/skills/{id}/metrics``.
 *
 * Ratios are floats in ``0.0..1.0``; ``formatSuccessRate`` below
 * converts them to the integer-percent strings the UI renders.
 *
 * Field naming is intentionally the same as the ``Skill`` counters so
 * consumers can spread the bundle onto a row without renaming.
 */
export interface SkillMetrics {
  total_selections: number;
  total_applied: number;
  total_completions: number;
  total_fallbacks: number;
  completion_rate: number; // 0.0–1.0
  fallback_rate: number;   // 0.0–1.0
  applied_rate: number;    // 0.0–1.0
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

/**
 * A/B test status returned by ``GET /api/skills/{id}/ab-test``.
 *
 * The Skills page renders this as a banner above the skill detail
 * when ``group`` is non-null. ``variant_ids`` are sibling skill ids
 * participating in the same test; ``winner_id`` is null until the
 * test is resolved.
 */
export interface AbTestStatus {
  skill_id: string;
  group: string;
  comparison_count: number;
  extension_count: number;
  variant_ids: string[];       // sibling skill ids in the test
  started_at: string;
  resolved_at: string | null;
  winner_id: string | null;
}

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
 * Skinny lineage shape returned by ``GET /api/skills/{id}/lineage``.
 *
 * Used by the lineage view on the detail page. ``SkillDetail.lineage``
 * mirrors this but excludes ``generation`` / ``origin`` because those
 * are already on the parent record itself.
 */
export interface SkillLineage {
  parents: Skill[];
  children: Skill[];
  generation: number;
  origin: string;
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
