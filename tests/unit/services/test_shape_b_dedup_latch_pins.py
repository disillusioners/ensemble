"""Shape-B dedup-latch pins (fix/post-outage-recovery SCOPE b2, F1).

Adjudicated wedge 6105e198: the scheduler's ONE-TIME idempotency key was
CONSTANT for the schedule's lifetime
(``scheduler:{source_id}:{run_at.isoformat()}``). On a post-outage re-fire
the substrate found a PRE-EXISTING JobItem for that key and logged the dedup
hit — yet the D4 compensation in ``enqueue_message_job`` did NOT run and the
phantom prelude pair (Task PENDING + MessageQueue ready) sat wedged for
11m19s.

ROOT CAUSE (pinned here at the real repository/SQL seam): the JobQueueService
terminal/TTL fallback rewrites the idempotency key with a synthetic suffix
and returns a FRESH row carrying the CALLER's minted ``job_id`` — so the
compensation predicate ``returned.job_id != minted`` evaluates False despite
the logged hit (the "masquerade").

Pins (claim-asserting — every pin asserts a row-level outcome, never
"no error appeared"):
* P1  the masquerade is reproduced against the REAL ``INSERT .. ON CONFLICT``
  claim path; the post-F1(a) per-execution keys mint DISTINCT keys per fire;
  the post-fire row shape claims through the REAL ``claim_pending_task`` SQL
  with no human intervention.
* P4  a forced dedup hit (genuine AND masquerade arms) runs the D4
  compensation: the phantom prelude Task is CANCELLED and the phantom
  MessageQueue row is DELETED (reconciled), with the compensation marker
  observed.

Hermetic SQLite (file-backed, NullPool, REAL SQLModel schema) — no live
daemon, no live DB.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
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
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.event.models import Event
from daemon.repositories.instance.models import Instance
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository as MessageQueueRepository,
)
from daemon.repositories.task.models import Task
from daemon.repositories.task.repository import TaskRepository

SCHED_KEY = "scheduler:sched-1:2026-11-23T19:00:00+00:00"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    eng = create_engine(
        f"sqlite:///{tmp_path}/shape_b_dedup.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def task_repo(engine: Engine) -> TaskRepository:
    return TaskRepository(engine)


@pytest.fixture
def mq_repo(engine: Engine) -> MessageQueueRepository:
    return MessageQueueRepository(engine)


def _seed_job(
    engine: Engine,
    *,
    job_id: str,
    idempotency_key: str | None,
    admission_state: str,
    source: str = "scheduler",
) -> JobItem:
    with Session(engine) as s:
        job = JobItem(
            job_id=job_id,
            agent_id="ari",
            agent_dir="/agents/ari",
            message="weekly report",
            source=source,
            idempotency_key=idempotency_key,
            admission_state=admission_state,
            job_type="message",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        return job


def _seed_instance(engine: Engine, iid: str = "inst-1") -> None:
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id="ari",
                agent_dir="/agents/ari",
                status="running",
            )
        )
        s.commit()


def _seed_prelude_pair(
    engine: Engine,
    *,
    task_repo: TaskRepository,
    mq_repo: MessageQueueRepository,
    work_id: str,
    message_id: str,
    instance_id: str = "inst-1",
) -> tuple[int, str]:
    """Mint the D13 prelude pair the way ``_prepare_enqueued_message`` does:
    Task PENDING (work_id-bound) + MessageQueue ready. NO JobItem — exactly
    the adjudicated phantom shape."""
    with Session(engine) as s:
        task = Task(
            work_id=work_id,
            task_type="process_message",
            instance_id=instance_id,
            message_id=message_id,
            status="pending",
        )
        s.add(task)
        s.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content="weekly report",
                source="scheduler",
                status="ready",
            )
        )
        s.commit()
        s.refresh(task)
        return task.id, message_id


def _get_task(engine: Engine, task_id: int) -> Task | None:
    with Session(engine) as s:
        return s.get(Task, task_id)


def _get_mq(engine: Engine, message_id: str) -> MessageQueue | None:
    with Session(engine) as s:
        return s.exec(
            select(MessageQueue).where(MessageQueue.message_id == message_id)
        ).first()


# ---------------------------------------------------------------------------
# P1 — the masquerade root cause, at the REAL service claim path
# ---------------------------------------------------------------------------


class _FakeAgentMeta:
    path = "/agents/ari"


class _FakeRegistry:
    def get_version(self, agent_id, tag=None):
        return _FakeAgentMeta()

    def get_resolved(self, agent_id):
        return _FakeAgentMeta()


@pytest.fixture
def job_service(engine: Engine, monkeypatch, mq_repo):
    """A REAL JobQueueService on hermetic SQLite (registry stubbed — the
    service's agent_dir resolution is disk-coupled, everything else is
    real SQL)."""
    import daemon.services.job_queue_service as jq_mod
    from daemon.repositories.job_queue.lock_repository import LockRepository
    from daemon.repositories.job_queue.queue_repository import (
        JobQueueRepository as _JQRepo,
    )
    from daemon.services.job_lock_manager import JobLockManager

    monkeypatch.setattr(jq_mod, "get_registry", lambda: _FakeRegistry())
    lock_repo = LockRepository(engine)
    svc = jq_mod.JobQueueService(
        repository=JobRepository(engine),
        lock_manager=JobLockManager(lock_repo),
        queue_repo=_JQRepo(engine),
        instance_manager=None,
    )
    # Seed the message default queue so the enqueue's queue lookup resolves
    # — under the NORMALIZED system default project id (uuid5-derived).
    from daemon.constants import SYSTEM_DEFAULT_PROJECT_ID
    from daemon.repositories.job_queue.models import JobQueue

    with Session(engine) as s:
        s.add(
            JobQueue(
                project_id=SYSTEM_DEFAULT_PROJECT_ID,
                queue_name="system_parallel_queue",
                queue_name_lower="system_parallel_queue",
                queue_type="parallel",
            )
        )
        s.commit()
    return svc


@pytest.mark.asyncio
async def test_p1_masquerade_root_cause_dedup_predicate_blind(
    engine: Engine, job_service
) -> None:
    """REPRO (adjudicated 6105e198): a pre-existing TERMINAL JobItem holds
    the constant scheduler key. The service logs the dedup hit, takes the
    terminal fallback, and returns a FRESH row carrying the CALLER's minted
    job_id with a REWRITTEN key — so the compensation predicate
    ``returned.job_id != minted`` evaluates False and the phantom prelude
    survives. Root cause pinned at the REAL service seam."""
    import logging as _logging

    minted = str(uuid4())
    _seed_job(
        engine,
        job_id=str(uuid4()),
        idempotency_key=SCHED_KEY,
        admission_state=AdmissionState.DONE.value,
    )

    import daemon.services.job_queue_service as jq_mod

    job = await job_service.enqueue(
        agent_id="ari",
        message="weekly report",
        source="scheduler",
        project_id=None,
        priority=5,
        metadata={},
        queue_id=None,
        job_type="message",
        instance_id="inst-1",
        job_id=minted,
        idempotency_key=SCHED_KEY,
    )

    assert job is not None
    assert job.job_id == minted, (
        "terminal fallback must return the caller's fresh mint (the masquerade)"
    )
    assert job.idempotency_key != SCHED_KEY, (
        "terminal fallback rewrote the key with a synthetic suffix"
    )
    # And therefore the PRE-F1(b) compensation predicate is BLIND:
    pre_fix_dedup_hit = getattr(job, "job_id", None) != minted
    assert pre_fix_dedup_hit is False, (
        "the old dedup predicate must evaluate False on the masquerade — "
        "this is the adjudicated compensation skip"
    )


@pytest.mark.asyncio
async def test_p1_dedup_outcome_classifier_catches_masquerade(
    engine: Engine, job_service
) -> None:
    """F1(b): the widened classifier flags the masquerade as a key-claim
    collision even though the returned row carries the caller's own mint."""
    from daemon.services.instance_messaging import classify_dedup_outcome

    minted = str(uuid4())
    _seed_job(
        engine,
        job_id=str(uuid4()),
        idempotency_key=SCHED_KEY,
        admission_state=AdmissionState.DONE.value,
    )
    job = await job_service.enqueue(
        agent_id="ari",
        message="weekly report",
        source="scheduler",
        project_id=None,
        metadata={},
        job_type="message",
        instance_id="inst-1",
        job_id=minted,
        idempotency_key=SCHED_KEY,
    )
    genuine, masquerade = classify_dedup_outcome(
        supplied_key=SCHED_KEY, returned_job=job, local_job_id=minted
    )
    assert genuine is False
    assert masquerade is True, "the masquerade must be classified as a key collision"

    # The genuine arm stays exactly as before: a non-terminal winner returns
    # its OWN job_id (≠ mint) — genuine dedup, masquerade False.
    minted2 = str(uuid4())
    live_key = "scheduler:sched-1:exec:live-key"
    _seed_job(
        engine,
        job_id=str(uuid4()),
        idempotency_key=live_key,
        admission_state=AdmissionState.QUEUED.value,
    )
    job2 = await job_service.enqueue(
        agent_id="ari",
        message="weekly report",
        source="scheduler",
        project_id=None,
        metadata={},
        job_type="message",
        instance_id="inst-1",
        job_id=minted2,
        idempotency_key=live_key,
    )
    assert job2 is not None and job2.job_id != minted2, (
        "a non-terminal winner is returned as-is (genuine dedup shape)"
    )
    genuine2, masquerade2 = classify_dedup_outcome(
        supplied_key=live_key,
        returned_job=job2,
        local_job_id=minted2,
    )
    assert genuine2 is True and masquerade2 is False


