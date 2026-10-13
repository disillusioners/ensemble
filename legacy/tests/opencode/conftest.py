"""Shared fixtures for the opencode test suite.

Two fixtures are exposed:

- ``sqlite_engine`` — An in-memory SQLite engine with **only** the
  ``opencode_sessions`` table created.  We deliberately call
  ``OpenCodeSessionRecord.__table__.create(engine, checkfirst=True)``
  instead of ``SQLModel.metadata.create_all(engine)`` so the test engine
  does NOT inherit the 22+ ensemble tables (instances, projects, jobs,
  message_queue, …) that the global ``SQLModel.metadata`` registry knows
  about.  This is the same constraint the production factory function
  ``create_opencode_session_repository()`` upholds.

A convenience fixture ``repository`` is also provided for tests that
exercise the repository directly without wanting to repeat the
``OpenCodeSessionRepository(engine)`` setup.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


@pytest.fixture
def sqlite_engine() -> Engine:
    """In-memory SQLite engine with ONLY the ``opencode_sessions`` table.

    Uses ``check_same_thread=False`` so the same engine can be reached
    from multiple threads (matches the production settings in
    ``create_opencode_session_repository``).

    The dedicated ``__table__.create()`` call is intentional — see the
    module docstring for why ``SQLModel.metadata.create_all`` is not
    appropriate here.
    """
    # Imported lazily so the test file's own imports stay cheap and so
    # this fixture can be used by tests that don't otherwise need the
    # repository module.
    from daemon.opencode.repository import OpenCodeSessionRecord

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    OpenCodeSessionRecord.__table__.create(engine, checkfirst=True)
    yield engine
    engine.dispose()


@pytest.fixture
def repository(sqlite_engine):
    """``OpenCodeSessionRepository`` wired to ``sqlite_engine``."""
    from daemon.opencode.repository import OpenCodeSessionRepository

    return OpenCodeSessionRepository(sqlite_engine)
