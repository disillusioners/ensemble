# Phase 1: Backend API Endpoints

## Objective
Expose two new API endpoints and extend two existing ones to surface the data the Skill Evolution UI needs. The repository and service methods already exist and are tested — this phase is API wiring plus targeted response enrichment for edge metadata and per-variant A/B metrics.

## Coupling
- **Depends on**: None
- **Coupling type**: root (no dependencies)
- **Shared files with other phases**: `daemon/routers/skills.py`, `daemon/services/skill_metrics_service.py`, `daemon/routers/skills.py` helper `_flatten_lineage_view` (backend only — no FE file overlap)
- **Shared APIs/interfaces**: Defines response shapes that Phase 2 will consume (loose coupling)
- **Why this coupling**: Phase 2 codes TypeScript interfaces against these response shapes; can be done in parallel if contracts are documented first.

## Context
- Backend is architecturally complete — 7 tables, services, repositories all exist
- Only gap is API surface for methods that already exist internally
- PostgreSQL is primary DB; repository methods are already dual-driver tested

## Existing Code (verified from source)

### 1. SkillUsageRepository.get_by_skill
**File**: `daemon/repositories/skill/repository.py:998-1003`
```python
def get_by_skill(
    self,
    skill_id: str,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[SkillUsageRecord], int]:
```
Returns: `(items, total)`. Items have columns: `id, skill_id, project_id, instance_id, agent_id, task_message, selected, applied, task_succeeded, iterations, duration_seconds, fallback, feedback_applied, feedback_note, created_at, ab_test_group, superseded`.

### 2. SkillMetricsService.get_ab_comparison_stats
**File**: `daemon/services/skill_metrics_service.py:945-948`
```python
async def get_ab_comparison_stats(self, ab_test_group: str) -> dict[str, Any]:
```
Returns (current):
```python
{
    "skill_id_a": Optional[str],
    "skill_id_b": Optional[str],
    "completion_rate_a": float,
    "completion_rate_b": float,
    "composite_score_a": float,
    "composite_score_b": float,
    "difference": float,
    "comparisons": int,
    "extension_count": int,
    "ready_to_resolve": bool,
    "needs_more_data": bool,
}
```

### 3. Lineage flattening
**File**: `daemon/routers/skills.py:263-310` (`_flatten_lineage_view`)
Currently flattens lineage edges into plain Skill dicts, **stripping `change_summary` and `content_diff`**. The `SkillLineage` model at `daemon/repositories/skill/models.py:241-301` has these fields:
```
skill_id, parent_skill_id, change_summary (str, default ""), content_diff (str, default ""), created_at
```
And `SkillLineageRepository` methods at `repository.py:805-893`: `get_parents(skill_id)`, `get_children(parent_skill_id)` — these return `SkillLineage` rows (edges), not bare Skill rows.

### 4. Existing router pattern
**File**: `daemon/routers/skills.py`
All existing endpoints follow a consistent pattern: extract path param, get repo/service from container, call method, return `dict[str, Any]`. No Pydantic `response_model`.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `GET /api/skills/{skill_id}/usage-records` endpoint | Query params: `limit` (default 50, max 200), `offset` (default 0). Call `SkillUsageRepository.get_by_skill()`. Return envelope: `{skill_id, records: [...], total, limit, offset}`. | `daemon/routers/skills.py` |
| 2 | Add `GET /api/skills/{skill_id}/ab-test/stats` endpoint + enrich response | Look up skill's `ab_test_group`. If null/empty, return `{skill_id, ab_test_group: null, stats: null}`. Call `SkillMetricsService.get_ab_comparison_stats(ab_test_group)`. **Also extend the response** (see Task 3). Return envelope: `{skill_id, ab_test_group, stats: {...}}`. | `daemon/routers/skills.py` |
| 3 | **[W2] Extend `get_ab_comparison_stats()` response with per-variant metrics** | The per-variant metrics are already computed internally via `get_stats_filtered()`. Add these fields to the return dict: `applied_rate_a/b`, `fallback_rate_a/b`, `avg_iterations_a/b`, `avg_duration_a/b`. These are available from the same stats objects used to compute `completion_rate_a/b`. **No schema migration needed** — this is pure dict enrichment in the service method. | `daemon/services/skill_metrics_service.py` (lines ~1060-1076) |
| 4 | **[W3] Add `sample_size` to `get_ab_comparison_stats()` response** | The config value `ab_sample_size` (currently 10, but configurable — was 20 earlier) must be surfaced so the FE doesn't hardcode it. Add `"sample_size": self.config.ab_sample_size` to the return dict. | `daemon/services/skill_metrics_service.py` (lines ~1060-1076) |
| 5 | **[C1] Extend `GET /api/skills/{skill_id}/lineage` to return edge metadata** | Currently `_flatten_lineage_view()` strips `change_summary` and `content_diff`. Modify the lineage endpoint (and/or the helper) to include edge metadata on each parent/child entry. **Recommended approach**: Enrich parent/child dicts to `{...skillFields, change_summary, content_diff}`. The `SkillLineageRepository.get_parents/get_children` methods already return `SkillLineage` rows (which contain edge metadata). Merge the edge fields onto the flattened skill dict. Alternatively, add a separate `edges: [...]` array to the response. **Backward-compatible**: additive fields only. | `daemon/routers/skills.py` (lines 263-310, 911-947) |
| 6 | Write backend tests | Test all new/modified endpoints: (a) usage-records happy path + pagination, (b) ab-test/stats with active test + missing group, (c) ab-test/stats response includes per-variant metrics + sample_size, (d) lineage endpoint returns `change_summary` + `content_diff` on parent/child entries. Follow existing patterns in `tests/routers/test_skills.py`. | `tests/routers/test_skills.py` |

