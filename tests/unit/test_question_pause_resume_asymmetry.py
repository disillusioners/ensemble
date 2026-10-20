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
        # The dismiss / supersession paths clear the question-pause flag.
        self.clear_question_pause_requested = MagicMock(return_value=None)

        self._lifecycle = InstanceLifecycleService.__new__(
            InstanceLifecycleService
        )
        self._lifecycle._manager = self

    async def get_instance(self, instance_id: str):
        """The instance-existence probe the router endpoints use."""
        row = self._instance_repository.get(instance_id)
        if row is None:
            raise KeyError(instance_id)
        return row

    # -- lifecycle facade (real implementation) --------------------------
    async def pause_instance_cascade(self, instance_id: str, **kwargs) -> dict:
        return await self._lifecycle.pause_instance_cascade(instance_id, **kwargs)

    async def resume_instance_cascade(
        self, instance_id: str, *, exclude_ids: list[str] | None = None
    ) -> dict:
        # The harness works against BOTH signatures: the pre-fix
        # lifecycle service has no ``exclude_ids`` parameter, and the
        # RED capture must show the SEMANTIC failure (not a TypeError
        # from the harness itself).
        try:
            return await self._lifecycle.resume_instance_cascade(
                instance_id, exclude_ids=exclude_ids
            )
        except TypeError:
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
        # Production WC branch (manager.resume_processing_job): a
        # ``waiting_children`` parent + silent=True enqueues a REAL wake
        # turn (``route_outcome=b2_wc_wake``) and returns
        # ``{"status": "wake_enqueued"}``.
        row = self._instance_repository.get(instance_id)
        if silent and getattr(row, "status", None) == "waiting_children":
            self.wakes.append(
                {
                    "instance_id": instance_id,
                    "route_outcome": "b2_wc_wake",
                }
            )
            return {
                "instance_id": instance_id,
                "job_id": "job-wc-wake",
                "message_id": "msg-wc-wake",
                "status": "wake_enqueued",
            }
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
        # A real turn resume — the graph is re-armed (the harness
        # records it as a wake so the completion-gate model can see it).
        self.wakes.append(
            {
                "instance_id": instance_id,
                "route_outcome": "resume_processing_job",
            }
        )
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


async def _mark_question_paused(
    manager: _FakeManager,
    instance_id: str,
    *,
    pack_id: str = "pack-question-1",
    paused_at: str | None = None,
) -> None:
    """Stamp the pause-cause marker exactly as the question cascade does.

    The sweeper's eligibility filter is the marker (R1, review cycle 3):
    only an instance parked BY A QUESTION may be reverted by the
    backstop. A deliberate pause never carries it.
    """
    from daemon.services.question_pause_set import stamp_question_pause_marker

    stamped = await stamp_question_pause_marker(
        manager, instance_id, pack_id, paused_at
    )
    assert stamped is True


# ---------------------------------------------------------------------------
# R1 (review cycle 3) — the pause-cause discriminator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliberate_pause_with_paused_task_is_not_swept(engine, manager):
    """T1 — a DELIBERATE pause is never undone by the backstop.

    A user ``POST /instances/{id}/pause`` (or the agent pause tools, or
    the watchover activation) parks the instance and suspends its
    RUNNING turn with ``suspension_reason='paused_external'`` /
    ``'watchover_setup'``. The backstop must NOT release it: undoing a
    deliberate pause resumes an agent the operator deliberately stopped
    (it spends tokens and takes actions).

    The question stratum is protected by TWO independent discriminators:
    the pause-cause MARKER (absent on a deliberate pause) and the
    task's ``suspension_reason`` (never a question-cascade reason).
    """
    from daemon.services.stale_pause_sweep import sweep_stale_pauses

    _seed_tree(engine)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with Session(engine) as s:
        s.add(
            _mk_instance(
                "deliberate-1",
                parent_id="root-1",
                status=InstanceStatus.PAUSED.value,
                paused_at=old,
            )
        )
        s.add(
            Task(
                instance_id="deliberate-1",
                work_id="work-deliberate-1",
                task_type="PROCESS_MESSAGE",
                status=TaskStatus.PAUSED.value,
                suspension_reason="paused_external",
                resume_target_turn_id="work-deliberate-1",
            )
        )
        s.commit()

    result = await sweep_stale_pauses(manager, age_bound_seconds=7200)

    assert "deliberate-1" not in result["instances"], (
        "a DELIBERATE pause (paused_external) was reverted by the "
        "backstop — the operator's park must stand"
    )
    assert _status(engine, "deliberate-1") == InstanceStatus.PAUSED.value
    assert "deliberate-1" not in _woken_ids(manager), (
        "the interrupted turn of a deliberately paused instance was "
        "re-armed — the agent would resume working"
    )
    # The watchover variant is protected by the same discriminator.
    with Session(engine) as s:
        row = s.get(Task, 1)
        row.suspension_reason = "watchover_setup"
        s.commit()
    result2 = await sweep_stale_pauses(manager, age_bound_seconds=7200)
    assert "deliberate-1" not in result2["instances"]
    assert _status(engine, "deliberate-1") == InstanceStatus.PAUSED.value


@pytest.mark.asyncio
async def test_idle_deliberate_pause_without_task_row_is_not_swept(
    engine, manager
):
    """T2 — the task-less DELIBERATE pause is protected too.

    An idle instance that was deliberately paused (no task row at all)
    is exposed by the sweep even before the W4 delta — Option A (the
    task reason) alone cannot protect it. The pause-cause MARKER is
    what protects it: a deliberate pause never carries the marker.
    """
    from daemon.services.stale_pause_sweep import sweep_stale_pauses

    _seed_tree(engine)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with Session(engine) as s:
        s.add(
            _mk_instance(
                "deliberate-idle-1",
                parent_id="root-1",
                status=InstanceStatus.PAUSED.value,
                paused_at=old,
            )
        )
        s.commit()

    result = await sweep_stale_pauses(manager, age_bound_seconds=7200)

    assert "deliberate-idle-1" not in result["instances"], (
        "a DELIBERATE (task-less) pause was reverted by the backstop"
    )
    assert _status(engine, "deliberate-idle-1") == InstanceStatus.PAUSED.value
    assert "deliberate-idle-1" not in _woken_ids(manager)


