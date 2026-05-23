# Phase 1: Core Layer — Models, Enums, Tool Implementation, Registry

## Objective
Rename all core Python constructs: enum names, Pydantic models, constants, the tool implementation file (with `git mv`), tool function names, and the tool registry entry. This is the foundation that all other phases depend on.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: — 
- **Shared files with other phases**: 
  - `daemon/tools/critical_experience.py` → `git mv` to `daemon/tools/critical_notes.py` in this phase
  - `daemon/repositories/project/models.py` (Phase 2 reads, Phase 4 tests)
  - `daemon/tools/_tool_registry.py` (Phase 2, 4 reference)
- **Shared APIs/interfaces**: Tool functions `project_ce_add` → `project_cn_add`, etc.

## Context
This phase rewrites the "source of truth" layer. All downstream consumers (API, agents, tests) will be updated in later phases.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Rename enums | `CriticalExperienceCategory` → `CriticalNotesCategory`, `CriticalExperiencePriority` → `CriticalNotesPriority`. Update all enum members if they reference "Experience". | `daemon/repositories/project/models.py` |
| 2 | Rename Pydantic model | `CriticalExperience` → `CriticalNotes`. Rename all fields that contain "experience" to "notes" equivalents (if any internal field names reference it). | `daemon/repositories/project/models.py` |
| 3 | Rename constant | `CRITICAL_EXPERIENCE_MAX_ENTRIES` → `CRITICAL_NOTES_MAX_ENTRIES` | `daemon/repositories/project/models.py` |
| 4 | Rename Project model field | `Project.critical_experience` → `Project.critical_notes`. Update Field() description. | `daemon/repositories/project/models.py` |
| 5 | Rewrite tool implementation content + `git mv` | **`git mv daemon/tools/critical_experience.py daemon/tools/critical_notes.py`** first. Then update content: rename class/functions `project_ce_add` → `project_cn_add`, `project_ce_list` → `project_cn_list`, `project_ce_remove` → `project_cn_remove`. Update all internal references. Update docstrings: "critical experience" → "critical notes". | `daemon/tools/critical_experience.py` → `daemon/tools/critical_notes.py` |
| 6 | Update tool registry | Change registry entry: key `"critical_experience"` → `"critical_notes"`, module path update, function name references. | `daemon/tools/_tool_registry.py` |
| 7 | Update instance tool import | Change import from `critical_experience` to `critical_notes`, update instantiation variable name. | `daemon/tools/instance.py` |

## Key Files
- `daemon/repositories/project/models.py` — All model/enum definitions
- `daemon/tools/critical_experience.py` — Tool implementation (**`git mv` to `critical_notes.py`**)
- `daemon/tools/_tool_registry.py` — Tool registry entry
- `daemon/tools/instance.py` — Import and instantiation

## Naming Map (exhaustive)

| Old Name | New Name |
|----------|----------|
| `CriticalExperienceCategory` | `CriticalNotesCategory` |
| `CriticalExperiencePriority` | `CriticalNotesPriority` |
| `CriticalExperience` (model) | `CriticalNotes` |
| `CRITICAL_EXPERIENCE_MAX_ENTRIES` | `CRITICAL_NOTES_MAX_ENTRIES` |
| `Project.critical_experience` | `Project.critical_notes` |
| `critical_experience.py` (file) | `critical_notes.py` |
| `project_ce_add` | `project_cn_add` |
| `project_ce_list` | `project_cn_list` |
| `project_ce_remove` | `project_cn_remove` |
| Registry key `"critical_experience"` | `"critical_notes"` |

## Constraints
- Do NOT change the JSON schema structure of stored data (just rename the wrapper types). Existing DB data should still deserialize correctly after migration renames the column.
- Tool descriptions in docstrings should use "critical notes" terminology.
- Keep the same parameter signatures for tool functions — only rename the functions themselves.
- **This phase owns the `git mv` of the tool file** — no other phase should rename it.

## Deliverables
- [ ] All 4 enum/model/constant names renamed in `models.py`
- [ ] Tool file `git mv`'d and all 3 tool functions renamed
- [ ] Registry entry updated
- [ ] Instance import updated
- [ ] No remaining `critical_experience` / `CriticalExperience` / `project_ce_` references in these files
