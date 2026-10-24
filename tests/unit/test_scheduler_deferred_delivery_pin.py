"""RED PINS — commission ``scheduler-deferred-alarm-delivery`` (pin file 1 of 3).

TEST-FIRST.  This file implements **NO fix**.  Every pin asserts the
POST-FIX behaviour specified by the frozen plan:

    plan (FROZEN, read-only):
      .agents/shared/planning/scheduler-deferred-alarm-delivery/
        - decisions.md   (601L — canonical pin spec, DEC-1..DEC-22)
        - plan-overview.md (223L — §(e) pins table = the mutation column)

at base commit ``8e6df58e445cd56b5e775dacebe0ef53f0add0b4`` (branch
``feature/scheduler-deferred-alarm-delivery``).  At base **all 13 pins are
RED by design** — each fails with a clean assertion failure proving the
required behaviour is ABSENT (never ImportError / AttributeError /
fixture crash: every pin drives only symbols that exist at base —
``SchedulerAdapter._run_schedule`` / ``._emit_scheduled_message`` /
``._is_instance_active``, ``InstanceManager.enqueue_message_job``,
``TaskRepository.claim_pending_task`` — and asserts on DB rows).

LINEAGE: the substrate (engine fixture, ``InstanceManager`` wiring,
seeding helpers, adapter-driving technique, production-callback replicas)
is copied from the commission's mechanism probe
``tests/unit/test_scheduler_deferred_mechanism_probe.py`` (1501L) with the
probe's PostgreSQL branch REMOVED — the plan's §(e) substrate contract is
a **file-backed SQLite** DB under ``tmp_path`` (no PG, no daemon boot, no
port binding); the ``ensemble_dev`` fence is never touched.  The probe
file is unmodified; sibling pin files (E1-mapper, visibility) are owned by
other coders — this file does not touch them.

THE DEFECT (plan §(a)): ``self_alarm`` in ``reuse_instance`` mode
SILENTLY DROPS the alarm when the bound caller is busy at the fire
instant; a one-shot is consumed and lost with zero durable trace.  Three
drop sites, one mechanism (busy branch ``scheduler.py:1197-1215``,
semaphore skip ``:909-942`` — plus the manual twin, a disclosed
non-goal).

THE 13 PINS (class::test — exact commission names):
  1  TestBusyCallerDefersDelivery::test_busy_caller_defers_instead_of_dropping
  2  TestExecutionRowOnDefer::test_deferral_writes_a_deferred_schedule_executions_row
  3  TestExecutionRowOnDefer::test_row_write_order_is_row_first_and_failure_leg_is_terminal
  4  TestIdempotencyCoalescing::test_n_busy_fires_coalesce_into_one_delivery_with_live_count
  5  TestOneShotDurability::test_one_shot_alarm_leaves_a_durable_pending_delivery
  6  TestTasklessEnqueue::test_marker_window_enqueue_without_task_is_failed_not_deferred_durable
  7  TestTerminalBypassSafetyNet::test_generation_key_enqueue_against_terminal_predecessor_yields_exactly_one_fresh_delivery
  8  TestBusyPredicate::test_busy_predicate_is_exactly_running_waiting_waiting_children
  9  TestRunCounterDivergence::test_run_counter_counts_direct_dispatches_only_and_is_blind_to_deferrals
 10  TestMarkerMissFallback::test_marker_miss_after_enqueue_self_heals_via_dedup_hit_kept_message_id
 11  TestWaitingOrderingDelta::test_waiting_and_waiting_children_receive_alarm_as_next_turn
 12  TestNonDeliveryAgeCap::test_age_cap_breach_terminates_visibly_and_never_opens_a_fresh_generation
 13  TestPausedCaller::test_paused_caller_is_not_blocking_and_receives_the_alarm

PLUS two plan-mandated sibling legs (house shape: sibling functions in the
same class, both RED at base, both mutation-proved by the tester):
  5b TestOneShotDurability::test_semaphore_no_target_rearm_leaves_the_one_shot_armed
      (plan §(e) pin #4's ADR-3 sibling assertion)
  4c TestIdempotencyCoalescing::test_claimed_delivery_still_coalesces_instead_of_opening_a_second_delivery
      (plan §(e) row 3's B3 CLAIMED-DELIVERY LEG)

PLUS three ruling-round legs (leader-ruled Q1 + Q2, added 2026-10-21):
  7b TestTerminalBypassSafetyNet::test_terminal_bypass_safety_net_mints_exactly_one_fresh_delivery
      (Q2 — the REAL safety-net property: an enqueue under a key that HAS a
      terminal predecessor under the SAME key yields EXACTLY ONE fresh
      delivery under '<key>#<8hex>'; the sibling test above keeps the Q2
      correction — the rollover's fresh generation key is NEW, i.e.
      'gen2_key == k2')
  12b TestNonDeliveryAgeCap::test_age_cap_breach_after_delivered_delivery_clears_the_carrier_without_a_failed_row
      (Q1 case 3a — the occurrence WAS DELIVERED: the carrier clears, NO
      false failed row, the normal consumption semantics apply)
  12c TestNonDeliveryAgeCap::test_age_cap_breach_without_marker_terminates_visibly_and_clears_the_carrier
      (Q1 case 3b/3c — nothing will ever land: terminate VISIBLY — consume
      + a terminal 'nondelivery_age_cap' failed row + WARNING + the carrier
      CLEARED; this leg closes the 0.2 Hz spin)

[LEADER-RULED — iter-003 B3, adopted 2026-10-21 — INTERIM CANONICAL DELTA]
The COALESCING gate's Task condition widens from ``pending`` to
NON-TERMINAL: the Task flips to RUNNING at claim
(``task/repository.py:2801-2804``) BEFORE the message_queue row leaves
'ready', so a PENDING-only gate fails in that window → gen-bump + a
SECOND live enqueue (W9's forbidden outcome, an at-most-once breach, the
count resets to 1).  PRECEDENCE (explicit):
  * coalescing LIVENESS = the MQ status guard (``status IN
    ('ready','retrying')``) — that IS the live-delivery test;
  * the Task check is the STRANDED-DELIVERY test, used at ENQUEUE time
    for durable evidence — THERE the predicate stays VERBATIM (PENDING,
    per-delivery, message_id-matched, guarded) and is NOT widened.
Consequence for this file: liveness assertions moved to
NON-TERMINAL Tasks + the MQ status guard (``LIVE_MQ_STATUSES``); the
enqueue-time checks (TestOneShotDurability's durable evidence,
TestTasklessEnqueue incl. its DECOY leg) keep PENDING verbatim.

Each pin's docstring carries its RED-mutation spec VERBATIM from the
plan's §(e) pins table (the tester mutation-proves with it).

FENCE: file-backed SQLite only — no PostgreSQL, no ``ensemble_prod``,
no live install, no port 9797, no daemon boot, no network.  Nothing
outside this file is created or modified; the file stays UNTRACKED.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select

import daemon.repositories.instance.models  # noqa: F401  (register tables)
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.source.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.source.models import (
    InstanceMapping,
    ScheduleExecution,
    SourceConfig as SourceConfigRow,
)
from daemon.repositories.task.models import Task, TaskStatus

#: Row namespace tag (never collides with the probe's or siblings' rows).
PIN_TAG = "pinscheddef"

#: The scheduler adapter's logger (``scheduler.py:93``) — the L-F and
#: age-cap WARNING assertions read records from THIS logger only.
SCHED_LOGGER = "daemon.sources.adapters.scheduler"

#: DEC-4 / W7(i): the busy predicate is EXACTLY this set (string values of
#: ``InstanceStatus``).  Asserted as an exact set, never a subset.
BUSY_STATUSES = ("running", "waiting", "waiting_children")

#: Plan §(e) pin #3 / DEC-19: the count cap (saturation carries "16+").
DEFER_COUNT_CAP = 16

#: Plan DEC-19: the age cap default (24h).  Pins seed episodes OLDER than
#: this; the implementation reads the real knob from ``SchedulingConfig``.
DEFER_AGE_CAP_S = 86400

#: Terminal Task statuses — a Task in one of these is NOT a live delivery.
#: [LEADER-RULED — iter-003 B3] the COALESCING liveness test is the MQ
#: status guard (``status IN ('ready','retrying')``); where a pin asserts
#: "the delivery is still live" it asserts the Task is NON-TERMINAL (the
#: claim window flips a Task to RUNNING while its MQ row is still
#: 'ready' — ``task/repository.py:2801-2804`` — so a PENDING-only
#: liveness assertion would fail a CORRECT implementation mid-claim).
TERMINAL_TASK_STATUSES = ("completed", "failed", "cancelled", "expired")

#: The MQ-side liveness guard (W9) — the canonical live-delivery test.
LIVE_MQ_STATUSES = ("ready", "retrying")


# ---------------------------------------------------------------------------
# Fixtures — hermetic file-backed SQLite (plan §(e) substrate contract)
# ---------------------------------------------------------------------------


@pytest.fixture
def pin_engine(tmp_path) -> Engine:
    """A REAL file-backed SQLite engine under ``tmp_path``.

    The plan's substrate contract (§(e)): file DB + ``NullPool``, never
    ``StaticPool``/``:memory:``; the probe's PostgreSQL branch is
    REMOVED so no fence database is ever contacted.
    """
    eng = create_engine(
        f"sqlite:///{tmp_path}/pin_sched_deferred.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
async def pin_manager(pin_engine: Engine):
    """A REAL ``InstanceManager`` over the pin engine.

    Mirrors the probe's ``probe_manager`` (lineage:
    ``tests/unit/test_scheduler_deferred_mechanism_probe.py:219``) and the
    production lifespan wiring: real ``JobQueueService`` + real
    repositories, NO WorkerPool (the Task must stay PENDING for DB
    inspection).
    """
    import daemon.manager as _daemon_manager_module  # noqa: F401
    from daemon.config import (
        AgentsConfig,
        Config,
        DaemonConfig,
        LLMConfig,
        LimitsConfig,
        PersistenceConfig,
    )
    from daemon.manager import InstanceManager
    from daemon.repositories.job_queue.lock_repository import LockRepository
    from daemon.repositories.job_queue.queue_repository import JobQueueRepository
    from daemon.repositories.job_queue.repository import JobRepository
    from daemon.repositories.task.repository import TaskRepository
    from daemon.services.job_lock_manager import JobLockManager
    from daemon.services.job_queue_service import JobQueueService

    _seed_system_default_project(pin_engine)

    config = Config(
        llm=LLMConfig(
            base_url="https://api.openai.com/v1",
            api_key="pin-key",
            model="gpt-4",
            temperature=0.7,
        ),
        limits=LimitsConfig(
            max_children_per_instance=3,
            instance_timeout_minutes=60,
        ),
        persistence=PersistenceConfig(
            db_path="/tmp/pin-sched-def-unused.db",
            checkpoint_interval=1,
            checkpoint_ttl_hours=168,
            checkpoint_cleanup_interval=24,
            max_instance_history=300,
        ),
        daemon=DaemonConfig(host="127.0.0.1", port=8079),
        agents=AgentsConfig(directory="./agents"),
    )

    from unittest.mock import patch

    with (
        patch(
            "daemon.migrations.runner.MigrationRunner.run_pending_migrations",
            return_value=[],
        ),
        patch(
            "daemon.manager.create_engine_from_config",
            return_value=pin_engine,
        ),
    ):
        manager = InstanceManager(config)

    manager._loop = asyncio.get_running_loop()
    manager._worker_pool = None  # no claim/consumption — Task stays PENDING

    # Production wires ``manager._task_repo`` inside ``setup_worker_pool()``;
    # the real TaskRepository is instantiated directly over the same engine.
    manager._task_repo = TaskRepository(
        engine=pin_engine, on_pending_task=lambda: None
    )

    # Provision the system queues (mirror of the production lifespan) so
    # ``JobQueueService.enqueue`` finds ``system_parallel_queue`` etc.
    from daemon import constants as _constants

    queue_repo = JobQueueRepository(pin_engine)
    for _qname, _qtype, _qlimit in (
        ("system_fifo_queue", "fifo", 1),
        ("system_parallel_queue", "parallel", 5),
        ("system_defer_queue", "defer", 1),
        ("system_background_queue", "background", 1),
    ):
        if queue_repo.get_by_name(
            _constants.SYSTEM_DEFAULT_PROJECT_ID, _qname
        ) is None:
            queue_repo.create(
                project_id=_constants.SYSTEM_DEFAULT_PROJECT_ID,
                queue_name=_qname,
                queue_type=_qtype,
                concurrency_limit=_qlimit,
                is_system=True,
            )

    job_service = JobQueueService(
        repository=JobRepository(pin_engine),
        lock_manager=JobLockManager(lock_repo=LockRepository(pin_engine)),
        queue_repo=JobQueueRepository(pin_engine),
        instance_manager=manager,
    )
    manager.set_job_queue_service(job_service)
    return manager


# ---------------------------------------------------------------------------
# Seeding helpers (lineage: probe file; SQLite-row shaping only)
# ---------------------------------------------------------------------------


def _seed_system_default_project(eng: Engine) -> None:
    from daemon import constants
    from daemon.repositories.project.models import Project, ProjectStatus

    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(eng) as s:
        s.add(
            Project(
                project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
                name="_system_default",
                project_type="system",
                status=ProjectStatus.ACTIVE.value,
                description="scheduler deferred delivery pins",
                project_metadata={},
                relationships={},
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        s.commit()


def _seed_instance(
    eng: Engine,
    *,
    instance_id: str,
    status: str = InstanceStatus.RUNNING.value,
) -> Instance:
    from daemon import constants

    inst = Instance(
        instance_id=instance_id,
        agent_id="developer",
        agent_dir="/agents/developer",
        project_id=constants.SYSTEM_DEFAULT_PROJECT_ID,
        status=status,
        version=1,
        instance_metadata={},
    )
    with Session(eng) as session:
        session.add(inst)
        session.commit()
        session.refresh(inst)
    return inst


def _set_instance_status(eng: Engine, instance_id: str, status: str) -> None:
    with Session(eng) as session:
        inst = session.get(Instance, instance_id)
        assert inst is not None, f"instance {instance_id} missing"
        inst.status = status
        session.add(inst)
        session.commit()


def _seed_task(
    eng: Engine,
    *,
    instance_id: str,
    status: str = TaskStatus.PENDING.value,
    message_id: str | None = None,
    work_id: str | None = None,
) -> Task:
    """Seed a Task row in an arbitrary status (DECOY / claimed-delivery /
    seeded-generation rows for the taskless + age-cap pins)."""
    task = Task(
        work_id=work_id or str(uuid.uuid4()),
        task_type="process_message",
        instance_id=instance_id,
        message_id=message_id,
        status=status,
        worker_id="pin-seeded",
    )
    with Session(eng) as session:
        session.add(task)
        session.commit()
        session.refresh(task)
    return task


def _seed_mq_row(
    eng: Engine,
    *,
    message_id: str,
    instance_id: str,
    content: str = "pin-seeded deferred alarm payload",
    status: str = "ready",
) -> MessageQueue:
    row = MessageQueue(
        message_id=message_id,
        instance_id=instance_id,
        content=content,
        source="scheduler",
        type="human",
        status=status,
        priority=5,
        message_metadata={},
    )
    with Session(eng) as session:
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def _tasks_for(eng: Engine, instance_id: str) -> list[Task]:
    with Session(eng) as session:
        return list(session.exec(select(Task).where(Task.instance_id == instance_id)))


def _mq_for(eng: Engine, instance_id: str) -> list[MessageQueue]:
    with Session(eng) as session:
        return list(
            session.exec(select(MessageQueue).where(MessageQueue.instance_id == instance_id))
        )


def _jobitems_for(eng: Engine, instance_id: str | None = None) -> list:
    from daemon.repositories.job_queue.models import JobItem

    with Session(eng) as session:
        stmt = select(JobItem)
        if instance_id is not None:
            stmt = stmt.where(JobItem.instance_id == instance_id)
        return list(session.exec(stmt))


def _jobitems_with_key(eng: Engine, key: str) -> list:
    from daemon.repositories.job_queue.models import JobItem

    with Session(eng) as session:
        return list(session.exec(select(JobItem).where(JobItem.idempotency_key == key)))


def _schedule_executions(eng: Engine, schedule_id: str) -> list[ScheduleExecution]:
    with Session(eng) as session:
        return list(
            session.exec(
                select(ScheduleExecution).where(ScheduleExecution.schedule_id == schedule_id)
            )
        )


def _source_row(eng: Engine, source_id: str) -> SourceConfigRow | None:
    with Session(eng) as session:
        return session.get(SourceConfigRow, source_id)


def _config_of(eng: Engine, source_id: str) -> dict:
    row = _source_row(eng, source_id)
    assert row is not None, f"source row {source_id} missing"
    return dict(row.config or {})


def _carrier_value(eng: Engine, source_id: str, key: str):
    """TOLERANT episode-carrier accessor.

    The carrier keys (``_defer_since`` / ``_defer_count`` / ``_fer_gen`` /
    ``_defer_message_id``) are POST-FIX attributes on
    ``source_configs.config``: at base the row may not carry them at all.
    This accessor returns ``None`` for a missing key instead of raising —
    never an ``AttributeError`` / ``KeyError``, which would surface as a
    FIXTURE-ERROR failure (a BAD pin) rather than a clean assertion about
    the missing post-fix behaviour.
    """
    return _config_of(eng, source_id).get(key)


def _edit_source_config(eng: Engine, source_id: str, mutate) -> dict:
    """Surgically mutate the ``source_configs.config`` JSON dict (used to
    plant/strip episode-carrier keys: ``_defer_message_id`` etc.)."""
    with Session(eng) as session:
        row = session.get(SourceConfigRow, source_id)
        assert row is not None, f"source row {source_id} missing"
        cfg = dict(row.config or {})
        mutate(cfg)
        row.config = cfg
        row.updated_at = datetime.now(timezone.utc).isoformat()
        session.add(row)
        session.commit()
        session.refresh(row)
        return dict(row.config or {})


def _set_job_admission(eng: Engine, job_id: str, state: str) -> None:
    """Force a JobItem's admission_state (probe lineage: the queue-awareness
    gate releases only when the linked JobItem is ACTIVE/terminal)."""
    from daemon.repositories.job_queue.models import JobItem

    with Session(eng) as session:
        job = session.get(JobItem, job_id)
        assert job is not None, f"JobItem {job_id} missing"
        job.admission_state = state
        session.add(job)
        session.commit()


def _complete_task_row(eng: Engine, task_pk: int) -> None:
    with Session(eng) as session:
        task = session.get(Task, task_pk)
        assert task is not None, f"task pk {task_pk} missing"
        task.status = TaskStatus.COMPLETED.value
        task.completed_at = datetime.now(timezone.utc)
        session.add(task)
        session.commit()


def _set_task_status(
    eng: Engine,
    task_pk: int,
    status: str,
    worker_id: str | None = None,
) -> None:
    """Force a Task's status (used to plant the CLAIM WINDOW: the Task
    flips to RUNNING at claim — ``task/repository.py:2801-2804`` — BEFORE
    the message_queue row leaves 'ready')."""
    with Session(eng) as session:
        task = session.get(Task, task_pk)
        assert task is not None, f"task pk {task_pk} missing"
        task.status = status
        if worker_id is not None:
            task.worker_id = worker_id
        session.add(task)
        session.commit()


def _seed_source_and_mapping(
    eng: Engine,
    *,
    source_id: str,
    instance_id: str | None,
    run_at: datetime | None = None,
    interval_seconds: int | None = None,
    instance_mode: str = "reuse_instance",
    message: str = "pin alarm payload",
    with_mapping: bool = True,
) -> None:
    """Seed the ``source_configs`` row (+ optional ``instance_mappings``
    row) in the real self-alarm row shape (probe lineage)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    config: dict = {
        "instance_mode": instance_mode,
        "agent": "developer",
        "message": message,
        "max_concurrent": 1,
    }
    if run_at is not None:
        config["run_at"] = run_at.isoformat()
    if interval_seconds is not None:
        config["interval_seconds"] = interval_seconds
    with Session(eng) as session:
        session.add(
            SourceConfigRow(
                source_id=source_id,
                source_type="scheduler",
                name=f"self-alarm:{PIN_TAG}",
                config=config,
                credentials=None,
                enabled=True,
                autostart=True,
                status="running",
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        session.commit()

    if with_mapping and instance_id is not None:
        with Session(eng) as session:
            session.add(
                InstanceMapping(
                    mapping_id=str(uuid.uuid4()),
                    source_id=source_id,
                    external_user_id=source_id,
                    agent_instance_id=instance_id,
                    agent_id="developer",
                    agent_dir="/agents/developer",
                    mapping_metadata={},
                    last_message_at=now_iso,
                    created_at=now_iso,
                )
            )
            session.commit()


def _expected_run_at_key(source_id: str, run_at: datetime) -> str:
    """The base derivation (``scheduler.py:1105-1113``): the one-shot
    idempotency key is ``scheduler:{source_id}:{run_at.isoformat()}``.
    ADR-2 keeps this key for one-shots post-fix; recurring rollovers use
    ``scheduler:{source_id}:deferred:v{gen}``."""
    return f"scheduler:{source_id}:{run_at.isoformat()}"


def _expected_generation_key(source_id: str, gen: int) -> str:
    """The post-fix rollover key shape (plan C1 / ADR-2)."""
    return f"scheduler:{source_id}:deferred:v{gen}"


# ---------------------------------------------------------------------------
# Production-callback replicas (lineage: probe file + registry.py)
# ---------------------------------------------------------------------------


def _production_on_complete(source_repo, source_id: str):
    """Replica of the production one-shot completion callback
    (``daemon/sources/registry.py:514-526``): stop + disable the source."""

    def _on_complete(source_id: str, completed: bool) -> None:
        source_repo.update_source_status(source_id, "stopped")
        source_repo.update_source_config(source_id, enabled=False)

    return _on_complete


def _db_execution_callback(repo):
    """Production-mirror execution callback (``registry.py:476-502``):
    ``triggered`` -> ``record_execution_start``; terminal statuses ->
    ``record_execution_complete``.  EXTENDED with ``deferred`` so the
    DB row lands whether the post-fix implementation writes the row via
    the callback OR via direct ``source_repo`` calls (plan C1 writes it
    directly; this mirror makes both routes observable)."""

    def _cb(
        execution_id: str,
        schedule_id: str,
        status: str,
        instance_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        try:
            if status == "triggered":
                repo.record_execution_start(
                    schedule_id=schedule_id,
                    instance_id=instance_id,
                    execution_id=execution_id,
                )
            elif status in ("completed", "failed", "skipped", "queued", "deferred"):
                repo.record_execution_complete(
                    execution_id=execution_id,
                    status=status,
                    error_message=error_message,
                )
        except Exception:  # noqa: BLE001 — audit mirror never breaks the fire
            pass

    return _cb


def _build_pin_adapter(
    pin_manager,
    pin_engine: Engine,
    *,
    source_id: str,
    run_at: datetime | None = None,
    interval_seconds: int | None = None,
    instance_mode: str = "reuse_instance",
    message: str = "pin alarm payload",
    max_concurrent: int = 1,
    execution_events: list | None = None,
    track_completion: bool = False,
):
    """Build a REAL ``SchedulerAdapter`` wired to the real repositories
    (lineage: probe ``_build_scheduler_adapter``).  Returns ``(adapter,
    execution_events, completion_events)``."""
    from daemon.sources.adapters.scheduler import SchedulerAdapter
    from daemon.sources.base import SourceConfig as AdapterSourceConfig

    cfg = AdapterSourceConfig(
        source_id=source_id,
        source_type="scheduler",
        name=f"self-alarm:{PIN_TAG}",
        config={
            "instance_mode": instance_mode,
            "agent": "developer",
            "message": message,
            "max_concurrent": max_concurrent,
            **({"run_at": run_at.isoformat()} if run_at is not None else {}),
            **(
                {"interval_seconds": interval_seconds}
                if interval_seconds is not None
                else {}
            ),
        },
        credentials={},
        enabled=True,
    )

    source_repo = pin_manager._source_repository
    completion_events: list = []

    def _execution_callback(
        execution_id: str,
        schedule_id: str,
        status: str,
        instance_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if execution_events is not None:
            execution_events.append(
                {
                    "execution_id": execution_id,
                    "schedule_id": schedule_id,
                    "status": status,
                    "instance_id": instance_id,
                    "error_message": error_message,
                }
            )
        # Production DB-write mirror (registry.py:476-502 + deferred).
        _db_execution_callback(source_repo)(
            execution_id, schedule_id, status, instance_id, error_message
        )

    adapter = SchedulerAdapter(
        cfg,
        on_message=_noop_on_message,
        execution_callback=_execution_callback,
        on_complete_callback=_production_on_complete(source_repo, source_id),
        job_queue_service=pin_manager._job_queue_service,
        source_repo=source_repo,
        instance_repo=pin_manager._instance_repository,
        manager=pin_manager,
    )
    # ``start()`` creates the semaphore; the pins drive the loop directly.
    adapter._execution_semaphore = asyncio.Semaphore(max_concurrent)

    if track_completion:
        original_on_complete = adapter._on_complete_callback

        def _tracking_on_complete(sid: str, completed: bool) -> None:
            completion_events.append((sid, completed))
            original_on_complete(sid, completed)

        adapter._on_complete_callback = _tracking_on_complete

    return adapter, execution_events if execution_events is not None else [], completion_events


async def _noop_on_message(_msg) -> None:
    return None


async def _fire_once(adapter, timeout_s: float = 8.0) -> None:
    """Drive the REAL ``_run_schedule`` loop once for a one-time schedule.

    At base the loop fires, consumes the one-shot and breaks (returns).
    Post-fix some legs legitimately keep the loop alive (the mandated 5s
    re-arm cadence) — the timeout is the harness's stop mechanism and is
    swallowed BY DESIGN; the pins assert on DB rows afterwards.
    """
    try:
        await asyncio.wait_for(adapter._run_schedule(), timeout=timeout_s)
    except asyncio.TimeoutError:
        pass


async def _fire_emit(adapter) -> None:
    """Drive ONE fire through the existing ``_emit_scheduled_message``
    entry point (the busy-branch site, ``scheduler.py:1182-1215``) —
    used by the multi-fire pins (coalescing, predicate, U4, run-counter,
    marker-miss)."""
    await asyncio.wait_for(adapter._emit_scheduled_message(), timeout=10.0)


# ===========================================================================
# PIN 1 — TestBusyCallerDefersDelivery  (plan §(e) pin #1, D1)
# ===========================================================================


class TestBusyCallerDefersDelivery:
    """PIN 1 (RED at base).

    RED MUTATION (plan §(e) pin #1, verbatim): "Restore the old busy
    branch: replace `_defer_delivery` with the SKIPPED callback + bare
    `return` (`:1204-1215`)" — RED symptom: no JobItem/Task for the bound
    instance, no `deferred` execution row; the L-F legs stay RED while
    the INFO-level skip record exists and no WARNING is emitted.

    Required POST-FIX behaviour: firing a reuse-mode one-shot at a BUSY
    caller DEFERS the delivery instead of dropping it — exactly one
    PENDING Task for the pinned caller, one MessageQueue row, one
    JobItem, and one ``schedule_executions`` row with
    ``status='deferred'``.  L-F (DEC-4): the deferral is logged at
    WARNING and the INFO-level "Skipping scheduled execution" record is
    GONE from the scheduled path.
    """

    async def test_busy_caller_defers_instead_of_dropping(
        self, pin_manager, pin_engine: Engine, caplog
    ):
        source_id = f"{PIN_TAG}-p1-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_id)  # RUNNING = busy
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )

        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id, run_at=run_at
        )

        with caplog.at_level(logging.INFO, logger=SCHED_LOGGER):
            await _fire_once(adapter, timeout_s=8.0)

        # --- the delivery is DURABLE, not dropped ------------------------
        # LIVENESS (B3): the delivery is live when its MQ row is in
        # ('ready','retrying') — the W9 guard.  The Task is asserted
        # NON-TERMINAL (a PENDING-only assertion would fail a correct
        # implementation inside the claim window).
        live_tasks = [
            t for t in _tasks_for(pin_engine, instance_id)
            if t.status not in TERMINAL_TASK_STATUSES
        ]
        assert len(live_tasks) == 1, (
            "PIN 1 RED: no live (non-terminal) Task exists for the busy "
            "caller — the alarm was dropped "
            f"(tasks={_tasks_for(pin_engine, instance_id)})"
        )
        mqs = _mq_for(pin_engine, instance_id)
        assert len(mqs) == 1, (
            f"PIN 1 RED: expected exactly 1 MessageQueue row, got {len(mqs)}"
        )
        assert mqs[0].status in LIVE_MQ_STATUSES, (
            f"PIN 1 RED: the deferred delivery's MessageQueue row is not in "
            f"{LIVE_MQ_STATUSES} (status={mqs[0].status!r}) — the W9 "
            "liveness guard"
        )
        jobs = _jobitems_for(pin_engine, instance_id)
        assert len(jobs) == 1, (
            f"PIN 1 RED: expected exactly 1 JobItem, got {len(jobs)}"
        )

        # --- the deferral is AUDITABLE ----------------------------------
        rows = _schedule_executions(pin_engine, source_id)
        deferred_rows = [r for r in rows if r.status == "deferred"]
        assert len(deferred_rows) == 1, (
            "PIN 1 RED: expected exactly one schedule_executions row with "
            f"status='deferred', got rows={[(r.status, r.error_message) for r in rows]}"
        )

        # --- L-F (DEC-4): WARNING-level deferral, no INFO skip record ----
        sched_records = [r for r in caplog.records if r.name == SCHED_LOGGER]
        assert not any(
            "Skipping scheduled execution" in r.getMessage() for r in sched_records
        ), (
            "PIN 1 RED (L-F): the busy branch still logs the INFO-level "
            "'Skipping scheduled execution' record — the log raise to "
            "warning is absent"
        )
        assert any(r.levelname == "WARNING" for r in sched_records), (
            "PIN 1 RED (L-F): no WARNING-level record was emitted by the "
            "scheduler logger during the deferral fire "
            f"(records={[(r.levelname, r.getMessage()[:80]) for r in sched_records]})"
        )


# ===========================================================================
# PIN 2 — TestExecutionRowOnDefer  (plan §(e) pin #2, D2 + the L-1 sibling)
# ===========================================================================


class TestExecutionRowOnDefer:
    """PIN 2 + L-1 sibling (RED at base).

    RED MUTATION (plan §(e) pin #2, verbatim): "Swap to the enqueue-first
    order (enqueue → `record_execution_start` → complete) — i.e. undo the
    L-1 row-first invariant" — RED symptom: the enqueue-failure leg leaves
    a `deferred`-asserting row with nothing pending (the audit-trail lie)
    and/or the order assertion fails.

    Required POST-FIX behaviour: every deferral writes a durable
    ``schedule_executions`` audit row (2-call start->complete with
    ``status='deferred'``, ``execution_id`` prefixed ``deferred-``,
    ``completed_at`` set), and the write order is ROW-FIRST (L-1:
    record_execution_start BEFORE enqueue_message_job), with the
    enqueue-failure leg completing the row FAILED and never consuming
    the one-shot.
    """

    async def test_deferral_writes_a_deferred_schedule_executions_row(
        self, pin_manager, pin_engine: Engine
    ):
        source_id = f"{PIN_TAG}-p2-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_id)
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )

        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id, run_at=run_at
        )

        await _fire_once(adapter, timeout_s=8.0)

        rows = _schedule_executions(pin_engine, source_id)
        assert len(rows) == 1, (
            "PIN 2 RED: expected exactly one schedule_executions row for the "
            f"deferred fire, got {len(rows)} rows="
            f"{[(r.status, r.error_message) for r in rows]}"
        )
        row = rows[0]
        assert row.status == "deferred", (
            f"PIN 2 RED: row status is {row.status!r}, expected 'deferred'"
        )
        assert row.execution_id.startswith("deferred-"), (
            f"PIN 2 RED: execution_id {row.execution_id!r} does not carry the "
            "mandatory 'deferred-' prefix"
        )
        assert row.completed_at is not None, (
            "PIN 2 RED: the deferred row has no completed_at — the "
            "start->complete pair did not land"
        )
        assert row.instance_id == instance_id, (
            f"PIN 2 RED: row instance_id {row.instance_id!r} != pinned caller "
            f"{instance_id!r}"
        )

    async def test_row_write_order_is_row_first_and_failure_leg_is_terminal(
        self, pin_manager, pin_engine: Engine
    ):
        # ---------- leg A: the L-1 ordering invariant (row-first) --------
        source_id = f"{PIN_TAG}-p3a-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_id)
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )

        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id, run_at=run_at
        )

        # Recording harness: wrap the two seams the L-1 invariant orders.
        order: list[str] = []
        source_repo = pin_manager._source_repository
        real_start = source_repo.record_execution_start

        def _recording_start(*args, **kwargs):
            order.append("record_execution_start")
            return real_start(*args, **kwargs)

        source_repo.record_execution_start = _recording_start  # type: ignore[method-assign]
        real_enqueue = pin_manager.enqueue_message_job

        async def _recording_enqueue(*args, **kwargs):
            order.append("enqueue_message_job")
            return await real_enqueue(*args, **kwargs)

        pin_manager.enqueue_message_job = _recording_enqueue  # type: ignore[method-assign]
        try:
            await _fire_once(adapter, timeout_s=8.0)
        finally:
            source_repo.record_execution_start = real_start  # type: ignore[method-assign]
            pin_manager.enqueue_message_job = real_enqueue  # type: ignore[method-assign]

        assert order == ["record_execution_start", "enqueue_message_job"], (
            "PIN 3 RED (L-1): the row-first invariant is violated — expected "
            f"record_execution_start BEFORE enqueue_message_job, got order={order} "
            "(at base NEITHER runs: the busy fire is dropped with zero rows)"
        )
        rows = _schedule_executions(pin_engine, source_id)
        assert rows and all(
            r.status != "triggered" for r in rows
        ), (
            "PIN 3 RED: an unfinished 'triggered' row was left behind after a "
            f"completed fire: {[(r.status, r.execution_id) for r in rows]}"
        )

        # ---------- leg B: the enqueue-failure leg is terminal -----------
        source_id_b = f"{PIN_TAG}-p3b-{uuid.uuid4().hex[:8]}"
        instance_b = str(uuid.uuid4())
        run_at_b = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_b)
        _seed_source_and_mapping(
            pin_engine, source_id=source_id_b, instance_id=instance_b, run_at=run_at_b
        )

        adapter_b, _events_b, _completions_b = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id_b, run_at=run_at_b
        )

        async def _exploding_enqueue(*args, **kwargs):
            raise RuntimeError("pin harness: enqueue_message_job exploded")

        pin_manager.enqueue_message_job = _exploding_enqueue  # type: ignore[method-assign]
        try:
            await _fire_once(adapter_b, timeout_s=8.0)
        finally:
            pin_manager.enqueue_message_job = real_enqueue  # type: ignore[method-assign]

        rows_b = _schedule_executions(pin_engine, source_id_b)
        assert rows_b, (
            "PIN 3 RED (leg B): the enqueue-failure leg wrote NO execution row "
            "— the audit trail is silent on a failed dispatch"
        )
        assert all(r.status == "failed" for r in rows_b), (
            "PIN 3 RED (leg B): the enqueue-failure leg did not complete its "
            f"row to 'failed': {[(r.status, r.error_message) for r in rows_b]}"
        )
        row_b_row = _source_row(pin_engine, source_id_b)
        assert row_b_row is not None
        assert row_b_row.enabled is True and row_b_row.status == "running", (
            "PIN 3 RED (leg B): the one-shot WAS consumed on an enqueue failure "
            f"(enabled={row_b_row.enabled}, status={row_b_row.status!r}) — L-1 "
            "consequence (a) forbids consumption on the failure leg"
        )
        assert adapter_b._is_one_time_executed is False, (
            "PIN 3 RED (leg B): _is_one_time_executed is True after an enqueue "
            "failure — the intent was destroyed"
        )


