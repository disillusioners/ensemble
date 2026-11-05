# Phase 1 — Schema, PG materialization, persona seed

**Depends on:** P0. **Flags consumed:** #14 (whole phase), #8 (mirror schema), #6 (column families), #13 (seed idempotence is unconditional — seed runs regardless of knob; the knob gates SURFACES, not the substrate).

## 1. Objective

The durable substrate: ONE `.sql` migration (SQLite-valid, additive, audit record), four SQLModel tables registered before `create_all`, the PG `tsv` generated column + GIN index via idempotent ALTER-ensure, and the §11 persona seed shipped VERBATIM via a boot-time get-or-create.

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected alternatives |
|----|----------|------------------------------|------------------------|
| D1.1 | ONE `.sql` file `…_create_memoir_tables.sql` with all four tables | commission mandates ONE; `20261020_060000_create_board_tables.sql` is the multi-table single-file house precedent (3 board tables, one file) | one-file-per-table (PSL style) — allowed but not mandated; commission says ONE |
| D1.2 | `.sql` is SQLite-valid only: `stars` TEXT (JSON), NO tsvector, no ARRAY | runner applies it to SQLite every boot (Migration Runner blueprint: runner SQLite-only, PG early-return); SQLite has neither type | carrying PG DDL in the .sql (would break every SQLite boot — the 20260714 breaker class) |
| D1.3 | PG tables via model registration immediately before `SQLModel.metadata.create_all(self._engine)` | verified site: registration block `daemon/manager.py:920-975` (board `:953-960`, bash_spawn `:961-965`, PSL `:966-973`), create_all at `:975` — the brand-new-table operative artifact (PSL A10 ruling) | `_ensure_postgres_columns` mirror as the table-creation path (that hook is for COLUMN evolution on existing tables) |
| D1.4 | `tsv` in NO SQLModel class; PG-only idempotent ensure: `ALTER TABLE memoir_episodes ADD COLUMN IF NOT EXISTS tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(title,'') || ' ' || body)) STORED` + `CREATE INDEX IF NOT EXISTS … USING GIN (tsv)` | a `Computed` tsvector breaks SQLite create_all (SQLite lacks `to_tsvector`); the raw-DDL ensure family is verified at `daemon/manager.py:9500-9522` (PSL ensure block); PG 12+ supports ADD COLUMN with STORED generated expression | dialect-conditional ORM column (SQLAlchemy cannot vary a STORED expression per dialect — precedentless surgery) |
| D1.5 | `stars` via TypeDecorator `StarsListType` (PG → `ARRAY(Text)`; else JSON) — the `JSONBType` pattern | `JSONBType` precedent at `daemon/repositories/infra/types.py:35-70`; no ARRAY column exists in any house model today (grep-verified) | plain JSONBType everywhere (loses the design's PG-canonical `TEXT[]`; v2 star queries would carry casts) |
| D1.6 | Embedding identity as SIBLING columns (`embedding_model TEXT NULL`, `embedding_dims INTEGER NULL`, `embedded_at TEXT NULL`) beside the JSONB vector, + `embedding_attempts INTEGER NOT NULL DEFAULT 0`, `embedding_error TEXT NULL` | `critical_note_embeddings` carries `model`+`dims` as queryable columns (`daemon/repositories/project/models.py:293-347` — verified); attempts/error are the worker's durability substrate (flag #7) | identity inside the embedding JSON object (not queryable; worker scan would deserialize) |
| D1.7 | Persona: `memoir_persona` (agent_id PK, current_version_id, updated_at) + `memoir_persona_versions` (id, agent_id, version, body, status CHECK in (draft,pending_approval,approved,rejected,archived), source ∈ {seed,ari,operator}, submitted_at, approved_at, UNIQUE(agent_id,version)) — transition vocabulary MIRRORED in the repository (flag #8) | `VALID_TRANSITIONS` verified at `daemon/repositories/prompt_version/repository.py:217-221`; mirroring keeps DD-9's gate without prompt-cache/UI coupling | reusing `prompt_versions` rows (wrong artifact family) |
| D1.8 | Seed via boot-time idempotent get-or-create (`source='seed'`, born `approved`), seed text constant `MEMOIR_PERSONA_SEED` in `daemon/repositories/memoir/seed.py` | runner is SQLite-only — a migration INSERT would NEVER seed PG (the operative path); boot ensure covers both drivers idempotently (flag #14 trade-off, stated) | migration-carried INSERT (silent PG gap — a defect dressed as a mechanism) |
| D1.9 | Timestamps: `DateTime(timezone=True)` for created_at/event_at/pinned_at/last_accessed_at; `embedded_at`/`submitted_at`/`approved_at` ISO-8601 TEXT (the PSL/critical-notes string convention for provenance stamps) | ordering correctness either way; provenance stamps are display-first | all-TEXT timestamps (range-index datetime comparisons stay native) |

## 3. Tables (exact proposed DDL — Phase B owns types/indexes/naming per design §4.1)

**`memoir_episodes`** — `id` (uuid4-hex TEXT PK, repository-minted), `agent_id` TEXT NOT NULL, `title` TEXT NULL, `body` TEXT NOT NULL, `created_at` ts NOT NULL, `event_at` ts NOT NULL (app-level default = created_at), `importance` INTEGER NOT NULL DEFAULT 5 CHECK (importance BETWEEN 1 AND 10), `stars` StarsListType NULL, `is_keepsake` BOOLEAN NOT NULL DEFAULT 0, `pinned_by` TEXT NULL CHECK (pinned_by IN ('empress','emperor')), `pinned_reason` TEXT NULL, `pinned_at` ts NULL, `last_accessed_at` ts NULL, `access_count` INTEGER NOT NULL DEFAULT 0, `embedding` JSONBType NULL, `embedding_model` TEXT NULL, `embedding_dims` INTEGER NULL, `embedded_at` TEXT NULL, `embedding_attempts` INTEGER NOT NULL DEFAULT 0, `embedding_error` TEXT NULL, `superseded_by` TEXT NULL REFERENCES memoir_episodes(id).
Indexes: `idx_memoir_ep_agent_created (agent_id, created_at DESC)` (dynamic zone); `idx_memoir_ep_agent_event (agent_id, event_at)` (timeline); partial `idx_memoir_ep_keepsakes … WHERE is_keepsake` (`sqlite_where`/`postgresql_where` — the resource_monitor precedent at `daemon/models/resource_monitor.py:260-268`); partial `idx_memoir_ep_pending_emb … WHERE embedding IS NULL` (worker scan). PG-only: `tsv` generated + GIN (D1.4).

**`memoir_persona`** / **`memoir_persona_versions`** / **`memoir_syntheses`** (schema only, EMPTY — DD-2; columns per design §4.4: claim, source_episode_ids (JSONBType), valid_from/valid_to TEXT NULL, verb CHECK in (CREATE,CORROBORATE,REFINE,CORRECT), supersedes/superseded_by TEXT NULL, confidence REAL, created_at ts).

## 4. Tasks

| ID | Task | Outcome | Touchpoints | Depends | AC |
|----|------|---------|-------------|---------|----|
| T1.1 | Write the `.sql` (reserved prefix; header: purpose, dual-driver notes, rollback posture, runtime-splitter semicolon rule) | file exists; `runner.discover_migrations` accepts it (unique prefix); SQLite fresh-DB apply green | daemon/migrations/versions/…_create_memoir_tables.sql | P0.T0.4 | AC1 |
| T1.2 | SQLModel models (4 tables, D1.5/D1.6/D1.9 shapes) | models importable; table names/columns match §3 exactly | daemon/repositories/memoir/models.py | T1.1 | AC2 |
| T1.3 | Manager registration import block + PG tsv/GIN ensure | PG boot lands 4 tables + tsv column + GIN (real-PG leg); SQLite boot unaffected (no tsv) | daemon/manager.py:920-975 region (registration), :9500-family region (ensure) | T1.2 | AC3 |
| T1.4 | Seed module + boot ensure: `seed_persona_if_absent(agent_id='ari')` in repository init path; `MEMOIR_PERSONA_SEED` byte-equal to design.md §11 fenced block | fresh DB (either driver) → one approved seed row `source='seed'`; re-boot → no duplicate | daemon/repositories/memoir/seed.py, repository.py, manager wiring | T1.3 | AC4 |
| T1.5 | Cross-driver boot proofs | fresh-SQLite boot applies migration cleanly; fresh-PG boot creates tables via create_all + tsv via ensure; census 119→120 files, 120 distinct prefixes | (P7 runs them; asserts land in T1.5's pack) | T1.3, T1.4 | AC5 |

## 5. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC1 | Migration census 120 files / 120 distinct prefixes; DuplicateMigrationVersionError never fires; file carries IF NOT EXISTS on every CREATE + a DOWN section |
| AC2 | Model registry: 4 tables present in `SQLModel.metadata`; columns/types/indexes == §3; `memoir_syntheses` has no writer anywhere (grep: only model + registration) |
| AC3 | Real-PG boot: `memoir_episodes.tsv` generated column exists, GIN index exists, `to_tsvector('english', …)` populates on INSERT (round-trip probe); SQLite boot: NO tsv column, boot green |
| AC4 | Seed: byte-identity pin vs design.md §11 passes; `source='seed'`, `status='approved'`, version=1; idempotent re-boot (row count 1); seed runs with knob OFF (substrate-independent) |
| AC5 | `.sql` is SQLite-valid: applied on a throwaway SQLite DB with zero statements failing (the 20260714 lesson: verify EVERY statement on real SQLite) |

## 6. Test packs

- `test/packs/memoir_schema_unit_test.sh` — model registry census, .sql parse/apply on throwaway SQLite, prefix uniqueness.
- `test/packs/memoir_schema_pg_test.sh` — real-PG legs (tsv generation, GIN, ARRAY stars round-trip, CHECK constraints, JSONB embedding round-trip, partial indexes).
- `test/packs/memoir_seed_unit_test.sh` — byte-identity + idempotence + knob-independent (mutation: seed text altered by one char → RED).

## 7. Flag consumption

#14 (all three artifacts + the seed trade-off), #8 (mirror schema + seed born approved), #6 (column families fixed here: record vs operational vs DB-derived), #1/#3 substrate (token-estimation is runtime, but `importance`/pin columns land here).
