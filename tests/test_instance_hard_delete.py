"""Tests for the instance hard-delete feature.

Locks in the behavior of the destructive DELETE /instances/{id}?hard_delete=true
path and its underlying repository / service plumbing:

    - daemon/routers/instances.py::terminate_instance (DELETE endpoint with hard_delete param)
    - daemon/repositories/instance/repository.py::SQLModelInstanceRepository.hard_delete_tree
    - daemon/services/instance_lifecycle.py::InstanceLifecycleService.hard_delete_instance
    - daemon/manager.py::InstanceManager.hard_delete_instance (facade)

Mirrors the style/fixtures in ``tests/test_instance_cascade.py`` (real in-memory
SQLite with FK enforcement on, ``StaticPool`` for cross-thread safety,
``SQLModel.metadata.create_all`` for table creation). The cascade is run
against the real engine so an accidental reorder of the FK-safe DELETEs would
surface as an ``IntegrityError`` — the same failure that previously crashed
the maintenance loop.

The file is run standalone:

    python -m pytest tests/test_instance_hard_delete.py -v
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, select

from daemon.repositories.event.models import Event
from daemon.repositories.instance.models import Instance, InstanceHierarchy
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.instance_ui_prefs.models import InstanceUiPrefs
from daemon.repositories.job_queue.models import JobItem, JobLock
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task
from daemon.repositories.dependency_bus.models import DependencyWatcher
from daemon.repositories.source.models import InstanceMapping, SourceConfig
from daemon.routers.instances import router as instances_router
from daemon.write_pause_guard import WritePauseGuard


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine with FK enforcement enabled.

    Mirrors ``tests/test_instance_cascade.py::engine``. ``StaticPool`` keeps a
    single connection alive for the test so reads after writes see the latest
    data even when the writer ran on a different asyncio.to_thread worker.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def instance_repo(engine: Engine) -> SQLModelInstanceRepository:
    """Repository under test."""
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def write_guard() -> WritePauseGuard:
    """Fresh ``WritePauseGuard`` — not paused."""
    return WritePauseGuard()


@pytest.fixture
def seed_tree(engine: Engine):
    """Factory for a 3-instance tree (parent + 2 children) with one of every
    dependent row type, including UI preferences, pointing at the parent or
    one of the children.

    Returns a dict with the instance IDs and the seeded dependency counts so
    individual tests can assert on the exact starting state.
    """

    def _seed(
        root_id: str = "root-001",
        child_a_id: str = "child-a-001",
        child_b_id: str = "child-b-001",
        *,
        agent_id: str = "developer",
        queue_id: str = "system_fifo_queue",
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()

        # Create the 3 instances (root + 2 children).
        with Session(engine) as s:
            s.add(Instance(
                instance_id=root_id,
                agent_id=agent_id,
                agent_dir=f"/tmp/agents/{agent_id}",
                agent_name=agent_id,
                parent_id=None,
                status="running",
                version=1,
                created_at=now,
                updated_at=now,
            ))
            s.add(Instance(
                instance_id=child_a_id,
                agent_id=agent_id,
                agent_dir=f"/tmp/agents/{agent_id}",
                agent_name=agent_id,
                parent_id=root_id,
                status="running",
                version=1,
                created_at=now,
                updated_at=now,
            ))
            s.add(Instance(
                instance_id=child_b_id,
                agent_id=agent_id,
                agent_dir=f"/tmp/agents/{agent_id}",
                agent_name=agent_id,
                parent_id=root_id,
                status="running",
                version=1,
                created_at=now,
                updated_at=now,
            ))
            # Hierarchy rows: root → child_a, root → child_b.
            s.add(InstanceHierarchy(
                parent_id=root_id, child_id=child_a_id, created_at=now,
            ))
            s.add(InstanceHierarchy(
                parent_id=root_id, child_id=child_b_id, created_at=now,
            ))
            s.commit()

        # Pre-seed SourceConfig rows in their own committed session so the
        # ``source_id`` FK on InstanceMapping is satisfied on the main
        # pass. Mirrors the ``seed_source`` fixture pattern used in
        # ``tests/unit/test_instance_mapping_upsert.py`` — splitting the
        # seed across two sessions avoids any unit-of-work FK-insert-
        # ordering issues (the SourceConfig rows survive the cascade
        # because they are not instance-scoped).
        with Session(engine) as s:
            for iid in (root_id, child_a_id, child_b_id):
                s.add(SourceConfig(
                    source_id=f"src-{iid}",
                    source_type="telegram",
                    name=f"source-{iid}",
                ))
            s.commit()

        # For each of the 3 instances, seed: 1 JobItem, 1 JobWatcher (real FK
        # to instances.instance_id), 1 JobLock (joins via job_id subquery),
        # 1 Task, 1 Event, 1 MessageQueue, 1 DependencyWatcher
        # (target_instance_id → instances.instance_id, logical FK only),
        # 1 InstanceMapping (agent_instance_id → instances.instance_id,
        # with source_id FK to the pre-seeded SourceConfig rows above), and
        # 1 InstanceUiPrefs (instance_id is a logical FK). This represents a
        # realistic tree whose instances have each been touched in the UI.
        # JobLocks share (project_id, queue_id) but use a unique lock_slot
        # per instance because the table has a UNIQUE(project_id,
        # queue_id, lock_slot) constraint (C5).
        ids = [root_id, child_a_id, child_b_id]
        with Session(engine) as s:
            for idx, iid in enumerate(ids):
                job = JobItem(
                    agent_id=agent_id,
                    agent_dir=f"/tmp/agents/{agent_id}",
                    message=f"msg for {iid}",
                    source="api",
                    project_id="test-project",
                    instance_id=iid,
                )
                s.add(job)
                s.flush()  # populate job_id
                s.add(JobWatcher(job_id=job.job_id, instance_id=iid))
                s.add(JobLock(
                    project_id="test-project",
                    queue_id=queue_id,
                    job_id=job.job_id,
                    instance_id=iid,
                    lock_slot=idx,  # unique per (project_id, queue_id)
                ))
                s.add(Task(instance_id=iid))
                s.add(Event(instance_id=iid, kind="message_received"))
                s.add(MessageQueue(instance_id=iid, content=f"hello {iid}"))
                # Dependency bus: one pending watcher targeting this
                # instance. Logical FK only (no DB-level FK declared),
                # but the cascade must still wipe the row.
                s.add(DependencyWatcher(
                    source_task_id=f"task-{iid}",
                    target_instance_id=iid,
                    state="PENDING",
                ))
                # Source mapping row referencing the pre-seeded
                # SourceConfig (source_id FK is real and must be
                # satisfied at INSERT time). The cascade must wipe the
                # InstanceMapping row; the SourceConfig row survives.
                s.add(InstanceMapping(
                    source_id=f"src-{iid}",
                    external_user_id=f"user-{iid}",
                    agent_instance_id=iid,
                    agent_id=agent_id,
                    agent_dir=f"/tmp/agents/{agent_id}",
                ))
                s.add(InstanceUiPrefs(
                    instance_id=iid,
                    color_tag="blue",
                ))
            s.commit()

        return {
            "tree_ids": ids,
            "root_id": root_id,
            "child_ids": [child_a_id, child_b_id],
            "per_instance_deps": 9,  # Includes one row for each dependent table.
            "hierarchy_rows": 2,
            "instances": 3,
        }

    return _seed


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _count_where(session: Session, model, **filters) -> int:
    """Count rows in `model` matching the given attribute filters."""
    stmt = select(model)
    for attr, value in filters.items():
        stmt = stmt.where(getattr(model, attr) == value)
    return len(list(session.exec(stmt)))


def _count_all(session: Session, model) -> int:
    """Count all rows in `model`."""
    return len(list(session.exec(select(model))))


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — End-to-end cascade: 3-instance tree, all tables wiped
# ─────────────────────────────────────────────────────────────────────────────


class TestHardDeleteTreeFullCascade:
    """``hard_delete_tree([root, child_a, child_b])`` removes every dependent
    row across all dependent tables in a single transaction.
    """

    def test_cascade_wipes_every_dependent_table_for_tree(
        self, engine, instance_repo, seed_tree
    ):
        info = seed_tree()
        tree_ids = info["tree_ids"]

        # Sanity-check the seed actually inserted every dependency type.
        with Session(engine) as s:
            for iid in tree_ids:
                assert _count_where(s, JobWatcher, instance_id=iid) == 1
                assert _count_where(s, Task, instance_id=iid) == 1
                assert _count_where(s, Event, instance_id=iid) == 1
                assert _count_where(s, MessageQueue, instance_id=iid) == 1
                assert _count_where(s, JobItem, instance_id=iid) == 1
                assert _count_where(s, JobLock, instance_id=iid) == 1
                assert _count_where(s, DependencyWatcher, target_instance_id=iid) == 1
                assert _count_where(s, InstanceMapping, agent_instance_id=iid) == 1
                assert _count_where(s, InstanceUiPrefs, instance_id=iid) == 1
            assert _count_all(s, Instance) == 3
            assert _count_all(s, InstanceHierarchy) == 2

        result = instance_repo.hard_delete_tree(tree_ids)

        # Return-shape contract.
        assert result["deleted"] is True
        assert sorted(result["tree_ids"]) == sorted(tree_ids)
        counts = result["counts"]
        assert counts["job_watchers"] == 3
        assert counts["tasks"] == 3
        assert counts["events"] == 3
        assert counts["message_queue"] == 3
        assert counts["job_queue_items"] == 3
        assert counts["job_locks"] == 3
        assert counts["dependency_watchers"] == 3
        assert counts["instance_mappings"] == 3
        assert counts["instance_hierarchy"] == 2
        assert counts["instance_ui_prefs"] == 3
        assert counts["instances"] == 3

        # Every table is empty for these IDs.
        with Session(engine) as s:
            for iid in tree_ids:
                assert _count_where(s, JobWatcher, instance_id=iid) == 0
                assert _count_where(s, Task, instance_id=iid) == 0
                assert _count_where(s, Event, instance_id=iid) == 0
                assert _count_where(s, MessageQueue, instance_id=iid) == 0
                assert _count_where(s, JobItem, instance_id=iid) == 0
                assert _count_where(s, JobLock, instance_id=iid) == 0
                assert _count_where(s, DependencyWatcher, target_instance_id=iid) == 0
                assert _count_where(s, InstanceMapping, agent_instance_id=iid) == 0
                assert _count_where(s, InstanceUiPrefs, instance_id=iid) == 0
            assert _count_all(s, Instance) == 0
            assert _count_all(s, InstanceHierarchy) == 0

    def test_cascade_does_not_touch_unrelated_instances(
        self, engine, instance_repo, seed_tree
    ):
        """Deleting tree T leaves tree U's instances and dependents intact."""
        target = seed_tree(root_id="tgt-root", child_a_id="tgt-a", child_b_id="tgt-b")
        # Different queue_id so the second tree's JobLocks don't collide
        # with the first tree's UNIQUE(project_id, queue_id, lock_slot).
        other = seed_tree(
            root_id="oth-root", child_a_id="oth-a", child_b_id="oth-b",
            queue_id="system_parallel_queue",
        )

        result = instance_repo.hard_delete_tree(target["tree_ids"])

        assert result["deleted"] is True
        # Target tree is gone.
        with Session(engine) as s:
            for iid in target["tree_ids"]:
                assert s.get(Instance, iid) is None
                assert _count_where(s, JobWatcher, instance_id=iid) == 0
                assert _count_where(s, Task, instance_id=iid) == 0
                assert _count_where(s, Event, instance_id=iid) == 0
                assert _count_where(s, MessageQueue, instance_id=iid) == 0
                assert _count_where(s, JobItem, instance_id=iid) == 0
                assert _count_where(s, JobLock, instance_id=iid) == 0
                assert _count_where(s, DependencyWatcher, target_instance_id=iid) == 0
                assert _count_where(s, InstanceMapping, agent_instance_id=iid) == 0
                assert _count_where(s, InstanceUiPrefs, instance_id=iid) == 0
            # Other tree is untouched.
            for iid in other["tree_ids"]:
                assert s.get(Instance, iid) is not None
                assert _count_where(s, JobWatcher, instance_id=iid) == 1
                assert _count_where(s, Task, instance_id=iid) == 1
                assert _count_where(s, Event, instance_id=iid) == 1
                assert _count_where(s, MessageQueue, instance_id=iid) == 1
                assert _count_where(s, JobItem, instance_id=iid) == 1
                assert _count_where(s, JobLock, instance_id=iid) == 1
                assert _count_where(s, DependencyWatcher, target_instance_id=iid) == 1
                assert _count_where(s, InstanceMapping, agent_instance_id=iid) == 1
                assert _count_where(s, InstanceUiPrefs, instance_id=iid) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — FK cascade order is FK-safe
