# Phase 1 — Schema Substrate, Constellation Tables & Repositories

**File:** `v2-phase1-plan.md`  
**Depends on:** Predecessor v1 schema (`20261105_102821_create_memoir_tables.sql`).  
**Flags / Decisions consumed:** DR-6, DD-2, DD-18, V2-2, V2-10, V2-13 (`v4-federated-sketch.md` §5).

---

## 1. Objective

Deliver the persistent database substrate for Memoir Store v2:
1. Create the three Constellation relational tables (`memoir_star`, `memoir_star_alias`, `memoir_line`) via ONE dedicated `.sql` migration file in `daemon/migrations/versions/`.
2. Register the new SQLModel classes in `daemon/repositories/memoir/models.py` and hook them into `daemon/manager.py` before `create_all`.
3. Add the `poignancy_status` column to existing `memoir_episodes` via `_ensure_postgres_columns` + model field + `.sql` twin.
4. Implement `MemoirConstellationRepository` for entity/alias management, line recording, heat calculation, and 1-hop neighborhood queries.
5. Implement `MemoirSynthesesRepository` over the pre-staged `memoir_syntheses` table with mandatory citation enforcement, epistemic verb validation, and supersession links.

---

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected Alternatives |
|----|----------|------------------------------|-----------------------|
| **V2-D 1.1** | ONE dedicated `.sql` migration file `YYYYMMDD_HHMMSS_create_memoir_constellation_tables.sql` holding DDL for `memoir_star`, `memoir_star_alias`, `memoir_line`, and `ALTER TABLE memoir_episodes ADD COLUMN IF NOT EXISTS poignancy_status TEXT;`. | 119-file house migration convention: every SQLModel table requires a `.sql` audit record in `daemon/migrations/versions/`. SQLite-valid by construction; all string columns declared `TEXT`. | Multiple migration files; OR relying on `create_all` alone without a migration file. |
| **V2-D 1.2** | Constellation tables created on Postgres via model registration immediately before `SQLModel.metadata.create_all(self._engine)` in `daemon/manager.py:975-997` (POK precedent). Brand-new tables need **NO** `_ensure_postgres_columns` entry. | `create_all` is the authoritative Postgres table creation mechanism for new tables. Existing tests (`test_memoir_retrieval_pg.py:98` and `test_memoir_pg_corpus.py:115`) pin the memoir ensure count at exactly 2. Adding brand-new tables to ensure would needlessly churn test pins. | Adding CREATE TABLE IF NOT EXISTS to `_ensure_postgres_columns`. |
| **V2-D 1.3** | `poignancy_status` on existing `memoir_episodes` DOES require an ensure entry: `ALTER TABLE memoir_episodes ADD COLUMN IF NOT EXISTS poignancy_status TEXT;` in `_ensure_postgres_columns` + model field + `.sql` twin. | In PostgreSQL, `create_all` does NOT add columns to existing tables. Column evolution on pre-existing tables mandates an ensure statement. | Leaving `poignancy_status` unmigrated on existing databases (causes missing column crash at runtime). |
| **V2-D 1.4** | Model-bound CHECK constraints via `__table_args__`: `star_type IN ('person', 'project', 'place', 'concept', 'domain')`, `heat >= 0.0`. | SQLModel `Field()` does not emit CHECK constraints; on PG, tables are constructed from models, so `__table_args__` is the only way CHECK constraints reach the database. | Relying on migration SQL alone for CHECK constraints. |
| **V2-D 1.5** | Two-axis time discipline across all new tables (`v4-federated-sketch.md` §5): `created_at` (write time) + `first_seen_at` / `last_touched_at` (event time). | Preserves DD-7 and satisfies the R3 backward constraint for future v3/v4 delta reconciliation. | Single timestamp columns (`created_at` only). |
| **V2-D 1.6** | Deterministic star canonicalization (`v4-federated-sketch.md` §5): canonical star name is derived as a deterministic function of the alias set + type (lowercase stripped canonical form). | Freeform star naming by LLM whim. Freeform names cannot be matched deterministically across sessions or kingdoms. | Unstructured freeform strings. |
| **V2-D 1.7** | Vocabulary discipline: `memoir_episodes.stars` (flat list of strings) is strictly decoupled from `memoir_star` entity rows. | Conflating episode tags with entity records causes schema ambiguity. | Reusing the same model/table for episode tags and entities. |

---

## 3. Domain Detail & Schema Specification

### 3.1 DDL Specification