# ===========================================================================
# PIN 3 (commission (c)) — TestIdempotencyCoalescing  (plan §(e) pin #3, D3)
# ===========================================================================


class TestIdempotencyCoalescing:
    """PIN 4 (RED at base).

    RED MUTATION (plan §(e) pin #3, verbatim): "Under ADR-1 the teardown
    mutation is gone. Either (a) remove the `status='ready'` guard from
    `update_ready_content` (unguarded content rewrite) or (b) make the
    coalescing path re-enqueue instead of updating in place" — RED
    symptom: (a) the guarded update lands on a row already
    claimed/processing (the delivered body is rewritten mid-claim) / (b) N
    live JobItems / N PENDING Tasks instead of 1.  Blocking-4 saturation
    leg: "with N > 16 busy fires the guarded update keeps coalescing into
    the SAME delivery — the count is pinned at the cap and the body
    carries '16+' (a saturating display — not min(count+1, cap), which
    would write '16'); there is still exactly one live JobItem and one
    PENDING Task, and no second enqueue (the `count < cap` gate is
    REMOVED from C1's coalescing branch — a saturated generation can
    never fall through to a fresh enqueue while a delivery is pending)".

    B3 CLAIMED-DELIVERY LEG [LEADER-RULED — iter-003 B3, adopted
    2026-10-21]: the coalescing gate's Task condition widens from
    ``pending`` to NON-TERMINAL, because the Task flips to RUNNING at
    claim (``task/repository.py:2801-2804``) BEFORE the message_queue row
    leaves 'ready' — a PENDING-only gate fails in that window and falls
    through to a gen-bump + a SECOND live enqueue (W9's forbidden
    outcome, an at-most-once breach, the count resets to 1).
    PRECEDENCE (B3, explicit): coalescing LIVENESS = the MQ status guard
    (``status IN ('ready','retrying')`` — that IS the live-delivery
    test); the Task check is the STRANDED-DELIVERY test, used at ENQUEUE
    time for durable evidence, and THERE the predicate stays VERBATIM
    (PENDING, per-delivery, message_id-matched, guarded) — it is NOT
    widened.  The claimed-delivery leg is carried by the sibling
    ``::test_claimed_delivery_still_coalesces_instead_of_opening_a_second_delivery``.

    Required POST-FIX behaviour: N busy fires coalesce into ONE live
    delivery carrying the LIVE count.  Coalescing = enqueue-once + a
    SINGLE-STATEMENT guarded in-place update
    (``WHERE message_id=:id AND status IN ('ready','retrying')`` — the
    W9-widened guard).  Blocking 4: the ``count < cap`` gate is REMOVED —
    saturation (N > 16) keeps coalescing into the SAME delivery with the
    count pinned at the cap and the body carrying the saturating "16+"
    display (NOT ``min(count+1, cap)`` which would write "16").
    """

    async def test_n_busy_fires_coalesce_into_one_delivery_with_live_count(
        self, pin_manager, pin_engine: Engine
    ):
        source_id = f"{PIN_TAG}-p4-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)
        key = _expected_run_at_key(source_id, run_at)

        _seed_instance(pin_engine, instance_id=instance_id)
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )

        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id, run_at=run_at
        )

        for _ in range(3):
            await _fire_emit(adapter)

        jobs = _jobitems_with_key(pin_engine, key)
        assert len(jobs) == 1, (
            "PIN 4 RED: expected exactly ONE live JobItem under the run_at "
            f"key after 3 busy fires, got {len(jobs)} "
            f"(all jobitems={len(_jobitems_for(pin_engine, instance_id))}) — "
            "at base the fires are dropped entirely (0 deliveries)"
        )
        live_tasks = [
            t for t in _tasks_for(pin_engine, instance_id)
            if t.status not in TERMINAL_TASK_STATUSES
        ]
        assert len(live_tasks) == 1, (
            f"PIN 4 RED: expected exactly 1 live (non-terminal) Task, "
            f"got {len(live_tasks)}"
        )
        mqs = _mq_for(pin_engine, instance_id)
        assert len(mqs) == 1, (
            f"PIN 4 RED: expected exactly 1 MessageQueue row, got {len(mqs)}"
        )
        assert mqs[0].status in LIVE_MQ_STATUSES, (
            f"PIN 4 RED: the single delivery's MessageQueue row is not in "
            f"{LIVE_MQ_STATUSES} (status={mqs[0].status!r})"
        )
        assert "3 occurrences" in (mqs[0].content or ""), (
            "PIN 4 RED: the delivered body does not carry the live count "
            f"'3 occurrences' (content={mqs[0].content!r})"
        )
        cfg = _config_of(pin_engine, source_id)
        assert cfg.get("_defer_count") == 3, (
            f"PIN 4 RED: config['_defer_count']={cfg.get('_defer_count')!r}, "
            "expected 3 (the authoritative counter committed to config)"
        )

        # ---------- Blocking 4: the saturation leg (N > cap) -------------
        for _ in range(DEFER_COUNT_CAP + 1):  # 14 more fires -> 17 total
            await _fire_emit(adapter)

        jobs_after = _jobitems_with_key(pin_engine, key)
        assert len(jobs_after) == 1, (
            "PIN 4 RED (Blocking 4): saturation fell through to a SECOND "
            f"enqueue — {len(jobs_after)} JobItems under the key after "
            f"{DEFER_COUNT_CAP + 1} busy fires (the 'count < cap' gate was "
            "not removed)"
        )
        mqs_after = _mq_for(pin_engine, instance_id)
        assert len(mqs_after) == 1, (
            f"PIN 4 RED (Blocking 4): expected still exactly 1 MessageQueue "
            f"row, got {len(mqs_after)}"
        )
        assert "16+" in (mqs_after[0].content or ""), (
            "PIN 4 RED (Blocking 4): the saturated body does not carry the "
            "saturating display '16+' "
            f"(content={mqs_after[0].content!r} — min(count+1, cap) writes "
            "'16' instead)"
        )
        cfg_after = _config_of(pin_engine, source_id)
        assert cfg_after.get("_defer_count") == DEFER_COUNT_CAP, (
            f"PIN 4 RED (Blocking 4): saturated _defer_count="
            f"{cfg_after.get('_defer_count')!r}, expected pinned at "
            f"{DEFER_COUNT_CAP}"
        )
        assert len(_jobitems_for(pin_engine, instance_id)) == 1, (
            "PIN 4 RED (Blocking 4): more than one live JobItem exists after "
            "saturation — a second delivery was opened"
        )

    async def test_claimed_delivery_still_coalesces_instead_of_opening_a_second_delivery(
        self, pin_manager, pin_engine: Engine
    ):
        """B3 CLAIMED-DELIVERY LEG [LEADER-RULED — iter-003 B3] (RED at base).

        RED MUTATION (plan §(e) row 3, B3 amendment, verbatim): "the gate
        requires PENDING" — the coalescing gate keeps the Task condition at
        PENDING while the delivery is CLAIMED (Task RUNNING, MQ row still
        'ready'/'retrying') → the gate fails → gen-bump + a SECOND live
        enqueue (W9's forbidden outcome, an at-most-once breach, the count
        resets to 1).

        PRECEDENCE (B3, explicit in the plan): coalescing LIVENESS = the MQ
        status guard (``status IN ('ready','retrying')``) — that IS the
        live-delivery test.  The Task check is the STRANDED-DELIVERY test,
        used at ENQUEUE time for durable evidence, and THERE the predicate
        stays VERBATIM (PENDING, per-delivery, message_id-matched,
        guarded) — it is NOT widened here.  Consequence asserted by this
        leg: a delivery caught in the claim window (Task RUNNING, MQ row
        still 'ready') MUST still coalesce — no second enqueue, no
        generation bump, the count rides the KEPT delivery.
        """
        source_id = f"{PIN_TAG}-p4c-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)
        key = _expected_run_at_key(source_id, run_at)

        _seed_instance(pin_engine, instance_id=instance_id)
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )

        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id, run_at=run_at
        )

        # ---- fire 1: the deferred delivery is committed -----------------
        await _fire_emit(adapter)

        jobs1 = _jobitems_with_key(pin_engine, key)
        assert len(jobs1) == 1, (
            "PIN 4 RED (claimed leg): fire 1 produced NO committed delivery "
            f"to sit in the claim window (jobs under key: {len(jobs1)}) — "
            "at base the busy fire is dropped"
        )
        live1 = [
            t for t in _tasks_for(pin_engine, instance_id)
            if t.status not in TERMINAL_TASK_STATUSES
        ]
        assert len(live1) == 1, (
            f"PIN 4 RED (claimed leg): expected 1 live Task after fire 1, "
            f"got {len(live1)}"
        )
        mq1 = _mq_for(pin_engine, instance_id)
        assert len(mq1) == 1 and mq1[0].status in LIVE_MQ_STATUSES, (
            f"PIN 4 RED (claimed leg): expected 1 live MessageQueue row, got "
            f"{[(m.message_id, m.status) for m in mq1]}"
        )

        # ---- the CLAIM WINDOW ------------------------------------------
        # The Task flips to RUNNING at claim (task/repository.py:2801-2804)
        # BEFORE the message_queue row leaves 'ready' (pipeline :464→:678).
        _set_task_status(
            pin_engine, live1[0].id, TaskStatus.RUNNING.value,
            worker_id="pin-claim-window",
        )
        _set_job_admission(
            pin_engine, jobs1[0].job_id, AdmissionState.ACTIVE.value
        )
        with Session(pin_engine) as session:
            row = session.get(MessageQueue, mq1[0].message_id)
            assert row is not None
            assert row.status == "ready", (
                "PIN 4 RED (claimed leg): the MQ row left 'ready' before the "
                f"claim window was planted (status={row.status!r})"
            )

        # ---- fire 2: must STILL coalesce into the SAME delivery --------
        await _fire_emit(adapter)

        jobs2 = _jobitems_with_key(pin_engine, key)
        assert len(jobs2) == 1, (
            "PIN 4 RED (claimed leg): the claim window opened a SECOND live "
            f"delivery ({len(jobs2)} JobItems under the key) — the coalescing "
            "gate still requires a PENDING Task (B3 mutation: 'the gate "
            "requires PENDING')"
        )
        cfg2 = _config_of(pin_engine, source_id)
        assert cfg2.get("_defer_gen") == 1, (
            "PIN 4 RED (claimed leg): the claim window bumped the generation "
            f"(_defer_gen={cfg2.get('_defer_gen')!r}, expected 1) — a "
            "gen-bump + fresh enqueue is the forbidden second-delivery shape"
        )
        mq2 = _mq_for(pin_engine, instance_id)
        assert len(mq2) == 1, (
            f"PIN 4 RED (claimed leg): expected still exactly 1 MessageQueue "
            f"row, got {len(mq2)}"
        )
        assert mq2[0].message_id == mq1[0].message_id, (
            "PIN 4 RED (claimed leg): the coalesce landed on a DIFFERENT "
            f"MessageQueue row ({mq2[0].message_id!r} != {mq1[0].message_id!r})"
        )
        assert "2 occurrences" in (mq2[0].content or ""), (
            "PIN 4 RED (claimed leg): the count did not ride the KEPT "
            f"delivery (content={mq2[0].content!r}) — the claim window reset "
            "the count to 1"
        )
        assert cfg2.get("_defer_count") == 2, (
            f"PIN 4 RED (claimed leg): _defer_count="
            f"{cfg2.get('_defer_count')!r}, expected 2"
        )
        live2 = [
            t for t in _tasks_for(pin_engine, instance_id)
            if t.status not in TERMINAL_TASK_STATUSES
        ]
        assert len(live2) == 1, (
            f"PIN 4 RED (claimed leg): expected still exactly 1 live Task, "
            f"got {len(live2)} — a second Task means a second delivery"
        )

        # ---- the 'retrying' half of the W9 guard, in the claim window ---
        with Session(pin_engine) as session:
            row = session.get(MessageQueue, mq1[0].message_id)
            assert row is not None
            row.status = "retrying"
            session.add(row)
            session.commit()

        await _fire_emit(adapter)

        jobs3 = _jobitems_with_key(pin_engine, key)
        assert len(jobs3) == 1, (
            "PIN 4 RED (claimed leg, retrying): a rollover against a "
            f"RETRYING row opened a second live delivery ({len(jobs3)} "
            "JobItems) — W9's forbidden outcome"
        )
        cfg3 = _config_of(pin_engine, source_id)
        assert cfg3.get("_defer_gen") == 1, (
            "PIN 4 RED (claimed leg, retrying): the generation was bumped "
            f"(_defer_gen={cfg3.get('_defer_gen')!r}, expected 1)"
        )
        mq3 = _mq_for(pin_engine, instance_id)
        assert len(mq3) == 1 and mq3[0].message_id == mq1[0].message_id, (
            "PIN 4 RED (claimed leg, retrying): the retrying row was not the "
            "coalescing target"
        )
        assert "3 occurrences" in (mq3[0].content or ""), (
            "PIN 4 RED (claimed leg, retrying): the count did not advance on "
            f"the kept delivery (content={mq3[0].content!r})"
        )


