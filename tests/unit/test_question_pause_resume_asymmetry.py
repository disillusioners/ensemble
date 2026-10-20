"""Regression suite — question pause/resume asymmetry (side-effect stranding).

Commission: ``fix/question-pause-resume-asymmetry`` (live cases
2026-10-19: ``f86c4015`` / ``756d37ba``).

THE SHAPE UNDER TEST
--------------------
``ask_questions`` parks the asker's WHOLE tree (the pause cascade —
``daemon/services/instance_lifecycle.py::pause_instance_cascade``).
Every instance the cascade parks other than the asker is a
**side-effect target**: it owns no ``awaiting_answer`` suspension
handle, so the answer path can never resume its turn. Before the fix
the parked set was never recorded, so a side-effect target could stay
parked (or running-but-wake-less) forever, holding its parent in
``waiting_children``.

The suite covers the four commissioned scenarios:

  (a) ask → cascade parks a child → answer → the child is un-paused
      AND its turn is resumed (a wake) so the parent can leave
      ``waiting_children``;
  (b) restart between ask and answer → the recorded set survives the
      restart and the answer still releases it; and a stranded
      instance (no outstanding question) is recovered by the BOOT
      backstop sweep;
  (c) a parked instance with no task row and no outstanding question
      older than the threshold is swept; a younger one is untouched;
  (d) a genuinely question-paused asker (pending question) is NOT
      swept — plus the D1 pin (tree-scoped predicate: a side-effect
      target whose TREE still holds a pending question is protected).

Plus two race pins:
  * the late-pause race (the answer is delivered BEFORE the deferred
    pause lands → the pause is REFUSED — a pause with no answering
    counterpart must not happen);
  * the already-delivered (CAS loser) branch still releases the
    recorded set (a pack can be ``answered`` while its recorded set is
    still parked — that IS the stranded state).

Harness notes:
  * file-backed SQLite under ``tmp_path`` — never a live-shaped path;
  * the suite never shells out and never resolves a HOME-derived
    install path;
  * REAL repositories, REAL lifecycle cascade DB writes, REAL
    ``QuestionManager``, REAL shared answer helper; only the graph
    driver is stubbed (the graph is not the subject).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.services.question_manager import QuestionManager
from daemon.write_pause_guard import WritePauseGuard


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL per project convention)."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/question_pause_resume.db",
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


NOW_ISO = "2026-10-20T09:00:00+00:00"


def _mk_instance(
    instance_id: str,
    *,
    status: str = InstanceStatus.RUNNING.value,
    parent_id: str | None = None,
    version: int = 2,
    paused_at: str | None = None,
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
        paused_at=paused_at,
    )


class _FakeManager:
    """Minimal manager surface for the answer helper + lifecycle service.

    Everything the QUESTION / PAUSE / RESUME paths touch is the REAL
    implementation except the graph driver, which is stubbed to mirror
    the production routing contract:

      * a paused task row  → a resume handle exists → ``{status: ...}``;
      * no paused task row → production returns ``None`` and logs
        ``"no suspended or paused turn found"`` (the stranded class).

    ``_schedule_explicit_handle_resume`` (the silent checkpoint resume
    used by the boot orphan sweep) is stubbed to RECORD the wake — the
    wake is the observable that proves a parked instance can complete
    its turn again.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.write_guard = WritePauseGuard()
        self._instance_repository = SQLModelInstanceRepository(engine)
        self._task_repo = TaskRepository(engine)
        self._question_manager = QuestionManager()
        self._request_registry = MagicMock()
        self._request_registry.cancel_by_instance = MagicMock(return_value=0)
        self._live_hub = MagicMock()
        self._live_hub.stream_status_change = AsyncMock()
        self._live_hub.stream_message = AsyncMock()
        self._graph_tasks: dict = {}
        self._gii_throttle: dict = {}
        self._loop_breaker_state: dict = {}
        self._deferred_question_pause: set[str] = set()
        self.is_write_paused = False
        self.config = MagicMock()
        self.resume_calls: list[dict] = []
        self.wakes: list[dict] = []
        self.enqueued: list[dict] = []
        self.release_context_usage_cache = MagicMock(return_value=None)
        self.clear_injection = MagicMock(return_value=None)

        self._lifecycle = InstanceLifecycleService.__new__(
            InstanceLifecycleService
        )
        self._lifecycle._manager = self

    # -- lifecycle facade (real implementation) --------------------------
    async def pause_instance_cascade(self, instance_id: str, **kwargs) -> dict:
        return await self._lifecycle.pause_instance_cascade(instance_id, **kwargs)

    async def resume_instance_cascade(self, instance_id: str) -> dict:
        return await self._lifecycle.resume_instance_cascade(instance_id)

    # -- graph driver (stubbed) -------------------------------------------
    async def resume_processing_job(
        self,
        instance_id: str,
        message: str | None = None,
        silent: bool = False,
        **kwargs,
    ) -> dict | None:
        self.resume_calls.append(
            {"instance_id": instance_id, "message": message, "silent": silent}
        )
        with Session(self.engine) as s:
            from sqlmodel import select

            rows = s.exec(
                select(Task).where(
                    Task.instance_id == instance_id,
                    Task.status == TaskStatus.PAUSED.value,
                )
            ).all()
        if not rows:
            # Production: ``no suspended or paused turn found`` → None.
            return None
        return {
            "instance_id": instance_id,
            "status": "resumed",
            "job_id": rows[0].work_id,
        }

    async def _schedule_explicit_handle_resume(self, **kwargs) -> dict:
        """Stub of the silent checkpoint resume (records the wake)."""
        self.wakes.append(dict(kwargs))
        return {"status": "resuming", "instance_id": kwargs.get("instance_id")}

    async def enqueue_message(
        self, instance_id: str, message: str, source: str | None = None, **kwargs
    ):
        """Stub of the fresh-message enqueue (the Defect-3 answer fallback)."""
        self.enqueued.append(
            {"instance_id": instance_id, "message": message, "source": source}
        )
        return MagicMock(message_id="msg-enqueued", job_id="job-enqueued")


