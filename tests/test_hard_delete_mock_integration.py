"""Hard-delete 3-level tree cascade + checkpoint cleanup mock tests.

Companion pack to ``tests/test_instance_hard_delete.py``. Covers six scenarios
the existing 2-level tree / API endpoint tests do not exercise:

    1. ``test_three_level_tree_cascade_complete`` — full cascade on a
       root → child → grandchild tree, with one row of every dependent
       table per node. Verifies all 10 tables are wiped AND unrelated
       instances survive.
    2. ``test_three_level_tree_idempotency`` — calling
       ``hard_delete_tree`` twice on the same 3-level tree is a safe
       no-op (does not raise, leaves tables empty).
    3. ``test_empty_tree_hard_delete`` — a leaf instance with NO children
       and NO dependent rows. ``hard_delete_tree`` removes only the
       instance row (all counts == 1 for instances, others == 0).
    4. ``test_already_terminated_instance_hard_delete`` — instance with
       ``status='terminated'`` plus a few dependent rows. Hard-delete
       still cleans the dependents (the feature is destructive — it
       is not gated on instance status).
    5. ``test_checkpoint_cleanup_best_effort`` — service-level test
       with a mocked ``CheckpointerAdapter`` whose ``adelete_thread``
       raises. Hard-delete still succeeds; the failed thread is
       surfaced via ``checkpoint_errors``; all 10 DB tables are still
       cleaned up.
    6. ``test_cascade_order_fk_safety`` — seed rows with REAL FK
       references (``job_watchers`` → ``instances.instance_id``,
       ``instance_mappings`` → ``source_configs.source_id``) and call
       ``hard_delete_tree``. No ``IntegrityError`` is raised — proving
       the 10-table cascade ORDER is FK-safe.

Mirrors the fixture pattern in ``tests/test_instance_hard_delete.py``
(real in-memory SQLite via :class:`StaticPool` with FK enforcement ON,
``SQLModel.metadata.create_all`` for table creation, no networking,
no daemon startup). Run standalone::

    python -m pytest tests/test_hard_delete_mock_integration.py -v
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, select

from daemon.repositories.dependency_bus.models import DependencyWatcher
from daemon.repositories.event.models import Event
from daemon.repositories.instance.models import Instance, InstanceHierarchy
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import JobItem, JobLock
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.source.models import InstanceMapping, SourceConfig
from daemon.repositories.task.models import Task
from daemon.write_pause_guard import WritePauseGuard


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Iterator[Engine]:
    """Real in-memory SQLite engine with FK enforcement enabled.

    Mirrors ``tests/test_instance_hard_delete.py::engine``. ``StaticPool``
    keeps a single connection alive so writes from one ``Session`` are
    visible to the next even when the writer ran on a different worker
    thread (``hard_delete_tree`` is off-loaded to ``asyncio.to_thread``).
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
    """Hard-delete cascade under test."""
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def write_guard() -> WritePauseGuard:
    """Fresh ``WritePauseGuard`` — not paused."""
    return WritePauseGuard()