# ─────────────────────────────────────────────────────────────────────────────


class TestFKCascadeOrder:
    """``hard_delete_tree`` deletes JobWatcher (real FK) BEFORE Instance.

    With ``PRAGMA foreign_keys=ON``, deleting an ``instances`` row while a
    ``job_watchers`` row still references it must raise ``IntegrityError``.
    The cascade in ``hard_delete_tree`` exists precisely to avoid this — if
    a future refactor accidentally drops JobWatcher from the cascade, this
    test will fail with the same ``IntegrityError`` that previously crashed
    the maintenance loop.
    """

    def test_naive_instance_delete_violates_jobwatcher_fk(
        self, engine, seed_tree,
    ):
        """Direct DELETE FROM instances … would crash; proves the FK is real."""
        info = seed_tree()
        root_id = info["root_id"]

        # Sanity: a JobWatcher exists for root_id.
        with Session(engine) as s:
            assert _count_where(s, JobWatcher, instance_id=root_id) == 1

        # Bypass the repository — try to delete the Instance row directly.
        with Session(engine) as s:
            inst = s.get(Instance, root_id)
            assert inst is not None
            s.delete(inst)
            with pytest.raises(IntegrityError):
                s.commit()

    def test_hard_delete_tree_succeeds_where_naive_delete_fails(
        self, engine, instance_repo, seed_tree,
    ):
        """``hard_delete_tree`` cleans JobWatcher FIRST → no IntegrityError.

        This is the regression-guard test. If the JobWatcher delete is ever
        reordered after the Instance delete, this test starts failing with
        IntegrityError.
        """
        info = seed_tree()

        # Should not raise.
        result = instance_repo.hard_delete_tree(info["tree_ids"])

        assert result["deleted"] is True
        with Session(engine) as s:
            assert _count_all(s, Instance) == 0
            assert _count_all(s, JobWatcher) == 0
            # The other dependent tables are also wiped — proves the cascade
            # ran to completion (rollback-on-error would have left rows).
            assert _count_all(s, Task) == 0
            assert _count_all(s, Event) == 0
            assert _count_all(s, MessageQueue) == 0
            assert _count_all(s, JobItem) == 0
            assert _count_all(s, JobLock) == 0
            assert _count_all(s, DependencyWatcher) == 0
            assert _count_all(s, InstanceMapping) == 0
            assert _count_all(s, InstanceUiPrefs) == 0
            assert _count_all(s, InstanceHierarchy) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Idempotency
