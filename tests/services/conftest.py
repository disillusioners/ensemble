"""Pytest fixtures for service-layer tests that need a real DB.

Mirrors the engine setup in ``tests/repositories/conftest.py``:
a SQLite database with FK enforcement enabled and
``SQLModel.metadata.create_all`` to register the six Phase 1 skill
tables. The engine SHAPE differs from the repository-layer mirror on
purpose — see the "G — FLAKINESS STABILIZATION" section below.

This file is local to ``tests/services/`` because most service
tests use mocks (the existing ``test_skill_embedding_service.py``
suite has zero DB dependencies). Only the few that need a real
SQLAlchemy engine (e.g. ``test_skill_metrics_service.py``)
import from this conftest.

The fixtures intentionally mirror the names used by the
repository test conftest (``engine``, ``skill_repo``,
``usage_repo``, ``trigger_repo``, ``ab_test_repo``, ``project_id``)
so tests can be moved between the two layers without renaming.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool, QueuePool
from sqlmodel import SQLModel, create_engine


# ── G — FLAKINESS STABILIZATION (phase1-plan §11; decisions.md DS-29) ──
#
# The engine fixture used to be
# ``create_engine("sqlite:///:memory:", connect_args={"check_same_thread":
# False}, poolclass=StaticPool)`` — ONE shared DBAPI connection for
# every thread. ``SkillEvolutionService.check_ab_test_resolution`` fans
# its resolve-path side effects out CONCURRENTLY via
# ``asyncio.gather(asyncio.to_thread(...), ...)``
# (``daemon/services/skill_evolution_service.py:1449-1451`` and
# ``:1475-1476``), so one session's COMMIT/ROLLBACK interleaved with
# another session's transaction ON THAT SINGLE CONNECTION →
# ``session.get(Skill, skill_id)`` returned ``None`` →
# ``daemon/repositories/skill/repository.py:333`` logged "Skill not
# found for update" → the loser was NEVER deactivated → the node failed.
#
# Measured flake rate: 15% isolated / 38% in full-file-set runs
# (base-evidence pack). Decisive A/B (same tree, same interpreter
# 3.14.7, same command — ONLY the thread hop differs): normal
# ``asyncio.to_thread`` = 11P/1F, inline monkey-patch = 12P/0F.
#
# FIXTURE-LEVEL FIX (DS-29 — the PRODUCTION fan-out is correct against
# PostgreSQL, where every connection is a distinct session; the defect
# was in the harness):
#   * the database is FILE-BACKED (``tmp_path``) — ``:memory:`` plus a
#     non-shared pool would hand every connection its OWN database, so
#     the data would not be shared;
#   * the pool is ``NullPool`` — every session gets its OWN connection,
#     so concurrent operations can never interleave transactions on a
#     single connection;
#   * ``journal_mode=WAL`` + ``busy_timeout`` — the three concurrent
#     WRITE transactions (deactivate + resolve + update) queue behind
#     each other instead of failing with "database is locked".
#
# The fixture NAMES are unchanged (``engine``, ``skill_repo``, ...) so
# no consumer had to change.

#: Filename of the per-test SQLite file inside pytest's ``tmp_path``.
SERVICES_DB_FILENAME = "skill-services.db"

#: SQLite busy timeout (milliseconds) for the concurrent write fan-out.
SQLITE_BUSY_TIMEOUT_MS = 5000

#: Pool sizing — the A/B resolve fan-out opens at most 3 concurrent
#: sessions (deactivate + resolve + update); the pool hands each of them
#: its OWN connection, which is the property the flake fix depends on.
SQLITE_POOL_SIZE = 5
SQLITE_POOL_OVERFLOW = 10


def _enable_sqlite_pragmas(engine: Engine) -> None:
    """Enable FK enforcement and a busy timeout on EVERY new connection.

    Applied on every connect (the pool hands out fresh connections, so a
    per-connection pragma must be set per connection):

    * ``foreign_keys=ON`` — unchanged behaviour (the original fixture
      set exactly this).
    * ``busy_timeout`` — a writer that finds the database locked WAITS
      instead of raising "database is locked" (SQLite's default busy
      timeout is 0 = fail immediately).

    ``journal_mode=WAL`` is deliberately NOT set here: the mode is a
    property of the DATABASE FILE (it persists), so it is set once by
    the fixture instead of on every connection — re-asserting it per
    connection would take a write lock on the database header for
    nothing.
    """

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        # WAL + synchronous=NORMAL is the standard fast pairing: with
        # the default (FULL) every COMMIT fsyncs the WAL, which turns a
        # 100-connection test file into a disk-bound one. A test
        # fixture does not need power-loss durability.
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def _enable_wal_mode(engine: Engine) -> None:
    """Set ``journal_mode=WAL`` once for the database file.

    WAL lets readers proceed while a writer holds the write lock, so
    the concurrent A/B resolve fan-out (three WRITE transactions) does
    not collide with each other. The mode persists in the database
    file — every later connection inherits it.
    """
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")


@pytest.fixture
def engine(tmp_path: Path):
    """File-backed SQLite engine (one connection per session) + skill tables.

    See the "G — FLAKINESS STABILIZATION" note in the module docstring
    for why this is NOT ``:memory:`` + ``StaticPool``.
    """
    engine = create_engine(
        f"sqlite:///{tmp_path / SERVICES_DB_FILENAME}",
        connect_args={"check_same_thread": False, "timeout": 30.0},
        poolclass=QueuePool,
        pool_size=SQLITE_POOL_SIZE,
        max_overflow=SQLITE_POOL_OVERFLOW,
        pool_recycle=3600,
    )
    _enable_sqlite_pragmas(engine)
    _enable_wal_mode(engine)

    # Importing the models registers them on SQLModel.metadata so
    # ``create_all`` picks them up.
    from daemon.repositories.skill.models import (
        Skill,
        SkillABTest,
        SkillEmbedding,
        SkillLineage,
        SkillTrigger,
        SkillUsageRecord,
    )

    _ = (Skill, SkillLineage, SkillUsageRecord, SkillTrigger,
         SkillEmbedding, SkillABTest)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def project_id() -> str:
    return "test-project"


@pytest.fixture
def skill_repo(engine):
    from daemon.repositories.skill.repository import SkillRepository
    return SkillRepository(engine)


@pytest.fixture
def usage_repo(engine):
    from daemon.repositories.skill.repository import SkillUsageRepository
    return SkillUsageRepository(engine)


@pytest.fixture
def trigger_repo(engine):
    from daemon.repositories.skill.repository import SkillTriggerRepository
    return SkillTriggerRepository(engine)


@pytest.fixture
def ab_test_repo(engine):
    from daemon.repositories.skill.repository import SkillABTestRepository
    return SkillABTestRepository(engine)