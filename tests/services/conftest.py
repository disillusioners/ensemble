"""Pytest fixtures for service-layer tests that need a real DB.

Mirrors the engine setup in ``tests/repositories/conftest.py``:
in-memory SQLite with ``StaticPool``, FK enforcement enabled,
and ``SQLModel.metadata.create_all`` to register the six
Phase 1 skill tables.

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

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """Enable FK enforcement on every new SQLite connection."""

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def engine():
    """In-memory SQLite engine with the six skill tables created."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _enable_sqlite_foreign_keys(engine)

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