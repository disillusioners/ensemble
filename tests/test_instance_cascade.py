"""Integration tests for SQLModelInstanceRepository cascade deletion.

These tests exercise the real `instance_repo.delete()` code path against a
real in-memory SQLite engine with foreign-key enforcement enabled. The
existing unit tests are 100% mocked, so they would never catch a missing
table from the cascade — most importantly the JobWatcher FK to
`instances.instance_id` that previously broke the maintenance cleanup loop
with `IntegrityError`.

Cascade order under test (from
`daemon/repositories/instance/repository.py::SQLModelInstanceRepository._cascade_instance_deps`):

    JobWatcher -> Task -> Event -> MessageQueue -> InstanceHierarchy(parent) -> InstanceHierarchy(child) -> Instance

The test file can be run standalone:

    python -m pytest tests/test_instance_cascade.py -v
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, select

# Importing the model classes is what registers them with SQLModel.metadata;
# `SQLModel.metadata.create_all` only creates tables for imported models.
from daemon.repositories.event.models import Event
from daemon.repositories.instance.models import Instance, InstanceHierarchy
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """Real in-memory SQLite engine with FK enforcement enabled.

    Mirrors the pragma setup in `daemon/repositories/factory.py` (which sets
    `PRAGMA foreign_keys=ON` on every new connection) but in-memory and
    using StaticPool so the same connection is reused across "threads"
    within a single test.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Create all tables for imported models. Imports at the top of this
    # module register them with SQLModel.metadata.
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def instance_repo(engine):
    """Repository under test, bound to the in-memory engine."""
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def make_instance(engine):
    """Factory for a parent instance with a unique ID and the minimum
    fields the `instances` table requires (PK + the two NOT NULL agent
    fields). All other columns have defaults and are left at their
    schema defaults."""

    def _make(instance_id: str | None = None) -> Instance:
        instance_id = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
        instance = Instance(
            instance_id=instance_id,
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            status="terminated",  # terminal — matches what the maintenance job would delete
        )
        with Session(engine) as session:
            session.add(instance)
            session.commit()
            session.refresh(instance)
        return instance

    return _make


@pytest.fixture
def make_job(engine):
    """Factory for a job row in `job_queue_items`.

    JobWatcher has a real FK to `job_queue_items.job_id`, so a JobItem must
    exist before the JobWatcher can be inserted (when FK enforcement is on).
    """

    def _make() -> JobItem:
        job = JobItem(
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="watch me",
        )
        with Session(engine) as session:
            session.add(job)
            session.commit()
            session.refresh(job)
        return job

    return _make


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _count_where(session: Session, model, **filters) -> int:
    """Count rows in `model` matching the given attribute filters."""
    stmt = select(model)
    for attr, value in filters.items():
        stmt = stmt.where(getattr(model, attr) == value)
    return len(list(session.exec(stmt)))


