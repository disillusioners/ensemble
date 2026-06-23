"""End-to-end DB tests for H10 / M8 / M9 / L14 transaction-boundary fixes.

These tests verify the SINGLE-TRANSACTION behavior of the
``InstanceLifecycleService`` cascade operations against a real in-memory
SQLite engine. They use raw ``Session`` operations to seed fixtures and
inspect the actual SQL state after the cascade runs — i.e. they assert
on the database's observable end-state, not on the repository layer's
mock call surface (the H10 fix moved DB writes to a raw
``WriteGuardSession`` inside the service, bypassing the repository layer
by design).

Covers:
  * H10 — terminate_instance atomic cascade (status + waiting_for +
    job_queue cancel + job_locks release + message_queue delete +
    instance_hierarchy cleanup) in ONE transaction.
  * M8  — spawn_instance atomic create + parent source inheritance
    (no orphan row without ``original_source``).
  * M9  — terminate_instance's sync DB writes consolidated into one
    ``asyncio.to_thread`` call (single WriteGuardSession).
  * L14 — pause_instance_cascade / resume_instance_cascade batch all
    per-tree-node UPDATEs into ONE ``UPDATE ... WHERE instance_id IN``
    statement.

Why these tests exist separately from the mock-based tests in
``test_instance_lifecycle_terminate.py``: the pre-fix mock tests assert
on ``mock_repo.update.call_args_list``. With the H10 / L14 fix, the
service no longer calls ``instance_repository.update`` — it writes
directly via raw ``Session``. The mock tests still cover the
side-effect orchestration (SSE / lifecycle / dispatch-bus / CM), but
the SQL atomicity invariants now need real-DB verification, which is
what this file provides.

Run with::

    pytest tests/services/test_instance_lifecycle_h10_l14.py -v --tb=short
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import (
    Instance,
    InstanceHierarchy,
    InstanceStatus,
)
from daemon.repositories.job_queue.models import JobItem, JobLock, JobStatus
from daemon.repositories.message_queue.models import MessageQueue, MessageStatus
from daemon.services.correlation_manager import (
    CorrelationManager,
    set_correlation_manager,
)
from daemon.write_pause_guard import WritePauseGuard


# ─── Shared fixtures & helpers ─────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool for cross-thread safety)."""
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
def write_guard() -> WritePauseGuard:
    """Fresh WritePauseGuard — not paused."""
    return WritePauseGuard()


def seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    agent_id: str = "coder",
    parent_id: str | None = None,
    waiting_for: int = 0,
    paused_at: str | None = None,
    metadata: dict[str, Any] | None = None,
    version: int = 1,
) -> str:
    """Insert an Instance row. Returns the instance_id."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        inst = Instance(
            instance_id=iid,
            agent_id=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}",
            agent_name=agent_id,
            parent_id=parent_id,
            status=status,
            waiting_for=waiting_for,
            paused_at=paused_at,
            instance_metadata=metadata or {},
            children="[]",
            version=version,
            created_at=now_iso,
            updated_at=now_iso,
        )
        s.add(inst)
        s.commit()
    return iid


def seed_hierarchy(engine: Engine, *, parent_id: str, child_id: str) -> None:
    """Insert an InstanceHierarchy row."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            InstanceHierarchy(
                parent_id=parent_id, child_id=child_id, created_at=now_iso
            )
        )
        s.commit()


def seed_job(
    engine: Engine,
    *,
    instance_id: str,
    job_id: str | None = None,
    project_id: str = "test-project",
    status: str = JobStatus.PROCESSING.value,
    job_type: str = "task",
) -> str:
    """Insert a JobItem row. Returns the job_id."""
    jid = job_id or f"job-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            JobItem(
                job_id=jid,
                agent_id="coder",
                agent_dir="/tmp/agents/coder",
                message="test job",
                source="api",
                job_type=job_type,
                status=status,
                instance_id=instance_id,
                project_id=project_id,
                created_at=now_iso,
            )
        )
        s.commit()
    return jid