@pytest.fixture
def seed_three_level_tree(engine: Engine) -> Any:
    """Factory for a 3-instance, 3-level tree (root → child → grandchild).

    Each instance has one dependent row in EVERY one of the 10 cascade
    tables, so a successful run leaves all 10 tables empty for these IDs.

    Seeds are split across three sessions — same pattern as
    ``tests/test_instance_hard_delete.py::seed_tree`` — to keep
    ``SourceConfig`` FK targets live before ``InstanceMapping`` inserts
    fire (the ``source_id`` FK on ``instance_mappings`` is REAL).
    """

    def _seed(
        root_id: str = "troot-001",
        child_id: str = "tmid-001",
        grandchild_id: str = "tleaf-001",
        *,
        agent_id: str = "developer",
        queue_id: str = "system_fifo_queue",
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        tree_ids = [root_id, child_id, grandchild_id]

        # Session 1 — Instance + InstanceHierarchy rows. Hierarchy links
        # are root→child and child→grandchild (BFS shape).
        with Session(engine) as s:
            s.add(Instance(
                instance_id=root_id,
                agent_id=agent_id,
                agent_dir=f"/tmp/agents/{agent_id}",
                agent_name=agent_id,
                parent_id=None,
                status="running",
                version=1,
                created_at=now, updated_at=now,
            ))
            s.add(Instance(
                instance_id=child_id,
                agent_id=agent_id,
                agent_dir=f"/tmp/agents/{agent_id}",
                agent_name=agent_id,
                parent_id=root_id,
                status="running",
                version=1,
                created_at=now, updated_at=now,
            ))
            s.add(Instance(
                instance_id=grandchild_id,
                agent_id=agent_id,
                agent_dir=f"/tmp/agents/{agent_id}",
                agent_name=agent_id,
                parent_id=child_id,
                status="running",
                version=1,
                created_at=now, updated_at=now,
            ))
            s.add(InstanceHierarchy(
                parent_id=root_id, child_id=child_id, created_at=now,
            ))
            s.add(InstanceHierarchy(
                parent_id=child_id, child_id=grandchild_id, created_at=now,
            ))
            s.commit()

        # Session 2 — SourceConfig rows for the InstanceMapping source_id FK.
        with Session(engine) as s:
            for iid in tree_ids:
                s.add(SourceConfig(
                    source_id=f"src-{iid}",
                    source_type="telegram",
                    name=f"source-{iid}",
                ))
            s.commit()

        # Session 3 — one row of each dependent table per instance.
        # lock_slot is unique per (project_id, queue_id), so the three
        # locks span slots 0/1/2 (matching the seed_tree fixture style).
        with Session(engine) as s:
            for idx, iid in enumerate(tree_ids):
                job = JobItem(
                    agent_id=agent_id,
                    agent_dir=f"/tmp/agents/{agent_id}",
                    message=f"msg for {iid}",
                    source="api",
                    project_id="test-project",
                    instance_id=iid,
                )
                s.add(job)
                s.flush()  # populate job.job_id before dependent inserts
                s.add(JobWatcher(job_id=job.job_id, instance_id=iid))
                s.add(JobLock(
                    project_id="test-project",
                    queue_id=queue_id,
                    job_id=job.job_id,
                    instance_id=iid,
                    lock_slot=idx,
                ))
                s.add(Task(instance_id=iid))
                s.add(Event(instance_id=iid, kind="message_received"))
                s.add(MessageQueue(instance_id=iid, content=f"hi {iid}"))
                s.add(DependencyWatcher(
                    source_task_id=f"task-{iid}",
                    target_instance_id=iid,
                    state="PENDING",
                ))
                s.add(InstanceMapping(
                    source_id=f"src-{iid}",
                    external_user_id=f"user-{iid}",
                    agent_instance_id=iid,
                    agent_id=agent_id,
                    agent_dir=f"/tmp/agents/{agent_id}",
                ))
            s.commit()

        return {
            "tree_ids": tree_ids,
            "root_id": root_id,
            "child_id": child_id,
            "grandchild_id": grandchild_id,
            "deps_per_instance": 8,
            "instances_total": 3,
            "hierarchy_rows": 2,
        }

    return _seed


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _count_where(session: Session, model, **filters) -> int:
    """Count rows in ``model`` matching the given attribute filters."""
    stmt = select(model)
    for attr, value in filters.items():
        stmt = stmt.where(getattr(model, attr) == value)
    return len(list(session.exec(stmt)))


def _count_all(session: Session, model) -> int:
    """Count all rows in ``model``."""
    return len(list(session.exec(select(model))))


def _build_mock_manager_for_checkpoint_test(
    engine: Engine,
    write_guard: WritePauseGuard,
    checkpointer_to_inject: Any,
) -> Any:
    """Build a MagicMock-shaped manager that delegates the cascade to a
    real SQLModelInstanceRepository while keeping the rest of the
    InstanceLifecycleService dependencies stubbed.

    The ``checkpointer_to_inject`` argument is what
    ``self._manager._checkpointer`` returns. Pass a real
    ``CheckpointerAdapter`` (with a mocked ``adelete_thread``) to
    exercise the checkpoint sweep branch in ``hard_delete_instance``.
    """
    repo = SQLModelInstanceRepository(engine)
    manager = MagicMock()
    manager.is_write_paused = False
    manager.engine = engine
    manager.write_guard = write_guard
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
    manager._checkpointer = checkpointer_to_inject
    return manager


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — 3-level tree cascade complete
# ─────────────────────────────────────────────────────────────────────────────


class TestThreeLevelTreeCascade:
    """``hard_delete_tree`` removes every dependent row across all 10
    tables for a 3-instance, 3-level tree in a single transaction.
    """

    def test_three_level_tree_cascade_complete(
        self, engine, instance_repo, seed_three_level_tree,
    ):
        info = seed_three_level_tree()
        tree_ids = info["tree_ids"]

        # Sanity-check the seed planted 1 row per dependent table per
        # instance, and that the hierarchy has the expected shape
        # (root→child, child→grandchild = 2 rows).
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
            assert _count_all(s, Instance) == 3
            assert _count_all(s, InstanceHierarchy) == 2

        # ── Cascade ────────────────────────────────────────────────
        result = instance_repo.hard_delete_tree(tree_ids)

        # Result shape — every counter == 3, hierarchy == 2.
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
        assert counts["instances"] == 3

        # ── Every table empty for these IDs ────────────────────────
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
            assert _count_all(s, Instance) == 0
            assert _count_all(s, InstanceHierarchy) == 0

    def test_three_level_cascade_does_not_touch_unrelated_instances(
        self, engine, instance_repo, seed_three_level_tree,
    ):
        """The cascade is restricted to ``tree_ids``. A second tree in
        the same engine survives untouched — proves no ``DELETE`` is
        missing its ``WHERE`` clause.
        """
        target = seed_three_level_tree(
            root_id="tgt-root", child_id="tgt-mid", grandchild_id="tgt-leaf",
            queue_id="system_fifo_queue",
        )
        # Different queue_id avoids the (project_id, queue_id, lock_slot)
        # UNIQUE collision with the target tree's locks.
        other = seed_three_level_tree(
            root_id="oth-root", child_id="oth-mid", grandchild_id="oth-leaf",
            queue_id="system_parallel_queue",
        )

        result = instance_repo.hard_delete_tree(target["tree_ids"])
        assert result["deleted"] is True

        # Target tree gone; every dependent and FK row gone.
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
            # Other tree untouched.
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


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — Idempotency on a 3-level tree
# ─────────────────────────────────────────────────────────────────────────────


class TestThreeLevelIdempotency:
    """``hard_delete_tree`` is a no-op the second time — no error, all
    counts remain zero.
    """

    def test_second_call_after_full_cascade_is_safe(
        self, engine, instance_repo, seed_three_level_tree,
    ):
        info = seed_three_level_tree()
        tree_ids = info["tree_ids"]

        first = instance_repo.hard_delete_tree(tree_ids)
        assert first["deleted"] is True
        # The first run moved every dependent row; all counts > 0.
        assert all(v > 0 for v in first["counts"].values())

        # ── Second call — no exception, all counts == 0 ───────────
        second = instance_repo.hard_delete_tree(tree_ids)

        assert second["deleted"] is False
        assert second["tree_ids"] == sorted(tree_ids) or set(second["tree_ids"]) == set(tree_ids)
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
            "instances": 0,
        }

        # The tables are still empty.
        with Session(engine) as s:
            assert _count_all(s, Instance) == 0
            assert _count_all(s, InstanceHierarchy) == 0
            assert _count_all(s, JobItem) == 0
            assert _count_all(s, JobLock) == 0
            assert _count_all(s, JobWatcher) == 0
            assert _count_all(s, Task) == 0
            assert _count_all(s, Event) == 0
            assert _count_all(s, MessageQueue) == 0
            assert _count_all(s, DependencyWatcher) == 0
            assert _count_all(s, InstanceMapping) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Empty tree hard delete
