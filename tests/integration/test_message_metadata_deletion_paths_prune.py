"""cpv2 final-gate 🟡1 — fold the T5.19 prune into the two non-`_cleanup_instance`
deletion paths: ``InstanceLifecycleService.hard_delete_instance``
(``daemon/services/instance_lifecycle.py``) and
``CheckpointCleanupJob._cleanup_orphaned_threads``
(``daemon/services/maintenance.py``).

Why this exists (reviewer memory 2026-09-04 §"Side-table orphan leak"):
the ``message_metadata`` side table has no FK on either backend, so without
this fold the orphan rows accumulate forever (growth ≈ 2–4 rows/turn ×
turns × instances). T5.19 wired ``delete_for_thread`` into
``_cleanup_instance`` step-2.5 for **1 of 3** deletion surfaces; the other
two were missing.

Behaviors under test (5, mapping to the fix's spec):

1. ``hard_delete_instance`` happy path — side-table rows exist for tree
   members → hard-delete → rows for those ``thread_id``s are deleted (0
   remain).
2. ``hard_delete_instance`` no-raise — ``delete_for_thread`` raises →
   hard-delete still completes (returns its result dict), WARNING logged
   with ``"orphans tolerated (never-raise guard)"``.
3. ``hard_delete_instance`` adapter-is-None — the prune MUST run for every
   tree_id even when the checkpointer adapter is None (the cpv2
   acceptance criterion: instance rows are already gone, so side-table
   rows are orphans unconditionally).
4. ``_cleanup_orphaned_threads`` happy path — orphaned thread with
   side-table rows → Op A → rows deleted.
5. ``_cleanup_orphaned_threads`` no-raise — ``delete_for_thread`` raises
   → sweep completes (other orphans still processed), WARNING logged.

Harness honesty contract (mirrors ``test_message_metadata_prune.py``):
the prune side uses a REAL ``MessageMetadataRepository`` on a disposable
per-test PG database (real ``DELETE`` statement, not a mock); the
orchestration sides use the conventions of ``test_instance_hard_delete.py``
/ ``test_maintenance.py`` (AsyncMock checkpointer, MagicMock manager,
in-memory SQLite + ``StaticPool`` for the instance repo — single shared
connection so reads-after-writes see the latest data). The never-raise
guard is proven on the **real** ``delete_for_thread`` (monkeypatched to
raise) so a regression in the guard's swallow semantics surfaces as an
exception, not as a side effect of a mock.

PG unreachable → loud skip; a skip is NOT green for the cpv2 final-gate
fold — start PostgreSQL and re-run.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool, StaticPool
from sqlmodel import Session, SQLModel


# ── langgraph eviction (repo standard; instance_lifecycle.py imports
#    ``langgraph.graph.state`` at module-load time) ────────────────────────


@pytest.fixture(autouse=True)
def _real_langgraph():
    """Evict the root-conftest langgraph mocks for this module (repo pattern)."""
    from tests.helpers.checkpoint_prune_pg import (
        evict_langgraph_mocks,
        restore_langgraph_mocks,
    )

    saved = evict_langgraph_mocks()
    try:
        yield
    finally:
        restore_langgraph_mocks(saved)


# ── fixtures ────────────────────────────────────────────────────────────────


async def _probe_pg_or_skip():
    import asyncpg

    from tests.helpers.checkpoint_prune_pg import ADMIN_DSN

    try:
        conn = await asyncpg.connect(ADMIN_DSN, timeout=5)
        await conn.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            f"cpv2 deletion-paths prune test SKIPPED — PostgreSQL not "
            f"available at {ADMIN_DSN} ({type(exc).__name__}: {exc}). A "
            f"skip is NOT green for the cpv2 final-gate fold; start "
            f"PostgreSQL and re-run "
            f"tests/integration/test_message_metadata_deletion_paths_prune.py."
        )


@pytest.fixture
async def pg_db():
    """Disposable database per test (safe under xdist; never touches shared DBs)."""
    from tests.helpers.checkpoint_prune_pg import create_disposable_db, drop_database

    await _probe_pg_or_skip()
    name, dsn = await create_disposable_db()
    try:
        yield name, dsn
    finally:
        await drop_database(name)


@pytest.fixture
def meta_repo(pg_db):
    """SYNC MessageMetadataRepository on the disposable PG database.

    Same wiring as ``tests/integration/test_message_metadata_prune.py`` —
    the engine is the daemon's sync convention
    (``postgresql+psycopg://``, matching
    ``factory.create_postgres_engine``); only the ``message_metadata``
    table is created here.
    """
    from daemon.repositories.message_metadata.models import MessageMetadata
    from daemon.repositories.message_metadata.repository import (
        MessageMetadataRepository,
    )

    _name, dsn = pg_db
    url = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    eng = create_engine(url, poolclass=NullPool, pool_pre_ping=True)
    SQLModel.metadata.create_all(eng, tables=[MessageMetadata.__table__])
    try:
        yield MessageMetadataRepository(engine=eng)
    finally:
        eng.dispose()


@pytest.fixture
def instance_repo_engine() -> Engine:
    """Real in-memory SQLite engine for the instance repository.

    Mirrors ``test_instance_hard_delete.py::engine`` — ``StaticPool``
    keeps a single connection alive so reads after writes see the latest
    data. ``foreign_keys=ON`` so an accidental reorder surfaces as an
    ``IntegrityError``.
    """
    import daemon.repositories.instance.repository  # noqa: F401 (register tables)

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


# ── helpers ────────────────────────────────────────────────────────────────


def write_tap_rows(repo, thread_id: str, n: int = 3) -> list[str]:
    """Deterministically write N side-table rows via the tap storage call.

    Same convention as ``test_message_metadata_prune.py::write_tap_rows``
    — ``upsert_batch`` IS the tap's storage call; invoking it directly is
    the deterministic simulation of the ``MessageTapSlot`` firing. The
    upsert's RETURN VALUE is deliberately not asserted (PG
    insertmanyvalues returns ``-1``); presence is asserted via
    ``get_for_thread`` instead.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    items = [(f"{uuid.uuid4()}", now_iso, None) for _ in range(n)]
    repo.upsert_batch(thread_id, items)
    return [mid for (mid, _ts, _seq) in items]