def test_p1_scheduler_key_is_per_execution() -> None:
    """F1(a): each scheduler fire mints a DISTINCT idempotency key; the same
    execution re-mints the SAME key (the within-execution re-entry latch);
    the deferred twin is distinct per generation and stable within it, and
    no longer collides with the direct key."""
    from daemon.sources.adapters.scheduler import (
        _deferred_idempotency_key,
        _scheduler_direct_idempotency_key,
    )

    sid = "db-volume-weekly"
    k1 = _scheduler_direct_idempotency_key(sid, "exec-1")
    k2 = _scheduler_direct_idempotency_key(sid, "exec-2")
    k1_again = _scheduler_direct_idempotency_key(sid, "exec-1")
    assert k1 != k2, "two fires of the SAME schedule must mint distinct keys"
    assert k1 == k1_again, "one execution must be a stable latch for its own re-entry"
    assert k1.startswith(f"scheduler:{sid}:")

    d_gen1_a = _deferred_idempotency_key(sid, "run-at", 1)
    d_gen1_b = _deferred_idempotency_key(sid, "run-at", 1)
    d_gen2 = _deferred_idempotency_key(sid, "run-at", 2)
    assert d_gen1_a == d_gen1_b, "the deferred key is the per-generation coalesce latch"
    assert d_gen1_a != d_gen2, "a rollover must mint a fresh deferred key"
    assert d_gen1_a != k1, "the deferred twin must never collide with the direct key"


