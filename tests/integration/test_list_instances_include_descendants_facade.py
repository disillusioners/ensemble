"""REAL-dispatch integration test: ``InstanceManager.list_instances`` must
thread the new ``include_descendants`` kwarg ALL THE WAY THROUGH the real
facade → real lifecycle service → real ``SQLModelInstanceRepository``
(poll-spam fix, 2026-09-09).

What this file proves over a real file-backed SQLite database:

  1. ``include_descendants=False`` (the badge's new default) skips the
     descendant BFS entirely — returns ONLY roots, no ``Descendant limit
     reached`` WARNING, ``truncated=False``.
  2. ``include_descendants=True`` (default back-compat) still walks
     descendants — returns roots + their children, ``truncated`` reflects
     whether the cap fired.
  3. The 3-tuple return shape ``(instances, total, truncated)`` is
     preserved end-to-end — facade, service, and repository all carry
     the new flag.

What is deliberately NOT real: the FastAPI app, the route, the SSE
hub. This test pins the ``list_instances`` seam through the manager
facade; the route test in ``tests/unit/test_hide_kb_instances.py`` (the
``TestListInstancesExcludeKB`` class) pins the HTTP layer separately.

Harness notes (repo lesson, QUARANTINE.md dependency_bus row): file-backed
SQLite via ``tmp_path`` with ``NullPool`` + WAL pragmas — NOT
``StaticPool``/``:memory:``, whose single shared connection trips the
documented cross-thread lost-write corruption. Same recipe as
``tests/integration/test_job_driven_enqueue_work_id_facade.py``
(``engine`` fixture, lifted verbatim with attribution).
``MigrationRunner`` is no-op'd — the quarantined pre-existing SQLite
migration family (20260714_000001) is orthogonal to this component.
"""

from __future__ import annotations

import asyncio
import logging
import os
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel


