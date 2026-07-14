# Phase 2: Schema Changes

## Objective

Add 5 new columns across 2 tables:
- **skill_bank**: `template_version`, `agent_id`, `auto_load` (3 columns)
- **skills** (evolution): `auto_load`, `source_skill_bank_id` (2 columns)

All changes must work on both SQLite and PostgreSQL using the three-path dual-driver pattern.

## Coupling

- **Depends on**: None (root phase, can run parallel with P1)
- **Coupling type**: independent
- **Shared files with other phases**: P3 (reads `template_version`/`agent_id`/`auto_load` on skill_bank), P4 (reads `auto_load`/`source_skill_bank_id` on skills; reads `auto_load` on skill_bank for clone), P5 (reads `auto_load` on skills for prompt section)
- **Why this coupling**: P3/P4/P5 all need the schema columns to exist

## Context

### Critical: Three-Path Dual-Driver Schema Evolution

| Mechanism | SQLite (existing DBs) | PostgreSQL (existing DBs) | Fresh DBs (either) |
|-----------|----------------------|--------------------------|---------------------|
| `SQLModel.metadata.create_all()` | Creates new tables only | Creates new tables only | Creates ALL tables + columns from models |
| `.sql` migration files | ✅ Runs | ❌ NO-OP (runner.py:446-448) | N/A |
| `_ensure_postgres_columns()` | N/A | ✅ ADD COLUMN IF NOT EXISTS | N/A |

**For each new column**: (1) model definition, (2) SQLite `.sql` migration, (3) PG `_ensure_postgres_columns()` statement.

### Why `auto_load` on skill_bank (C2 fix)

The clone-on-miss operation (Phase 4) must propagate the `auto_load` flag from the skill definition to the cloned skill. The `auto_load` value is defined in `skill-set.md`, stored on the `skill_bank` template during seeding (Phase 3), and read during clone. Without `auto_load` on `skill_bank`, the clone path would hardcode `auto_load=False` and the feature would never activate.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `template_version` + `agent_id` + `auto_load` to SkillBankItem model | Field definitions | `daemon/repositories/skill/models.py` (SkillBankItem class) |
| 2 | Add `auto_load` + `source_skill_bank_id` to Skill model | Field definitions | `daemon/repositories/skill/models.py` (Skill class) |
| 3 | Update `SkillBankItem.to_dict()` | Include new fields | `daemon/repositories/skill/models.py` |
| 4 | Update `Skill.to_dict()` | Include new fields | `daemon/repositories/skill/models.py` |
| 5 | Create SQLite migration for skill_bank columns | ALTER TABLE ADD COLUMN | `daemon/migrations/versions/20260714_000001_skill_bank_template_version.sql` |
| 6 | Create SQLite migration for skills columns | ALTER TABLE ADD COLUMN | `daemon/migrations/versions/20260714_000002_skills_auto_load.sql` |
| 7 | Add PG ALTER statements to `_ensure_postgres_columns()` | IF NOT EXISTS | `daemon/manager.py:2466+` |
| 8 | Update `SkillBankRepository.create()` signature | Accept `template_version`, `agent_id`, `auto_load` | `daemon/repositories/skill/skill_bank_repository.py` |
| 9 | Add `SkillBankRepository.get_by_name_and_agent()` | Lookup for seeding | `daemon/repositories/skill/skill_bank_repository.py` |
| 10 | Add `SkillBankRepository.get_auto_load_by_agent()` | Fetch auto_load templates for clone | `daemon/repositories/skill/skill_bank_repository.py` |
| 11 | Add `SkillRepository.get_auto_load_skills()` | Query project auto_load skills | `daemon/repositories/skill/repository.py` |
| 12 | Update skill_bank API response models | Add optional fields | `daemon/routers/skill_bank.py` |

## Detailed Changes

### 2.1 SkillBankItem Model — 3 New Columns

**File**: `daemon/repositories/skill/models.py`, class `SkillBankItem`

Add after `category` field:

```python
    template_version: str = Field(
        default="1.0.0",
        max_length=32,
        description=(
            "Semver version of this template. Bumped when the "
            "skills-template source file changes so startup "
            "seeding can detect and refresh stale bank copies."
        ),
    )
    agent_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description=(
            "Agent this template belongs to (e.g. 'tester'). "
            "NULL means generic/shared template."
        ),
    )
    auto_load: bool = Field(
        default=False,
        description=(
            "Whether skills cloned from this template should have "
            "auto_load=true (loaded into system prompt before every "
            "task). Source of truth from skill-set.md definition."
        ),
    )
```