## Key Files
- `daemon/routers/skills.py` — Add 2 new route handlers + modify lineage endpoint (primary work)
- `daemon/services/skill_metrics_service.py` — Enrich `get_ab_comparison_stats()` return dict
- `daemon/repositories/skill/repository.py` — Reference only (methods already exist)
- `tests/routers/test_skills.py` — Add test cases

## Response Shape Contracts (for Phase 2 consumption)

### `GET /api/skills/{skill_id}/usage-records?limit=50&offset=0`
```json
{
  "skill_id": "uuid",
  "records": [
    {
      "id": "uuid",
      "skill_id": "uuid",
      "project_id": "uuid|null",
      "instance_id": "uuid",
      "agent_id": "string",
      "task_message": "string",
      "selected": true,
      "applied": false,
      "task_succeeded": true,
      "iterations": 3,
      "duration_seconds": 12.5,
      "fallback": false,
      "feedback_applied": null,
      "feedback_note": null,
      "ab_test_group": "string|null",
      "superseded": false,
      "created_at": "iso-8601"
    }
  ],
  "total": 142,
  "limit": 50,
  "offset": 0
}
```

### `GET /api/skills/{skill_id}/ab-test/stats` (enriched)
```json
{
  "skill_id": "uuid",
  "ab_test_group": "group-name",
  "stats": {
    "skill_id_a": "uuid|null",
    "skill_id_b": "uuid|null",
    "completion_rate_a": 0.75,
    "completion_rate_b": 0.82,
    "composite_score_a": 0.68,
    "composite_score_b": 0.81,
    "difference": 0.13,
    "comparisons": 8,
    "sample_size": 10,
    "extension_count": 1,
    "ready_to_resolve": false,
    "needs_more_data": true,
    "applied_rate_a": 0.60,
    "applied_rate_b": 0.70,
    "fallback_rate_a": 0.15,
    "fallback_rate_b": 0.08,
    "avg_iterations_a": 4.2,
    "avg_iterations_b": 3.1,
    "avg_duration_a": 15.3,
    "avg_duration_b": 12.1
  }
}
```

### `GET /api/skills/{skill_id}/lineage` (enriched with edge metadata)
```json
{
  "skill_id": "uuid",
  "generation": 2,
  "origin": "uuid",
  "parents": [
    {
      "id": "uuid",
      "name": "Parent Skill Name",
      "...other skill fields...": "...",
      "change_summary": "Improved prompt clarity for edge cases",
      "content_diff": "--- old\n+++ new\n@@ ..."
    }
  ],
  "children": [
    {
      "id": "uuid",
      "name": "Child Skill Name",
      "...other skill fields...": "...",
      "change_summary": "Auto-evolved: added error handling section",
      "content_diff": ""
    }
  ]
}
```

## Constraints
- PostgreSQL primary; repository methods already dual-driver (SQLite/PG) tested
- Follow existing router patterns (dict responses, no Pydantic response_model required)
- Route ordering: `/usage-records` and `/ab-test/stats` are sub-paths of `/{skill_id}/` — no conflict with `/{skill_id}`
- Max limit cap at 200 to prevent excessive queries
- **Lineage enrichment must be backward-compatible** — additive fields only (`change_summary`, `content_diff` added to existing parent/child dicts). Do not change existing field names or structure.
- **`get_ab_comparison_stats()` enrichment must be backward-compatible** — new fields are additive. Existing consumers (`SkillEvolutionService` at `skill_evolution_service.py:704-715`) read `composite_score_a/b` only and will not break.

## Deliverables
- [ ] `GET /api/skills/{skill_id}/usage-records` endpoint working
- [ ] `GET /api/skills/{skill_id}/ab-test/stats` endpoint working
- [ ] `get_ab_comparison_stats()` returns per-variant `applied_rate`, `fallback_rate`, `avg_iterations`, `avg_duration`, and `sample_size`
- [ ] `GET /api/skills/{skill_id}/lineage` returns `change_summary` + `content_diff` per parent/child
- [ ] Tests passing for all new/modified endpoints
- [ ] Response shape contracts documented (above) for Phase 2
