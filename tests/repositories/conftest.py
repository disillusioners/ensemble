"""Pytest configuration and fixtures for skill repository tests.

The skill repository tests run against an in-memory SQLite database (via
``StaticPool``) so they are fast and isolated from any other test or the
production database. All six skill tables (``skills``, ``skill_lineage``,
``skill_usage_records``, ``skill_triggers``, ``skill_embeddings``,
``skill_ab_tests``) are created at fixture-setup time.

We enable SQLite foreign-key enforcement so the ``ON DELETE CASCADE``
constraints declared on ``skill_lineage``, ``skill_usage_records``,
``skill_embeddings``, and ``skill_ab_tests`` actually fire — without
this pragma, SQLite silently ignores FK actions, masking FK-related
bugs in the repositories.

Mirrors the engine-setup pattern used in ``tests/repositories/infra/conftest.py``
(StaticPool + FK pragma + SQLModel.metadata.create_all) so the skill
tests follow the same conventions as the existing repository tests.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """Enable FK enforcement on every new SQLite connection.

    SQLite disables foreign key enforcement by default — without this,
    the ``ON DELETE CASCADE`` on the skill FKs (lineage, usage_records,
    embeddings, ab_tests) would silently be ignored, masking bugs in
    the cascading-delete paths.
    """

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# ---------------------------------------------------------------------
# Engine + repository fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def engine():
    """In-memory SQLite engine with all six skill tables created.

    Uses ``StaticPool`` (per the project's standard pattern) so the
    in-memory database survives across threads. ``SQLModel.metadata.create_all``
    creates every table currently registered on the global SQLModel
    metadata (the six skill tables plus any others pulled in by the
    import chain).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _enable_sqlite_foreign_keys(engine)

    # Importing the skill module registers all six tables on
    # SQLModel.metadata so ``create_all`` picks them up.
    from daemon.repositories.skill.models import (
        Skill,
        SkillABTest,
        SkillEmbedding,
        SkillLineage,
        SkillTrigger,
        SkillUsageRecord,
    )

    # Reference the models so static analyzers don't flag them as
    # unused — they need to be imported for create_all to register
    # their tables.
    _ = (Skill, SkillLineage, SkillUsageRecord, SkillTrigger,
         SkillEmbedding, SkillABTest)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def skill_repo(engine):
    """A :class:`SkillRepository` bound to the test engine."""
    from daemon.repositories.skill.repository import SkillRepository

    return SkillRepository(engine)


@pytest.fixture
def lineage_repo(engine):
    """A :class:`SkillLineageRepository` bound to the test engine."""
    from daemon.repositories.skill.repository import SkillLineageRepository

    return SkillLineageRepository(engine)


@pytest.fixture
def usage_repo(engine):
    """A :class:`SkillUsageRepository` bound to the test engine."""
    from daemon.repositories.skill.repository import SkillUsageRepository

    return SkillUsageRepository(engine)


@pytest.fixture
def trigger_repo(engine):
    """A :class:`SkillTriggerRepository` bound to the test engine."""
    from daemon.repositories.skill.repository import SkillTriggerRepository

    return SkillTriggerRepository(engine)


@pytest.fixture
def embedding_repo(engine):
    """A :class:`SkillEmbeddingRepository` bound to the test engine."""
    from daemon.repositories.skill.repository import SkillEmbeddingRepository

    return SkillEmbeddingRepository(engine)


@pytest.fixture
def ab_test_repo(engine):
    """A :class:`SkillABTestRepository` bound to the test engine."""
    from daemon.repositories.skill.repository import SkillABTestRepository

    return SkillABTestRepository(engine)


# ---------------------------------------------------------------------
# Project-id helpers
# ---------------------------------------------------------------------


@pytest.fixture
def project_id() -> str:
    """Default project_id used by most tests."""
    return "test-project"


@pytest.fixture
def other_project_id() -> str:
    """Second project_id used by isolation tests."""
    return "other-project"