# ─────────────────────────────────────────────────────────────────────────────


class TestEmptyTreeHardDelete:
    """A single instance with NO children and NO dependent rows is a
    valid tree of size 1. ``hard_delete_tree([leaf])`` removes it and
    touches no other row.
    """

    def test_leaf_only_instance_hard_delete(
        self, engine, instance_repo,
    ):
        leaf_id = f"leaf-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        with Session(engine) as s:
            s.add(Instance(
                instance_id=leaf_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                parent_id=None,
                status="running",
                version=1,
                created_at=now, updated_at=now,
            ))
            s.commit()
        # Sanity.
        with Session(engine) as s:
            assert s.get(Instance, leaf_id) is not None
            assert _count_all(s, JobWatcher) == 0
            assert _count_all(s, Task) == 0
            assert _count_all(s, Event) == 0

        # ── Cascade on a single, bare instance ────────────────────
        result = instance_repo.hard_delete_tree([leaf_id])

        assert result["deleted"] is True
        assert result["tree_ids"] == [leaf_id]
        # Only the ``instances`` counter is non-zero — every other table
        # had zero matching rows.
        counts = result["counts"]
        assert counts["instances"] == 1
        assert counts["job_locks"] == 0
        assert counts["job_queue_items"] == 0
        assert counts["job_watchers"] == 0
        assert counts["tasks"] == 0
        assert counts["events"] == 0
        assert counts["message_queue"] == 0
        assert counts["dependency_watchers"] == 0
        assert counts["instance_mappings"] == 0
        assert counts["instance_hierarchy"] == 0  # no parent link either

        # The instance row is gone; the engine is otherwise empty.
        with Session(engine) as s:
            assert s.get(Instance, leaf_id) is None
            assert _count_all(s, Instance) == 0
            assert _count_all(s, InstanceHierarchy) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — Already-terminated instance hard delete
