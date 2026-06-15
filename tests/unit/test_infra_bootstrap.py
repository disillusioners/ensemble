"""Tests for ``daemon.manager.InstanceManager._bootstrap_infra_types``.

Phase 1.5 of the infra info storage design seeds 9 default type
definitions on daemon startup. The bootstrap must:

1. Be called from the ``InstanceManager.__init__`` constructor.
2. Call ``repository.bootstrap_default_types()`` to seed the 9
   built-in types defined in
   :data:`~daemon.repositories.infra.types.INFRA_TYPE_DEFINITIONS`.
3. Be **idempotent** — invoking it twice does not duplicate rows.
4. **Handle repository errors gracefully** (try/except) so a
   bootstrap failure does not block daemon startup.

These tests cover the wiring in :mod:`daemon.manager`. The
underlying ``bootstrap_default_types`` repository method is
already covered by
``tests/repositories/infra/test_infra_repository.py::TestBootstrapDefaultTypes``.

Why a stub manager? Building a full ``InstanceManager`` requires a
working LLM config, MCP service pool, migration runner, and more —
far too much surface area for a unit test of one bootstrap method.
Instead we construct a ``SimpleNamespace`` that quacks like the
manager for the duration of one method call. The
:class:`TestBootstrapCalledInConstructor` static check confirms
the method is actually invoked from the real ``__init__`` body.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from daemon.repositories.infra import (
    INFRA_TYPE_DEFINITIONS,
    SQLModelInfraRepository,
)


# These names match the 9 built-in types declared in
# daemon/repositories/infra/types.py::INFRA_TYPE_DEFINITIONS.
# Keep this set in sync with that module — if a new type is added
# or removed, both must change.
EXPECTED_TYPE_NAMES: set[str] = {
    "datacenter",
    "server",
    "rack",
    "k8s_cluster",
    "k8s_node",
    "network",
    "load_balancer",
    "database",
    "storage",
}


# =============================================================================
# Shared fixtures
# =============================================================================


@pytest.fixture
def sqlite_engine():
    """In-memory SQLite engine with the infra tables created.

    Mirrors the engine used by ``tests/repositories/infra/conftest.py``.
    Uses ``StaticPool`` so the connection survives across threads.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Importing the infra models registers them on SQLModel.metadata;
    # ``create_all`` then materializes the three infra tables.
    from daemon.repositories.infra.models import (
        InfraAsset,
        InfraAssetHistory,
        InfraAssetType,
    )

    _ = (InfraAsset, InfraAssetHistory, InfraAssetType)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def infra_repository(sqlite_engine):
    """A :class:`SQLModelInfraRepository` bound to the test engine."""
    return SQLModelInfraRepository(sqlite_engine)


@pytest.fixture
def stub_manager(infra_repository):
    """A bare InstanceManager-like object exposing only ``_bootstrap_infra_types``.

    The real ``InstanceManager.__init__`` does a lot of heavy lifting
    (LLM semaphore, MCP service pool, migrations, …) that is unrelated
    to the bootstrap wiring being tested. This stub exposes only the
    ``_infra_repository`` attribute that ``_bootstrap_infra_types``
    actually reads, with the real method bound to it.
    """
    from daemon.manager import InstanceManager

    stub = SimpleNamespace(_infra_repository=infra_repository)
    # Bind the real bootstrap method so we exercise the real code path.
    stub._bootstrap_infra_types = InstanceManager._bootstrap_infra_types.__get__(
        stub, type(stub)
    )
    return stub


# =============================================================================
# Source-level static checks
# =============================================================================