# ===========================================================================
# PIN 4 (commission (d)) — TestOneShotDurability  (plan §(e) pin #4, ADR-3)
# ===========================================================================


class TestOneShotDurability:
    """PIN 5 (RED at base).

    RED MUTATION (plan §(e) pin #4, verbatim): "W3 (restated
    deterministically): restore today's unconditional post-`_emit`
    consumption (`scheduler.py:620-632` — `_is_one_time_executed = True`
    at `:624` + the `on_complete_callback`) with the deferral enqueue
    suppressed → the one-shot is consumed while no durable delivery
    exists" — RED symptom: after the one-shot fires against a busy caller
    the source row is `stopped`/`enabled=False` with no PENDING Task →
    consumed-and-lost.  Sibling (ADR-3): the no-target branch consumes
    (or consumes silently) → the enabled/`_is_one_time_executed`/DEFERRED-
    row/`_defer_since` assertions fail.

    Required POST-FIX behaviour: a one-shot fired at a busy caller is
    consumed (deferred_durable) BUT a durable pending delivery remains —
    the schedule is stopped while a PENDING Task + a 'deferred' row
    survive.  Sibling (ADR-3): the semaphore NO-TARGET branch re-arms —
    the source stays enabled, ``_is_one_time_executed`` stays False, one
    DEFERRED row is written (start->complete), no delivery exists, and
    the non-delivery episode carrier ``_defer_since`` is set.
    """

    async def test_one_shot_alarm_leaves_a_durable_pending_delivery(
        self, pin_manager, pin_engine: Engine
    ):
        source_id = f"{PIN_TAG}-p5-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_id)
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )

        adapter, _events, completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id, run_at=run_at,
            track_completion=True,
        )

        await _fire_once(adapter, timeout_s=8.0)

        # ENQUEUE-TIME durable evidence (DEC-9 / Blocking 1): the predicate
        # stays VERBATIM PENDING (per-delivery, message_id-matched) — the
        # B3 widening applies ONLY to the coalescing liveness gate, never
        # to this check.
        pending_tasks = [
            t for t in _tasks_for(pin_engine, instance_id)
            if t.status == TaskStatus.PENDING.value
        ]
        assert len(pending_tasks) == 1, (
            "PIN 5 RED: the one-shot fired at a busy caller left NO durable "
            f"pending delivery (pending tasks={pending_tasks}) — consumed-"
            "and-lost, the original defect"
        )
        deferred_rows = [
            r for r in _schedule_executions(pin_engine, source_id)
            if r.status == "deferred"
        ]
        assert len(deferred_rows) == 1, (
            "PIN 5 RED: expected exactly one 'deferred' audit row to survive "
            f"the consumption, got {deferred_rows}"
        )
        row = _source_row(pin_engine, source_id)
        assert row is not None
        assert row.status == "stopped" and row.enabled is False, (
            f"PIN 5 RED: expected the one-shot consumed (stopped/disabled), "
            f"got status={row.status!r} enabled={row.enabled}"
        )
        assert completions, "PIN 5: the on_complete callback never fired"
        assert adapter._is_one_time_executed is True

    async def test_semaphore_no_target_rearm_leaves_the_one_shot_armed(
        self, pin_manager, pin_engine: Engine
    ):
        """Sibling (ADR-3 + DEC-22): the semaphore-timeout branch with NO
        deferrable target (new_instance) RE-ARMS — never consumes
        silently; one DEFERRED row is written; ``_defer_since`` is set."""
        source_id = f"{PIN_TAG}-p5b-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_id)
        # new_instance mode, WITH a mapping target — the deferrable-target
        # test is mode+mapping based; new_instance = no deferrable target
        # (plan C2 branch 2) even though the mapping row exists.
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id,
            run_at=run_at, instance_mode="new_instance",
        )

        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id, run_at=run_at,
            instance_mode="new_instance",
        )

        # Exhaust the execution semaphore -> the acquire times out -> the
        # exact branch under test (_acquire_execution_slot timeout).
        assert adapter._execution_semaphore is not None
        await adapter._execution_semaphore.acquire()

        # 3.0s: base consumes+breaks at ~1.05s; post-fix the re-arm branch
        # writes its DEFERRED row at ~1.2s and then sits in the mandated
        # 5s sleep — the timeout is the harness's stop.
        await _fire_once(adapter, timeout_s=3.0)

        row = _source_row(pin_engine, source_id)
        assert row is not None
        assert row.enabled is True and row.status == "running", (
            "PIN 5 RED (sibling): the no-target re-arm leg CONSUMED the "
            f"one-shot (enabled={row.enabled}, status={row.status!r}) — "
            "silent consumption, ADR-3 violation"
        )
        assert adapter._is_one_time_executed is False, (
            "PIN 5 RED (sibling): _is_one_time_executed is True on the "
            "re-arm leg"
        )
        deferred_rows = [
            r for r in _schedule_executions(pin_engine, source_id)
            if r.status == "deferred"
        ]
        assert len(deferred_rows) == 1, (
            "PIN 5 RED (sibling): expected exactly one DEFERRED row from the "
            f"re-arm branch (start->complete), got {deferred_rows}"
        )
        assert deferred_rows and deferred_rows[0].error_message, (
            "PIN 5 RED (sibling): the re-arm DEFERRED row carries no reason"
        )
        assert _jobitems_for(pin_engine, instance_id) == [], (
            "PIN 5 RED (sibling): the re-arm leg produced a delivery"
        )
        cfg = _config_of(pin_engine, source_id)
        assert cfg.get("_defer_since") is not None, (
            "PIN 5 RED (sibling): the non-delivery episode carrier "
            "_defer_since was not set at the first re-arm (DEC-22)"
        )


# ===========================================================================
# PIN 5 (commission (e)) — TestTasklessEnqueue  (plan §(e) pin #7, Blocking 1)
# ===========================================================================


class TestTasklessEnqueue:
    """PIN 6 (RED at base).

    RED MUTATION (plan §(e) pin #7, verbatim): "Classify on `message_id`
    alone (the pre-Blocking-1 behaviour) — treat the enqueue as
    `deferred_durable` because it returned a real id, with no
    Task-existence check" — RED symptom: the one-shot is consumed,
    `_defer_message_id` is persisted, and a stranded `ready` MQ row with
    no PENDING Task pins the generation → the recurring alarm absorbs
    every later busy fire into a dead delivery.  DECOY leg (Blocking
    1(e)): "an unrelated PENDING task for the SAME instance (a different
    message_id) must NOT satisfy the predicate — the enqueue still
    classifies `taskless_enqueue`."

    Required POST-FIX behaviour: an enqueue that returns a real
    ``message_id`` but leaves NO PENDING Task whose ``message_id`` EQUALS
    that id (the question-pause marker window) is classified ``failed``
    with reason ``taskless_enqueue`` — NEVER ``deferred_durable``.  The
    leg: best-effort MQ cleanup by message_id, no one-shot consumption,
    re-arm, no JobItem (the key stays unoccupied).  DECOY leg
    (Blocking 1(e)): an UNRELATED PENDING task on the SAME instance must
    NOT satisfy the per-delivery predicate.
    """

    async def test_marker_window_enqueue_without_task_is_failed_not_deferred_durable(
        self, pin_manager, pin_engine: Engine
    ):
        source_id = f"{PIN_TAG}-p6-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)
        key = _expected_run_at_key(source_id, run_at)

        _seed_instance(pin_engine, instance_id=instance_id)  # RUNNING (busy)
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )

        # DECOY (Blocking 1(e)): an UNRELATED PENDING task on the SAME
        # instance, carrying a DIFFERENT message_id.  A per-instance
        # predicate would wrongly accept it as durable evidence.
        decoy = _seed_task(
            pin_engine, instance_id=instance_id,
            status=TaskStatus.PENDING.value, message_id=str(uuid.uuid4()),
        )

        # The marker window: the RAM marker makes _prepare_enqueued_message
        # SKIP Task+JobItem creation while the DB still says RUNNING
        # (instance_messaging.py:2301-2309 / :3000-3030).
        marker_set = getattr(pin_manager, "_deferred_question_pause")
        marker_set.add(instance_id)
        try:
            adapter, _events, _completions = _build_pin_adapter(
                pin_manager, pin_engine, source_id=source_id, run_at=run_at
            )
            # 8.0s: base consumes+breaks at ~1.3s; post-fix the leg re-arms
            # (mandated 5s cadence) — the timeout is the harness stop.
            await _fire_once(adapter, timeout_s=8.0)
        finally:
            marker_set.discard(instance_id)

        # --- classified failed/taskless_enqueue, never deferred_durable --
        rows = _schedule_executions(pin_engine, source_id)
        failed_rows = [r for r in rows if r.status == "failed"]
        assert failed_rows, (
            "PIN 6 RED: no 'failed' execution row was written for the "
            f"marker-window fire (rows={[(r.status, r.error_message) for r in rows]}) "
            "— at base the fire is dropped with zero rows"
        )
        assert all(
            r.error_message and "taskless_enqueue" in r.error_message
            for r in failed_rows
        ), (
            "PIN 6 RED: the failed row(s) do not carry the reason token "
            f"'taskless_enqueue': {[(r.error_message) for r in failed_rows]}"
        )
        assert not any(r.status == "deferred" for r in rows), (
            "PIN 6 RED: the taskless enqueue was classified 'deferred' "
            "(deferred_durable) — the per-delivery Task-existence predicate "
            "is absent"
        )

        # --- the stranded-generation shape must NOT exist ----------------
        remaining_mq = _mq_for(pin_engine, instance_id)
        assert remaining_mq == [], (
            "PIN 6 RED: a MessageQueue row SURVIVED the taskless fire — the "
            "best-effort cleanup by message_id did not run and the ready row "
            f"now pins the generation: {[m.message_id for m in remaining_mq]}"
        )
        assert _jobitems_for(pin_engine, instance_id) == [], (
            "PIN 6 RED: a JobItem exists for the taskless attempt — a "
            "Task-less enqueue must leave NO JobItem "
            "(instance_messaging.py:3000-3030)"
        )
        assert _jobitems_with_key(pin_engine, key) == [], (
            "PIN 6 RED: the idempotency key is occupied after the taskless "
            "attempt — the key must stay unoccupied (deleted MQ row = sole "
            "residue)"
        )

        # --- the DECOY is untouched and does NOT satisfy the predicate ---
        with Session(pin_engine) as session:
            refreshed = session.get(Task, decoy.id)
            assert refreshed is not None
            assert refreshed.status == TaskStatus.PENDING.value, (
                f"PIN 6 RED: the DECOY task was disturbed ({refreshed.status!r})"
            )
        # ENQUEUE-TIME predicate (DEC-9 / Blocking 1) — PENDING verbatim,
        # per-delivery: the B3 widening applies ONLY to the coalescing
        # liveness gate, never to the stranded-delivery test.
        other_pending = [
            t for t in _tasks_for(pin_engine, instance_id)
            if t.status == TaskStatus.PENDING.value and t.id != decoy.id
        ]
        assert other_pending == [], (
            "PIN 6 RED: a PENDING Task exists for the delivery's message_id — "
            "the taskless classification was bypassed: "
            f"{[(t.work_id, t.message_id) for t in other_pending]}"
        )

        # --- the one-shot is NOT consumed (intent survives; re-arm) ------
        row = _source_row(pin_engine, source_id)
        assert row is not None
        assert row.enabled is True and row.status == "running", (
            "PIN 6 RED: the one-shot WAS consumed on a taskless enqueue "
            f"(enabled={row.enabled}, status={row.status!r}) — no durable "
            "delivery existed"
        )


