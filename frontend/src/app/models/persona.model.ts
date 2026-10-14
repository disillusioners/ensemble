// Persona (Prompt Version) Models — agent-prompt-immutability Phase C P1.
//
// Wire shapes mirror the canonical 15-endpoint family under
// ``/api/prompts`` documented in ``daemon/routers/prompts.py``. Keep
// these interfaces aligned with the backend Pydantic response models
// (addendum S4 canonical family + USER DIRECTIVE #2 deltas).
//
// Two artifact classes — ``CORE`` and ``MEMORY`` (Ud#3 RESOLUTION-CORE,
// 2026-10-14) — gate which state transitions are legal and which UI
// affordances render:
//   * CORE rows: full lifecycle (draft → pending_approval → approved/
//     rejected) plus editable in draft/pending + settable selection
//     pointer.
//   * MEMORY rows: born-effective (state="approved"), no
//     pending_approval entry, never editable, never deletable via
//     state-machine path.
//
// The badge in the header pulls ``pending-count`` which is CORE-only
// (the badge excludes MEMORY rows by repo design).

/** Allowed ``artifact_class`` enum — Ud#3. */
export type ArtifactClass = 'CORE' | 'MEMORY';

/** Allowed state values (mirror repo ``valid_states`` set). */
export type VersionState =
  | 'draft'
  | 'pending_approval'
  | 'approved'
  | 'rejected'
  | 'archived';

/** A single version row returned by ``GET /api/prompts/versions``. */
export interface PromptVersion {
  version_id: string;
  agent_id: string;
  version_tag: string;
  artifact: string;
  /** Ud#3 — CORE | MEMORY (legacy rows fall back to CORE via repo). */
  artifact_class: ArtifactClass;
  /** Monotonic version number per (agent, tag, artifact) key. */
  version: number;
  state: VersionState;
  content: string;
  content_hash: string;
  /** 1 when this row IS the per-(agent, tag, artifact) selection pointer. */
  is_selected: 0 | 1;
  submitter_instance_id: string | null;
  submitter_agent_id: string | null;
  submitter_version_tag: string;
  approver_subject: string | null;
  approver_auth_method: string | null;
  /** ISO-8601 timestamps (text columns per migration design). */
  created_at: string;
  updated_at: string;
}

/** List response envelope from ``GET /api/prompts/versions``. */
export interface PromptVersionListResponse {
  items: PromptVersion[];
  total: number;
}

/** Filters for ``GET /api/prompts/versions``. All optional + AND-combined. */
export interface PromptVersionFilters {
  agent_id?: string;
  version_tag?: string;
  artifact?: string;
  state?: VersionState;
  artifact_class?: ArtifactClass;
  limit?: number;
  offset?: number;
}

/** Create-draft body for ``POST /api/prompts/versions``. */
export interface PromptVersionCreate {
  agent_id: string;
  version_tag?: string;
  artifact: string;
  content: string;
}

/** PATCH body for ``PATCH /api/prompts/versions/{id}`` — UD#2 endpoint 13. */
export interface PromptVersionUpdate {
  content: string;
}

/** DELETE response for ``DELETE /api/prompts/versions/{id}`` — UD#2 endpoint 14. */
export interface PromptVersionDeleteResponse {
  version_id: string;
  deleted: boolean;
}

/** Diff response from ``GET /api/prompts/versions/{id}/diff?against=…``. */
export interface PromptVersionDiffResponse {
  version_id: string;
  against: string;
  against_version_id: string | null;
  /** Unified diff text per ``difflib.unified_diff``. */
  diff: string;
}

/** Allowed ``against`` values for the diff endpoint. */
export type DiffAgainst = 'effective' | 'approved' | string;

/** Effective-content response — `GET /effective/{agent}/{tag}/{artifact}`. */
export interface EffectiveContentResponse {
  agent_id: string;
  version_tag: string;
  artifact: string;
  /** ``null`` means the template file is the baseline (loader fallback). */
  content: string | null;
}

/** Selection response (set + clear). */
export interface PromptSelectionResponse {
  agent_id: string;
  version_tag: string;
  artifact: string;
  version_id: string | null;
}

/** Selection-set body for ``POST /api/prompts/selections``. */
export interface PromptSelectionCreate {
  agent_id: string;
  version_tag?: string;
  artifact: string;
  version_id: string;
}

/** Audit-log event shape — `GET /api/prompts/audit`. */
export interface PromptAuditEvent {
  event_id: string;
  agent_id: string;
  version_tag: string;
  artifact: string;
  version_id: string | null;
  event_type: string;
  actor_subject: string | null;
  actor_auth_method: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

/** Audit list envelope. */
export interface PromptAuditListResponse {
  items: PromptAuditEvent[];
  total: number;
}

/** Pending-count envelope — `GET /api/prompts/pending-count`. CORE-only by design. */
export interface PendingCountResponse {
  count: number;
}

/** Error envelope shape from `daemon/middleware/auth.py:78-82` + mcp_kb_keys.py. */
export interface PromptApiError {
  error: string;
  message: string;
}

/** Human-friendly typed mapping for known error codes (Persona UI banner). */
export const PROMPT_ERROR_MESSAGES: Record<string, string> = {
  HUMAN_LANE_REQUIRED:
    'Approve/reject requires a signed-in session (session lane). Bearer tokens cannot approve.',
  VERSION_IMMUTABLE:
    'This version is immutable in its current state — only draft or pending rows are editable.',
  MEMORY_CLASS_MISMATCH:
    'MEMORY-class rows are born-effective and have no approval/edit/draft path.',
  ALREADY_APPROVED: 'Version is already approved.',
  ALREADY_REJECTED: 'Version is already rejected.',
  INVALID_TRANSITION: 'State transition is not allowed from the current state.',
  RETRACT_FORBIDDEN: 'Only the submitter or a session user may retract this row.',
  VERSION_NOT_FOUND: 'No version matches that id.',
  AUTH_NOT_CONFIGURED:
    'Auth is not configured on the daemon. Reads/writes fail closed until auth is enabled.',
  PROMPTS_UNAVAILABLE: 'Prompt-version service is unavailable on this daemon.',
  INVALID_PATH_PARAM: 'A path parameter did not match the expected character class.',
  INVALID_FILTER_PARAM: 'A filter parameter did not match the expected character class.',
};

/**
 * Resolve the human-friendly message for a known error code.
 * Falls back to the wire ``message`` field for unknown codes.
 */
export function promptErrorMessage(err: PromptApiError): string {
  return PROMPT_ERROR_MESSAGES[err.error] ?? err.message ?? 'Unknown error';
}

/** Convenience predicate: a row is CORE-class (state-machine governed). */
export function isCoreRow(v: PromptVersion): boolean {
  return v.artifact_class === 'CORE';
}

/** Convenience predicate: a row is editable (draft or pending_approval). */
export function isEditableRow(v: PromptVersion): boolean {
  return v.state === 'draft' || v.state === 'pending_approval';
}

/** Convenience predicate: a row is awaiting human approval. */
export function isPendingApproval(v: PromptVersion): boolean {
  return v.state === 'pending_approval';
}