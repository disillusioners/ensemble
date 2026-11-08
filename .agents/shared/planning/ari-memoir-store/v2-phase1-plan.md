# Phase 1 — Schema Substrate, Constellation Tables & Repositories

**File:** `v2-phase1-plan.md`  
**Depends on:** Predecessor v1 schema (`20261105_102821_create_memoir_tables.sql`).  
**Flags / Decisions consumed:** DR-6, DD-2, DD-18, V2-2, V2-10, V2-13 (`v4-federated-sketch.md` §5), V2-14, V2-15, V2-20, V2-21, V2-22, V2-26 (C1), V2-28 (C3), V2-29 (SF2), V2-33 (SF11), V2-36 (W2).

---

## 1. Objective

Deliver the persistent database substrate for Memoir Store v2:
1. Create the Constellation relational tables (`memoir_star`, `memoir_star_alias`, `memoir_line`), `memoir_reflection_state`, and the durable proposal ledger table `memoir_proposal_ledger` (C3 / Leader ruling 2026-11-09) via ONE dedicated `.sql` migration file in `daemon/migrations/versions/`.
2. Evolve `memoir_episodes` via four plain `ADD COLUMN` statements in the `.sql` migration file (`poignancy_status`, `poignancy`, `poignancy_attempts`, `reflected_at`) and one cutover-bounded legacy seed `UPDATE` using the space-separated storage format `'2026-11-09 00:00:00'` (SF11 / V2-33).
3. Register new SQLModel classes in `daemon/repositories/memoir/models.py` and hook them into `daemon/manager.py` before `create_all`.
4. In `daemon/manager.py:_ensure_postgres_columns`, add TWO new ensure entries: (1) DDL multi-clause with `TIMESTAMPTZ` typing for `reflected_at` (C1 / V2-26) and `IF NOT EXISTS` clauses; (2) DML cutover-bounded legacy seed `UPDATE`.
5. Implement `MemoirConstellationRepository` for entity/alias management, line recording with `last_pass_id` crash-recovery idempotence (SF2 / V2-29), heat calculation, cooldown watermarks, and 1-hop neighborhood queries.
6. Implement `MemoirSynthesesRepository` over `memoir_syntheses` with mandatory citation enforcement, epistemic verb validation, per-agent scan-window dedup (SF4 / V2-30), and the single-transaction `supersede_with(old_id, new_row)` atomic writer.