Update `__table_args__`:
```python
    __table_args__ = (
        Index("ix_skill_bank_project_id", "project_id"),
        Index("ix_skill_bank_agent_id", "agent_id"),
    )
```

Update `to_dict()`:
```python
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "name": self.name,
            "description": self.description,
            "content": self.content,
            "category": self.category,
            "template_version": self.template_version,
            "agent_id": self.agent_id,
            "auto_load": self.auto_load,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
```

### 2.2 Skill Model — 2 New Columns

**File**: `daemon/repositories/skill/models.py`, class `Skill`

Add after `lineage_origin` field:

```python
    auto_load: bool = Field(
        default=False,
        description=(
            "Whether this skill is auto-loaded into the system "
            "prompt before every task. False = on-demand only."
        ),
    )
    source_skill_bank_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description=(
            "If cloned from a skill_bank template, the bank item ID. "
            "NULL for manually created or evolved skills."
        ),
    )
```

Update `__table_args__` — add index:
```python
    __table_args__ = (
        UniqueConstraint("project_id", "name", "generation", name="uq_skills_project_name_gen"),
        Index("ix_skills_project_id", "project_id"),
        Index("ix_skills_is_active", "is_active"),
        Index("ix_skills_ab_test_group", "ab_test_group"),
        Index("ix_skills_auto_load", "auto_load"),
    )
```

Update `to_dict()` to include `auto_load` and `source_skill_bank_id`.

### 2.3 SQLite Migrations

**File**: `daemon/migrations/versions/20260714_000001_skill_bank_template_version.sql`
```sql
-- Add template_version, agent_id, and auto_load columns to skill_bank.
-- PostgreSQL counterpart: _ensure_postgres_columns() in daemon/manager.py.
-- DUAL-DRIVER: SQLite gets it here; PG gets it in _ensure_postgres_columns().
ALTER TABLE skill_bank ADD COLUMN template_version TEXT NOT NULL DEFAULT '1.0.0';
ALTER TABLE skill_bank ADD COLUMN agent_id TEXT;
ALTER TABLE skill_bank ADD COLUMN auto_load INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS ix_skill_bank_agent_id ON skill_bank(agent_id);
```

**File**: `daemon/migrations/versions/20260714_000002_skills_auto_load.sql`
```sql
-- Add auto_load and source_skill_bank_id columns to skills table.
ALTER TABLE skills ADD COLUMN auto_load INTEGER NOT NULL DEFAULT 0;
ALTER TABLE skills ADD COLUMN source_skill_bank_id TEXT;
CREATE INDEX IF NOT EXISTS ix_skills_auto_load ON skills(auto_load);
```

### 2.4 PostgreSQL _ensure_postgres_columns()

**File**: `daemon/manager.py`, append to `statements` list in `_ensure_postgres_columns()`:

```python
            # ── Skill Bank template versioning + agent_id + auto_load (2026-07-14) ──
            "ALTER TABLE skill_bank ADD COLUMN IF NOT EXISTS template_version TEXT NOT NULL DEFAULT '1.0.0'",
            "ALTER TABLE skill_bank ADD COLUMN IF NOT EXISTS agent_id TEXT",
            "ALTER TABLE skill_bank ADD COLUMN IF NOT EXISTS auto_load BOOLEAN NOT NULL DEFAULT false",
            "CREATE INDEX IF NOT EXISTS ix_skill_bank_agent_id ON skill_bank(agent_id)",
            # ── Skills auto_load + source_skill_bank_id (2026-07-14) ──
            "ALTER TABLE skills ADD COLUMN IF NOT EXISTS auto_load BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE skills ADD COLUMN IF NOT EXISTS source_skill_bank_id TEXT",
            "CREATE INDEX IF NOT EXISTS ix_skills_auto_load ON skills(auto_load)",
```

### 2.5 SkillBankRepository Updates

**File**: `daemon/repositories/skill/skill_bank_repository.py`

Update `create()` signature:
```python
    def create(
        self,
        name: str,
        content: str,
        project_id: Optional[str] = None,
        description: str = "",
        category: str = "workflow",
        template_version: str = "1.0.0",
        agent_id: Optional[str] = None,
        auto_load: bool = False,
    ) -> SkillBankItem:
```

