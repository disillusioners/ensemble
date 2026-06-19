# Key Decisions: JSONB Migration + PG Test Infrastructure

## D1: Migration Approach — Replace `Column(JSON)` with `JSONBType` everywhere (Option C: Both)

**Decision**: Replace all 17 `Column(JSON)` with `Column(JSONBType)` in model definitions AND add `ALTER COLUMN TYPE jsonb` to `_ensure_postgres_columns()`.

**Alternatives considered**:
| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| A: ALTER only | Only add ALTER COLUMN TYPE, don't change models | Minimal code change | Fresh PG DBs still create `json` columns (models define JSON); type drift between fresh and existing DBs |
| B: JSONBType only | Only change models to JSONBType, don't add ALTER | Code is correct for fresh DBs | Existing dev DBs keep `json` type → `jsonb_set` still fails on existing data |
| **C: Both** ✅ | Change models + add ALTER | Fresh DBs get JSONB via `create_all()`, existing DBs get converted via ALTER. Full consistency. | More changes, but both are straightforward |

**Rationale**: Option C is the only approach that handles both fresh and existing databases. The dual-driver pattern (`.sql` migration for SQLite + `_ensure_postgres_columns()` for existing PG) is already established in the codebase (Era 4 migrations).

---

## D2: ALTER COLUMN TYPE Idempotency — PL/pgSQL DO Block

**Decision**: Use a single PL/pgSQL `DO $$ ... $$` block that queries `information_schema.columns` and only ALTERs columns still typed as `json`.

**Alternatives considered**:
| Approach | Pros | Cons |
|----------|------|------|
| **DO block** ✅ | Single self-contained statement, fits existing "list of SQL strings" pattern, no Python changes, idempotent by nature | Slightly harder to read |
| Python-side check | More explicit, Python-readable | More code in `_ensure_postgres_columns()`, breaks the pattern |
| Raw ALTER with EXCEPTION catch | Simple | PostgreSQL has no `ALTER COLUMN TYPE IF NOT EXISTS`; wrapping each in `BEGIN...EXCEPTION` is verbose for 17 columns |

**Rationale**: The DO block is one entry in the `statements` list. It self-filters (only converts `json` → `jsonb`, skips already-`jsonb` columns). On re-run it's a no-op. This matches the established pattern where each migration is one or more SQL string entries.

---

## D3: Test Strategy — Dedicated `tests/postgres/` Directory (Opt-in Marker)

**Decision**: Create a `tests/postgres/` directory for all PostgreSQL tests, using `@pytest.mark.postgres` marker. Tests are skipped by default; run with `pytest -m postgres`.

**Alternatives considered**:
| Approach | Pros | Cons |
|----------|------|------|
| Separate test files (`test_*_pg.py`) alongside existing | Co-located with SQLite tests | Clutters existing directories; hard to run "all PG tests" |
| **Dedicated `tests/postgres/` dir** ✅ | Clear separation, easy to run subset, auto-marks via conftest | PG tests are physically separated from their SQLite counterparts |
| Parametrized tests (both engines) | One test function, two engines | Doubles test count; SQLite concurrency tests would fail/be meaningless; parametrize doesn't fit concurrency tests well |
| Replace SQLite tests with PG | Single source of truth | Requires PG for ALL test runs — too heavy for unit tests; breaks dev workflow |