@pytest.mark.asyncio
async def test_deliberate_pause_clears_a_stale_question_marker(engine, manager):
    """A deliberate pause must not inherit a stale question marker.

    The instance was question-paused (marker stamped) and then paused
    DELIBERATELY (a non-question cascade). The deliberate pause clears
    the marker, so the backstop can never revert it — even though the
    instance still carries the question-pause history.
    """
    from daemon.services.stale_pause_sweep import sweep_stale_pauses
    from daemon.services.question_pause_set import (
        QUESTION_PAUSED_MARKER_METADATA_KEY,
    )

    _seed_tree(engine)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with Session(engine) as s:
        s.add(
            _mk_instance(
                "reparked-1",
                parent_id="root-1",
                status=InstanceStatus.PAUSED.value,
                paused_at=old,
            )
        )
        s.commit()
    # The question pause stamps the marker.
    await _mark_question_paused(manager, "reparked-1", paused_at=old)
    assert manager._instance_repository.get_metadata_value(
        "reparked-1", QUESTION_PAUSED_MARKER_METADATA_KEY
    )

    # A DELIBERATE pause (no awaiting_answer reason) clears it.
    with Session(engine) as s:
        row = s.get(Instance, "reparked-1")
        row.status = InstanceStatus.RUNNING.value
        row.paused_at = None
        s.commit()
    await manager.pause_instance_cascade("reparked-1")
    marker = manager._instance_repository.get_metadata_value(
        "reparked-1", QUESTION_PAUSED_MARKER_METADATA_KEY
    )
    assert not (marker or {}).get("pack_id"), (
        "a deliberate pause must replace the stale POSITIVE question "
        f"marker (marker={marker!r})"
    )
    assert (marker or {}).get("deliberate") is True, (
        "a deliberate pause must write the explicit tombstone "
        f"(marker={marker!r})"
    )

    result = await sweep_stale_pauses(manager, age_bound_seconds=7200)
    assert "reparked-1" not in result["instances"]
    assert _status(engine, "reparked-1") == InstanceStatus.PAUSED.value


# ---------------------------------------------------------------------------
# GATE CYCLE 1 — C1 concurrency pin, council test (i), council test (ii),
#                 W1 pin, W3 pin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c1_concurrent_record_merges_both_complete(engine, manager):
    """C1 — two concurrent contenders on the merge path both COMPLETE.

    The merge used to hold a ``threading.Lock`` across an ``await``:
    a contender acquiring it on the event-loop thread would block the
    loop while the holder's await could never complete — a permanent
    whole-daemon deadlock. The merge is now serialized by a dedicated
    single-worker executor (no lock spans an await).

    THE REAL GUARD — read this before reading the assertions: the
    pre-fix failure mode of this case is NOT an assertion failure. It
    is a HANG: the second contender blocks the event loop inside
    ``with _MERGE_LOCK:`` and the first contender's ``await`` can never
    complete, so the test never finishes on its own. The bound that
    makes the regression OBSERVABLE is the PER-TEST TIMEOUT — the
    timeout kills the deadlocked run and pytest reports it as a
    FAILED (timeout-killed) case, not as a passing one. That is why the
    pack carries ``--timeout=60``: without it a regression deadlock
    hangs the pack instead of failing it.

    The assertions below (both contenders complete, neither write is
    lost) are the SEMANTIC part; the timeout is the deadlock guard.
    """
    import asyncio as _asyncio

    from daemon.services.question_pause_set import (
        read_question_paused_set,
        record_question_paused_set,
    )

    _seed_tree(engine)

    async def _merge(pack_id: str) -> list[str]:
        return await record_question_paused_set(
            manager, "asker-1", pack_id, ["side-1"]
        )

    results = await _asyncio.wait_for(
        _asyncio.gather(_merge("pack-a"), _merge("pack-b")),
        timeout=20.0,
    )

    assert results == [["side-1"], ["side-1"]], (
        f"both contenders must complete: {results!r}"
    )
    for pack_id in ("pack-a", "pack-b"):
        assert await read_question_paused_set(manager, "asker-1", pack_id) == [
            "side-1"
        ], (
            "the serialized merge must not lose a concurrent write "
            f"(pack={pack_id})"
        )


@pytest.mark.asyncio
async def test_council_i_deliberate_pause_over_parked_node_is_not_rearmed(
    engine, manager
):
    """Council test (i) — the PRODUCTION path, no forged reason.

    Shape: the question cascade parks the tree → a DELIBERATE pause
    lands on the already-parked side-effect target → the answer
    arrives.

    Expected: the deliberate pause WINS end-to-end — the target stays
    parked and its interrupted turn is NOT re-armed (no wake).

    No ``suspension_reason`` is forged anywhere: the deliberate pause
    is the real cascade, and the discriminator is the pause-cause
    TOMBSTONE it writes.
    """
    _seed_tree(engine)
    _ask(manager)
    await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )
    assert _status(engine, "side-1") == InstanceStatus.PAUSED.value

    # A DELIBERATE pause lands on the already-parked side-effect
    # target (the user /pause path — no awaiting_answer reason).
    await manager.pause_instance_cascade("side-1")

    # The answer arrives.
    await _answer(manager)

    assert _status(engine, "side-1") == InstanceStatus.PAUSED.value, (
        "a deliberate pause must survive the answer — the tree-scoped "
        "resume must not flip it back to running"
    )
    assert "side-1" not in _woken_ids(manager), (
        "the deliberately stopped turn was re-armed — the agent would "
        "resume working"
    )


@pytest.mark.asyncio
async def test_council_ii_terminated_asker_stale_shadow_does_not_block_sweep(
    engine, manager
):
    """Council test (ii) — a DEAD asker's stale shadow must not block.

    The durable ``question_pack_payload`` shadow is cleared ONLY at
    answer consumption, so a TERMINATED asker keeps a stale
    ``status='pending'`` shadow forever. Treating it as an outstanding
    question made the D1 predicate protect the tree FOREVER — the
    backstop could never release the exact strand it exists for.

    Expected: the paused target IS swept (the asker is dead, so its
    pack is stale, not outstanding).
    """
    from daemon.services.stale_pause_sweep import sweep_stale_pauses

    _seed_tree(engine)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with Session(engine) as s:
        s.add(
            _mk_instance(
                "stranded-2",
                parent_id="root-1",
                status=InstanceStatus.PAUSED.value,
                paused_at=old,
            )
        )
        s.commit()
    await _mark_question_paused(manager, "stranded-2", paused_at=old)

    # The asker is DEAD and its durable shadow still says "pending".
    _ask(manager, "asker-1")
    with Session(engine) as s:
        row = s.get(Instance, "asker-1")
        row.status = InstanceStatus.TERMINATED.value
        s.commit()
    from daemon.services.midflight_qa import (
        QUESTION_PACK_PAYLOAD_METADATA_KEY,
    )

    shadow = manager._instance_repository.get_metadata_value(
        "asker-1", QUESTION_PACK_PAYLOAD_METADATA_KEY
    )
    assert shadow is not None and shadow.get("status") == "pending", (
        "the stale shadow must still be present for this case to be "
        f"meaningful (shadow={shadow!r})"
    )

    result = await sweep_stale_pauses(manager, age_bound_seconds=7200)

    assert "stranded-2" in result["instances"], (
        "a dead asker's stale pack shadow must not protect the tree "
        "forever — the stranded instance must be swept"
    )
    assert _status(engine, "stranded-2") == InstanceStatus.RUNNING.value


