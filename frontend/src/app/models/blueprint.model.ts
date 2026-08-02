// Project Blueprint Models — Frontend (Phase 5).
//
// TypeScript interfaces for the Project Blueprint CRUD surface.
// The shape mirrors the REAL backend `daemon/routers/blueprints.py`
// `BlueprintResponse` / `BlueprintRevisionResponse` Pydantic models.
//
// IMPORTANT: this file is the single source of truth for the wire
// shape. If the backend renames or reshapes a field, update it here
// AND in the consuming service/component, not at the call site.
//
// History:
//   - v1: Initial implementation (Phase 5). `tags` is a list of
//     `{category, value}` records (NOT a `Record<string, string>` map),
//     so the category/value pair round-trips losslessly from the
//     backend's JSONB column. `BlueprintListResponse` uses key `items`
//     (NOT `blueprints`).

/** A blueprint's "kind" — semantic role in the project. */
export type BlueprintKind = 'core' | 'area';

/** Lifecycle state of a blueprint. */
export type BlueprintStatus = 'published' | 'draft' | 'review_needed';

/** Provenance of a blueprint (auto-generated vs. hand-authored). */
export type BlueprintSource = 'auto' | 'manual';

/**
 * A single tag — category/value pair stored as a dict on the backend
 * JSONB column. The pair round-trips losslessly so we model it as a
 * record instead of a `Record<string, string>`.
 */
export interface BlueprintTag {
  category: string;
  value: string;
}

/**
 * A project-scoped blueprint document. Mirrors the fields returned by
 * ``Blueprint.to_dict()`` filtered through the Pydantic
 * ``BlueprintResponse`` schema.
 */
export interface Blueprint {
  id: string;
  project_id: string;
  slug: string;
  name: string;
  kind: BlueprintKind;
  content: string;
  status: BlueprintStatus;
  tags: BlueprintTag[];
  file_refs: string[];
  version: number;
  embedding_model: string | null;
  source: BlueprintSource;
  created_at: string;
  updated_at: string;
  last_reviewed_at: string | null;
  is_active: boolean;
}

/**
 * A single revision entry. Mirrors the Pydantic
 * ``BlueprintRevisionResponse`` schema (verified against
 * ``daemon/routers/blueprints.py``) — NOT the full
 * ``BlueprintRevision.to_dict()`` payload. The API response only
 * surfaces the fields below (no `file_refs`, `tags`,
 * `trigger_queries`, `revision_summary`, or `changed_by`).
 *
 * NOTE: the spec described the summary field as `revision_summary`,
 * but the committed Pydantic schema exposes it as `reason`. The
 * Pydantic schema is the source of truth — backend typo aside,
 * the JSON wire field is `reason`. Track the discrepancy in the
 * followups so it can be renamed end-to-end later.
 */
export interface BlueprintRevision {
  id: string;
  blueprint_id: string;
  version: number;
  content_snapshot: string;
  source: BlueprintSource;
  reason: string | null;
  created_at: string;
}

/** List response envelope from `GET /api/projects/{project_id}/blueprints`. */
export interface BlueprintListResponse {
  items: Blueprint[];
  total: number;
}

/** Create payload for `POST /api/projects/{project_id}/blueprints`. */
export interface BlueprintCreateRequest {
  slug: string;
  name: string;
  kind: BlueprintKind;
  content: string;
  tags?: BlueprintTag[];
  file_refs?: string[];
}

/**
 * Update payload for `PUT /api/projects/{project_id}/blueprints/{id}`.
 * All fields optional — only non-null fields are forwarded to the
 * backend (server-side no-op when no fields set → 400).
 */
export interface BlueprintUpdateRequest {
  name?: string;
  content?: string;
  tags?: BlueprintTag[];
  file_refs?: string[];
  status?: BlueprintStatus;
}

/** Query-string filters accepted by ``GET /api/projects/{project_id}/blueprints``. */
export interface BlueprintFilters {
  kind?: BlueprintKind;
  status?: BlueprintStatus;
}
