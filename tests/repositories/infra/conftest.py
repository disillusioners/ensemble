"""Pytest configuration and fixtures for infra asset repository tests.

The infra asset repository has FK dependencies on the ``projects`` table
(``infra_assets.project_id`` and ``infra_asset_history.project_id`` both
reference ``projects.project_id``). Tests must therefore create the
``projects`` table before the infra tables.

We reuse the real :class:`Project` model from
``daemon.repositories.project.models`` rather than defining a stub:
the stub collides with the real ``projects`` table on the shared
global ``SQLModel.metadata`` instance.

The engine uses ``StaticPool`` so the in-memory SQLite database is
shared across threads, mirroring the production engine setup in
``daemon/repositories/factory.py``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """Enable FK enforcement on every new SQLite connection.

    SQLite disables foreign key enforcement by default
    (``PRAGMA foreign_keys = OFF``) — without this, the
    ``ON DELETE SET NULL`` on ``infra_assets.parent_asset_id`` is
    silently ignored, the ``ON DELETE CASCADE`` on the project FK
    never fires, and ``ON DELETE SET NULL`` on the history FK is
    a no-op.

    The production factory in ``daemon/repositories/factory.py``
    sets this via the same event-listener pattern. Tests need
    the same behavior to be meaningful.
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

    Uses ``StaticPool`` (per the project's standard pattern) so the
    in-memory database survives across threads. The infra models
    reference ``projects.project_id`` for two FKs, so we create the
    ``projects`` table first via ``SQLModel.metadata.create_all``,
    which creates every table that's already been imported
    (including the real ``Project`` model and the infra tables).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _enable_sqlite_foreign_keys(engine)
    # Importing the infra module pulls in the daemon-level __init__ chain
    # which registers the real ``Project`` table on ``SQLModel.metadata``.
    # ``create_all`` creates every table currently registered, including
    # ``projects`` and the three infra tables.
    from daemon.repositories.infra.models import (
        InfraAsset,
        InfraAssetHistory,
        InfraAssetType,
    )

    # Ensure all three infra tables are registered on SQLModel.metadata.
    # The Project table is already registered from the daemon import chain.
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
    """Insert two project rows so the FK is satisfied for cross-project tests.

    Returns a dict with both IDs. The default ``project_id`` fixture
    points at the first one — tests that need the second should
    request ``other_project_id`` explicitly.
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
