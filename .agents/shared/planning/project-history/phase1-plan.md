# Phase 1: Data Layer — Model, Repository, Migration

## Objective
Create the `ProjectHistoryEntry` SQLModel, add repository methods for CRUD + search, and write the database migration to create the new table.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: —
- **Shared files with other phases**: 
  - `daemon/repositories/project/models.py` — new model class added (Phase 2 imports)
  - `daemon/repositories/project/repository.py` — new methods added (Phase 2, 3, 4 call these)
  - `daemon/repositories/project/__init__.py` — export new model (Phase 2 imports)
  - `daemon/migrations/versions/` — new migration file
- **Why this coupling**: All subsequent phases depend on the data model and repository interface being in place.

## Context
- Current `Project` model lives in `daemon/repositories/project/models.py` (196 lines)
- Repository is `SQLModelProjectRepository` in `daemon/repositories/project/repository.py` (609 lines)
- Migrations use naming format `{YYYYMMDD}_{HHMMSS}_{description}.sql` with `-- UP` / `-- DOWN` sections
- Latest migration: `20260520_000001_add_critical_experience_to_projects.sql` (verified: no `20260521_*` files exist yet)
- `__init__.py` exports: `SQLModelProjectRepository, Project, ProjectTagLink, ProjectShortnameLink`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Define `ProjectHistoryEntry` SQLModel | New class in models.py with fields: id (UUID), project_id (FK with ON DELETE CASCADE), entry_type (str), summary (str max 300), details (optional str, max 5000), recorded_by_agent (optional str), recorded_by_instance (optional str), entry_metadata (optional JSON dict), created_at (datetime). Use `table=True`. | `daemon/repositories/project/models.py` |
| 2 | Define `HistoryEntryType` enum | Enum with values: milestone, commit, phase, bugfix, deployment, note, config_change, feature, other. Place in models.py near other enums. | `daemon/repositories/project/models.py` |
| 3 | Add `to_dict()` method to `ProjectHistoryEntry` | Simple serialization for JSON output, following same pattern as `CriticalExperience.to_dict()`. | `daemon/repositories/project/models.py` |
| 4 | Add repository CRUD methods | `add_history_entry()`, `get_history_entry()`, `delete_history_entry()` — basic create/read/delete. Use SQLAlchemy sessions. `delete_history_entry()` takes `entry_id` + `project_id` and validates entry belongs to project before deleting. | `daemon/repositories/project/repository.py` |
| 5 | Add repository list/paging method | `list_history_entries(project_id, limit=20, offset=0, entry_type=None)` — returns list + total count. Order by `created_at DESC`. | `daemon/repositories/project/repository.py` |
| 6 | Add repository search method | `search_history_entries(project_id, query, limit=20, offset=0)` — LIKE-based search on summary + details fields. **Must handle NULL details** using `coalesce()` or explicit NULL guard. Returns list + total count. | `daemon/repositories/project/repository.py` |
| 7 | Add `get_recent_history()` method | `get_recent_history(project_id, limit=10)` — used by injection. Returns entries ordered by created_at DESC. | `daemon/repositories/project/repository.py` |
| 8 | Update `__init__.py` exports | Add `ProjectHistoryEntry` and `HistoryEntryType` to exports. | `daemon/repositories/project/__init__.py` |
| 9 | Write database migration | New file `20260521_000001_add_project_history_table.sql` creating `project_history` table with columns matching model, index on `(project_id, created_at DESC)`. FK must include `ON DELETE CASCADE`. Include `-- DOWN` to DROP TABLE. **Verify no `20260521_*` files exist before creating** — if they do, increment the sequence number. | `daemon/migrations/versions/20260521_000001_add_project_history_table.sql` |
| 10 | Add testing for repository methods | Unit tests for: CRUD (add, get, delete), list with paging and type filter, search (including NULL details handling), get_recent_history, and migration UP/DOWN. | `tests/` (new or existing test file) |