def _add_dependents(engine, instance_id: str, job_id: str) -> None:
    """Insert one of every dependent row pointing at `instance_id`.

    Creates: 1 JobWatcher + 1 Task + 1 Event + 1 MessageQueue +
    1 InstanceHierarchy(parent=instance) + 1 InstanceHierarchy(child=instance).
    """
    with Session(engine) as session:
        session.add(JobWatcher(job_id=job_id, instance_id=instance_id))
        session.add(Task(instance_id=instance_id))
        session.add(Event(instance_id=instance_id))
        session.add(MessageQueue(instance_id=instance_id, content="hello"))
        session.add(InstanceHierarchy(parent_id=instance_id, child_id=f"child-of-{instance_id}"))
        session.add(InstanceHierarchy(parent_id=f"parent-of-{instance_id}", child_id=instance_id))
        session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCascadeDeletesAllDependents:
    """`instance_repo.delete()` removes every dependent row in one call."""

    def test_cascade_deletes_jobwatcher_task_event_messagequeue_hierarchy(
        self, engine, instance_repo, make_instance, make_job
    ):
        instance = make_instance()
        job = make_job()
        _add_dependents(engine, instance.instance_id, job.job_id)

        # Sanity: the fixture actually inserted all six dependents.
        with Session(engine) as session:
            assert _count_where(session, JobWatcher, instance_id=instance.instance_id) == 1
            assert _count_where(session, Task, instance_id=instance.instance_id) == 1
            assert _count_where(session, Event, instance_id=instance.instance_id) == 1
            assert _count_where(session, MessageQueue, instance_id=instance.instance_id) == 1
            assert (
                _count_where(session, InstanceHierarchy, parent_id=instance.instance_id) == 1
            )
            assert (
                _count_where(session, InstanceHierarchy, child_id=instance.instance_id) == 1
            )

        result = instance_repo.delete(instance.instance_id)

        # Return value contract: deleted=True and the agent_dir is reported back.
        assert result["deleted"] is True
        assert result["instance_id"] == instance.instance_id
        assert result["agent_dir"] == instance.agent_dir

        # All six dependent rows are gone, and the instance itself is gone.
        with Session(engine) as session:
            assert _count_where(session, JobWatcher, instance_id=instance.instance_id) == 0
            assert _count_where(session, Task, instance_id=instance.instance_id) == 0
            assert _count_where(session, Event, instance_id=instance.instance_id) == 0
            assert _count_where(session, MessageQueue, instance_id=instance.instance_id) == 0
            assert (
                _count_where(session, InstanceHierarchy, parent_id=instance.instance_id) == 0
            )
            assert (
                _count_where(session, InstanceHierarchy, child_id=instance.instance_id) == 0
            )
            assert session.get(Instance, instance.instance_id) is None

    def test_cascade_does_not_touch_unrelated_rows(
        self, engine, instance_repo, make_instance, make_job
    ):
        """Deleting instance A leaves instance B's dependents intact."""
        instance_a = make_instance("inst-A")
        instance_b = make_instance("inst-B")
        job = make_job()
        _add_dependents(engine, instance_b.instance_id, job.job_id)

        instance_repo.delete(instance_a.instance_id)

        with Session(engine) as session:
            # B's dependents are untouched.
            assert _count_where(session, JobWatcher, instance_id=instance_b.instance_id) == 1
            assert _count_where(session, Task, instance_id=instance_b.instance_id) == 1
            assert _count_where(session, Event, instance_id=instance_b.instance_id) == 1
            assert _count_where(session, MessageQueue, instance_id=instance_b.instance_id) == 1
            assert session.get(Instance, instance_b.instance_id) is not None

    def test_delete_nonexistent_instance_returns_not_found(
        self, instance_repo
    ):
        """Deleting an ID that was never inserted is a soft no-op, not an
        exception — matches the existing `delete()` contract."""
        result = instance_repo.delete("does-not-exist")

        assert result == {
            "deleted": False,
            "instance_id": "does-not-exist",
            "error": "Not found",
        }


class TestJobWatcherFKRequiresCascade:
    """The whole point of this test: prove the cascade is necessary.

    With `PRAGMA foreign_keys=ON`, deleting an `instances` row while a
    `job_watchers` row still references it must raise `IntegrityError`.
    The cascade in `_cascade_instance_deps` exists precisely to avoid
    this — if a future refactor accidentally drops JobWatcher from the
    cascade, this test will fail with the same `IntegrityError` that
    previously crashed the maintenance loop.
    """

    def test_naive_instance_delete_violates_jobwatcher_fk(
        self, engine, make_instance, make_job
    ):
        instance = make_instance()
        job = make_job()
        with Session(engine) as session:
            session.add(JobWatcher(job_id=job.job_id, instance_id=instance.instance_id))
            session.commit()

        # Bypassing the repository — try to delete the Instance directly.
        with Session(engine) as session:
            inst = session.get(Instance, instance.instance_id)
            session.delete(inst)
            with pytest.raises(IntegrityError):
                session.commit()

    def test_repository_delete_succeeds_where_naive_delete_fails(
        self, engine, instance_repo, make_instance, make_job
    ):
        """Same setup as above, but going through `instance_repo.delete()`
        must succeed because the cascade handles JobWatcher first."""
        instance = make_instance()
        job = make_job()
        with Session(engine) as session:
            session.add(JobWatcher(job_id=job.job_id, instance_id=instance.instance_id))
            session.commit()

        result = instance_repo.delete(instance.instance_id)

        assert result["deleted"] is True
        with Session(engine) as session:
            assert session.get(Instance, instance.instance_id) is None
            assert (
                _count_where(session, JobWatcher, instance_id=instance.instance_id) == 0
            )