# ===========================================================================
# PIN 6 (commission (f)) — TestTerminalBypassSafetyNet  (plan U4, ADR-2)
# ===========================================================================


class TestTerminalBypassSafetyNet:
    """PIN 7 (RED at base).

    RED MUTATION (plan §(e) U4 row, verbatim): "a generation-key enqueue
    against a terminal predecessor yields exactly one fresh delivery —
    the bypass is a safety net, never the mechanism" — RED symptom under
    the mutation "re-enqueue under a colliding key while a delivery is
    live" (architect §15.6) or a dropped synthetic-suffix bypass: N live
    JobItems instead of exactly one per generation, and/or the fresh
    JobItem reusing the original key verbatim (the index then blocks or
    freezes the next delivery).

    Required POST-FIX behaviour: the terminal-bypass is a SAFETY NET,
    never the mechanism.  A generation-key enqueue against a TERMINAL
    predecessor yields EXACTLY ONE fresh delivery under a SYNTHETIC key
    (``<key>#<8-hex>`` — the original key is never reused verbatim); a
    TTL-expired predecessor is treated identically; and the adapter's
    generation rollover produces exactly one fresh delivery per
    generation.
    """

    async def test_generation_key_enqueue_against_terminal_predecessor_yields_exactly_one_fresh_delivery(
        self, pin_manager, pin_engine: Engine
    ):
        source_id = f"{PIN_TAG}-p7-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())

        # RECURRING (interval) schedule — rollovers mint generation keys
        # (ADR-2: one-shots keep the run_at key).
        _seed_instance(pin_engine, instance_id=instance_id)
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id,
            interval_seconds=3600,
        )

        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id,
            interval_seconds=3600,
        )

        # ---- generation 1: the deferred delivery ------------------------
        await _fire_emit(adapter)

        k1 = _expected_generation_key(source_id, 1)
        gen1_jobs = _jobitems_with_key(pin_engine, k1)
        assert len(gen1_jobs) == 1, (
            "PIN 7 RED: the first busy fire produced NO generation-keyed "
            f"delivery (jobs under {k1!r}: {len(gen1_jobs)}; all="
            f"{len(_jobitems_for(pin_engine, instance_id))}) — at base the "
            "fire is dropped"
        )
        cfg1 = _config_of(pin_engine, source_id)
        assert cfg1.get("_defer_gen") == 1, (
            f"PIN 7 RED: config['_defer_gen']={cfg1.get('_defer_gen')!r}, "
            "expected 1 after the first deferred fire"
        )

        # ---- simulate the delivery COMPLETING (terminal predecessor) ----
        gen1_job = gen1_jobs[0]
        _set_job_admission(pin_engine, gen1_job.job_id, AdmissionState.DONE.value)
        gen1_tasks = [
            t for t in _tasks_for(pin_engine, instance_id)
            if t.status not in TERMINAL_TASK_STATUSES
        ]
        assert len(gen1_tasks) == 1
        _complete_task_row(pin_engine, gen1_tasks[0].id)
        with Session(pin_engine) as session:
            mq_row = session.get(MessageQueue, gen1_job.job_metadata.get("message_id"))
            if mq_row is not None:
                mq_row.status = "completed"
                session.add(mq_row)
                session.commit()

        # ---- generation 2: rollover -> fresh delivery, exactly once -----
        await _fire_emit(adapter)

        k2 = _expected_generation_key(source_id, 2)
        all_jobs = _jobitems_for(pin_engine, instance_id)
        assert len(all_jobs) == 2, (
            f"PIN 7 RED: expected exactly 2 JobItems after the rollover "
            f"(one per generation), got {len(all_jobs)}"
        )
        gen2_jobs = [j for j in all_jobs if j.idempotency_key != k1]
        assert len(gen2_jobs) == 1, (
            f"PIN 7 RED: expected exactly ONE fresh gen-2 delivery, got "
            f"{len(gen2_jobs)}: {[j.idempotency_key for j in all_jobs]}"
        )
        gen2_key = gen2_jobs[0].idempotency_key
        # [LEADER-RULED — Q2, RATIFIED] The fresh generation key is NEW:
        # the rollover mints ``v{gen+1}``, so the fresh delivery carries
        # exactly that key.  The SYNTHETIC ``<key>#<8hex>`` shape belongs
        # to the substrate's terminal-bypass SAFETY NET (a DIFFERENT leg —
        # see the sibling ``test_terminal_bypass_safety_net_...`` below);
        # the original assertion demanded that shape here, which is
        # unsatisfiable (the synthetic key can only be minted when the
        # very key being enqueued already has a TERMINAL predecessor —
        # and ``v2`` never does: its predecessor sits under ``v1``).
        # The "the original key is never reused verbatim" intent is
        # carried by the existing ``gen2_key != k1`` assertion below.
        assert gen2_key == k2, (
            f"PIN 7 RED: the fresh delivery's key {gen2_key!r} is not the NEW "
            f"generation key {k2!r} — the generation rollover must mint a "
            f"fresh key (the 'the original key is never reused verbatim' "
            f"intent is carried by the gen2_key != k1 assertion)"
        )
        assert gen2_key != k1, "PIN 7: the gen-2 key collides with the gen-1 key"

        live_after = [
            t for t in _tasks_for(pin_engine, instance_id)
            if t.status not in TERMINAL_TASK_STATUSES
        ]
        assert len(live_after) == 1, (
            f"PIN 7 RED: expected exactly 1 live Task after the rollover, "
            f"got {len(live_after)}"
        )
        cfg2 = _config_of(pin_engine, source_id)
        assert cfg2.get("_defer_gen") == 2, (
            f"PIN 7 RED: the generation marker did not roll over exactly once "
            f"(_defer_gen={cfg2.get('_defer_gen')!r}, expected 2 — a breach/"
            "double-bump opens extra generations)"
        )

        # ---- the fresh delivery is claimable (queue-awareness gate) -----
        _set_job_admission(pin_engine, gen2_jobs[0].job_id, AdmissionState.ACTIVE.value)
        claimed = pin_manager._task_repo.claim_pending_task("pin-worker-u4")
        assert claimed is not None and claimed.id == live_after[0].id, (
            "PIN 7 RED: the fresh gen-2 delivery's Task was not claimable "
            f"(claimed={claimed!r}) — the bypass delivery would not actually "
            "be delivered"
        )

        # ---- TTL-expired predecessor: identical synthetic treatment -----
        source_id_ttl = f"{PIN_TAG}-p7ttl-{uuid.uuid4().hex[:8]}"
        instance_ttl = str(uuid.uuid4())
        _seed_instance(pin_engine, instance_id=instance_ttl)
        first = await pin_manager.enqueue_message_job(
            instance_id=instance_ttl,
            message="pin ttl predecessor",
            source="scheduler",
            idempotency_key=_expected_generation_key(source_id_ttl, 1),
        )
        # Age the predecessor past the 24h TTL, keep it NON-terminal: the
        # TTL branch (job_queue_service.py:1433-1442) must bypass exactly
        # like the terminal branch.
        from daemon.repositories.job_queue.models import JobItem

        with Session(pin_engine) as session:
            job = session.get(JobItem, first.job_id)
            assert job is not None
            job.created_at = (
                datetime.now(timezone.utc) - timedelta(hours=72)
            ).isoformat()
            session.add(job)
            session.commit()
        second = await pin_manager.enqueue_message_job(
            instance_id=instance_ttl,
            message="pin ttl retry",
            source="scheduler",
            idempotency_key=_expected_generation_key(source_id_ttl, 1),
        )
        ttl_jobs = _jobitems_with_key(
            pin_engine, _expected_generation_key(source_id_ttl, 1)
        )
        assert len(ttl_jobs) == 1, (
            f"PIN 7 RED (TTL): expected exactly 1 JobItem still under the "
            f"original key, got {len(ttl_jobs)}"
        )
        assert second.job_id != first.job_id, (
            "PIN 7 RED (TTL): the TTL-expired predecessor did NOT bypass — "
            "the second enqueue dedup-hit the stale job instead of creating "
            "a fresh delivery"
        )
        with Session(pin_engine) as session:
            fresh_job = session.get(JobItem, second.job_id)
            assert fresh_job is not None
            assert fresh_job.idempotency_key.startswith(
                _expected_generation_key(source_id_ttl, 1) + "#"
            ), (
                f"PIN 7 RED (TTL): fresh key {fresh_job.idempotency_key!r} is "
                "not the synthetic shape — the original K was reused verbatim"
            )

    async def test_terminal_bypass_safety_net_mints_exactly_one_fresh_delivery(
        self, pin_manager, pin_engine: Engine
    ):
        """U4 SAFETY-NET LEG (the REAL property behind the Q2 correction).

        The generation rollover mints a NEW key (``v{gen+1}`` — the Q2
        correction), so the SYNTHETIC ``<key>#<8hex>`` shape belongs to a
        DIFFERENT leg: the substrate's TERMINAL-BYPASS safety net
        (``job_queue_service.py:1447-1480`` — a terminal predecessor under
        the SAME key does NOT block a fresh delivery; the service mints
        ``f"{key}#{uuid4().hex[:8]}"`` and delivers).

        This leg exercises that property through the REAL adapter path: a
        RECURRING schedule whose generation-1 delivery went TERMINAL, with
        the marker lost (the crash window) — the next fire re-enqueues
        under the CURRENT generation key, hits the terminal predecessor and
        the substrate's safety net mints the synthetic key.

        RED MUTATION SPEC (verbatim): "RED = safety net removed -> the key
        is reused verbatim or no fresh delivery".

        Required POST-FIX behaviour: EXACTLY ONE fresh delivery under
        ``<key>#<8hex>`` — the safety net is a SAFETY NET, never the
        mechanism: the original key is never reused verbatim and the
        delivery is never lost.
        """
        source_id = f"{PIN_TAG}-p7sn-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())

        # RECURRING (interval) schedule — the generation keys are minted
        # for recurring fires (ADR-2).
        _seed_instance(pin_engine, instance_id=instance_id)
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id,
            interval_seconds=3600,
        )

        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id,
            interval_seconds=3600,
        )

        # ---- generation 1: the deferred delivery ------------------------
        await _fire_emit(adapter)

        k1 = _expected_generation_key(source_id, 1)
        gen1_jobs = _jobitems_with_key(pin_engine, k1)
        assert len(gen1_jobs) == 1, (
            "PIN 7 (safety net) RED: the first busy fire produced NO "
            f"generation-keyed delivery (jobs under {k1!r}: {len(gen1_jobs)})"
        )
        gen1_job = gen1_jobs[0]

        # ---- the generation-1 delivery goes TERMINAL ---------------------
        _set_job_admission(pin_engine, gen1_job.job_id, AdmissionState.DONE.value)
        gen1_tasks = [
            t for t in _tasks_for(pin_engine, instance_id)
            if t.status not in TERMINAL_TASK_STATUSES
        ]
        assert len(gen1_tasks) == 1
        _complete_task_row(pin_engine, gen1_tasks[0].id)
        with Session(pin_engine) as session:
            mq_row = session.get(MessageQueue, gen1_job.job_metadata.get("message_id"))
            if mq_row is not None:
                mq_row.status = "completed"
                session.add(mq_row)
                session.commit()

        # ---- the marker is LOST (the crash window) -----------------------
        def _strip_marker(cfg: dict) -> None:
            cfg.pop("_defer_message_id", None)

        _edit_source_config(pin_engine, source_id, _strip_marker)

        # ---- the next fire re-enqueues under the SAME key ----------------
        # The generation-1 key now has a TERMINAL predecessor → the
        # substrate's safety net mints the synthetic key.
        await _fire_emit(adapter)

        all_jobs = _jobitems_for(pin_engine, instance_id)
        fresh = [j for j in all_jobs if j.idempotency_key != k1]
        assert len(fresh) == 1, (
            "PIN 7 (safety net) RED: expected exactly ONE fresh delivery under "
            f"the synthetic key, got {len(fresh)}: "
            f"{[j.idempotency_key for j in all_jobs]} — the safety net must "
            "deliver exactly once"
        )
        fresh_key = fresh[0].idempotency_key
        assert fresh_key.startswith(k1 + "#"), (
            f"PIN 7 (safety net) RED: the fresh delivery's key {fresh_key!r} "
            f"is not the synthetic safety-net shape {k1 + '#<8hex>'!r} — the "
            "original key would be reused verbatim"
        )
        assert len(all_jobs) == 2, (
            f"PIN 7 (safety net) RED: expected 2 JobItems (the terminal "
            f"predecessor + the fresh delivery), got {len(all_jobs)}"
        )
        live_tasks = [
            t for t in _tasks_for(pin_engine, instance_id)
            if t.status not in TERMINAL_TASK_STATUSES
        ]
        assert len(live_tasks) == 1, (
            f"PIN 7 (safety net) RED: expected exactly 1 live Task for the "
            f"fresh delivery, got {len(live_tasks)} — no fresh delivery"
        )
        live_mqs = [
            m for m in _mq_for(pin_engine, instance_id)
            if m.status in LIVE_MQ_STATUSES
        ]
        assert len(live_mqs) == 1, (
            f"PIN 7 (safety net) RED: expected exactly 1 live MessageQueue row "
            f"for the fresh delivery, got {len(live_mqs)}"
        )


# ===========================================================================
# PIN 7 (commission W7(i)) — TestBusyPredicate  (plan §(e) pin #8 / DEC-4)
# ===========================================================================


class TestBusyPredicate:
    """PIN 8 (RED at base).

    RED MUTATION (plan §(e) W7(i) row, verbatim): "add `PAUSED` (or drop
    `WAITING_CHILDREN`) to the predicate tuple → RED".

    Required POST-FIX behaviour: the busy predicate stays EXACTLY
    ``{RUNNING, WAITING, WAITING_CHILDREN}`` [DEC-4] — asserted as an
    exact set through BOTH the predicate itself AND the deferral
    observable (a fire at a busy status produces a 'deferred' row; a fire
    at PAUSED/IDLE produces a DELIVERY, never a deferral).
    """

    async def test_busy_predicate_is_exactly_running_waiting_waiting_children(
        self, pin_manager, pin_engine: Engine
    ):
        all_statuses = [s.value for s in InstanceStatus]
        expected_busy = set(BUSY_STATUSES)

        # ---- exact-set check on the predicate itself --------------------
        for status in all_statuses:
            source_id = f"{PIN_TAG}-p8-{uuid.uuid4().hex[:8]}"
            instance_id = str(uuid.uuid4())
            run_at = datetime.now(timezone.utc) + timedelta(seconds=30)
            _seed_instance(pin_engine, instance_id=instance_id, status=status)
            _seed_source_and_mapping(
                pin_engine, source_id=source_id, instance_id=instance_id,
                run_at=run_at,
            )
            adapter, _events, _completions = _build_pin_adapter(
                pin_manager, pin_engine, source_id=source_id, run_at=run_at
            )
            is_active, resolved_id, resolved_status = adapter._is_instance_active()
            assert is_active == (status in expected_busy), (
                f"PIN 8 RED: _is_instance_active for status {status!r} "
                f"returned {is_active} — the busy predicate is not EXACTLY "
                f"{sorted(expected_busy)} (W7(i) mutation: an added or "
                "dropped member)"
            )
            if status in expected_busy:
                assert resolved_id == instance_id and resolved_status == status

        # ---- deferral observable for each BUSY status -------------------
        for status in BUSY_STATUSES:
            source_id = f"{PIN_TAG}-p8b-{uuid.uuid4().hex[:8]}"
            instance_id = str(uuid.uuid4())
            run_at = datetime.now(timezone.utc) + timedelta(seconds=30)
            _seed_instance(pin_engine, instance_id=instance_id, status=status)
            _seed_source_and_mapping(
                pin_engine, source_id=source_id, instance_id=instance_id,
                run_at=run_at,
            )
            adapter, _events, _completions = _build_pin_adapter(
                pin_manager, pin_engine, source_id=source_id, run_at=run_at
            )
            await _fire_emit(adapter)
            deferred_rows = [
                r for r in _schedule_executions(pin_engine, source_id)
                if r.status == "deferred"
            ]
            assert len(deferred_rows) == 1, (
                f"PIN 8 RED: a fire at busy status {status!r} did NOT defer "
                f"(deferred rows={deferred_rows}) — at base the fire is "
                "dropped with zero rows"
            )

        # ---- delivery observable for NOT-busY statuses (paused/idle) ----
        for status in ("paused", "idle"):
            source_id = f"{PIN_TAG}-p8c-{uuid.uuid4().hex[:8]}"
            instance_id = str(uuid.uuid4())
            run_at = datetime.now(timezone.utc) + timedelta(seconds=30)
            _seed_instance(pin_engine, instance_id=instance_id, status=status)
            _seed_source_and_mapping(
                pin_engine, source_id=source_id, instance_id=instance_id,
                run_at=run_at,
            )
            adapter, _events, _completions = _build_pin_adapter(
                pin_manager, pin_engine, source_id=source_id, run_at=run_at
            )
            await _fire_emit(adapter)
            deferred_rows = [
                r for r in _schedule_executions(pin_engine, source_id)
                if r.status == "deferred"
            ]
            assert deferred_rows == [], (
                f"PIN 8 RED: status {status!r} produced a DEFERRAL — it is "
                "not in the busy set {running, waiting, waiting_children} "
                f"(rows={deferred_rows})"
            )
            assert _jobitems_for(pin_engine, instance_id), (
                f"PIN 8 RED: status {status!r} produced NO delivery either — "
                "the fire vanished"
            )


# ===========================================================================
# PIN 8 (commission W7(ii)) — TestRunCounterDivergence  (ADR-8, DEC-20.2)
# ===========================================================================


class TestRunCounterDivergence:
    """PIN 9 (RED at base).

    RED MUTATION (plan §(e) W7(ii) row, verbatim): "increment
    `_run_counter` on the defer path → RED" — the deferred fire then
    advances the dispatch counter and the `#N [CONTINUATION]` numbering,
    so the counter==0 / '#1' assertions fail.

    Required POST-FIX behaviour: ``_run_counter`` counts DIRECT
    dispatches only and is BLIND to deferrals by design (ADR-8) — a
    deferred fire leaves it unchanged and the ``#N [CONTINUATION]``
    numbering un-advanced; the next DIRECT fire is #1.
    """

    async def test_run_counter_counts_direct_dispatches_only_and_is_blind_to_deferrals(
        self, pin_manager, pin_engine: Engine
    ):
        source_id = f"{PIN_TAG}-p9-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_id)  # RUNNING = busy
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )

        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id, run_at=run_at
        )

        # ---- fire 1: the DEFERRED fire must not advance the counter -----
        await _fire_emit(adapter)

        deferred_rows = [
            r for r in _schedule_executions(pin_engine, source_id)
            if r.status == "deferred"
        ]
        assert len(deferred_rows) == 1, (
            "PIN 9 RED: fire 1 did not defer (deferred rows="
            f"{deferred_rows}) — at base the busy fire is dropped, so the "
            "divergence contract has no deferral to be blind to"
        )
        cfg1 = _config_of(pin_engine, source_id)
        assert cfg1.get("_run_counter") in (None, 0), (
            "PIN 9 RED: the deferred fire advanced _run_counter to "
            f"{cfg1.get('_run_counter')!r} — deferrals must be invisible to "
            "the dispatch counter"
        )

        # ---- fire 2: the caller frees -> DIRECT dispatch is #1 ----------
        _set_instance_status(pin_engine, instance_id, InstanceStatus.IDLE.value)
        await _fire_emit(adapter)

        cfg2 = _config_of(pin_engine, source_id)
        assert cfg2.get("_run_counter") == 1, (
            f"PIN 9 RED: after the direct dispatch _run_counter="
            f"{cfg2.get('_run_counter')!r}, expected 1 — the deferral must "
            "not have consumed a run number (the direct fire is #1)"
        )
        mqs = _mq_for(pin_engine, instance_id)
        direct_bodies = [m.content for m in mqs if (m.content or "").startswith("#")]
        assert direct_bodies, (
            f"PIN 9 RED: no '#N'-prefixed direct delivery body found "
            f"(mq contents={[m.content[:40] for m in mqs]})"
        )
        assert direct_bodies[0].startswith("#1\n"), (
            "PIN 9 RED: the direct dispatch body is not '#1' — the deferred "
            f"fire advanced the continuation numbering "
            f"(body={direct_bodies[0][:60]!r})"
        )


