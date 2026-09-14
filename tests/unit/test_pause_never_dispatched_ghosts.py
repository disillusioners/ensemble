"""DEFECT B (cascade-resume limbo fix, 2026-09-14) — never-dispatched
ghost children are SKIPPED by the pause cascade.

Incident 2026-09-14 (ensemble_prod forensics): a leader spawned two
children WITHOUT dispatch (idle, zero task/message/checkpoint rows).
``ask_questions`` pause-cascade paused them anyway; the answer resume
flipped them PAUSED→RUNNING (bare DB flip); ``resume_processing_job``
routed them to ``route_outcome=internal_child_noop`` (no suspension
handle, no paused turn) — leaving them in ``running`` with NO graph.
Every later dispatch to them used to strand in the in-memory injection
lane, and the parent-completion gate counted them as live children
forever (``_ghost_child_filter`` only matches ``idle``), wedging the
tree at WAITING_CHILDREN.

The fix: ``pause_instance_cascade`` skips never-dispatched ghosts (no
in-flight work to quiesce — pausing them is semantically vacuous and
sets up the limbo). They stay ``idle``/version=1/empty — exactly the
fresh-spawn state, where dispatch is durable and the completion gate's
``ChildReportsService._ghost_child_filter`` already excludes them.

Test layout:

  * Probe unit tests over a real file-backed SQLite engine
    (``InstanceRepository.filter_never_dispatched_ids``).
  * Semantic-equivalence drift test vs the canonical SQL expression
    (``_ghost_child_filter``) evaluated on the SAME fixture DB — the
    two predicates MUST stay in lockstep.
  * Cascade-classification tests: ghost children land in
    ``skipped_ids`` (never in the batched pause UPDATE), dispatched
    children still pause, and probe failure degrades to legacy
    pause-all with a WARNING.
  * Resume-side: ghosts are never in ``resumed_ids`` (they were never
    paused) and stay ``idle``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task
from daemon.services.instance_lifecycle import (
    InstanceLifecycleService,
    _CascadeUpdateResult,
)


# ---------------------------------------------------------------------------
# Fixtures — real file-backed SQLite (NullPool + WAL per testing conventions)
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    eng = create_engine(
        f"sqlite:///{tmp_path}/pause_ghosts.db",
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


NOW_ISO = "2026-09-14T10:00:00Z"


def _mk_instance(
    instance_id: str,
    *,
    status: str = InstanceStatus.IDLE.value,
    version: int = 1,
    parent_id: str | None = None,
) -> Instance:
    return Instance(
        instance_id=instance_id,
        agent_id="worker",
        agent_dir="/agents/worker",
        agent_name="worker",
        parent_id=parent_id,
        status=status,
        version=version,
        created_at=NOW_ISO,
        updated_at=NOW_ISO,
    )


def _seed_tree(eng: Engine) -> dict[str, list[str]]:
    """leader + ghost-1 + ghost-2 (never dispatched) + dispatched-child
    (idle but HAS a message + task row — queued work, not started).

    Returns the seeded id groups for assertions.
    """
    with Session(eng) as s:
        s.add(_mk_instance("leader", status=InstanceStatus.RUNNING.value))
        s.add(
            _mk_instance(
                "ghost-1", parent_id="leader"
            )
        )
        s.add(
            _mk_instance(
                "ghost-2", parent_id="leader"
            )
        )
        s.add(
            _mk_instance(
                "dispatched-child", parent_id="leader"
            )
        )
        s.commit()
        s.add(
            MessageQueue(
                message_id="m-1",
                instance_id="dispatched-child",
                content="queued work",
                role="user",
                created_at=NOW_ISO,
            )
        )
        s.add(
            Task(instance_id="dispatched-child", task_type="PROCESS_MESSAGE")
        )
        s.commit()
    return {
        "ghosts": ["ghost-1", "ghost-2"],
        "dispatched": ["dispatched-child"],
        "all": ["leader", "ghost-1", "ghost-2", "dispatched-child"],
    }


# ---------------------------------------------------------------------------
# Probe unit tests
# ---------------------------------------------------------------------------


class TestFilterNeverDispatchedIds:
    def test_ghosts_detected_and_non_ghosts_excluded(self, engine):
        _seed_tree(engine)
        repo = SQLModelInstanceRepository(engine)

        out = repo.filter_never_dispatched_ids(
            ["leader", "ghost-1", "ghost-2", "dispatched-child"]
        )

        assert out == {"ghost-1", "ghost-2"}

    def test_empty_input_skips_db(self, engine):
        repo = SQLModelInstanceRepository(engine)
        assert repo.filter_never_dispatched_ids([]) == set()

    def test_running_status_never_matches(self, engine):
        """A resumed-from-pause ghost (running, version bumped) is NOT
        detected by the probe — the fix works by preventing the pause,
        not by detecting post-hoc limbo states."""
        with Session(engine) as s:
            s.add(
                _mk_instance(
                    "resumed-ghost",
                    status=InstanceStatus.RUNNING.value,
                    version=3,
                )
            )
            s.commit()
        repo = SQLModelInstanceRepository(engine)
        assert repo.filter_never_dispatched_ids(["resumed-ghost"]) == set()


class TestProbeEquivalenceWithCanonicalGhostFilter:
    """Anti-drift pin: the repo probe and the canonical
    ``ChildReportsService._ghost_child_filter`` SQL expression must
    agree on the SAME fixture DB. The repo docstring promises lockstep;
    this test enforces it."""

    def test_predicates_agree_on_mixed_fixture(self, engine):
        from daemon.services.child_reports import ChildReportsService

        _seed_tree(engine)
        repo = SQLModelInstanceRepository(engine)
        ids = ["leader", "ghost-1", "ghost-2", "dispatched-child"]

        repo_out = repo.filter_never_dispatched_ids(ids)

        stmt = select(Instance.instance_id).where(
            Instance.instance_id.in_(ids),
            ChildReportsService._ghost_child_filter(),
        )
        with Session(engine) as s:
            canonical = set(s.exec(stmt).all())

        assert repo_out == canonical


# ---------------------------------------------------------------------------
# Cascade classification tests
# ---------------------------------------------------------------------------


def _build_pause_db_sync_mock(captured: dict, engine: Engine) -> MagicMock:
    def _mock(engine_, write_guard, *, tree_ids, paused_at_iso, paused_instances_data, suspension_reason=None):
        updated_ids = [iid for iid, _agent in paused_instances_data]
        updated_set = set(updated_ids)
        # Perform the REAL status flip for the updated ids so a
        # subsequent resume cascade classifies against true DB state.
        with Session(engine) as s:
            for iid in updated_ids:
                row = s.get(Instance, iid)
                if row is not None:
                    row.status = InstanceStatus.PAUSED.value
                    row.updated_at = paused_at_iso
            s.commit()
        result = _CascadeUpdateResult(
            updated_ids=updated_ids,
            skipped_ids=[iid for iid in tree_ids if iid not in updated_set],
            agent_ids_by_instance={
                iid: agent for iid, agent in paused_instances_data
            },
        )
        captured["pause_calls"].append(
            {
                "tree_ids": list(tree_ids),
                "paused_instances_data": list(paused_instances_data),
            }
        )
        return result

    return MagicMock(side_effect=_mock)


def _build_resume_db_sync_mock(captured: dict) -> MagicMock:
    def _mock(engine, write_guard, *, tree_ids, ancestor_ids, is_root_resume):
        result = _CascadeUpdateResult(
            updated_ids=list(tree_ids),
            skipped_ids=[],
            agent_ids_by_instance={},
        )
        captured["resume_calls"].append({"tree_ids": list(tree_ids)})
        return result

    return MagicMock(side_effect=_mock)


def _build_lifecycle(engine: Engine) -> tuple[InstanceLifecycleService, MagicMock, dict]:
    repo = SQLModelInstanceRepository(engine)
    manager = MagicMock()
    manager._instance_repository = repo
    registry = MagicMock()
    registry.cancel_by_instance = MagicMock(return_value=0)
    manager._request_registry = registry
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._live_hub.stream_message = AsyncMock()
    manager._graph_tasks = {}
    manager._gii_throttle = {}
    manager._loop_breaker_state = {}

    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    service._manager = manager
    captured: dict = {"pause_calls": [], "resume_calls": []}
    service._pause_cascade_db_sync = _build_pause_db_sync_mock(captured, engine)
    service._resume_cascade_db_sync = _build_resume_db_sync_mock(captured)
    service._captured = captured
    return service, manager, captured


class TestPauseCascadeSkipsNeverDispatchedGhosts:
    @pytest.mark.asyncio
    async def test_ghosts_skipped_dispatched_child_still_pauses(self, engine):
        ids = _seed_tree(engine)
        service, _, captured = _build_lifecycle(engine)

        result = await service.pause_instance_cascade("leader")

        # Ghosts are skipped (NOT paused); the dispatched child and the
        # leader still pause.
        assert set(result["paused_ids"]) == {"leader", "dispatched-child"}
        assert set(result["skipped_ids"]) >= {"ghost-1", "ghost-2"}
        # The batched pause UPDATE never touches the ghosts.
        paused_in_update = {
            iid
            for call in captured["pause_calls"]
            for iid, _agent in call["paused_instances_data"]
        }
        assert "ghost-1" not in paused_in_update
        assert "ghost-2" not in paused_in_update
        # DB truth: ghosts are still idle at version 1.
        with Session(engine) as s:
            for gid in ("ghost-1", "ghost-2"):
                row = s.get(Instance, gid)
                assert row.status == InstanceStatus.IDLE.value
                assert row.version == 1

    @pytest.mark.asyncio
    async def test_resume_never_touches_ghosts_and_they_stay_idle(
        self, engine
    ):
        """The end-to-end DEFECT B shape: after pause → resume, the
        ghosts were NEVER in ``resumed_ids`` and are still ``idle`` —
        the ``running``+``internal_child_noop`` limbo never forms, and
        the parent-completion gate's ghost filter keeps excluding them.
        """
        ids = _seed_tree(engine)
        service, _, captured = _build_lifecycle(engine)

        await service.pause_instance_cascade("leader")
        resume_result = await service.resume_instance_cascade("leader")

        assert "ghost-1" not in resume_result["resumed_ids"]
        assert "ghost-2" not in resume_result["resumed_ids"]
        assert set(resume_result["resumed_ids"]) == {"leader", "dispatched-child"}
        with Session(engine) as s:
            for gid in ("ghost-1", "ghost-2"):
                row = s.get(Instance, gid)
                assert row.status == InstanceStatus.IDLE.value

    @pytest.mark.asyncio
    async def test_probe_failure_degrades_to_legacy_pause_all(
        self, engine, caplog
    ):
        """Probe outage must never block the cascade — degrade to the
        legacy pause-all behavior with a WARNING (fail-open to the
        pre-fix semantics, loudly)."""
        import logging

        _seed_tree(engine)
        service, manager, captured = _build_lifecycle(engine)
        manager._instance_repository.filter_never_dispatched_ids = MagicMock(
            side_effect=RuntimeError("probe db down")
        )

        with caplog.at_level(logging.WARNING, logger="daemon.services.instance_lifecycle"):
            result = await service.pause_instance_cascade("leader")

        assert "probe" in caplog.text.lower()
        # Legacy behavior: everything pausable got paused.
        assert set(result["paused_ids"]) == {"leader", "ghost-1", "ghost-2", "dispatched-child"}

    @pytest.mark.asyncio
    async def test_probe_failure_falls_back_to_pause_not_skip(
        self, engine
    ):
        """Directional pin: degradation must be pause-all (legacy), NOT
        skip-all — a probe bug must never silently refuse to pause a
        tree that has real in-flight work."""
        _seed_tree(engine)
        service, manager, _ = _build_lifecycle(engine)
        manager._instance_repository.filter_never_dispatched_ids = MagicMock(
            side_effect=RuntimeError("boom")
        )

        result = await service.pause_instance_cascade("leader")

        assert "leader" in result["paused_ids"]
        assert result["skipped_ids"] == []
