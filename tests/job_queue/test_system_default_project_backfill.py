"""Regression tests for the system-default ``project_id`` on instances.

Background (bug reproduced 2026-07-07, ``logs/dev_run.log``):
``InstanceLifecycleService.spawn_instance`` used to skip
``normalize_project_id`` when the caller passed ``project_id=None``
(root instances, direct messages, source mappings). Those rows were
stored with a NULL / empty ``project_id`` instead of the system
default UUID, which made them invisible to project-scoped gates such
as the defer-queue idle check
(``TaskRepository.has_active_non_deferred_work``). A paused
non-deferred instance on the system default project then failed to
hold back ``system_defer_queue`` and the defer job started
prematurely.

These tests pin the data-repair side of the fix
(``SQLModelInstanceRepository.backfill_system_default_project_id``)
and the user-visible consequence: once the paused instance is stamped
with the system default ``project_id``, the defer gate fires and the
defer queue is correctly blocked.

Run with::

    pytest tests/job_queue/test_system_default_project_backfill.py -v --tb=short
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlmodel import Session as SQLModelSession

from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository

# Deterministic system default project id (matches
# ``uuid5(NAMESPACE_DNS, "__system_default__")`` used everywhere else).
SYSTEM_DEFAULT_ID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"


def _insert_instance(
    engine,
    instance_id: str,
    project_id: str | None,
    status: str = InstanceStatus.PAUSED.value,
    agent_id: str = "leader",
) -> None:
    """Insert an Instance row directly via SQL (raw project_id control)."""
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, version)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status, :project_id,
                     :created_at, :updated_at, 1)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": agent_id,
                "agent_dir": f"agents/{agent_id}",
                "status": status,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
            },
        )


def _insert_paused_non_deferred_task(engine, instance_id: str) -> None:
    """Insert a PAUSED non-deferred Task for ``instance_id`` via SQL."""
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                     retry_count, created_at, cancel_requested,
                     retry_scheduled, work_id, is_deferred)
                VALUES
                    (:task_type, :instance_id, :message_id, :status,
                     :retry_count, :created_at, :cancel_requested,
                     :retry_scheduled, :work_id, :is_deferred)
                """
            ),
            {
                "task_type": TaskType.PROCESS_MESSAGE.value,
                "instance_id": instance_id,
                "message_id": f"msg-{instance_id}",
                "status": TaskStatus.PAUSED.value,
                "retry_count": 0,
                "created_at": now,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": f"work-{instance_id}",
                "is_deferred": False,
            },
        )


def _instance_project_id(engine, instance_id: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT project_id FROM instances WHERE instance_id = :iid"),
            {"iid": instance_id},
        ).fetchone()
        return row[0] if row else None


@pytest.fixture
def instance_repo(engine):
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def task_repo(engine):
    return TaskRepository(engine)


class TestBackfillSystemDefaultProjectId:
    """``SQLModelInstanceRepository.backfill_system_default_project_id``."""

    def test_backfills_null_project_id(self, instance_repo, engine):
        _insert_instance(engine, "inst-null", project_id=None)

        updated = instance_repo.backfill_system_default_project_id(SYSTEM_DEFAULT_ID)

        assert updated == 1
        assert _instance_project_id(engine, "inst-null") == SYSTEM_DEFAULT_ID

    def test_backfills_empty_string_project_id(self, instance_repo, engine):
        _insert_instance(engine, "inst-empty", project_id="")

        updated = instance_repo.backfill_system_default_project_id(SYSTEM_DEFAULT_ID)

        assert updated == 1
        assert _instance_project_id(engine, "inst-empty") == SYSTEM_DEFAULT_ID

    def test_does_not_touch_already_set_project_id(self, instance_repo, engine):
        _insert_instance(engine, "inst-set", project_id="some-other-project")

        updated = instance_repo.backfill_system_default_project_id(SYSTEM_DEFAULT_ID)

        assert updated == 0
        assert _instance_project_id(engine, "inst-set") == "some-other-project"

    def test_idempotent_on_clean_database(self, instance_repo):
        # A database with no NULL/empty rows is a no-op.
        assert instance_repo.backfill_system_default_project_id(SYSTEM_DEFAULT_ID) == 0
        # Re-running after a prior backfill is also a no-op.
        assert instance_repo.backfill_system_default_project_id(SYSTEM_DEFAULT_ID) == 0

    def test_backfills_only_null_or_empty_rows(self, instance_repo, engine):
        _insert_instance(engine, "inst-null", project_id=None)
        _insert_instance(engine, "inst-empty", project_id="")
        _insert_instance(engine, "inst-keep", project_id="user-project-1")

        updated = instance_repo.backfill_system_default_project_id(SYSTEM_DEFAULT_ID)

        assert updated == 2
        assert _instance_project_id(engine, "inst-null") == SYSTEM_DEFAULT_ID
        assert _instance_project_id(engine, "inst-empty") == SYSTEM_DEFAULT_ID
        assert _instance_project_id(engine, "inst-keep") == "user-project-1"