# ===========================================================================
# PIN 9 (commission W7(iii)) — TestMarkerMissFallback  (architect R3, DEC-18.4)
# ===========================================================================


class TestMarkerMissFallback:
    """PIN 10 (RED at base).

    RED MUTATION (plan §(e) W7(iii) row / DEC-18 amendment, verbatim):
    "remove the marker-miss fallback branch → RED" — the crash window
    then strands the committed delivery: the kept `message_id` is never
    persisted and the next guarded update never corrects the kept body.

    Required POST-FIX behaviour (architect R3 crash window): a marker-miss
    after a committed enqueue SELF-HEALS — the next fire's enqueue under
    the SAME key produces a dedup hit that returns the KEPT
    ``message_id`` (never the phantom's), the marker is persisted with
    the kept id, no second delivery is opened, and the next guarded
    update corrects the kept body's count.
    """

    async def test_marker_miss_after_enqueue_self_heals_via_dedup_hit_kept_message_id(
        self, pin_manager, pin_engine: Engine
    ):
        source_id = f"{PIN_TAG}-p10-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)
        key = _expected_run_at_key(source_id, run_at)

        _seed_instance(pin_engine, instance_id=instance_id)
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )

        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id, run_at=run_at
        )

        # ---- fire 1: a deferred delivery commits ------------------------
        await _fire_emit(adapter)

        jobs1 = _jobitems_with_key(pin_engine, key)
        assert len(jobs1) == 1, (
            "PIN 10 RED: fire 1 produced no committed delivery to heal onto "
            f"(jobs under key: {len(jobs1)}) — at base the busy fire is dropped"
        )
        kept_job = jobs1[0]
        kept_message_id = (kept_job.job_metadata or {}).get("message_id")
        assert kept_message_id, (
            "PIN 10: the committed JobItem carries no stamped message_id"
        )

        # ---- the CRASH WINDOW: the marker write never landed ------------
        def _strip_marker(cfg: dict) -> None:
            cfg.pop("_defer_message_id", None)

        cfg_after_strip = _edit_source_config(pin_engine, source_id, _strip_marker)
        assert "_defer_message_id" not in cfg_after_strip

        # ---- fire 2: the marker-miss fallback -> dedup hit -> kept id ----
        await _fire_emit(adapter)

        cfg2 = _config_of(pin_engine, source_id)
        assert cfg2.get("_defer_message_id") == kept_message_id, (
            "PIN 10 RED: the marker-miss fallback did not persist the KEPT "
            f"message_id (config _defer_message_id="
            f"{cfg2.get('_defer_message_id')!r}, kept={kept_message_id!r}) "
            "— the crash window strands the delivery"
        )
        jobs2 = _jobitems_with_key(pin_engine, key)
        assert len(jobs2) == 1, (
            f"PIN 10 RED: the fallback opened a SECOND delivery "
            f"({len(jobs2)} JobItems under the key) — no loss, no duplicate "
            "is violated"
        )
        assert cfg2.get("_defer_gen") in (None, 1), (
            "PIN 10 RED: the fallback bumped the generation — a dedup hit "
            "must resume the SAME generation, not roll over"
        )
        live_tasks = [
            t for t in _tasks_for(pin_engine, instance_id)
            if t.status not in TERMINAL_TASK_STATUSES
        ]
        assert len(live_tasks) == 1, (
            f"PIN 10 RED: expected the original live Task to survive, got "
            f"{len(live_tasks)}"
        )

        # ---- fire 3: the next guarded update corrects the kept body -----
        await _fire_emit(adapter)

        with Session(pin_engine) as session:
            mq_row = session.get(MessageQueue, kept_message_id)
            assert mq_row is not None, "the kept MessageQueue row vanished"
            assert "3 occurrences" in (mq_row.content or ""), (
                "PIN 10 RED: the guarded update did not correct the KEPT "
                f"body's count (content={mq_row.content!r}) — the healed "
                "marker is not driving coalescing"
            )


# ===========================================================================
# PIN 10 (commission) — TestWaitingOrderingDelta  (ADR-9, DEC-20.1)
# ===========================================================================


class TestWaitingOrderingDelta:
    """PIN 11 (RED at base).

    RED MUTATION (plan §(e) optional (c) row, verbatim): "restore the old
    drop-on-busy behaviour for those two states → RED".

    Required POST-FIX behaviour (ADR-9): a deferred delivery to a WAITING
    caller is claimable IMMEDIATELY (the queue serializes only against
    RUNNING), and a WAITING_CHILDREN caller is auto-resumed to RUNNING at
    the enqueue seam (instance_messaging.py:2375-2379) and then claimable
    — both states RECEIVE the alarm as the next serialized turn instead
    of being dropped.
    """

    async def test_waiting_and_waiting_children_receive_alarm_as_next_turn(
        self, pin_manager, pin_engine: Engine
    ):
        for status in ("waiting", "waiting_children"):
            source_id = f"{PIN_TAG}-p11-{uuid.uuid4().hex[:8]}"
            instance_id = str(uuid.uuid4())
            run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

            _seed_instance(pin_engine, instance_id=instance_id, status=status)
            _seed_source_and_mapping(
                pin_engine, source_id=source_id, instance_id=instance_id,
                run_at=run_at,
            )

            adapter, _events, _completions = _build_pin_adapter(
                pin_manager, pin_engine, source_id=source_id, run_at=run_at
            )
            await _fire_once(adapter, timeout_s=8.0)

            deferred_rows = [
                r for r in _schedule_executions(pin_engine, source_id)
                if r.status == "deferred"
            ]
            assert len(deferred_rows) == 1, (
                f"PIN 11 RED: a fire at {status!r} did NOT defer "
                f"(deferred rows={deferred_rows}) — at base the state is "
                "dropped by the busy branch with zero rows"
            )
            live_tasks = [
                t for t in _tasks_for(pin_engine, instance_id)
                if t.status not in TERMINAL_TASK_STATUSES
            ]
            assert len(live_tasks) == 1, (
                f"PIN 11 RED: expected exactly 1 live Task for the "
                f"{status!r} caller, got {len(live_tasks)}"
            )

            if status == "waiting_children":
                # The enqueue seam auto-resumes WAITING_CHILDREN -> RUNNING
                # (instance_messaging.py:2375-2379 — the IDLE/WAITING_CHILDREN
                # branch, NOT the terminal-revival set at :2369-2374).
                with Session(pin_engine) as session:
                    inst = session.get(Instance, instance_id)
                    assert inst is not None
                    assert inst.status == InstanceStatus.RUNNING.value, (
                        f"PIN 11 RED: WAITING_CHILDREN was not auto-resumed to "
                        f"RUNNING by the delivery enqueue (status={inst.status!r})"
                    )

            # The alarm is the NEXT serialized turn: claimable now.
            job_for_task = _jobitems_for(pin_engine, instance_id)
            assert job_for_task, f"PIN 11 RED: no JobItem for the {status!r} delivery"
            _set_job_admission(
                pin_engine, job_for_task[0].job_id, AdmissionState.ACTIVE.value
            )
            claimed = pin_manager._task_repo.claim_pending_task(
                f"pin-worker-p11-{status}"
            )
            assert claimed is not None and claimed.id == live_tasks[0].id, (
                f"PIN 11 RED: the deferred alarm for the {status!r} caller is "
                f"not claimable as the next turn (claimed={claimed!r})"
            )


# ===========================================================================
# PIN 11 (commission #8 NEW) — TestNonDeliveryAgeCap  (Blocking 3, DEC-22)
# ===========================================================================