@pytest.fixture
def manager(engine) -> _FakeManager:
    return _FakeManager(engine)


def _seed_tree(engine: Engine) -> dict[str, str]:
    """root (WAITING_CHILDREN) + asker-1 (RUNNING, has a turn) +
    side-1 (RUNNING, NO task row — the side-effect-pause target)."""
    with Session(engine) as s:
        s.add(
            _mk_instance(
                "root-1", status=InstanceStatus.WAITING_CHILDREN.value, version=3
            )
        )
        s.add(_mk_instance("asker-1", parent_id="root-1"))
        s.add(_mk_instance("side-1", parent_id="root-1"))
        s.commit()
    with Session(engine) as s:
        s.add(
            Task(
                instance_id="asker-1",
                work_id="work-asker-1",
                task_type="PROCESS_MESSAGE",
                status=TaskStatus.RUNNING.value,
            )
        )
        s.commit()
    return {"root": "root-1", "asker": "asker-1", "side": "side-1"}


def _status(engine: Engine, instance_id: str) -> str:
    with Session(engine) as s:
        row = s.get(Instance, instance_id)
        return row.status if row is not None else "<missing>"


def _paused_at(engine: Engine, instance_id: str) -> str | None:
    with Session(engine) as s:
        row = s.get(Instance, instance_id)
        return row.paused_at if row is not None else None


def _ask(manager: _FakeManager, instance_id: str = "asker-1"):
    """The production ask: store the pack + stamp the durable shadow."""
    from daemon.services.midflight_qa import stamp_question_pack_metadata

    pack = manager._question_manager.set_question_pack(
        instance_id, [{"id": "q1", "text": "Proceed?"}]
    )
    assert pack is not None
    stamp_question_pack_metadata(manager, instance_id, pack)
    return pack