# ---------------------------------------------------------------------------
# Fixtures — real manager over a real file-backed SQLite engine
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    """Real SQLite FILE database (tmp_path) with NullPool.

    Mirrors the harness in
    ``tests/integration/test_job_driven_enqueue_work_id_facade.py``.
    """
    eng = create_engine(
        f"sqlite:///{tmp_path}/list_instances_include_descendants.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_pragmas(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
async def facade_manager(engine: Engine, tmp_path):
    """Real ``InstanceManager`` with its ONE shared engine patched to the
    fixture's engine. No worker pool (out of scope); no FastAPI app (out
    of scope — this test pins the ``list_instances`` seam, not the route).
    """
    import daemon.manager as daemon_manager_module
    from daemon.config import (
        AgentsConfig,
        Config,
        DaemonConfig,
        LLMConfig,
        LimitsConfig,
        PersistenceConfig,
    )
    from daemon.manager import InstanceManager

    config = Config(
        llm=LLMConfig(
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4",
        ),
        daemon=DaemonConfig(),
        persistence=PersistenceConfig(),
        limits=LimitsConfig(),
        agents=AgentsConfig(),
    )

    # Engine injection — same approach as test_job_driven_enqueue_work_id_facade.py
    with (
        patch(
            "daemon.migrations.runner.MigrationRunner.run_pending_migrations",
            return_value=[],
        ),
        patch(
            "daemon.manager.create_engine_from_config", return_value=engine
        ),
    ):
        manager = InstanceManager(config)

    manager._loop = asyncio.get_running_loop()
    manager._worker_pool = None

    return manager


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str,
    parent_id: str | None = None,
    agent_id: str = "developer",
    status: str = "running",
    project_id: str = "proj-1",
) -> None:
    """Insert one Instance row via the repository (real path)."""
    from daemon.repositories.instance.repository import (
        SQLModelInstanceRepository,
    )

    repo = SQLModelInstanceRepository(engine)
    repo.create(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=f"./agents/{agent_id}",
        parent_id=parent_id,
        status=status,
        project_id=project_id,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListInstancesIncludeDescendantsFacade:
    """The ``include_descendants`` kwarg threads end-to-end through the
    real facade.
    """

    @pytest.mark.asyncio
    async def test_include_descendants_false_returns_flat_paginated(
        self, facade_manager, engine
    ):
        """The badge's new path: include_descendants=False skips the
        descendant BFS and returns a flat paginated list.

        Fixture: 2 roots, each with 2 children (6 total). With
        include_descendants=False, the facade returns a flat
        created_at-DESC slice of all matching instances — no BFS, no
        truncation warnings. With limit=10 (the badge's page size), all 6
        rows fit in the response because there's no per-root expansion.
        """
        _seed_instance(engine, instance_id="root-a")
        _seed_instance(engine, instance_id="root-b")
        _seed_instance(engine, instance_id="child-a1", parent_id="root-a")
        _seed_instance(engine, instance_id="child-a2", parent_id="root-a")
        _seed_instance(engine, instance_id="child-b1", parent_id="root-b")
        _seed_instance(engine, instance_id="child-b2", parent_id="root-b")

        instances, total, truncated = facade_manager.list_instances(
            limit=10,
            offset=0,
            exclude_kb=True,
            include_descendants=False,  # the badge path
        )

        # Flat pagination returns ALL matching instances (both roots and
        # their children) — just no BFS expansion beyond what the WHERE
        # produces. That's the cost reduction: a single SELECT, not a
        # recursive walk.
        returned_ids = {inst["instance_id"] for inst in instances}
        assert returned_ids == {"root-a", "root-b", "child-a1", "child-a2",
                                "child-b1", "child-b2"}
        # Flat pagination: total counts all matching.
        assert total == 6
        # No BFS → no cap → truncated is False.
        assert truncated is False

    @pytest.mark.asyncio
    async def test_include_descendants_false_emits_no_cap_warning(
        self, facade_manager, engine, caplog
    ):
        """With include_descendants=False, the descendant cap warning
        must NEVER fire — even with thousands of rows. The badge poll
        was the only path that hit the cap; this regression-pins the
        shape of the fix.
        """
        # Seed 5 roots and 5 descendants. Not enough to hit the cap, but
        # the absence of the warning is the test.
        for i in range(5):
            _seed_instance(engine, instance_id=f"root-{i}")
            _seed_instance(
                engine, instance_id=f"child-{i}", parent_id=f"root-{i}"
            )

        with caplog.at_level(
            logging.WARNING, logger="daemon.repositories.instance.repository"
        ):
            facade_manager.list_instances(
                limit=10,
                include_descendants=False,
            )

        warning_texts = [
            rec.getMessage() for rec in caplog.records if rec.levelno == logging.WARNING
        ]
        assert not any("Descendant limit" in t for t in warning_texts), (
            f"Expected NO descendant-cap warning with include_descendants=False, "
            f"got: {warning_texts}"
        )

    @pytest.mark.asyncio
    async def test_include_descendants_true_returns_roots_plus_descendants(
        self, facade_manager, engine
    ):
        """Default back-compat behavior: include_descendants=True walks
        descendants and returns the union."""
        _seed_instance(engine, instance_id="root-a")
        _seed_instance(engine, instance_id="root-b")
        _seed_instance(engine, instance_id="child-a1", parent_id="root-a")
        _seed_instance(engine, instance_id="child-a2", parent_id="root-a")

        instances, total, truncated = facade_manager.list_instances(
            limit=10,
            include_descendants=True,
        )

        returned_ids = {inst["instance_id"] for inst in instances}
        assert returned_ids == {"root-a", "root-b", "child-a1", "child-a2"}
        # Root-based pagination: total reflects roots only.
        assert total == 2
        # No cap fired (4 rows < MAX_DESCENDANTS_PER_PAGE=1000).
        assert truncated is False

    @pytest.mark.asyncio
    async def test_default_kwarg_forwards_false_to_repository(
        self, facade_manager, engine
    ):
        """Omitting the kwarg forwards include_descendants=False (the
        repository-layer default — internal callers stay unaffected by
        the facade change). The badge fix rides on the ROUTE setting
        ``include_descendants=True`` by default for back-compat; without
        a route, the default cascades down to the repository default.
        """
        _seed_instance(engine, instance_id="root-1")
        _seed_instance(engine, instance_id="child-1", parent_id="root-1")

        # No include_descendants kwarg.
        instances, total, _ = facade_manager.list_instances(limit=10)

        # Default = flat pagination: child shows up in the page.
        returned_ids = {inst["instance_id"] for inst in instances}
        assert returned_ids == {"root-1", "child-1"}
        assert total == 2

    @pytest.mark.asyncio
    async def test_truncated_flag_surfaces_through_facade(
        self, facade_manager, engine, monkeypatch
    ):
        """The 3rd tuple element (``truncated``) is plumbed through the
        facade. When the descendant cap fires, the flag is True; when
        it doesn't, False.

        Monkeypatches the cap down to 3 so the fixture stays small. Uses
        a CHAIN shape (each BFS batch adds exactly one row) so the cap
        fires at the predicted bound — same pattern as the unit-level
        cap test (``tests/unit/test_instance_tree_loading.py``).
        """
        monkeypatch.setattr(
            "daemon.repositories.instance.repository.MAX_DESCENDANTS_PER_PAGE", 3
        )

        # Chain: root → child-1 → child-2 → child-3 → child-4
        # BFS: root (1) → child-1 (2) → child-2 (3) → cap fires, break.
        # child-3 and child-4 are never loaded.
        _seed_instance(engine, instance_id="root")
        _seed_instance(engine, instance_id="child-1", parent_id="root")
        _seed_instance(engine, instance_id="child-2", parent_id="child-1")
        _seed_instance(engine, instance_id="child-3", parent_id="child-2")
        _seed_instance(engine, instance_id="child-4", parent_id="child-3")

        instances, total, truncated = facade_manager.list_instances(
            limit=10,
            include_descendants=True,
        )

        # Cap fires: exactly MAX_DESCENDANTS_PER_PAGE rows loaded (root + 2 chain links).
        assert len(instances) == 3
        returned_ids = {inst["instance_id"] for inst in instances}
        assert returned_ids == {"root", "child-1", "child-2"}
        # The truncation flag is True (cap fired).
        assert truncated is True
        # Root-based pagination: total reflects roots only.
        assert total == 1