---

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected Alternatives |
|----|----------|------------------------------|-----------------------|
| **V2-D 1.1** | ONE dedicated `.sql` migration file `YYYYMMDD_HHMMSS_create_memoir_v2_tables.sql` using **PLAIN `ADD COLUMN`** (no `IF NOT EXISTS`) for SQLite syntax compatibility (POK `20261107_110003` precedent; §3-A5 / Register #1). SQLite requires one column per ALTER statement (4 plain ALTERs) plus the cutover-bounded legacy seed UPDATE. 126-file house migration convention (dated evidence `daemon/migrations/versions/`). | `ADD COLUMN IF NOT EXISTS` is a hard syntax error in SQLite and fails `MigrationError`. The migration runner tolerates column-exists collisions on fresh SQLite databases. All string columns declared `TEXT`. | `ADD COLUMN IF NOT EXISTS` in `.sql` (breaks SQLite boots). |
| **V2-D 1.2** | Constellation tables, `memoir_reflection_state`, and `memoir_proposal_ledger` created on Postgres via model registration immediately before `SQLModel.metadata.create_all(self._engine)` in `daemon/manager.py:975-997` (POK precedent). Brand-new tables need **NO** `_ensure_postgres_columns` entry. | `create_all` is the authoritative Postgres table creation mechanism for new tables. Brand-new tables registered before `create_all` do not require ensure entries. | Adding CREATE TABLE IF NOT EXISTS to `_ensure_postgres_columns`. |
| **V2-D 1.3** | Postgres column evolution via TWO ensure entries in `_ensure_postgres_columns`: (1) Multi-clause DDL: `ALTER TABLE memoir_episodes ADD COLUMN IF NOT EXISTS poignancy_status TEXT, ADD COLUMN IF NOT EXISTS poignancy INTEGER, ADD COLUMN IF NOT EXISTS poignancy_attempts INTEGER NOT NULL DEFAULT 0, ADD COLUMN IF NOT EXISTS reflected_at TIMESTAMPTZ;` (C1: must use `TIMESTAMPTZ` to match model; manager.py:8396/8410 precedent); (2) DML legacy seed: `UPDATE memoir_episodes SET poignancy_status='legacy' WHERE poignancy_status IS NULL AND created_at < '2026-11-09 00:00:00';` (SF11 / V2-33). | `DATETIME` is SQLite-specific and causes DDL syntax errors on Postgres. Postgres supports multiple `IF NOT EXISTS` clauses in a single ALTER statement. Space-separated timestamp prevents SQLite lexicographic comparison bug. | `DATETIME` in PG ensure (causes boot abort); ISO-8601 `'T'` literal in seed. |
| **V2-D 1.4** | `poignancy_attempts INTEGER NOT NULL DEFAULT 0` column created in Phase 1 (Register #2). | Phase 4 worker query and retry/terminal logic depend on this column. Missing column in Phase 1 would break Phase 4 migrations. | Adding attempts column late in Phase 4. |
| **V2-D 1.5** | Model-bound CHECK constraints via `__table_args__`: `star_type IN ('person', 'project', 'place', 'concept', 'domain')`, `heat >= 0.0`, `notification_status IN ('pending', 'sent', 'failed')`. | SQLModel `Field()` does not emit CHECK constraints; on PG, tables are constructed from models, so `__table_args__` is the only way CHECK constraints reach the database. | Relying on migration SQL alone for CHECK constraints. |
| **V2-D 1.6** | Two-axis time discipline across all new tables (`v4-federated-sketch.md` §5): `created_at` (write time) + `first_seen_at` / `last_touched_at` (event time). | Preserves DD-7 and satisfies the R3 backward constraint for future v3/v4 delta reconciliation. | Single timestamp columns (`created_at` only). |
| **V2-D 1.7** | Cooldown watermark column: `memoir_star.description_as_of DATETIME NULL` added to `memoir_star` (§2-Q5 / Register #9). | Carries the 24h LightRAG M4 summarization cooldown state and serves as the v4 derivation watermark. `last_touched_at` updates on every mention and cannot track summarization time. | Overloading `last_touched_at` for cooldown. |
| **V2-D 1.8** | Atomic supersession writer: `supersede_with(old_id, new_row)` in `MemoirSynthesesRepository` executes insert + old-row `superseded_by` pointer + new-row `supersedes` back-pointer in ONE `engine.begin()` (§3-A2 / Register #6). | Two-call create+mark pattern leaves an unlinked fork if the process dies between calls, violating the v4 conflict-resolution invariant. | Two separate repository calls (`create_synthesis` then `mark_superseded`). |
| **V2-D 1.9** | Synthesis dedup key & per-agent window check (SF4 / V2-30): Normalization = NFC + whitespace-collapse + casefold; dedup enforced against active un-superseded syntheses within the 2-day reflection window (`WHERE agent_id=:ari AND created_at >= now()-interval '2 days' AND superseded_by IS NULL`). | Scale rationale: Reflection operates over a bounded 2-day window (~dozens of claims). In-window check is instantaneous (<1ms) and avoids altering pre-staged `memoir_syntheses` table (honoring DD-2). | Adding indexed column to pre-staged table; OR skipping dedup. |
| **V2-D 1.10** | Ensure census pin update asymmetry (§3-A5 / Register #8): `test_memoir_retrieval_pg.py:98-104` (filters on `memoir_episodes` AND (`tsv` OR `GIN`)) STAYS at 2. `test_memoir_pg_corpus.py:115-123` (filters on `memoir_episodes` only) flips 2→4 (due to the 2 new memoir entries). | The two test files use asymmetric SQL filters; updating both to 3 or 4 blindly is a defect. | Updating both assertions blindly to 3. |
| **V2-D 1.11** | Durable Proposal Ledger Table in Phase 1 (C3 / Leader ruling 2026-11-09 / V2-28 / W2): Create `memoir_proposal_ledger` (`id`, `agent_id`, `episode_id`, `proposal_date`, `proposed_at`, `notification_status`, `notified_at`, `reason`, `created_at`) with `uq_memoir_proposal_agent_episode UNIQUE (agent_id, episode_id)` in Phase 1. Stamped `notification_status='sent'` enforces restart-safe 1/day notification constraint, while the unique constraint enforces once-ever-per-episode proposal tracking (W2 / V2-36). | Restart-safe 1/day notification constraint and once-ever proposal tracking are blessed requirements; in-memory state is ruled out. | In-memory ledger; ad-hoc metadata file. |
| **V2-D 1.12** | `memoir_line.last_pass_id TEXT NULL` upsert guard (SF2 / V2-29 / Suggestion #8): Add `last_pass_id` column to `memoir_line` to ensure mid-pass crash re-extractions do not double-increment mention counts. Precondition note (VERIFY-AT-IMPLEMENTATION): SQLite syntax support for `IS DISTINCT FROM` (or `CASE WHEN ...` fallback) must be verified against host SQLite version on a throwaway SQLite database during implementation (per 20260714_000001 house lesson; do NOT write unverified version claims as fact). Dialect-neutral fallback: `(memoir_line.last_pass_id IS NULL OR memoir_line.last_pass_id != :pass_id)`. | Upsert increments `mention_count` only when `last_pass_id IS DISTINCT FROM :pass_id` (or dialect fallback), providing restart idempotence. | Plain `+= 1` on conflict without pass check. |

---

## 3. Domain Detail & Schema Specification

### 3.1 Migration SQL Specification (`daemon/migrations/versions/`)

```sql
-- UP: Memoir Store v2 Substrate Migration (126-file convention precedent)

-- 1. memoir_star: Canonical entity nodes in the Constellation
CREATE TABLE IF NOT EXISTS memoir_star (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    name TEXT NOT NULL,
    star_type TEXT NOT NULL CHECK (star_type IN ('person', 'project', 'place', 'concept', 'domain')),
    description TEXT NOT NULL DEFAULT '',
    description_as_of DATETIME,
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

-- 3. memoir_line: Weighted episodic co-occurrence edges (with last_pass_id guard, SF2)
CREATE TABLE IF NOT EXISTS memoir_line (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    star_id TEXT NOT NULL REFERENCES memoir_star(id),
    episode_id TEXT NOT NULL REFERENCES memoir_episodes(id),
    mention_count INTEGER NOT NULL DEFAULT 1 CHECK (mention_count > 0),
    last_pass_id TEXT,
    first_mentioned_at DATETIME NOT NULL,
    last_mentioned_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT uq_memoir_line_star_episode UNIQUE (star_id, episode_id)
);
CREATE INDEX IF NOT EXISTS idx_memoir_line_episode ON memoir_line (episode_id);
CREATE INDEX IF NOT EXISTS idx_memoir_line_agent ON memoir_line (agent_id, last_mentioned_at DESC);

-- 4. memoir_reflection_state: Durable watermark and claimant tracking
CREATE TABLE IF NOT EXISTS memoir_reflection_state (
    id TEXT NOT NULL PRIMARY KEY,
    last_nightly_date TEXT,
    claimed_at DATETIME
);

-- 5. memoir_proposal_ledger: Durable keepsake proposal and notification ledger (C3 / W2)
CREATE TABLE IF NOT EXISTS memoir_proposal_ledger (
    id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    episode_id TEXT NOT NULL REFERENCES memoir_episodes(id),
    proposal_date TEXT NOT NULL,
    proposed_at DATETIME NOT NULL,
    notification_status TEXT NOT NULL CHECK (notification_status IN ('pending', 'sent', 'failed')),
    notified_at DATETIME,
    reason TEXT NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL,
    CONSTRAINT uq_memoir_proposal_agent_episode UNIQUE (agent_id, episode_id)
);
CREATE INDEX IF NOT EXISTS idx_memoir_proposal_ledger_date ON memoir_proposal_ledger (agent_id, proposal_date);

-- 6. Evolution of memoir_episodes: PLAIN ADD COLUMN statements (SQLite-safe, Register #1)
ALTER TABLE memoir_episodes ADD COLUMN poignancy_status TEXT;
ALTER TABLE memoir_episodes ADD COLUMN poignancy INTEGER;
ALTER TABLE memoir_episodes ADD COLUMN poignancy_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memoir_episodes ADD COLUMN reflected_at DATETIME;

-- 7. Cutover-bounded legacy seed UPDATE (Register #5 / SF11 space-separated literal)
UPDATE memoir_episodes SET poignancy_status = 'legacy' WHERE poignancy_status IS NULL AND created_at < '2026-11-09 00:00:00';

-- DOWN: Order-safe additive rollback (child tables dropped before parent tables;
-- column additions are additive-only and retained in standard forward-only migration posture).
DROP INDEX IF EXISTS idx_memoir_proposal_ledger_date;
DROP TABLE IF EXISTS memoir_proposal_ledger;
DROP INDEX IF EXISTS idx_memoir_line_agent;
DROP INDEX IF EXISTS idx_memoir_line_episode;
DROP TABLE IF EXISTS memoir_line;
DROP INDEX IF EXISTS idx_memoir_star_alias_star;
DROP TABLE IF EXISTS memoir_star_alias;
DROP INDEX IF EXISTS idx_memoir_star_agent_heat;
DROP TABLE IF EXISTS memoir_star;
DROP TABLE IF EXISTS memoir_reflection_state;
```

### 3.2 Repository Contracts

#### `MemoirConstellationRepository` (`daemon/repositories/memoir/constellation_repository.py`)
- `upsert_star(agent_id, name, star_type, description=None, event_at=None) -> MemoirStar`: Upserts a canonical star. If existing, updates `last_touched_at = max(existing, event_at)`.
- `add_alias(agent_id, star_id, alias) -> MemoirStarAlias`: Binds alias string (e.g. "my dear", "the Emperor") to canonical star id.
- `resolve_star(agent_id, query_name) -> MemoirStar | None`: Resolves by canonical name OR alias.
- `record_line(agent_id, star_id, episode_id, event_at, pass_id=None) -> MemoirLine`: Upserts an episodic mention line. If conflict on `(star_id, episode_id)`, increments `mention_count` only if `last_pass_id IS DISTINCT FROM pass_id` (or fallback `last_pass_id IS NULL OR last_pass_id != pass_id`), updating `last_mentioned_at` and `last_pass_id`.
- `get_star_card(agent_id, star_id) -> dict`: Returns star record, all aliases, recent mention lines, and 1-hop neighbor stars.
- `increment_heat(agent_id, star_id, amount=1.0) -> float`: Atomically increments stored heat accumulator upon explicit recall.
- `update_star_description(agent_id, star_id, description, as_of_ts) -> bool`: Updates description and sets `description_as_of = as_of_ts`.

#### `MemoirSynthesesRepository` (`daemon/repositories/memoir/syntheses_repository.py`)
- `create_synthesis(agent_id, claim, source_episode_ids, verb, confidence, valid_from=None, valid_to=None, supersedes=None) -> MemoirSynthesis`:
  - **Citations validation:** Refuses with `ValueError` if `source_episode_ids` is None or empty.
  - **Verb validation:** Refuses if `verb not in SYNTHESIS_VERBS`.
  - **Dedup check (SF4):** Normalizes claim (`NFC + whitespace-collapse + casefold`). Queries active syntheses in 2-day window. Returns existing row if match found.
- `supersede_with(agent_id, old_synthesis_id, new_synthesis_dict) -> MemoirSynthesis`:
  - Performs atomic single-transaction insertion of new synthesis row + updating `old_row.superseded_by = new_id` + `new_row.supersedes = old_synthesis_id` in ONE `engine.begin()`.
- `get_synthesis(synthesis_id) -> MemoirSynthesis | None`
- `list_active_syntheses(agent_id, limit=50) -> list[MemoirSynthesis]`: Returns syntheses where `superseded_by IS NULL`.

---

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks carried | AC |
|----|------|--------------------|-------------|------------|---------------|----|
| **T1.1** | Create migration `.sql` file in `daemon/migrations/versions/` with DDL for `memoir_star`, `memoir_star_alias`, `memoir_line` (with `last_pass_id`), `memoir_reflection_state`, `memoir_proposal_ledger` (with `UNIQUE(agent_id, episode_id)`), and four plain `ADD COLUMN` statements + space-separated legacy seed UPDATE. | Migration runner applies cleanly on fresh SQLite; prefix is unique; down section drops indexes and tables in reverse order. | `daemon/migrations/versions/20261109_..._create_memoir_v2_tables.sql` | — | R10 | AC1 |
| **T1.2** | Define SQLModel classes `MemoirStar`, `MemoirStarAlias`, `MemoirLine`, `MemoirReflectionState`, and `MemoirProposalLedger` in `daemon/repositories/memoir/models.py`; update `MemoirEpisode` with `poignancy_status`, `poignancy`, `poignancy_attempts`, and `reflected_at`. | Models importable; `__table_args__` match DDL byte-identically; constraints match; 3-site index lockstep verified. | `daemon/repositories/memoir/models.py` | T1.1 | — | AC2 |
| **T1.3** | Register new models in `daemon/manager.py:920-975` before `create_all`; add TWO new ensure entries (multi-clause DDL ALTER with `TIMESTAMPTZ` + DML legacy seed) into `_ensure_postgres_columns`. | Fresh Postgres boot creates all 5 new tables + adds 4 columns to `memoir_episodes`; re-boot is completely idempotent. | `daemon/manager.py` | T1.2 | R1 | AC3 |
| **T1.4** | Implement `MemoirConstellationRepository` with star upsert, alias binding, line recording with `last_pass_id` guard, heat scoring, `description_as_of` updates, and 1-hop network joins. Verify SQLite `IS DISTINCT FROM` vs fallback (Suggestion #8). | Unit tests pass for alias resolution, duplicate handling, crash-recovery pass idempotence, and 1-hop neighbor queries. | `daemon/repositories/memoir/constellation_repository.py` | T1.2 | R9 | AC4 |
| **T1.5** | Implement `MemoirSynthesesRepository` enforcing non-empty `source_episode_ids`, valid verbs, normalized in-window dedup check, and atomic `supersede_with` writer. | Writing without citations raises `ValueError`; dedup returns existing row; `supersede_with` links both rows atomically. | `daemon/repositories/memoir/syntheses_repository.py` | T1.2 | R2 | AC5 |
| **T1.6** | Cross-driver schema verification: throwaway SQLite test (with cutover fixture row verifying `' ' < 'T'` safety) + real-PG test; update ensure census assertions (`retrieval_pg` stays 2, `pg_corpus` flips 2→4). | Tests verify table creation, index presence, CHECK constraint enforcement, and ensure idempotence on both engines. | `tests/unit/test_memoir_v2_schema.py`, `tests/postgres/test_memoir_v2_schema_pg.py` | T1.3, T1.4, T1.5 | R1 | AC6 |

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| **AC1** | Migration discovery accepts the new `.sql` file without prefix collision; every CREATE TABLE carries `IF NOT EXISTS`; all ALTER TABLE statements use plain `ADD COLUMN` (zero `IF NOT EXISTS` in `.sql`); all string columns are `TEXT`; DOWN section exists with additive-rollback note; fresh SQLite execution executes cleanly. |
| **AC2** | SQLModel metadata contains `memoir_star`, `memoir_star_alias`, `memoir_line`, `memoir_reflection_state`, `memoir_proposal_ledger`; `__table_args__` enforce constraints; `MemoirEpisode` carries `poignancy_status` (TEXT NULL), `poignancy` (INTEGER NULL), `poignancy_attempts` (INTEGER NOT NULL DEFAULT 0), and `reflected_at` (TIMESTAMPTZ NULL). |
| **AC3** | Real-PG boot creates the five tables; the four new columns appear on `memoir_episodes` (with `reflected_at` typed `TIMESTAMPTZ`); pre-existing rows before cutover date flip `poignancy_status = 'legacy'`; re-running boot is completely idempotent. `test_memoir_retrieval_pg.py` stays 2; `test_memoir_pg_corpus.py` flips 2→4. |
| **AC4** | `MemoirConstellationRepository`: Alias lookup resolves to canonical star; alias collision raises unique constraint refusal; re-recording a line with same `pass_id` leaves `mention_count` unchanged; distinct `pass_id` increments `mention_count`; updating description sets `description_as_of`; 1-hop query returns connected stars within 50ms. |
| **AC5** | `MemoirSynthesesRepository`: Attempting to create a synthesis with `source_episode_ids=[]` or `None` is refused with `ValueError`; duplicate synthesis within 2-day window returns existing row without double-write; `supersede_with` executes insert and both pointers in one transaction. |
| **AC6 (Mutation)** | Removing citation check from `create_synthesis` flips AC5 RED; swapping `star_type` CHECK order flips AC2 RED; changing `.sql` to `ADD COLUMN IF NOT EXISTS` breaks SQLite execution and flips AC1 RED; using ISO-8601 `'T'` in seed UPDATE misclassifies cutover fixture and flips AC3 RED. |

---

## 6. Test Packs

- `test/packs/memoir_v2_schema_unit_test.sh`: Tests `.sql` file syntax, plain `ADD COLUMN` statements, cutover literal format, unique migration prefix, model registry census, and constraint definitions.
- `test/packs/memoir_v2_schema_pg_test.sh`: Real PostgreSQL integration test verifying table creation, ensure column additions with `TIMESTAMPTZ`, legacy seed, GIN/B-tree indexes, and ensure census pins (retrieval_pg=2, pg_corpus=4).
- `test/packs/memoir_v2_constellation_repo_test.sh`: Comprehensive test of star upsert, alias resolution, lines with `last_pass_id`, heat scoring, `description_as_of`, and 1-hop graph joins.
- `test/packs/memoir_v2_syntheses_repo_test.sh`: Tests synthesis creation, citation enforcement, verb validation, NFC-normalized window dedup, and atomic `supersede_with` links.

---

## 7. Flag Consumption

- **DR-6:** `JSONBType` representation for `source_episode_ids`.
- **DD-2:** Activating the syntheses table pre-staged empty in v1.
- **DD-18:** ONE dedicated `.sql` migration file for schema evolution (126-file convention).
- **V2-2:** Relational 1–2 hop SQL joins without a graph database.
- **V2-10 / §3-A2:** `poignancy` and `poignancy_attempts` columns created for async scoring without touching write-frozen `importance`.
- **V2-13:** Adopting `v4-federated-sketch.md` §5 constraints (two-axis time, deterministic star canonicalization, and vocabulary discipline).
- **V2-14 / §3-A3:** Cutover-bounded legacy seed in migration.
- **V2-15 / §3-A2:** Atomic `supersede_with` writer.
- **V2-20 / §3-A1:** `reflected_at` watermark column and `memoir_reflection_state` table.
- **V2-21 / §3-A5:** Plain `ADD COLUMN` SQLite DDL syntax.
- **V2-22 / §2-Q5:** `description_as_of` cooldown carrier on `memoir_star`.
- **V2-26 / C1:** `TIMESTAMPTZ` typing for `reflected_at` in Postgres ensure.
- **V2-28 / C3:** Durable `memoir_proposal_ledger` table.
- **V2-29 / SF2:** `memoir_line.last_pass_id` crash-recovery upsert guard with SQLite `IS DISTINCT FROM` precondition note (Suggestion #8).
- **V2-30 / SF4:** NFC claim normalization and per-agent window dedup.
- **V2-33 / SF11:** Space-separated storage format `'2026-11-09 00:00:00'` cutover literal.
- **V2-36 / W2:** Proposal once-everness per episode enforced by `UNIQUE(agent_id, episode_id)`.
