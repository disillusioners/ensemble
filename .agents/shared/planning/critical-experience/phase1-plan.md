# Phase 1: Schema & Migration

## Objective
Add the `CriticalExperience` Pydantic model, the `critical_experience` JSON column to the `Project` SQLModel table, create the database migration, fix the `to_data()` bug, and update `to_dict()` serialization.

## Coupling
- **Depends on**: None (foundation phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: `daemon/repositories/project/models.py` (shared with Phase 4 via `to_dict()`)
- **Shared APIs/interfaces**: `CriticalExperience` model used by Phase 2 tools
- **Why this coupling**: Schema is the foundation — all other phases depend on the model and column existing

## Context
- This is the first phase — no prior work completed
- Key decisions: Use Pydantic BaseModel for `CriticalExperience` (not SQLModel table), JSON column pattern matching existing `relationships` and `project_metadata` fields

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `CriticalExperience` Pydantic model | Add before `Project` class. Fields: `id: str` (UUID), `created_at: str`, `updated_at: str`, `source_agent: str`, `category: str` (enum: convention/pattern/risk/decision/constraint), `priority: str` (enum: critical/high/medium), `summary: str` (max 200 chars), `reference: str \| None`. Add validators for category and priority enums. Add a `to_dict()` method. | `daemon/repositories/project/models.py` |
| 2 | Add `critical_experience` column to `Project` model | Add as JSON column with `default_factory=list` and `sa_column=Column(JSON)`, following the pattern of `relationships` field (line 86-89). Place after `relationships` field. | `daemon/repositories/project/models.py` |
| 3 | Update `Project.to_dict()` | Add `"critical_experience": [ce.to_dict() for ce in self.critical_experience]` to the dict output (line 117-136). | `daemon/repositories/project/models.py` |
| 4 | Create DB migration file | Create `daemon/migrations/versions/20260520_000001_add_critical_experience_to_projects.sql` with `-- UP` (ALTER TABLE ADD COLUMN) and `-- DOWN` (ALTER TABLE DROP COLUMN) sections. Follow exact format of `20260517_000001_add_builtin_fields_to_mcp_servers.sql`. | `daemon/migrations/versions/20260520_000001_add_critical_experience_to_projects.sql` (new) |
| 5 | Fix `to_data()` latent bug (defensive) | Change `project.to_data()` to `project.to_dict()` on line 140 of `repository.py`. **Note**: This is a defensive/latent bug fix — the code path only triggers if `_enrich_project()` returns None while `project` is non-None, which shouldn't happen in practice. | `daemon/repositories/project/repository.py` |

## Key Files
- `daemon/repositories/project/models.py` — Project model + new CriticalExperience model
- `daemon/migrations/versions/20260520_000001_add_critical_experience_to_projects.sql` — New migration file
- `daemon/repositories/project/repository.py` — Latent bug fix at line 140

## Detailed Implementation Notes

### Task 1: CriticalExperience Model

```python
class CriticalExperienceCategory(str, enum.Enum):
    CONVENTION = "convention"
    PATTERN = "pattern"
    RISK = "risk"
    DECISION = "decision"
    CONSTRAINT = "constraint"

class CriticalExperiencePriority(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"

class CriticalExperience(BaseModel):
    """A single critical experience entry for a project."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    source_agent: str = ""
    category: str  # Validated against CriticalExperienceCategory
    priority: str  # Validated against CriticalExperiencePriority
    summary: str   # Max 200 chars
    reference: str | None = None

    @field_validator('category')
    @classmethod
    def validate_category(cls, v):
        if not CriticalExperienceCategory.is_valid(v):
            raise ValueError(f"Invalid category '{v}'")
        return v

    @field_validator('priority')
    @classmethod
    def validate_priority(cls, v):
        if not CriticalExperiencePriority.is_valid(v):
            raise ValueError(f"Invalid priority '{v}'")
        return v

    @field_validator('summary')
    @classmethod
    def validate_summary(cls, v):
        if len(v) > 200:
            raise ValueError(f"Summary must be ≤200 chars, got {len(v)}")
        return v

    def to_dict(self) -> dict:
        return self.model_dump()
```

### Task 2: Column Addition

```python
# In Project class, after relationships field (line 89)
critical_experience: list[dict] = Field(
    default_factory=list,
    sa_column=Column(JSON)
)
```

**Note:** Store as `list[dict]` in SQLModel (not `list[CriticalExperience]`) because JSON columns serialize/deserialize as plain dicts. The `CriticalExperience` Pydantic model is used for validation at the tool layer (Phase 2).

### Task 4: Migration File

Create new file: `daemon/migrations/versions/20260520_000001_add_critical_experience_to_projects.sql`

```sql
-- Migration: add critical_experience column to projects table
-- Created: 2026-05-20
-- Author: system
-- Description: Add critical_experience JSON column to projects table for storing concise, high-value knowledge entries

-- UP

ALTER TABLE projects ADD COLUMN critical_experience JSON DEFAULT '[]';

-- DOWN

ALTER TABLE projects DROP COLUMN critical_experience;
```

**Why this format**: Follows the exact pattern of `20260517_000001_add_builtin_fields_to_mcp_servers.sql` — header comments, `-- UP` section, `-- DOWN` section. The `MigrationRunner` discovers files via `daemon/migrations/versions/*.sql` glob, sorts by version string, and applies unapplied migrations automatically on startup (called from `daemon/manager.py:394-399`).

## Constraints
- Use Pydantic BaseModel (not SQLModel table) for CriticalExperience — it's a value object stored in a JSON column
- Summary max 200 chars enforced at model level (validator)
- Max 30 entries NOT enforced here — enforced at tool layer (Phase 2)
- All changes must be backward-compatible (default `[]` means existing projects unaffected)
- Follow existing code patterns exactly (JSON column pattern, migration pattern, to_dict pattern)

## Deliverables
- [ ] `CriticalExperience` Pydantic model with validators in `models.py`
- [ ] `critical_experience` JSON column on `Project` model with default `[]`
- [ ] `to_dict()` updated to include `critical_experience`
- [ ] SQL migration file `20260520_000001_add_critical_experience_to_projects.sql` with UP/DOWN sections
- [ ] Defensive fix: `to_data()` → `to_dict()` in `repository.py:140`
