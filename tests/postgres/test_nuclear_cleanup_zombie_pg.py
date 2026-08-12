"""PostgreSQL parity tests for the zombie-instance scan.

Verifies ``find_zombie_instances()`` and ``count_zombie_instances()``
work correctly against a real PostgreSQL backend — not just the SQLite
in-memory pack used by the unit tests in
``tests/unit/routers/test_jobs_cleanup_endpoint.py``.

This is critical because PostgreSQL is the PRIMARY database for this
project (see project notes), and the zombie-scan SQL uses anti-joins
with baked string literals::

    WHERE i.status NOT IN ('completed','error','terminated','failed')
      AND i.instance_id NOT IN (
        SELECT DISTINCT jqi.instance_id FROM job_queue_items jqi
        WHERE jqi.admission_state IN ('queued','active')
          AND jqi.deleted_at IS NULL
      )
      AND i.instance_id NOT IN (
        SELECT DISTINCT t.instance_id FROM task t
        WHERE t.status IN ('pending','running','paused')
      )

The baked-literal approach avoids SQLAlchemy's dialect-fragile
``expanding`` parameter style inside ``NOT IN (...)`` on SQLite. This
test confirms the same SQL parses and returns correct results on
PostgreSQL.

Run with::

    .venv/bin/pytest tests/postgres/test_nuclear_cleanup_zombie_pg.py \\
        -v --tb=short --override-ini="addopts=" -m postgres

The ``pg_engine`` fixture in ``tests/postgres/conftest.py`` skips the
entire module cleanly when PostgreSQL is not reachable, so this file is
safe to collect even on machines without a running PG.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session as SQLModelSession

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.task.models import Task, TaskStatus


# Auto-apply the postgres marker so ``pytest -m postgres`` selects these
# tests and the default ``addopts = "-m 'not integration and not postgres'"``
# skips them unless overridden.
pytestmark = pytest.mark.postgres


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def repo(pg_repository_factory):
    """Instance repository bound to the shared PG engine."""
    return pg_repository_factory(SQLModelInstanceRepository)


# =============================================================================
# Row-creation helpers (thin wrappers that bypass lifecycle orchestration
# so the test can pin specific status / admission_state values directly)
# =============================================================================


def _create_instance(
    engine,
    instance_id: str = "inst-1",
    status: str = "running",
    project_id: str = "pg-test-project",
):
    """Insert an Instance row directly via SQLModel session."""
    with SQLModelSession(engine) as session:
        inst = Instance(
            instance_id=instance_id,
            project_id=project_id,
            agent_id="tester",
            agent_dir="/tmp/tester",
            agent_name="Tester",
            status=status,
        )
        session.add(inst)
        session.commit()


def _create_task(
    engine,
    instance_id: str,
    status: str = "running",
):
    """Insert a Task row directly via SQLModel session."""
    status_enum = TaskStatus(status)
    task = Task(
        instance_id=instance_id,
        status=status_enum.value,
    )
    with SQLModelSession(engine) as session:
        session.add(task)
        session.commit()


def _create_job(
    engine,
    instance_id: str,
    admission_state: str = "active",
):
    """Insert a JobItem row directly via SQLModel session."""
    job = JobItem(
        agent_id="tester",
        agent_dir="/tmp/tester",
        message="hello",
        source="api",
        project_id="pg-test-project",
        priority=5,
        instance_id=instance_id,
        admission_state=admission_state,
    )
    with SQLModelSession(engine) as session:
        session.add(job)
        session.commit()


# =============================================================================
# Tests
# =============================================================================


class TestZombieScanPostgreSQL:
    """PG-parity coverage for ``find_zombie_instances`` / ``count_zombie_instances``.

    Mirrors the SQLite test class ``TestInstanceRepositoryZombieScan`` in
    ``tests/unit/routers/test_jobs_cleanup_endpoint.py`` but runs against
    a real PostgreSQL engine (session-scoped ``pg_engine`` fixture).
    The autouse ``_pg_truncate_tables`` fixture in the PG conftest TRUNCATEs
    every table before each test, so each test starts with a clean slate.
    """

    # ------------------------------------------------------------------
    # Scenario 1: empty database
    # ------------------------------------------------------------------

    def test_empty_database_returns_empty_list(self, repo, pg_engine):
        """No instances exist -> ``find_zombie_instances`` returns []."""
        assert repo.find_zombie_instances() == []

    def test_empty_database_count_is_zero(self, repo, pg_engine):
        """No instances exist -> ``count_zombie_instances`` returns 0."""
        assert repo.count_zombie_instances() == 0

    # ------------------------------------------------------------------
    # Scenario 2: a single true zombie (running, no work)
    # ------------------------------------------------------------------

    def test_finds_running_instance_with_no_work(self, repo, pg_engine):
        """A running instance with no JobItems and no Tasks IS a zombie."""
        _create_instance(pg_engine, instance_id="zombie-1", status="running")

        result = repo.find_zombie_instances()

        assert result == ["zombie-1"]

    # ------------------------------------------------------------------
    # Scenario 3: protected by active JobItem
    # ------------------------------------------------------------------

    def test_excludes_instance_with_active_jobitem(self, repo, pg_engine):
        """Instance with admission_state='active' JobItem is NOT a zombie."""
        _create_instance(pg_engine, instance_id="inst-active", status="running")
        _create_job(
            pg_engine, instance_id="inst-active", admission_state="active"
        )

        result = repo.find_zombie_instances()

        assert result == []

    # ------------------------------------------------------------------
    # Scenario 4: protected by queued JobItem
    # ------------------------------------------------------------------

    def test_excludes_instance_with_queued_jobitem(self, repo, pg_engine):
        """Instance with admission_state='queued' JobItem is NOT a zombie."""
        _create_instance(pg_engine, instance_id="inst-queued", status="running")
        _create_job(
            pg_engine, instance_id="inst-queued", admission_state="queued"
        )

        result = repo.find_zombie_instances()

        assert result == []

    # ------------------------------------------------------------------
    # Scenario 5: protected by live Task
    # ------------------------------------------------------------------

    def test_excludes_instance_with_live_task(self, repo, pg_engine):
        """Instance with a live (pending/running/paused) Task is NOT a zombie."""
        for live_status in ("pending", "running", "paused"):
            iid = f"inst-task-{live_status}"
            _create_instance(pg_engine, instance_id=iid, status="running")
            _create_task(pg_engine, instance_id=iid, status=live_status)

        result = repo.find_zombie_instances()

        assert result == [], (
            f"Instances protected by live tasks should be excluded; got {result}"
        )

    # ------------------------------------------------------------------
    # Scenario 6: terminal statuses excluded
    # ------------------------------------------------------------------

    def test_excludes_terminal_status_instances(self, repo, pg_engine):
        """Instances with terminal status (completed/error/terminated/failed)
        are NEVER zombies — the status filter alone excludes them."""
        for terminal in ("completed", "error", "terminated", "failed"):
            _create_instance(
                pg_engine,
                instance_id=f"inst-{terminal}",
                status=terminal,
            )

        result = repo.find_zombie_instances()

        assert result == []

    # ------------------------------------------------------------------
    # Scenario 7: count matches find
    # ------------------------------------------------------------------

    def test_count_matches_find_length(self, repo, pg_engine):
        """``count_zombie_instances()`` must equal
        ``len(find_zombie_instances())`` for the same DB state."""
        # 2 zombies + 1 terminal
        _create_instance(pg_engine, instance_id="z1", status="running")
        _create_instance(pg_engine, instance_id="z2", status="paused")
        _create_instance(pg_engine, instance_id="done", status="completed")

        find_result = repo.find_zombie_instances()
        count_result = repo.count_zombie_instances()

        assert len(find_result) == count_result == 2

    # ------------------------------------------------------------------
    # Scenario 8: mixed scenario — 2 zombies + 2 protected + 1 terminal
    # ------------------------------------------------------------------

    def test_mixed_scenario_returns_exactly_two_zombies(self, repo, pg_engine):
        """Mixed DB state: 2 zombies + 2 protected + 1 terminal.

        Verifies the scan returns EXACTLY the 2 true zombies and nothing
        else, exercising all three exclusion predicates in a single query.
        """
        # --- 2 true zombies (non-terminal, no work) ---
        _create_instance(pg_engine, instance_id="zombie-A", status="running")
        _create_instance(pg_engine, instance_id="zombie-B", status="paused")

        # --- 2 protected instances ---
        # Protected by active JobItem
        _create_instance(pg_engine, instance_id="alive-job", status="running")
        _create_job(
            pg_engine, instance_id="alive-job", admission_state="active"
        )
        # Protected by live Task
        _create_instance(
            pg_engine, instance_id="alive-task", status="running"
        )
        _create_task(
            pg_engine, instance_id="alive-task", status="running"
        )

        # --- 1 terminal instance (should be excluded by status filter) ---
        _create_instance(pg_engine, instance_id="terminal-1", status="completed")

        result = repo.find_zombie_instances()
        count = repo.count_zombie_instances()

        assert sorted(result) == ["zombie-A", "zombie-B"]
        assert count == 2