@pytest.mark.asyncio
async def test_w1_lost_status_flip_is_an_error_and_keeps_the_record(
    engine, manager
):
    """W1 — a LOST status flip is an error, not a successful apply.

    ``transition_status_if`` returns ``None`` when its guard does not
    hold (the row is gone, or the status changed under a concurrent
    writer). Reporting that as a successful apply cleared the marker
    and deleted the record for an instance that is STILL parked — a
    permanent strand.
    """
    from daemon.services.question_pause_set import (
        read_question_paused_set,
        unpause_instance_after_question_pause,
    )

    _seed_tree(engine)
    pack = _ask(manager)
    await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )

    # The guarded flip does NOT apply — the lost race (the status
    # changed under a concurrent writer between the read and the
    # UPDATE). ``transition_status_if`` returns None for that.
    original_transition = manager._instance_repository.transition_status_if

    def _lost_race(instance_id, new_status, allowed_from):
        return None

    manager._instance_repository.transition_status_if = _lost_race
    try:
        outcome = await unpause_instance_after_question_pause(
            manager, "side-1", reason="w1_pin", pre_status="running"
        )
    finally:
        manager._instance_repository.transition_status_if = (
            original_transition
        )
    assert outcome == "error", (
        f"a lost status flip must be reported as an error (got {outcome!r})"
    )
    # The record is untouched — a later pass can retry.
    assert "side-1" in await read_question_paused_set(
        manager, "asker-1", pack.id
    ), "a lost status flip must NOT delete the record"
    _unused_outcome = None


@pytest.mark.asyncio
async def test_w3_duplicate_answer_does_not_re_flip_or_re_wake(engine, manager):
    """W3 — a DUPLICATE answer must not flip or wake a released parent.

    After a release that was refused for ONE instance, the record is
    kept (the retry path). The next (duplicate) answer runs the release
    again — and must NOT flip the already-released parent back to
    ``waiting_children`` nor enqueue a SECOND real wake.

    The record's per-instance outcomes are the idempotency ledger.
    """
    from daemon.services.question_pause_set import (
        read_question_pause_record,
    )

    _seed_tree(engine)
    pack = _ask(manager)
    await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )

    # The wake of ONE instance is refused (a transient failure) so the
    # record is KEPT — that is the state a duplicate answer finds.
    original_schedule = manager._schedule_explicit_handle_resume

    async def _refusing_schedule(**kwargs):
        if kwargs.get("instance_id") == "side-1":
            return None
        return await original_schedule(**kwargs)

    manager._schedule_explicit_handle_resume = _refusing_schedule
    try:
        # The first answer: the parent is released and woken; side-1's
        # release is refused.
        await _answer(manager)
    finally:
        manager._schedule_explicit_handle_resume = original_schedule

    parent_wakes = [
        w for w in manager.wakes if w.get("instance_id") == "root-1"
    ]
    assert parent_wakes, "the WC parent must be woken on the first answer"

    # The record survives (the refused instance keeps it) and carries
    # the FIRST pass's per-instance outcomes.
    from daemon.services.question_pause_set import read_question_pause_record

    record = await read_question_pause_record(manager, "asker-1", pack.id)
    outcomes = record.get("outcomes") or {}
    assert outcomes.get("root-1") in (
        "wake_scheduled",
        "wc_parked",
        "unpaused",
    ), (
        "the record must carry the first pass's outcome for the parent "
        f"(outcomes={outcomes})"
    )
    assert outcomes.get("side-1") == "wake_refused", (
        f"the refused instance must be recorded as refused ({outcomes})"
    )

    # The DUPLICATE answer: the parent is already released → it must
    # NOT be flipped again and must NOT be woken again.
    manager.wakes.clear()
    second = await _answer(manager)
    assert second["status"] == "already_delivered"
    assert not [
        w for w in manager.wakes if w.get("instance_id") == "root-1"
    ], "a duplicate answer must not enqueue a second WC wake"


@pytest.mark.asyncio
async def test_dismiss_path_deliberate_pause_survives_the_release(
    engine, manager
):
    """Rider — the DISMISS path honours the deliberate-pause exclusion.

    Shape (the same shape as council test (i), on the dismiss path):
    the question cascade parks the tree → a DELIBERATE pause lands on
    one side-effect target → the DISMISS path runs.

    Expected:
      * the deliberately parked target SURVIVES the dismiss (it stays
        parked and its interrupted turn is NOT re-armed — no wake);
      * the NON-deliberate member of the recorded set IS still released
        (the dismiss must not turn into a no-op for the rest).

    No ``suspension_reason`` is forged: the deliberate pause is the real
    cascade and the discriminator is the pause-cause TOMBSTONE.
    """
    from daemon.routers.instances import dismiss_question

    _seed_tree(engine)
    # A second, ordinary side-effect target (released by the dismiss).
    with Session(engine) as s:
        s.add(_mk_instance("side-2", parent_id="root-1"))
        s.commit()

    _ask(manager)
    await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )
    assert _status(engine, "side-1") == InstanceStatus.PAUSED.value
    assert _status(engine, "side-2") == InstanceStatus.PAUSED.value

    # A DELIBERATE pause lands on side-1 only. The subtree-scoped form
    # (``cascade_to_root=False``) is the PRODUCTION /stop semantics —
    # the whole-tree form (the user /pause default) would park the
    # entire tree, and then EVERY recorded member would be deliberately
    # parked (and the dismiss would correctly release none of them).
    await manager.pause_instance_cascade("side-1", cascade_to_root=False)

    # The DISMISS path runs (the real endpoint function).
    fake_request = MagicMock()
    fake_request.app.state.manager = manager
    fake_request.app.state.live_hub = None
    result = await dismiss_question("asker-1", fake_request)
    assert result["status"] in ("dismissed", "no_active_job")

    # The deliberate park SURVIVES: still parked, NOT re-armed.
    assert _status(engine, "side-1") == InstanceStatus.PAUSED.value, (
        "the dismiss path flipped a deliberately parked instance back to "
        "running — the exclusion hole"
    )
    assert "side-1" not in _woken_ids(manager), (
        "the dismiss path re-armed a deliberately stopped turn"
    )

    # The NON-deliberate member of the recorded set is still released.
    assert _status(engine, "side-2") != InstanceStatus.PAUSED.value, (
        "the dismiss path must still release the non-deliberate members "
        "of the recorded set"
    )