def test_p1_scheduler_route_keys_all_fires() -> None:
    """F1(a): the job-queue route mints the per-execution key for ALL
    scheduled fires (the ONE_TIME-only gate is gone — the weekly chain and
    every recurring fire are covered)."""
    import inspect

    from daemon.sources.adapters import scheduler as sched_mod

    src = inspect.getsource(sched_mod.SchedulerAdapter._route_via_job_queue)
    assert "_scheduler_direct_idempotency_key(" in src, (
        "the route must mint the per-execution key"
    )
    assert "SCHEDULE_TYPE_ONE_TIME and self._run_at is not None:\n                enqueue_kwargs" not in src, (
        "the ONE_TIME-only constant-key gate must be retired"
    )


@pytest.mark.asyncio
async def test_p1_post_fire_row_claims_without_intervention(
    engine: Engine,
    task_repo: TaskRepository,
    mq_repo: MessageQueueRepository,
) -> None:
    """THE CLAIM (P1): after F1(a) the fire mints a fresh JobItem (no
    collision, no phantom) — the row shape a normal fire leaves is
    Task PENDING + JobItem ACTIVE + MQ ready, and it reaches
    RUNNING/processing through the REAL claim SQL with no human
    intervention (no pause/resume, no operator repair)."""
    _seed_instance(engine, "inst-claim")
    work_id = str(uuid4())
    message_id = f"mq-{uuid4().hex[:12]}"
    task_id, message_id = _seed_prelude_pair(
        engine,
        task_repo=task_repo,
        mq_repo=mq_repo,
        work_id=work_id,
        message_id=message_id,
        instance_id="inst-claim",
    )
    # The fresh fire's JobItem — post-F1(a) it exists, bound to work_id, and
    # the job pipeline has ACTIVATED it (admission_state='active').
    _seed_job(
        engine,
        job_id=work_id,
        idempotency_key=_mk_exec_key("sched-claim", "exec-1"),
        admission_state=AdmissionState.ACTIVE.value,
    )

    claimed = task_repo.claim_pending_task(worker_id="worker-pin-1", lane="default")
    assert claimed is not None, "the post-fire row MUST be claimable without human repair"
    assert claimed.id == task_id
    assert claimed.status == "running"

    # The message left 'ready': the REAL dequeue claims it into processing.
    msg = mq_repo.dequeue(instance_id="inst-claim")
    assert msg is not None and msg.message_id == message_id
    assert msg.status == "processing"
    assert msg.processing_started_at is not None


def _mk_exec_key(source_id: str, execution_id: str) -> str:
    from daemon.sources.adapters.scheduler import (
        _scheduler_direct_idempotency_key,
    )

    return _scheduler_direct_idempotency_key(source_id, execution_id)


# ---------------------------------------------------------------------------
# P4 — the D4 compensation actually reconciles phantom prelude rows
# ---------------------------------------------------------------------------


class _RecordingJobCancel:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, job_id: str) -> bool:
        self.calls.append(job_id)
        return True