class TestNonDeliveryAgeCap:
    """PIN 12 (RED at base).

    RED MUTATION (plan §(e) pin #8 row, verbatim): "Consume unconditionally
    on breach (Rule A without the abandon guard) — the breach consumes
    even when nothing is pending" — RED symptom: the no-row leg (nothing
    pending, or already claimed and in flight) wrongly consumes an
    occurrence and writes a `failed` row for a delivery that was about to
    land.

    NAME NOTE (leader-adjudicated): this function keeps the OPERATIVE
    MANDATE name ``test_age_cap_breach_terminates_visibly_and_never_opens_a_fresh_generation``;
    the SEMANTICS and the MUTATION SPEC carried here are plan §(e) row 8's
    (whose own label reads ``test_age_cap_breach_consumes_only_when_the_abandon_guard_returns_a_row``).
    The label divergence is reported for leader adjudication.

    ADJ-4 (LEADER RULING): the no-row leg NOW PINS THE CARRIER RETENTION
    — at a breach where the abandon guard returns NO row, ``_defer_since``
    is RETAINED (not cleared): the episode is not over (the claimed
    delivery will land, or it is terminal), and clearing it would orphan
    the episode so a post-claim death would escape the age cap entirely.
    See the leader rationale at the assertion site.

    [LEADER-RULED — Q1, the refined three-sub-case breach rule] The no-row
    leg of this test is the **IN-FLIGHT sub-case (case 2)**: its fixture
    seeds a marker (``_defer_message_id``) AND a NON-TERMINAL Task whose
    ``message_id`` EQUALS that marker — a CLAIMED delivery that WILL land.
    Every existing assertion is kept verbatim (carrier RETAINED + no
    consumption + no ``failed`` row + no fresh delivery at the breach
    instant); the fixture did NOT need adjusting.

    The two sibling legs below cover the rest of the refined rule:
    ``test_age_cap_breach_after_delivered_delivery_clears_the_carrier_without_a_failed_row``
    (case 3a — the occurrence WAS DELIVERED: the carrier clears, NO failed
    row) and
    ``test_age_cap_breach_without_marker_terminates_visibly_and_clears_the_carrier``
    (case 3b/3c — nothing will ever land: terminate VISIBLY).

    Required POST-FIX behaviour (the ONE age-cap rule, ONE token
    ``nondelivery_age_cap``): at the breach tick the episode terminates
    VISIBLY — (row leg) abandon any still-pending delivery with ONE
    guarded statement (``WHERE status IN ('ready','retrying')``) and, ONLY
    if that guard returns a row, CONSUME the occurrence with a terminal
    'failed' row (reason ``nondelivery_age_cap``) + WARNING; the carrier
    is cleared; (no-row leg) an already-claimed in-flight delivery is NOT
    abandoned, nothing is consumed, no failed row is written; and in BOTH
    legs the breach NEVER opens a fresh generation.
    """

    async def test_age_cap_breach_terminates_visibly_and_never_opens_a_fresh_generation(
        self, pin_manager, pin_engine: Engine, caplog
    ):
        # ================= row leg: pending delivery is abandoned ========
        source_id = f"{PIN_TAG}-p12a-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)
        stale_delivery_id = str(uuid.uuid4())

        _seed_instance(pin_engine, instance_id=instance_id)  # RUNNING = busy
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )
        seeded_mq = _seed_mq_row(
            pin_engine,
            message_id=stale_delivery_id,
            instance_id=instance_id,
            status="ready",
        )
        _seed_task(
            pin_engine, instance_id=instance_id,
            status=TaskStatus.PENDING.value, message_id=stale_delivery_id,
        )

        def _plant_stale_episode(cfg: dict) -> None:
            cfg["_defer_gen"] = 1
            cfg["_defer_count"] = 3
            cfg["_defer_message_id"] = stale_delivery_id
            cfg["_defer_since"] = (
                datetime.now(timezone.utc) - timedelta(seconds=DEFER_AGE_CAP_S + 3600)
            ).isoformat()

        _edit_source_config(pin_engine, source_id, _plant_stale_episode)

        with caplog.at_level(logging.INFO, logger=SCHED_LOGGER):
            await _fire_once(adapter=_build_pin_adapter(
                pin_manager, pin_engine, source_id=source_id, run_at=run_at
            )[0], timeout_s=8.0)

        with Session(pin_engine) as session:
            abandoned = session.get(MessageQueue, stale_delivery_id)
            assert abandoned is not None
            assert abandoned.status not in ("ready", "retrying"), (
                "PIN 12 RED (row leg): the still-pending delivery was NOT "
                f"abandoned (status={abandoned.status!r}) — the guarded "
                "abandon statement did not run"
            )

        cap_rows = [
            r for r in _schedule_executions(pin_engine, source_id)
            if r.status == "failed"
            and r.error_message
            and "nondelivery_age_cap" in r.error_message
        ]
        assert len(cap_rows) == 1, (
            "PIN 12 RED (row leg): expected exactly one terminal 'failed' "
            f"row with reason 'nondelivery_age_cap', got "
            f"{[(r.status, r.error_message) for r in _schedule_executions(pin_engine, source_id)]} "
            "— at base the breach tick does not exist and the fire is "
            "silently dropped"
        )
        assert cap_rows and cap_rows[0].completed_at is not None

        sched_records = [r for r in caplog.records if r.name == SCHED_LOGGER]
        assert any(r.levelname == "WARNING" for r in sched_records), (
            "PIN 12 RED (row leg): the breach produced no WARNING from the "
            "scheduler logger (DEC-19: visible on breach, never a silent loss)"
        )

        row = _source_row(pin_engine, source_id)
        assert row is not None
        assert row.status == "stopped" and row.enabled is False, (
            "PIN 12 RED (row leg): the breach did NOT consume the one-shot "
            f"visibly (enabled={row.enabled}, status={row.status!r})"
        )

        cfg_after = _config_of(pin_engine, source_id)
        assert cfg_after.get("_defer_since") is None, (
            f"PIN 12 RED (row leg): the carrier was not cleared "
            f"(_defer_since={cfg_after.get('_defer_since')!r}) — the episode "
            "must end at its terminal disposition"
        )
        assert cfg_after.get("_defer_message_id") is None, (
            "PIN 12 RED (row leg): _defer_message_id was not cleared"
        )
        assert cfg_after.get("_defer_count") in (None, 0), (
            f"PIN 12 RED (row leg): _defer_count was not reset "
            f"({cfg_after.get('_defer_count')!r})"
        )
        assert cfg_after.get("_defer_gen") == 1, (
            "PIN 12 RED (row leg): the breach opened a fresh generation "
            f"(_defer_gen={cfg_after.get('_defer_gen')!r}) — the breach "
            "NEVER opens one (Blocking 3 rule 4)"
        )
        # "never opens a fresh generation" is asserted as a TOTAL Task/row
        # count for the instance (status-agnostic): the breach may leave
        # the seeded task PENDING or cancel it, but it must not ADD one.
        assert len(_tasks_for(pin_engine, instance_id)) == 1, (
            "PIN 12 RED (row leg): the breach opened a fresh delivery "
            f"({len(_tasks_for(pin_engine, instance_id))} Task rows for the "
            "instance) — the breach NEVER opens a fresh generation"
        )
        assert _jobitems_for(pin_engine, instance_id) == [], (
            "PIN 12 RED (row leg): the breach opened a fresh JobItem — a new "
            "generation was started"
        )

        # ============ no-row leg: claimed delivery will land =============
        source_id_b = f"{PIN_TAG}-p12b-{uuid.uuid4().hex[:8]}"
        instance_b = str(uuid.uuid4())
        run_at_b = datetime.now(timezone.utc) + timedelta(seconds=1.2)
        claimed_delivery_id = str(uuid.uuid4())

        _seed_instance(pin_engine, instance_id=instance_b)  # RUNNING = busy
        _seed_source_and_mapping(
            pin_engine, source_id=source_id_b, instance_id=instance_b,
            run_at=run_at_b,
        )
        _seed_mq_row(
            pin_engine,
            message_id=claimed_delivery_id,
            instance_id=instance_b,
            status="processing",  # already claimed — it WILL land
        )
        _seed_task(
            pin_engine, instance_id=instance_b,
            status=TaskStatus.RUNNING.value, message_id=claimed_delivery_id,
        )

        def _plant_stale_episode_b(cfg: dict) -> None:
            cfg["_defer_gen"] = 1
            cfg["_defer_count"] = 2
            cfg["_defer_message_id"] = claimed_delivery_id
            cfg["_defer_since"] = (
                datetime.now(timezone.utc) - timedelta(seconds=DEFER_AGE_CAP_S + 3600)
            ).isoformat()

        _edit_source_config(pin_engine, source_id_b, _plant_stale_episode_b)

        adapter_b, _events_b, _completions_b = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id_b, run_at=run_at_b
        )
        # 8.0s: post-fix no-row breach does NOT consume -> the loop re-arms
        # (5s cadence); the timeout is the harness stop.  Base consumes and
        # breaks at ~1.3s.
        await _fire_once(adapter_b, timeout_s=8.0)

        with Session(pin_engine) as session:
            inflight = session.get(MessageQueue, claimed_delivery_id)
            assert inflight is not None
            assert inflight.status == "processing", (
                "PIN 12 RED (no-row leg): the in-flight (claimed) delivery "
                f"was disturbed (status={inflight.status!r}) — the abandon "
                "guard must touch only ready/retrying rows"
            )

        cap_rows_b = [
            r for r in _schedule_executions(pin_engine, source_id_b)
            if r.error_message and "nondelivery_age_cap" in r.error_message
        ]
        assert cap_rows_b == [], (
            "PIN 12 RED (no-row leg): a 'nondelivery_age_cap' failed row was "
            f"written although the guard returned NO row (rows={cap_rows_b}) "
            "— the breach consumed a delivery that was about to land"
        )

        # ---- ADJ-4 (LEADER RULING): the carrier is RETAINED -----------
        # LEADER RATIONALE (condensed): the no-row case means the delivery
        # was CLAIMED and will land, or is terminal — the episode is NOT
        # over in either sub-case; it ends via (i) the abandon-WITH-row leg
        # (consume + failed + clear), (ii) the delivery actually landing
        # (the clear event for a delivered occurrence), or (iii) a refusal.
        # Clearing at the no-row instant would ORPHAN the episode: if the
        # claimed delivery then DIES post-claim, the next fire's rollover
        # mints a fresh delivery, and the carrier must still show the
        # episode age so the very next breach check abandons it WITH a row
        # and terminates visibly.  Retaining is the only reading under
        # which every path terminates visibly and nothing spins.
        # (The sibling no-row facts — NO occurrence consumed, NO
        # ``nondelivery_age_cap`` failed row, NO fresh delivery minted at
        # the breach instant — are asserted below/above this one.)
        # Tolerant accessor: the carrier keys are POST-FIX attributes, so
        # a missing key yields a clean AssertionError, never a fixture
        # error (AttributeError/KeyError).
        assert _carrier_value(pin_engine, source_id_b, "_defer_since") is not None, (
            "PIN 12 RED (no-row leg): the breach CLEARED the carrier "
            "_defer_since although the abandon guard returned NO row — the "
            "episode is orphaned: if the claimed delivery dies post-claim, "
            "the next rollover mints a fresh delivery and the very next "
            "breach check would never abandon it WITH a row (the carrier "
            "must still show the episode age)"
        )

        row_b = _source_row(pin_engine, source_id_b)
        assert row_b is not None
        assert row_b.enabled is True and row_b.status == "running", (
            "PIN 12 RED (no-row leg): the breach CONSUMED the one-shot "
            f"(enabled={row_b.enabled}, status={row_b.status!r}) — rule 3 "
            "forbids consumption when the guard returns no row"
        )
        new_jobs = _jobitems_for(pin_engine, instance_b)
        assert new_jobs == [], (
            "PIN 12 RED (no-row leg): the breach opened a fresh delivery "
            f"({len(new_jobs)} JobItems) — the breach never opens a fresh "
            "generation"
        )
        assert len(_tasks_for(pin_engine, instance_b)) == 1, (
            "PIN 12 RED (no-row leg): the breach added a Task row "
            f"({len(_tasks_for(pin_engine, instance_b))} for the instance) — "
            "a fresh delivery was opened"
        )

    async def test_age_cap_breach_after_delivered_delivery_clears_the_carrier_without_a_failed_row(
        self, pin_manager, pin_engine: Engine, caplog
    ):
        """Q1 CASE 3a (leader-ruled) — the occurrence WAS DELIVERED.

        The marker's Task is terminal-COMPLETED: the deferred delivery
        LANDED, so the episode is CLOSED.  The breach must CLEAR the
        carrier (this is the delivery-detection clear W8 had no home for)
        and apply the NORMAL CONSUMPTION semantics — and it must NOT write
        a ``failed`` row: that row would be a LIE (the alarm DID reach the
        caller).

        RED MUTATION SPEC (verbatim): "the unrefined rule — case 3 falls
        into the no-row-no-consume path -> the spin shape (one-shot NOT
        consumed, no failed row, carrier retained)".

        Required POST-FIX behaviour: the carrier is CLEARED, NO
        ``nondelivery_age_cap`` failed row is written, the occurrence is
        CONSUMED (normal consumption semantics) and the breach NEVER opens
        a fresh generation.
        """
        source_id = f"{PIN_TAG}-p12c-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)
        delivered_id = str(uuid.uuid4())

        _seed_instance(pin_engine, instance_id=instance_id)  # RUNNING = busy
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )
        # The delivery LANDED: its queue row is 'completed' and its Task is
        # COMPLETED (terminal) — the marker still names it.
        _seed_mq_row(
            pin_engine,
            message_id=delivered_id,
            instance_id=instance_id,
            status="completed",
        )
        _seed_task(
            pin_engine,
            instance_id=instance_id,
            status=TaskStatus.COMPLETED.value,
            message_id=delivered_id,
        )

        def _plant_delivered_episode(cfg: dict) -> None:
            cfg["_defer_gen"] = 1
            cfg["_defer_count"] = 2
            cfg["_defer_message_id"] = delivered_id
            cfg["_defer_since"] = (
                datetime.now(timezone.utc) - timedelta(seconds=DEFER_AGE_CAP_S + 3600)
            ).isoformat()

        _edit_source_config(pin_engine, source_id, _plant_delivered_episode)

        with caplog.at_level(logging.INFO, logger=SCHED_LOGGER):
            await _fire_once(
                adapter=_build_pin_adapter(
                    pin_manager, pin_engine, source_id=source_id, run_at=run_at
                )[0],
                timeout_s=8.0,
            )

        # --- NO false failed row (the alarm WAS delivered) ----------------
        failed_rows = [
            r
            for r in _schedule_executions(pin_engine, source_id)
            if r.status == "failed"
        ]
        assert failed_rows == [], (
            "PIN 12 RED (case 3a): a 'failed' row was written for an "
            "occurrence that WAS delivered — the breach disposition must be "
            f"truthful: {[(r.status, r.error_message) for r in failed_rows]}"
        )

        # --- the carrier is CLEARED (the W8 delivery-detection clear) -----
        cfg = _config_of(pin_engine, source_id)
        assert cfg.get("_defer_since") is None, (
            f"PIN 12 RED (case 3a): the carrier was not cleared "
            f"(_defer_since={cfg.get('_defer_since')!r}) — the delivered "
            "episode must end"
        )
        assert cfg.get("_defer_message_id") is None, (
            "PIN 12 RED (case 3a): _defer_message_id was not cleared"
        )
        assert cfg.get("_defer_count") in (None, 0), (
            f"PIN 12 RED (case 3a): _defer_count was not reset "
            f"({cfg.get('_defer_count')!r})"
        )
        assert cfg.get("_defer_gen") == 1, (
            f"PIN 12 RED (case 3a): the breach opened a fresh generation "
            f"(_defer_gen={cfg.get('_defer_gen')!r})"
        )

        # --- the occurrence is CONSUMED (normal consumption semantics) ----
        row = _source_row(pin_engine, source_id)
        assert row is not None
        assert row.status == "stopped" and row.enabled is False, (
            "PIN 12 RED (case 3a): the delivered occurrence was NOT consumed "
            f"(status={row.status!r} enabled={row.enabled}) — the normal "
            "consumption semantics must apply"
        )

        # --- a WARNING is emitted (the breach is visible) -----------------
        sched_records = [r for r in caplog.records if r.name == SCHED_LOGGER]
        assert any(r.levelname == "WARNING" for r in sched_records), (
            "PIN 12 RED (case 3a): the breach produced no WARNING from the "
            "scheduler logger"
        )

        # --- and NO fresh delivery ---------------------------------------
        assert _jobitems_for(pin_engine, instance_id) == [], (
            "PIN 12 RED (case 3a): the breach opened a fresh delivery"
        )

    async def test_age_cap_breach_without_marker_terminates_visibly_and_clears_the_carrier(
        self, pin_manager, pin_engine: Engine, caplog
    ):
        """Q1 CASE 3b/3c (leader-ruled) — NOTHING WILL EVER LAND.

        No marker at all (the delivery-less legs — e.g. the semaphore
        re-arm leg, where no delivery was ever enqueued), or the marker's
        Task is terminal-FAILED / ABSENT.  Nothing can land, so the breach
        must TERMINATE VISIBLY: consume + a terminal ``failed`` row (reason
        ``nondelivery_age_cap``) + a WARNING + the carrier CLEARED.  This
        leg closes the 0.2 Hz spin.

        RED MUTATION SPEC (verbatim): "the unrefined rule — case 3 falls
        into the no-row-no-consume path -> the spin shape (one-shot NOT
        consumed, no failed row, carrier retained)".

        Required POST-FIX behaviour: the occurrence is CONSUMED, exactly one
        ``nondelivery_age_cap`` failed row is written, the carrier is
        CLEARED and the breach NEVER opens a fresh generation.
        """
        source_id = f"{PIN_TAG}-p12d-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_id)  # RUNNING = busy
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )

        def _plant_deliveryless_episode(cfg: dict) -> None:
            # An episode with NO marker: nothing was ever enqueued for it.
            cfg["_defer_gen"] = 1
            cfg["_defer_count"] = 2
            cfg.pop("_defer_message_id", None)
            cfg["_defer_since"] = (
                datetime.now(timezone.utc) - timedelta(seconds=DEFER_AGE_CAP_S + 3600)
            ).isoformat()

        _edit_source_config(pin_engine, source_id, _plant_deliveryless_episode)

        with caplog.at_level(logging.INFO, logger=SCHED_LOGGER):
            await _fire_once(
                adapter=_build_pin_adapter(
                    pin_manager, pin_engine, source_id=source_id, run_at=run_at
                )[0],
                timeout_s=8.0,
            )

        # --- exactly one terminal failed row ----------------------------
        cap_rows = [
            r
            for r in _schedule_executions(pin_engine, source_id)
            if r.status == "failed"
            and r.error_message
            and "nondelivery_age_cap" in r.error_message
        ]
        assert len(cap_rows) == 1, (
            "PIN 12 RED (case 3b): expected exactly one terminal 'failed' row "
            f"with reason 'nondelivery_age_cap', got "
            f"{[(r.status, r.error_message) for r in _schedule_executions(pin_engine, source_id)]}"
        )
        assert cap_rows[0].completed_at is not None

        # --- the occurrence is CONSUMED ---------------------------------
        row = _source_row(pin_engine, source_id)
        assert row is not None
        assert row.status == "stopped" and row.enabled is False, (
            "PIN 12 RED (case 3b): the occurrence was NOT consumed "
            f"(status={row.status!r} enabled={row.enabled}) — nothing will "
            "ever land, so the episode must terminate visibly"
        )

        # --- the carrier is CLEARED --------------------------------------
        cfg = _config_of(pin_engine, source_id)
        assert cfg.get("_defer_since") is None, (
            f"PIN 12 RED (case 3b): the carrier was not cleared "
            f"(_defer_since={cfg.get('_defer_since')!r}) — the episode must "
            "end at its terminal disposition"
        )
        assert cfg.get("_defer_message_id") is None, (
            "PIN 12 RED (case 3b): _defer_message_id was not cleared"
        )
        assert cfg.get("_defer_count") in (None, 0), (
            f"PIN 12 RED (case 3b): _defer_count was not reset "
            f"({cfg.get('_defer_count')!r})"
        )
        assert cfg.get("_defer_gen") == 1, (
            f"PIN 12 RED (case 3b): the breach opened a fresh generation "
            f"(_defer_gen={cfg.get('_defer_gen')!r})"
        )

        # --- a WARNING is emitted ----------------------------------------
        sched_records = [r for r in caplog.records if r.name == SCHED_LOGGER]
        assert any(r.levelname == "WARNING" for r in sched_records), (
            "PIN 12 RED (case 3b): the breach produced no WARNING from the "
            "scheduler logger (DEC-19: visible on breach, never a silent loss)"
        )

        # --- and NO fresh delivery ---------------------------------------
        assert _jobitems_for(pin_engine, instance_id) == [], (
            "PIN 12 RED (case 3b): the breach opened a fresh delivery — the "
            "breach NEVER opens a fresh generation"
        )
        assert _tasks_for(pin_engine, instance_id) == [], (
            "PIN 12 RED (case 3b): the breach added a Task row — a fresh "
            "delivery was opened"
        )

    async def test_age_cap_breach_in_the_claim_window_retains_the_carrier(
        self, pin_manager, pin_engine: Engine, caplog
    ):
        """C2 (code review, CRITICAL) — the CLAIM WINDOW is case 2.

        THE DEFECT THIS PINS: the claim window has BOTH a ``'ready'``
        message_queue row AND a **RUNNING** Task — the delivery IS in
        flight and WILL land.  The case-1-first ordering abandoned it,
        consumed the occurrence and wrote a terminal ``failed`` row for a
        delivery that lands (a FALSE failed disposition), and clearing the
        carrier orphaned the episode (ADJ-4's forbidden shape: a
        post-claim death would then escape the cap entirely).

        The existing case-2 fixture only covered the DEQUEUED shape (the
        queue row already ``'processing'``) — which is why the suite was
        green while this shipped.

        RED MUTATION SPEC (verbatim): "remove the Task cross-check".

        Required POST-FIX behaviour: the marker's Task is CLAIMED
        (running/paused) => the case-2 path (no consume, NO failed row,
        the carrier RETAINED) even though the queue row is still
        ``'ready'``.
        """
        source_id = f"{PIN_TAG}-p12e-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)
        claimed_id = str(uuid.uuid4())

        _seed_instance(pin_engine, instance_id=instance_id)  # RUNNING = busy
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )
        # THE CLAIM WINDOW: the queue row is still 'ready' while the Task
        # has flipped to RUNNING at claim (task/repository.py:2801-2804).
        _seed_mq_row(
            pin_engine,
            message_id=claimed_id,
            instance_id=instance_id,
            status="ready",
        )
        _seed_task(
            pin_engine,
            instance_id=instance_id,
            status=TaskStatus.RUNNING.value,
            message_id=claimed_id,
        )

        def _plant_claim_window_episode(cfg: dict) -> None:
            cfg["_defer_gen"] = 1
            cfg["_defer_count"] = 2
            cfg["_defer_message_id"] = claimed_id
            cfg["_defer_since"] = (
                datetime.now(timezone.utc) - timedelta(seconds=DEFER_AGE_CAP_S + 3600)
            ).isoformat()

        _edit_source_config(pin_engine, source_id, _plant_claim_window_episode)

        with caplog.at_level(logging.INFO, logger=SCHED_LOGGER):
            await _fire_once(
                adapter=_build_pin_adapter(
                    pin_manager, pin_engine, source_id=source_id, run_at=run_at
                )[0],
                timeout_s=8.0,
            )

        # --- NO failed row (the delivery WILL land) ----------------------
        failed_rows = [
            r
            for r in _schedule_executions(pin_engine, source_id)
            if r.status == "failed"
        ]
        assert failed_rows == [], (
            "PIN 12 RED (claim window): a 'failed' row was written for a "
            "delivery that is CLAIMED and WILL land — the breach disposition "
            f"must be truthful: {[(r.status, r.error_message) for r in failed_rows]}"
        )

        # --- the carrier is RETAINED (the episode is not over) ------------
        cfg = _config_of(pin_engine, source_id)
        assert _carrier_value(pin_engine, source_id, "_defer_since") is not None, (
            "PIN 12 RED (claim window): the carrier was CLEARED although the "
            "delivery is IN FLIGHT — clearing it orphans the episode (a "
            "post-claim death would escape the cap entirely)"
        )
        assert cfg.get("_defer_message_id") == claimed_id, (
            "PIN 12 RED (claim window): the marker was cleared although the "
            "delivery is IN FLIGHT"
        )

        # --- the one-shot is NOT consumed --------------------------------
        row = _source_row(pin_engine, source_id)
        assert row is not None
        assert row.enabled is True and row.status == "running", (
            "PIN 12 RED (claim window): the one-shot WAS consumed although "
            f"the delivery is IN FLIGHT (enabled={row.enabled}, "
            f"status={row.status!r})"
        )

        # --- and the in-flight delivery was NOT abandoned ----------------
        with Session(pin_engine) as session:
            inflight = session.get(MessageQueue, claimed_id)
            assert inflight is not None
            assert inflight.status == "ready", (
                "PIN 12 RED (claim window): the in-flight delivery was "
                f"disturbed (status={inflight.status!r}) — the breach must "
                "not abandon a CLAIMED delivery"
            )


# ===========================================================================
# PIN 13 (code-review fix round) — TestAgeCapSingleEvaluationSite
# (C1: the age-cap breach is evaluated at EXACTLY ONE site per fire)
# ===========================================================================


class TestAgeCapSingleEvaluationSite:
    """C1 (code review, CRITICAL) — the breach check has ONE site.

    THE DEFECT THIS PINS: the age-cap breach was evaluated ONLY inside
    ``_defer_delivery``.  But the non-delivery episode carrier OPENS at
    FIVE sites — the deferral, the audit-row write failure, the enqueue
    exception, the taskless enqueue and the semaphore RE-ARM — and only
    ONE of them is on the deferral path: the RE-ARM and the DIRECT-FAILURE
    legs never re-enter ``_defer_delivery``.  So a saturated semaphore (or
    a persistently failing enqueue) spun at ~0.2 Hz writing DEFERRED rows
    with NO age-cap row and an UNCLEARED carrier — DEC-22's quantified
    hazard, and a regression against base (pre-change those legs consumed
    unconditionally, i.e. they were bounded).

    THE FIX: the breach check is hoisted to the TOP of
    ``_emit_scheduled_message`` (after the fire-outcome reset, before the
    semaphore acquire and the busy check) and the internal call site is
    REMOVED — ONE evaluation site per fire (a second site would risk a
    double consumption).

    RED MUTATION SPEC (verbatim): "the breach check appearing twice per
    fire -> no double consumption / no double failed row" — the
    single-evaluation-site property is asserted as a NAMED ASSERTION
    inside the re-arm leg below (the breach handler is called EXACTLY
    once per fire).
    """

    async def test_rearm_leg_stale_episode_cap_fires_and_terminates_visibly(
        self, pin_manager, pin_engine: Engine, caplog
    ):
        """C1 leg 1 — the RE-ARM leg is bounded by the age cap.

        A stale, delivery-less episode + a saturated execution semaphore +
        NO deferrable target (new_instance mode) => the cap fires at the
        top of the fire and the episode terminates VISIBLY (one terminal
        ``nondelivery_age_cap`` failed row + the carrier CLEARED + the
        one-shot consumed).  The re-arm leg itself never runs (the breach
        precedes the semaphore acquire) — so there is NO spin.
        """
        source_id = f"{PIN_TAG}-p13a-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_id)
        # new_instance mode => NO deferrable target (the re-arm branch).
        _seed_source_and_mapping(
            pin_engine,
            source_id=source_id,
            instance_id=instance_id,
            run_at=run_at,
            instance_mode="new_instance",
        )

        def _plant_stale_deliveryless_episode(cfg: dict) -> None:
            cfg["_defer_gen"] = 1
            cfg["_defer_count"] = 2
            cfg.pop("_defer_message_id", None)
            cfg["_defer_since"] = (
                datetime.now(timezone.utc) - timedelta(seconds=DEFER_AGE_CAP_S + 3600)
            ).isoformat()

        _edit_source_config(pin_engine, source_id, _plant_stale_deliveryless_episode)

        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id, run_at=run_at,
            instance_mode="new_instance",
        )
        # Exhaust the execution semaphore -> the RE-ARM branch is the one
        # under test (it would write a DEFERRED row every 5s without the
        # cap).
        assert adapter._execution_semaphore is not None
        await adapter._execution_semaphore.acquire()

        # The single-evaluation-site property (NAMED ASSERTION): the
        # breach handler is called EXACTLY ONCE per fire.
        breach_calls: list = []
        real_breach = adapter._handle_age_cap_breach

        async def _counting_breach(*args, **kwargs):
            breach_calls.append(1)
            return await real_breach(*args, **kwargs)

        adapter._handle_age_cap_breach = _counting_breach  # type: ignore[method-assign]
        try:
            with caplog.at_level(logging.INFO, logger=SCHED_LOGGER):
                await _fire_once(adapter, timeout_s=8.0)
        finally:
            adapter._handle_age_cap_breach = real_breach  # type: ignore[method-assign]

        assert len(breach_calls) == 1, (
            "PIN 13 RED (single-evaluation-site): the age-cap breach was "
            f"evaluated {len(breach_calls)} times in ONE fire — the breach "
            "check must have EXACTLY ONE evaluation site per fire (a second "
            "site risks a double consumption)"
        )

        # --- the cap fired and the episode terminated VISIBLY -------------
        cap_rows = [
            r
            for r in _schedule_executions(pin_engine, source_id)
            if r.status == "failed"
            and r.error_message
            and "nondelivery_age_cap" in r.error_message
        ]
        assert len(cap_rows) == 1, (
            "PIN 13 RED (re-arm leg): the stale episode did NOT terminate "
            "visibly — expected exactly one terminal 'failed' row with reason "
            f"'nondelivery_age_cap', got "
            f"{[(r.status, r.error_message) for r in _schedule_executions(pin_engine, source_id)]}"
        )

        # --- and the re-arm leg NEVER ran (no spin) ----------------------
        deferred_rows = [
            r for r in _schedule_executions(pin_engine, source_id)
            if r.status == "deferred"
        ]
        assert deferred_rows == [], (
            "PIN 13 RED (re-arm leg): the re-arm branch wrote DEFERRED rows "
            f"({len(deferred_rows)}) — the breach precedes the semaphore "
            "acquire, so the saturated-semaphore spin is bounded"
        )

        # --- the one-shot is CONSUMED (the loop terminates) --------------
        row = _source_row(pin_engine, source_id)
        assert row is not None
        assert row.status == "stopped" and row.enabled is False, (
            "PIN 13 RED (re-arm leg): the stale episode was NOT consumed "
            f"(status={row.status!r} enabled={row.enabled}) — the spin must "
            "terminate visibly"
        )

        # --- the carrier is CLEARED --------------------------------------
        cfg = _config_of(pin_engine, source_id)
        assert cfg.get("_defer_since") is None, (
            f"PIN 13 RED (re-arm leg): the carrier was not cleared "
            f"(_defer_since={cfg.get('_defer_since')!r})"
        )
        assert cfg.get("_defer_count") in (None, 0), (
            f"PIN 13 RED (re-arm leg): _defer_count was not reset "
            f"({cfg.get('_defer_count')!r})"
        )

        # --- a WARNING is emitted -----------------------------------------
        sched_records = [r for r in caplog.records if r.name == SCHED_LOGGER]
        assert any(r.levelname == "WARNING" for r in sched_records), (
            "PIN 13 RED (re-arm leg): the breach produced no WARNING from "
            "the scheduler logger"
        )

    async def test_direct_failure_leg_stale_episode_cap_fires(
        self, pin_manager, pin_engine: Engine, caplog
    ):
        """C1 leg 2 — the DIRECT-FAILURE leg is bounded by the age cap.

        A failing enqueue on the direct path opens the non-delivery
        episode (the fifth carrier site).  Once the episode is older than
        the cap, the NEXT fire terminates it VISIBLY — the direct-failure
        leg never re-enters ``_defer_delivery``, so without the hoisted
        evaluation site the episode would spin forever.
        """
        source_id = f"{PIN_TAG}-p13b-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_id, status=InstanceStatus.IDLE.value)
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )

        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id, run_at=run_at
        )

        # The direct path's enqueue FAILS (the direct-failure leg).
        attempts: list = []

        async def _exploding_enqueue(*args, **kwargs):
            attempts.append(1)
            raise RuntimeError("pin harness: the direct enqueue exploded")

        real_enqueue = pin_manager.enqueue_message_job
        pin_manager.enqueue_message_job = _exploding_enqueue  # type: ignore[method-assign]
        try:
            # ---- fire 1: the direct failure opens the episode ------------
            await _fire_emit(adapter)
            assert attempts, (
                "PIN 13 RED (direct-failure leg): the direct path was never "
                "attempted — the fixture's failing enqueue was not reached"
            )
            cfg_after_first = _config_of(pin_engine, source_id)
            assert cfg_after_first.get("_defer_since") is not None, (
                "PIN 13 RED (direct-failure leg): the direct failure did not "
                "open the non-delivery episode carrier"
            )

            # ---- the episode ages past the cap --------------------------
            def _age_the_episode(cfg: dict) -> None:
                cfg["_defer_since"] = (
                    datetime.now(timezone.utc)
                    - timedelta(seconds=DEFER_AGE_CAP_S + 3600)
                ).isoformat()

            _edit_source_config(pin_engine, source_id, _age_the_episode)

            # ---- fire 2: the cap fires ---------------------------------
            with caplog.at_level(logging.INFO, logger=SCHED_LOGGER):
                await _fire_emit(adapter)
        finally:
            pin_manager.enqueue_message_job = real_enqueue  # type: ignore[method-assign]

        cap_rows = [
            r
            for r in _schedule_executions(pin_engine, source_id)
            if r.status == "failed"
            and r.error_message
            and "nondelivery_age_cap" in r.error_message
        ]
        assert len(cap_rows) == 1, (
            "PIN 13 RED (direct-failure leg): the stale episode did NOT "
            "terminate visibly — expected exactly one terminal 'failed' row "
            f"with reason 'nondelivery_age_cap', got "
            f"{[(r.status, r.error_message) for r in _schedule_executions(pin_engine, source_id)]}"
        )
        assert len(attempts) == 1, (
            "PIN 13 RED (direct-failure leg): the second fire attempted the "
            f"enqueue again ({len(attempts)} attempts) — the breach "
            "precedes the dispatch, so the stale episode terminates the fire"
        )

        cfg_after = _config_of(pin_engine, source_id)
        assert cfg_after.get("_defer_since") is None, (
            f"PIN 13 RED (direct-failure leg): the carrier was not cleared "
            f"(_defer_since={cfg_after.get('_defer_since')!r})"
        )

        sched_records = [r for r in caplog.records if r.name == SCHED_LOGGER]
        assert any(r.levelname == "WARNING" for r in sched_records), (
            "PIN 13 RED (direct-failure leg): the breach produced no WARNING "
            "from the scheduler logger"
        )


