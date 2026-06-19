# Phase 2: PostgreSQL Test Infrastructure

## Objective
Build a reusable, session-scoped PostgreSQL test infrastructure: a `tests/conftest_postgres.py` with `pg_engine`, `pg_session_factory`, and `pg_repository_factory` fixtures; register `@pytest.mark.postgres` marker; wire `docker-compose.test.yml` to be auto-started; and ensure PG tests are opt-in (skipped by default, run with `-m postgres`).

## Coupling
- **Depends on**: Phase 1 (JSONB columns must exist for `jsonb_set` operations to work in Phase 3 tests)
- **Coupling type**: loose — Phase 2 only needs Phase 1's *result*. Can start scaffolding the conftest while Phase 1 review is in progress.
- **Shared files with other phases**: `tests/conftest_postgres.py` (Phase 3 imports fixtures from here)
- **Shared APIs/interfaces**: `pg_engine`, `pg_session_factory`, `pg_repository_factory` fixtures — consumed by Phase 3
- **Why this coupling**: Phase 3's concurrency tests will `from tests.conftest_postgres import pg_engine` (or use fixture injection). The fixture API must be stable before Phase 3 coding.

## Context
- **Existing test DB fixtures**: In-memory SQLite + StaticPool, defined in `tests/job_queue/conftest.py` and duplicated across 5+ subdirectory conftests. Phase 2 does NOT modify these — it adds a parallel PG path.
- **docker-compose.test.yml**: Already exists (`postgres:16-alpine`, `ensemble_test` DB, user `ensemble`, password `test_password`, port 5432). Currently manual-start only.
- **No `pytest-postgresql`**: Not installed. Decision needed (see decisions.md) — manual connection vs `pytest-postgresql` vs `testcontainers`.
- **pyproject.toml pytest config**: `asyncio_mode = "auto"`, `markers = ["integration: ..."]`, `addopts = "-m 'not integration'"`.
- **Driver**: `psycopg[binary]>=3.1.0` is already a project dependency. Connection URL format: `postgresql+psycopg://ensemble:test_password@localhost:5432/ensemble_test`.
- **Existing engine factory**: `daemon/repositories/factory.py:146-189` (`create_postgres_engine`) reads env vars. Tests can reuse this or build their own engine.
- **Existing test count**: ~324 test files, all SQLite-based. PG tests will be a small subset.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Register `postgres` pytest marker | Add `"postgres: tests requiring a live PostgreSQL database (ensemble_test)"` to `markers` in `pyproject.toml`. Do NOT add to `addopts` filter (tests are opt-in). | `pyproject.toml` |
| 2 | Create `tests/conftest_postgres.py` | Session-scoped `pg_engine` fixture: connects to `ensemble_test` via `postgresql+psycopg://...`. Calls `SQLModel.metadata.create_all(engine)` to create schema. Yields engine. Teardown: drop all tables (or truncate). See Fixture Design below. | `tests/conftest_postgres.py` (new) |
| 3 | Add `pg_session_factory` fixture | Function-scoped fixture providing a session factory (callable) that creates sessions bound to `pg_engine`. Each test gets fresh sessions. Session-level isolation strategy: truncate all tables between tests (TRUNCATE ... RESTART IDENTITY CASCADE). | `tests/conftest_postgres.py` |
| 4 | Add `pg_repository_factory` fixture | Factory fixture that creates repository instances bound to `pg_engine` (e.g., `JobQueueRepository(pg_engine)`, `ProjectRepository(pg_engine)`). Mirrors the SQLite `repository` fixture pattern. | `tests/conftest_postgres.py` |
| 5 | Add PG skip-on-unavailable logic | If `ensemble_test` is unreachable, `pytest.skip("PostgreSQL test database not available")`. Use a session-scoped `pg_available` fixture that tries to connect once. Avoids noisy failures when PG isn't running. | `tests/conftest_postgres.py` |
| 6 | Update `docker-compose.test.yml` | Add a `healthcheck` wait condition and optional volume for persistence. Document startup: `docker compose -f docker-compose.test.yml up -d`. Add comment with pytest command: `pytest -m postgres`. | `docker-compose.test.yml` |
| 7 | Create `tests/postgres/conftest.py` | Subdirectory conftest that imports fixtures from `tests/conftest_postgres.py`. Apply `@pytest.mark.postgres` to all tests in `tests/postgres/` via `pytest_collection_modifyitems` or `pytestmark`. | `tests/postgres/conftest.py` (new) |
| 8 | Create smoke test | `tests/postgres/test_pg_smoke.py` — minimal test: connect, create_all, insert a row, query it back, verify JSONB type on a column. Proves the infrastructure works end-to-end. | `tests/postgres/test_pg_smoke.py` (new) |
| 9 | Add optional `pytest-xdist` dependency | For parallel PG test execution (PG supports concurrent connections). Add `pytest-xdist` to dev dependencies in `pyproject.toml`. Document `pytest -m postgres -n auto`. | `pyproject.toml` |