def seed_terminal_instance(engine: Engine, instance_id: str) -> None:
    """Seed a terminal (completed) instance row.

    Mirrors ``test_message_metadata_prune.py::seed_terminal_instance``.
    """
    from daemon.repositories.instance.models import Instance, InstanceStatus

    inst = Instance(
        instance_id=instance_id,
        agent_id="developer",
        agent_dir="/agents/developer",
        status=InstanceStatus.COMPLETED.value,
        version=1,
        instance_metadata={},
    )
    with Session(engine) as session:
        session.add(inst)
        session.commit()


def _build_manager(
    instance_repo_engine: Engine,
    meta_repo,
    *,
    checkpointer: Any = ...,
) -> Any:
    """Build the MagicMock manager shape used by the hard_delete tests.

    Mirrors ``test_instance_hard_delete.py`` / ``test_hard_delete_mock_integration.py``
    — the real ``SQLModelInstanceRepository`` is the only real piece; the
    rest are MagicMocks. ``manager.message_metadata_repo`` (the property
    the service reads at line 2711) returns the real ``meta_repo`` so
    the prune loop's ``asyncio.to_thread(meta_repo.delete_for_thread, …)``
    runs against a real engine.

    ``checkpointer``:
      * sentinel (``...``) → fresh AsyncMock with ``adelete_thread``
        (default for the cpv2 happy-path tests).
      * explicit ``None`` → ``manager._checkpointer = None`` (the cpv2
        acceptance criterion: prune must run per tree_id even when the
        checkpointer adapter is None).
      * any other value → installed as-is.
    """
    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    from daemon.write_pause_guard import WritePauseGuard

    real_repo = SQLModelInstanceRepository(instance_repo_engine)
    manager = MagicMock()
    manager.is_write_paused = False
    manager.engine = instance_repo_engine
    manager.write_guard = WritePauseGuard()
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
    # Wire the REAL meta_repo via the property the service reads.
    manager.message_metadata_repo = meta_repo
    # Checkpointer: default sentinel → AsyncMock; explicit None → None.
    if checkpointer is ...:
        checkpointer = MagicMock()
        checkpointer.adelete_thread = AsyncMock()
    manager._checkpointer = checkpointer
    return manager