# ---------------------------------------------------------------------------
# H1-GUARD — the ROUTE TABLE is the evidence (a handler called as a
#             plain function is NOT)
# ---------------------------------------------------------------------------


def test_route_table_binds_the_dismiss_endpoint():
    """H1-GUARD — the dismiss route must bind to ``dismiss_question``.

    THE FAILURE MODE THIS GUARDS: a helper inserted BETWEEN a route's
    decorator and its handler binds the DECORATOR TO THE HELPER. The
    module still imports (FastAPI's ``eval_type_lenient`` swallows the
    NameError raised by the un-imported ``InstanceManager`` annotation),
    the real handler is never registered, and the endpoint answers 422
    (the HELPER's parameters are read as query params — ``manager`` /
    ``pack_id``). It is user-facing: the frontend calls the dismiss
    route, and 22 HTTP call sites depend on it.

    A FUNCTION-LEVEL test cannot see this: it calls the handler as a
    plain function and never consults the route table — which is
    exactly why the 39-case pack stayed green while the endpoint was
    broken. So the guard reads the ROUTE TABLE and asserts the binding.

    Asserts:
      (a) the dismiss route binds to ``dismiss_question`` (both in the
          instances router and in the mounted app);
      (b) NO route of the touched routers has an underscore-prefixed
          ``endpoint.__name__`` (the shape of the defect).
    """
    from daemon.api import app
    from daemon.routers import instances as instances_router

    # ── (a) the dismiss route binds to dismiss_question ──
    router_dismiss = [
        route
        for route in instances_router.router.routes
        if getattr(route, "path", "").endswith(
            "/{instance_id}/question/dismiss"
        )
    ]
    assert router_dismiss, (
        "the dismiss route must be registered on the instances router"
    )
    for route in router_dismiss:
        endpoint_name = getattr(route.endpoint, "__name__", None)
        assert endpoint_name == "dismiss_question", (
            f"the dismiss route binds to {endpoint_name!r}, not "
            f"'dismiss_question' — a helper was inserted between the "
            f"decorator and the handler (the endpoint would 422)"
        )

    app_dismiss = [
        route
        for route in app.routes
        if getattr(route, "path", "").endswith(
            "/{instance_id}/question/dismiss"
        )
    ]
    assert app_dismiss, (
        "the dismiss route must be registered on the mounted app"
    )
    for route in app_dismiss:
        endpoint_name = getattr(route.endpoint, "__name__", None)
        assert endpoint_name == "dismiss_question", (
            f"the mounted dismiss route binds to {endpoint_name!r} — the "
            "handler is not registered (the endpoint would 422)"
        )

    # ── (b) no underscore-prefixed endpoint in the touched routers ──
    for label, routes in (
        ("daemon/routers/instances.py", instances_router.router.routes),
        ("daemon/api.py", app.routes),
    ):
        offenders = sorted(
            {
                getattr(getattr(route, "endpoint", None), "__name__", "")
                for route in routes
                if getattr(
                    getattr(route, "endpoint", None), "__name__", ""
                ).startswith("_")
            }
        )
        assert not offenders, (
            f"{label} registers underscore-prefixed endpoint(s) {offenders} "
            "— a private helper must never be route-shaped (the decorator "
            "would bind to the helper instead of the handler)"
        )


# ---------------------------------------------------------------------------
# SYMPTOM REPRO — the RED the fix must flip
# ---------------------------------------------------------------------------


def _parent_can_leave_waiting_children(manager, engine, root_id) -> bool:
    """Model the parent-completion gate (ChildReportsService).

    A ``waiting_children`` parent leaves its parked state ONLY when
    every live child has reported terminal. A child that was never
    re-armed (no wake) can never report → the parent stays parked
    forever.

    The harness models "the child's turn completed and reported" as:
    the child was woken (a wake was scheduled for it) — the graph then
    runs the turn to completion and the report is delivered by the
    normal report lane.

    Returns True when the parent is released.
    """
    from sqlmodel import select

    with Session(engine) as s:
        children = s.exec(
            select(Instance).where(Instance.parent_id == root_id)
        ).all()
        parent = s.get(Instance, root_id)
        if parent is None:
            return False
        parent_status = parent.status
        child_ids = [c.instance_id for c in children]
    if parent_status != InstanceStatus.WAITING_CHILDREN.value:
        # Already out of the parked state (the pre-fix tree cascade
        # flips every parked node to ``running``) — the gate does not
        # own this parent any more.
        return parent_status not in (
            InstanceStatus.PAUSED.value,
            InstanceStatus.WAITING_CHILDREN.value,
        )
    woken = set(_woken_ids(manager))
    for child_id in child_ids:
        if child_id not in woken:
            # The child's turn was never re-armed → it can never report
            # terminal → the parent stays parked.
            return False
    return True


@pytest.mark.asyncio
async def test_symptom_repro_side_effect_child_strands_the_parent(
    engine, manager
):
    """SYMPTOM REPRO — the child stays stranded; the parent stays parked.

    Pre-fix (RED): the answer is delivered, but the side-effect
    target's interrupted turn is NEVER re-armed (the tree-scoped resume
    flips its status only — there is no task row, hence no suspension
    handle, hence no wake). The child can therefore never report
    terminal, and the parent can never leave ``waiting_children``.

    Post-fix (GREEN): the recorded set is released and the child's turn
    is re-armed → the child reports → the parent leaves
    ``waiting_children``.
    """
    _seed_tree(engine)
    _ask(manager)
    await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )
    assert _status(engine, "side-1") == InstanceStatus.PAUSED.value

    await _answer(manager)

    # The child must have been re-armed — otherwise its interrupted
    # turn can never complete.
    assert "side-1" in _woken_ids(manager), (
        "the side-effect target was never re-armed — its turn can never "
        "complete, so the parent stays in waiting_children forever"
    )
    # …and therefore the parent is released.
    assert _parent_can_leave_waiting_children(manager, engine, "root-1"), (
        "the parent is still parked in waiting_children — the child's "
        "turn never completed"
    )


