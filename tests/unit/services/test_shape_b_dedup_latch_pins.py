"""Shape-B dedup-latch pins (fix/post-outage-recovery SCOPE b2, R1/R2).

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

R1 (verifier REJECTED F1(a)): the PER-EXECUTION key
(``scheduler:{sid}:exec:{execution_id}``) removed the cross-restart
at-most-once guard for one-shot/chain fires — the chain re-arm /
source-disable write commits AFTER the delivery, so a crash in that window
replays the SAME slot and a fresh ``execution_id`` minted a FRESH key, i.e. a
SECOND delivery. The direct key is SLOT-STABLE and namespaced again:
``scheduler:{source_id}:slot:{run_at.isoformat()}``.

Pins (claim-asserting — every pin asserts a row-level outcome, never
"no error appeared"):
* P1  the masquerade is reproduced against the REAL ``INSERT .. ON CONFLICT``
  claim path; the post-fire row shape claims through the REAL
  ``claim_pending_task`` SQL with no human intervention.
* P1A (R5a) crash-window double-fire: slot-stable key + LIVE prior + two
  fires ⇒ exactly ONE JobItem. Driven END-TO-END — the key is minted by the
  REAL scheduler route (``_route_via_job_queue``), not hand-seeded (R5f).
* P1B (R2/R5b) DONE-prior reconciliation: slot key + DONE prior + two fires
  ⇒ exactly ONE DELIVERED chain (the orphan fallback JobItem cancelled by
  the F1(b) compensation). This is BOTH the old-bug repro and the remedy
  proof — under the per-execution key it was 2 rows (a second live chain).
* P4  a forced dedup hit (genuine AND masquerade arms) runs the D4
  compensation: the phantom prelude Task is CANCELLED and the phantom
  MessageQueue row is DELETED.
* P4E (R5e) BEHAVIORAL replacement for the string-presence wiring pin: a
  masquerade through the REAL ``enqueue_message_job`` fires the
  compensation, and a DEFERRED key does NOT (DEC-18 pinned behaviorally).

Hermetic SQLite (file-backed, NullPool, REAL SQLModel schema) — no live
daemon, no live DB.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
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

RUN_AT_ISO = "2026-11-23T19:00:00+00:00"
SCHED_KEY = "scheduler:sched-1:slot:" + RUN_AT_ISO
#: DEC-18: the deferred twin for the same schedule/generation — namespaced
#: away from the direct slot key, and NEVER compensated.
DEFERRED_KEY = "scheduler:sched-1:deferred:" + RUN_AT_ISO + ":v1"


def _slot_key(source_id: str) -> str:
    """The direct fire's key — minted by the PRODUCTION helper."""
    from daemon.sources.adapters.scheduler import (
        _scheduler_direct_idempotency_key,
    )

    return _scheduler_direct_idempotency_key(source_id, RUN_AT_ISO)


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


def _all_jobs(engine: Engine) -> list[JobItem]:
    with Session(engine) as s:
        return list(s.exec(select(JobItem)).all())


def _job_by_id(engine: Engine, job_id: str) -> JobItem | None:
    with Session(engine) as s:
        return s.exec(
            select(JobItem).where(JobItem.job_id == job_id)
        ).first()


# The REAL InstanceMessagingService stack (used by BOTH the R2/R5b
# DONE-prior demo and the R5e behavioral key-family pins).