## Key Files

| File | Purpose |
|------|---------|
| `tests/conftest_postgres.py` (new) | Core PG fixtures: `pg_engine`, `pg_session_factory`, `pg_repository_factory`, `pg_available` |
| `tests/postgres/conftest.py` (new) | Subdirectory conftest: auto-marks all tests as `@pytest.mark.postgres` |
| `tests/postgres/test_pg_smoke.py` (new) | Smoke test proving infrastructure works |
| `pyproject.toml` | Add `postgres` marker, optional `pytest-xdist` |
| `docker-compose.test.yml` | Document startup, add healthcheck improvements |

## Fixture Design

### `tests/conftest_postgres.py`

```python
"""PostgreSQL test fixtures.

All fixtures here connect to the ``ensemble_test`` database.
Start it with:
    docker compose -f docker-compose.test.yml up -d

Run PG tests with:
    pytest -m postgres

If the database is unreachable, all PG tests are skipped (not failed).
"""
import os
import pytest
from sqlalchemy import create_engine, text
from sqlmodel import SQLModel


PG_TEST_URL = os.environ.get(
    "ENSEMBLE_PG_TEST_URL",
    "postgresql+psycopg://ensemble:test_password@localhost:5432/ensemble_test",
)


@pytest.fixture(scope="session")
def pg_available() -> bool:
    """Check once per session if PG test DB is reachable."""
    try:
        engine = create_engine(PG_TEST_URL, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def pg_engine(pg_available):
    """Session-scoped PG engine. Creates all tables once."""
    if not pg_available:
        pytest.skip("PostgreSQL test database not available")
    engine = create_engine(
        PG_TEST_URL,
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    # Drop all tables on session end (clean slate for next run)
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def _pg_truncate_tables(pg_engine):
    """Truncate all tables between tests for isolation.
    
    Uses TRUNCATE ... RESTART IDENTITY CASCADE for speed.
    Applied only when the pg_engine fixture is active.
    """
    yield
    with pg_engine.begin() as conn:
        # Get all table names and truncate
        result = conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ))
        tables = [row[0] for row in result]
        if tables:
            conn.execute(text(
                f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE"
            ))


@pytest.fixture
def pg_session_factory(pg_engine):
    """Factory that creates new sessions bound to pg_engine."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=pg_engine)
    return Session


@pytest.fixture
def pg_repository_factory(pg_engine, pg_session_factory):
    """Factory creating repository instances on PG."""
    # Pattern mirrors existing SQLite repository fixtures.
    # Usage: repo = pg_repository_factory(JobQueueRepository)
    def _create(repo_class, *args, **kwargs):
        session = pg_session_factory()
        return repo_class(session=session, *args, **kwargs)
    return _create
```

### Isolation Strategy Decision

**TRUNCATE between tests** (chosen) vs **transaction-rollback** vs **separate schemas**:

| Strategy | Pros | Cons |
|----------|------|------|
| TRUNCATE CASCADE | Simple, fast enough for PG, reset identity | Brief lock per test |
| Transaction rollback (savepoint) | Fastest, no cleanup | Doesn't work with multi-connection concurrency tests (Phase 3) |
| Separate schema per test | Full isolation | Complex, schema creation overhead |

**TRUNCATE is chosen** because Phase 3 concurrency tests use **separate connections**, which can't share a transaction rollback boundary. TRUNCATE is visible across connections and fast on PG.

## Constraints
- **Do NOT modify existing SQLite fixtures** — the 324 existing tests must remain unaffected.
- **PG tests are opt-in** — default `pytest` run must NOT attempt PG connection. Only `pytest -m postgres` connects.
- **No external service required for normal CI** — if PG is not running, tests SKIP, not FAIL.
- **Reuse existing engine factory patterns** — mirror `create_postgres_engine` connection settings.
- **Session isolation** — each test must see a clean database state. TRUNCATE between tests.

## Deliverables
- [ ] `tests/conftest_postgres.py` with `pg_engine`, `pg_session_factory`, `pg_repository_factory`, `pg_available` fixtures
- [ ] `tests/postgres/conftest.py` auto-marking tests
- [ ] `tests/postgres/test_pg_smoke.py` passing on real PG
- [ ] `postgres` marker registered in `pyproject.toml`
- [ ] `docker-compose.test.yml` documented with startup instructions
- [ ] `pytest` (no marker) still runs all SQLite tests, skips all PG tests
- [ ] `pytest -m postgres` runs PG tests, skips if DB unavailable
- [ ] `pytest-xdist` optionally available for `pytest -m postgres -n auto`
