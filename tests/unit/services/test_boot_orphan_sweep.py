"""Boot-time task-less orphan sweep — D13 follow-up (NG-8).

Feature: boot-orphan-sweep. Pins the implementation behavior:

  * The orphan selector returns ONLY task-less RUNNING instances
    with an active JobItem (the safe clause set; the
    ``running+idle`` recommended shape is narrowed to ``running``
    only for the false-positive reason — see ``test_idle_not_swept``).
  * ``WAITING_CHILDREN`` parents are NEVER swept (the parked-parent
    false-positive concern named in the commission; the live parent
    is owned by the DependencyBus, NOT the boot sweep).
  * The boot pass uses **checkpoint last-write < boot_epoch** as the
    natural-immunity idempotency mechanism (no ``orphan_swept_at``
    column was added — the existing checkpoint ``ts`` is the
    generation barrier).
  * Per-instance failure isolation: a single exception does NOT
    abort the sweep.
  * Kill-switch and ``boot_epoch=None`` short-circuits match the
    companion AutoContinue pass at
    ``daemon/services/auto_continue_boot_pass.py``.

The tests deliberately use the same hermetic SQLite fixture as
``test_auto_continue_interleaving.py`` (per-worktree gate, per-test
``tmp_path`` database, NullPool, no live daemon, no live install
paths).
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.services.auto_continue_boot_pass as pass_mod
from daemon.repositories.instance.models import Instance
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.boot_epoch import set_boot_epoch
from daemon.services.timestamps import now_utc_naive


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    """REAL SQLite FILE database, NullPool.

    Note: ``PRAGMA foreign_keys=ON`` is intentionally NOT enabled —
    the test only exercises the ``find_orphaned_live_turns`` selector
    predicate; FK enforcement is unnecessary and would force every
    JobItem insert to seed a paired project + queue row, which is
    out of scope for the orphan-sweep contract.
    """
    eng = create_engine(
        f"sqlite:///{tmp_path}/orphan_sweep.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def task_repo(engine: Engine) -> TaskRepository:
    return TaskRepository(engine)


def _seed_instance(
    engine: Engine, instance_id: str, status: str = "running"
) -> None:
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="ari",
                agent_dir="/agents/ari",
                status=status,
            )
        )
        s.commit()


def _seed_jobitem(
    engine: Engine,
    instance_id: str,
    admission_state: str,
    deleted_at: str | None = None,
) -> str:
    """Insert a JobItem bound to ``instance_id``. Returns the job_id.

    ``deleted_at`` is a tombstone marker — a set value simulates the
    soft-delete state documented at
    ``daemon/repositories/job_queue/repository.py:79-84`` (where
    ``admission_state`` is FROZEN at deletion time, NOT a liveness
    claim, so every reconcile query filters ``deleted_at IS NULL``
    deliberately). The default ``None`` keeps all pre-existing call
    sites identical — only new tests that probe the soft-delete
    tombstone behavior should pass a non-None value.
    """
    job_id = f"job-{uuid4().hex[:12]}"
    with Session(engine) as s:
        s.add(
            JobItem(
                job_id=job_id,
                agent_id="ari",
                agent_dir="/agents/ari",
                message="orphan-sweep test",
                project_id="proj-test",
                queue_id="queue-test",
                instance_id=instance_id,
                admission_state=admission_state,
                deleted_at=deleted_at,
            )
        )
        s.commit()
    return job_id


def _seed_completed_task(
    engine: Engine, instance_id: str
) -> None:
    """Seed a non-active task row (COMPLETED) so the instance has task history but no active task."""
    with Session(engine) as s:
        s.add(
            Task(
                work_id=f"work-old-{uuid4().hex[:12]}",
                task_type="process_message",
                instance_id=instance_id,
                status=TaskStatus.COMPLETED.value,
                created_at=now_utc_naive(),
            )
        )
        s.commit()


# ---------------------------------------------------------------------------
# Mock manager — pass calls ``_has_checkpoint``,
# ``_get_checkpoint_last_write_naive_utc`` is INLINED in the pass via
# ``manager.checkpointer.raw_saver.aget_tuple``; we mock at the
# ``checkpointer.raw_saver.aget_tuple`` boundary.
# ---------------------------------------------------------------------------


class _CheckpointMock:
    """Mock ``checkpointer.raw_saver`` — tests control per-instance last-write.

    The pass accesses the saver via ``manager.checkpointer.raw_saver``
    (matching ``manager.py:11285`` where ``_has_checkpoint`` reads
    the raw saver). This mock exposes itself as that raw_saver.
    """

    def __init__(self) -> None:
        # instance_id → naive-utc datetime or None (no checkpoint)
        self.last_writes: dict[str, datetime | None] = {}
        self.aget_calls: list[str] = []
        self.aget_fail: dict[str, Exception] = {}

    async def aget_tuple(self, config: dict) -> Any:
        instance_id = config["configurable"]["thread_id"]
        self.aget_calls.append(instance_id)
        if instance_id in self.aget_fail:
            raise self.aget_fail[instance_id]
        ts = self.last_writes.get(instance_id)
        if ts is None:
            return None
        ts_aware = ts.replace(tzinfo=timezone.utc)
        ts_iso = ts_aware.isoformat()
        ckpt = {
            "v": 1,
            "id": f"ckpt-{uuid4().hex[:12]}",
            "ts": ts_iso,
            "channel_values": {},
            "channel_versions": {},
            "versions_seen": {},
        }
        return _CheckpointTuple(ckpt)


class _CheckpointTuple:
    def __init__(self, checkpoint: dict) -> None:
        self.checkpoint = checkpoint
        self.metadata: dict = {}
        self.config: dict = {}
        self.parent_config: dict | None = None
        self.pending_writes: list | None = None


class _CheckpointerAdapterMock:
    """Mock the ``InstanceManager.checkpointer`` property.

    The pass reaches the saver via ``manager.checkpointer.raw_saver``
    (the checkpointer adapter wraps the raw saver; see
    ``daemon/checkpoint_adapter.py:772-808``). This class exposes
    ``raw_saver`` directly so the call chain resolves.
    """

    def __init__(self) -> None:
        self.raw_saver = _CheckpointMock()


class _MockManager:
    """Mock ``InstanceManager`` — pass calls these methods."""

    def __init__(self, task_repo: TaskRepository) -> None:
        self._task_repo = task_repo
        self.checkpointer = _CheckpointerAdapterMock()
        # per-instance resume returns
        self.resume_returns: dict[str, dict[str, Any] | None] = {}
        self.resume_calls: list[dict[str, Any]] = []

    async def _has_checkpoint(self, instance_id: str) -> bool:
        return self.checkpointer.raw_saver.last_writes.get(instance_id) is not None

    async def _schedule_explicit_handle_resume(self, **kwargs) -> dict[str, Any] | None:
        iid = kwargs.get("instance_id", "")
        self.resume_calls.append(kwargs)
        return self.resume_returns.get(iid, {"status": "resuming"})


@contextmanager
def _env(name: str, value: str | None):
    old = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


# ---------------------------------------------------------------------------
# Repo selector tests
# ---------------------------------------------------------------------------


def test_empty_repo_returns_empty(task_repo: TaskRepository) -> None:
    """No instances at all → empty list."""
    out = task_repo.find_orphaned_live_turns(boot_epoch=now_utc_naive())
    assert out == []


def test_boot_epoch_none_returns_empty(task_repo: TaskRepository) -> None:
    """Defensive: ``boot_epoch=None`` short-circuits."""
    out = task_repo.find_orphaned_live_turns(boot_epoch=None)  # type: ignore[arg-type]
    assert out == []


def test_orphan_running_with_active_jobitem_returned(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """Single orphan: status='running' + no active task + active JobItem → returned."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)
    out = task_repo.find_orphaned_live_turns(boot_epoch=now_utc_naive())
    assert out == [iid]