Pass new fields to `SkillBankItem(...)` constructor.

Add `get_by_name_and_agent()`:
```python
    def get_by_name_and_agent(
        self, name: str, agent_id: str,
    ) -> SkillBankItem | None:
        """Fetch a skill bank template by name + agent_id."""
        with Session(self.engine) as session:
            stmt = (
                select(SkillBankItem)
                .where(SkillBankItem.name == name)
                .where(SkillBankItem.agent_id == agent_id)
            )
            return session.exec(stmt).first()
```

Add `get_auto_load_by_agent()`:
```python
    def get_auto_load_by_agent(
        self, agent_id: str,
    ) -> list[SkillBankItem]:
        """Fetch all auto_load=true templates for an agent.
        
        Used by clone-on-miss to clone foundational skills into
        project scope before the first spawn.
        """
        with Session(self.engine) as session:
            stmt = (
                select(SkillBankItem)
                .where(SkillBankItem.agent_id == agent_id)
                .where(SkillBankItem.auto_load == True)  # noqa: E712
            )
            return list(session.exec(stmt))
```

Add `list_by_agent()`:
```python
    def list_by_agent(
        self, agent_id: str,
    ) -> list[SkillBankItem]:
        """Fetch all templates for an agent (all auto_load values)."""
        with Session(self.engine) as session:
            stmt = select(SkillBankItem).where(
                SkillBankItem.agent_id == agent_id
            )
            return list(session.exec(stmt))
```

### 2.6 SkillRepository Updates

Add `get_auto_load_skills()`:
```python
    def get_auto_load_skills(self, project_id: str) -> list[Skill]:
        """Fetch all active auto_load skills for a project."""
        with Session(self.engine) as session:
            stmt = (
                select(Skill)
                .where(Skill.project_id == project_id)
                .where(Skill.is_active == True)  # noqa: E712
                .where(Skill.auto_load == True)  # noqa: E712
            )
            return list(session.exec(stmt))
```

The `create()` method already accepts `**kwargs` forwarded to `Skill()` — `auto_load` and `source_skill_bank_id` pass through automatically.

### 2.7 Skill Bank API Response Update

**File**: `daemon/routers/skill_bank.py`

Add to `SkillBankItemResponse`:
```python
    template_version: str = "1.0.0"
    agent_id: str | None = None
    auto_load: bool = False
```

Add to `SkillBankItemCreate`:
```python
    template_version: str = "1.0.0"
    agent_id: str | None = None
    auto_load: bool = False
```

## Key Files

- `daemon/repositories/skill/models.py` — model definitions
- `daemon/repositories/skill/skill_bank_repository.py` — CRUD + new queries
- `daemon/repositories/skill/repository.py` — `get_auto_load_skills()`
- `daemon/manager.py:2466` — PG ALTER statements
- `daemon/migrations/versions/` — SQLite migrations
- `daemon/routers/skill_bank.py` — API models

## Constraints

- **5 new columns, each with 3 paths**: model + SQLite migration + PG `_ensure_postgres_columns()`
- Boolean columns: `INTEGER DEFAULT 0` on SQLite, `BOOLEAN DEFAULT false` on PostgreSQL
- `source_skill_bank_id` is a soft FK (no CONSTRAINT FK)
- `auto_load` appears on BOTH tables: `skill_bank` (template-level flag) and `skills` (cloned skill flag)

## Test Strategy

- **Extend** `tests/unit/test_skill_bank_repository.py`: test `get_by_name_and_agent()`, `get_auto_load_by_agent()`, verify `auto_load` persists
- **Extend** skill repository tests: verify `auto_load` + `source_skill_bank_id` persist
- Verify `_ensure_postgres_columns()` statements valid

## Deliverables

- [ ] SkillBankItem model has `template_version`, `agent_id`, `auto_load` fields
- [ ] Skill model has `auto_load`, `source_skill_bank_id` fields
- [ ] Both `to_dict()` include new fields
- [ ] SQLite migration files (2 files)
- [ ] PG `_ensure_postgres_columns()` statements (7 statements)
- [ ] `SkillBankRepository`: `get_by_name_and_agent()`, `get_auto_load_by_agent()`, `list_by_agent()`
- [ ] `SkillRepository.get_auto_load_skills()` exists
- [ ] API response includes new optional fields
- [ ] Tests pass on both SQLite and PostgreSQL
