"""PostgreSQL test fixtures — opt-in test infrastructure.

This conftest lives in ``tests/postgres/`` so its autouse TRUNCATE fixture
does NOT cascade to the 8000+ SQLite tests in the rest of the suite.
Tests under this directory are opt-in via ``pytest -m postgres`` (see
``addopts`` in ``pyproject.toml``).
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlmodel import Session, SQLModel

try:
    import psycopg.errors as pgerrs
except ImportError:  # pragma: no cover - psycopg3 is always a project dep
    pgerrs = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Ensure all SQLModel table classes are registered in metadata before
# ``pg_engine`` calls ``SQLModel.metadata.create_all``. Without these
# imports, ``create_all`` produces an empty schema and the autouse
# ``_pg_truncate_tables`` fixture below fails on
# ``UndefinedTable: relation "..." does not exist`` for any model
# registered by test-file imports after the session-scoped engine is built.
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.db_connection.models  # noqa: F401
import daemon.repositories.execution_lease.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.mcp_server.models  # noqa: F401
import daemon.repositories.infra.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.source.models  # noqa: F401
import daemon.migrations.models  # noqa: F401

# Default PG connection — overridable via env vars for CI / Docker setups.
# Matches ``docker-compose.test.yml`` (user/pass: ensemble/ensemble_dev).
PG_HOST = os.environ.get("PG_TEST_HOST", "localhost")
PG_PORT = int(os.environ.get("PG_TEST_PORT", "5432"))
PG_DB = os.environ.get("PG_TEST_DB", "ensemble_test")
PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")

PG_URL = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"


# Auto-apply the ``postgres`` marker to every test collected in this
# directory so ``pytest -m postgres`` selects them and the default
# ``-m 'not integration and not postgres'`` addopts skips them.
def pytest_collection_modifyitems(config, items):
    postgres_marker = pytest.mark.postgres
    for item in items:
        # Restrict to tests under tests/postgres/ — anything else that
        # happens to import this conftest via parent-directory inheritance
        # is left alone.
        if "tests/postgres/" in str(item.fspath):
            item.add_marker(postgres_marker)


# PostgreSQL SQLSTATE codes that indicate an authentication failure (as
# opposed to "PG is down"). Surfacing these distinctly helps users tell
# "wrong password" from "service not running" without flipping a server.
_AUTH_SQLSTATES = {"28000", "28P01"}
_AUTH_MESSAGE_HINTS = (
    "password authentication failed",
    "authentication failed",
)


def _is_auth_failure(exc: BaseException) -> bool:
    """True if the given DBAPI/OperationalError is a PG auth failure."""
    orig = getattr(exc, "orig", exc)
    # psycopg3-native class check (most reliable)
    if pgerrs is not None and isinstance(
        orig, (pgerrs.InvalidPassword, pgerrs.InvalidAuthorizationSpecification)
    ):
        return True
    # Fallback: SQLSTATE on the diag object or direct attribute
    sqlstate = getattr(getattr(orig, "diag", None), "sqlstate", None) or getattr(
        orig, "sqlstate", None
    )
    if sqlstate in _AUTH_SQLSTATES:
        return True
    # Last-resort string match
    msg = str(exc).lower()
    return any(hint in msg for hint in _AUTH_MESSAGE_HINTS)


def _probe_postgres(url: str) -> Engine | None:
    """Return a connected Engine, or None if PostgreSQL is unreachable.

    Skips the whole test session cleanly when PG is unavailable — never
    errors. Distinguishes authentication failures from "PG is down" so
    the skip message is diagnostic instead of misleading.
    """
    try:
        engine = create_engine(url, pool_pre_ping=True, future=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except OperationalError as exc:
        # Connection-level failure. Could be "PG not running" or "wrong
        # password" — split them so the user gets a useful skip reason.
        if _is_auth_failure(exc):
            logger.warning(
                "PostgreSQL auth failed for %s: %s "
                "(check PG_TEST_USER / PG_TEST_PASSWORD)",
                url, exc,
            )
            return None
        logger.warning("PostgreSQL unreachable at %s: %s", url, exc)
        return None
    except DBAPIError as exc:
        logger.warning("PostgreSQL DBAPI error for %s: %s", url, exc)
        return None
    except Exception as exc:  # pragma: no cover - environment-dependent
        logger.warning("PostgreSQL probe failed unexpectedly for %s: %s", url, exc)
        return None


@pytest.fixture(scope="session")
def pg_engine():
    """Session-scoped PostgreSQL engine.

    Skips the entire module if PostgreSQL is unreachable. Creates the full
    SQLModel schema on setup and drops it on teardown so the test database
    is left clean.
    """
    engine = _probe_postgres(PG_URL)
    if engine is None:
        pytest.skip(f"PostgreSQL not available at {PG_URL}")

    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        try:
            SQLModel.metadata.drop_all(engine)
        finally:
            engine.dispose()


@pytest.fixture
def pg_session_factory(pg_engine):
    """Function-scoped factory returning a SQLModel ``Session``.

    SQLModel's ``Session`` exposes the ``.exec()`` convenience API used
    throughout the daemon's repositories, so callers can do
    ``session.exec(select(...)).all()`` directly. Each ``with`` block
    opens and closes a session on the shared engine.
    """

    @contextmanager
    def _create():
        session = Session(pg_engine)
        try:
            yield session
        finally:
            session.close()

    return _create


@pytest.fixture
def pg_repository_factory(pg_engine):
    """Factory for repository instances bound to the PG engine.

    Repositories take ``engine`` positionally (verified against
    ``daemon/repositories/factory.py`` and every ``__init__`` signature in
    ``daemon/repositories/**/repository.py``). Example::

        job_repo = pg_repository_factory(JobRepository)
        project_repo = pg_repository_factory(SQLModelProjectRepository)

    For repositories that need extra kwargs (e.g. ``TaskRepository`` takes
    ``on_pending_task``), pass them positionally / by keyword after the
    factory's first arg.
    """
    def _create(repo_cls, *args, **kwargs):
        return repo_cls(pg_engine, *args, **kwargs)

    return _create


@pytest.fixture(autouse=True)
def _pg_truncate_tables(pg_engine):
    """TRUNCATE every SQLModel table before each PG test.

    ``TRUNCATE ... RESTART IDENTITY CASCADE`` handles foreign-key cascades
    automatically and resets auto-increment sequences, so we don't need to
    disable FK triggers (which requires a superuser-set
    ``session_replication_role``). Truncating in reverse metadata order is
    a belt-and-braces measure for any FK cycles that survive CASCADE.
    """
    tables = [t.name for t in reversed(SQLModel.metadata.sorted_tables)]
    if not tables:
        yield
        return

    with pg_engine.begin() as conn:
        joined = ", ".join(f'"{name}"' for name in tables)
        conn.execute(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def pg_two_connections(pg_engine):
    """Yield two independent connections for Phase 3 concurrency tests.

    Each call opens a fresh pair from the shared engine pool so the two
    connections have separate transaction state. Use to simulate two
    competing workers racing on the same row::

        with pg_two_connections() as (conn_a, conn_b):
            ...
    """
    @contextmanager
    def _create():
        conn1 = pg_engine.connect()
        conn2 = pg_engine.connect()
        try:
            yield conn1, conn2
        finally:
            conn1.close()
            conn2.close()

    return _create


@pytest.fixture
def pg_url() -> str:
    """Expose the PG connection URL for tests that build their own engine."""
    return PG_URL