# ─────────────────────────────────────────────────────────────────────────────


class TestAlreadyTerminatedHardDelete:
    """A terminated instance whose dependent rows still exist (e.g. a
    partial cleanup from an earlier failed terminate) is still
    hard-delete-able. The destructive cascade is NOT gated on instance
    status.
    """

    def test_terminated_instance_with_dependents_hard_deletes_clean(
        self, engine, instance_repo,
    ):
        root_id = "term-root"
        now = datetime.now(timezone.utc).isoformat()
        with Session(engine) as s:
            s.add(Instance(
                instance_id=root_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                parent_id=None,
                status="terminated",  # already terminated
                version=1,
                created_at=now, updated_at=now,
            ))
            job = JobItem(
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="leftover",
                source="api",
                project_id="test-project",
                instance_id=root_id,
            )
            s.add(job); s.flush()
            s.add(JobWatcher(job_id=job.job_id, instance_id=root_id))
            s.add(Task(instance_id=root_id))
            s.add(Event(instance_id=root_id, kind="message_received"))
            s.commit()

        # Sanity — pre-cascade row counts.
        with Session(engine) as s:
            assert _count_where(s, JobItem, instance_id=root_id) == 1
            assert _count_where(s, JobWatcher, instance_id=root_id) == 1
            assert _count_where(s, Task, instance_id=root_id) == 1
            assert _count_where(s, Event, instance_id=root_id) == 1

        # ── Hard-delete despite terminated status ─────────────────
        result = instance_repo.hard_delete_tree([root_id])

        assert result["deleted"] is True
        counts = result["counts"]
        assert counts["instances"] == 1
        assert counts["job_watchers"] == 1
        assert counts["job_queue_items"] == 1
        assert counts["tasks"] == 1
        assert counts["events"] == 1

        # All dependents are gone — the terminated-status instance no
        # longer holds any FK-protected or FK-free dependent row.
        with Session(engine) as s:
            assert s.get(Instance, root_id) is None
            assert _count_where(s, JobItem, instance_id=root_id) == 0
            assert _count_where(s, JobWatcher, instance_id=root_id) == 0
            assert _count_where(s, Task, instance_id=root_id) == 0
            assert _count_where(s, Event, instance_id=root_id) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — Checkpoint cleanup best-effort
# ─────────────────────────────────────────────────────────────────────────────