@pytest.fixture
def messaging_stack(engine: Engine, task_repo: TaskRepository, mq_repo: MessageQueueRepository):
    """The REAL ``InstanceMessagingService`` stack over the hermetic engine
    (real repos, real JobQueueService, real write guard; the registry and
    the title/streams are stubbed — everything the dedup path touches is
    real)."""
    import daemon.services.job_queue_service as jq_mod
    from daemon.constants import SYSTEM_DEFAULT_PROJECT_ID
    from daemon.repositories.instance.repository import (
        SQLModelInstanceRepository,
    )
    from daemon.repositories.job_queue.lock_repository import LockRepository
    from daemon.repositories.job_queue.models import JobQueue
    from daemon.repositories.job_queue.queue_repository import (
        JobQueueRepository,
    )
    from daemon.services.cancellation import CancellationService
    from daemon.services.instance_messaging import InstanceMessagingService
    from daemon.services.job_lock_manager import JobLockManager
    from daemon.services.job_queue_service import JobQueueService
    from daemon.write_pause_guard import WritePauseGuard

    instance_repo = SQLModelInstanceRepository(engine)
    lock_repo = LockRepository(engine)
    queue_repo = JobQueueRepository(engine)
    jq_service = JobQueueService(
        repository=JobRepository(engine),
        lock_manager=JobLockManager(lock_repo),
        queue_repo=queue_repo,
    )
    jq_service._project_repo = None

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

    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._instance_repository = instance_repo
    manager._queue_repository = mq_repo
    manager._project_repository = MagicMock()
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._worker_pool = MagicMock()
    manager._worker_pool.notify_work = MagicMock()
    manager._job_queue_service = jq_service
    manager._task_repo = task_repo
    manager._generate_and_broadcast_title = MagicMock()

    svc = InstanceMessagingService(
        manager=manager,
        cancellation_service=MagicMock(
            spec=CancellationService, is_shutting_down=False
        ),
    )
    return manager, svc


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

    def resolve_to_id(self, agent_id):
        return agent_id


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
    the SLOT key. The service logs the dedup hit, takes the terminal
    fallback, and returns a FRESH row carrying the CALLER's minted job_id
    with a REWRITTEN key — so the compensation predicate
    ``returned.job_id != minted`` evaluates False and the phantom prelude
    survives. Root cause pinned at the REAL service seam."""
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
    live_key = _slot_key("sched-1") + "-live"
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


def test_p1_scheduler_key_is_slot_stable() -> None:
    """R1 (fix/post-outage-recovery b2): the direct key is SLOT-STABLE and
    namespaced.

    * the SAME slot re-fired (post-outage restart, boot-guard auto-fire,
      adapter reconstruction) mints the SAME key — the collapse latch;
    * a DIFFERENT slot (a chain re-arm, an operator re-schedule) mints a
      DIFFERENT key — the re-arm is a new delivery, not a duplicate;
    * the key NEVER carries the per-fire execution_id (the crash window:
      a fresh execution id must never mean a second delivery);
    * the deferred twin is distinct per generation and stable within it,
      and never collides with the direct key.
    """
    from daemon.sources.adapters.scheduler import (
        _deferred_idempotency_key,
        _scheduler_direct_idempotency_key,
    )

    sid = "db-volume-weekly"
    slot = "2030-01-15T09:00:00+00:00"
    k1 = _scheduler_direct_idempotency_key(sid, slot)
    k1_again = _scheduler_direct_idempotency_key(sid, slot)
    k2 = _scheduler_direct_idempotency_key(sid, "2030-01-22T09:00:00+00:00")
    assert k1 == k1_again, (
        "a re-fire of the SAME slot must mint the SAME key — the "
        "cross-restart at-most-once latch"
    )
    assert k1 != k2, "a different slot is a different delivery"
    assert k1 == f"scheduler:{sid}:slot:{slot}", "the slot key shape"
    # The execution_id is telemetry, never part of the key: two DIFFERENT
    # executions of the SAME slot collapse onto one key.
    assert "exec" not in k1
    assert k1.startswith(f"scheduler:{sid}:slot:")

    d_gen1_a = _deferred_idempotency_key(sid, slot, 1)
    d_gen1_b = _deferred_idempotency_key(sid, slot, 1)
    d_gen2 = _deferred_idempotency_key(sid, slot, 2)
    assert d_gen1_a == d_gen1_b, "the deferred key is the per-generation coalesce latch"
    assert d_gen1_a != d_gen2, "a rollover must mint a fresh deferred key"
    assert d_gen1_a != k1, "the deferred twin must never collide with the direct key"


# ---------------------------------------------------------------------------
# The scheduler key path, END-TO-END (R5f — no hand-seeded rows)
# ---------------------------------------------------------------------------


def _adapter_route_harness(engine: Engine, monkeypatch, source_id: str, config_dict: dict):
    """A REAL ``SchedulerAdapter`` whose manager funnels the emitted key
    into the REAL ``create_or_get_by_idempotency_key`` claim (the exact
    atomic claim production ``JobQueueService.enqueue`` makes).

    Returns ``(adapter, manager, results)``. ``results`` collects the
    substrate-assigned ``job_id`` of every fire.
    """
    from daemon.sources.adapters.scheduler import SchedulerAdapter
    from tests.conftest import make_config

    monkeypatch.setattr(
        "daemon.sources.adapters.scheduler.get_registry", lambda: _FakeRegistry()
    )
    job_repo = JobRepository(engine)
    mapper_repo = MagicMock()
    mapper_repo.get_instance_mapping = MagicMock(return_value=None)

    manager = MagicMock()
    manager.spawn_instance_with_mcp = AsyncMock(return_value="inst-route-1")
    results: list = []

    async def _enqueue(**kwargs):
        key = kwargs.get("idempotency_key")
        job, _created = job_repo.create_or_get_by_idempotency_key(
            agent_id="ari",
            agent_dir="/agents/ari",
            message=kwargs.get("message", "x"),
            source="scheduler",
            project_id="p",
            priority=5,
            # An unkeyed enqueue mints a fresh unique key (mirrors
            # production: unkeyed enqueues never collapse).
            idempotency_key=key if key else f"unkeyed-{uuid4()}",
        )
        result = MagicMock(
            job_id=job.job_id,
            message_id="m-1",
            instance_id="inst-route-1",
            status="queued",
        )
        results.append(result)
        return result

    manager.enqueue_message_job = AsyncMock(side_effect=_enqueue)

    config = make_config(source_id, config_dict)
    adapter = SchedulerAdapter(
        config, lambda *a, **k: None, manager=manager, source_repo=mapper_repo
    )
    return adapter, manager, results


@pytest.mark.asyncio
async def test_p1a_crash_window_slot_key_collapses_refire_with_live_prior(
    engine: Engine, monkeypatch
) -> None:
    """R5a — THE VERIFIER'S SCENARIO, as a hermetic pin.

    The crash window: the fire DELIVERS (a live JobItem exists), the chain
    re-arm / source-disable write has NOT committed yet, the process dies,
    the boot guard replays the SAME slot
    (``past_run_at_left_for_auto_fire``). The PER-EXECUTION key minted a
    fresh key for that replay ⇒ TWO JobItems ⇒ a SECOND delivery. Under
    the SLOT-STABLE key (R1) the replay emits the SAME key, so the REAL
    atomic claim returns the PRIOR live JobItem — exactly ONE JobItem, one
    delivery.

    End-to-end: the key is minted by the REAL scheduler route (two adapters
    built from the SAME persisted config — what a daemon restart produces),
    and the claim is the REAL substrate claim SQL. No hand-seeded rows."""
    adapter1, manager1, results1 = _adapter_route_harness(
        engine, monkeypatch, "idem-crashwin",
        {"run_at": RUN_AT_ISO, "agent": "ari", "message": "weekly report",
         "project_id": "p"},
    )
    adapter2, manager2, results2 = _adapter_route_harness(
        engine, monkeypatch, "idem-crashwin",
        {"run_at": RUN_AT_ISO, "agent": "ari", "message": "weekly report",
         "project_id": "p"},
    )

    # Fire #1 — the crash-window FIRST leg (delivered, live).
    await adapter1._route_via_job_queue(
        "exec-crash-1", "weekly report", {"agent": "ari"}
    )
    # Fire #2 — the crash-window REPLAY (a NEW execution id, the SAME slot).
    await adapter2._route_via_job_queue(
        "exec-crash-2", "weekly report", {"agent": "ari"}
    )

    # The replay emitted the SAME SLOT key (this is the guard F1(a) lost).
    kwargs1 = manager1.enqueue_message_job.await_args.kwargs
    kwargs2 = manager2.enqueue_message_job.await_args.kwargs
    assert kwargs1["idempotency_key"] == _slot_key("idem-crashwin"), (
        "the key must be the SLOT key (scheduler:{sid}:slot:{run_at})"
    )
    assert kwargs2["idempotency_key"] == kwargs1["idempotency_key"], (
        "a re-fire of the SAME slot must emit the SAME key even across two "
        "execution ids"
    )
    assert results1[0].job_id != "exec-crash-2"

    # The REAL atomic claim collapsed the replay: exactly ONE JobItem row,
    # and both fires were handed the SAME (live) row.
    rows = _all_jobs(engine)
    assert len(rows) == 1, (
        f"the crash-window re-fire must collapse onto ONE JobItem, got "
        f"{len(rows)}: {[(r.job_id, r.idempotency_key) for r in rows]}"
    )
    assert rows[0].idempotency_key == _slot_key("idem-crashwin")
    assert results2[0].job_id == results1[0].job_id, (
        "the replay must be handed the PRIOR live JobItem (no second delivery)"
    )
    assert rows[0].admission_state in (
        AdmissionState.QUEUED.value,
        AdmissionState.ACTIVE.value,
    ), "the surviving row is the LIVE prior (not a second, cancelled chain)"


@pytest.mark.asyncio
async def test_p1_slot_key_path_end_to_end_and_claimable(
    engine: Engine, monkeypatch, task_repo: TaskRepository, mq_repo: MessageQueueRepository
) -> None:
    """THE CLAIM (P1, R5f): the scheduler key path is exercised END-TO-END —
    the adapter's REAL route mints the slot key, the REAL substrate claim
    inserts the JobItem, and the resulting row shape reaches
    RUNNING/processing through the REAL ``claim_pending_task`` SQL with no
    human intervention (no pause/resume, no operator repair)."""
    adapter, _manager, results = _adapter_route_harness(
        engine, monkeypatch, "sched-claim",
        {"run_at": RUN_AT_ISO, "agent": "ari", "message": "weekly report",
         "project_id": "p"},
    )
    # The scheduler's own prelude rows (Task PENDING bound to the minted
    # work_id + MQ ready) — the production enqueue_message_job writes them;
    # this harness stubs enqueue_message_job, so the pair is minted by the
    # harness with the SAME work_id the substrate assigned.
    await adapter._route_via_job_queue(
        "exec-claim-1", "weekly report", {"agent": "ari"}
    )
    work_id = results[0].job_id
    _seed_instance(engine, "inst-route-1")
    task_id, message_id = _seed_prelude_pair(
        engine,
        task_repo=task_repo,
        mq_repo=mq_repo,
        work_id=work_id,
        message_id=f"mq-{uuid4().hex[:12]}",
        instance_id="inst-route-1",
    )

    rows = _all_jobs(engine)
    assert len(rows) == 1, "one fire ⇒ exactly ONE JobItem"
    assert rows[0].idempotency_key == _slot_key("sched-claim"), (
        f"the route must emit the SLOT key, got {rows[0].idempotency_key!r}"
    )
    assert rows[0].job_id == work_id
    assert rows[0].admission_state == AdmissionState.QUEUED.value, (
        "the substrate claim leaves the row QUEUED (the job pipeline owns "
        "the activation)"
    )

    # The JobProcessor's OWN activation step (the queue-aware claim gate
    # requires it — a QUEUED anchor holds the task until the job pipeline
    # activates). Flipping the admission state here models exactly that
    # step; the task row itself is NEVER touched by hand.
    with Session(engine) as s:
        j = s.exec(
            select(JobItem).where(JobItem.job_id == work_id)
        ).first()
        j.admission_state = AdmissionState.ACTIVE.value
        s.add(j)
        s.commit()

    claimed = task_repo.claim_pending_task(worker_id="worker-pin-1", lane="default")
    assert claimed is not None, "the post-fire row MUST be claimable without human repair"
    assert claimed.id == task_id
    assert claimed.status == "running"

    # The message left 'ready': the REAL dequeue claims it into processing.
    msg = mq_repo.dequeue(instance_id="inst-route-1")
    assert msg is not None and msg.message_id == message_id
    assert msg.status == "processing"
    assert msg.processing_started_at is not None


# ---------------------------------------------------------------------------
# P1B — the DONE-prior reconciliation (R2): ONE net delivered chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p1b_done_prior_reconciles_to_one_net_delivery(
    engine: Engine, messaging_stack, caplog
) -> None:
    """R2/R5b — the verifier's substrate demo, under the remedy:

        slot key + DONE prior + two fires  ⇒  exactly ONE DELIVERED chain.

    The FIRST fire delivered (its JobItem is DONE — the normal end state of
    a one-shot). The SECOND fire of the SAME slot (a crash-window replay)
    goes through the REAL ``enqueue_message_job``: the prelude pair is
    minted, the substrate takes the terminal fallback and re-inserts a
    FRESH fallback JobItem bound to the caller's mint (the masquerade), and
    the F1(b) scope predicate must route THAT masquerade into the
    compensation — cancelling the phantom prelude AND the orphan fallback
    JobItem.

    FAIL-HONEST: under the rejected PER-EXECUTION key + its ':exec:' scope
    substring the slot key was never classified direct, the compensation
    never ran, and this scenario left TWO rows with a LIVE second chain —
    exactly the guard the remedy restores. This is BOTH the old-bug repro
    and the remedy proof."""
    _manager, svc = messaging_stack
    _seed_instance(engine, "inst-doneprior")
    delivered_job_id = str(uuid4())
    # Fire #1 delivered: the JobItem is DONE.
    _seed_job(
        engine,
        job_id=delivered_job_id,
        idempotency_key=SCHED_KEY,
        admission_state=AdmissionState.DONE.value,
    )

    with caplog.at_level(logging.INFO, logger="daemon.services.instance_messaging"):
        result2 = await svc.enqueue_message_job(
            instance_id="inst-doneprior",
            message="weekly report",
            source="scheduler",
            idempotency_key=SCHED_KEY,
        )

    # The masquerade was observed and ROUTED into the compensation.
    assert any("masquerad" in r.message.lower() for r in caplog.records), (
        "the masquerade arm must log its truthful marker"
    )
    assert any(
        "compensat" in r.message.lower() or "cancel" in r.message.lower()
        for r in caplog.records
    ), "the compensation must actually run (observable marker)"

    # The reconciliation: prelude cancelled/deleted, orphan fallback
    # JobItem cancelled ⇒ net ONE DELIVERED chain.
    fallback_id = result2.job_id
    assert fallback_id != delivered_job_id
    cancelled_fallback = _job_by_id(engine, fallback_id)
    assert cancelled_fallback is not None
    assert cancelled_fallback.admission_state == AdmissionState.DONE.value, (
        "the orphan fallback JobItem must be terminal (cancelled) — "
        "no second live chain"
    )
    rows = _all_jobs(engine)
    assert delivered_job_id in {j.job_id for j in rows}
    live_second = [
        j for j in rows
        if j.job_id != delivered_job_id
        and j.admission_state in (AdmissionState.QUEUED.value, AdmissionState.ACTIVE.value)
    ]
    assert live_second == [], (
        f"NO second live chain may survive: {[j.job_id for j in live_second]}"
    )
    # The phantom prelude is reconciled: NO orphaned PENDING Task or ready
    # MessageQueue row survives.
    with Session(engine) as s:
        tasks = list(s.exec(select(Task)).all())
        mqs = list(s.exec(select(MessageQueue)).all())
    pending = [t for t in tasks if t.status == "pending"]
    assert pending == [], (
        f"the phantom prelude Task must be cancelled: "
        f"{[(t.id, t.work_id, t.status) for t in tasks]}"
    )
    ready = [m for m in mqs if m.status == "ready"]
    assert ready == [], (
        f"the phantom prelude MQ row must be deleted: {[m.message_id for m in mqs]}"
    )


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
        idempotency_key=_slot_key("sched-1"),
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
async def test_p4e_masquerade_through_real_enqueue_message_job_compensates(
    engine: Engine, messaging_stack
) -> None:
    """R5e — BEHAVIORAL (replaces the string-presence wiring pin): a
    masquerade through the REAL ``enqueue_message_job`` fires the
    compensation. A DONE prior holds the SLOT key; the second call writes
    the prelude, hits the terminal fallback, and the F1(b) reconcile must
    cancel the phantom prelude AND the orphan fallback JobItem — exactly
    ONE delivered chain."""
    _manager, svc = messaging_stack
    _seed_instance(engine, "inst-real-masq")
    delivered_job_id = str(uuid4())
    _seed_job(
        engine,
        job_id=delivered_job_id,
        idempotency_key=SCHED_KEY,
        admission_state=AdmissionState.DONE.value,
    )

    result2 = await svc.enqueue_message_job(
        instance_id="inst-real-masq",
        message="weekly report",
        source="scheduler",
        idempotency_key=SCHED_KEY,
    )

    rows = _all_jobs(engine)
    # The masquerade inserted a fresh fallback row bound to the caller's
    # mint — and the compensation CANCELLED it.
    fallback_id = result2.job_id
    assert fallback_id != delivered_job_id
    fallback = _job_by_id(engine, fallback_id)
    assert fallback is not None
    assert fallback.admission_state == AdmissionState.DONE.value, (
        "the orphan fallback JobItem must be terminal after the "
        "compensation (cancelled) — no second live chain"
    )
    live_rows = [
        j for j in rows
        if j.admission_state in (AdmissionState.QUEUED.value, AdmissionState.ACTIVE.value)
    ]
    assert live_rows == [], (
        f"exactly ONE delivered chain — no live second chain may survive: "
        f"{[(j.job_id, j.admission_state) for j in live_rows]}"
    )
    # The phantom prelude is reconciled: NO orphaned PENDING Task or
    # ready MessageQueue row survives.
    with Session(engine) as s:
        tasks = list(s.exec(select(Task)).all())
        mqs = list(s.exec(select(MessageQueue)).all())
    pending = [t for t in tasks if t.status == "pending"]
    assert pending == [], (
        f"the phantom prelude Task must be cancelled: "
        f"{[(t.id, t.work_id, t.status) for t in tasks]}"
    )
    ready = [m for m in mqs if m.status == "ready"]
    assert ready == [], (
        f"the phantom prelude MQ row must be deleted: "
        f"{[m.message_id for m in mqs]}"
    )


@pytest.mark.asyncio
async def test_p4e_deferred_key_does_not_compensate(
    engine: Engine, messaging_stack
) -> None:
    """R5e — DEC-18 pinned BEHAVIORALLY: a DEFERRED key on a DONE prior does
    NOT compensate — the terminal-bypass re-delivery is the deliberate
    deferred feature (the marker-miss fallback depends on the substrate
    returning a fresh row). The fresh row STAYS QUEUED and the prelude pair
    stays PENDING/ready (a live second delivery is CORRECT here)."""
    _manager, svc = messaging_stack
    _seed_instance(engine, "inst-real-deferred")
    prior_job_id = str(uuid4())
    _seed_job(
        engine,
        job_id=prior_job_id,
        idempotency_key=DEFERRED_KEY,
        admission_state=AdmissionState.DONE.value,
    )

    result2 = await svc.enqueue_message_job(
        instance_id="inst-real-deferred",
        message="deferred delivery",
        source="scheduler",
        idempotency_key=DEFERRED_KEY,
    )

    # NO compensation: the fallback row survives LIVE (the re-delivery).
    assert result2.job_id != prior_job_id
    fallback = _job_by_id(engine, result2.job_id)
    assert fallback is not None
    assert fallback.admission_state in (
        AdmissionState.QUEUED.value,
        AdmissionState.ACTIVE.value,
    ), (
        f"the DEFERRED re-delivery must NOT be compensated/cancelled, got "
        f"{fallback.admission_state}"
    )
    # And the prelude pair survives (the delivery is going to be driven).
    with Session(engine) as s:
        tasks = list(s.exec(select(Task)).all())
        mqs = list(s.exec(select(MessageQueue)).all())
    assert len(tasks) == 1 and tasks[0].status == "pending", (
        "the deferred prelude Task must stay PENDING (no compensation)"
    )
    assert len(mqs) == 1 and mqs[0].status == "ready", (
        "the deferred prelude MQ row must stay ready (no compensation)"
    )


def test_p4e_scope_predicate_is_segment_wise() -> None:
    """R3 — the compensation-scope predicate is SEGMENT-WISE, not a
    substring test: a source_id slug that itself carries a ``slot`` segment
    must NOT make a DEFERRED key look direct (compensating a deferred
    delivery would break DEC-18's no-loss guarantee), and a deferred key is
    never direct."""
    from daemon.services.instance_messaging import is_scheduler_direct_slot_key

    assert is_scheduler_direct_slot_key(SCHED_KEY) is True
    assert is_scheduler_direct_slot_key("scheduler:sid:slot:2030-01-15T09:00:00+00:00")
    # The DEFERRED twin is never direct — including when the slug carries a
    # 'slot' segment (the exclusion wins).
    assert is_scheduler_direct_slot_key(DEFERRED_KEY) is False
    assert is_scheduler_direct_slot_key("scheduler:x:deferred:v1") is False
    assert (
        is_scheduler_direct_slot_key(
            "scheduler:weird:slot:sid:deferred:2030-01-15T09:00:00+00:00:v1"
        )
        is False
    ), "a ':slot:' source_id slug must not trip the direct classification"
    assert (
        is_scheduler_direct_slot_key("scheduler:weird:slot:sid:slot:2030-01-15T09:00:00+00:00")
        is True
    ), "a direct slot key of a slug carrying ':slot:' is still direct"
    # Legacy per-execution keys (pre-R1) are NOT direct slot keys.
    assert is_scheduler_direct_slot_key("scheduler:sid:exec:abc") is False
    # Non-scheduler keys are never direct.
    assert is_scheduler_direct_slot_key("chat:slot:whatever") is False
    assert is_scheduler_direct_slot_key(None) is False
