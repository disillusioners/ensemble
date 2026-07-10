# Phase 1: Foundation

## Objective
Create the database schema (6 tables), repository layer (6 repositories following ensemble's existing pattern), config system for embedding/evolution models, and add `skill_injection: bool` to `AgentMetadata`. This phase provides the data foundation that all subsequent phases build upon.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: `daemon/registry.py` (AgentMetadata), `daemon/manager.py` (_ensure_postgres_columns), `daemon/repositories/__init__.py`, `daemon/repositories/factory.py`
- **Shared APIs/interfaces**: All 6 repository classes + models are consumed by Phases 2-6
- **Why this coupling**: Foundation layer — everything builds on these data models

## Context
- No previous phase — this is the starting point
- Key decision: Skills are plain markdown stored in DB (no filesystem). All metadata in DB columns.

## Tasks

### Task 1: Create SQLModel Models (6 tables)

**Create** `daemon/repositories/skill/models.py`:

```python
# Models to define:
class Skill(SQLModel, table=True):
    __tablename__ = "skills"
    # Fields: id (TEXT PK, UUID generated in Python), project_id, name, description, content (TEXT),
    # category, is_active, status, lineage_origin, generation, ab_test_group,
    # embedding (JSONBType, nullable - legacy single embedding, may be unused),
    # total_selections, total_applied, total_completions, total_fallbacks,
    # consecutive_failures, created_at, updated_at, last_used_at
    # __table_args__: UNIQUE(project_id, name, generation), indexes

class SkillLineage(SQLModel, table=True):
    __tablename__ = "skill_lineage"
    # PK: (skill_id, parent_skill_id) composite
    # Fields: change_summary, content_diff, created_at

class SkillUsageRecord(SQLModel, table=True):
    __tablename__ = "skill_usage_records"
    # Fields: id (TEXT PK), skill_id, project_id, instance_id, agent_id,
    # task_message, selected, applied, task_succeeded, iterations,
    # duration_seconds, fallback, feedback_applied (nullable bool),
    # feedback_note (nullable text), created_at

class SkillTrigger(SQLModel, table=True):
    __tablename__ = "skill_triggers"
    # Fields: id (TEXT PK), project_id (nullable), name, condition_type,
    # condition_json (JSONBType), action, is_enabled, created_at

class SkillEmbedding(SQLModel, table=True):
    __tablename__ = "skill_embeddings"
    # Fields: id (TEXT PK), skill_id, trigger_query (TEXT), 
    # embedding (JSONBType - JSON array of floats, NOT BYTEA — numpy is excluded),
    # created_at

class SkillABTest(SQLModel, table=True):
    __tablename__ = "skill_ab_tests"
    # Fields: id (TEXT PK), ab_test_group (TEXT — shared UUID grouping old+new variants),
    # skill_id_old (TEXT, FK to skills.id), skill_id_new (TEXT, FK to skills.id),
    # extension_count (INTEGER, default 0), comparisons (INTEGER, default 0),
    # created_at, resolved_at (nullable), winner_skill_id (TEXT, nullable, FK to skills.id)
    # __table_args__: index on ab_test_group
```

**Key conventions to follow:**
- TEXT PKs (NOT UUID type): `id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)` — generate UUIDs in Python, store as strings. Consistent with existing tables like `instance_execution_leases`.
- Timestamps as ISO strings: `created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())`
- JSON columns: `from daemon.repositories.infra.types import JSONBType` → `Column("condition_json", JSONBType)`
- Indexes in `__table_args__` tuple
- Unique constraint: `UniqueConstraint("project_id", "name", "generation", name="uq_skills_project_name_gen")`
- `to_dict()` method on each model for serialization

**Create** `daemon/repositories/skill/__init__.py`:
```python
from .models import Skill, SkillLineage, SkillUsageRecord, SkillTrigger, SkillEmbedding, SkillABTest
from .repository import SkillRepository, SkillLineageRepository, SkillUsageRepository, SkillTriggerRepository, SkillEmbeddingRepository, SkillABTestRepository

__all__ = [
    "Skill", "SkillLineage", "SkillUsageRecord", "SkillTrigger", "SkillEmbedding", "SkillABTest",
    "SkillRepository", "SkillLineageRepository", "SkillUsageRepository",
    "SkillTriggerRepository", "SkillEmbeddingRepository", "SkillABTestRepository",
]
```

### Task 2: Create Repository Classes (6 repositories)

**Create** `daemon/repositories/skill/repository.py`:

```python
class SkillRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
    
    # CRUD:
    def create(self, name, description, content, project_id=None, category="workflow", ...) -> Skill
    def get(self, skill_id: str) -> Skill | None
    def get_by_name(self, project_id: str | None, name: str, generation: int = 0) -> Skill | None
    def list(self, project_id: str | None = None, active_only: bool = True, limit=100, offset=0) -> tuple[list[Skill], int]
    def update(self, skill_id: str, **fields) -> Skill | None
    def delete(self, skill_id: str) -> bool
    def deactivate(self, skill_id: str) -> Skill | None
    def increment_counter(self, skill_id: str, counter: str, amount: int = 1) -> None
    # Atomic counter increment: UPDATE skills SET total_X = total_X + :amount WHERE id = :id
    
    # A/B testing:
    def get_ab_variants(self, ab_test_group: str) -> list[Skill]
    def get_active_variant(self, project_id: str | None, name: str) -> Skill | None
    
    # Search support:
    def search_bm25(self, project_id: str | None, query: str, limit: int = 10) -> list[Skill]
    # Simple in-memory BM25 over name + description + content

class SkillLineageRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
    
    def create(self, skill_id: str, parent_skill_id: str, change_summary: str, content_diff: str) -> SkillLineage
    def get_parents(self, skill_id: str) -> list[SkillLineage]
    def get_children(self, parent_skill_id: str) -> list[SkillLineage]

class SkillUsageRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
    
    def create(self, skill_id: str, project_id: str, instance_id: str, agent_id: str, ...) -> SkillUsageRecord
    def get_by_skill(self, skill_id: str, limit=100, offset=0) -> tuple[list[SkillUsageRecord], int]
    def get_stats(self, skill_id: str) -> dict  # completion_rate, fallback_rate, etc.
    def update_feedback(self, record_id: str, applied: bool, note: str) -> SkillUsageRecord | None
    # Updates feedback_applied and feedback_note on the record
    def count_comparisons(self, ab_test_group: str) -> dict  # {skill_id: count}
    
    # Capture flow support (Phase 5):
    def get_applied_for_instance(self, instance_id: str) -> list[SkillUsageRecord]:
        """Get all usage records for an instance where feedback_applied = True.
        
        Used by CAPTURED flow to check if any skill was actually applied.
        Returns empty list if no skills were applied.
        """
    
    def has_applied_for_instance(self, instance_id: str) -> bool:
        """Check if any skill was applied (feedback_applied = True) for an instance.
        
        Returns True if at least one SkillUsageRecord exists with feedback_applied=True.
        Used by CAPTURED flow: if True, skip capture (a skill was applied).
        If False or no records (NULL feedback_applied), capture is eligible.
        """
        # SELECT 1 FROM skill_usage_records WHERE instance_id = :id AND feedback_applied = TRUE LIMIT 1

class SkillTriggerRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
    
    def create(self, name: str, condition_type: str, condition_json: dict, action: str, project_id=None) -> SkillTrigger
    def get(self, trigger_id: str) -> SkillTrigger | None
    def list(self, project_id: str | None = None, enabled_only=True) -> list[SkillTrigger]
    def update(self, trigger_id: str, **fields) -> SkillTrigger | None
    def delete(self, trigger_id: str) -> bool

class SkillEmbeddingRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
    
    def create(self, skill_id: str, trigger_query: str, embedding: list[float]) -> SkillEmbedding
    def get_by_skill(self, skill_id: str) -> list[SkillEmbedding]
    def delete_by_skill(self, skill_id: str) -> int
    def get_all_for_project(self, project_id: str | None) -> list[tuple[SkillEmbedding, str]]  # (embedding, skill_id)

class SkillABTestRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
    
    def create_ab_test(self, ab_test_group: str, skill_id_old: str, skill_id_new: str) -> SkillABTest:
        """Create a new A/B test record when FIX evolution creates a new version."""
    
    def get_by_group(self, ab_test_group: str) -> SkillABTest | None:
        """Get A/B test state by group UUID. Returns None if not found."""
    
    def increment_comparison(self, ab_test_group: str) -> None:
        """Atomically increment comparisons counter.
        
        UPDATE skill_ab_tests SET comparisons = comparisons + 1 WHERE ab_test_group = :group
        """
    
    def increment_extension(self, ab_test_group: str) -> None:
        """Atomically increment extension_count counter.
        
        Called when A/B test is extended (difference < ab_min_difference after N comparisons).
        UPDATE skill_ab_tests SET extension_count = extension_count + 1 WHERE ab_test_group = :group
        """
    
    def resolve(self, ab_test_group: str, winner_skill_id: str) -> SkillABTest | None:
        """Mark A/B test as resolved with winner.
        
        Sets resolved_at = NOW(), winner_skill_id = :winner.
        """
    
    def get_active_tests(self, project_id: str | None = None) -> list[SkillABTest]:
        """Get all unresolved A/B tests (resolved_at IS NULL)."""
```

### Task 3: Register in Factory + __init__

**Modify** `daemon/repositories/factory.py`:
- Add imports for all 6 repository classes
- Add 6 factory functions:
```python
def create_skill_repository(config=None, engine=None, create_tables=True) -> SkillRepository
def create_skill_lineage_repository(config=None, engine=None, create_tables=True) -> SkillLineageRepository
def create_skill_usage_repository(config=None, engine=None, create_tables=True) -> SkillUsageRepository
def create_skill_trigger_repository(config=None, engine=None, create_tables=True) -> SkillTriggerRepository
def create_skill_embedding_repository(config=None, engine=None, create_tables=True) -> SkillEmbeddingRepository
def create_skill_ab_test_repository(config=None, engine=None, create_tables=True) -> SkillABTestRepository
```
- Add all 6 to `__all__`

**Modify** `daemon/repositories/__init__.py`:
- Add imports:
```python
# Skill Evolution repositories
from .skill.models import Skill, SkillLineage, SkillUsageRecord, SkillTrigger, SkillEmbedding, SkillABTest
from .skill.repository import SkillRepository, SkillLineageRepository, SkillUsageRepository, SkillTriggerRepository, SkillEmbeddingRepository, SkillABTestRepository
```
- Add to `__all__`

### Task 4: SQLite Migration

**Create** `daemon/migrations/versions/20260710_000001_create_skill_tables.sql`:
```sql
-- Migration: create skill evolution tables (6 tables)
-- DUAL-DRIVER NOTES:
--   For PostgreSQL: _ensure_postgres_columns() in manager.py handles creation.
--   For SQLite: This migration creates the tables.

-- UP

CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'workflow',
    is_active INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    lineage_origin TEXT NOT NULL DEFAULT 'imported',
    generation INTEGER NOT NULL DEFAULT 0,
    ab_test_group TEXT,
    embedding BLOB,  -- legacy single embedding on skills table (unused, kept for schema compat)
    total_selections INTEGER NOT NULL DEFAULT 0,
    total_applied INTEGER NOT NULL DEFAULT 0,
    total_completions INTEGER NOT NULL DEFAULT 0,
    total_fallbacks INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT,
    UNIQUE(project_id, name, generation)
);

CREATE INDEX IF NOT EXISTS idx_skills_project ON skills(project_id);
CREATE INDEX IF NOT EXISTS idx_skills_active ON skills(is_active);
CREATE INDEX IF NOT EXISTS idx_skills_ab_group ON skills(ab_test_group);

-- Remaining 5 tables: skill_lineage, skill_usage_records, skill_triggers, skill_embeddings, skill_ab_tests

CREATE TABLE IF NOT EXISTS skill_lineage (
    skill_id TEXT NOT NULL,
    parent_skill_id TEXT NOT NULL,
    change_summary TEXT NOT NULL DEFAULT '',
    content_diff TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (skill_id, parent_skill_id)
);

CREATE TABLE IF NOT EXISTS skill_usage_records (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    task_message TEXT,
    selected INTEGER NOT NULL DEFAULT 0,
    applied INTEGER NOT NULL DEFAULT 0,
    task_succeeded INTEGER NOT NULL DEFAULT 0,
    iterations INTEGER NOT NULL DEFAULT 0,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    fallback INTEGER NOT NULL DEFAULT 0,
    feedback_applied INTEGER,  -- nullable: NULL=not yet provided, 1=applied, 0=not applied
    feedback_note TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_usage_skill ON skill_usage_records(skill_id);
CREATE INDEX IF NOT EXISTS idx_skill_usage_instance ON skill_usage_records(instance_id);
CREATE INDEX IF NOT EXISTS idx_skill_usage_applied ON skill_usage_records(instance_id, feedback_applied);

CREATE TABLE IF NOT EXISTS skill_triggers (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    name TEXT NOT NULL,
    condition_type TEXT NOT NULL,
    condition_json JSON NOT NULL DEFAULT '{}',
    action TEXT NOT NULL,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_embeddings (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    trigger_query TEXT NOT NULL,
    embedding JSON NOT NULL,  -- JSON array of floats — NOT BLOB, numpy is excluded
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_embeddings_skill ON skill_embeddings(skill_id);

CREATE TABLE IF NOT EXISTS skill_ab_tests (
    id TEXT PRIMARY KEY,
    ab_test_group TEXT NOT NULL,
    skill_id_old TEXT NOT NULL,
    skill_id_new TEXT NOT NULL,
    extension_count INTEGER NOT NULL DEFAULT 0,
    comparisons INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    winner_skill_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_skill_ab_tests_group ON skill_ab_tests(ab_test_group);

-- DOWN
DROP TABLE IF EXISTS skill_ab_tests;
DROP TABLE IF EXISTS skill_embeddings;
DROP TABLE IF EXISTS skill_triggers;
DROP TABLE IF EXISTS skill_usage_records;
DROP TABLE IF EXISTS skill_lineage;
DROP TABLE IF EXISTS skills;
```

### Task 5: PostgreSQL Parity (CRITICAL)

**Modify** `daemon/manager.py` — extend `_ensure_postgres_columns()`:
- Add `CREATE TABLE IF NOT EXISTS` statements for all 6 tables using PostgreSQL syntax
- Use `BOOLEAN` instead of `INTEGER` for booleans
- Use `JSONB` for `condition_json` column and for `embedding` columns (JSON array of floats — NOT BYTEA, numpy is excluded)
- Use `TIMESTAMPTZ` for timestamps
- Use `TEXT PRIMARY KEY` (NOT UUID type) — generate UUIDs in Python, store as strings. Consistent with existing tables like `instance_execution_leases`.
- Add `CREATE INDEX IF NOT EXISTS` for all indexes

```python
# Add to the statements list in _ensure_postgres_columns():
# Skills table
(
    "CREATE TABLE IF NOT EXISTS skills ("
    "id TEXT PRIMARY KEY, "
    "project_id TEXT, "
    "name TEXT NOT NULL, "
    "description TEXT NOT NULL DEFAULT '', "
    "content TEXT NOT NULL, "
    "category TEXT NOT NULL DEFAULT 'workflow', "
    "is_active BOOLEAN NOT NULL DEFAULT TRUE, "
    "status TEXT NOT NULL DEFAULT 'active', "
    "lineage_origin TEXT NOT NULL DEFAULT 'imported', "
    "generation INTEGER NOT NULL DEFAULT 0, "
    "ab_test_group TEXT, "
    "embedding JSONB, "
    "total_selections INTEGER NOT NULL DEFAULT 0, "
    "total_applied INTEGER NOT NULL DEFAULT 0, "
    "total_completions INTEGER NOT NULL DEFAULT 0, "
    "total_fallbacks INTEGER NOT NULL DEFAULT 0, "
    "consecutive_failures INTEGER NOT NULL DEFAULT 0, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
    "last_used_at TIMESTAMPTZ"
    ")"
),
"CREATE INDEX IF NOT EXISTS idx_skills_project ON skills(project_id)",
"CREATE INDEX IF NOT EXISTS idx_skills_active ON skills(is_active)",
"CREATE INDEX IF NOT EXISTS idx_skills_ab_group ON skills(ab_test_group)",

# skill_lineage table
(
    "CREATE TABLE IF NOT EXISTS skill_lineage ("
    "skill_id TEXT NOT NULL, "
    "parent_skill_id TEXT NOT NULL, "
    "change_summary TEXT NOT NULL DEFAULT '', "
    "content_diff TEXT NOT NULL DEFAULT '', "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
    "PRIMARY KEY (skill_id, parent_skill_id)"
    ")"
),

# skill_usage_records table
(
    "CREATE TABLE IF NOT EXISTS skill_usage_records ("
    "id TEXT PRIMARY KEY, "
    "skill_id TEXT NOT NULL, "
    "project_id TEXT NOT NULL, "
    "instance_id TEXT NOT NULL, "
    "agent_id TEXT NOT NULL, "
    "task_message TEXT, "
    "selected BOOLEAN NOT NULL DEFAULT FALSE, "
    "applied BOOLEAN NOT NULL DEFAULT FALSE, "
    "task_succeeded BOOLEAN NOT NULL DEFAULT FALSE, "
    "iterations INTEGER NOT NULL DEFAULT 0, "
    "duration_seconds INTEGER NOT NULL DEFAULT 0, "
    "fallback BOOLEAN NOT NULL DEFAULT FALSE, "
    "feedback_applied BOOLEAN, "  # nullable: NULL=not yet provided, TRUE=applied, FALSE=not applied
    "feedback_note TEXT, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
    ")"
),
"CREATE INDEX IF NOT EXISTS idx_skill_usage_skill ON skill_usage_records(skill_id)",
"CREATE INDEX IF NOT EXISTS idx_skill_usage_instance ON skill_usage_records(instance_id)",
"CREATE INDEX IF NOT EXISTS idx_skill_usage_applied ON skill_usage_records(instance_id, feedback_applied)",

# skill_triggers table
(
    "CREATE TABLE IF NOT EXISTS skill_triggers ("
    "id TEXT PRIMARY KEY, "
    "project_id TEXT, "
    "name TEXT NOT NULL, "
    "condition_type TEXT NOT NULL, "
    "condition_json JSONB NOT NULL DEFAULT '{}', "
    "action TEXT NOT NULL, "
    "is_enabled BOOLEAN NOT NULL DEFAULT TRUE, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
    ")"
),

# skill_embeddings table
(
    "CREATE TABLE IF NOT EXISTS skill_embeddings ("
    "id TEXT PRIMARY KEY, "
    "skill_id TEXT NOT NULL, "
    "trigger_query TEXT NOT NULL, "
    "embedding JSONB NOT NULL, "  # JSON array of floats — NOT BYTEA, numpy is excluded
    "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
    ")"
),
"CREATE INDEX IF NOT EXISTS idx_skill_embeddings_skill ON skill_embeddings(skill_id)",

# skill_ab_tests table
(
    "CREATE TABLE IF NOT EXISTS skill_ab_tests ("
    "id TEXT PRIMARY KEY, "
    "ab_test_group TEXT NOT NULL, "
    "skill_id_old TEXT NOT NULL, "
    "skill_id_new TEXT NOT NULL, "
    "extension_count INTEGER NOT NULL DEFAULT 0, "
    "comparisons INTEGER NOT NULL DEFAULT 0, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
    "resolved_at TIMESTAMPTZ, "
    "winner_skill_id TEXT"
    ")"
),
"CREATE INDEX IF NOT EXISTS idx_skill_ab_tests_group ON skill_ab_tests(ab_test_group)",
```

### Task 6: Config System

**Modify** `daemon/config.py` — add `SkillEvolutionConfig(BaseSettings)` and register on `Config(BaseSettings)` (NOT `EnsembleConfig` — that's DB-only config; runtime config with env-var support lives in `Config` at `daemon/config.py:473`):
```python
class SkillEvolutionConfig(BaseSettings):
    """Configuration for the skill evolution system.
    
    All settings support env-var overrides via BaseSettings.
    """
    # Embedding
    embedding_model: str = Field(default="text-embedding-3-small")
    embedding_dimensions: int = Field(default=1536)
    embedding_base_url: str | None = Field(default=None)  # Falls back to LLMConfig.base_url
    embedding_api_key: str | None = Field(default=None)  # Falls back to LLMConfig.api_key
    
    # Evolution models
    evolution_model: str | None = Field(default=None)  # Falls back to main model
    analysis_model: str | None = Field(default=None)  # Cheap model for Tier 2
    
    # Injection
    max_inject_skills: int = Field(default=2)
    min_score_full_inject: float = Field(default=0.7)
    min_score_low_match: float = Field(default=0.3)
    bm25_top_k: int = Field(default=10)
    llm_select_top_k: int = Field(default=5)
    
    # Triggers
    default_task_count_threshold: int = Field(default=20)
    default_daily_scan_hour: int = Field(default=3)  # 3 AM
    
    # A/B testing
    ab_sample_size: int = Field(default=10)
    ab_min_difference: float = Field(default=0.15)  # Loser must be at least 15% worse on completion_rate
    max_extensions: int = Field(default=3)  # After 3 extensions (30 total comparisons), force-resolve by raw completion_rate even if difference < threshold
    
    # Capture
    capture_min_iterations: int = Field(default=5)
    capture_min_duration_seconds: int = Field(default=60)


class Config(BaseSettings):
    # ... existing fields ...
    skill_evolution: SkillEvolutionConfig = Field(default_factory=SkillEvolutionConfig)
```

**Access pattern:** Use `self._config.skill_evolution` everywhere (where `self._config` is the `Config` instance on `InstanceManager`). NOT `self._ensemble_config.skill_evolution`.

### Task 7: Add `skill_injection` to AgentMetadata (field + constructor wiring)

**CRITICAL — two changes required:**

#### Change 1: Add the field to the Pydantic model

**Modify** `daemon/registry.py:69-127` — `AgentMetadata` class:
```python
class AgentMetadata(BaseModel):
    # ... existing fields ...
    skill_injection: bool = Field(
        default=False,
        description="Whether this agent should have dynamic skills injected into conversations."
    )
```

#### Change 2: Wire the field in the constructor (WITHOUT THIS, THE FIELD IS ALWAYS FALSE)

The `AgentMetadata` model config has `extra="ignore"`, so Pydantic silently drops unknown keys from loaded JSON. The model is constructed at `daemon/registry.py:195-210` using **explicit kwargs** from the `meta` dict — `skill_injection` is NOT among them. Without explicitly passing it, the field will always be `False` even when `meta.json` contains `"skill_injection": true`.

**Modify** `daemon/registry.py:195-210` — the `discover()` method where `AgentMetadata` is constructed:

```python
# BEFORE (existing code, approximate):
agent_meta = AgentMetadata(
    id=meta["id"],
    name=meta.get("name", meta["id"]),
    description=meta.get("description", ""),
    icon=meta.get("icon", "🤖"),
    color=meta.get("color", "accent-blue"),
    version=meta.get("version"),
    path=agent_dir,
    system=meta.get("system", False),
    capabilities=meta.get("capabilities", []),
    tags=meta.get("tags", []),
    innate_skills=meta.get("innate_skills", []),
    tools=ToolFilter(**meta["tools"]) if "tools" in meta else None,
    llm_model=meta.get("llm_model"),
    team_members=meta.get("team_members", []),
    # MISSING: skill_injection — Pydantic drops it due to extra="ignore"
)

# AFTER (add skill_injection to the explicit kwargs):
agent_meta = AgentMetadata(
    id=meta["id"],
    name=meta.get("name", meta["id"]),
    description=meta.get("description", ""),
    icon=meta.get("icon", "🤖"),
    color=meta.get("color", "accent-blue"),
    version=meta.get("version"),
    path=agent_dir,
    system=meta.get("system", False),
    capabilities=meta.get("capabilities", []),
    tags=meta.get("tags", []),
    innate_skills=meta.get("innate_skills", []),
    tools=ToolFilter(**meta["tools"]) if "tools" in meta else None,
    llm_model=meta.get("llm_model"),
    team_members=meta.get("team_members", []),
    skill_injection=meta.get("skill_injection", False),  # ← ADD THIS LINE
)
```

**Test requirement:** Add a test that loads an agent with `"skill_injection": true` in its `meta.json` and asserts `agent_meta.skill_injection == True`. Also test that agents without the field default to `False`.

This is backward compatible — defaults to `False`, only agents with explicit `"skill_injection": true` in meta.json are affected.

## Key Files

| File | Action | Purpose |
|------|--------|---------|
| `daemon/repositories/skill/models.py` | Create | 6 SQLModel table definitions |
| `daemon/repositories/skill/repository.py` | Create | 6 repository classes |
| `daemon/repositories/skill/__init__.py` | Create | Package exports |
| `daemon/repositories/factory.py` | Modify | Add 6 factory functions |
| `daemon/repositories/__init__.py` | Modify | Register models + repos |
| `daemon/migrations/versions/20260710_000001_create_skill_tables.sql` | Create | SQLite migration |
| `daemon/manager.py` | Modify | Extend `_ensure_postgres_columns()` for 6 tables |
| `daemon/config.py` | Modify | `SkillEvolutionConfig(BaseSettings)` + register on `Config` |
| `daemon/registry.py` | Modify | Add `skill_injection: bool` to `AgentMetadata` |

## Constraints
- PostgreSQL is PRIMARY dev/test DB — test against PostgreSQL
- Use `_ensure_postgres_columns()` for ALL new tables (SQL migrations NO-OP on PG)
- All timestamps as ISO-8601 strings (ensemble convention)
- Use `JSONBType` for JSON columns (dual-driver) — including `embedding` columns (JSON array of floats, NOT BYTEA — numpy is excluded in `ensemble.spec`)
- Use `TEXT PRIMARY KEY` (not UUID type) — generate UUIDs in Python, store as strings. Consistent with existing tables.
- `SkillEvolutionConfig(BaseSettings)` goes in `daemon/config.py` on `Config` (NOT `EnsembleConfig` — that's DB-only). Access via `self._config.skill_evolution`.
- No semicolons in SQLite migration comments
- `server_default` for NOT NULL columns on PostgreSQL
- Engine shared across all repositories (created once at InstanceManager level)

## Testing Strategy
1. **Unit tests** (`tests/repositories/test_skill_repository.py`):
   - CRUD operations for each repository (create, get, update, delete)
   - List with pagination and filters
   - Counter increment (atomic)
   - A/B variant queries
   - `SkillABTestRepository`: `create_ab_test`, `get_by_group`, `increment_comparison`, `increment_extension`, `resolve`
   - BM25 search basic functionality
   - Test against both SQLite (in-memory) and PostgreSQL
2. **Migration tests**: Verify tables created correctly on both engines
3. **Config test**: Verify `SkillEvolutionConfig` defaults and overrides
4. **Registry test**: Verify `skill_injection` field parsing from meta.json — **must test that an agent with `"skill_injection": true` in meta.json actually gets `agent_meta.skill_injection == True`** (verifies the constructor wiring, not just the Pydantic field). Also test default `False` for agents without the field.

## Deliverables
- [ ] `daemon/repositories/skill/models.py` — 6 SQLModel classes
- [ ] `daemon/repositories/skill/repository.py` — 6 repository classes with CRUD
- [ ] `daemon/repositories/skill/__init__.py` — package exports
- [ ] `daemon/repositories/factory.py` — 6 new factory functions added
- [ ] `daemon/repositories/__init__.py` — models + repos registered
- [ ] `daemon/migrations/versions/20260710_000001_create_skill_tables.sql` — SQLite migration (6 tables)
- [ ] `daemon/manager.py` — `_ensure_postgres_columns()` extended for 6 tables
- [ ] `daemon/config.py` — `SkillEvolutionConfig(BaseSettings)` added on `Config` (NOT `EnsembleConfig`)
- [ ] `daemon/registry.py` — `skill_injection: bool` field on `AgentMetadata` + explicit kwarg in constructor (BI2: without the constructor change, field is always `False`)
- [ ] Tests pass for all repositories on both SQLite and PostgreSQL