class TestBootstrapCalledInConstructor:
    """Static checks that ``_bootstrap_infra_types()`` is invoked from
    ``InstanceManager.__init__`` and positioned correctly.
    """

    MANAGER_PATH: Path = (
        Path(__file__).parent.parent.parent / "daemon" / "manager.py"
    )

    def test_bootstrap_infra_types_called_in_init(self):
        """``self._bootstrap_infra_types()`` appears in the ``__init__`` body."""
        manager_source = self.MANAGER_PATH.read_text()

        # The ``__init__`` signature is multi-line, so we can't rely on
        # a one-shot regex. Instead, locate the start of ``__init__`` and
        # capture forward until the next sibling method definition
        # (``def _bootstrap_builtin_servers`` is the method that
        # immediately follows ``__init__`` in daemon/manager.py).
        init_start = manager_source.find("def __init__(")
        assert init_start != -1, "Could not find def __init__( in daemon/manager.py"

        next_method_pos = manager_source.find(
            "def _bootstrap_builtin_servers", init_start
        )
        assert next_method_pos != -1, (
            "Could not find the method that follows __init__ in "
            "daemon/manager.py — has the file been refactored?"
        )

        body = manager_source[init_start:next_method_pos]
        assert "self._bootstrap_infra_types()" in body, (
            "InstanceManager.__init__ must call self._bootstrap_infra_types() — "
            "Phase 1.5 of the infra info storage design requires the daemon "
            "to seed the 9 default infra asset types on startup."
        )

    def test_bootstrap_call_positioned_correctly(self):
        """The bootstrap call sits AFTER ``_infra_repository`` is set
        and BEFORE service initialization.
        """
        manager_source = self.MANAGER_PATH.read_text()

        repo_pos = manager_source.find(
            "self._infra_repository = create_infra_repository"
        )
        bootstrap_pos = manager_source.find("self._bootstrap_infra_types()")
        # ``CancellationService`` is the first service constructed
        # after the bootstrap, so it makes a reliable lower bound.
        service_pos = manager_source.find(
            "self._cancellation_service = CancellationService"
        )

        assert repo_pos != -1, "_infra_repository assignment not found"
        assert bootstrap_pos != -1, "_bootstrap_infra_types() call not found"
        assert service_pos != -1, "service init (CancellationService) not found"

        assert repo_pos < bootstrap_pos < service_pos, (
            "_bootstrap_infra_types() must run AFTER _infra_repository is "
            "constructed (it needs the repo to call) and BEFORE service "
            "initialization (services may want to query the type registry)."
        )


# =============================================================================
# Functional tests for _bootstrap_infra_types
# =============================================================================