# ===========================================================================
# PIN 14 (code-review fix round) — TestFaultInjectionCarrierPaths
# (H1/H2/H3: the unknown-vs-absent discipline on the carrier paths)
# ===========================================================================


class TestFaultInjectionCarrierPaths:
    """H1 + H2 + H3 (code review) — the carrier paths' fault handling.

    THE DEFECT THIS PINS: the file's own "unknown != absent" tri-state
    discipline (``_TASK_LOOKUP_FAILED``, W3) was applied inconsistently on
    the CARRIER paths — three swallow shapes that are invisible to the
    happy-path suite:

    * **H1** — a coalescing-write EXCEPTION returned the rowcount-0 PROOF
      token ("rollover"), so the caller minted a FRESH enqueue while the
      original row might still be ready (a SECOND live delivery).
    * **H2** — a liveness-read EXCEPTION returned a confident negative
      (``False``), so the caller skipped the coalescing and opened a FRESH
      GENERATION while the pending delivery might still be live.
    * **H3** — the carrier write was swallowed (no return value), so a
      failed ``_open_episode`` left the episode UNTRACKED (the 24h age
      cap could never fire on it → an unbounded 5s re-arm spin) and a
      failed ``_clear_episode`` duplicated the terminal disposition on
      every recurring fire.

    Every leg below is FAULT INJECTION (a monkeypatched repo/service
    raises) and asserts the CONSERVATIVE outcome: no second live
    delivery, no fresh generation, the loud token, and a bounded retry.
    """

    async def test_coalesce_write_exception_does_not_open_a_second_delivery(
        self, pin_manager, pin_engine: Engine, caplog
    ):
        """H1 — a coalescing-write EXCEPTION must not authorise a mint.

        RED MUTATION SPEC (verbatim): "H1: exception -> 'rollover'".

        Required POST-FIX behaviour: the write outcome is UNKNOWN (not
        proof that the row is gone) -> NO fresh enqueue is opened, the
        pending delivery is left untouched, the ERROR token
        ``coalesce_write_unknown`` is logged and the next fire retries
        (the age cap bounds it).
        """
        source_id = f"{PIN_TAG}-p14a-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_id)  # RUNNING = busy
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )

        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id, run_at=run_at
        )

        # F5 (WIRING POSITIVE CONTROL): the enqueue-attempt counter is
        # installed BEFORE the first fire, so the EXPECTED-enqueue leg can
        # prove the counter is wired (attempts == 1).  Without this leg the
        # "no fresh enqueue" assertion below could pass VACUOUSLY (a
        # counter that never counts would trivially be empty).
        enqueue_attempts: list = []
        real_enqueue = pin_manager.enqueue_message_job

        async def _counting_enqueue(*args, **kwargs):
            enqueue_attempts.append(1)
            return await real_enqueue(*args, **kwargs)

        pin_manager.enqueue_message_job = _counting_enqueue  # type: ignore[method-assign]
        try:
            # ---- fire 1: the deferred delivery is committed ------------------
            await _fire_emit(adapter)

            # --- F5: the counter is WIRED (the expected enqueue) -----------
            assert enqueue_attempts == [1], (
                "PIN 14 RED (H1, F5 positive control): the expected first "
                f"enqueue was NOT observed ({len(enqueue_attempts)} attempts) "
                "— the attempt counter is not wired, so the 'no fresh enqueue' "
                "assertion below would pass VACUOUSLY"
            )
            enqueue_attempts.clear()

            mqs = _mq_for(pin_engine, instance_id)
            assert len(mqs) == 1, (
                f"PIN 14 RED (H1): the first fire produced {len(mqs)} MessageQueue "
                "rows (expected 1) — the fixture is broken"
            )
            original_body = mqs[0].content
            jobs_before = len(_jobitems_for(pin_engine, instance_id))
            assert jobs_before == 1

            # ---- the coalescing write RAISES (fault injection) ---------------
            mq_repo = pin_manager._queue_repository

            def _exploding_update(*args, **kwargs):
                raise RuntimeError("pin harness: the coalescing write exploded")

            real_update = mq_repo.update_ready_content
            mq_repo.update_ready_content = _exploding_update  # type: ignore[method-assign]
            # Count the enqueue ATTEMPTS: the "no fresh enqueue" property is
            # about the attempt, not about the resulting row count (a one-shot's
            # idempotency key would collapse a second enqueue into the kept
            # JobItem — the ATTEMPT is the observable).
            with caplog.at_level(logging.INFO, logger=SCHED_LOGGER):
                await _fire_emit(adapter)
        finally:
            mq_repo.update_ready_content = real_update  # type: ignore[method-assign]
            pin_manager.enqueue_message_job = real_enqueue  # type: ignore[method-assign]

        # --- NO fresh enqueue was attempted -------------------------------
        assert enqueue_attempts == [], (
            "PIN 14 RED (H1): a fresh enqueue was ATTEMPTED "
            f"({len(enqueue_attempts)} attempts) after the coalescing write "
            "raised — an unknown write outcome is NOT proof that the row is "
            "gone"
        )

        # --- NO second live delivery -------------------------------------
        jobs_after = len(_jobitems_for(pin_engine, instance_id))
        assert jobs_after == jobs_before, (
            "PIN 14 RED (H1): the coalescing-write exception opened a SECOND "
            f"live delivery ({jobs_before} -> {jobs_after} JobItems) — an "
            "unknown write outcome is NOT proof that the row is gone"
        )
        mqs_after = _mq_for(pin_engine, instance_id)
        assert len(mqs_after) == 1, (
            f"PIN 14 RED (H1): the coalescing-write exception left "
            f"{len(mqs_after)} MessageQueue rows (expected 1)"
        )
        assert mqs_after[0].content == original_body, (
            "PIN 14 RED (H1): the pending delivery's body was rewritten even "
            "though the write raised"
        )

        # --- the ERROR token is logged -----------------------------------
        sched_records = [r for r in caplog.records if r.name == SCHED_LOGGER]
        assert any(
            "coalesce_write_unknown" in r.getMessage() for r in sched_records
        ), (
            "PIN 14 RED (H1): no 'coalesce_write_unknown' ERROR was logged "
            f"(records={[(r.levelname, r.getMessage()[:90]) for r in sched_records]})"
        )

        # --- and the fire did NOT consume the one-shot --------------------
        row = _source_row(pin_engine, source_id)
        assert row is not None
        assert row.enabled is True, (
            "PIN 14 RED (H1): the one-shot was consumed even though the "
            "coalescing write's outcome was unknown"
        )

    async def test_liveness_read_exception_does_not_open_a_fresh_generation(
        self, pin_manager, pin_engine: Engine, caplog
    ):
        """H2 — a liveness-read EXCEPTION must not authorise a fresh generation.

        RED MUTATION SPEC (verbatim): "H2: exception -> False".

        Required POST-FIX behaviour: the liveness is UNKNOWN (not "not
        live") -> no fresh generation is opened and nothing is minted, the
        ERROR token ``liveness_read_unknown`` is logged and the next fire
        retries (the age cap bounds it).
        """
        source_id = f"{PIN_TAG}-p14b-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_id)  # RUNNING = busy
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )

        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id, run_at=run_at
        )

        # F5 (WIRING POSITIVE CONTROL): the counter is installed BEFORE the
        # first fire so the EXPECTED-enqueue leg proves it is wired
        # (attempts == 1) — otherwise the "no mint" assertion below could
        # pass VACUOUSLY.
        enqueue_attempts: list = []
        real_enqueue = pin_manager.enqueue_message_job

        async def _counting_enqueue(*args, **kwargs):
            enqueue_attempts.append(1)
            return await real_enqueue(*args, **kwargs)

        pin_manager.enqueue_message_job = _counting_enqueue  # type: ignore[method-assign]
        try:
            await _fire_emit(adapter)  # the committed delivery

            # --- F5: the counter is WIRED (the expected enqueue) -----------
            assert enqueue_attempts == [1], (
                "PIN 14 RED (H2, F5 positive control): the expected first "
                f"enqueue was NOT observed ({len(enqueue_attempts)} attempts) "
                "— the attempt counter is not wired, so the 'no mint' "
                "assertion below would pass VACUOUSLY"
            )
            enqueue_attempts.clear()

            jobs_before = len(_jobitems_for(pin_engine, instance_id))
            mqs_before = len(_mq_for(pin_engine, instance_id))
            assert jobs_before == 1 and mqs_before == 1

            # ---- the liveness READ raises (fault injection) -------------------
            mq_repo = pin_manager._queue_repository

            def _exploding_get(message_id):
                raise RuntimeError("pin harness: the liveness read exploded")

            real_get = mq_repo.get
            mq_repo.get = _exploding_get  # type: ignore[method-assign]
            # Count the enqueue ATTEMPTS (the "no mint" property is about the
            # attempt — a one-shot's idempotency key would collapse the attempt
            # into the kept JobItem).
            with caplog.at_level(logging.INFO, logger=SCHED_LOGGER):
                await _fire_emit(adapter)
        finally:
            mq_repo.get = real_get  # type: ignore[method-assign]
            pin_manager.enqueue_message_job = real_enqueue  # type: ignore[method-assign]

        # --- NO mint, NO fresh generation --------------------------------
        assert enqueue_attempts == [], (
            "PIN 14 RED (H2): a fresh enqueue was ATTEMPTED "
            f"({len(enqueue_attempts)} attempts) after the liveness read "
            "raised — an unknown liveness is not 'not live'"
        )
        assert len(_jobitems_for(pin_engine, instance_id)) == jobs_before, (
            "PIN 14 RED (H2): the liveness-read exception opened a FRESH "
            f"GENERATION ({jobs_before} -> "
            f"{len(_jobitems_for(pin_engine, instance_id))} JobItems) — an "
            "unknown liveness is not 'not live'"
        )
        assert len(_mq_for(pin_engine, instance_id)) == mqs_before, (
            "PIN 14 RED (H2): the liveness-read exception minted a second "
            f"delivery ({mqs_before} -> {len(_mq_for(pin_engine, instance_id))} "
            "MessageQueue rows)"
        )

        # --- the ERROR token is logged -----------------------------------
        sched_records = [r for r in caplog.records if r.name == SCHED_LOGGER]
        assert any(
            "liveness_read_unknown" in r.getMessage() for r in sched_records
        ), (
            "PIN 14 RED (H2): no 'liveness_read_unknown' ERROR was logged "
            f"(records={[(r.levelname, r.getMessage()[:90]) for r in sched_records]})"
        )

        # --- and the fire did NOT consume the one-shot --------------------
        row = _source_row(pin_engine, source_id)
        assert row is not None
        assert row.enabled is True, (
            "PIN 14 RED (H2): the one-shot was consumed even though the "
            "delivery's liveness was unknown"
        )

    async def test_open_episode_failure_still_delivers_and_is_loud(
        self, pin_manager, pin_engine: Engine, caplog
    ):
        """H3(i) — a failed carrier OPEN must not suppress the delivery.

        RED MUTATION SPEC (verbatim): "H3: return-void swallow".

        Required POST-FIX behaviour: delivery-FIRST — the deferred delivery
        IS still enqueued (a different write may succeed) — but the fire
        is LOUD about being untracked (the ERROR tokens
        ``carrier_write_failed`` / ``carrier_marker_write_failed``), and the
        episode clock survives in memory so the 24h age cap can still
        terminate the episode (the bound).

        LEG 2 covers the ``_open_episode`` leg itself (the re-arm leg,
        which never delivers): its token is ``episode_open_failed``.

        LEG 3 is the F-N1 fold-in: the repository's MISSING-ROW shape (a
        warn, NO raise, a ``None`` result) must be reported as a FAILED
        carrier write — a success would retire the in-memory episode clock
        while the carrier stayed untracked, and the age cap could never
        fire on that path.
        """
        source_id = f"{PIN_TAG}-p14c-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_id)  # RUNNING = busy
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )

        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id, run_at=run_at
        )

        # ---- the carrier write RAISES (fault injection) -----------------
        source_repo = pin_manager._source_repository

        def _exploding_carrier_write(*args, **kwargs):
            raise RuntimeError("pin harness: the carrier write exploded")

        real_apply = source_repo.apply_scheduler_defer_state
        source_repo.apply_scheduler_defer_state = _exploding_carrier_write  # type: ignore[method-assign]
        try:
            with caplog.at_level(logging.INFO, logger=SCHED_LOGGER):
                await _fire_emit(adapter)
        finally:
            source_repo.apply_scheduler_defer_state = real_apply  # type: ignore[method-assign]

        # --- the delivery IS still there (delivery-first) -----------------
        jobs = _jobitems_for(pin_engine, instance_id)
        assert len(jobs) == 1, (
            "PIN 14 RED (H3 i): the deferred delivery was NOT enqueued even "
            f"though the carrier write failed ({len(jobs)} JobItems) — a "
            "carrier-write failure must not suppress the delivery"
        )
        assert len(_mq_for(pin_engine, instance_id)) == 1, (
            "PIN 14 RED (H3 i): no MessageQueue row — the delivery was "
            "suppressed by the carrier-write failure"
        )

        # --- the fire is LOUD about being untracked ----------------------
        sched_records = [r for r in caplog.records if r.name == SCHED_LOGGER]
        assert any(
            "carrier_write_failed" in r.getMessage() for r in sched_records
        ), (
            "PIN 14 RED (H3 i): no 'carrier_write_failed' ERROR was logged — "
            "the fire silently proceeded as if it were tracked "
            f"(records={[(r.levelname, r.getMessage()[:90]) for r in sched_records]})"
        )
        assert any(
            "carrier_marker_write_failed" in r.getMessage() for r in sched_records
        ), (
            "PIN 14 RED (H3 i): no 'carrier_marker_write_failed' ERROR was "
            "logged — the untracked episode is not visible"
        )

        # --- the episode clock survives in memory (the bound) -------------
        assert adapter._defer_since_in_memory is not None, (
            "PIN 14 RED (H3 i): the episode clock was not kept in memory — "
            "with an untracked episode the 24h age cap could never fire "
            "(an unbounded 5s re-arm spin)"
        )

        # ================= LEG 2 — the _open_episode leg ====================
        source_id_b = f"{PIN_TAG}-p14c2-{uuid.uuid4().hex[:8]}"
        instance_b = str(uuid.uuid4())
        run_at_b = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_b)
        # new_instance mode => NO deferrable target -> the re-arm leg (which
        # opens the episode and never delivers).
        _seed_source_and_mapping(
            pin_engine,
            source_id=source_id_b,
            instance_id=instance_b,
            run_at=run_at_b,
            instance_mode="new_instance",
        )
        adapter_b, _events_b, _completions_b = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id_b, run_at=run_at_b,
            instance_mode="new_instance",
        )
        assert adapter_b._execution_semaphore is not None
        await adapter_b._execution_semaphore.acquire()

        source_repo.apply_scheduler_defer_state = _exploding_carrier_write  # type: ignore[method-assign]
        try:
            with caplog.at_level(logging.INFO, logger=SCHED_LOGGER):
                await _fire_emit(adapter_b)
        finally:
            source_repo.apply_scheduler_defer_state = real_apply  # type: ignore[method-assign]

        sched_records_b = [r for r in caplog.records if r.name == SCHED_LOGGER]
        assert any(
            "episode_open_failed" in r.getMessage() for r in sched_records_b
        ), (
            "PIN 14 RED (H3 i, leg 2): no 'episode_open_failed' ERROR was "
            "logged — the untracked episode is not visible "
            f"(records={[(r.levelname, r.getMessage()[:90]) for r in sched_records_b]})"
        )
        assert adapter_b._defer_since_in_memory is not None, (
            "PIN 14 RED (H3 i, leg 2): the episode clock was not kept in "
            "memory — the 24h age cap could never fire on the untracked "
            "episode (an unbounded 5s re-arm spin)"
        )

        # ================= LEG 3 — F-N1: the MISSING-ROW shape ===========
        # The repository's return value must be HONOURED: a missing source
        # row makes ``apply_scheduler_defer_state`` return ``None`` (a warn,
        # no raise) — the write did NOT happen, so the wrapper must report a
        # FAILED write.  Reporting it as a success retired the in-memory
        # episode clock while the carrier stayed untracked, and the age cap
        # could never fire on that path.
        source_id_c = f"{PIN_TAG}-p14c3-{uuid.uuid4().hex[:8]}"
        instance_c = str(uuid.uuid4())
        run_at_c = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_c)
        _seed_source_and_mapping(
            pin_engine,
            source_id=source_id_c,
            instance_id=instance_c,
            run_at=run_at_c,
            instance_mode="new_instance",
        )
        adapter_c, _events_c, _completions_c = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id_c, run_at=run_at_c,
            instance_mode="new_instance",
        )
        assert adapter_c._execution_semaphore is not None
        await adapter_c._execution_semaphore.acquire()

        def _missing_row_write(source_id_arg, **kwargs):
            # The repository's "no such row" shape: a warn, NO raise, and a
            # ``None`` result — the write did not happen.
            return None

        # Isolate this leg's log records (caplog accumulates across the
        # legs — an earlier leg's token must not satisfy this assertion).
        log_mark = len(caplog.records)
        source_repo.apply_scheduler_defer_state = _missing_row_write  # type: ignore[method-assign]
        try:
            with caplog.at_level(logging.INFO, logger=SCHED_LOGGER):
                await _fire_emit(adapter_c)
        finally:
            source_repo.apply_scheduler_defer_state = real_apply  # type: ignore[method-assign]

        sched_records_c = [
            r for r in caplog.records[log_mark:] if r.name == SCHED_LOGGER
        ]
        assert any(
            "episode_open_failed" in r.getMessage() for r in sched_records_c
        ), (
            "PIN 14 RED (F-N1, leg 3): no 'episode_open_failed' ERROR was "
            "logged for the MISSING-ROW carrier write — a ``None`` repository "
            "result was reported as a success "
            f"(records={[(r.levelname, r.getMessage()[:90]) for r in sched_records_c]})"
        )
        assert adapter_c._defer_since_in_memory is not None, (
            "PIN 14 RED (F-N1, leg 3): the episode clock was RETIRED after a "
            "MISSING-ROW carrier write — the write did not happen, so the "
            "episode stays untracked and the 24h age cap could never fire on "
            "it (the bound is zero)"
        )

    async def test_clear_episode_failure_does_not_duplicate_the_terminal_row(
        self, pin_manager, pin_engine: Engine, caplog
    ):
        """H3(ii) — a failed carrier CLEAR must not duplicate the terminal row.

        RED MUTATION SPEC (verbatim): "H3: return-void swallow".

        Required POST-FIX behaviour: the clear failure is LOGGED (the token
        ``episode_clear_failed``) and the terminal disposition is NOT
        re-written on the next fire (the idempotency guard re-checks the
        last execution status) — the episode still terminates (the
        occurrence is consumed) exactly once.
        """
        source_id = f"{PIN_TAG}-p14d-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_id)  # RUNNING = busy
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )

        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id, run_at=run_at
        )

        # A stale, delivery-less episode -> the next fire hits the age cap.
        def _plant_stale_episode(cfg: dict) -> None:
            cfg["_defer_gen"] = 1
            cfg["_defer_count"] = 2
            cfg.pop("_defer_message_id", None)
            cfg["_defer_since"] = (
                datetime.now(timezone.utc) - timedelta(seconds=DEFER_AGE_CAP_S + 3600)
            ).isoformat()

        _edit_source_config(pin_engine, source_id, _plant_stale_episode)

        source_repo = pin_manager._source_repository
        real_apply = source_repo.apply_scheduler_defer_state

        def _exploding_clear(source_id_arg, **kwargs):
            if kwargs.get("clear_since"):
                raise RuntimeError("pin harness: the carrier clear exploded")
            return real_apply(source_id_arg, **kwargs)

        source_repo.apply_scheduler_defer_state = _exploding_clear  # type: ignore[method-assign]
        try:
            with caplog.at_level(logging.INFO, logger=SCHED_LOGGER):
                await _fire_emit(adapter)
                # ---- the SECOND fire: the episode lingers (the clear failed)
                await _fire_emit(adapter)
        finally:
            source_repo.apply_scheduler_defer_state = real_apply  # type: ignore[method-assign]

        cap_rows = [
            r
            for r in _schedule_executions(pin_engine, source_id)
            if r.error_message and "nondelivery_age_cap" in r.error_message
        ]
        assert len(cap_rows) == 1, (
            "PIN 14 RED (H3 ii): the terminal age-cap row was written "
            f"{len(cap_rows)} times — the idempotency guard must prevent a "
            "duplicated terminal disposition when the carrier clear fails"
        )

        sched_records = [r for r in caplog.records if r.name == SCHED_LOGGER]
        assert any(
            "episode_clear_failed" in r.getMessage() for r in sched_records
        ), (
            "PIN 14 RED (H3 ii): no 'episode_clear_failed' ERROR was logged — "
            "the lingering episode is not visible"
        )