```sql
-- 1. memoir_star: Canonical entity nodes in the Constellation
CREATE TABLE IF NOT EXISTS memoir_star (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    name TEXT NOT NULL,
    star_type TEXT NOT NULL CHECK (star_type IN ('person', 'project', 'place', 'concept', 'domain')),
    description TEXT NOT NULL DEFAULT '',
    first_seen_at DATETIME NOT NULL,
    last_touched_at DATETIME NOT NULL,
    heat REAL NOT NULL DEFAULT 0.0 CHECK (heat >= 0.0),
    created_at DATETIME NOT NULL,
    CONSTRAINT uq_memoir_star_agent_name UNIQUE (agent_id, name)
);
CREATE INDEX IF NOT EXISTS idx_memoir_star_agent_heat ON memoir_star (agent_id, heat DESC);

-- 2. memoir_star_alias: Alternative surface forms resolved to canonical stars
CREATE TABLE IF NOT EXISTS memoir_star_alias (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    star_id TEXT NOT NULL REFERENCES memoir_star(id),
    alias TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT uq_memoir_alias_agent_alias UNIQUE (agent_id, alias)
);
CREATE INDEX IF NOT EXISTS idx_memoir_star_alias_star ON memoir_star_alias (star_id);

-- 3. memoir_line: Weighted episodic co-occurrence edges
CREATE TABLE IF NOT EXISTS memoir_line (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    star_id TEXT NOT NULL REFERENCES memoir_star(id),
    episode_id TEXT NOT NULL REFERENCES memoir_episodes(id),
    mention_count INTEGER NOT NULL DEFAULT 1 CHECK (mention_count > 0),
    first_mentioned_at DATETIME NOT NULL,
    last_mentioned_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT uq_memoir_line_star_episode UNIQUE (star_id, episode_id)
);
CREATE INDEX IF NOT EXISTS idx_memoir_line_episode ON memoir_line (episode_id);
CREATE INDEX IF NOT EXISTS idx_memoir_line_agent ON memoir_line (agent_id, last_mentioned_at DESC);

-- 4. Evolution of memoir_episodes for async poignancy tracking
-- Note: on PG executed via _ensure_postgres_columns; on SQLite via this migration.
```

### 3.2 Repository Contracts

#### `MemoirConstellationRepository` (`daemon/repositories/memoir/constellation_repository.py`)
- `upsert_star(agent_id, name, star_type, description=None, event_at=None) -> MemoirStar`: Upserts a canonical star. If existing, updates `last_touched_at = max(existing, event_at)`.
- `add_alias(agent_id, star_id, alias) -> MemoirStarAlias`: Binds alias string (e.g. "my dear", "the Emperor") to canonical star id.
- `resolve_star(agent_id, query_name) -> MemoirStar | None`: Resolves by canonical name OR alias.
- `record_line(agent_id, star_id, episode_id, event_at) -> MemoirLine`: Upserts an episodic mention line, incrementing `mention_count` and updating `last_mentioned_at`.
- `get_star_card(agent_id, star_id) -> dict`: Returns star record, all aliases, recent mention lines, and 1-hop neighbor stars.
- `increment_heat(agent_id, star_id, amount=1.0) -> float`: Atomically increments star heat score upon explicit recall.

#### `MemoirSynthesesRepository` (`daemon/repositories/memoir/syntheses_repository.py`)
- `create_synthesis(agent_id, claim, source_episode_ids, verb, confidence, valid_from=None, valid_to=None, supersedes=None) -> MemoirSynthesis`:
  - **Citations validation:** Refuses with `ValueError` if `source_episode_ids` is None or empty.
  - **Verb validation:** Refuses if `verb not in SYNTHESIS_VERBS`.
  - Atomically writes row to `memoir_syntheses`.
- `get_synthesis(synthesis_id) -> MemoirSynthesis | None`
- `list_active_syntheses(agent_id, limit=50) -> list[MemoirSynthesis]`: Returns syntheses where `superseded_by IS NULL`.
- `mark_superseded(synthesis_id, superseded_by) -> bool`: Sets `superseded_by` pointer.