## Key Files
- `daemon/repositories/project/models.py` — New model + enum (append after existing enums, before Project class)
- `daemon/repositories/project/repository.py` — New methods (append to SQLModelProjectRepository class)
- `daemon/repositories/project/__init__.py` — Update exports
- `daemon/migrations/versions/20260521_000001_add_project_history_table.sql` — New migration

## Detailed Implementation Notes

### Model Definition (`models.py`)
```python
class HistoryEntryType(str, Enum):
    MILESTONE = "milestone"
    COMMIT = "commit"
    PHASE = "phase"
    BUGFIX = "bugfix"
    DEPLOYMENT = "deployment"
    NOTE = "note"
    CONFIG_CHANGE = "config_change"
    OTHER = "other"

class ProjectHistoryEntry(SQLModel, table=True):
    __tablename__ = "project_history"
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    project_id: str = Field(
        sa_column=Column(
            String, ForeignKey("projects.project_id", ondelete="CASCADE"),
            nullable=False, index=True
        )
    )
    entry_type: str  # HistoryEntryType value
    summary: str = Field(max_length=300)
    details: str | None = Field(default=None, max_length=5000)
    recorded_by_agent: str | None = Field(default=None)
    recorded_by_instance: str | None = Field(default=None)
    entry_metadata: dict | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "entry_type": self.entry_type,
            "summary": self.summary,
            "details": self.details,
            "recorded_by_agent": self.recorded_by_agent,
            "recorded_by_instance": self.recorded_by_instance,
            "entry_metadata": self.entry_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
```

### Repository Methods (`repository.py`)
All methods follow the existing `SQLModelProjectRepository` pattern: each method opens a session via `with Session(self.engine) as session:` and uses the local `session` variable. **Do NOT use `self.session` — the class has no such attribute.**

**Required imports (add to existing import block at top of file):**
```python
from sqlalchemy import func, or_, and_
# Note: `select` and `Session` are already imported from sqlmodel
```

```python
def add_history_entry(self, project_id: str, entry_type: str, summary: str,
                      details: str | None = None, agent_id: str | None = None,
                      instance_id: str | None = None, 
                      entry_metadata: dict | None = None) -> ProjectHistoryEntry:
    """Add a history entry to a project."""
    with Session(self.engine) as session:
        entry = ProjectHistoryEntry(
            project_id=project_id,
            entry_type=entry_type,
            summary=summary[:300],
            details=details[:5000] if details else None,
            recorded_by_agent=agent_id,
            recorded_by_instance=instance_id,
            entry_metadata=entry_metadata,
        )
        session.add(entry)
        session.commit()
        session.refresh(entry)
        return entry

def get_history_entry(self, entry_id: str) -> ProjectHistoryEntry | None:
    """Get a single history entry by ID."""
    with Session(self.engine) as session:
        return session.exec(
            select(ProjectHistoryEntry).where(ProjectHistoryEntry.id == entry_id)
        ).first()

def delete_history_entry(self, entry_id: str, project_id: str | None = None) -> bool:
    """Delete a history entry by ID. If project_id is provided, validates entry belongs to that project."""
    with Session(self.engine) as session:
        entry = session.exec(
            select(ProjectHistoryEntry).where(ProjectHistoryEntry.id == entry_id)
        ).first()
        if not entry:
            return False
        if project_id is not None and entry.project_id != project_id:
            return False
        session.delete(entry)
        session.commit()
        return True

def list_history_entries(self, project_id: str, limit: int = 20, offset: int = 0,
                         entry_type: str | None = None) -> tuple[list[ProjectHistoryEntry], int]:
    """List history entries with paging and optional type filter."""
    with Session(self.engine) as session:
        base_query = select(ProjectHistoryEntry).where(
            ProjectHistoryEntry.project_id == project_id
        )
        count_query = select(func.count()).select_from(ProjectHistoryEntry).where(
            ProjectHistoryEntry.project_id == project_id
        )
        if entry_type:
            base_query = base_query.where(ProjectHistoryEntry.entry_type == entry_type)
            count_query = count_query.where(ProjectHistoryEntry.entry_type == entry_type)
        
        total = session.exec(count_query).one()
        entries = session.exec(
            base_query.order_by(ProjectHistoryEntry.created_at.desc())
            .offset(offset).limit(limit)
        ).all()
        return list(entries), total

def search_history_entries(self, project_id: str, query: str,
                           limit: int = 20, offset: int = 0) -> tuple[list[ProjectHistoryEntry], int]:
    """Search history entries by text in summary and details.
    
    NOTE: Uses coalesce() for details column because NULL ILIKE returns NULL, not False.
    """
    with Session(self.engine) as session:
        search_term = f"%{query}%"
        search_filter = and_(
            ProjectHistoryEntry.project_id == project_id,
            or_(
                ProjectHistoryEntry.summary.ilike(search_term),
                func.coalesce(ProjectHistoryEntry.details, "").ilike(search_term),
            )
        )
        total = session.exec(
            select(func.count()).select_from(ProjectHistoryEntry).where(search_filter)
        ).one()
        entries = session.exec(
            select(ProjectHistoryEntry).where(search_filter)
            .order_by(ProjectHistoryEntry.created_at.desc())
            .offset(offset).limit(limit)
        ).all()
        return list(entries), total

def get_recent_history(self, project_id: str, limit: int = 10) -> list[ProjectHistoryEntry]:
    """Get most recent history entries for context injection."""
    with Session(self.engine) as session:
        return list(session.exec(
            select(ProjectHistoryEntry)
            .where(ProjectHistoryEntry.project_id == project_id)
            .order_by(ProjectHistoryEntry.created_at.desc())
            .limit(limit)
        ).all())
```