@pytest.mark.asyncio
async def test_symptom_repro_late_pause_leaves_the_child_parked(
    engine, manager
):
    """SYMPTOM REPRO (late-pause race) — the child REMAINS parked.

    Pre-fix (RED): the deferred question pause lands AFTER the answer
    was consumed → the whole tree is parked with no question to answer
    → the child REMAINS paused and the parent is parked with it.

    Post-fix (GREEN): a question pause whose question is no longer
    outstanding is REFUSED → the child is never parked.
    """
    _seed_tree(engine)
    _ask(manager)
    # The answer lands first.
    await _answer(manager)

    # The deferred pause fires afterwards.
    pause_result = await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )

    assert pause_result.get("question_pause_refused") is True, (
        "a question pause fired after the question was consumed — the "
        "tree is parked with no answering counterpart"
    )
    assert _status(engine, "side-1") != InstanceStatus.PAUSED.value, (
        "the side-effect target remained parked after a late pause"
    )
    assert _status(engine, "root-1") != InstanceStatus.PAUSED.value


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

    recorded = await read_question_paused_set(manager, "asker-1", pack.id)
    assert "side-1" in recorded
    # The originator is NOT a side-effect target (it owns the
    # awaiting_answer handle and is resumed by the answer gate).
    assert "asker-1" not in recorded

    # Durable: the record lives in the DB (a fresh manager over the
    # same engine sees it — a restart does not lose it).
    fresh = _FakeManager(engine)
    assert "side-1" in await read_question_paused_set(
        fresh, "asker-1", pack.id
    )

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

    The stranded state is modelled honestly: the FIRST delivery's
    release fails (a transient read error on the recorded instance), so
    the record is KEPT (a record is only deleted after a CLEAN
    release) and the set stays parked. The duplicate answer then
    retries the release.
    """
    _seed_tree(engine)
    pack = _ask(manager)
    await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )

    # The first delivery's release hits a transient error on the
    # recorded instance → the record survives, the set stays parked.
    original_get = manager._instance_repository.get

    def _boom(instance_id):
        if instance_id == "side-1":
            raise RuntimeError("transient repository error")
        return original_get(instance_id)

    manager._instance_repository.get = _boom
    try:
        await _answer(manager)
    finally:
        manager._instance_repository.get = original_get

    assert _status(engine, "side-1") == InstanceStatus.PAUSED.value, (
        "the failed release must leave the recorded set parked"
    )
    from daemon.services.question_pause_set import read_question_paused_set

    assert "side-1" in await read_question_paused_set(manager, "asker-1", pack.id), (
        "a failed release must KEEP the record so a later attempt can "
        "retry it"
    )

    # The duplicate answer (CAS loser) retries the release.
    manager.wakes.clear()
    second = await _answer(manager)
    assert second["status"] == "already_delivered"
    assert _status(engine, "side-1") != InstanceStatus.PAUSED.value
    assert "side-1" in _woken_ids(manager)
    # The record is deleted after the clean release.
    assert await read_question_paused_set(manager, "asker-1", pack.id) == []


@pytest.mark.asyncio
async def test_recorded_set_is_deleted_after_a_clean_release(engine, manager):
    """#10 — the record does not accumulate after a successful release."""
    from daemon.services.question_pause_set import (
        QUESTION_PAUSED_SET_METADATA_KEY,
        read_question_paused_set,
    )

    _seed_tree(engine)
    pack = _ask(manager)
    await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )
    assert set(
        await read_question_paused_set(manager, "asker-1", pack.id)
    ) == {"side-1", "root-1"}

    await _answer(manager)

    assert await read_question_paused_set(manager, "asker-1", pack.id) == []
    raw = manager._instance_repository.get_metadata_value(
        "asker-1", QUESTION_PAUSED_SET_METADATA_KEY
    )
    assert not raw, f"the released record must be deleted (raw={raw!r})"


@pytest.mark.asyncio
async def test_durable_shadow_pack_id_is_resolved(engine, manager):
    """CRITICAL #1 — the durable shadow's pack id is ``pack_id``.

    The frozen ``pack_to_dict`` schema keys the id as ``pack_id`` (NOT
    ``id``). Reading the wrong key silently dropped the R1 record on
    the durable-shadow path (no RAM pack — e.g. right after a daemon
    restart).
    """
    from daemon.services.question_pause_set import (
        question_pack_state,
        read_question_paused_set,
        record_question_paused_set,
    )

    _seed_tree(engine)
    pack = _ask(manager)
    # Simulate the post-restart shape: the RAM store is EMPTY, only the
    # durable shadow survives.
    manager._question_manager.clear_question_pack("asker-1")

    state, resolved_pack_id = await question_pack_state(manager, "asker-1")
    assert state == "pending"
    assert resolved_pack_id == pack.id, (
        "the durable shadow's pack id must resolve to the pack id "
        f"(resolved={resolved_pack_id!r}, expected={pack.id!r})"
    )

    # The record is written and readable back on that path.
    recorded = await record_question_paused_set(
        manager, "asker-1", resolved_pack_id, ["side-1"]
    )
    assert recorded == ["side-1"]
    assert await read_question_paused_set(
        manager, "asker-1", resolved_pack_id
    ) == ["side-1"]


@pytest.mark.asyncio
async def test_record_drop_is_loud(engine, manager, caplog):
    """CRITICAL #1 — a dropped record is a LOUD error, never a silent one."""
    from daemon.services.question_pause_set import record_question_paused_set

    _seed_tree(engine)
    with caplog.at_level("ERROR"):
        recorded = await record_question_paused_set(
            manager, "asker-1", None, ["side-1"]
        )
    assert recorded == []
    assert any(
        "RECORD DROPPED" in rec.getMessage() for rec in caplog.records
    ), "a dropped record must be logged at ERROR level"


