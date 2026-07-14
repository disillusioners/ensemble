# Phase 1: Backend Persistence Layer

## Objective

Create the `skill_bank` table (dual SQLite + PostgreSQL), the `SkillBankItem` SQLModel, the `SkillBankRepository` class, the factory function, register the model in `__init__.py`, and wire everything in `manager.py` — **not** gated behind `config.skill_evolution`. After this phase, the persistence layer is fully functional and testable.

## Coupling

- **Depends on:** None (root phase)
- **Coupling type:** —
- **Shared files with other phases:** `daemon/repositories/skill/models.py` (Phase 2 imports the model from here)
- **Shared APIs/interfaces:** `SkillBankItem` model, `SkillBankRepository` class, `create_skill_bank_repository()` factory
- **Why:** Phase 2 (API router) imports these directly and accesses `manager._skill_bank_repo`. Must be sequential.

## Context

This phase establishes the data layer in isolation. The existing skill evolution system has 6 tables/models/repos that are all gated behind `config.skill_evolution`. The Skill Bank is deliberately **separate**: a single table with a simple CRUD repository, always initialized regardless of config.

**No service layer** — the router (Phase 2) accesses the repository directly. This phase only creates the persistence layer.

### Pattern References (verified from source)

- **Model shape:** `daemon/repositories/skill/models.py` → `class Skill(SQLModel, table=True)` with `__tablename__`, `__table_args__`, `Field(...)`, `to_dict()`, module-level `_now_iso()`.
- **Model registration (CRITICAL):** `daemon/repositories/__init__.py` imports all SQLModel table models so `SQLModel.metadata.create_all()` discovers them. Without the import, the table is NOT created on fresh PostgreSQL databases. (See existing imports of `Skill`, `SkillABTest`, etc. + the explicit comment for `DependencyWatcher`: "Imported here so `SQLModel.metadata.create_all()` ... registers the table on fresh PostgreSQL databases.")
- **Repository shape:** `daemon/repositories/skill/repository.py` → `class SkillRepository:` with `__init__(self, engine: Engine)`, sync methods using `with Session(self.engine) as session`.
- **Factory shape:** `daemon/repositories/factory.py` → `create_skill_repository(config=None, engine=None, create_tables=True)`.
- **Manager wiring (non-gated):** `daemon/manager.py` line 736 → `self._mcp_server_repository = create_mcp_server_repository(engine=self._engine, create_tables=False)` (NOT inside `if self.config.skill_evolution`).
- **PG DDL:** `daemon/manager.py` — the `_ensure_postgres_columns()` method (line 2460) contains a `statements` array with `CREATE TABLE IF NOT EXISTS ...` for all skill tables (DDL at ~line 2959) + `with engine.begin() as conn: for stmt: conn.execute(text(stmt))`.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `SkillBankItem` model | Add SQLModel class to existing `models.py`. Table `skill_bank`. Fields: `id` (UUID4 str PK), `project_id` (nullable str), `name` (str NOT NULL), `description` (str default `''`), `content` (str NOT NULL), `category` (str default `'workflow'`), `created_at` / `updated_at` (ISO-8601 TEXT). Include `__table_args__` with index on `project_id`. Include `to_dict()`. No FK. No UNIQUE. | `daemon/repositories/skill/models.py` |
| 2 | **Register model in `__init__.py` (CRITICAL)** | Add `SkillBankItem` to the `from .skill.models import (...)` block AND to `__all__`. Without this, `SQLModel.metadata.create_all()` will NOT discover the `skill_bank` table on fresh PostgreSQL databases. | `daemon/repositories/__init__.py` |
| 3 | Create `SkillBankRepository` | New file. Sync class with `__init__(self, engine: Engine)`. Methods: `create(...)`, `get(id)`, `list_items(project_id=None, category=None, limit=100, offset=0)`, `update(id, **fields)`, `delete(id)`, `count(project_id=None, category=None)`. Use `with Session(engine) as session` pattern. Return `SkillBankItem` or `None`. Bump `updated_at` on update. Hard-delete on delete. | `daemon/repositories/skill/skill_bank_repository.py` |
| 4 | Add factory function | `create_skill_bank_repository(config=None, engine=None, create_tables=True)` → calls `SQLModel.metadata.create_all(engine)` if `create_tables`, returns `SkillBankRepository(engine)`. Add to imports at top + `__all__` list. | `daemon/repositories/factory.py` |
| 5 | Wire repository in manager | Add `create_skill_bank_repository` import. In the repository-init block (near line 771, but OUTSIDE the `if self.config.skill_evolution` gate), add `self._skill_bank_repo = create_skill_bank_repository(engine=self._engine, create_tables=False)`. | `daemon/manager.py` |
| 6 | Add PG CREATE TABLE DDL | Add `skill_bank` table DDL to the `statements` array INSIDE `_ensure_postgres_columns()` (method at line 2460, DDL array near line 2959). Statement: `CREATE TABLE IF NOT EXISTS skill_bank (id TEXT PRIMARY KEY, project_id TEXT, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', content TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'workflow', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)` + `CREATE INDEX IF NOT EXISTS idx_skill_bank_project ON skill_bank(project_id)`. | `daemon/manager.py` |
| 7 | Add SQLite migration | New `.sql` file with comment header + `CREATE TABLE IF NOT EXISTS skill_bank (...)` + index. Document that this is a no-op on PostgreSQL (handled by raw DDL in `_ensure_postgres_columns()`). | `daemon/migrations/versions/20260713_000001_create_skill_bank.sql` |
| 8 | Write repository unit tests | Test all 6 methods against SQLite in-memory engine. Cover: create → get, list with filters, update (verify `updated_at` bumped), delete, count. | `tests/` (new test file) |