class TestDeferGateSeesBackfilledPausedInstance:
    """The user-visible symptom: a paused non-deferred instance with a
    NULL/empty ``project_id`` is invisible to the project-scoped defer
    gate; after the backfill stamps the system default ``project_id``,
    the gate fires and the defer queue is correctly blocked.
    """

    def test_null_project_id_invisible_to_defer_gate(self, task_repo, engine):
        """Pre-fix state: paused non-deferred instance with NULL
        project_id → ``has_active_non_deferred_work(system_default)``
        returns False, so the defer queue would wrongly admit.
        """
        _insert_instance(engine, "inst-paused-null", project_id=None)
        _insert_paused_non_deferred_task(engine, "inst-paused-null")

        result = task_repo.has_active_non_deferred_work(SYSTEM_DEFAULT_ID)

        assert result is False, (
            "A paused non-deferred instance with NULL project_id is invisible "
            "to the project-scoped defer gate — this is the bug."
        )

    def test_backfill_makes_paused_instance_block_defer_gate(
        self, instance_repo, task_repo, engine
    ):
        """Post-fix state: after the backfill stamps the system default
        ``project_id``, the same paused non-deferred instance makes the
        defer gate fire (returns True) so ``system_defer_queue`` is held
        back.
        """
        _insert_instance(engine, "inst-paused-null", project_id=None)
        _insert_paused_non_deferred_task(engine, "inst-paused-null")

        # Sanity: invisible before backfill.
        assert task_repo.has_active_non_deferred_work(SYSTEM_DEFAULT_ID) is False

        updated = instance_repo.backfill_system_default_project_id(SYSTEM_DEFAULT_ID)
        assert updated == 1

        # After backfill, the paused non-deferred instance blocks the
        # project-scoped defer gate (and the system-wide probe too).
        assert task_repo.has_active_non_deferred_work(SYSTEM_DEFAULT_ID) is True
        assert task_repo.has_active_non_deferred_work(None) is True


# ── Job backfill ──────────────────────────────────────────────────────────────
#
# The SQLite migration ``20260424_000001`` repairs NULL/empty
# ``project_id`` on ``job_queue_items``, but the migration runner is a
# NO-OP on PostgreSQL, so PG rows keep a NULL project_id. Those rows
# vanish from the Jobs UI's project-scoped refresh
# (``GET /api/jobs?project_id=…``) — e.g. a paused job on the system
# default project disappears on refresh even though it shows on the
# initial (unfiltered) page load.


def _insert_job(engine, job_id: str, project_id: str | None) -> None:
    """Insert a ``job_queue_items`` row via the SQLModel (raw project_id).

    The table has many NOT NULL columns without DB-level defaults, so we
    build a full ``JobItem`` (which supplies all model defaults) and then
    override ``project_id`` with a raw UPDATE so the test controls the
    exact NULL / empty / set value.
    """
    from daemon.repositories.job_queue.models import JobItem

    with SQLModelSession(engine) as session:
        session.add(
            JobItem(
                job_id=job_id,
                agent_id="leader",
                agent_dir="agents/leader",
                message="say hi",
                project_id="placeholder",  # set to satisfy NOT NULL, overwritten below
                admission_state=AdmissionState.ACTIVE.value,
            )
        )
        session.commit()
        session.exec(
            text(
                "UPDATE job_queue_items SET project_id = :project_id "
                "WHERE job_id = :job_id"
            ),
            params={"project_id": project_id, "job_id": job_id},
        )
        session.commit()


def _job_project_id(engine, job_id: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT project_id FROM job_queue_items WHERE job_id = :jid"),
            {"jid": job_id},
        ).fetchone()
        return row[0] if row else None


@pytest.fixture
def job_repo(engine):
    return JobRepository(engine)


class TestBackfillSystemDefaultProjectIdOnJobs:
    """``JobRepository.backfill_system_default_project_id`` for
    ``job_queue_items`` (the PG counterpart of the SQLite migration)."""

    def test_backfills_null_project_id(self, job_repo, engine):
        _insert_job(engine, "job-null", project_id=None)

        updated = job_repo.backfill_system_default_project_id(SYSTEM_DEFAULT_ID)

        assert updated == 1
        assert _job_project_id(engine, "job-null") == SYSTEM_DEFAULT_ID

    def test_backfills_empty_string_project_id(self, job_repo, engine):
        _insert_job(engine, "job-empty", project_id="")

        updated = job_repo.backfill_system_default_project_id(SYSTEM_DEFAULT_ID)

        assert updated == 1
        assert _job_project_id(engine, "job-empty") == SYSTEM_DEFAULT_ID

    def test_does_not_touch_already_set_project_id(self, job_repo, engine):
        _insert_job(engine, "job-set", project_id="user-project-1")

        updated = job_repo.backfill_system_default_project_id(SYSTEM_DEFAULT_ID)

        assert updated == 0
        assert _job_project_id(engine, "job-set") == "user-project-1"

    def test_backfills_only_null_or_empty_rows(self, job_repo, engine):
        _insert_job(engine, "job-null", project_id=None)
        _insert_job(engine, "job-empty", project_id="")
        _insert_job(engine, "job-keep", project_id="user-project-2")

        updated = job_repo.backfill_system_default_project_id(SYSTEM_DEFAULT_ID)

        assert updated == 2
        assert _job_project_id(engine, "job-null") == SYSTEM_DEFAULT_ID
        assert _job_project_id(engine, "job-empty") == SYSTEM_DEFAULT_ID
        assert _job_project_id(engine, "job-keep") == "user-project-2"

    def test_idempotent(self, job_repo, engine):
        _insert_job(engine, "job-null", project_id=None)
        assert job_repo.backfill_system_default_project_id(SYSTEM_DEFAULT_ID) == 1
        # Second run is a no-op — the row already has a project_id.
        assert job_repo.backfill_system_default_project_id(SYSTEM_DEFAULT_ID) == 0