def _answer(manager: _FakeManager, instance_id: str = "asker-1", **kwargs):
    from daemon.routers.answer_helper import answer_questions_via_instance

    return answer_questions_via_instance(
        manager, instance_id, {"q1": "yes"}, None, None, **kwargs
    )


def _woken_ids(manager: _FakeManager) -> list[str]:
    return [w.get("instance_id") for w in manager.wakes]


# ---------------------------------------------------------------------------
# (a) ask → cascade parks a child → answer → the child is released
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_a_answer_releases_side_effect_paused_child(
    engine, manager
):
    """(a) The side-effect target is released AND its turn is resumed.

    The recorded set is the source of truth: the answer path un-pauses
    exactly the instances the question pause parked, and — because the
    side-effect target owns no task row — schedules the silent
    checkpoint resume that lets its turn complete (which is what lets
    the parent leave ``waiting_children``).
    """
    _seed_tree(engine)
    _ask(manager)

    # The deferred question pause (the production call site).
    pause_result = await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )
    assert "side-1" in pause_result["paused_ids"]
    assert _status(engine, "side-1") == InstanceStatus.PAUSED.value

    answer_result = await _answer(manager)
    assert answer_result["status"] in ("answered", "no_active_job")

    # The side-effect target is no longer parked ...
    assert _status(engine, "side-1") != InstanceStatus.PAUSED.value, (
        "the recorded side-effect set was not released by the answer"
    )
    # ... and its interrupted turn was resumed (the wake).
    assert "side-1" in _woken_ids(manager), (
        "the side-effect target was released without a wake — its turn "
        "can never complete and its parent stays in waiting_children"
    )


@pytest.mark.asyncio
async def test_recorded_paused_set_is_durable_and_scoped(engine, manager):
    """R1 — the paused set is recorded on the ASKER's durable row."""
    from daemon.services.question_pause_set import (
        QUESTION_PAUSED_SET_METADATA_KEY,
        read_question_paused_set,
    )

    _seed_tree(engine)
    pack = _ask(manager)
    await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )

    recorded = read_question_paused_set(manager, "asker-1", pack.id)
    assert "side-1" in recorded
    # The originator is NOT a side-effect target (it owns the
    # awaiting_answer handle and is resumed by the answer gate).
    assert "asker-1" not in recorded

    # Durable: the record lives in the DB (a fresh manager over the
    # same engine sees it — a restart does not lose it).
    fresh = _FakeManager(engine)
    assert "side-1" in read_question_paused_set(fresh, "asker-1", pack.id)

    # The metadata key is namespaced so it never collides with the
    # pack-payload shadow.
    raw = manager._instance_repository.get_metadata_value(
        "asker-1", QUESTION_PAUSED_SET_METADATA_KEY
    )
    assert isinstance(raw, dict)
    assert pack.id in raw


@pytest.mark.asyncio
async def test_cas_loser_branch_also_releases_recorded_set(engine, manager):
    """A5 (ii) — the already-delivered (CAS loser) branch releases too.

    A pack can be ``answered`` while its recorded side-effect set is
    still parked — that IS the stranded state (historical answer-gate
    failure mode, 2026-09-10). The un-pause is derived from the
    RECORD, never from "was the pack answered".
    """
    _seed_tree(engine)
    pack = _ask(manager)
    await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )

    # First answer → CAS win. The tree resume alone does NOT wake the
    # task-less side-effect target.
    await _answer(manager)
    manager.wakes.clear()

    # Re-park the side-effect target (simulates the stranded state:
    # the pack is answered, the recorded set is parked again).
    with Session(engine) as s:
        row = s.get(Instance, "side-1")
        row.status = InstanceStatus.PAUSED.value
        row.paused_at = datetime.now(timezone.utc).isoformat()
        s.commit()

    # Second answer → CAS loser (already_delivered) → must still
    # release the recorded set.
    second = await _answer(manager)
    assert second["status"] == "already_delivered"
    assert _status(engine, "side-1") != InstanceStatus.PAUSED.value
    assert "side-1" in _woken_ids(manager)