# ===========================================================================
# PIN 15 (reopen 2) — TestEpisodeClockAccumulation  (B1: the in-memory
# episode clock must ACCUMULATE, not be re-stamped on every failure)
# ===========================================================================


class TestEpisodeClockAccumulation:
    """B1 — the IN-MEMORY episode clock must be SET-IF-ABSENT.

    THE DEFECT THIS PINS: the fallback clock that keeps the 24h age cap
    able to terminate an UNTRACKED episode (a failed carrier write) was
    PLAIN-OVERWRITTEN on every failed write.  On the ~5s re-arm cadence
    the clock was re-stamped every tick, so the visible age never reached
    the cap — the bound H3(i) provides was ZERO and the untracked episode
    could spin forever.
    """

    async def test_in_memory_episode_clock_accumulates_and_the_cap_can_trip(
        self, pin_manager, pin_engine: Engine, caplog
    ):
        """B1 — the clock must not move on every failed carrier write.

        RED MUTATION SPEC (verbatim from the review): "the plain
        overwrite" — every failed carrier write re-stamps the clock.

        Required POST-FIX behaviour: the clock is SET-IF-ABSENT — two
        failed opens separated by a TIME ADVANCE leave the clock where the
        FIRST failure put it, and the ACCUMULATED age is what the cap sees
        (the cap really can trip on an untracked episode).
        """
        source_id = f"{PIN_TAG}-p15a-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_id)
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )
        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id, run_at=run_at
        )

        # ---- the carrier write RAISES (the untracked-episode world) ------
        source_repo = pin_manager._source_repository

        def _exploding_carrier_write(*args, **kwargs):
            raise RuntimeError("pin harness: the carrier write exploded")

        real_apply = source_repo.apply_scheduler_defer_state
        source_repo.apply_scheduler_defer_state = _exploding_carrier_write  # type: ignore[method-assign]
        try:
            with caplog.at_level(logging.INFO, logger=SCHED_LOGGER):
                # ---- failed open #1 -> the clock starts ------------------
                opened = await adapter._open_episode()
                clock_first = adapter._defer_since_in_memory
                assert opened is False, (
                    "PIN 15 RED (B1): the failed carrier write was NOT "
                    "reported as a failure — the fixture is broken"
                )
                assert clock_first is not None, (
                    "PIN 15 RED (B1): no in-memory episode clock after a "
                    "failed carrier write — an untracked episode could never "
                    "be terminated by the age cap"
                )

                # ---- a TIME ADVANCE -------------------------------------
                await asyncio.sleep(1.2)

                # ---- failed open #2 -> the clock must NOT move ----------
                await adapter._open_episode()
                clock_second = adapter._defer_since_in_memory
        finally:
            source_repo.apply_scheduler_defer_state = real_apply  # type: ignore[method-assign]

        # --- and the cap really can trip on the untracked episode ---------
        # (asserted FIRST: it is the OUTCOME property — the bound H3(i)
        # exists to provide.  The mechanism checks below explain WHY it
        # trips.)
        source_repo.apply_scheduler_defer_state = _exploding_carrier_write  # type: ignore[method-assign]
        try:
            with caplog.at_level(logging.INFO, logger=SCHED_LOGGER):
                breach = await adapter._handle_age_cap_breach(
                    adapter._defer_state(), 1, instance_id
                )
        finally:
            source_repo.apply_scheduler_defer_state = real_apply  # type: ignore[method-assign]

        assert breach == adapter.FIRE_OUTCOME_REFUSED_PERMANENT, (
            "PIN 15 RED (B1): the 24h age cap did NOT trip on the untracked "
            f"episode (outcome={breach!r}) — the in-memory clock's age never "
            "reached the cap, so the bound the clock exists to provide is zero"
        )

        # --- the clock did NOT move ---------------------------------------
        assert clock_second == clock_first, (
            "PIN 15 RED (B1): the in-memory episode clock MOVED on the second "
            f"failed carrier write ({clock_first} -> {clock_second}) — the "
            "clock must be SET-IF-ABSENT (its age accumulates from the FIRST "
            "failed write); a plain overwrite re-stamps it on every re-arm "
            "tick, so the age never reaches the cap (the bound is zero)"
        )

        # --- the ACCUMULATED age is what the cap sees ---------------------
        accumulated_age = (
            datetime.now(timezone.utc) - datetime.fromisoformat(str(clock_second))
        ).total_seconds()
        assert accumulated_age >= 1.0, (
            "PIN 15 RED (B1): the accumulated age of the in-memory clock is "
            f"{accumulated_age:.3f}s after a 1.2s advance — the cap would "
            "not see the episode's real age"
        )


# ===========================================================================
# PIN 16 (reopen 2) — TestEpisodeScopedTerminalGuard  (W-A: the age-cap
# terminal-disposition guard must be scoped to the EPISODE)
# ===========================================================================


class TestEpisodeScopedTerminalGuard:
    """W-A — the age-cap terminal-disposition guard is EPISODE-SCOPED.

    THE DEFECT THIS PINS: the guard asked "is the LATEST execution row a
    terminal ``nondelivery_age_cap`` row?" — a question about the ROW
    LIST, not about the EPISODE.  It had two failure modes:

    * an UNRELATED execution row landing between two breaches of the SAME
      episode hid the earlier terminal row -> the duplicate terminal row
      was written (the suppression missed);
    * a terminal row of an EARLIER episode suppressed a LATER episode's
      legitimate terminal -> the later episode terminated SILENTLY, with no
      terminal row of its own (a false disposition).

    The guard now asks about the EPISODE: the terminal row carries the
    episode's key (``since=``) and suppresses the write only when it
    matches.
    """

    async def test_terminal_guard_is_scoped_to_the_episode(
        self, pin_manager, pin_engine: Engine, caplog
    ):
        """W-A — the guard must be episode-scoped, not latest-row-any.

        RED MUTATION SPEC (verbatim from the review): "the current
        latest-row-any predicate".

        Required POST-FIX behaviour:

        * LEG 1 (the SAME episode, an unrelated row interleaved): the
          duplicate terminal row is STILL suppressed;
        * LEG 2 (a LATER episode): the legitimate terminal row IS still
          written (an earlier episode's terminal row must not suppress it).
        """
        # ================= LEG 1 — the same episode, interleaved ==========
        source_id = f"{PIN_TAG}-p16a-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_id)
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )
        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id, run_at=run_at
        )

        source_repo = pin_manager._source_repository

        def _exploding_carrier_write(*args, **kwargs):
            raise RuntimeError("pin harness: the carrier write exploded")

        real_apply = source_repo.apply_scheduler_defer_state
        source_repo.apply_scheduler_defer_state = _exploding_carrier_write  # type: ignore[method-assign]
        try:
            with caplog.at_level(logging.INFO, logger=SCHED_LOGGER):
                # the episode starts (untracked: the carrier write fails)
                await adapter._open_episode()
                # ---- breach #1 (max_age=0 -> the episode is already due) --
                first = await adapter._handle_age_cap_breach(
                    adapter._defer_state(), 0, instance_id
                )
        finally:
            source_repo.apply_scheduler_defer_state = real_apply  # type: ignore[method-assign]

        assert first == adapter.FIRE_OUTCOME_REFUSED_PERMANENT, (
            "PIN 16 RED (W-A, leg 1): the first breach did not terminate the "
            f"episode visibly (outcome={first!r}) — the fixture is broken"
        )

        # ---- an UNRELATED execution row lands between the two breaches ---
        unrelated_id = f"unrelated-{uuid.uuid4().hex[:8]}"
        source_repo.record_execution_start(
            source_id, instance_id=instance_id, execution_id=unrelated_id
        )
        source_repo.record_execution_complete(unrelated_id, "completed", None)

        # ---- breach #2 — the SAME episode (the clear failed, it lingers) --
        source_repo.apply_scheduler_defer_state = _exploding_carrier_write  # type: ignore[method-assign]
        try:
            with caplog.at_level(logging.INFO, logger=SCHED_LOGGER):
                second = await adapter._handle_age_cap_breach(
                    adapter._defer_state(), 0, instance_id
                )
        finally:
            source_repo.apply_scheduler_defer_state = real_apply  # type: ignore[method-assign]

        assert second == adapter.FIRE_OUTCOME_REFUSED_PERMANENT, (
            "PIN 16 RED (W-A, leg 1): the second breach did not terminate the "
            f"episode (outcome={second!r}) — the fixture is broken"
        )
        cap_rows = [
            r
            for r in _schedule_executions(pin_engine, source_id)
            if r.error_message and "nondelivery_age_cap" in r.error_message
        ]
        assert len(cap_rows) == 1, (
            "PIN 16 RED (W-A, leg 1): the duplicate terminal row was written "
            f"for the SAME episode ({len(cap_rows)} rows) — an unrelated "
            "execution row between the two breaches defeated the suppression; "
            "the guard must be episode-scoped, not latest-row-any"
        )

        # ================= LEG 2 — a LATER episode ==========================
        source_id_b = f"{PIN_TAG}-p16b-{uuid.uuid4().hex[:8]}"
        instance_id_b = str(uuid.uuid4())
        run_at_b = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(pin_engine, instance_id=instance_id_b)
        _seed_source_and_mapping(
            pin_engine, source_id=source_id_b, instance_id=instance_id_b, run_at=run_at_b
        )
        adapter_b, _events_b, _completions_b = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id_b, run_at=run_at_b
        )

        # ---- episode 1: an old, TRACKED episode ---------------------------
        episode1_since = (
            datetime.now(timezone.utc) - timedelta(seconds=DEFER_AGE_CAP_S + 3600)
        ).isoformat()
        _edit_source_config(
            pin_engine,
            source_id_b,
            lambda cfg: cfg.update({"_defer_since": episode1_since}),
        )
        first_b = await adapter_b._handle_age_cap_breach(
            adapter_b._defer_state(), 1, instance_id_b
        )
        assert first_b == adapter_b.FIRE_OUTCOME_REFUSED_PERMANENT, (
            "PIN 16 RED (W-A, leg 2): the first episode did not terminate "
            f"visibly (outcome={first_b!r}) — the fixture is broken"
        )

        # ---- episode 2: a NEW episode (a new episode key) -----------------
        episode2_since = (
            datetime.now(timezone.utc) - timedelta(seconds=DEFER_AGE_CAP_S + 7200)
        ).isoformat()
        _edit_source_config(
            pin_engine,
            source_id_b,
            lambda cfg: cfg.update({"_defer_since": episode2_since}),
        )
        second_b = await adapter_b._handle_age_cap_breach(
            adapter_b._defer_state(), 1, instance_id_b
        )
        assert second_b == adapter_b.FIRE_OUTCOME_REFUSED_PERMANENT, (
            "PIN 16 RED (W-A, leg 2): the second episode did not terminate "
            f"visibly (outcome={second_b!r}) — the fixture is broken"
        )
        cap_rows_b = [
            r
            for r in _schedule_executions(pin_engine, source_id_b)
            if r.error_message and "nondelivery_age_cap" in r.error_message
        ]
        assert len(cap_rows_b) == 2, (
            "PIN 16 RED (W-A, leg 2): the LATER episode's legitimate terminal "
            f"row was suppressed ({len(cap_rows_b)} rows, expected 2) — an "
            "earlier episode's terminal row must not suppress it (that is the "
            "false-disposition class: the episode terminates SILENTLY)"
        )


# ===========================================================================
# PIN 12 (commission D6) — TestPausedCaller  (DEC-11 / ADR-7, substrate-
# bounded: the full receipt is the e2e pack's obligation, plan optional (f))
# ===========================================================================


class TestPausedCaller:
    """PIN 13 (RED at base).

    Required POST-FIX behaviour (D6 / DEC-11): a PAUSED caller is
    NON-blocking — the fire DELIVERS (a PENDING Task + JobItem + MQ row
    exist; never a 'deferred' row) and the Task is gated while paused by
    the claim path's SQL pause gate, to land on the first turn after
    resume (ADR-7).  SUBSTRATE-BOUNDED (plan optional f): this unit pin
    proves the DECISION; the full receipt (the alarm landing on the first
    turn after resume) is the E2E PACK's obligation — real PG, isolated
    systemd-run transient unit, never the live daemon's cgroup.

    RED MUTATION (plan §(d) D6 / DEC-11): widen the busy predicate with
    PAUSED (or drop the paused delivery path) → RED — the paused caller
    then defers (a 'deferred' row appears) or the fire vanishes (no
    delivery rows), and the "no deferral + delivery exists + claim-gated"
    assertion set fails.
    """

    async def test_paused_caller_is_not_blocking_and_receives_the_alarm(
        self, pin_manager, pin_engine: Engine
    ):
        source_id = f"{PIN_TAG}-p13-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(
            pin_engine, instance_id=instance_id, status=InstanceStatus.PAUSED.value
        )
        _seed_source_and_mapping(
            pin_engine, source_id=source_id, instance_id=instance_id, run_at=run_at
        )

        adapter, _events, _completions = _build_pin_adapter(
            pin_manager, pin_engine, source_id=source_id, run_at=run_at
        )

        await _fire_once(adapter, timeout_s=8.0)

        # --- the alarm is DELIVERED (queued), never deferred -------------
        deferred_rows = [
            r for r in _schedule_executions(pin_engine, source_id)
            if r.status == "deferred"
        ]
        assert deferred_rows == [], (
            "PIN 13 RED: the paused caller produced a DEFERRAL — PAUSED is "
            f"not in the busy set (rows={deferred_rows})"
        )
        pending_tasks = [
            t for t in _tasks_for(pin_engine, instance_id)
            if t.status == TaskStatus.PENDING.value
        ]
        assert len(pending_tasks) == 1, (
            "PIN 13 RED: the paused caller received NO durable delivery "
            f"(pending tasks={pending_tasks}) — at base the fire is dropped "
            "and the one-shot consumed-and-lost"
        )
        jobs = _jobitems_for(pin_engine, instance_id)
        assert len(jobs) == 1, (
            f"PIN 13 RED: expected exactly 1 JobItem for the paused-caller "
            f"delivery, got {len(jobs)}"
        )
        assert _mq_for(pin_engine, instance_id), (
            "PIN 13 RED: no MessageQueue row for the paused-caller delivery"
        )

        # --- the Task is GATED while paused (claim-path SQL pause gate) --
        _set_job_admission(pin_engine, jobs[0].job_id, AdmissionState.ACTIVE.value)
        claimed = pin_manager._task_repo.claim_pending_task("pin-worker-p13")
        assert claimed is None or claimed.id != pending_tasks[0].id, (
            "PIN 13 RED: the paused instance's PENDING task was CLAIMABLE "
            "while the instance is PAUSED — the claim-path pause gate "
            "(task/repository.py:3008-3030) is bypassed"
        )

        # --- the direct delivery consumed the one-shot (direct outcome) --
        row = _source_row(pin_engine, source_id)
        assert row is not None
        assert row.status == "stopped" and row.enabled is False, (
            f"PIN 13 RED: expected the one-shot consumed on the direct "
            f"delivery (status={row.status!r}, enabled={row.enabled})"
        )