def _build_svc(manager: Any):
    """Build the real ``InstanceLifecycleService`` over the mock manager."""
    from daemon.services.instance_lifecycle import InstanceLifecycleService

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
    # Stub the graph-task termination so we don't wire up the full
    # graph / SSE machinery — but still keep enough side effects to
    # make ``hard_delete_instance``'s step 2 realistic.
    svc.terminate_instance = AsyncMock(return_value=True)
    return svc


# ── tests: hard_delete_instance ─────────────────────────────────────────────


class TestHardDeletePruneHappyPath:
    """The cpv2 fold's primary invariant: ``hard_delete_instance`` drops
    ``message_metadata`` side-table rows for every tree member."""

    @pytest.mark.asyncio
    async def test_hard_delete_drops_side_table_rows_for_tree_members(
        self, meta_repo, instance_repo_engine,
    ):
        """Tree of 2 instances → side-table rows for both → hard_delete →
        rows for BOTH ``thread_id``s are deleted (0 remain)."""
        manager = _build_manager(instance_repo_engine, meta_repo)
        svc = _build_svc(manager)

        root_id = f"hd-root-{uuid.uuid4().hex[:8]}"
        child_id = f"hd-child-{uuid.uuid4().hex[:8]}"
        # Seed root + child rows; the cascade discovers child via
        # ``parent_id`` permanent lineage (the D1 fix).
        now = datetime.now(timezone.utc).isoformat()
        from daemon.repositories.instance.models import (
            Instance,
            InstanceHierarchy,
        )

        with Session(instance_repo_engine) as s:
            s.add(Instance(
                instance_id=root_id, agent_id="developer",
                agent_dir="/agents/developer", agent_name="developer",
                parent_id=None, status="completed", version=1,
                created_at=now, updated_at=now,
            ))
            s.add(Instance(
                instance_id=child_id, agent_id="developer",
                agent_dir="/agents/developer", agent_name="developer",
                parent_id=root_id, status="completed", version=1,
                created_at=now, updated_at=now,
            ))
            s.add(InstanceHierarchy(
                parent_id=root_id, child_id=child_id, created_at=now,
            ))
            s.commit()

        # Write side-table rows for BOTH tree members.
        for iid in (root_id, child_id):
            write_tap_rows(meta_repo, iid, n=3)
            assert len(meta_repo.get_for_thread(iid)) == 3

        # Drive the REAL hard_delete_instance end-to-end.
        result = await svc.hard_delete_instance(root_id)

        # The cascade succeeded — every tree member's instance row is
        # gone and the checkpoint sweep ran for both.
        assert result["deleted"] is True
        assert result["terminated"] is True
        assert sorted(result["tree_ids"]) == sorted([root_id, child_id])
        assert result["checkpoint_threads_deleted"] == 2
        assert result["checkpoint_errors"] == []

        # THE acceptance assertion: side-table rows are GONE for every
        # tree member. Pre-fix these rows survived the hard-delete
        # (orphan leak — cpv2 finding 🟡1).
        for iid in (root_id, child_id):
            assert meta_repo.get_for_thread(iid) == {}, (
                f"cpv2 fold regression: hard_delete_instance must prune "
                f"side-table rows for tree member {iid}; "
                f"got {meta_repo.get_for_thread(iid)}"
            )


class TestHardDeletePruneNeverRaise:
    """The W3 / D14 never-raise guard mirrored from
    ``maintenance.py:_cleanup_instance`` step-2.5."""

    @pytest.mark.asyncio
    async def test_prune_failure_does_not_break_hard_delete(
        self, meta_repo, instance_repo_engine, monkeypatch, caplog,
    ):
        """``delete_for_thread`` raises → ``hard_delete_instance`` still
        completes (returns its result dict, teardown not broken),
        WARNING logged with the tolerance note."""
        from daemon.repositories.message_metadata.repository import (
            MessageMetadataRepository,
        )

        manager = _build_manager(instance_repo_engine, meta_repo)
        svc = _build_svc(manager)

        iid = f"hd-pr-{uuid.uuid4().hex[:8]}"
        seed_terminal_instance(instance_repo_engine, iid)
        write_tap_rows(meta_repo, iid, n=3)
        assert len(meta_repo.get_for_thread(iid)) == 3

        def explode(self, thread_id: str) -> int:
            raise RuntimeError("prune backend exploded")

        monkeypatch.setattr(
            MessageMetadataRepository, "delete_for_thread", explode,
        )

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.instance_lifecycle",
        ):
            # MUST NOT raise.
            result = await svc.hard_delete_instance(iid)

        # Hard-delete still succeeded — teardown not broken by the
        # prune failure.
        assert result["deleted"] is True
        assert result["terminated"] is True
        assert iid in result["tree_ids"]
        assert result["checkpoint_threads_deleted"] == 1
        assert result["checkpoint_errors"] == []

        # Side-table rows STAY (over-record-only orphans, tolerated).
        assert len(meta_repo.get_for_thread(iid)) == 3

        # The never-raise WARNING is logged with the tolerance note.
        assert "orphans tolerated (never-raise guard)" in caplog.text
        assert iid[:8] in caplog.text