## Key Files

- `daemon/repositories/skill/models.py` — Add `SkillBankItem` class (append to existing file after `SkillABTest`)
- `daemon/repositories/__init__.py` — Add `SkillBankItem` to model imports + `__all__` (**CRITICAL for table discovery**)
- `daemon/repositories/skill/skill_bank_repository.py` — **NEW** repository
- `daemon/repositories/factory.py` — Add `create_skill_bank_repository()`
- `daemon/manager.py` — Wire repo (non-gated) + PG DDL in `_ensure_postgres_columns()`
- `daemon/migrations/versions/20260713_000001_create_skill_bank.sql` — **NEW** SQLite migration

## Detailed Specs

### `SkillBankItem` Model (Task 1)

```python
class SkillBankItem(SQLModel, table=True):
    """A skill stored in the Skill Bank — a user-managed template.

    Isolated from the skill evolution system: no FK to ``skills``,
    no counters, no lineage, no triggers, no embeddings. Pure
    user-facing CRUD storage.

    Attributes:
        id: UUID4 primary key (TEXT for dual-driver portability).
        project_id: Owning project. ``NULL`` = global/shared.
        name: Human-readable name (NOT unique — duplicates allowed).
        description: One-line summary (default empty string).
        content: The skill body — markdown / instructions.
        category: Free-form category string (default ``'workflow'``).
        created_at: ISO-8601 timestamp, immutable.
        updated_at: ISO-8601 timestamp, bumped on every update.
    """

    __tablename__ = "skill_bank"
    __table_args__ = (Index("ix_skill_bank_project_id", "project_id"),)

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    project_id: Optional[str] = Field(default=None, max_length=64)
    name: str = Field(sa_column=Column(String, nullable=False), max_length=256)
    description: str = Field(default="")
    content: str = Field(sa_column=Column(String, nullable=False))
    category: str = Field(default="workflow", max_length=64)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "name": self.name,
            "description": self.description,
            "content": self.content,
            "category": self.category,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
```

**Imports needed in models.py (already present):** `uuid`, `Optional`, `Any`, `Column`, `Index`, `String`, `Field`, `SQLModel`, `_now_iso`.

### `__init__.py` Registration (Task 2 — CRITICAL)

Add `SkillBankItem` to the existing skill model imports in `daemon/repositories/__init__.py`:

```python
# Existing block (modify):
from .skill.models import (
    Skill,
    SkillABTest,
    SkillEmbedding,
    SkillLineage,
    SkillTrigger,
    SkillUsageRecord,
    SkillBankItem,          # ← ADD THIS
)
```

And add to `__all__`:

```python
__all__ = [
    # ... existing entries ...
    # Skill (Phase 1 of the Skill Evolution System)
    "SkillRepository",
    # ... existing skill entries ...
    "SkillABTest",
    "SkillBankItem",         # ← ADD THIS
    # ... rest ...
]
```

**Why this is critical:** `SQLModel.metadata.create_all()` only creates tables for models that have been imported and registered in `SQLModel.metadata`. If `SkillBankItem` is never imported, its table is invisible to `create_all` — the table will NOT exist on fresh PostgreSQL databases. The `__init__.py` import is the registration point (see the existing comment for `DependencyWatcher`: *"Imported here so `SQLModel.metadata.create_all()` ... registers the table on fresh PostgreSQL databases"*).