# ─────────────────────────────────────────────────────────────────────────────


class TestIdempotency:
    """Calling ``hard_delete_tree`` twice with the same ``tree_ids`` is safe."""

    def test_second_call_is_noop(self, engine, instance_repo, seed_tree):
        info = seed_tree()
        tree_ids = info["tree_ids"]

        first = instance_repo.hard_delete_tree(tree_ids)
        assert first["deleted"] is True
        assert all(v > 0 for v in first["counts"].values())

        second = instance_repo.hard_delete_tree(tree_ids)

        # Second call: deleted=False, all counts zero, no exception.
        assert second["deleted"] is False
        assert second["counts"] == {
            "job_locks": 0,
            "job_queue_items": 0,
            "job_watchers": 0,
            "tasks": 0,
            "events": 0,
            "message_queue": 0,
            "dependency_watchers": 0,
            "instance_mappings": 0,
            "instance_hierarchy": 0,
            "instance_ui_prefs": 0,
            "instances": 0,
        }

    def test_empty_tree_ids_returns_zero_counts_no_error(
        self, engine, instance_repo,
    ):
        """Calling with ``[]`` is a documented no-op, not an error."""
        result = instance_repo.hard_delete_tree([])

        assert result["deleted"] is False
        assert result["tree_ids"] == []
        assert all(v == 0 for v in result["counts"].values())


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — Empty tree fallback at the service layer
# ─────────────────────────────────────────────────────────────────────────────


