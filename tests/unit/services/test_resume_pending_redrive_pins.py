"""F2 no_active_job-repair pins (fix/post-outage-recovery SCOPE b2).

Adjudicated shape (6105e198 net): a LIVE RUNNING instance holds a PENDING
task anchored to a non-terminal JobItem, the operator pause/resume repair
path cannot see PENDING (``find_paused_or_cancellable_turn`` selects
paused/running only), and the verdict comes back ``no_active_job`` while the
task sits unclaimed.

Pins:
* P2  the resume path FINDS the shape and returns a truthful re-drive
  outcome (NOT None → ``no_active_job``); after it the task is claimable
  and IS claimed through the REAL ``claim_pending_task`` SQL.
* P5  cross-fix idempotency (F4): the re-drive NEVER claims — the claim
  UPDATE in ``claim_pending_task`` is the single consumer CAS; two
  competing claims yield exactly ONE RUNNING task; the re-drive is
  repeatable without duplicating consumption.
* F4  structural: no new code path writes a pending→running task flip
  outside ``claim_pending_task``.

Hermetic SQLite (file-backed, NullPool, REAL SQLModel schema) — no live
daemon, no live DB. The manager under test is a minimal real-class harness
(``InstanceManager.__new__`` + only the attrs the resume path touches).
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select

import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.repositories.task.models import Task
from daemon.repositories.task.repository import TaskRepository


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    eng = create_engine(
        f"sqlite:///{tmp_path}/resume_redrive.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def task_repo(engine: Engine) -> TaskRepository:
    return TaskRepository(engine)


@pytest.fixture
def report_repo(engine: Engine) -> ReportInjectionRepository:
    return ReportInjectionRepository(engine=engine)


def _seed_instance(engine: Engine, iid: str, status: str = "running") -> None:
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id="ari",
                agent_dir="/agents/ari",
                status=status,
            )
        )
        s.commit()


def _seed_wedge_shape(
    engine: Engine,
    iid: str = "inst-wedge",
    *,
    task_status: str = "pending",
    admission_state: str = AdmissionState.ACTIVE.value,
    next_retry_at: datetime | None = None,
    message_id: str | None = None,
) -> tuple[int, str]:
    """The adjudicated shape: PENDING task + non-terminal JobItem (+ ready
    MQ row) on a LIVE instance."""
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id="ari",
                agent_dir="/agents/ari",
                status="running",
            )
        )
        wid = f"w-{uuid4().hex[:12]}"
        mid = message_id or f"mq-{uuid4().hex[:12]}"
        s.add(
            Task(
                work_id=wid,
                task_type="process_message",
                instance_id=iid,
                message_id=mid,
                status=task_status,
                next_retry_at=next_retry_at,
            )
        )
        s.add(
            JobItem(
                job_id=wid,
                agent_id="ari",
                agent_dir="/agents/ari",
                message="weekly report",
                source="scheduler",
                admission_state=admission_state,
                job_type="message",
                instance_id=iid,
                idempotency_key=f"scheduler:sched-1:exec:{uuid4().hex[:8]}",
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        s.add(
            MessageQueue(
                message_id=mid,
                instance_id=iid,
                content="weekly report",
                source="scheduler",
                status="ready",
            )
        )
        s.commit()
        return _get_pending_task_id(engine, iid), wid


def _get_pending_task_id(engine: Engine, iid: str) -> int:
    with Session(engine) as s:
        row = s.exec(
            select(Task.id)
            .where(Task.instance_id == iid)
            .where(Task.status == "pending")
        ).first()
        assert row is not None
        return int(row)


def _get_task(engine: Engine, task_id: int) -> Task | None:
    with Session(engine) as s:
        return s.get(Task, task_id)


def _running_tasks(engine: Engine, iid: str) -> list[Task]:
    with Session(engine) as s:
        return list(
            s.exec(
                select(Task)
                .where(Task.instance_id == iid)
                .where(Task.status == "running")
            )
        )


class _RedriveHarness:
    """A minimal real-class manager: ``InstanceManager.__new__`` plus ONLY
    the attributes ``resume_processing_job`` touches on this path."""

    def __init__(self, engine: Engine) -> None:
        from daemon.manager import InstanceManager

        mgr = InstanceManager.__new__(InstanceManager)
        mgr._task_repo = TaskRepository(engine)
        mgr._report_injection_repo = ReportInjectionRepository(engine=engine)
        mgr._lifecycle_service = None
        mgr._job_queue_service = None  # no job pipeline in this harness
        self.mgr = mgr
        self.pool_wakes = 0

        # Fingerprint the pool wake seam (the F4 "wake, don't claim" proof).
        orig = mgr.__class__.__dict__.get("_notify_all_pools")

        def _counting_notify(inner_self=None, *a, **k):  # type: ignore[no-untyped-def]
            self.pool_wakes += 1
            if orig is not None:
                try:
                    return orig(inner_self, *a, **k)  # type: ignore[misc]
                except Exception:
                    return 0
            return 0

        object.__setattr__(mgr, "_notify_all_pools", _counting_notify.__get__(mgr))

    async def resume(self, instance_id: str, *, silent: bool = False) -> Any:
        return await self.mgr.resume_processing_job(
            instance_id, message="resume", silent=silent
        )


@pytest.fixture
def harness(engine: Engine) -> _RedriveHarness:
    return _RedriveHarness(engine)


# ---------------------------------------------------------------------------
# P2 — the resume path returns a truthful re-drive outcome
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p2_resume_returns_redrive_outcome_and_claim_happens(
    engine: Engine, harness: _RedriveHarness, task_repo: TaskRepository
) -> None:
    task_id, _wid = _seed_wedge_shape(engine, "inst-wedge")

    result = await harness.resume("inst-wedge", silent=False)

    assert result is not None, (
        "the wedge shape must NOT fall through to None → no_active_job"
    )
    assert result.get("status") == "pending_redrive", (
        f"expected a truthful re-drive verdict, got {result!r}"
    )
    assert result.get("task_id") == task_id

    # After the re-drive the task IS claimable — backoff cleared — and the
    # REAL claim SQL takes it (the single consumer CAS).
    row = _get_task(engine, task_id)
    assert row is not None and row.status == "pending"
    assert row.next_retry_at is None, "the re-drive must clear any claim-blocking backoff"
    claimed = task_repo.claim_pending_task(worker_id="worker-p2", lane="default")
    assert claimed is not None and claimed.id == task_id
    assert claimed.status == "running"
    # The re-drive woke the pool instead of claiming (F4).
    assert harness.pool_wakes >= 1


@pytest.mark.asyncio
async def test_p2_resume_clears_backoff_blocking_claim(
    engine: Engine, harness: _RedriveHarness, task_repo: TaskRepository
) -> None:
    """A future ``next_retry_at`` holds the claim gate (the ready-check);
    the re-drive must make the row claimable again."""
    # next_retry_at is a STRING column compared against the claim's
    # now_str digits — build the future stamp in the repo's own format.
    now = datetime.now(timezone.utc) + timedelta(minutes=30)
    future = now.strftime("%Y-%m-%dT%H:%M:%S.%f") + now.strftime("%z")
    task_id, _wid = _seed_wedge_shape(
        engine, "inst-backoff", next_retry_at=future
    )
    before = _get_task(engine, task_id)
    assert before is not None and before.next_retry_at is not None

    result = await harness.resume("inst-backoff", silent=False)
    assert result is not None and result.get("status") == "pending_redrive"

    claimed = task_repo.claim_pending_task(worker_id="worker-p2b", lane="default")
    assert claimed is not None and claimed.id == task_id, (
        "the re-drive must clear the backoff so the claim gate passes"
    )


@pytest.mark.asyncio
async def test_p2_queued_jobitem_shape_also_redrives(
    engine: Engine, harness: _RedriveHarness
) -> None:
    """A QUEUED (slot-denied) JobItem anchors the same wedge; the selector
    must find it (non-terminal = queued or active) and re-drive."""
    task_id, _wid = _seed_wedge_shape(
        engine, "inst-queued", admission_state=AdmissionState.QUEUED.value
    )
    result = await harness.resume("inst-queued", silent=False)
    assert result is not None and result.get("status") == "pending_redrive"
    assert result.get("task_id") == task_id


@pytest.mark.asyncio
async def test_p2_genuinely_orphan_instance_still_no_active_job(
    engine: Engine, harness: _RedriveHarness
) -> None:
    """The old verdict stays truthful for the shape it was written for: an
    instance with NO suspension handle and NO pending work still gets
    ``no_active_job`` (via None) — the fix adds a leg, it does not blur the
    contract."""
    _seed_instance(engine, "inst-idle")
    result = await harness.resume("inst-idle", silent=False)
    assert result is None, "no work → None (routers map to no_active_job) — unchanged"


@pytest.mark.asyncio
async def test_p2_terminal_jobitem_shape_not_redriven(
    engine: Engine, harness: _RedriveHarness
) -> None:
    """A PENDING task whose JobItem is TERMINAL (done) is NOT the wedge
    shape — the claim would strand (no live job drives it). No re-drive."""
    _seed_wedge_shape(
        engine, "inst-done", admission_state=AdmissionState.DONE.value
    )
    result = await harness.resume("inst-done", silent=False)
    assert result is None, "terminal-anchor pending work is not re-drivable via F2"


# ---------------------------------------------------------------------------
# P5 — cross-fix idempotency: exactly ONE claim transition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p5_redrive_never_claims_and_claim_cas_is_single(
    engine: Engine, harness: _RedriveHarness, task_repo: TaskRepository
) -> None:
    task_id, _wid = _seed_wedge_shape(engine, "inst-idem")

    # Re-drive TWICE — repeatable repair, still zero claims by the repair.
    first = await harness.resume("inst-idem", silent=False)
    second = await harness.resume("inst-idem", silent=False)
    assert first is not None and first.get("status") == "pending_redrive"
    assert second is not None and second.get("status") == "pending_redrive"
    assert _running_tasks(engine, "inst-idem") == [], (
        "the re-drive must NEVER claim — claimability + wake only (F4)"
    )

    # Two competing workers race the claim CAS: exactly ONE wins.
    w1 = task_repo.claim_pending_task(worker_id="worker-a", lane="default")
    w2 = task_repo.claim_pending_task(worker_id="worker-b", lane="default")
    winners = [w for w in (w1, w2) if w is not None]
    assert len(winners) == 1, "the claim UPDATE is the single consumer CAS"
    assert winners[0].id == task_id
    assert len(_running_tasks(engine, "inst-idem")) == 1

    # After consumption the F2 selector sees NO pending row (the task is
    # RUNNING) — the re-drive seam is out of the picture; the running task
    # routes through the PRE-EXISTING pause-cascade selector (out of F2
    # scope). Idempotency: still exactly one RUNNING task, nothing revived.
    assert task_repo.find_pending_redrive_turn("inst-idem") is None
    assert len(_running_tasks(engine, "inst-idem")) == 1


@pytest.mark.asyncio
async def test_p5_structural_single_pending_running_cas() -> None:
    """F4 structural: the pending→running flip exists ONLY inside
    ``claim_pending_task`` — the reconciler, the F2 re-drive and the F1
    compensation never direct-drive a task."""
    import pathlib

    repo_src = (
        pathlib.Path("daemon/repositories/task/repository.py")
        .read_text(encoding="utf-8")
    )
    claim_fn_src = inspect.getsource(TaskRepository.claim_pending_task)
    # The single CAS lives in the claim path.
    assert "SET status = :status_running" in claim_fn_src
    # And nowhere else in the repository module.
    outside = repo_src.replace(claim_fn_src, "")
    assert "SET status = :status_running" not in outside, (
        "a second pending→running UPDATE appeared outside claim_pending_task"
    )
    # The new F1/F2 seams carry no task-status writes at all.
    comp_src = inspect.getsource(
        __import__(
            "daemon.services.instance_messaging", fromlist=["compensate_dedup_hit"]
        ).compensate_dedup_hit
    )
    assert "SET status" not in comp_src, "compensation must not write task status"
    reconciler_src = pathlib.Path(
        "daemon/services/boot_consistency_reconciler.py"
    ).read_text(encoding="utf-8")
    assert "UPDATE task" not in reconciler_src
    assert "SET status" not in reconciler_src