---

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks carried | AC |
|----|------|--------------------|-------------|------------|---------------|----|
| **T1.1** | Create migration `.sql` file in `daemon/migrations/versions/` with DDL for `memoir_star`, `memoir_star_alias`, `memoir_line` and `ALTER TABLE memoir_episodes ADD COLUMN IF NOT EXISTS poignancy_status TEXT;`. | Migration runner applies cleanly on fresh SQLite; prefix is unique; down section drops indexes and tables in reverse order. | `daemon/migrations/versions/20261109_..._create_memoir_constellation_tables.sql` | — | R10 | AC1 |
| **T1.2** | Define SQLModel classes `MemoirStar`, `MemoirStarAlias`, and `MemoirLine` in `daemon/repositories/memoir/models.py`; update `MemoirEpisode` with `poignancy_status: str | None = Field(default=None, sa_column=Column(Text, nullable=True))`. | Models importable; `__table_args__` match DDL byte-identically; constraints match; 3-site index lockstep verified. | `daemon/repositories/memoir/models.py` | T1.1 | — | AC2 |
| **T1.3** | Register new models in `daemon/manager.py:920-975` before `create_all`; add `ALTER TABLE memoir_episodes ADD COLUMN IF NOT EXISTS poignancy_status TEXT;` into `_ensure_postgres_columns`. | Fresh Postgres boot creates all 3 new tables + adds column to `memoir_episodes`; re-boot is completely idempotent. | `daemon/manager.py` | T1.2 | R1 | AC3 |
| **T1.4** | Implement `MemoirConstellationRepository` with star upsert, alias binding, line recording, heat scoring, and 1-hop network joins. | Unit tests pass for alias resolution, duplicate handling, and 1-hop neighbor queries. | `daemon/repositories/memoir/constellation_repository.py` | T1.2 | R9 | AC4 |
| **T1.5** | Implement `MemoirSynthesesRepository` enforcing non-empty `source_episode_ids`, valid verbs, and atomic supersession pointers. | Writing without citations raises `ValueError`; supersession updates pointer; search returns active syntheses. | `daemon/repositories/memoir/syntheses_repository.py` | T1.2 | R2 | AC5 |
| **T1.6** | Cross-driver schema verification: throwaway SQLite test + real-PG test. | Tests verify table creation, index presence, CHECK constraint enforcement, and ensure idempotence on both engines. | `tests/unit/test_memoir_v2_schema.py`, `tests/postgres/test_memoir_v2_schema_pg.py` | T1.3, T1.4, T1.5 | R1 | AC6 |

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| **AC1** | Migration discovery accepts the new `.sql` file without prefix collision; every CREATE TABLE carries `IF NOT EXISTS`; all string columns are `TEXT`; DOWN section exists; fresh SQLite execution executes cleanly. |
| **AC2** | SQLModel metadata contains `memoir_star`, `memoir_star_alias`, `memoir_line`; `__table_args__` enforce `star_type` enum and `heat >= 0.0`; `MemoirEpisode.poignancy_status` defaults to `None`. |
| **AC3** | Real-PG boot creates the three tables; `poignancy_status` column appears on `memoir_episodes`; re-running boot is completely idempotent. Existing tests `test_memoir_retrieval_pg.py` and `test_memoir_pg_corpus.py` updated to reflect the single new ensure entry (2 -> 3). |
| **AC4** | `MemoirConstellationRepository`: Alias lookup resolves to canonical star; alias collision raises unique constraint refusal; recording lines increments mention counts; 1-hop query returns connected stars within 50ms. |
| **AC5** | `MemoirSynthesesRepository`: Attempting to create a synthesis with `source_episode_ids=[]` or `None` is refused with `ValueError` ("syntheses must carry at least one source episode citation"); invalid verb is refused; `mark_superseded` sets replacement pointer. |
| **AC6 (Mutation)** | Removing citation check from `create_synthesis` flips AC5 RED; swapping `star_type` CHECK order flips AC2 RED; omitting `poignancy_status` from ensure flips AC3 RED on pre-existing PG tables. |

---

## 6. Test Packs

- `test/packs/memoir_v2_schema_unit_test.sh`: Tests `.sql` file syntax, unique migration prefix, model registry census, and constraint definitions.
- `test/packs/memoir_v2_schema_pg_test.sh`: Real PostgreSQL integration test verifying table creation, ensure column addition, GIN/B-tree indexes, and idempotence.
- `test/packs/memoir_v2_constellation_repo_test.sh`: Comprehensive test of star upsert, alias resolution, lines, heat scoring, and 1-hop graph joins.
- `test/packs/memoir_v2_syntheses_repo_test.sh`: Tests synthesis creation, citation enforcement, verb validation, and supersession links.

---

## 7. Flag Consumption

- **DR-6:** `JSONBType` representation for `source_episode_ids`.
- **DD-2:** Activating the syntheses table pre-staged empty in v1.
- **DD-18:** ONE dedicated `.sql` migration file for schema evolution.
- **V2-2:** Relational 1–2 hop SQL joins without a graph database.
- **V2-10:** Adding `poignancy_status` column to `memoir_episodes` for async scoring.
- **V2-13:** Adopting `v4-federated-sketch.md` §5 constraints (two-axis time, deterministic star canonicalization, and vocabulary discipline).
