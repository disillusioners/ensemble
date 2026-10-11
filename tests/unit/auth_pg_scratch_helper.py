"""Scratch-PostgreSQL helper for the auth-store tests (oauth2-auth P2).

Repo convention (G2.1 note): repository tests run against a REAL
database. The house PG-test convention lives in
``tests/postgres/conftest.py`` (directory-scoped fixtures, opt-in
``-m postgres``, serial-only). The auth tests need a LIGHTER slice —
only the four auth tables, runnable from BOTH ``tests/unit/`` (the
dual-backend legs) and the default suite — so this helper mirrors the
conftest's connection discipline without its whole-schema session
scope:

* **Scratch coordinates only**: the URL comes from the ``PG_TEST_*``
  env vars (same defaults as ``tests/postgres/conftest.py``:
  ``ensemble_test`` on localhost:5432 — the docker-compose.test.yml
  scratch database). ``POSTGRES_*`` / ``DATABASE_URL`` are NEVER read
  here, so ambient live coordinates (e.g. a shell carrying
  ``POSTGRES_DB=ensemble_prod``) cannot redirect the engine. A
  defensive tripwire refuses to hand out an engine whose host matches
  the ambient ``POSTGRES_HOST`` (fail-closed against env poison).
* **Skip-cleanly**: an unreachable scratch PG skips the calling test
  (same UX as ``tests/postgres``) — the SQLite leg still runs.
* **Auth-tables-only scope**: setup drops + recreates ONLY the four
  auth tables (``tables=[...]`` create_all) and teardown drops them —
  the shared ``ensemble_test`` scratch DB is otherwise untouched.
* **xdist guard**: the PostgreSQL leg SKIPS under ``pytest -n auto``
  (``PYTEST_XDIST_WORKER`` set) — the shared scratch DB + fixed table
  names cannot take concurrent DROP/CREATE across workers. Mirror of
  ``tests/postgres/conftest.py``; the SQLite legs stay race-free
  (per-test ``tmp_path`` files) and still run in parallel.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel

from daemon.repositories.auth_session.models import (  # noqa: F401
    AuthBootstrapConsumption,
    AuthSession,
    AuthState,
    BearerToken,
)

AUTH_TABLES = [
    AuthSession.__table__,
    AuthState.__table__,
    BearerToken.__table__,
    AuthBootstrapConsumption.__table__,
]
AUTH_TABLE_NAMES = [t.name for t in AUTH_TABLES]

# Same defaults as tests/postgres/conftest.py (docker-compose.test.yml).
PG_HOST = os.environ.get("PG_TEST_HOST", "localhost")
PG_PORT = int(os.environ.get("PG_TEST_PORT", "5432"))
PG_DB = os.environ.get("PG_TEST_DB", "ensemble_test")
PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")

PG_URL = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"

# --- xdist guard (mirror of tests/postgres/conftest.py:27-95) --------------
# This helper shares ONE scratch database + the SAME four table names
# across every worker; setup DROPs + CREATEs them per call, so concurrent
# workers race (UndefinedTable / UniqueViolation / DuplicateTable — the
# exact symptom set the 2026-10-11 final merge gate captured under
# ``pytest -n auto``). The sibling PG lane skips its tests at collection
# time under xdist; this helper has no conftest hook, so the same guard
# lives at the single chokepoint every PG leg flows through
# (``scratch_pg_engine_or_skip``). Solo behavior is UNCHANGED (no
# ``PYTEST_XDIST_WORKER`` ⇒ no skip); the SQLite legs are per-test
# ``tmp_path`` files, stay race-free, and still run under ``-n auto``.
_XDIST_WORKER_ENV = "PYTEST_XDIST_WORKER"


def _tripwire() -> None:
    """Fail-closed guard against ambient env poison.

    If the ambient shell carries ``POSTGRES_HOST`` (e.g. a live-pointing
    deployment shell) and it equals our resolved scratch host, REFUSE —
    the test legs must never be one config mistake away from a live
    database. ``ensemble_prod``/``ensemble_dev``/``ensemble_sandbox`` DB
    names are likewise refused outright.
    """
    forbidden_dbs = {"ensemble_prod", "ensemble_dev", "ensemble_sandbox"}
    if PG_DB.lower() in forbidden_dbs:
        raise RuntimeError(
            f"auth test helper resolved a FORBIDDEN database name {PG_DB!r} "
            "(scratch-only lane: set PG_TEST_DB to a scratch database)"
        )
    ambient_host = os.environ.get("POSTGRES_HOST")
    if ambient_host and ambient_host == PG_HOST:
        raise RuntimeError(
            f"auth test helper host {PG_HOST!r} equals the ambient "
            "POSTGRES_HOST — refusing (env-poison tripwire; override with "
            "PG_TEST_HOST to an explicit scratch host)"
        )


def scratch_pg_engine_or_skip() -> Engine:
    """Engine on the scratch PG with ONLY the auth tables created.

    Skips (pytest.skip) when PG is unreachable. Drop+create per call so
    every test starts from a clean slate (create_all is CREATE-only; a
    stale schema from a crashed prior session would otherwise leak).
    """
    if _XDIST_WORKER_ENV in os.environ:
        # xdist guard (see block above) — the shared scratch DB + fixed
        # table names cannot take concurrent DROP/CREATE. Skip the PG
        # leg the same way the sibling ``tests/postgres`` lane does.
        pytest.skip(
            "auth scratch-PG tests cannot run under pytest-xdist (shared "
            "scratch DB + fixed table names race on DROP/CREATE). Run "
            "serially, e.g. pytest <auth files> -p no:cacheprovider "
            "(no -n); mirror of tests/postgres/conftest.py xdist guard."
        )
    _tripwire()
    engine = create_engine(PG_URL, pool_pre_ping=True, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — unreachable ⇒ clean skip
        engine.dispose()
        pytest.skip(f"scratch PostgreSQL not available at {PG_URL}: {exc}")

    _drop_auth_tables(engine)
    SQLModel.metadata.create_all(engine, tables=AUTH_TABLES)
    return engine


def _drop_auth_tables(engine: Engine) -> None:
    with engine.begin() as conn:
        for name in AUTH_TABLE_NAMES:
            conn.execute(text(f'DROP TABLE IF EXISTS "{name}" CASCADE'))


@contextmanager
def scratch_pg_auth_tables() -> Iterator[Engine]:
    """Context-managed variant: teardown drops the four auth tables."""
    engine = scratch_pg_engine_or_skip()
    try:
        yield engine
    finally:
        try:
            _drop_auth_tables(engine)
        finally:
            engine.dispose()


def sqlite_engine(tmp_path) -> Engine:
    """File-backed scratch SQLite engine (file, not :memory: — multiple
    short-lived Sessions must see one database)."""
    return create_engine(f"sqlite:///{tmp_path}/auth_test.db", future=True)


@pytest.fixture(params=["sqlite", "postgres"])
def dual_backend_engine(request, tmp_path):
    """Parametrized engine fixture: scratch SQLite + scratch PostgreSQL.

    The SQLite leg always runs; the PostgreSQL leg mirrors the
    ``tests/postgres`` convention (real DB, skip-clean when
    unreachable). Import into a test module with
    ``from tests.unit.auth_pg_scratch_helper import dual_backend_engine
    # noqa: F401`` to register it.
    """
    if request.param == "sqlite":
        engine = sqlite_engine(tmp_path)
        SQLModel.metadata.create_all(engine, tables=AUTH_TABLES)
        try:
            yield engine
        finally:
            engine.dispose()
    else:
        with scratch_pg_auth_tables() as engine:
            yield engine


__all__ = [
    "AUTH_TABLE_NAMES",
    "AUTH_TABLES",
    "PG_URL",
    "dual_backend_engine",
    "scratch_pg_auth_tables",
    "scratch_pg_engine_or_skip",
    "sqlite_engine",
]