### `SkillBankRepository` (Task 3)

```python
class SkillBankRepository:
    """SQLModel-based repository for the ``skill_bank`` table.

    All methods are synchronous; callers bridge to async via
    ``asyncio.to_thread``.
    """

    def __init__(self, engine: Engine):
        self.engine = engine

    def create(self, name, content, project_id=None, description="", category="workflow"):
        # Insert + commit + refresh + return SkillBankItem

    def get(self, item_id: str) -> SkillBankItem | None:
        # session.get(SkillBankItem, item_id)

    def list_items(self, project_id=None, category=None, limit=100, offset=0):
        # select(SkillBankItem).where(...).offset(offset).limit(limit)
        # Order by created_at DESC

    def update(self, item_id: str, **fields) -> SkillBankItem | None:
        # Fetch, if None return None. Apply provided fields.
        # Always bump updated_at = _now_iso(). Commit + refresh.

    def delete(self, item_id: str) -> bool:
        # Fetch, if None return False. session.delete(). commit(). return True.

    def count(self, project_id=None, category=None) -> int:
        # select(func.count()).select_from(SkillBankItem).where(...)
```

### Manager Wiring (Tasks 5–6)

**Task 5 — Repository init** (near line 771, OUTSIDE the `skill_evolution` gate):

```python
# Skill Bank — standalone user-facing CRUD (NOT gated by skill_evolution)
self._skill_bank_repo = create_skill_bank_repository(
    engine=self._engine, create_tables=False
)
```

**Task 6 — PG DDL** (append to `statements` array INSIDE `_ensure_postgres_columns()`, after the `skill_ab_tests` block ~line 3057):

```python
# ── Skill Bank table (isolated user CRUD, not skill evolution) ────
(
    "CREATE TABLE IF NOT EXISTS skill_bank ("
    "id TEXT PRIMARY KEY, "
    "project_id TEXT, "
    "name TEXT NOT NULL, "
    "description TEXT NOT NULL DEFAULT '', "
    "content TEXT NOT NULL, "
    "category TEXT NOT NULL DEFAULT 'workflow', "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL"
    ")"
),
"CREATE INDEX IF NOT EXISTS idx_skill_bank_project ON skill_bank(project_id)",
```

### SQLite Migration (Task 7)

File: `daemon/migrations/versions/20260713_000001_create_skill_bank.sql`

```sql
-- Migration: create skill_bank table
-- Created: 2026-07-13
-- Description:
--   Creates the ``skill_bank`` table for the isolated Skill Bank CRUD
--   feature. This is NOT part of the skill evolution system — it is
--   a standalone user-facing template store. No FK to ``skills``.
--
--   NOTE: This .sql migration is a NO-OP on PostgreSQL (the .sql
--   runner skips non-SQLite engines). PostgreSQL table creation is
--   handled by raw DDL in ``daemon/manager.py``
--   ``_ensure_postgres_columns()`` (line 2460). On SQLite, this
--   provides idempotent CREATE TABLE IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS skill_bank (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'workflow',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_bank_project ON skill_bank(project_id);
```

## Constraints

- **PostgreSQL is PRIMARY** — table MUST be created via raw DDL in `_ensure_postgres_columns()` (line 2460), not just via `SQLModel.metadata.create_all()`.
- **Model registration** — `SkillBankItem` MUST be imported in `daemon/repositories/__init__.py` so `SQLModel.metadata.create_all()` discovers the table on fresh databases.
- **Dual-driver** — use `TEXT` for all columns (not UUID type, not native JSON). ISO-8601 strings for timestamps.
- **NOT gated** — repository init must be OUTSIDE `if self.config.skill_evolution`.
- **No FK** — `skill_bank` has no foreign keys. Isolation is by design.
- **No UNIQUE on name** — duplicates are allowed.

## Deliverables

- [ ] `SkillBankItem` model added to `daemon/repositories/skill/models.py`
- [ ] `SkillBankItem` imported in `daemon/repositories/__init__.py` (imports + `__all__`)
- [ ] `SkillBankRepository` in `daemon/repositories/skill/skill_bank_repository.py`
- [ ] `create_skill_bank_repository()` added to `daemon/repositories/factory.py`
- [ ] Repository wired in `daemon/manager.py` (non-gated)
- [ ] PG CREATE TABLE DDL in `_ensure_postgres_columns()` (line 2460)
- [ ] SQLite migration file `20260713_000001_create_skill_bank.sql`
- [ ] Repository unit tests pass