### Migration SQL
```sql
-- UP
CREATE TABLE IF NOT EXISTS project_history (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    entry_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    details TEXT,
    recorded_by_agent TEXT,
    recorded_by_instance TEXT,
    entry_metadata JSON,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_project_history_project_created 
    ON project_history(project_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_project_history_entry_type 
    ON project_history(project_id, entry_type);

-- DOWN
DROP TABLE IF EXISTS project_history;
```

## Constraints
- Must not modify existing model fields or repository methods
- New table only — no ALTER TABLE on existing tables
- Use same session pattern as existing code: `with Session(self.engine) as session:` — **never `self.session`** (the class has no such attribute)
- All methods must handle None/null gracefully
- **Required SQLAlchemy imports:** `func`, `or_`, `and_` from `sqlalchemy` (add to existing import block). `select` and `Session` are already imported from `sqlmodel`.
- Search uses `coalesce()` to guard against NULL details (NULL ILIKE returns NULL, not False)
- FK uses `ON DELETE CASCADE` — project deletion must clean up history automatically
- Repository param uses `entry_metadata` (not `metadata`) consistently

## Testing Strategy
Add tests covering the repository layer:
- **CRUD:** add entry, get by ID, delete by ID, delete with wrong project_id returns False
- **Paging:** list returns correct page, respects limit/offset, total count is accurate
- **Type filter:** list with entry_type filter, only matching entries returned
- **Search:** matches in summary, matches in details, matches when details is NULL (no false positives), no match returns empty
- **get_recent_history:** returns N most recent, ordered by created_at DESC
- **Migration:** UP creates table and indexes, DOWN drops table, re-running UP is idempotent (IF NOT EXISTS)

## Deliverables
- [ ] `ProjectHistoryEntry` model with `to_dict()` method
- [ ] `HistoryEntryType` enum
- [ ] 6 repository methods (add, get, delete, list, search, get_recent)
- [ ] Updated `__init__.py` exports
- [ ] Migration file with UP and DOWN sections (CASCADE on FK)
- [ ] Repository unit tests covering NULL handling, paging, search, and CRUD