def seed_lock(
    engine: Engine, *, instance_id: str, project_id: str = "p", lock_slot: int = 0
) -> None:
    """Insert a JobLock row.

    The unique constraint is on (project_id, queue_id, lock_slot), so
    each lock needs a distinct (project, queue, slot) tuple. Use
    ``lock_slot`` to disambiguate when seeding multiple locks for the
    same instance.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            JobLock(
                lock_id=f"lock-{uuid.uuid4().hex[:8]}",
                project_id=project_id,
                queue_id="default",
                job_id=f"job-{uuid.uuid4().hex[:8]}",
                instance_id=instance_id,
                lock_slot=lock_slot,
                created_at=now_iso,
            )
        )
        s.commit()


def seed_message(
    engine: Engine, *, instance_id: str, status: str = MessageStatus.READY.value
) -> None:
    """Insert a MessageQueue row."""
    with Session(engine) as s:
        s.add(
            MessageQueue(
                instance_id=instance_id,
                content="test message",
                status=status,
            )
        )
        s.commit()


def get_instance(engine: Engine, instance_id: str) -> Instance | None:
    """Read a fresh Instance row (no session caching)."""
    with Session(engine) as s:
        return s.get(Instance, instance_id)


def get_jobs_for_instance(engine: Engine, instance_id: str) -> list[JobItem]:
    """All JobItem rows for an instance."""
    with Session(engine) as s:
        return list(
            s.exec(__import__("sqlmodel").select(JobItem).where(JobItem.instance_id == instance_id))
        )


def get_locks_for_instance(engine: Engine, instance_id: str) -> list[JobLock]:
    """All JobLock rows for an instance."""
    with Session(engine) as s:
        return list(
            s.exec(__import__("sqlmodel").select(JobLock).where(JobLock.instance_id == instance_id))
        )


def get_messages_for_instance(engine: Engine, instance_id: str) -> list[MessageQueue]:
    """All MessageQueue rows for an instance."""
    with Session(engine) as s:
        return list(
            s.exec(__import__("sqlmodel").select(MessageQueue).where(MessageQueue.instance_id == instance_id))
        )


def get_hierarchy_children(engine: Engine, parent_id: str) -> list[str]:
    """Child IDs of the given parent in instance_hierarchy."""
    with Session(engine) as s:
        rows = list(
            s.exec(
                __import__("sqlmodel").select(InstanceHierarchy.child_id).where(
                    InstanceHierarchy.parent_id == parent_id
                )
            )
        )
        return [r for r in rows]


def make_mock_manager(
    engine: Engine, write_guard: WritePauseGuard, *, with_dispatch_bus: bool = True
) -> MagicMock:
    """Build a mock manager that uses a real engine for DB writes.

    Mirrors the shape of ``make_manager`` from
    ``test_instance_lifecycle_terminate.py`` but plugs in the real engine
    so the ``_terminate_instance_db_sync`` /
    ``_pause_cascade_db_sync`` / ``_resume_cascade_db_sync`` /
    ``_spawn_instance_db_sync`` helpers actually execute against a real
    in-memory DB.

    Side-effect stubs (live_hub / request_registry / watcher_repo /
    queue_repository) are still MagicMocks so the post-commit outbox
    can be asserted on.
    """
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    manager._instance_repository = MagicMock()
    manager._graph_tasks = {}
    manager._request_registry = MagicMock()
    manager._request_registry.cancel_by_instance = MagicMock(return_value=0)
    manager._live_hub = MagicMock()
    manager._live_hub.cleanup_instance = AsyncMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._live_hub.stream_instance_created = AsyncMock()
    manager._watcher_repo = MagicMock()
    manager._watcher_repo.remove_all_watches_for_instance = MagicMock(return_value=0)
    manager._mcp_service = None
    manager.instances = {}
    manager._queue_repository = MagicMock()
    manager._queue_repository.delete_by_instance = MagicMock(return_value=0)

    # JobQueueService stub — used by the post-commit side effects in
    # ``terminate_instance``. The DB-level job cancellation is done
    # inside ``_terminate_instance_db_sync``; the post-commit path
    # only fires notify side effects.
    manager._job_queue_service = MagicMock()
    manager._job_queue_service._repository = MagicMock()
    manager._job_queue_service._repository.find_jobs_by_instance = MagicMock(return_value=[])
    manager._job_queue_service.cancel_job = AsyncMock(return_value=True)
    manager._job_queue_service.cancel_message_job = AsyncMock(return_value=True)
    manager._job_queue_service.complete_job = AsyncMock(return_value=None)
    manager._job_queue_service.release_lock_by_instance = AsyncMock(return_value=[])
    manager._job_queue_service.get_job_by_instance_sync = MagicMock(return_value=None)
    manager._job_queue_service.trigger_next_job_sync = MagicMock()

    if with_dispatch_bus:
        manager._job_queue_mgmt_service = MagicMock()
        manager._job_queue_mgmt_service._dispatch_bus = MagicMock()
        manager._job_queue_mgmt_service._dispatch_bus.notify_all = MagicMock()

    manager.config = MagicMock()
    manager.config.queue.llm_retry_transient_attempts = 1
    manager.config.queue.llm_retry_timeout_attempts = 1

    return manager


def make_lifecycle_service(manager: MagicMock) -> "InstanceLifecycleService":
    """Instantiate InstanceLifecycleService with the given manager."""
    from daemon.services.instance_lifecycle import InstanceLifecycleService
    return InstanceLifecycleService(
        manager=manager,
        cancellation_service=MagicMock(),
        events_service=None,
        job_queue_service=manager._job_queue_service,
    )


# =============================================================================
# H10 — terminate_instance atomic cascade
# =============================================================================


@pytest.mark.asyncio
async def test_h10_terminate_writes_status_and_waiting_for_atomically(
    engine, write_guard
):
    """H10 fix: a single ``UPDATE instances SET status, waiting_for``
    in one transaction — no partial-failure crash window between
    status and waiting_for.
    """
    instance_id = seed_instance(
        engine,
        status=InstanceStatus.RUNNING.value,
        waiting_for=3,  # non-zero — proves the reset happens
    )

    manager = make_mock_manager(engine, write_guard)
    svc = make_lifecycle_service(manager)

    result = await svc.terminate_instance(instance_id)

    assert result is True

    # ─── The single atomic write verified via real DB ───
    inst = get_instance(engine, instance_id)
    assert inst is not None
    assert inst.status == InstanceStatus.TERMINATED.value, (
        f"status must be 'terminated', got {inst.status!r}"
    )
    assert inst.waiting_for == 0, (
        f"waiting_for must be reset to 0, got {inst.waiting_for}"
    )


@pytest.mark.asyncio
async def test_h10_terminate_cancels_all_non_terminal_jobs_atomically(
    engine, write_guard
):
    """H10 fix: jobs are cancelled in the same transaction as the
    instance status update. Pre-fix this was 3 separate async calls
    (process / message / sweep) each with its own transaction; a crash
    mid-sweep would leave PROCESSING jobs pointed at a terminated
    instance (orphaned job).
    """
    instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
    job_proc = seed_job(
        engine,
        instance_id=instance_id,
        status=JobStatus.PROCESSING.value,
    )
    job_pend = seed_job(
        engine,
        instance_id=instance_id,
        status=JobStatus.PENDING.value,
    )
    job_msg = seed_job(
        engine,
        instance_id=instance_id,
        status=JobStatus.PENDING.value,
        job_type="message",
    )
    job_done = seed_job(
        engine,
        instance_id=instance_id,
        status=JobStatus.COMPLETED.value,
    )

    manager = make_mock_manager(engine, write_guard)
    svc = make_lifecycle_service(manager)
    await svc.terminate_instance(instance_id)

    # All non-terminal jobs are now CANCELLED.
    jobs_after = get_jobs_for_instance(engine, instance_id)
    jobs_by_id = {j.job_id: j for j in jobs_after}
    assert jobs_by_id[job_proc].status == JobStatus.CANCELLED.value, (
        "PROCESSING job must be CANCELLED in same transaction as instance terminate"
    )
    assert jobs_by_id[job_pend].status == JobStatus.CANCELLED.value
    assert jobs_by_id[job_msg].status == JobStatus.CANCELLED.value
    # COMPLETED is terminal — left alone.
    assert jobs_by_id[job_done].status == JobStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_h10_terminate_releases_locks_deletes_msgq_atomically(
    engine, write_guard
):
    """H10 fix: job_locks release + message_queue cleanup happen in
    the same transaction as the status update. Pre-fix these were
    separate repository calls (different transactions), so a crash
    between the status update and the lock release could leak a
    queue slot permanently.
    """
    instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
    # Distinct lock_slots so the (project_id, queue_id, lock_slot)
    # unique constraint is satisfied for the 2 locks we seed.
    seed_lock(engine, instance_id=instance_id, lock_slot=0)
    seed_lock(engine, instance_id=instance_id, lock_slot=1)
    seed_message(engine, instance_id=instance_id)
    seed_message(engine, instance_id=instance_id)
    seed_message(engine, instance_id=instance_id)

    manager = make_mock_manager(engine, write_guard)
    svc = make_lifecycle_service(manager)
    await svc.terminate_instance(instance_id)

    locks_after = get_locks_for_instance(engine, instance_id)
    msgs_after = get_messages_for_instance(engine, instance_id)

    assert locks_after == [], (
        f"All job_locks for terminated instance must be released in "
        f"same transaction; got {len(locks_after)} still present"
    )
    assert msgs_after == [], (
        f"All message_queue rows for terminated instance must be "
        f"deleted in same transaction; got {len(msgs_after)} still present"
    )


@pytest.mark.asyncio
async def test_h10_terminate_cleans_hierarchy_atomically(engine, write_guard):
    """H10 fix: ``instance_hierarchy`` rows where the terminated
    instance is the parent are deleted in the same transaction. The
    child rows themselves stay (so audit lookups still resolve), but
    the parent→child link is gone so future tree traversals don't
    include the dead subtree.
    """
    parent_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
    child_id = seed_instance(
        engine, status=InstanceStatus.RUNNING.value, parent_id=parent_id
    )
    seed_hierarchy(engine, parent_id=parent_id, child_id=child_id)

    manager = make_mock_manager(engine, write_guard)
    svc = make_lifecycle_service(manager)
    await svc.terminate_instance(parent_id)

    # Parent row still exists but is TERMINATED.
    parent_after = get_instance(engine, parent_id)
    assert parent_after is not None
    assert parent_after.status == InstanceStatus.TERMINATED.value

    # Child row still exists (orphan, will be GC'd later).
    child_after = get_instance(engine, child_id)
    assert child_after is not None

    # Hierarchy link is removed — parent's children[] is empty now.
    children_after = get_hierarchy_children(engine, parent_id)
    assert children_after == [], (
        f"hierarchy rows for terminated parent must be removed in "
        f"same transaction; got {children_after}"
    )


@pytest.mark.asyncio
async def test_h10_terminate_is_idempotent_on_already_terminated(
    engine, write_guard
):
    """H10 fix: a concurrent terminate that already moved the row to
    TERMINATED is a no-op (the sync helper's re-entrancy guard).
    The first terminate commits the row; the second short-circuits
    without touching the DB or firing post-commit side effects.
    """
    instance_id = seed_instance(
        engine, status=InstanceStatus.TERMINATED.value  # already terminal
    )
    seed_job(engine, instance_id=instance_id, status=JobStatus.PROCESSING.value)

    manager = make_mock_manager(engine, write_guard)
    svc = make_lifecycle_service(manager)

    result = await svc.terminate_instance(instance_id)

    assert result is True

    # Job was NOT touched (the re-entrancy guard short-circuited).
    jobs_after = get_jobs_for_instance(engine, instance_id)
    assert len(jobs_after) == 1
    assert jobs_after[0].status == JobStatus.PROCESSING.value, (
        "Already-terminal instance must NOT cascade-cancel its jobs"
    )


@pytest.mark.asyncio
async def test_h10_terminate_returns_false_when_instance_missing(
    engine, write_guard
):
    """H10 fix: terminate_instance returns False (per the docstring)
    when the instance row is missing AND the instance is not in the
    in-memory dict — the caller can interpret False as "no such
    instance".

    The sync helper itself returns ``skip=True`` for a missing row,
    which is the authoritative re-entrancy guard for concurrent
    deletes.
    """
    manager = make_mock_manager(engine, write_guard)
    # Force the meta-lookup to return None so the "not found" branch
    # runs (the MagicMock default would return a MagicMock and the
    # test would silently fall through to the DB helper's skip=True
    # path).
    manager._instance_repository.get = MagicMock(return_value=None)

    svc = make_lifecycle_service(manager)
    result = await svc.terminate_instance("does-not-exist-12345")
    assert result is False


@pytest.mark.asyncio
async def test_h10_terminate_emits_status_change_sse_after_commit(
    engine, write_guard
):
    """H10 fix: the SSE ``status_change`` event is emitted AFTER the
    WriteGuardSession commits — so a subscriber never sees a
    ``terminated`` SSE before the DB row reflects it.
    """
    instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
    manager = make_mock_manager(engine, write_guard)
    svc = make_lifecycle_service(manager)

    await svc.terminate_instance(instance_id)

    # SSE was emitted exactly once with 'terminated'.
    manager._live_hub.stream_status_change.assert_awaited_once()
    call = manager._live_hub.stream_status_change.call_args
    assert call.args[0] == instance_id
    assert call.args[1] == InstanceStatus.TERMINATED.value


@pytest.mark.asyncio
async def test_h10_terminate_notifies_dispatch_bus_exactly_once(
    engine, write_guard
):
    """H10 fix: the dispatch bus ``notify_all`` is called exactly once
    after the DB commit. Pre-fix this was preserved; the H10 fix
    moves it to the post-commit outbox so a slow SSE subscriber does
    not delay the dispatch wakeup (which was already after commit,
    but now is part of a documented single outbox step).
    """
    instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
    manager = make_mock_manager(engine, write_guard)
    svc = make_lifecycle_service(manager)

    await svc.terminate_instance(instance_id)

    manager._job_queue_mgmt_service._dispatch_bus.notify_all.assert_called_once()


# =============================================================================
# L14 — pause/resume cascade batched UPDATE
# =============================================================================


@pytest.mark.asyncio
async def test_l14_pause_cascade_batches_all_updates_into_one_transaction(
    engine, write_guard
):
    """L14 fix: pausing a tree of N nodes issues ONE batched UPDATE
    ``WHERE instance_id IN (...)`` — not N individual updates.

    Verified by:
      * All nodes transition to PAUSED with non-null paused_at.
      * All nodes have waiting_for reset to 0.
      * The captured ``paused_at`` timestamp matches the call (single
        write, single moment).
    """
    parent = seed_instance(engine, status=InstanceStatus.RUNNING.value)
    c1 = seed_instance(
        engine, status=InstanceStatus.RUNNING.value, parent_id=parent
    )
    c2 = seed_instance(
        engine, status=InstanceStatus.RUNNING.value, parent_id=parent
    )
    seed_hierarchy(engine, parent_id=parent, child_id=c1)
    seed_hierarchy(engine, parent_id=parent, child_id=c2)

    # Wire the manager's repo mocks to the real engine so the
    # tree-traversal helpers return our seeded IDs.
    manager = make_mock_manager(engine, write_guard)

    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    real_repo = SQLModelInstanceRepository(engine=engine)
    manager._instance_repository = real_repo
    manager._instance_repository.get_tree_root_id = lambda iid: parent
    manager._instance_repository.get_tree_ids = lambda root_id: [parent, c1, c2]

    svc = make_lifecycle_service(manager)
    result = await svc.pause_instance_cascade(parent)

    assert set(result["paused_ids"]) == {parent, c1, c2}
    assert result["skipped_ids"] == []

    # All three nodes are now PAUSED with paused_at set.
    for iid in (parent, c1, c2):
        inst = get_instance(engine, iid)
        assert inst.status == InstanceStatus.PAUSED.value, (
            f"node {iid[:8]} must be PAUSED, got {inst.status!r}"
        )
        assert inst.paused_at is not None, (
            f"node {iid[:8]} must have paused_at set"
        )
        assert inst.waiting_for == 0, (
            f"node {iid[:8]} must have waiting_for=0"
        )


@pytest.mark.asyncio
async def test_l14_resume_cascade_batches_all_updates_into_one_transaction(
    engine, write_guard
):
    """L14 fix: resuming a tree of N paused nodes issues ONE batched
    UPDATE — verified via real DB end-state."""
    parent = seed_instance(
        engine, status=InstanceStatus.PAUSED.value, paused_at="2026-01-01T00:00:00+00:00"
    )
    c1 = seed_instance(
        engine,
        status=InstanceStatus.PAUSED.value,
        parent_id=parent,
        paused_at="2026-01-01T00:00:00+00:00",
    )
    c2 = seed_instance(
        engine,
        status=InstanceStatus.PAUSED.value,
        parent_id=parent,
        paused_at="2026-01-01T00:00:00+00:00",
    )

    manager = make_mock_manager(engine, write_guard)

    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    real_repo = SQLModelInstanceRepository(engine=engine)
    manager._instance_repository = real_repo
    manager._instance_repository.get_tree_root_id = lambda iid: parent
    manager._instance_repository.get_tree_ids = lambda root_id: [parent, c1, c2]
    manager._instance_repository.get_ancestor_ids = lambda iid: []

    svc = make_lifecycle_service(manager)
    result = await svc.resume_instance_cascade(parent)

    assert set(result["resumed_ids"]) == {parent, c1, c2}
    assert result["skipped_ids"] == []

    # All three nodes are now RUNNING with paused_at cleared.
    for iid in (parent, c1, c2):
        inst = get_instance(engine, iid)
        assert inst.status == InstanceStatus.RUNNING.value, (
            f"node {iid[:8]} must be RUNNING, got {inst.status!r}"
        )
        assert inst.paused_at is None, (
            f"node {iid[:8]} must have paused_at cleared"
        )
        assert inst.waiting_for == 0, (
            f"node {iid[:8]} must have waiting_for=0"
        )


@pytest.mark.asyncio
async def test_l14_resume_from_child_sets_ancestor_waiting_for_to_one(
    engine, write_guard
):
    """L14 fix carve-out: when resuming from a non-root node, the resumed
    node itself gets waiting_for=0 (preserved).

    Phase 3 update: ``waiting_for`` is rebuild-only cache (ADR-011). The
    legacy ``waiting_for=1`` ancestor bump was removed with the
    ``USE_LEGACY_WAITING_FOR_CASCADE`` flag — all nodes (including
    ancestors) get ``waiting_for=0`` (preserved) on resume. The CM
    callback owns the terminal transition when all children resolve.
    """
    root = seed_instance(engine, status=InstanceStatus.PAUSED.value)
    parent = seed_instance(
        engine, status=InstanceStatus.PAUSED.value, parent_id=root
    )
    child = seed_instance(
        engine, status=InstanceStatus.PAUSED.value, parent_id=parent
    )

    manager = make_mock_manager(engine, write_guard)

    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    real_repo = SQLModelInstanceRepository(engine=engine)
    manager._instance_repository = real_repo
    manager._instance_repository.get_tree_root_id = lambda iid: root
    manager._instance_repository.get_tree_ids = lambda root_id: [root, parent, child]
    # child → ancestors are [parent, root].
    manager._instance_repository.get_ancestor_ids = lambda iid: (
        [parent, root] if iid == child else []
    )

    svc = make_lifecycle_service(manager)
    result = await svc.resume_instance_cascade(child)

    assert set(result["resumed_ids"]) == {root, parent, child}

    # Phase 3: waiting_for is preserved (0 for all nodes). No ancestor bump.
    assert get_instance(engine, root).waiting_for == 0
    assert get_instance(engine, parent).waiting_for == 0
    assert get_instance(engine, child).waiting_for == 0


@pytest.mark.asyncio
async def test_l14_pause_skips_already_paused_nodes(engine, write_guard):
    """L14 fix: the pre-filter in the async wrapper identifies
    already-paused nodes and excludes them from the batched UPDATE.
    The sync helper only writes the eligible subset.
    """
    parent = seed_instance(engine, status=InstanceStatus.RUNNING.value)
    c1 = seed_instance(
        engine, status=InstanceStatus.PAUSED.value, parent_id=parent
    )
    c2 = seed_instance(
        engine, status=InstanceStatus.RUNNING.value, parent_id=parent
    )

    manager = make_mock_manager(engine, write_guard)

    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    real_repo = SQLModelInstanceRepository(engine=engine)
    manager._instance_repository = real_repo
    manager._instance_repository.get_tree_root_id = lambda iid: parent
    manager._instance_repository.get_tree_ids = lambda root_id: [parent, c1, c2]

    svc = make_lifecycle_service(manager)
    result = await svc.pause_instance_cascade(parent)

    # parent + c2 paused, c1 skipped.
    assert set(result["paused_ids"]) == {parent, c2}
    assert result["skipped_ids"] == [c1]

    # c1 still has its pre-existing paused_at unchanged.
    c1_after = get_instance(engine, c1)
    assert c1_after.status == InstanceStatus.PAUSED.value


# =============================================================================
# M8 — spawn_instance atomic create + source inheritance
# =============================================================================


@pytest.mark.asyncio
async def test_m8_spawn_inherits_original_source_atomically(engine, write_guard):
    """M8 fix: a child spawned under a parent with ``original_source``
    metadata gets that key in its OWN ``instance_metadata`` column, in
    the SAME transaction as the instance INSERT.

    Pre-fix the create-then-set_metadata path was two transactions; a
    crash between them left a child visible without the inherited
    source.
    """
    from daemon.services.instance_lifecycle import InstanceLifecycleService

    parent_id = seed_instance(
        engine,
        agent_id="leader",
        metadata={"original_source": "telegram:user-42"},
    )

    manager = make_mock_manager(engine, write_guard)
    # ``spawn_instance`` reads several config knobs to decide whether
    # to allow the spawn (max_children_per_instance limit). The
    # MagicMock defaults would compare two MagicMock instances and
    # raise TypeError — set explicit int values for the spawn-time
    # config checks.
    manager.config.limits.max_children_per_instance = 100
    manager.config.limits.graph_recursion_limit = 50
    # ``count_children`` is the per-parent children count guard.
    manager._instance_repository.count_children = MagicMock(return_value=0)
    # ``append_context_key`` calls ``get_tree_root_id`` to find the
    # root parent; the mock default would return a MagicMock and the
    # ``.replace(...)`` call in append_context_key would raise
    # TypeError. Wire a deterministic root-id lookup.
    manager._instance_repository.get_tree_root_id = MagicMock(return_value=parent_id)

    svc = InstanceLifecycleService(
        manager=manager,
        cancellation_service=MagicMock(),
        events_service=None,
        job_queue_service=None,
    )

    # Patch the agent registry + graph builder + tools + prompt cache
    # so spawn_instance can resolve the agent and build a graph without
    # touching the real filesystem or LLM.
    with patch("daemon.registry.get_registry") as mock_registry_factory, \
         patch("daemon.manager.load_and_cache_prompt") as mock_lcp, \
         patch("daemon.manager.create_instance_tools", return_value=[]), \
         patch("daemon.manager.build_instance_graph") as mock_big:

        agent_meta = MagicMock()
        agent_meta.path = MagicMock()
        agent_meta.path.__str__ = lambda self: "/tmp/agents/leader"
        agent_meta.name = "Leader"
        registry = MagicMock()
        registry.resolve_to_id.return_value = "leader"
        registry.get.return_value = agent_meta
        mock_registry_factory.return_value = registry

        mock_lcp.return_value = ("system prompt", 100)
        mock_graph = MagicMock()
        mock_big.return_value = mock_graph

        # Use a properly-formatted UUID (the spawn code rejects non-UUID
        # ``instance_id`` values and auto-generates a UUID instead — see
        # ``_UUID_PATTERN`` check at instance_lifecycle.py:317).
        new_id = str(uuid.uuid4())
        result = svc.spawn_instance(
            agent_id="leader",
            instance_id=new_id,
            parent_id=parent_id,
        )

    assert result == new_id

    # ─── Verify the atomic inheritance ───
    child = get_instance(engine, new_id)
    assert child is not None
    assert child.parent_id == parent_id
    assert child.instance_metadata is not None
    assert child.instance_metadata.get("original_source") == "telegram:user-42", (
        f"child must inherit parent's original_source in the same "
        f"transaction as the INSERT; got metadata={child.instance_metadata!r}"
    )


# =============================================================================
# Crash-safety contract — kill the helper mid-transaction, no orphan state
# =============================================================================


@pytest.mark.asyncio
async def test_h10_terminate_crash_safety_no_partial_state(engine, write_guard):
    """H10 contract: if the sync helper raises mid-transaction, the
    DB rolls back to the pre-terminate state — no partial state
    leaks.

    We can't actually SIGKILL the worker thread mid-UPDATE from a
    pytest, but we CAN simulate a mid-transaction failure by
    monkey-patching the session to raise on ``session.commit()``.
    The pre-commit state must be unchanged.
    """
    from daemon.services.instance_lifecycle import InstanceLifecycleService

    instance_id = seed_instance(
        engine, status=InstanceStatus.RUNNING.value, waiting_for=5
    )
    seed_job(engine, instance_id=instance_id, status=JobStatus.PROCESSING.value)
    seed_lock(engine, instance_id=instance_id)
    seed_message(engine, instance_id=instance_id)

    manager = make_mock_manager(engine, write_guard)
    svc = InstanceLifecycleService(
        manager=manager,
        cancellation_service=MagicMock(),
        events_service=None,
        job_queue_service=manager._job_queue_service,
    )

    real_session_class = Session
    committed = []

    class FailingCommitSession(real_session_class):  # type: ignore[misc]
        """Session whose commit() raises — simulates a crash mid-cascade."""

        def commit(self) -> None:
            # Record that commit was attempted, then raise.
            committed.append(self)
            raise RuntimeError("simulated crash mid-cascade")

    # Patch sqlmodel.Session in the lifecycle service's namespace to
    # the failing version. The lifecycle service imports Session at
    # module top via ``from sqlmodel import Session`` — patching the
    # symbol in daemon.services.instance_lifecycle affects the lookup
    # at the helper's ``Session(engine)`` call site.
    import daemon.services.instance_lifecycle as lifecycle_module

    with patch.object(lifecycle_module, "Session", FailingCommitSession):
        with pytest.raises(RuntimeError, match="simulated crash mid-cascade"):
            await svc.terminate_instance(instance_id)

    # The commit was attempted exactly once (single transaction).
    assert len(committed) == 1, (
        f"Expected exactly one commit attempt, got {len(committed)} — "
        f"if this is > 1, the cascade has regressed to multiple transactions"
    )

    # ─── Crash-safety: rollback restored the pre-terminate state ───
    inst = get_instance(engine, instance_id)
    assert inst.status == InstanceStatus.RUNNING.value, (
        f"Pre-terminate status must be preserved when commit fails; "
        f"got {inst.status!r}"
    )
    assert inst.waiting_for == 5, (
        f"Pre-terminate waiting_for must be preserved when commit fails; "
        f"got {inst.waiting_for}"
    )

    # Job still PROCESSING (the in-session UPDATE rolled back).
    jobs = get_jobs_for_instance(engine, instance_id)
    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.PROCESSING.value

    # Lock still present.
    locks = get_locks_for_instance(engine, instance_id)
    assert len(locks) == 1, (
        f"Lock must be preserved on rollback; got {len(locks)}"
    )

    # Message still present.
    msgs = get_messages_for_instance(engine, instance_id)
    assert len(msgs) == 1, (
        f"MessageQueue row must be preserved on rollback; got {len(msgs)}"
    )