def test_orphan_with_queued_jobitem_returned(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """Active JobItem in QUEUED state also matches (the active-admission-state set)."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.QUEUED.value)
    out = task_repo.find_orphaned_live_turns(boot_epoch=now_utc_naive())
    assert out == [iid]


def test_soft_deleted_jobitem_not_swept(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """A soft-deleted JobItem tombstone MUST NOT satisfy the active clause.

    Regression for boot-orphan-sweep review advisory A1: the
    canonical live-JobItem predicate in this codebase requires
    BOTH ``admission_state IN (ACTIVE_ADMISSION_STATES)`` AND
    ``deleted_at IS NULL`` (see
    ``daemon/services/job_queue_service.py:2602-2608`` and
    ``daemon/repositories/job_queue/repository.py:79-84`` — soft-delete
    semantics). A JobItem row with frozen
    ``admission_state='queued'`` but ``deleted_at`` SET is a
    tombstone — its ``admission_state`` is a deletion-time
    snapshot, NOT a liveness claim — and the boot-orphan-sweep
    selector must exclude it. Otherwise the instance would be
    spuriously swept/resumed at boot.

    Shape: orphan-looking instance (status='running', no active
    task row) + a single JobItem whose ``admission_state`` matches
    the active set BUT whose ``deleted_at`` is set → selector
    returns ``[]``.
    """
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(
        engine,
        iid,
        admission_state=AdmissionState.QUEUED.value,
        # Set ``deleted_at`` to a non-empty deletion-time snapshot
        # string — mirrors the soft-delete tombstone shape the
        # canonical predicate is designed to exclude.
        deleted_at="2026-10-19T12:00:00",
    )
    out = task_repo.find_orphaned_live_turns(boot_epoch=now_utc_naive())
    assert out == [], (
        "Soft-deleted JobItem (deleted_at set, admission_state frozen "
        "at 'queued') MUST NOT satisfy the exists() subquery — its "
        "admission_state is a deletion-time snapshot, NOT a liveness "
        "claim; the canonical live-JobItem predicate requires BOTH "
        "admission_state IN active AND deleted_at IS NULL. Returning "
        "the instance here would spuriously sweep / resume it at boot."
    )


def test_idle_instance_not_swept(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """NARROWED clause set — IDLE status is NOT swept (graph at END, would create spurious wake)."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="idle")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)
    out = task_repo.find_orphaned_live_turns(boot_epoch=now_utc_naive())
    assert out == [], (
        "IDLE instance must NOT be swept — graph is at END; silent "
        "resume would re-execute the END node and create spurious "
        "wake noise (D13 false-positive (c))."
    )


def test_parked_parent_waiting_children_not_swept(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """Parked parent (WAITING_CHILDREN) MUST NOT be swept — bus owns the live parent."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="waiting_children")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)
    out = task_repo.find_orphaned_live_turns(boot_epoch=now_utc_naive())
    assert out == [], (
        "WAITING_CHILDREN parent must NOT be swept — DependencyBus "
        "owns the live parent; the boot sweep would duplicate the "
        "child-report notification path (D13 false-positive (c))."
    )


def test_instance_with_active_task_not_swept(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """An instance WITH an active task row (running/pending/paused) is the standard AutoContinue class, not this pass."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)
    # Add an active RUNNING task
    with Session(engine) as s:
        s.add(
            Task(
                work_id=f"work-active-{uuid4().hex[:12]}",
                task_type="process_message",
                instance_id=iid,
                status=TaskStatus.RUNNING.value,
                created_at=now_utc_naive(),
            )
        )
        s.commit()
    out = task_repo.find_orphaned_live_turns(boot_epoch=now_utc_naive())
    assert out == [], (
        "Active task row blocks the task-less selector — that class "
        "is owned by the companion find_auto_continue_candidates."
    )


def test_instance_without_active_jobitem_not_swept(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """Without an active JobItem, the orphan has no observable Job surface."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    # No JobItem
    out = task_repo.find_orphaned_live_turns(boot_epoch=now_utc_naive())
    assert out == []


def test_completed_latest_task_not_swept(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """B1 — instance whose LATEST task row is terminal-COMPLETED is EXCLUDED.

    This is the finalize-gap class: the turn genuinely completed, but
    the paired JobItem finalize did not land before the crash. The
    sweep must NOT resume such an instance (it would astream from a
    terminal checkpoint, violating the D1 doctrine).

    FLIPPED from the original ``test_completed_task_does_not_block_selector``
    (B1 review advisory, 2026-10-19) — the old test pinned the WRONG
    behavior (COMPLETED history does not block) because the original
    selector had only the active-task NOT-IN clause; the finalize-gap
    crash window makes COMPLETED-latest a real orphan-mimicking state
    that must be excluded.

    MUTATION-VERIFY: without the B1 NOT-EXISTS clause this test
    FAILS (the instance IS returned); with the clause it PASSES
    (the instance is excluded). See the pasted RED/GREEN outputs in
    the report.
    """
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)
    _seed_completed_task(engine, iid)
    out = task_repo.find_orphaned_live_turns(boot_epoch=now_utc_naive())
    assert out == [], (
        "Latest-task-COMPLETED instance must NOT be swept — the turn "
        "genuinely completed; resuming would astream from a terminal "
        "checkpoint (B1 / D1 doctrine)."
    )


def test_cancelled_latest_task_not_swept(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """CANCELLED latest-task instances are EXCLUDED — deliberate stop, not a crash rescue."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)
    # Seed a CANCELLED latest task
    with Session(engine) as s:
        s.add(
            Task(
                work_id=f"work-cancelled-{uuid4().hex[:12]}",
                task_type="process_message",
                instance_id=iid,
                status=TaskStatus.CANCELLED.value,
                created_at=now_utc_naive(),
            )
        )
        s.commit()
    out = task_repo.find_orphaned_live_turns(boot_epoch=now_utc_naive())
    assert out == [], (
        "Latest-task-CANCELLED instance must NOT be swept — the "
        "cancel was a deliberate stop; resuming would override it."
    )


def test_failed_latest_task_still_resumable(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """FAILED latest-task instances REMAIN in the sweep — crash-rescue semantics.

    A failed latest task means the turn died mid-flight; the Task axis
    says 'give it another shot' via astream(None) from the surviving
    checkpoint. This is the rescue semantic the companion
    find_auto_continue_candidates pass applies to surviving running
    task rows.
    """
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)
    # Seed a FAILED latest task
    with Session(engine) as s:
        s.add(
            Task(
                work_id=f"work-failed-{uuid4().hex[:12]}",
                task_type="process_message",
                instance_id=iid,
                status=TaskStatus.FAILED.value,
                created_at=now_utc_naive(),
            )
        )
        s.commit()
    out = task_repo.find_orphaned_live_turns(boot_epoch=now_utc_naive())
    assert out == [iid], (
        "Latest-task-FAILED instance must REMAIN swept — failed turn "
        "is the crash-rescue case; the checkpoint is still alive and "
        "the resume retry is legitimate."
    )


def test_latest_task_tiebreak_completed_with_older_failed(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """Latest-per-group tiebreak: FAILED (older) + COMPLETED (newer) → COMPLETED wins → excluded."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)
    import time as _time_mod
    # Older FAILED task
    with Session(engine) as s:
        s.add(
            Task(
                work_id=f"work-old-{uuid4().hex[:12]}",
                task_type="process_message",
                instance_id=iid,
                status=TaskStatus.FAILED.value,
                created_at=now_utc_naive(),
            )
        )
        s.commit()
    _time_mod.sleep(0.01)
    # Newer COMPLETED task (the LATEST)
    _seed_completed_task(engine, iid)
    out = task_repo.find_orphaned_live_turns(boot_epoch=now_utc_naive())
    assert out == [], (
        "LATEST task is COMPLETED (the FAILED one is older history) — "
        "the instance must be excluded by the B1 clause."
    )


def test_latest_task_tiebreak_failed_with_older_completed(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """Latest-per-group tiebreak: COMPLETED (older) + FAILED (newer) → FAILED wins → swept (rescue)."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)
    import time as _time_mod
    # Older COMPLETED task
    _seed_completed_task(engine, iid)
    _time_mod.sleep(0.01)
    # Newer FAILED task (the LATEST)
    with Session(engine) as s:
        s.add(
            Task(
                work_id=f"work-new-{uuid4().hex[:12]}",
                task_type="process_message",
                instance_id=iid,
                status=TaskStatus.FAILED.value,
                created_at=now_utc_naive(),
            )
        )
        s.commit()
    out = task_repo.find_orphaned_live_turns(boot_epoch=now_utc_naive())
    assert out == [iid], (
        "LATEST task is FAILED (the COMPLETED one is older history) — "
        "the instance REMAINS swept (crash-rescue semantics)."
    )


def test_terminal_status_not_swept(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """Terminal instance statuses (COMPLETED, FAILED, etc.) are NOT candidates."""
    for status in ("completed", "failed", "terminated", "error", "paused"):
        iid = f"i-{uuid4().hex[:8]}"
        _seed_instance(engine, iid, status=status)
        _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)
    out = task_repo.find_orphaned_live_turns(boot_epoch=now_utc_naive())
    assert out == [], (
        f"Terminal / paused instances must never be swept; got {out}"
    )


def test_done_jobitem_does_not_satisfy_active_clause(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """JobItem with DONE/DEAD admission_state does NOT count as 'active'."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.DONE.value)
    out = task_repo.find_orphaned_live_turns(boot_epoch=now_utc_naive())
    assert out == []


def test_multiple_orphans_returned_oldest_first(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """Multiple orphans → all returned, oldest-first by ``instances.created_at``."""
    import time as _time_mod
    iids = []
    for i in range(3):
        iid = f"i-{uuid4().hex[:8]}"
        iids.append(iid)
        _seed_instance(engine, iid, status="running")
        _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)
        _time_mod.sleep(0.01)  # ensure distinct created_at
    out = task_repo.find_orphaned_live_turns(boot_epoch=now_utc_naive())
    assert out == iids  # oldest-first by created_at


# ---------------------------------------------------------------------------
# Pass tests — checkpoint-based filtering + scheduling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orphan_swept_and_resumed_when_ckpt_stale(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """The flagship case — orphan + stale checkpoint → swept."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)

    boot_epoch = now_utc_naive()
    ckpt_ts = boot_epoch - timedelta(minutes=5)  # PRIOR-generation checkpoint
    mgr = _MockManager(task_repo)
    mgr.checkpointer.raw_saver.last_writes[iid] = ckpt_ts
    set_boot_epoch(boot_epoch)

    result = await pass_mod.sweep_orphaned_live_turns(mgr, boot_epoch)

    assert result.candidates == 1
    assert result.scheduled == 1
    assert result.errors == 0
    assert len(mgr.resume_calls) == 1
    assert mgr.resume_calls[0]["instance_id"] == iid
    assert mgr.resume_calls[0]["silent"] is True
    assert mgr.resume_calls[0]["route_outcome"] == "boot_continue_orphan_sweep"


@pytest.mark.asyncio
async def test_natural_immunity_orphan_not_reswept_in_same_generation(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """Same-generation re-sweep: ckpt_ts >= boot_epoch → skipped (natural immunity)."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)

    boot_epoch = now_utc_naive()
    ckpt_ts = boot_epoch + timedelta(seconds=30)  # current-generation ckpt
    mgr = _MockManager(task_repo)
    mgr.checkpointer.raw_saver.last_writes[iid] = ckpt_ts
    set_boot_epoch(boot_epoch)

    result = await pass_mod.sweep_orphaned_live_turns(mgr, boot_epoch)

    assert result.candidates == 1
    assert result.scheduled == 0
    assert result.skipped_resume_refused == 1
    assert len(mgr.resume_calls) == 0


@pytest.mark.asyncio
async def test_orphan_skipped_when_no_checkpoint(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """No checkpoint → SKIPPED (StaleTaskRecovery backstop eventually owns it)."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)

    boot_epoch = now_utc_naive()
    mgr = _MockManager(task_repo)
    # No checkpoint — leave last_writes entry empty
    set_boot_epoch(boot_epoch)

    result = await pass_mod.sweep_orphaned_live_turns(mgr, boot_epoch)

    assert result.candidates == 1
    assert result.scheduled == 0
    assert result.skipped_no_checkpoint == 1
    assert len(mgr.resume_calls) == 0


@pytest.mark.asyncio
async def test_parked_parent_pass_not_invoked(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """WC parent — the selector excludes it; the pass has nothing to do."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="waiting_children")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)

    boot_epoch = now_utc_naive()
    mgr = _MockManager(task_repo)
    set_boot_epoch(boot_epoch)

    result = await pass_mod.sweep_orphaned_live_turns(mgr, boot_epoch)

    assert result.candidates == 0
    assert result.scheduled == 0
    assert len(mgr.resume_calls) == 0


@pytest.mark.asyncio
async def test_idle_instance_pass_not_invoked(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """IDLE instance — selector excludes it; pass has nothing to do."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="idle")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)

    boot_epoch = now_utc_naive()
    mgr = _MockManager(task_repo)
    set_boot_epoch(boot_epoch)

    result = await pass_mod.sweep_orphaned_live_turns(mgr, boot_epoch)

    assert result.candidates == 0
    assert result.scheduled == 0
    assert len(mgr.resume_calls) == 0


@pytest.mark.asyncio
async def test_kill_switch_short_circuits(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART=0 → pass returns 0-counters."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)

    boot_epoch = now_utc_naive()
    mgr = _MockManager(task_repo)
    mgr.checkpointer.raw_saver.last_writes[iid] = boot_epoch - timedelta(minutes=5)
    set_boot_epoch(boot_epoch)

    with _env(pass_mod.AUTO_CONTINUE_KILL_SWITCH_ENV, "0"):
        result = await pass_mod.sweep_orphaned_live_turns(mgr, boot_epoch)

    assert result.skipped_kill_switch == 1
    assert result.scheduled == 0
    assert result.candidates == 0
    assert len(mgr.resume_calls) == 0


@pytest.mark.asyncio
async def test_boot_epoch_none_short_circuits(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """boot_epoch=None → SKIPPED."""
    boot_epoch = None
    set_boot_epoch(boot_epoch)
    mgr = _MockManager(task_repo)
    result = await pass_mod.sweep_orphaned_live_turns(mgr, boot_epoch)  # type: ignore[arg-type]
    assert result.skipped_no_boot_epoch == 1


@pytest.mark.asyncio
async def test_resume_refused_does_not_increment_scheduled(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """Resume returns non-'resuming' (e.g. already_resuming dedup) → SKIPPED, NOT scheduled."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)

    boot_epoch = now_utc_naive()
    mgr = _MockManager(task_repo)
    mgr.checkpointer.raw_saver.last_writes[iid] = boot_epoch - timedelta(minutes=5)
    mgr.resume_returns[iid] = {"status": "already_resuming"}
    set_boot_epoch(boot_epoch)

    result = await pass_mod.sweep_orphaned_live_turns(mgr, boot_epoch)

    assert result.candidates == 1
    assert result.scheduled == 0
    assert result.skipped_resume_refused == 1


@pytest.mark.asyncio
async def test_per_instance_exception_isolated(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """One instance's exception does NOT abort the sweep — Layer 1 isolation."""
    # Two orphans: first will raise in resume, second should be swept.
    iid_a = f"i-a-{uuid4().hex[:8]}"
    iid_b = f"i-b-{uuid4().hex[:8]}"
    for iid in (iid_a, iid_b):
        _seed_instance(engine, iid, status="running")
        _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)

    boot_epoch = now_utc_naive()
    mgr = _MockManager(task_repo)
    mgr.checkpointer.raw_saver.last_writes[iid_a] = boot_epoch - timedelta(minutes=5)
    mgr.checkpointer.raw_saver.last_writes[iid_b] = boot_epoch - timedelta(minutes=5)

    # Override resume to raise for iid_a only.
    original_resume = mgr._schedule_explicit_handle_resume

    async def selective_resume(**kwargs):
        if kwargs.get("instance_id") == iid_a:
            raise RuntimeError("synthetic resume failure")
        return await original_resume(**kwargs)

    mgr._schedule_explicit_handle_resume = selective_resume  # type: ignore[method-assign]
    set_boot_epoch(boot_epoch)

    result = await pass_mod.sweep_orphaned_live_turns(mgr, boot_epoch)

    assert result.candidates == 2
    assert result.scheduled == 1  # iid_b succeeded
    assert result.errors == 1  # iid_a failed


@pytest.mark.asyncio
async def test_inloop_task_recheck_skips_toctou_winner(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """A#2 (2026-10-19 review advisory) — in-loop task re-check closes the SELECT→schedule TOCTOU.

    The selector initially sees the instance as task-less; a concurrent
    actor mints a fresh RUNNING task row for the same instance before
    the pass reaches the scheduling step. The re-check catches this and
    SKIPS the instance (defense-in-depth; the companion pass owns it).
    """
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)

    boot_epoch = now_utc_naive()
    mgr = _MockManager(task_repo)
    mgr.checkpointer.raw_saver.last_writes[iid] = boot_epoch - timedelta(minutes=5)

    # Simulate the TOCTOU race: between SELECT and schedule, a
    # concurrent actor mints a RUNNING task row.
    original_resume = mgr._schedule_explicit_handle_resume

    async def racing_resume(**kwargs):
        # Mint the racing task row DURING the resume call (simulating
        # the concurrent actor acting between the SELECT and schedule).
        with Session(engine) as s:
            s.add(
                Task(
                    work_id=f"work-raced-{uuid4().hex[:12]}",
                    task_type="process_message",
                    instance_id=iid,
                    status=TaskStatus.RUNNING.value,
                    created_at=now_utc_naive(),
                )
            )
            s.commit()
        # The re-check in the pass runs BEFORE this resume, so the
        # racing task is minted too late to be caught by the re-check.
        # To make this test actually pin the re-check, we instead mint
        # the task BEFORE the sweep runs — but that would make the
        # SELECTOR exclude the instance too. So the simulation here is:
        # mint the task in a hook that fires BEFORE the re-check.
        return await original_resume(**kwargs)

    # Simpler approach: mint the task AFTER the SELECT (which happens
    # at sweep_orphaned_live_turns entry) but BEFORE the per-instance
    # re-check. The re-check runs inside the per-instance loop, so we
    # monkey-patch the repo's has_instance_busy to mint a task when
    # called for the first time.
    _original_has_busy = task_repo.has_instance_busy
    _minted = {"done": False}

    def minting_has_busy(iid_: str) -> bool:
        if iid_ == iid and not _minted["done"]:
            # Mint the racing task row the FIRST time the re-check is
            # called for this instance — simulates the concurrent actor
            # acting between SELECT and the re-check.
            _minted["done"] = True
            with Session(engine) as s:
                s.add(
                    Task(
                        work_id=f"work-raced-{uuid4().hex[:12]}",
                        task_type="process_message",
                        instance_id=iid,
                        status=TaskStatus.RUNNING.value,
                        created_at=now_utc_naive(),
                    )
                )
                s.commit()
        return _original_has_busy(iid_)

    task_repo.has_instance_busy = minting_has_busy  # type: ignore[method-assign]
    set_boot_epoch(boot_epoch)

    result = await pass_mod.sweep_orphaned_live_turns(mgr, boot_epoch)

    assert result.candidates == 1  # the selector matched before the re-check
    assert result.scheduled == 0  # the re-check skipped it
    assert result.skipped_resume_refused == 1  # counted as TOCTOU skip
    assert len(mgr.resume_calls) == 0  # no resume dispatched


@pytest.mark.asyncio
async def test_inloop_task_recheck_noop_when_still_taskless(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """A#2 re-check is a no-op when the instance is still task-less at schedule time."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)

    boot_epoch = now_utc_naive()
    mgr = _MockManager(task_repo)
    mgr.checkpointer.raw_saver.last_writes[iid] = boot_epoch - timedelta(minutes=5)
    set_boot_epoch(boot_epoch)

    result = await pass_mod.sweep_orphaned_live_turns(mgr, boot_epoch)

    # No concurrent actor: the re-check passes through, resume proceeds.
    assert result.candidates == 1
    assert result.scheduled == 1
    assert result.skipped_resume_refused == 0
    assert len(mgr.resume_calls) == 1


@pytest.mark.asyncio
async def test_summary_line_emitted(
    task_repo: TaskRepository, engine: Engine, caplog
) -> None:
    """The summary line is always emitted (even when N=0)."""
    boot_epoch = now_utc_naive()
    set_boot_epoch(boot_epoch)
    mgr = _MockManager(task_repo)

    with caplog.at_level("INFO", logger=pass_mod.logger.name):
        result = await pass_mod.sweep_orphaned_live_turns(mgr, boot_epoch)

    summary_lines = [
        r.getMessage() for r in caplog.records
        if "orphan-sweep summary" in r.getMessage()
    ]
    assert len(summary_lines) == 1, (
        "Summary line MUST always emit (one per boot) — even on N=0; "
        f"got {len(summary_lines)}"
    )
    assert "boot_epoch=" in summary_lines[0]


@pytest.mark.asyncio
async def test_multiple_orphans_all_swept(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """Multiple orphans → all swept (when all have stale checkpoints)."""
    iids = []
    for i in range(4):
        iid = f"i-{uuid4().hex[:8]}"
        iids.append(iid)
        _seed_instance(engine, iid, status="running")
        _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)

    boot_epoch = now_utc_naive()
    mgr = _MockManager(task_repo)
    for iid in iids:
        mgr.checkpointer.raw_saver.last_writes[iid] = boot_epoch - timedelta(minutes=5)
    set_boot_epoch(boot_epoch)

    result = await pass_mod.sweep_orphaned_live_turns(mgr, boot_epoch)

    assert result.candidates == 4
    assert result.scheduled == 4
    assert result.errors == 0
    resumed_ids = {c["instance_id"] for c in mgr.resume_calls}
    assert resumed_ids == set(iids)


@pytest.mark.asyncio
async def test_repeat_boot_same_generation_no_resweep(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """Second boot in SAME generation: ckpt_ts >= boot_epoch → NOT swept."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)

    boot_epoch = now_utc_naive()

    # First boot: stale checkpoint → swept
    mgr1 = _MockManager(task_repo)
    mgr1.checkpointer.raw_saver.last_writes[iid] = boot_epoch - timedelta(minutes=5)
    set_boot_epoch(boot_epoch)
    result1 = await pass_mod.sweep_orphaned_live_turns(mgr1, boot_epoch)
    assert result1.scheduled == 1

    # Second boot SAME generation: ckpt has been written by the first
    # boot's resume, so ckpt_ts is now AFTER boot_epoch → natural immunity.
    new_ckpt_ts = boot_epoch + timedelta(seconds=1)
    mgr2 = _MockManager(task_repo)
    mgr2.checkpointer.raw_saver.last_writes[iid] = new_ckpt_ts
    result2 = await pass_mod.sweep_orphaned_live_turns(mgr2, boot_epoch)
    assert result2.candidates == 1  # still matches the selector
    assert result2.scheduled == 0
    assert result2.skipped_resume_refused == 1  # natural immunity


@pytest.mark.asyncio
async def test_new_restart_sweeps_fresh_orphan(
    task_repo: TaskRepository, engine: Engine
) -> None:
    """A NEW restart produces a fresh boot_epoch; a new orphan from a
    later restart with a pre-restart checkpoint IS swept."""
    iid = f"i-{uuid4().hex[:8]}"
    _seed_instance(engine, iid, status="running")
    _seed_jobitem(engine, iid, admission_state=AdmissionState.ACTIVE.value)

    boot_epoch_1 = now_utc_naive() - timedelta(hours=1)
    boot_epoch_2 = now_utc_naive()  # fresh boot
    # ckpt was written by a turn that ran between boot_epoch_1 and boot_epoch_2
    ckpt_ts = boot_epoch_1 + timedelta(minutes=30)

    mgr = _MockManager(task_repo)
    mgr.checkpointer.raw_saver.last_writes[iid] = ckpt_ts
    set_boot_epoch(boot_epoch_2)

    result = await pass_mod.sweep_orphaned_live_turns(mgr, boot_epoch_2)

    assert result.candidates == 1
    assert result.scheduled == 1
    assert ckpt_ts < boot_epoch_2  # sanity: ckpt predates new boot epoch