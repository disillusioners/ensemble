// Skill Bank Models — Skill Bank CRUD page (Phase 3).
//
// User-facing CRUD over `/api/skill-bank/*`. This is isolated from the
// skill evolution system — there is no A/B testing, no metrics, no
// lineage. The shape mirrors the backend `SkillBankItemResponse`.
//
// Phase 2 (skill-evolution-ui): ``SkillBankItem`` was extended with
// ``template_version`` / ``agent_id`` / ``auto_load`` — all three
// fields are returned by ``daemon/repositories/skill/models.py:
// SkillBankItem.to_dict()`` and were previously missing here.
//
// Timestamps are ISO-8601 strings (same convention as skill.model.ts).

/** A skill-bank entry — a user-managed skill template. */
export interface SkillBankItem {
  id: string;
  project_id: string | null;
  name: string;
  description: string;
  content: string;
  category: string;
  /**
   * Semver version of this template (string, e.g. ``"1.0.0"``).
   * Bumped when the source skill-template file changes so startup
   * seeding can detect and refresh stale bank copies.
   */
  template_version: string;
  /**
   * Owning agent id (e.g. ``"tester"``) when the template is
   * agent-scoped; ``null`` means a generic / shared template.
   */
  agent_id: string | null;
  /**
   * Whether skills cloned from this template should have
   * ``auto_load=true`` — source of truth from skill-set.md.
   */
  auto_load: boolean;
  created_at: string;
  updated_at: string;
}

/** Create payload for POST /api/skill-bank. */
export interface SkillBankItemCreate {
  name: string;
  content: string;
  project_id?: string | null;
  description?: string;
  category?: string;
}

/** Update payload for PUT /api/skill-bank/{id}. All fields optional. */
export interface SkillBankItemUpdate {
  name?: string;
  content?: string;
  description?: string;
  category?: string;
  project_id?: string | null;
}

/** Optional filters for GET /api/skill-bank. */
export interface SkillBankFilters {
  project_id?: string;
  category?: string;
}

/** List response envelope from GET /api/skill-bank. */
export interface SkillBankListResponse {
  items: SkillBankItem[];
  total: number;
}

// Reuse the canonical skill categories from the existing skill model
// so adding a new category only requires editing skill.model.ts.
export { SKILL_CATEGORIES, type SkillCategory } from './skill.model';