**Rationale**: Concurrency tests fundamentally differ between SQLite and PostgreSQL (SQLite can't do EPQ). A dedicated directory with an opt-in marker is cleanest. Unit tests stay on fast SQLite. PG tests are run explicitly when validating production behavior.

---

## D4: Test DB Lifecycle — Session-Scoped Engine + TRUNCATE Between Tests

**Decision**: Session-scoped `pg_engine` (created once, `create_all()` once), with `TRUNCATE ... RESTART IDENTITY CASCADE` between tests.

**Alternatives considered**:
| Strategy | Speed | Isolation | Concurrency-Safe? |
|----------|-------|-----------|-------------------|
| **TRUNCATE between tests** ✅ | Fast (PG TRUNCATE is O(tables), not O(rows)) | Full reset | ✅ Works with multi-connection tests |
| Transaction rollback (savepoint) | Fastest | Per-transaction | ❌ Breaks with separate connections |
| Drop/create tables per test | Slow | Full | ✅ But slow |
| Separate schema per test | Medium | Full | ✅ But complex |

**Rationale**: Phase 3 concurrency tests use **separate connections** that can't share a transaction boundary. TRUNCATE is the only strategy that (a) works across connections, (b) is fast on PostgreSQL, and (c) resets identity sequences. Transaction rollback would be ideal for unit tests but is incompatible with multi-connection concurrency tests.

---

## D5: PG Test Tooling — Manual Connection (No pytest-postgresql / testcontainers)

**Decision**: Use direct `create_engine(PG_TEST_URL)` connection. No `pytest-postgresql` or `testcontainers` dependency.

**Alternatives considered**:
| Tool | Pros | Cons |
|------|------|------|
| **Manual connection** ✅ | Zero new dependencies, full control, uses existing `psycopg` driver | Must start PG manually (via docker-compose) |
| `pytest-postgresql` | Auto-starts PG, manages lifecycle | New dependency; may conflict with existing docker-compose setup; version pinning concerns |
| `testcontainers` | Spins up Docker PG per session | Heavy startup time (~10s); Docker-in-Docker issues in CI; new dependency |

**Rationale**: `docker-compose.test.yml` already exists and is the project's established way to run PG. Adding `pytest-postgresql` or `testcontainers` introduces unnecessary dependencies and complexity. The `pg_available` fixture gracefully skips tests when PG isn't running. Developers run `docker compose -f docker-compose.test.yml up -d` then `pytest -m postgres`.

---

## D6: OpenCodeSessionRecord Migration — Use JSONBType (Harmless on SQLite)

**Decision**: Change `OpenCodeSessionRecord.latest_response` and `questions` to `JSONBType`. The opencode session registry uses a separate SQLite DB, so this is a no-op on SQLite (JSONBType → JSON). If PG is ever used for opencode sessions, it'll get JSONB automatically.

**Rationale**: Consistency. All JSON columns should use `JSONBType`. Even though opencode uses a separate DB, the model definition should follow the same pattern. No risk, no behavior change on SQLite.

---

## D7: Concurrency Test Scope — 5 Critical Scenarios (Not All 7)

**Decision**: Port 5 of the 7 existing concurrency test scenarios to PostgreSQL. The 2 not ported (`test_atomic_dequeue.py`, `test_task_retry_repository.py`) are message-queue-specific and can be added later if needed.

**Scenarios to port**:
1. ✅ Atomic status transition (WHERE guard) — `test_pg_atomic_transition.py`
2. ✅ Idempotent enqueue (unique index) — `test_pg_idempotent_enqueue.py`
3. ✅ Job lock slot claiming (unique constraint) — `test_pg_job_lock_slot.py`
4. ✅ Optimistic locking (version_id_col) — `test_pg_optimistic_locking.py`
5. ✅ JSONB atomic key update (jsonb_set) — `test_pg_jsonb_atomic_update.py`
6. ✅ Instance mapping upsert (unique index) — `test_pg_instance_mapping_upsert.py`

**Deferred** (can add in follow-up):
- ⏸️ Message queue atomic dequeue — `test_atomic_dequeue.py`
- ⏸️ Task retry repository (NullPool+WAL pattern) — `test_task_retry_repository.py`

**Rationale**: The 6 chosen scenarios cover all concurrency remediation patterns (status guards, optimistic locking, unique constraints, jsonb_set). The 2 deferred scenarios test message-queue internals that are less critical for validating the core concurrency fixes.

---

## D8: Optional Cleanup — Redundant `cast(..., JSONB)` Calls

**Decision**: Document but do NOT clean up in this migration. The runtime casts in `project/repository.py:286,313` (`cast(Project.relationships, JSONB)`, `cast(Project.related_directories, JSONB)`) become redundant no-ops after the columns are JSONB.

**Rationale**: These casts are harmless (casting JSONB to JSONB is a no-op). Removing them is optional cleanup, not a migration requirement. Cleaning them up adds risk (might break a query edge case) for zero functional benefit. Document for future cleanup.

---

## Summary Decision Matrix

| # | Decision | Choice | Risk |
|---|----------|--------|------|
| D1 | Migration approach | Models + ALTER (Option C) | low |
| D2 | ALTER idempotency | PL/pgSQL DO block | low |
| D3 | Test strategy | Dedicated `tests/postgres/` + opt-in marker | low |
| D4 | Test DB lifecycle | Session engine + TRUNCATE | low |
| D5 | PG test tooling | Manual connection (no new deps) | low |
| D6 | OpenCodeSessionRecord | JSONBType (no-op on SQLite) | none |
| D7 | Concurrency test scope | 6 critical scenarios | low |
| D8 | Redundant casts cleanup | Document, don't clean | none |