@pytest.mark.asyncio
async def test_late_pause_after_answer_is_refused(engine, manager):
    """Race pin — a pause with no answering counterpart must not happen.

    The deferred question pause runs OUTSIDE the graph task, so it can
    land AFTER the answer was consumed. Parking the tree at that point
    strands it with no question to answer (live cases 2026-10-19).
    """
    _seed_tree(engine)
    _ask(manager)

    # The answer lands FIRST (the asker has no paused turn yet, so the
    # Defect-3 fallback enqueues the answer as a fresh message).
    answer_result = await _answer(manager)
    assert answer_result["status"] in (
        "answered",
        "no_active_job",
        "answer_fallback_enqueued",
    )

    # The deferred pause fires afterwards → must be REFUSED.
    pause_result = await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )
    assert pause_result.get("question_pause_refused") is True, (
        "a question pause fired after the question was consumed — the "
        "tree would be parked with no answering counterpart"
    )
    assert pause_result["paused_ids"] == []
    assert _status(engine, "side-1") != InstanceStatus.PAUSED.value


@pytest.mark.asyncio
async def test_recorded_target_with_paused_turn_is_resumed_by_handle(
    engine, manager
):
    """The recorded target's PAUSED turn is resumed through its handle.

    When the tree-scoped resume does not reach the recorded target, its
    task row stays ``paused`` — and a paused task is NOT claimable by
    the worker pool. The un-pause therefore routes it through
    ``resume_processing_job`` (the handle-based resume) so the turn
    becomes ``pending`` again instead of being left suspended.
    """
    _seed_tree(engine)
    pack = _ask(manager)
    await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )

    # Give the side-effect target its own (suspended) turn, then park
    # it again — the shape the tree-scoped resume cannot reach.
    with Session(engine) as s:
        s.add(
            Task(
                instance_id="side-1",
                work_id="work-side-1",
                task_type="PROCESS_MESSAGE",
                status=TaskStatus.PAUSED.value,
                suspension_reason="paused_by_parent",
                resume_target_turn_id="work-side-1",
            )
        )
        row = s.get(Instance, "side-1")
        row.status = InstanceStatus.PAUSED.value
        row.paused_at = datetime.now(timezone.utc).isoformat()
        s.commit()

    manager.resume_calls.clear()
    await _answer(manager)

    assert _status(engine, "side-1") == InstanceStatus.RUNNING.value
    assert any(
        call["instance_id"] == "side-1" for call in manager.resume_calls
    ), (
        "the recorded target's suspended turn was not routed through "
        "resume_processing_job — a paused task is not claimable, so its "
        "turn would never complete"
    )
    with Session(engine) as s:
        from sqlmodel import select

        statuses = [t.status for t in s.exec(select(Task)).all()]
    assert TaskStatus.PAUSED.value not in statuses, (
        f"the suspended turn was left paused: {statuses}"
    )


# ---------------------------------------------------------------------------
# (b) restart between ask and answer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_b_recorded_set_survives_restart(engine, manager):
    """(b1) The recorded set survives a restart; the answer releases it.

    ``QuestionManager._packs`` is RAM-only (lost on restart); the
    recorded paused set lives in the DB, so the answer path still finds
    it after the daemon comes back.
    """
    _seed_tree(engine)
    pack = _ask(manager)
    await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )
    assert _status(engine, "side-1") == InstanceStatus.PAUSED.value

    # ── restart: a NEW manager over the same engine (RAM pack lost) ──
    restarted = _FakeManager(engine)
    assert restarted._question_manager.get_question_pack("asker-1") is None

    # The answer path rehydrates the pack from the durable shadow and
    # releases the recorded set.
    answer_result = await _answer(restarted)
    assert answer_result["status"] in ("answered", "no_active_job")
    assert _status(engine, "side-1") != InstanceStatus.PAUSED.value
    assert "side-1" in _woken_ids(restarted)