class TestBootstrapInfraTypes:
    """Behavioral tests for the ``_bootstrap_infra_types`` method."""

    def test_seeds_all_nine_types_on_fresh_db(
        self, stub_manager, infra_repository
    ):
        """On a fresh DB the bootstrap inserts all 9 built-in types."""
        # Sanity: no types exist before bootstrap.
        assert infra_repository.list_types() == []

        # Run the bootstrap.
        stub_manager._bootstrap_infra_types()

        # All 9 built-in types should now be present.
        types = infra_repository.list_types()
        assert {t.name for t in types} == EXPECTED_TYPE_NAMES
        assert len(types) == 9

    def test_definition_count_matches_expectation(self):
        """``INFRA_TYPE_DEFINITIONS`` has exactly 9 entries and they
        match ``EXPECTED_TYPE_NAMES``.
        """
        declared = {d.type_name for d in INFRA_TYPE_DEFINITIONS}
        assert declared == EXPECTED_TYPE_NAMES, (
            f"INFRA_TYPE_DEFINITIONS ({declared}) does not match "
            f"EXPECTED_TYPE_NAMES ({EXPECTED_TYPE_NAMES}) — update both."
        )
        assert len(INFRA_TYPE_DEFINITIONS) == 9

    def test_idempotent_runs_twice_no_duplicates(
        self, stub_manager, infra_repository
    ):
        """Calling ``_bootstrap_infra_types`` twice does not duplicate rows."""
        stub_manager._bootstrap_infra_types()
        first_count = len(infra_repository.list_types())
        assert first_count == 9

        # Second invocation must be a no-op for row counts.
        stub_manager._bootstrap_infra_types()
        second_count = len(infra_repository.list_types())
        assert second_count == 9
        assert first_count == second_count

    def test_idempotent_keeps_same_row_ids(
        self, stub_manager, infra_repository
    ):
        """Idempotency reuses the existing primary keys, not new ones."""
        stub_manager._bootstrap_infra_types()
        first_ids = {t.name: t.updated_at for t in infra_repository.list_types()}

        stub_manager._bootstrap_infra_types()
        second_types = infra_repository.list_types()
        second_ids = {t.name: t.updated_at for t in second_types}

        # Same names, same row count. ``updated_at`` may move forward
        # because ``register_type`` bumps it on every call, but the
        # primary keys are stable.
        assert set(first_ids.keys()) == set(second_ids.keys()) == EXPECTED_TYPE_NAMES
        assert len(second_types) == 9

    def test_handles_repository_error_gracefully(self, infra_repository, caplog):
        """If the repository raises, the bootstrap logs and returns
        without re-raising — daemon startup must not crash.
        """
        from daemon.manager import InstanceManager

        broken_repo = MagicMock(spec=SQLModelInfraRepository)
        broken_repo.bootstrap_default_types.side_effect = RuntimeError("boom")
        stub = SimpleNamespace(_infra_repository=broken_repo)
        stub._bootstrap_infra_types = InstanceManager._bootstrap_infra_types.__get__(
            stub, type(stub)
        )

        with caplog.at_level(logging.ERROR, logger="daemon.manager"):
            # Must NOT raise.
            stub._bootstrap_infra_types()

        # The error must have been logged with enough context to debug.
        assert any(
            "Failed to bootstrap default infra asset types" in rec.message
            for rec in caplog.records
        ), (
            "Expected an error log containing 'Failed to bootstrap default "
            f"infra asset types', got: {[r.message for r in caplog.records]}"
        )

        # The repository must have been invoked exactly once before the
        # error path returns — never re-tried inside the method.
        assert broken_repo.bootstrap_default_types.call_count == 1

    def test_logs_seeded_count_on_fresh_db(self, infra_repository, caplog):
        """On a fresh DB the bootstrap logs an INFO message with the
        number of new types it just inserted.
        """
        from daemon.manager import InstanceManager

        stub = SimpleNamespace(_infra_repository=infra_repository)
        stub._bootstrap_infra_types = InstanceManager._bootstrap_infra_types.__get__(
            stub, type(stub)
        )

        with caplog.at_level(logging.INFO, logger="daemon.manager"):
            stub._bootstrap_infra_types()

        # ``new_count=9`` on a fresh DB triggers the "Seeded N new ..."
        # INFO log (see daemon/manager.py::_bootstrap_infra_types).
        assert any(
            "new infra asset types" in rec.message
            for rec in caplog.records
        ), (
            "Expected an info log containing 'new infra asset types', "
            f"got: {[r.message for r in caplog.records]}"
        )

    def test_logs_only_debug_when_already_seeded(
        self, infra_repository, caplog
    ):
        """On a second run (no new types) the bootstrap logs at DEBUG,
        not INFO — the INFO path is reserved for "first time" events.
        """
        from daemon.manager import InstanceManager

        stub = SimpleNamespace(_infra_repository=infra_repository)
        stub._bootstrap_infra_types = InstanceManager._bootstrap_infra_types.__get__(
            stub, type(stub)
        )

        # Prime the registry.
        stub._bootstrap_infra_types()
        caplog.clear()

        with caplog.at_level(logging.DEBUG, logger="daemon.manager"):
            stub._bootstrap_infra_types()

        # No new INFO log about seeding — the second run is idempotent.
        assert not any(
            rec.levelno == logging.INFO
            and "new infra asset types" in rec.message
            for rec in caplog.records
        ), (
            "Second bootstrap should not log 'new infra asset types' at "
            "INFO level — only on the first run."
        )
        # And it should emit a DEBUG line saying types are already there.
        assert any(
            rec.levelno == logging.DEBUG
            and "already registered" in rec.message
            for rec in caplog.records
        ), (
            "Expected a DEBUG log containing 'already registered' on the "
            f"second run, got: {[r.message for r in caplog.records]}"
        )

    def test_schemas_match_infra_type_definitions(
        self, stub_manager, infra_repository
    ):
        """Each bootstrapped type has the ``schema_doc`` and
        ``description`` declared in ``INFRA_TYPE_DEFINITIONS``.
        """
        stub_manager._bootstrap_infra_types()

        expected = {d.type_name: d for d in INFRA_TYPE_DEFINITIONS}
        for t in infra_repository.list_types():
            assert t.name in expected, f"Unexpected type seeded: {t.name}"
            assert t.schema_doc == expected[t.name].schema_doc, (
                f"Schema mismatch for type {t.name!r}"
            )
            assert t.description == expected[t.name].description, (
                f"Description mismatch for type {t.name!r}"
            )