@pytest.mark.asyncio
async def test_release_runs_even_when_the_cascade_raises(engine, manager):
    """CRITICAL #2 — a raising cascade must not skip the release.

    The pack is consumed by the CAS before the cascade runs; a cascade
    failure (500) must not leave the recorded set parked.
    """
    _seed_tree(engine)
    _ask(manager)
    await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )
    assert _status(engine, "side-1") == InstanceStatus.PAUSED.value

    # The cascade raises.
    original_cascade = manager.resume_instance_cascade

    async def _raising_cascade(instance_id):
        raise RuntimeError("cascade exploded")

    manager.resume_instance_cascade = _raising_cascade
    with pytest.raises(Exception):
        await _answer(manager)
    manager.resume_instance_cascade = original_cascade

    # The recorded set was released anyway.
    assert _status(engine, "side-1") != InstanceStatus.PAUSED.value
    assert "side-1" in _woken_ids(manager)


@pytest.mark.asyncio
async def test_terminal_asker_releases_the_recorded_set(engine, manager):
    """#9 — a terminal asker releases the recorded set.

    The asker is gone: nothing will ever answer its outstanding
    question, so the recorded side-effect set must be released. Without
    this the set stays parked forever (the D1 predicate keeps
    protecting a pack nobody can answer).
    """
    from fastapi import HTTPException

    from daemon.services.question_pause_set import read_question_paused_set

    _seed_tree(engine)
    pack = _ask(manager)
    await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )
    # The asker dies before the answer arrives.
    with Session(engine) as s:
        row = s.get(Instance, "asker-1")
        row.status = InstanceStatus.TERMINATED.value
        s.commit()

    with pytest.raises(HTTPException) as exc_info:
        await _answer(manager)
    assert exc_info.value.status_code == 410

    assert _status(engine, "side-1") != InstanceStatus.PAUSED.value
    assert "side-1" in _woken_ids(manager)
    assert await read_question_paused_set(manager, "asker-1", pack.id) == []


@pytest.mark.asyncio
async def test_pack_id_mismatch_does_not_release_the_outstanding_set(
    engine, manager
):
    """#9 (adjudicated) — the mismatch branch releases NOTHING.

    The caller's pack id does not match the asker's current pack, so
    the outstanding question is a DIFFERENT one and still owns the
    park: releasing its recorded set would tear the tree apart while
    the question is still live.
    """
    from fastapi import HTTPException

    from daemon.routers.answer_helper import answer_questions_via_instance

    _seed_tree(engine)
    _ask(manager)
    await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )

    with pytest.raises(HTTPException) as exc_info:
        await answer_questions_via_instance(
            manager, "asker-1", {"q1": "yes"}, "not-the-current-pack", None
        )
    assert exc_info.value.status_code == 400
    assert _status(engine, "side-1") == InstanceStatus.PAUSED.value
    assert "side-1" not in _woken_ids(manager)


@pytest.mark.asyncio
async def test_waiting_children_parent_is_restored_and_woken(engine, manager):
    """The WC parent returns to ``waiting_children`` and is woken.

    A ``waiting_children`` parent is parked waiting for its children;
    the dependency bus owns its wake. The release therefore restores the
    RECORDED pre-pause status (``waiting_children`` — NOT ``running``)
    and routes the parent through the sanctioned WC wake path
    (``resume_processing_job(silent=True)`` → ``b2_wc_wake``).
    """
    _seed_tree(engine)
    _ask(manager)
    await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )
    assert _status(engine, "root-1") == InstanceStatus.PAUSED.value

    await _answer(manager)

    assert _status(engine, "root-1") == InstanceStatus.WAITING_CHILDREN.value, (
        "the WC parent must return to waiting_children (the dependency "
        "bus owns its wake), not to running"
    )
    assert "root-1" in _woken_ids(manager), (
        "the WC parent must go through the sanctioned WC wake path"
    )
    assert any(
        w.get("route_outcome") == "b2_wc_wake" for w in manager.wakes
    ), "the wake must be the WC wake route"


@pytest.mark.asyncio
async def test_sweeper_admits_instances_with_a_paused_task(engine, manager):
    """#4 — a PAUSED-task side-effect target is a sweep candidate.

    A paused task is exactly the shape of a question-pause side-effect
    target (the cascade suspends the child's RUNNING turn) and a
    paused task is NOT claimable by the worker pool — so such an
    instance can never complete on its own. The sweeper must admit it.
    """
    from daemon.services.stale_pause_sweep import sweep_stale_pauses

    _seed_tree(engine)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with Session(engine) as s:
        s.add(
            _mk_instance(
                "paused-task-1",
                parent_id="root-1",
                status=InstanceStatus.PAUSED.value,
                paused_at=old,
            )
        )
        s.add(
            Task(
                instance_id="paused-task-1",
                work_id="work-paused-task-1",
                task_type="PROCESS_MESSAGE",
                status=TaskStatus.PAUSED.value,
                suspension_reason="paused_by_parent",
                resume_target_turn_id="work-paused-task-1",
            )
        )
        s.commit()
    await _mark_question_paused(manager, "paused-task-1", paused_at=old)

    result = await sweep_stale_pauses(manager, age_bound_seconds=7200)

    assert "paused-task-1" in result["instances"], (
        "a PAUSED-task instance must be admitted by the sweeper"
    )
    assert _status(engine, "paused-task-1") != InstanceStatus.PAUSED.value


@pytest.mark.asyncio
async def test_sweeper_skips_instances_with_a_live_task(engine, manager):
    """#4 — a PENDING/RUNNING task means the worker pool owns the turn."""
    from daemon.services.stale_pause_sweep import sweep_stale_pauses

    _seed_tree(engine)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with Session(engine) as s:
        s.add(
            _mk_instance(
                "live-task-1",
                parent_id="root-1",
                status=InstanceStatus.PAUSED.value,
                paused_at=old,
            )
        )
        s.add(
            Task(
                instance_id="live-task-1",
                work_id="work-live-task-1",
                task_type="PROCESS_MESSAGE",
                status=TaskStatus.PENDING.value,
            )
        )
        s.commit()
    await _mark_question_paused(manager, "live-task-1", paused_at=old)

    result = await sweep_stale_pauses(manager, age_bound_seconds=7200)

    assert "live-task-1" not in result["instances"]
    assert _status(engine, "live-task-1") == InstanceStatus.PAUSED.value


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
    await _mark_question_paused(manager, "stranded-1", paused_at=old)

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
    await _mark_question_paused(manager, "stale-1", paused_at=old)
    await _mark_question_paused(manager, "young-1", paused_at=fresh)

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
    await _mark_question_paused(manager, "stale-2", paused_at=old)

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
    # A live question on asker-2 — a question-paused asker DOES carry
    # the pause-cause marker (it is the question stratum), and the D1
    # predicate is what protects it.
    await _mark_question_paused(manager, "asker-2", paused_at=old)
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
    await _mark_question_paused(manager, "side-2", paused_at=old)
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
    await _mark_question_paused(manager, "side-3", paused_at=old)
    _ask(manager, "asker-1")
    # The question is consumed — the pack is no longer pending (the
    # answer path clears the durable shadow at answer consumption).
    manager._question_manager.set_answers("asker-1", {"q1": "yes"})
    from daemon.services.midflight_qa import clear_question_pack_metadata

    clear_question_pack_metadata(manager, "asker-1")

    result = await sweep_stale_pauses(manager, age_bound_seconds=7200)

    assert "side-3" in result["instances"]
    assert _status(engine, "side-3") == InstanceStatus.RUNNING.value