@pytest.mark.asyncio
async def test_scenario_b_boot_sweep_recovers_stranded_instance(engine, manager):
    """(b2) The BOOT backstop sweep recovers a stranded instance.

    A parked instance with no live turn and no outstanding question
    (the question was resolved, or the daemon restarted after the
    answer) is recovered by the stale-pause sweep — the boot pass runs
    it directly on the lifespan loop.
    """
    from daemon.services.stale_pause_sweep import sweep_stale_pauses

    _seed_tree(engine)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with Session(engine) as s:
        s.add(
            _mk_instance(
                "stranded-1",
                parent_id="root-1",
                status=InstanceStatus.PAUSED.value,
                paused_at=old,
            )
        )
        s.commit()

    result = await sweep_stale_pauses(manager, age_bound_seconds=7200)

    assert result["candidates"] >= 1
    assert "stranded-1" in result["instances"]
    assert _status(engine, "stranded-1") == InstanceStatus.RUNNING.value
    assert "stranded-1" in _woken_ids(manager)


@pytest.mark.asyncio
async def test_sweep_service_refuses_to_bridge_onto_its_own_loop(
    engine, manager
):
    """A1 pin — the periodic service never self-bridges onto its loop.

    The RDRS boot sweep once self-blocked the event loop by bridging a
    coroutine onto the very loop it was running on. The stale-pause
    service refuses that shape: when it is invoked ON the manager's
    loop thread it skips the cycle instead of blocking the loop.
    """
    import asyncio

    from daemon.services.stale_pause_sweep import StalePauseSweepService

    manager._loop = asyncio.get_running_loop()
    service = StalePauseSweepService(manager)
    result = service.sweep_now()
    assert result.get("skipped") == "on_loop_thread", (
        "the sweep must refuse to bridge a coroutine onto the loop "
        "thread it is running on (self-block class)"
    )


def test_sweep_service_respects_the_kill_switch(engine, manager):
    """The kill-switch short-circuits the periodic cycle."""
    from daemon.services.stale_pause_sweep import StalePauseSweepService

    service = StalePauseSweepService(manager, enabled=False)
    assert service.sweep_now() == {"skipped": "disabled"}
    service.start()  # no thread is spawned when disabled
    assert service._thread is None


# ---------------------------------------------------------------------------
# (c) age bound
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_c_sweep_respects_the_age_bound(engine, manager):
    """(c) Older than the threshold → swept; younger → untouched."""
    from daemon.services.stale_pause_sweep import sweep_stale_pauses

    _seed_tree(engine)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    fresh = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with Session(engine) as s:
        s.add(
            _mk_instance(
                "stale-1",
                parent_id="root-1",
                status=InstanceStatus.PAUSED.value,
                paused_at=old,
            )
        )
        s.add(
            _mk_instance(
                "young-1",
                parent_id="root-1",
                status=InstanceStatus.PAUSED.value,
                paused_at=fresh,
            )
        )
        s.commit()

    result = await sweep_stale_pauses(manager, age_bound_seconds=7200)

    assert "stale-1" in result["instances"]
    assert "young-1" not in result["instances"]
    assert _status(engine, "stale-1") == InstanceStatus.RUNNING.value
    assert _status(engine, "young-1") == InstanceStatus.PAUSED.value
    assert "young-1" not in _woken_ids(manager)