class TestEmptyTreeFallback:
    """``InstanceLifecycleService.hard_delete_instance`` falls back to
    ``[instance_id]`` when ``get_tree_ids`` returns ``[]``.

    Why this matters: a partially-deleted tree (root missing from DB,
    descendants still present) would otherwise leave orphan rows. The
    fallback ensures the call still attempts cleanup + checkpoint sweep
    for the requested root.
    """

    @pytest.mark.asyncio
    async def test_falls_back_to_single_id_when_get_tree_ids_is_empty(
        self, engine, write_guard,
    ):
        """When the repo returns ``[]``, the service passes ``[instance_id]``
        into ``hard_delete_tree`` so the call still cleans up + sweeps
        checkpoints for that one ID.
        """
        from daemon.services.instance_lifecycle import InstanceLifecycleService

        instance_id = "orphan-123"

        # Build a manager whose get_tree_ids returns [] and hard_delete_tree
        # captures whatever tree_ids it was called with.
        captured: dict[str, Any] = {}

        def _fake_hard_delete_tree(tree_ids):
            captured["tree_ids"] = list(tree_ids)
            return {
                "deleted": False,
                "tree_ids": list(tree_ids),
                "counts": {
                    "job_locks": 0,
                    "job_queue_items": 0,
                    "job_watchers": 0,
                    "tasks": 0,
                    "events": 0,
                    "message_queue": 0,
                    "dependency_watchers": 0,
                    "instance_mappings": 0,
                    "instance_hierarchy": 0,
                    "instance_ui_prefs": 0,
                    "instances": 0,
                },
            }

        repo = MagicMock()
        repo.get_tree_ids = MagicMock(return_value=[])
        repo.hard_delete_tree = MagicMock(side_effect=_fake_hard_delete_tree)

        manager = MagicMock()
        manager._instance_repository = repo
        manager.engine = engine
        manager.write_guard = write_guard
        manager._graph_tasks = {}
        manager._instance_repository.get = MagicMock(return_value=None)
        manager.instances = {}
        manager._request_registry = MagicMock()
        manager._live_hub = MagicMock()
        manager._live_hub.cleanup_instance = AsyncMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._watcher_repo = MagicMock()
        manager._watcher_repo.remove_all_watches_for_instance = MagicMock(
            return_value=0,
        )
        manager._mcp_service = None
        manager._queue_repository = MagicMock()
        manager._queue_repository.delete_by_instance = MagicMock(return_value=0)
        manager._job_queue_mgmt_service = MagicMock()
        manager._job_queue_mgmt_service._dispatch_bus = MagicMock()
        manager._job_queue_mgmt_service._dispatch_bus.notify_all = MagicMock()
        # No checkpointer — exercises the "skip sweep" branch.
        manager._checkpointer = None

        svc = InstanceLifecycleService(
            manager=manager,
            cancellation_service=MagicMock(),
            job_queue_service=MagicMock(
                _repository=MagicMock(find_jobs_by_instance=MagicMock(return_value=[])),
                cancel_job=AsyncMock(return_value=True),
                complete_job=AsyncMock(return_value=None),
                release_lock_by_instance=AsyncMock(return_value=[]),
                trigger_next_job_sync=MagicMock(),
                get_job_by_instance_sync=MagicMock(return_value=None),
            ),
        )

        result = await svc.hard_delete_instance(instance_id)

        # The repo's get_tree_ids was queried exactly once with the
        # requested root, and returned the empty list we mocked.
        repo.get_tree_ids.assert_called_once_with(instance_id)

        # The fallback kicked in: hard_delete_tree received [instance_id].
        assert captured["tree_ids"] == [instance_id]
        assert result["tree_ids"] == [instance_id]
        assert result["root_instance_id"] == instance_id
        # No checkpointer → 0 threads swept, no error.
        assert result["checkpoint_threads_deleted"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — Soft-delete (hard_delete=False) is unchanged
# ─────────────────────────────────────────────────────────────────────────────


class TestSoftDeleteUnchanged:
    """``terminate_instance`` (the default DELETE path) must NOT invoke
    ``hard_delete_tree`` — the ``instances`` row, ``job_watchers`` (real FK),
    ``events``, and ``job_queue_items`` rows all survive the default DELETE.
    The destructive DELETE (``?hard_delete=true``) is the only path that
    wipes those rows.
    """

    def test_default_terminate_preserves_critical_db_rows(
        self, engine, write_guard,
    ):
        """Default terminate leaves the instance row + its FK-protected and
        FK-free dependent rows intact. The hard-delete path is the sole
        mechanism for removing them.

        What terminate DOES delete (in-memory cleanup cascade):
            * ``job_locks`` — step 6 of ``_terminate_instance_db_sync``
            * ``message_queue`` — step 7
            * ``task`` — step 7b (closes the orphan window where the
              worker would claim a task whose message is gone)
            * ``instance_hierarchy`` parent-side — step 8

        What terminate does NOT touch:
            * ``instances`` row — only the ``status`` column flips to
              ``terminated``
            * ``job_watchers`` (real FK to ``instances.instance_id``)
            * ``events`` (no FK)
            * ``job_queue_items`` — only ``status`` flips to ``cancelled``
        """
        from daemon.repositories.instance.models import InstanceStatus
        from daemon.services.instance_lifecycle import InstanceLifecycleService

        root_id = "soft-root-001"
        child_id = "soft-child-001"

        # Seed a 2-instance tree with one of every dependent row.
        now = datetime.now(timezone.utc).isoformat()
        with Session(engine) as s:
            s.add(Instance(
                instance_id=root_id, agent_id="developer",
                agent_dir="/tmp/agents/developer", agent_name="developer",
                parent_id=None, status="running", version=1,
                created_at=now, updated_at=now,
            ))
            s.add(Instance(
                instance_id=child_id, agent_id="developer",
                agent_dir="/tmp/agents/developer", agent_name="developer",
                parent_id=root_id, status="running", version=1,
                created_at=now, updated_at=now,
            ))
            s.add(InstanceHierarchy(
                parent_id=root_id, child_id=child_id, created_at=now,
            ))
            for iid in (root_id, child_id):
                job = JobItem(
                    agent_id="developer", agent_dir="/tmp/agents/developer",
                    message="m", source="api", project_id="p", instance_id=iid,
                )
                s.add(job); s.flush()
                s.add(JobWatcher(job_id=job.job_id, instance_id=iid))
                s.add(Task(instance_id=iid))
                s.add(Event(instance_id=iid))
                s.add(MessageQueue(instance_id=iid, content="hi"))
            s.commit()

        manager = MagicMock()
        manager.engine = engine
        manager.write_guard = write_guard
        manager._instance_repository = MagicMock()
        manager._graph_tasks = {}
        manager._request_registry = MagicMock()
        manager._live_hub = MagicMock()
        manager._live_hub.cleanup_instance = AsyncMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._watcher_repo = MagicMock()
        manager._watcher_repo.remove_all_watches_for_instance = MagicMock(
            return_value=0,
        )
        manager._mcp_service = None
        manager.instances = {}
        manager._queue_repository = MagicMock()
        manager._queue_repository.delete_by_instance = MagicMock(return_value=0)
        manager._job_queue_mgmt_service = MagicMock()
        manager._job_queue_mgmt_service._dispatch_bus = MagicMock()
        manager._job_queue_mgmt_service._dispatch_bus.notify_all = MagicMock()

        svc = InstanceLifecycleService(
            manager=manager,
            cancellation_service=MagicMock(),
            job_queue_service=MagicMock(
                _repository=MagicMock(find_jobs_by_instance=MagicMock(return_value=[])),
                cancel_job=AsyncMock(return_value=True),
                complete_job=AsyncMock(return_value=None),
                release_lock_by_instance=AsyncMock(return_value=[]),
                trigger_next_job_sync=MagicMock(),
                get_job_by_instance_sync=MagicMock(return_value=None),
            ),
        )

        asyncio.run(svc.terminate_instance(root_id))

        # CRITICAL invariants of the default DELETE path:
        #   1. The instances row stays (only status flipped).
        #   2. The real-FK row (job_watchers) stays — proving we did NOT
        #      accidentally invoke the hard-delete cascade.
        #   3. Events and the job_queue_items rows stay.
        #   4. The in-memory cascade DOES still clean up the rows it is
        #      responsible for: task, message_queue, job_locks, and the
        #      parent-side instance_hierarchy link. These are NOT part of
        #      the hard-delete cascade — they're the per-instance cleanup
        #      written into ``_terminate_instance_db_sync`` (steps 6/7/7b/8).
        with Session(engine) as s:
            # (1) Instance rows preserved, status flipped to terminated.
            assert _count_all(s, Instance) == 2
            root = s.get(Instance, root_id)
            assert root is not None
            assert root.status == InstanceStatus.TERMINATED.value

            # (2) JobWatcher rows (real FK to instances) PRESERVED — if
            # they had been wiped, hard_delete_tree ran. They are still
            # there, so the default path is unchanged.
            assert _count_where(s, JobWatcher, instance_id=root_id) == 1
            assert _count_where(s, JobWatcher, instance_id=child_id) == 1

            # (3) Events and job_queue_items rows PRESERVED.
            assert _count_where(s, Event, instance_id=root_id) == 1
            assert _count_where(s, Event, instance_id=child_id) == 1
            assert _count_where(s, JobItem, instance_id=root_id) == 1
            assert _count_where(s, JobItem, instance_id=child_id) == 1

            # (4) In-memory cleanup cascade DID remove what it's supposed
            # to remove. Step 6 deletes job_locks, step 7 deletes
            # message_queue, step 7b deletes task, step 8 deletes the
            # parent-side instance_hierarchy link. With our mocks the
            # "delete by instance_id" queries don't see the rows because
            # no locks were seeded for these instances, but message_queue,
            # task, and the parent hierarchy row are all removed.
            assert _count_where(s, MessageQueue, instance_id=root_id) == 0
            assert _count_where(s, Task, instance_id=root_id) == 0
            assert _count_where(s, InstanceHierarchy, parent_id=root_id) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — API endpoint
# ─────────────────────────────────────────────────────────────────────────────


class TestDeleteEndpoint:
    """``DELETE /instances/{id}?hard_delete=true`` returns ``hard_deleted=True``
    and the cascade summary; ``DELETE /instances/{id}`` (no param) returns
    ``{"terminated": True}`` and does NOT delete DB rows.
    """

    @pytest.fixture
    def app_with_manager(self, engine):
        """Build a FastAPI app wired to a real engine + a mock manager whose
        ``hard_delete_instance`` / ``terminate_instance`` call into the real
        repository + lifecycle service.

        This gives us end-to-end coverage of the router → manager → service →
        repository path against the real SQLModel cascade (not a mock).
        """
        from daemon.repositories.instance.repository import SQLModelInstanceRepository
        from daemon.services.instance_lifecycle import InstanceLifecycleService

        repo = SQLModelInstanceRepository(engine)

        manager = MagicMock()
        manager.is_write_paused = False
        manager.engine = engine
        manager.write_guard = WritePauseGuard()
        manager._instance_repository = repo
        manager._graph_tasks = {}
        manager._request_registry = MagicMock()
        manager._live_hub = MagicMock()
        manager._live_hub.cleanup_instance = AsyncMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._watcher_repo = MagicMock()
        manager._watcher_repo.remove_all_watches_for_instance = MagicMock(
            return_value=0,
        )
        manager._mcp_service = None
        manager.instances = {}
        manager._queue_repository = MagicMock()
        manager._queue_repository.delete_by_instance = MagicMock(return_value=0)
        manager._job_queue_mgmt_service = MagicMock()
        manager._job_queue_mgmt_service._dispatch_bus = MagicMock()
        manager._job_queue_mgmt_service._dispatch_bus.notify_all = MagicMock()
        manager._checkpointer = None  # Skip checkpoint sweep.

        # The router calls manager.get_instance() for the 404 check. Make it
        # return a minimal async-iterable-ish object — the router only checks
        # for KeyError, so a real Instance read is enough.
        async def _get_instance(iid: str):
            with Session(engine) as s:
                row = s.get(Instance, iid)
                if row is None:
                    raise KeyError(iid)
                s.expunge(row)
                return row

        manager.get_instance = _get_instance

        # Real lifecycle service for terminate + hard_delete, so the cascade
        # actually runs against the engine.
        job_q_svc = MagicMock(
            _repository=MagicMock(find_jobs_by_instance=MagicMock(return_value=[])),
            cancel_job=AsyncMock(return_value=True),
            complete_job=AsyncMock(return_value=None),
            release_lock_by_instance=AsyncMock(return_value=[]),
            trigger_next_job_sync=MagicMock(),
            get_job_by_instance_sync=MagicMock(return_value=None),
        )
        svc = InstanceLifecycleService(
            manager=manager,
            cancellation_service=MagicMock(),
            job_queue_service=job_q_svc,
        )
        manager._lifecycle_service = svc
        manager.hard_delete_instance = svc.hard_delete_instance
        manager.terminate_instance = svc.terminate_instance

        app = FastAPI()
        app.include_router(instances_router)
        # app.state.manager is read by the router via _get_manager(request).
        app.state.manager = manager
        return app, engine

    @pytest.fixture
    def seed_simple_instance(self, engine):
        """Insert one instance + a JobWatcher (so the cascade has real FK
        pressure). Returns the instance ID.
        """
        def _seed(iid: str | None = None) -> str:
            iid = iid or f"api-{uuid.uuid4().hex[:8]}"
            now = datetime.now(timezone.utc).isoformat()
            with Session(engine) as s:
                s.add(Instance(
                    instance_id=iid, agent_id="developer",
                    agent_dir="/tmp/agents/developer", agent_name="developer",
                    parent_id=None, status="running", version=1,
                    created_at=now, updated_at=now,
                ))
                job = JobItem(
                    agent_id="developer", agent_dir="/tmp/agents/developer",
                    message="m", source="api", project_id="p", instance_id=iid,
                )
                s.add(job); s.flush()
                s.add(JobWatcher(job_id=job.job_id, instance_id=iid))
                s.commit()
            return iid

        return _seed

    def test_delete_with_hard_delete_true_returns_hard_deleted_summary(
        self, app_with_manager, seed_simple_instance,
    ):
        app, engine = app_with_manager
        iid = seed_simple_instance()

        with TestClient(app) as client:
            resp = client.delete(f"/instances/{iid}?hard_delete=true")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["terminated"] is True
        assert body["hard_deleted"] is True
        assert body["root_instance_id"] == iid
        assert iid in body["tree_ids"]
        assert body["checkpoint_threads_deleted"] == 0
        assert "checkpoint_errors" in body
        assert body["checkpoint_errors"] == []
        assert "db_counts" in body
        # The seed planted exactly one row of each — the cascade removes
        # exactly that one. ``== 1`` (not ``>= 1``) catches both an
        # accidentally-under-counting cascade and an accidentally-
        # over-counting one (e.g. a second phantom instance row).
        counts = body["db_counts"]
        assert counts["instances"] == 1
        assert counts["job_watchers"] == 1
        assert counts["job_queue_items"] == 1

        # Instance row is GONE from the DB.
        with Session(engine) as s:
            assert s.get(Instance, iid) is None
            assert _count_where(s, JobWatcher, instance_id=iid) == 0
            assert _count_where(s, JobItem, instance_id=iid) == 0

    def test_delete_without_hard_delete_param_returns_terminated_only(
        self, app_with_manager, seed_simple_instance,
    ):
        """Default terminate path: no hard_deleted key, DB rows preserved."""
        app, engine = app_with_manager
        iid = seed_simple_instance()

        with TestClient(app) as client:
            resp = client.delete(f"/instances/{iid}")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"terminated": True}
        # CRITICAL: no hard_deleted key on the soft path.
        assert "hard_deleted" not in body

        # Instance row is STILL PRESENT (soft-delete only flips status).
        with Session(engine) as s:
            row = s.get(Instance, iid)
            assert row is not None
            assert _count_where(s, JobWatcher, instance_id=iid) == 1
            assert _count_where(s, JobItem, instance_id=iid) == 1

    def test_delete_nonexistent_instance_returns_404(
        self, app_with_manager,
    ):
        """Missing instance → 404, no cascade runs."""
        app, _ = app_with_manager

        with TestClient(app) as client:
            resp = client.delete("/instances/does-not-exist?hard_delete=true")

        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["code"] == "INSTANCE_NOT_FOUND"

    def test_delete_nonexistent_instance_soft_path_also_404(
        self, app_with_manager,
    ):
        """The 404 mapping applies to BOTH the soft and hard paths."""
        app, _ = app_with_manager

        with TestClient(app) as client:
            resp = client.delete("/instances/does-not-exist")

        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "INSTANCE_NOT_FOUND"