class TestHardDeletePruneAdapterIsNone:
    """Acceptance criterion: prune MUST run per tree_id even when the
    checkpointer adapter is None (the ``else`` branch at the original
    ``instance_lifecycle.py:2686-2690`` skipped the whole sweep when
    adapter is None). The instance rows are already gone at this point,
    so side-table rows are orphans unconditionally."""

    @pytest.mark.asyncio
    async def test_prune_runs_when_checkpointer_adapter_is_none(
        self, meta_repo, instance_repo_engine,
    ):
        # No checkpointer injected → manager._checkpointer is None.
        manager = _build_manager(
            instance_repo_engine, meta_repo, checkpointer=None,
        )
        assert manager._checkpointer is None
        svc = _build_svc(manager)

        iid = f"hd-noad-{uuid.uuid4().hex[:8]}"
        seed_terminal_instance(instance_repo_engine, iid)
        write_tap_rows(meta_repo, iid, n=3)
        assert len(meta_repo.get_for_thread(iid)) == 3

        result = await svc.hard_delete_instance(iid)

        # Hard-delete succeeded even without a checkpointer (the
        # original behavior).
        assert result["deleted"] is True
        assert result["terminated"] is True
        # No checkpointer → 0 threads swept, no error.
        assert result["checkpoint_threads_deleted"] == 0
        assert result["checkpoint_errors"] == []

        # THE acceptance assertion: side-table rows pruned even though
        # the checkpoint sweep was skipped entirely.
        assert meta_repo.get_for_thread(iid) == {}, (
            f"cpv2 fold acceptance criterion regression: prune must run "
            f"per tree_id even when checkpointer adapter is None; "
            f"got {meta_repo.get_for_thread(iid)}"
        )


# ── tests: _cleanup_orphaned_threads (Operation A) ──────────────────────────


class TestOrphanSweepPruneHappyPath:
    """``_cleanup_orphaned_threads`` drops side-table rows for every
    orphan thread it sweeps."""

    @pytest.mark.asyncio
    async def test_orphan_sweep_drops_side_table_rows(
        self, meta_repo,
    ):
        """Orphaned thread (checkpoint thread exists, instance gone) with
        side-table rows → Op A → rows deleted. Also covers the
        multi-orphan case: one prune failure mid-loop must not stop
        the next iteration's prune from running."""
        from daemon.config import PersistenceConfig
        from daemon.services.maintenance import CheckpointCleanupJob

        # Two orphan threads: opx-1 + opx-2. None in the instance repo.
        opx_1 = f"opx-{uuid.uuid4().hex[:8]}"
        opx_2 = f"opx-{uuid.uuid4().hex[:8]}"
        checkpointer = MagicMock()
        checkpointer.adelete_thread = AsyncMock()  # succeeds silently
        checkpointer.list_thread_ids = AsyncMock(return_value=[opx_1, opx_2])
        instance_repo = MagicMock()
        # Empty instance list → BOTH threads are orphans.
        instance_repo.list = MagicMock(return_value=([], 0))

        job = CheckpointCleanupJob(
            config=PersistenceConfig(),
            checkpointer=checkpointer,
            instance_repo=instance_repo,
            message_metadata_repo=meta_repo,
        )

        # Side-table rows for both orphans.
        write_tap_rows(meta_repo, opx_1, n=3)
        write_tap_rows(meta_repo, opx_2, n=2)
        assert len(meta_repo.get_for_thread(opx_1)) == 3
        assert len(meta_repo.get_for_thread(opx_2)) == 2

        await job._cleanup_orphaned_threads()

        # Both checkpoint sweeps ran.
        checkpointer.adelete_thread.assert_any_await(opx_1)
        checkpointer.adelete_thread.assert_any_await(opx_2)

        # THE acceptance assertion: side-table rows pruned for BOTH
        # orphan threads.
        assert meta_repo.get_for_thread(opx_1) == {}, (
            f"cpv2 fold regression: _cleanup_orphaned_threads must prune "
            f"side-table rows for orphan {opx_1}; "
            f"got {meta_repo.get_for_thread(opx_1)}"
        )
        assert meta_repo.get_for_thread(opx_2) == {}, (
            f"cpv2 fold regression: _cleanup_orphaned_threads must prune "
            f"side-table rows for orphan {opx_2}; "
            f"got {meta_repo.get_for_thread(opx_2)}"
        )