@pytest.mark.asyncio
async def test_p4_genuine_dedup_compensation_reconciles_phantom(
    engine: Engine,
    task_repo: TaskRepository,
    mq_repo: MessageQueueRepository,
    caplog,
) -> None:
    """P4: on a GENUINE dedup hit (pre-existing non-terminal winner returned)
    the compensation cancels the phantom Task and deletes the phantom MQ row
    — and the compensation marker is observable."""
    from daemon.services.instance_messaging import compensate_dedup_hit

    _seed_instance(engine, "inst-dedup")
    work_id_b = str(uuid4())
    message_id_b = f"mq-{uuid4().hex[:12]}"
    task_b_id, message_id_b = _seed_prelude_pair(
        engine,
        task_repo=task_repo,
        mq_repo=mq_repo,
        work_id=work_id_b,
        message_id=message_id_b,
        instance_id="inst-dedup",
    )
    real_job_id = str(uuid4())
    _seed_job(
        engine,
        job_id=real_job_id,
        idempotency_key="scheduler:sched-1:exec:exec-1",
        admission_state=AdmissionState.QUEUED.value,
    )
    cancel_stub = _RecordingJobCancel()

    with caplog.at_level(logging.INFO, logger="daemon.services.instance_messaging"):
        outcome = await compensate_dedup_hit(
            task_repo=task_repo,
            queue_repository=mq_repo,
            phantom_task_id=task_b_id,
            phantom_message_id=message_id_b,
            real_job_id=real_job_id,
            local_job_id=work_id_b,
            cancel_job_fn=cancel_stub,
            masquerade=False,
        )

    assert outcome["task_cancelled"] is True
    assert outcome["mq_deleted"] is True
    assert any(
        "dedup" in r.message.lower() and "compensat" in r.message.lower()
        for r in caplog.records
    ), "the compensation must emit its observable marker"
    task_b = _get_task(engine, task_b_id)
    assert task_b is not None and task_b.status == "cancelled", (
        "the phantom prelude Task must be CANCELLED — reconciled"
    )
    assert _get_mq(engine, message_id_b) is None, (
        "the phantom prelude MQ row must be deleted — reconciled"
    )


@pytest.mark.asyncio
async def test_p4_masquerade_scheduler_compensation_reconciles(
    engine: Engine,
    task_repo: TaskRepository,
    mq_repo: MessageQueueRepository,
    caplog,
) -> None:
    """P4 (masquerade arm): when the substrate rewrote the key (terminal
    fallback) the widened detector routes the SCHEDULER fire through the same
    compensation — the re-delivery is killed and the orphan fallback JobItem
    is cancelled. The at-most-once contract holds on BOTH arms."""
    from daemon.services.instance_messaging import compensate_dedup_hit

    _seed_instance(engine, "inst-masq")
    work_id_b = str(uuid4())
    message_id_b = f"mq-{uuid4().hex[:12]}"
    task_b_id, message_id_b = _seed_prelude_pair(
        engine,
        task_repo=task_repo,
        mq_repo=mq_repo,
        work_id=work_id_b,
        message_id=message_id_b,
        instance_id="inst-masq",
    )
    # The masquerade left a FRESH fallback JobItem bound to the caller's mint.
    _seed_job(
        engine,
        job_id=work_id_b,
        idempotency_key=f"{SCHED_KEY}#deadbeef",
        admission_state=AdmissionState.QUEUED.value,
    )
    cancel_stub = _RecordingJobCancel()

    with caplog.at_level(logging.INFO, logger="daemon.services.instance_messaging"):
        outcome = await compensate_dedup_hit(
            task_repo=task_repo,
            queue_repository=mq_repo,
            phantom_task_id=task_b_id,
            phantom_message_id=message_id_b,
            real_job_id=work_id_b,
            local_job_id=work_id_b,
            cancel_job_fn=cancel_stub,
            masquerade=True,
        )

    assert outcome["task_cancelled"] is True
    assert outcome["mq_deleted"] is True
    assert cancel_stub.calls == [work_id_b], (
        "the masquerade arm must also dispose the orphan fallback JobItem"
    )
    assert any("masquerad" in r.message.lower() for r in caplog.records), (
        "the masquerade arm must log its own truthful marker"
    )
    task_b = _get_task(engine, task_b_id)
    assert task_b is not None and task_b.status == "cancelled"
    assert _get_mq(engine, message_id_b) is None


@pytest.mark.asyncio
async def test_p4_wiring_routes_both_arms_through_compensation() -> None:
    """Structural F1(b)/F4: ``enqueue_message_job`` classifies BOTH arms and
    routes through ``compensate_dedup_hit`` — no bypass, no direct drive."""
    import inspect

    import daemon.services.instance_messaging as im

    src = inspect.getsource(im.InstanceMessagingService.enqueue_message_job)
    assert "classify_dedup_outcome(" in src
    assert "compensate_dedup_hit(" in src