@pytest.mark.asyncio
async def test_sweep_never_finalizes_an_instance(engine, manager):
    """A6 pin — the sweeper un-pauses / resumes; it NEVER finalizes.

    Finalizing is irreversible and user-visible, and "the report was
    already delivered" is not reliably determinable for a parked
    instance (a parked ``waiting_children`` instance has no claimable
    task by construction). The sweeper therefore only un-pauses and
    gives a natural resume.
    """
    from daemon.services.stale_pause_sweep import sweep_stale_pauses

    _seed_tree(engine)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with Session(engine) as s:
        s.add(
            _mk_instance(
                "stale-2",
                parent_id="root-1",
                status=InstanceStatus.PAUSED.value,
                paused_at=old,
            )
        )
        s.commit()

    await sweep_stale_pauses(manager, age_bound_seconds=7200)

    assert _status(engine, "stale-2") == InstanceStatus.RUNNING.value
    assert _status(engine, "stale-2") not in (
        InstanceStatus.COMPLETED.value,
        InstanceStatus.ERROR.value,
        InstanceStatus.FAILED.value,
        InstanceStatus.TERMINATED.value,
    )


# ---------------------------------------------------------------------------
# (d) a genuinely question-paused asker is NOT swept (D1 pin)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_d_pending_question_protects_the_asker(engine, manager):
    """(d) An asker with a PENDING question is never swept."""
    from daemon.services.stale_pause_sweep import sweep_stale_pauses

    _seed_tree(engine)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with Session(engine) as s:
        s.add(
            _mk_instance(
                "asker-2",
                parent_id="root-1",
                status=InstanceStatus.PAUSED.value,
                paused_at=old,
            )
        )
        s.commit()
    # A live question on asker-2.
    _ask(manager, "asker-2")

    result = await sweep_stale_pauses(manager, age_bound_seconds=7200)

    assert "asker-2" not in result["instances"]
    assert result["skipped_protected"] >= 1
    assert _status(engine, "asker-2") == InstanceStatus.PAUSED.value
    assert "asker-2" not in _woken_ids(manager)


@pytest.mark.asyncio
async def test_d1_tree_scoped_protection_pins_the_predicate(engine, manager):
    """D1 PIN — the pending-question predicate is TREE/LINEAGE scoped.

    A side-effect target whose TREE still holds an outstanding question
    is protected (the question pause is still doing its job); the
    alternative (asker-scoped) would tear the tree apart while the
    user is still deciding.
    """
    from daemon.services.stale_pause_sweep import sweep_stale_pauses

    _seed_tree(engine)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with Session(engine) as s:
        s.add(
            _mk_instance(
                "side-2",
                parent_id="root-1",
                status=InstanceStatus.PAUSED.value,
                paused_at=old,
            )
        )
        s.commit()
    # The question lives on the ASKER (a different instance in the
    # same permanent-lineage tree) — side-2 itself has no pack.
    _ask(manager, "asker-1")
    assert manager._question_manager.get_question_pack("side-2") is None

    result = await sweep_stale_pauses(manager, age_bound_seconds=7200)

    assert "side-2" not in result["instances"], (
        "the tree-scoped D1 predicate must protect a side-effect target "
        "whose tree still holds an outstanding question"
    )
    assert _status(engine, "side-2") == InstanceStatus.PAUSED.value


@pytest.mark.asyncio
async def test_d1_resolved_question_releases_the_tree(engine, manager):
    """D1 counter-pin — once the question is resolved, the tree is free.

    The asker's pack is consumed (answered) → no pending question
    anywhere in the tree → the parked side-effect target IS swept.
    """
    from daemon.services.stale_pause_sweep import sweep_stale_pauses

    _seed_tree(engine)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with Session(engine) as s:
        s.add(
            _mk_instance(
                "side-3",
                parent_id="root-1",
                status=InstanceStatus.PAUSED.value,
                paused_at=old,
            )
        )
        s.commit()
    _ask(manager, "asker-1")
    # The question is consumed — the pack is no longer pending (the
    # answer path clears the durable shadow at answer consumption).
    manager._question_manager.set_answers("asker-1", {"q1": "yes"})
    from daemon.services.midflight_qa import clear_question_pack_metadata

    clear_question_pack_metadata(manager, "asker-1")

    result = await sweep_stale_pauses(manager, age_bound_seconds=7200)

    assert "side-3" in result["instances"]
    assert _status(engine, "side-3") == InstanceStatus.RUNNING.value