class TestOrphanSweepPruneNeverRaise:
    """Per-thread never-raise guard on the prune side: a single
    ``delete_for_thread`` failure MUST NOT abort the sweep — the next
    orphan is still processed (and its side-table rows still pruned)."""

    @pytest.mark.asyncio
    async def test_prune_failure_does_not_abort_orphan_sweep(
        self, meta_repo, monkeypatch, caplog,
    ):
        from daemon.config import PersistenceConfig
        from daemon.repositories.message_metadata.repository import (
            MessageMetadataRepository,
        )
        from daemon.services.maintenance import CheckpointCleanupJob

        opx_fail = f"opxf-{uuid.uuid4().hex[:8]}"
        opx_ok = f"opxo-{uuid.uuid4().hex[:8]}"
        checkpointer = MagicMock()
        checkpointer.adelete_thread = AsyncMock()
        checkpointer.list_thread_ids = AsyncMock(
            return_value=[opx_fail, opx_ok],
        )
        instance_repo = MagicMock()
        instance_repo.list = MagicMock(return_value=([], 0))

        job = CheckpointCleanupJob(
            config=PersistenceConfig(),
            checkpointer=checkpointer,
            instance_repo=instance_repo,
            message_metadata_repo=meta_repo,
        )

        # Side-table rows for BOTH orphans.
        write_tap_rows(meta_repo, opx_fail, n=3)
        write_tap_rows(meta_repo, opx_ok, n=2)
        assert len(meta_repo.get_for_thread(opx_fail)) == 3
        assert len(meta_repo.get_for_thread(opx_ok)) == 2

        # ``delete_for_thread`` raises only on the FAIL thread.
        def selective_explode(self, thread_id: str) -> int:
            if thread_id == opx_fail:
                raise RuntimeError("prune backend exploded")
            # Real implementation for other threads (let the loop
            # proceed so the next orphan's prune runs).
            from sqlalchemy import delete as sa_delete

            from daemon.repositories.message_metadata.models import (
                MessageMetadata,
            )
            with self._engine.begin() as conn:
                stmt = sa_delete(MessageMetadata).where(
                    MessageMetadata.thread_id == thread_id
                )
                return conn.execute(stmt).rowcount or 0

        monkeypatch.setattr(
            MessageMetadataRepository, "delete_for_thread", selective_explode,
        )

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.maintenance",
        ):
            # MUST NOT raise — the outer try swallows nothing; the
            # inner per-thread guard swallows the prune failure.
            await job._cleanup_orphaned_threads()

        # The sweep continued past the failing orphan to the next one
        # — both checkpoint sweeps ran.
        checkpointer.adelete_thread.assert_any_await(opx_fail)
        checkpointer.adelete_thread.assert_any_await(opx_ok)

        # Failing orphan: rows STAY (orphan tolerated).
        assert len(meta_repo.get_for_thread(opx_fail)) == 3
        # Successful orphan: rows pruned (proves the loop continued).
        assert meta_repo.get_for_thread(opx_ok) == {}, (
            f"cpv2 fold regression: prune failure on one orphan must "
            f"not abort the sweep — rows for the next orphan "
            f"{opx_ok} should be pruned; got "
            f"{meta_repo.get_for_thread(opx_ok)}"
        )

        # The never-raise WARNING is logged with the tolerance note for
        # the failing orphan only.
        assert "orphans tolerated (never-raise guard)" in caplog.text
        assert opx_fail[:8] in caplog.text