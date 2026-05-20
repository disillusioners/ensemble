# Phase 4: Project Injection & API Updates

## Objective
Update `format_project_context()` to include a prominent, structured critical experience section in the injected context, and verify that project API responses reflect the new field.

## Coupling
- **Depends on**: Phase 1 (schema)
- **Coupling type**: **independent** — Phase 4 reads the `critical_experience` field via `to_dict()` which was updated in Phase 1. No shared files with Phase 2 or Phase 3.
- **Shared files with other phases**: `daemon/repositories/project/models.py` (reads `to_dict()` updated in Phase 1)
- **Shared APIs/interfaces**: None
- **Why this coupling**: Only depends on the field existing in `to_dict()` output — completely independent of tools and agent config

## Context
- Phase 1 completed: `critical_experience` field exists on Project model, `to_dict()` includes it
- `format_project_context()` currently serializes `to_dict()` output as JSON — the field will appear in the JSON blob automatically
- **However**: Burying critical experience inside a JSON blob defeats the purpose. Agents skim context, not parse JSON. A structured section is REQUIRED for visibility.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Verify `format_project_context()` includes `critical_experience` in JSON | Since `format_project_context()` calls `project.to_dict()` and Phase 1 adds `critical_experience` to `to_dict()`, the field appears in the JSON output automatically. **Verify this** — check the function doesn't filter fields. | `daemon/manager.py:156-175` |
| 2 | **Add structured Critical Experience section to `format_project_context()`** (**REQUIRED**) | Add a formatted markdown section AFTER the JSON block that lists critical experience entries with priority icons and category labels. This is the primary visibility mechanism — agents must see this at a glance, not buried in JSON. | `daemon/manager.py:156-175` |
| 3 | Verify API endpoints return `critical_experience` | Check that `GET /api/projects`, `GET /api/projects/{id}`, and `POST /api/projects` all use `to_dict()` or `_enrich_project()` which eventually calls `to_dict()`. If they do, the field is already included. | `daemon/routers/projects.py`, `daemon/repositories/project/repository.py` |
| 4 | Verify `_enrich_project()` handles `critical_experience` | The `_enrich_project()` method in `repository.py` enriches a project after loading. Ensure it properly handles the `critical_experience` JSON column (stored as `list[dict]`, no special deserialization needed). | `daemon/repositories/project/repository.py` |

## Key Files
- `daemon/manager.py:156-175` — `format_project_context()` function
- `daemon/routers/projects.py` — API endpoints
- `daemon/repositories/project/repository.py` — Repository with `_enrich_project()` and `to_dict()` usage

## Detailed Implementation Notes

### Task 1: Verify JSON Inclusion

The current `format_project_context()`:
```python
def format_project_context(project) -> str:
    import json
    project_dict = project.to_dict() if hasattr(project, 'to_dict') else vars(project)
    return f"""## Related Project

```json
{json.dumps(project_dict, indent=2)}
```

"""
```

Since `to_dict()` now includes `critical_experience`, it will appear in the JSON output automatically.

### Task 2: Structured Critical Experience Section (REQUIRED)

**Why this is required, not optional**: The entire purpose of `critical_experience` is that high-impact knowledge is *always visible* to agents. If it's only inside a JSON blob, agents will miss it — they scan context for actionable headers, not parse nested JSON arrays. A structured section with icons and formatting ensures agents actually notice and use the entries.

**Implementation**: Modify `format_project_context()` to append a formatted section:

```python
def format_project_context(project) -> str:
    import json
    
    project_dict = project.to_dict() if hasattr(project, 'to_dict') else vars(project)
    
    # Build structured critical experience section (REQUIRED for agent visibility)
    ce_entries = project_dict.get("critical_experience", [])
    ce_section = ""
    if ce_entries:
        ce_section = "\n### ⚡ Critical Experience\n"
        for entry in ce_entries:
            priority_icon = {
                "critical": "🔴", "high": "🟡", "medium": "🟢"
            }.get(entry.get("priority", ""), "⚪")
            category = entry.get("category", "")
            summary = entry.get("summary", "")
            reference = entry.get("reference")
            ref_str = f" *(ref: {reference})*" if reference else ""
            ce_section += f"- {priority_icon} **[{category}]** {summary}{ref_str}\n"
    
    return f"""## Related Project

```json
{json.dumps(project_dict, indent=2)}
```
{ce_section}
"""
```

**Example output** when project has entries:
```
## Related Project

```json
{
  "project_id": "...",
  "name": "agents-ensemble",
  "critical_experience": [
    {"id": "...", "category": "convention", "priority": "high", "summary": "Use yarn, not npm — project standard"}
  ]
}
```

### ⚡ Critical Experience
- 🟡 **[convention]** Use yarn, not npm — project standard
- 🔴 **[risk]** Database migrations run on startup — never manually alter schema
- 🟢 **[pattern]** All API responses follow {success, data, error} envelope
```

**Token budget**: 30 entries × ~120 chars formatted ≈ 3.6K chars. Well within acceptable limits for the visibility benefit.

### Task 3: API Endpoint Verification

All project API endpoints ultimately call `to_dict()` on the project model:
- `POST /api/projects` → `store.create()` → returns `project.to_dict()`
- `GET /api/projects` → `store.list_all()` → returns list of `project.to_dict()`
- `GET /api/projects/{id}` → `store.get()` → returns `project.to_dict()`

Since Phase 1 adds `critical_experience` to `to_dict()`, the field appears in all API responses automatically. Verification only — no code changes needed.

### Task 4: _enrich_project() Check

The `critical_experience` field is a JSON column that SQLModel will deserialize as `list[dict]` (matching the `list[dict]` type annotation from Phase 1). The `_enrich_project()` method only enriches tags and shortnames (via junction table queries). No special handling needed for `critical_experience` — it's stored and retrieved as part of the project row directly.

## Constraints
- No breaking changes to API response format (additive only)
- Token budget for CE section: ~3.6K chars max — acceptable
- The structured section must come AFTER the JSON block (don't break existing parsing)
- If `critical_experience` is empty `[]`, omit the structured section entirely (no empty header)

## Deliverables
- [ ] Verified `format_project_context()` includes `critical_experience` in JSON output
- [ ] **Structured Critical Experience section added to `format_project_context()`** with priority icons and category labels
- [ ] Verified API endpoints return `critical_experience` in project JSON
- [ ] Verified `_enrich_project()` handles the new field correctly (no changes needed)