class TestCheckpointCleanupBestEffort:
    """When ``checkpointer.adelete_thread`` raises, the DB cascade must
    STILL succeed (best-effort sweep — the maintenance orphan-thread
    job will retry the failed threads on its next cycle).
    """

    @pytest.mark.asyncio
    async def test_checkpoint_failure_does_not_block_db_cascade(
        self, engine, write_guard, seed_three_level_tree,
    ):
        """Service-level: mock ``adelete_thread`` to raise, and verify
        the cascade still runs + the failed thread ID is surfaced in
        ``checkpoint_errors``.
        """
        from daemon.services.instance_lifecycle import InstanceLifecycleService

        info = seed_three_level_tree()
        tree_ids = info["tree_ids"]

        # Capture what tree_ids the real repo's ``hard_delete_tree``
        # was called with (proves the cascade ran against the right set).
        # The spy is patched onto the *class* via ``patch.object`` — it is
        # therefore called as an unbound function with ``self`` as the
        # first arg, so accept (self, tree_ids).
        captured: dict[str, Any] = {}
        real_repo = SQLModelInstanceRepository(engine)

        original_hard_delete_tree = real_repo.hard_delete_tree

        def _spy_hard_delete_tree(_self, tree_ids_arg):
            captured["tree_ids"] = list(tree_ids_arg)
            return original_hard_delete_tree(tree_ids_arg)

        # Patch the repo into the manager AFTER wrapping its method.
        # We also patch ``adelete_thread`` on the checkpointer to raise
        # — the sweep must NOT abort the rest of the cascade.
        failing_checkpointer = MagicMock()
        failing_checkpointer.adelete_thread = AsyncMock(
            side_effect=RuntimeError("simulated checkpoint failure"),
        )

        manager = MagicMock()
        manager.is_write_paused = False
        manager.engine = engine
        manager.write_guard = write_guard
        manager._instance_repository = real_repo
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
        manager._checkpointer = failing_checkpointer

        # Stub the lifecycle's terminate so we don't have to wire up
        # the full graph / SSE machinery — but still keep enough side
        # effects to make ``hard_delete_instance``'s step 2 realistic.
        async def _fake_terminate(instance_id: str) -> bool:
            return True

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
        svc.terminate_instance = _fake_terminate
        # Wrap the real repo method so we can capture the tree_ids that
        # the service passed in.
        with patch.object(
            type(real_repo), "hard_delete_tree", _spy_hard_delete_tree,
        ):
            result = await svc.hard_delete_instance(info["root_id"])

        # ── The DB cascade ran against the right tree ─────────────
        assert sorted(captured["tree_ids"]) == sorted(tree_ids)

        # ── adelete_thread was attempted once per tree node ────────
        assert failing_checkpointer.adelete_thread.await_count == len(tree_ids)

        # ── Return shape: deleted=True, errors contain every id ────
        assert result["deleted"] is True
        assert result["root_instance_id"] == info["root_id"]
        assert sorted(result["tree_ids"]) == sorted(tree_ids)
        assert result["checkpoint_threads_deleted"] == 0
        assert sorted(result["checkpoint_errors"]) == sorted(tree_ids)
        # The DB cascade counts match what the seed planted.
        counts = result["counts"]
        assert counts["instances"] == 3
        assert counts["instance_hierarchy"] == 2
        assert counts["job_watchers"] == 3
        assert counts["job_queue_items"] == 3
        assert counts["job_locks"] == 3
        assert counts["tasks"] == 3
        assert counts["events"] == 3
        assert counts["message_queue"] == 3
        assert counts["dependency_watchers"] == 3
        assert counts["instance_mappings"] == 3

        # ── DB-level proof: all 10 cascade tables are cleaned ──────
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
            assert _count_all(s, Instance) == 0
            assert _count_all(s, InstanceHierarchy) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — Cascade order FK safety
# ─────────────────────────────────────────────────────────────────────────────


class TestCascadeOrderFKSafety:
    """The 10-table cascade order respects FK constraints. With
    ``PRAGMA foreign_keys=ON``, a delete in the wrong order would
    raise ``IntegrityError`` on the REAL FK (job_watchers.instance_id
    → instances.instance_id) or on the InstanceMapping.source_id FK
    to ``source_configs.source_id``.
    """

    def test_real_fk_relationships_do_not_raise(
        self, engine, instance_repo, seed_three_level_tree,
    ):
        """The seed already inserts rows with REAL FKs:
          - job_watchers.instance_id → instances.instance_id
          - instance_mappings.source_id → source_configs.source_id
        ``hard_delete_tree`` MUST issue the dependent DELETEs in the
        order documented in the production cascade — otherwise one of
        these FKs would fire and surface as ``IntegrityError``.
        """
        info = seed_three_level_tree()
        # Sanity: confirm the real FKs exist for at least one node.
        with Session(engine) as s:
            root = info["root_id"]
            assert _count_where(s, JobWatcher, instance_id=root) == 1
            assert _count_where(s, InstanceMapping, agent_instance_id=root) == 1
            # SourceConfig rows are NOT instance-scoped — they survive.
            assert _count_where(s, SourceConfig, source_id=f"src-{root}") == 1

        # ── Should not raise — the production cascade order is FK-safe
        result = instance_repo.hard_delete_tree(info["tree_ids"])

        # SourceConfig rows are intentionally preserved (they are not
        # instance-scoped) but every instance-scoped dependent row is
        # gone.
        with Session(engine) as s:
            for iid in info["tree_ids"]:
                assert s.get(Instance, iid) is None
                assert _count_where(s, JobWatcher, instance_id=iid) == 0
                assert _count_where(s, InstanceMapping, agent_instance_id=iid) == 0
            # SourceConfigs survive — proves we did NOT accidentally
            # wipe the source catalog by missing a WHERE clause.
            for iid in info["tree_ids"]:
                assert _count_where(s, SourceConfig, source_id=f"src-{iid}") == 1

        assert result["deleted"] is True