# ---------------------------------------------------------------------------
# N1 — the pause-cause marker block is O(N+M), not O(N x (N+M))
# ---------------------------------------------------------------------------


def _seed_park_tree(engine: Engine) -> dict[str, str]:
    """root-1 (WAITING_CHILDREN) + kid-1/kid-2 (RUNNING) + kid-3 (PAUSED).

    A DELIBERATE cascade on kid-1 parks 3 nodes (root-1, kid-1, kid-2)
    and finds 1 node ALREADY parked (kid-3) -> the pause-cause marker
    block touches 3 + 1 = 4 nodes.
    """
    with Session(engine) as s:
        s.add(
            _mk_instance(
                "root-1",
                status=InstanceStatus.WAITING_CHILDREN.value,
                version=3,
            )
        )
        s.add(_mk_instance("kid-1", parent_id="root-1"))
        s.add(_mk_instance("kid-2", parent_id="root-1"))
        s.add(
            _mk_instance(
                "kid-3",
                parent_id="root-1",
                status=InstanceStatus.PAUSED.value,
                paused_at=NOW_ISO,
            )
        )
        s.commit()
    return {
        "root": "root-1",
        "kid1": "kid-1",
        "kid2": "kid-2",
        "parked": "kid-3",
    }


@pytest.mark.asyncio
async def test_n1_marker_work_happens_once_per_affected_node(
    engine, manager, monkeypatch
):
    """N1 — the marker block touches EXACTLY ONE node per marker operation.

    The non-question branch of the pause-cause marker block was nested
    INSIDE the ``for node_id in paused_ids`` loop (and its own
    ``node_id`` shadowed the outer one), so the whole clear list
    (``paused_ids + already_paused_ids``) was re-cleared once per
    parked node: N x (N+M) ``delete_metadata`` transactions — each its
    own session + commit — on the user-facing SYNCHRONOUS ``/pause``
    path (inside the cascade, before the response). ~900 deletes for a
    30-node tree.

    The pause cascade is deliberately batched (ONE UPDATE for all
    nodes) to avoid an N+1 transaction storm; the marker block must not
    regress that by ~N x.

    3 parked nodes + 1 already-paused node -> EXACTLY 4 marker
    operations (not 3 x 4 = 12).
    """
    from daemon.services import question_pause_set as qps

    tree = _seed_park_tree(engine)

    calls: list[tuple[str, str]] = []
    # C2: the deliberate branch now writes the TOMBSTONE (one marker
    # operation per node — the N1 count is unchanged).
    real_clear = qps.mark_deliberate_pause_override
    real_stamp = qps.stamp_question_pause_marker

    async def _spy_clear(mgr, instance_id):
        calls.append(("clear", instance_id))
        return await real_clear(mgr, instance_id)

    async def _spy_stamp(mgr, instance_id, pack_id, paused_at=None):
        calls.append(("stamp", instance_id))
        return await real_stamp(mgr, instance_id, pack_id, paused_at)

    monkeypatch.setattr(qps, "mark_deliberate_pause_override", _spy_clear)
    monkeypatch.setattr(qps, "stamp_question_pause_marker", _spy_stamp)

    await manager.pause_instance_cascade(tree["kid1"])

    cleared = sorted(i for kind, i in calls if kind == "clear")
    assert cleared == sorted(
        [tree["root"], tree["kid1"], tree["kid2"], tree["parked"]]
    ), (
        "the non-question branch must clear the marker on every parked "
        "node AND every already-paused node — exactly once each"
    )
    assert len(calls) == 4, (
        f"expected exactly 4 marker operations (3 parked + 1 already "
        f"paused), got {len(calls)}: the clear branch is nested inside "
        f"the per-node loop and re-runs N x (N+M) times"
    )
    assert not [c for c in calls if c[0] == "stamp"], (
        "a DELIBERATE pause must never stamp the question marker"
    )


@pytest.mark.asyncio
async def test_n1_question_pause_stamps_the_marker_once_per_parked_node(
    engine, manager, monkeypatch
):
    """N1 (question half) — one stamp per parked node, no clears.

    The question cascade stamps the pause-cause marker on every node it
    parks (the asker AND the side-effect targets). An ALREADY-parked
    node is NOT stamped (it was not parked by this question) and the
    question branch never clears.
    """
    from daemon.services import question_pause_set as qps

    tree = _seed_park_tree(engine)

    calls: list[tuple[str, str]] = []
    real_clear = qps.clear_question_pause_marker
    real_stamp = qps.stamp_question_pause_marker

    async def _spy_clear(mgr, instance_id):
        calls.append(("clear", instance_id))
        return await real_clear(mgr, instance_id)

    async def _spy_stamp(mgr, instance_id, pack_id, paused_at=None):
        calls.append(("stamp", instance_id))
        return await real_stamp(mgr, instance_id, pack_id, paused_at)

    monkeypatch.setattr(qps, "clear_question_pause_marker", _spy_clear)
    monkeypatch.setattr(qps, "stamp_question_pause_marker", _spy_stamp)

    _ask(manager, tree["kid1"])
    await manager.pause_instance_cascade(
        tree["kid1"], suspension_reason="awaiting_answer"
    )

    stamped = sorted(i for kind, i in calls if kind == "stamp")
    assert stamped == sorted([tree["root"], tree["kid1"], tree["kid2"]]), (
        "the question cascade stamps the marker on EXACTLY the nodes it "
        "parks — once each"
    )
    assert len(calls) == 3, (
        f"expected exactly 3 marker operations, got {len(calls)}"
    )
    assert not [c for c in calls if c[0] == "clear"], (
        "a QUESTION pause must never clear the pause-cause marker"
    )


