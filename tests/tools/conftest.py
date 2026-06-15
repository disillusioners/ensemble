"""Pytest configuration and fixtures for infra tool tests.

The infra tool tests live in ``tests/tools/`` (a sibling of
``tests/repositories/``), so they do not automatically pick up the
infra conftest from ``tests/repositories/infra/conftest.py``.
We re-declare the engine / repository / project-seed fixtures here
to keep the test files in this directory self-contained.

Conventions mirror ``tests/repositories/infra/conftest.py``:

* In-memory SQLite with ``StaticPool`` (so the in-memory DB
  survives across threads) and FK enforcement enabled.
* The ``projects`` table must exist before any infra table
  because ``infra_assets.project_id`` and
  ``infra_asset_history.project_id`` both reference it.
* We re-use the real :class:`Project` model from
  ``daemon.repositories.project.models`` (a stub would collide
  with the real table on the shared ``SQLModel.metadata``).
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """Enable FK enforcement on every new SQLite connection.

    Mirrors the production factory in
    ``daemon/repositories/factory.py``: SQLite defaults to
    ``PRAGMA foreign_keys=OFF``, which would silently ignore
    the ``ON DELETE SET NULL`` on ``infra_assets.parent_asset_id``
    and the FK to ``projects``.
    """

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# ---------------------------------------------------------------------
# Engine + tables fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def engine():
    """In-memory SQLite engine with all infra tables created.

    Uses ``StaticPool`` so the in-memory database survives across
    threads, mirroring the production engine setup. Importing
    the infra module pulls in the daemon-level ``__init__`` chain
    that registers the real ``Project`` table on
    ``SQLModel.metadata``; ``create_all`` then creates every
    currently-registered table (including ``projects`` and the
    three infra tables).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _enable_sqlite_foreign_keys(engine)

    from daemon.repositories.infra.models import (
        InfraAsset,
        InfraAssetHistory,
        InfraAssetType,
    )

    # Touch the models so they register on SQLModel.metadata.
    _ = (InfraAsset, InfraAssetHistory, InfraAssetType)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def infra_repository(engine):
    """A :class:`SQLModelInfraRepository` bound to the test engine."""
    from daemon.repositories.infra import SQLModelInfraRepository

    return SQLModelInfraRepository(engine)


# ---------------------------------------------------------------------
# Project seeding helpers
# ---------------------------------------------------------------------


@pytest.fixture
def project_id() -> str:
    """Default project_id used by most tests."""
    return "test-project"


@pytest.fixture
def other_project_id() -> str:
    """Second project_id used by isolation tests."""
    return "other-project"


@pytest.fixture
def seed_projects(engine, project_id, other_project_id):
    """Insert two project rows so the FK is satisfied.

    Returns a dict with both IDs. The default ``project_id``
    fixture points at the first one; tests that need the second
    should request ``other_project_id`` explicitly.
    """
    from daemon.repositories.project.models import Project

    with Session(engine) as session:
        session.add(
            Project(
                project_id=project_id,
                name="Test Project",
                project_type="general",
            )
        )
        session.add(
            Project(
                project_id=other_project_id,
                name="Other Project",
                project_type="general",
            )
        )
        session.commit()
    return {
        "project_id": project_id,
        "other_project_id": other_project_id,
    }