@pytest.mark.asyncio
async def test_n5_already_paused_node_loses_a_stale_question_marker(
    engine, manager
):
    """N5 — a DELIBERATE cascade clears a stale marker on an ALREADY-parked node.

    The ``already_paused_ids`` branch of the marker block was
    implemented but unpinned: an instance that was ALREADY parked when a
    DELIBERATE cascade ran must lose its (possibly stale) question
    marker — otherwise the backstop could later revert a pause the
    operator intended.
    """
    from daemon.services.question_pause_set import (
        QUESTION_PAUSED_MARKER_METADATA_KEY,
    )

    with Session(engine) as s:
        s.add(
            _mk_instance(
                "parked-1",
                status=InstanceStatus.PAUSED.value,
                paused_at=NOW_ISO,
            )
        )
        s.commit()
    await _mark_question_paused(manager, "parked-1", paused_at=NOW_ISO)
    assert manager._instance_repository.get_metadata_value(
        "parked-1", QUESTION_PAUSED_MARKER_METADATA_KEY
    )

    # A DELIBERATE (non-question) cascade over an ALREADY-parked node.
    await manager.pause_instance_cascade("parked-1")

    # C2: the deliberate pause REPLACES the stale positive marker with
    # an explicit TOMBSTONE (the positive marker is what the backstop
    # keys on — a tombstone carries no pack_id, so the backstop can
    # never revert the operator's park).
    marker = manager._instance_repository.get_metadata_value(
        "parked-1", QUESTION_PAUSED_MARKER_METADATA_KEY
    )
    assert not (marker or {}).get("pack_id"), (
        "a deliberate pause must clear the stale POSITIVE question "
        f"marker of a node that was ALREADY parked (marker={marker!r})"
    )
    assert (marker or {}).get("deliberate") is True, (
        f"a deliberate pause must write the tombstone (marker={marker!r})"
    )


# ---------------------------------------------------------------------------
# N3 — precedence: a LATER DELIBERATE pause wins on the release path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_n3_later_deliberate_pause_wins_over_the_recorded_release(
    engine, manager
):
    """N3 — the release path defers to a LATER DELIBERATE pause.

    The sweeper already defers to a later deliberate pause (a
    non-question pause CLEARS the pause-cause marker, so the instance is
    not a candidate). The release-from-record path did NOT: the
    instance is still in the asker's recorded set, so when the answer
    arrived the release un-paused it AND re-armed its deliberately
    stopped turn. The two paths of the same feature disagreed on
    precedence.

    The release must leave the instance PARKED and must NOT re-arm its
    stopped turn.
    """
    from daemon.services.question_pause_set import (
        OUTCOME_DELIBERATE_PAUSE,
        release_question_paused_set,
    )

    _seed_tree(engine)
    # side-1 owns a turn — the question cascade suspends it (a cascade
    # artifact carries ``paused_by_parent``). The task type is the
    # PRODUCTION spelling: the release path's paused-turn probe
    # (``find_paused_or_cancellable_turn``) only matches
    # ``process_message`` / ``process_report``.
    with Session(engine) as s:
        s.add(
            Task(
                instance_id="side-1",
                work_id="work-side-1",
                task_type="process_message",
                status=TaskStatus.RUNNING.value,
            )
        )
        s.commit()

    pack = _ask(manager)
    await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )
    assert _status(engine, "side-1") == InstanceStatus.PAUSED.value

    # The operator parks side-1 DELIBERATELY: the question cascade
    # already parked it, so the deliberate pause lands on the
    # already-paused branch and clears its pause-cause marker.
    await manager.pause_instance_cascade("side-1")
    # The deliberate park owns the turn. (A deliberate pause of an
    # ALREADY-parked node does not re-suspend the turn — the cascade
    # only suspends RUNNING turns — so the deliberate reason is written
    # onto the turn explicitly: the same shape the sweep-side pin
    # uses.)
    with Session(engine) as s:
        from sqlmodel import select

        turn = s.exec(select(Task).where(Task.instance_id == "side-1")).one()
        turn.suspension_reason = "paused_external"
        s.commit()

    summary = await release_question_paused_set(
        manager, "asker-1", pack.id, reason="answer"
    )

    assert summary["outcomes"]["side-1"] == OUTCOME_DELIBERATE_PAUSE, (
        "the release path must defer to a later deliberate pause"
    )
    assert _status(engine, "side-1") == InstanceStatus.PAUSED.value, (
        "a deliberately parked instance must stay parked when the "
        "answer arrives"
    )
    assert "side-1" not in _woken_ids(manager), (
        "the deliberately stopped turn of a deliberately parked instance "
        "must NOT be re-armed"
    )


@pytest.mark.asyncio
async def test_n3_question_stratum_is_still_released(engine, manager):
    """N3 (counter-pin) — the QUESTION stratum is released exactly as before.

    The N3 guard must fire for a DELIBERATE pause only. A turn the
    question cascade suspended carries ``awaiting_answer`` (the
    originator) or ``paused_by_parent`` (the cascade artifacts) — those
    instances are released exactly as before.
    """
    from daemon.services.question_pause_set import (
        OUTCOME_UNPAUSED,
        OUTCOME_WAKE_SCHEDULED,
        unpause_instance_after_question_pause,
    )

    _seed_tree(engine)
    # The production task-type spelling — the release path's paused-turn
    # probe (``find_paused_or_cancellable_turn``) only matches
    # ``process_message`` / ``process_report``.
    with Session(engine) as s:
        s.add(
            Task(
                instance_id="side-1",
                work_id="work-side-1",
                task_type="process_message",
                status=TaskStatus.RUNNING.value,
            )
        )
        s.commit()

    _ask(manager)
    await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )

    # The cascade suspended side-1's turn as a cascade artifact.
    with Session(engine) as s:
        from sqlmodel import select

        turn = s.exec(select(Task).where(Task.instance_id == "side-1")).one()
        assert turn.suspension_reason == "paused_by_parent"

    outcome = await unpause_instance_after_question_pause(
        manager, "side-1", reason="answer"
    )

    assert outcome in (OUTCOME_UNPAUSED, OUTCOME_WAKE_SCHEDULED), (
        f"the question stratum must still be released (got {outcome!r})"
    )
    assert _status(engine, "side-1") == InstanceStatus.RUNNING.value
    assert "side-1" in _woken_ids(manager), (
        "the question stratum's interrupted turn must still be re-armed"
    )
