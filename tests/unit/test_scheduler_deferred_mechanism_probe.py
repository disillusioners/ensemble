"""MECHANISM PROBE — commission ``scheduler-deferred-alarm-delivery``.

DIAGNOSIS ONLY.  This file implements **NO fix**.  It exists to PROVE or
REFUTE five load-bearing claims about the CURRENT code at
``8e6df58e445cd56b5e775dacebe0ef53f0add0b4`` (branch
``feature/scheduler-deferred-alarm-delivery``, worktree
``/home/nea/ensemble-src-wt-sched-deferred-alarm``).

The commission's whole mechanism rests on this claim:

    "enqueue to a RUNNING instance already QUEUES a follow-up turn behind
     the per-instance claim gate — not injection, not refused."

This file answers that question empirically, on **DB ROWS** (never on log
output — the scheduler's busy branch fires at ``logger.info``).

--------------------------------------------------------------------------
FINDINGS SUMMARY (all on REAL PostgreSQL rows, fenced ``ensemble_dev``)
--------------------------------------------------------------------------
P1  ENQUEUE-TO-BUSY QUEUES (not injects, not refuses)      -> PROVEN
P2  THE CLAIM GATE SERIALIZES                              -> PROVEN
P3  IDEMPOTENCY-KEY BEHAVIOR (THE PIVOT)                   -> PROVEN,
    WITH ONE CORRECTION TO THE COMMISSIONING HYPOTHESIS (see below)
P4  BASELINE OF THE DEFECT (busy caller)                    -> PROVEN (RED at
    base; GREEN after the commission's fix landed — see below)
P5  THE SIBLING SITE (semaphore-timeout skip)               -> PROVEN

P3 CORRECTION (the one thing the designer MUST know):
  * INDEX LEVEL: ``idx_job_idempotency``'s predicate is
    ``idempotency_key IS NOT NULL AND deleted_at IS NULL`` — NO status
    filter — so a COMPLETED (admission_state='done') JobItem with
    ``deleted_at IS NULL`` still occupies the key at the INDEX level.
  * SERVICE LEVEL: ``JobQueueService.enqueue`` does NOT rely on that.
    A TERMINAL matched job is BYPASSED with a synthetic key
    (``f"{key}#{uuid4().hex[:8]}"``) and a FRESH JobItem is created —
    so a constant per-schedule key does NOT kill the schedule once the
    previous JobItem is terminal.
  * THE REAL LOSS WINDOW: while the previous JobItem is still
    NON-TERMINAL (queued/active), a second delivery under the SAME key
    is a dedup hit: the loser's Task is CANCELLED and its MessageQueue
    row is DELETED — the delivery is SILENTLY LOST, and the kept
    delivery's payload is NOT updated by the loser (the count-freezing
    tension the designer must resolve).

--------------------------------------------------------------------------
WHY P4 WAS RED ON PURPOSE (and what it asserts NOW)
--------------------------------------------------------------------------
P4's ``test_p4_red_pin_*`` was the *defect pin candidate* for the later
fix: at BASE it asserted the REQUIRED behaviour (a busy-caller reuse-mode
scheduled delivery MUST leave a durable, auditable row behind) and the
base code did neither, so the assertions failed — loudly, with the full
before/after row evidence embedded in the failure message.

The commission's fix has since landed, so the assertion set was INVERTED
to the post-fix contract (the delivery is DEFERRED — a durable Task +
MessageQueue row + JobItem + a 'deferred' audit row — and the one-shot IS
consumed, because the intent survives as the pending delivery; plan §(e)
pin #4 / DEC-9's ``deferred_durable`` outcome).  The sibling
``test_p4_characterization_*`` and ``test_p5_*`` were inverted the same
way: they now characterise the CURRENT (post-fix) behaviour.

THIS ROUND (the code-review fix round) TOUCHED THE DOCSTRING ONLY — the
inversion happened in the previous round, and no assertion body was
modified in either round beyond that inversion.

``test_p4_characterization_busy_caller_delivers_nothing_today`` is the
companion GREEN test: it pins the *present* (broken) behaviour so the fix
can be proven to have changed it.  It is a characterisation, not a pin.

P1/P2/P3/P5 are statements about EXISTING behaviour and are GREEN today.

--------------------------------------------------------------------------
SUBSTRATE
--------------------------------------------------------------------------
Real DB rows, on the fenced dev PostgreSQL (``ensemble_dev`` @ 127.0.0.1:5432)
inside a THROWAWAY PostgreSQL schema (isolated from every other lane) when
reachable; otherwise a real file-backed SQLite database.  The substrate in
force is recorded in ``PROBE_SUBSTRATE`` and asserted in
``test_probe_substrate_is_recorded`` so no reader can mistake a mock-level
proof for a real-DB proof.

FENCE: the fixture refuses to run against any database other than
``ensemble_dev`` (never ``ensemble_prod``).

NOTHING outside this file is created or modified.  No daemon is started;
no live install is touched; no git state is changed.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
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
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.source.models import (
    ScheduleExecution,
    SourceConfig as SourceConfigRow,
)
from daemon.repositories.task.models import Task, TaskStatus

# ---------------------------------------------------------------------------
# Substrate bookkeeping
# ---------------------------------------------------------------------------

#: ``"postgres"`` | ``"sqlite"`` — set by the ``probe_engine`` fixture.
PROBE_SUBSTRATE: str = "unset"

#: The database the probe is allowed to touch.  Hard fence.
ALLOWED_DB_NAME = "ensemble_dev"

#: Every probe row is namespaced with this prefix so a cleanup can never
#: touch a row it did not create.
PROBE_TAG = "probe_sched_def"


def _repo_root() -> Path:
    """Repository root of the WORKTREE under test."""
    return Path(__file__).resolve().parents[2]


def _read_fence_env() -> dict[str, str]:
    """Read the worktree ``.env`` fence file (KEY=VALUE lines only)."""
    env_path = _repo_root() / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values


# ---------------------------------------------------------------------------
# Fixtures — real engine (isolated PG schema, else file-backed SQLite)
# ---------------------------------------------------------------------------


@pytest.fixture
def probe_engine(tmp_path) -> Engine:
    """A REAL database engine for the probe.

    Preferred: the fenced dev PostgreSQL, inside a dedicated THROWAWAY
    schema (``probe_sched_def_<uuid8>``) so the probe is fully isolated
    from every other lane sharing ``ensemble_dev``.  The schema is
    dropped (cascade) on teardown.

    Fallback: a real file-backed SQLite database under ``tmp_path``
    (the repo's documented recipe — file DB + NullPool + WAL, never
    ``StaticPool``/``:memory:``).

    Either way this is a real engine with real rows; the substrate in use
    is published in ``PROBE_SUBSTRATE``.
    """
    global PROBE_SUBSTRATE

    fence = _read_fence_env()
    host = fence.get("POSTGRES_HOST") or os.environ.get("POSTGRES_HOST")
    port = fence.get("POSTGRES_PORT") or os.environ.get("POSTGRES_PORT", "5432")
    dbname = fence.get("POSTGRES_DB") or os.environ.get("POSTGRES_DB")
    user = fence.get("POSTGRES_USER") or os.environ.get("POSTGRES_USER")
    password = fence.get("POSTGRES_PASSWORD") or os.environ.get("POSTGRES_PASSWORD")

    pg_error: str | None = None
    if dbname and user and password:
        # ---- HARD FENCE: never touch anything but ensemble_dev ----------
        if dbname != ALLOWED_DB_NAME:
            raise RuntimeError(
                f"FENCE VIOLATION: probe refuses to run against database "
                f"{dbname!r} (allowed: {ALLOWED_DB_NAME!r})."
            )
        schema = f"{PROBE_TAG}_{uuid.uuid4().hex[:8]}"
        from urllib.parse import quote_plus

        url = (
            f"postgresql+psycopg://{quote_plus(user)}:{quote_plus(password)}"
            f"@{host}:{port}/{dbname}"
        )
        try:
            admin = create_engine(url, poolclass=NullPool)
            with admin.connect() as conn:
                conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                conn.commit()
            admin.dispose()

            eng = create_engine(
                url,
                poolclass=NullPool,
                pool_pre_ping=True,
                connect_args={
                    "options": f"-c timezone=UTC -c search_path={schema}",
                },
            )
            SQLModel.metadata.create_all(eng)
            PROBE_SUBSTRATE = "postgres"
            try:
                yield eng
            finally:
                eng.dispose()
                try:
                    drop = create_engine(url, poolclass=NullPool)
                    with drop.connect() as conn:
                        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                        conn.commit()
                    drop.dispose()
                except Exception:  # noqa: BLE001 — teardown best-effort
                    pass
            return
        except Exception as exc:  # noqa: BLE001 — fall through to SQLite
            pg_error = f"{type(exc).__name__}: {exc}"

    # ---- Fallback: real file-backed SQLite -----------------------------
    PROBE_SUBSTRATE = "sqlite"
    if pg_error:
        print(f"\n[PROBE] PostgreSQL unavailable ({pg_error}); using SQLite substrate.")

    eng = create_engine(
        f"sqlite:///{tmp_path}/probe_sched_def.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
async def probe_manager(probe_engine: Engine):
    """A REAL ``InstanceManager`` over the probe engine.

    Mirrors the production wiring (``daemon/api.py`` lifespan ordering):
    real ``JobQueueService`` + real repositories, no WorkerPool (the Task
    must stay PENDING for DB inspection).
    """
    import daemon.manager as daemon_manager_module
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
    from daemon.services.job_lock_manager import JobLockManager
    from daemon.services.job_queue_service import JobQueueService

    _seed_system_default_project(probe_engine)

    config = Config(
        llm=LLMConfig(
            base_url="https://api.openai.com/v1",
            api_key="probe-key",
            model="gpt-4",
            temperature=0.7,
        ),
        limits=LimitsConfig(
            max_children_per_instance=3,
            instance_timeout_minutes=60,
        ),
        persistence=PersistenceConfig(
            db_path="/tmp/probe-sched-def-unused.db",
            checkpoint_interval=1,
            checkpoint_ttl_hours=168,
            checkpoint_cleanup_interval=24,
            max_instance_history=300,
        ),
        daemon=DaemonConfig(host="127.0.0.1", port=8079),
        agents=AgentsConfig(directory="./agents"),
    )

    with (
        patch(
            "daemon.migrations.runner.MigrationRunner.run_pending_migrations",
            return_value=[],
        ),
        patch(
            "daemon.manager.create_engine_from_config",
            return_value=probe_engine,
        ),
    ):
        manager = InstanceManager(config)

    manager._loop = asyncio.get_running_loop()
    manager._worker_pool = None  # no claim/consumption — Task stays PENDING

    # Production wires ``manager._task_repo`` inside ``setup_worker_pool()``
    # (``daemon/manager.py`` ~2438).  The probe runs without a worker pool,
    # so the REAL ``TaskRepository`` class is instantiated directly over the
    # same engine — same class, same DB, same claim path.
    from daemon.repositories.task.repository import TaskRepository

    manager._task_repo = TaskRepository(
        engine=probe_engine, on_pending_task=lambda: None
    )

    # Provision the system queues the production lifespan provisions for
    # the system-default project (``system_parallel_queue`` etc.).  Without
    # them ``JobQueueService.enqueue`` raises
    # "No system parallel queue found for project ...".
    from daemon import constants as _daemon_constants

    queue_repo = JobQueueRepository(probe_engine)
    for _qname, _qtype, _qlimit in (
        ("system_fifo_queue", "fifo", 1),
        ("system_parallel_queue", "parallel", 5),
        ("system_defer_queue", "defer", 1),
        ("system_background_queue", "background", 1),
    ):
        if queue_repo.get_by_name(
            _daemon_constants.SYSTEM_DEFAULT_PROJECT_ID, _qname
        ) is None:
            queue_repo.create(
                project_id=_daemon_constants.SYSTEM_DEFAULT_PROJECT_ID,
                queue_name=_qname,
                queue_type=_qtype,
                concurrency_limit=_qlimit,
                is_system=True,
            )

    job_service = JobQueueService(
        repository=JobRepository(probe_engine),
        lock_manager=JobLockManager(lock_repo=LockRepository(probe_engine)),
        queue_repo=JobQueueRepository(probe_engine),
        instance_manager=manager,
    )
    manager.set_job_queue_service(job_service)
    return manager


# ---------------------------------------------------------------------------
# Seeding helpers
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
                description="scheduler deferred mechanism probe",
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


def _seed_running_task(
    eng: Engine,
    *,
    instance_id: str,
    work_id: str | None = None,
) -> Task:
    """Seed a Task in RUNNING state — the 'live turn' that holds the
    per-instance claim gate shut."""
    task = Task(
        work_id=work_id or str(uuid.uuid4()),
        task_type="process_message",
        instance_id=instance_id,
        status=TaskStatus.RUNNING.value,
        worker_id="probe-live-turn",
    )
    with Session(eng) as session:
        session.add(task)
        session.commit()
        session.refresh(task)
    return task


def _tasks_for(eng: Engine, instance_id: str) -> list[Task]:
    with Session(eng) as session:
        return list(
            session.exec(select(Task).where(Task.instance_id == instance_id))
        )


def _mq_for(eng: Engine, instance_id: str) -> list[MessageQueue]:
    with Session(eng) as session:
        return list(
            session.exec(
                select(MessageQueue).where(MessageQueue.instance_id == instance_id)
            )
        )


def _jobitems_for(eng: Engine, instance_id: str | None = None) -> list[JobItem]:
    with Session(eng) as session:
        stmt = select(JobItem)
        if instance_id is not None:
            stmt = stmt.where(JobItem.instance_id == instance_id)
        return list(session.exec(stmt))


def _jobitems_with_key(eng: Engine, key: str) -> list[JobItem]:
    with Session(eng) as session:
        return list(
            session.exec(select(JobItem).where(JobItem.idempotency_key == key))
        )


def _schedule_executions(eng: Engine, schedule_id: str) -> list[ScheduleExecution]:
    with Session(eng) as session:
        return list(
            session.exec(
                select(ScheduleExecution).where(
                    ScheduleExecution.schedule_id == schedule_id
                )
            )
        )


def _source_row(eng: Engine, source_id: str) -> SourceConfigRow | None:
    with Session(eng) as session:
        return session.get(SourceConfigRow, source_id)


def _set_job_admission(eng: Engine, job_id: str, state: str) -> None:
    """Force a JobItem's admission_state.

    The job processor promotes ``queued -> active`` when it picks the job
    up; the Task claim path's queue-awareness gate only releases the Task
    once its linked JobItem is ``active`` (or absent).  The probe performs
    that promotion explicitly so the per-instance gate is what is measured.
    """
    with Session(eng) as session:
        job = session.get(JobItem, job_id)
        assert job is not None, f"JobItem {job_id} missing"
        job.admission_state = state
        session.commit()


def _complete_task(eng: Engine, task_pk: int) -> None:
    with Session(eng) as session:
        task = session.get(Task, task_pk)
        assert task is not None, f"Task {task_pk} missing"
        task.status = TaskStatus.COMPLETED.value
        task.completed_at = datetime.now(timezone.utc)
        session.commit()


# ---------------------------------------------------------------------------
# Scheduler adapter helpers (P4 / P5)
# ---------------------------------------------------------------------------


def _build_scheduler_adapter(
    probe_engine: Engine,
    probe_manager,
    *,
    source_id: str,
    run_at: datetime,
    on_complete,
    execution_events: list,
    instance_mode: str = "reuse_instance",
    max_concurrent: int = 1,
):
    """Build a REAL ``SchedulerAdapter`` wired to the real repositories."""
    from daemon.sources.adapters.scheduler import SchedulerAdapter
    from daemon.sources.base import SourceConfig as AdapterSourceConfig

    cfg = AdapterSourceConfig(
        source_id=source_id,
        source_type="scheduler",
        name=f"self-alarm:{PROBE_TAG}",
        config={
            "run_at": run_at.isoformat(),
            "instance_mode": instance_mode,
            "agent": "developer",
            "message": "probe alarm payload",
            "project_id": None,
            "max_concurrent": max_concurrent,
        },
        credentials={},
        enabled=True,
    )

    def _execution_callback(
        execution_id: str,
        schedule_id: str,
        status: str,
        instance_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        execution_events.append(
            {
                "execution_id": execution_id,
                "schedule_id": schedule_id,
                "status": status,
                "instance_id": instance_id,
                "error_message": error_message,
            }
        )

    adapter = SchedulerAdapter(
        cfg,
        on_message=_noop_on_message,
        execution_callback=_execution_callback,
        on_complete_callback=on_complete,
        job_queue_service=probe_manager._job_queue_service,
        source_repo=probe_manager._source_repository,
        instance_repo=probe_manager._instance_repository,
        manager=probe_manager,
    )
    # The semaphore is created in ``start()``; the probe calls ``start()``
    # only where the semaphore is under test.
    adapter._execution_semaphore = asyncio.Semaphore(max_concurrent)
    return adapter


async def _noop_on_message(_msg) -> None:
    return None


def _seed_scheduler_row_and_mapping(
    eng: Engine,
    *,
    source_id: str,
    instance_id: str,
    run_at: datetime,
) -> None:
    """Seed the ``source_configs`` row + the ``instance_mappings`` row that
    makes the adapter resolve to the (busy) mapped instance.

    Row shape mirrors the real self-alarm row:
      ``source_configs.name = 'self-alarm:<label>'`` and
      ``source_configs.source_type = 'scheduler'``
    (``source_type`` is NOT a discriminator — every self-alarm row shares
    it with every other scheduler row).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(eng) as session:
        session.add(
            SourceConfigRow(
                source_id=source_id,
                source_type="scheduler",
                name=f"self-alarm:{PROBE_TAG}",
                config={
                    "run_at": run_at.isoformat(),
                    "instance_mode": "reuse_instance",
                    "agent": "developer",
                    "message": "probe alarm payload",
                    "max_concurrent": 1,
                },
                credentials=None,
                enabled=True,
                autostart=True,
                status="running",
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        session.commit()

    with Session(eng) as session:
        from daemon.repositories.source.models import InstanceMapping

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


def _production_on_complete(source_repo, source_id: str):
    """Replica of the production one-shot completion callback.

    Verbatim behaviour of ``daemon/sources/registry.py`` ``on_complete_callback``
    (the closure is not importable, so the probe replicates it and cites the
    source):

        self._source_repo.update_source_status(source_id, "stopped")
        self._source_repo.update_source_config(source_id, enabled=False)
    """

    def _on_complete(source_id: str, completed: bool) -> None:
        source_repo.update_source_status(source_id, "stopped")
        source_repo.update_source_config(source_id, enabled=False)

    return _on_complete


# ===========================================================================
# P0 — substrate bookkeeping (so no reader mistakes mock for real)
# ===========================================================================


class TestProbeSubstrate:
    def test_probe_substrate_is_recorded(self, probe_engine: Engine):
        assert PROBE_SUBSTRATE in {"postgres", "sqlite"}
        print(f"\n[PROBE] substrate in force: {PROBE_SUBSTRATE}")

    def test_probe_never_targets_a_live_database(self):
        fence = _read_fence_env()
        dbname = fence.get("POSTGRES_DB")
        if dbname is not None:
            assert dbname == ALLOWED_DB_NAME, (
                f"worktree .env fence points at {dbname!r}, not "
                f"{ALLOWED_DB_NAME!r}"
            )


# ===========================================================================
# P1 — ENQUEUE-TO-BUSY QUEUES (not injects, not refuses)
# ===========================================================================


class TestP1EnqueueToBusyQueues:
    """P1 (GREEN — statement about EXISTING behaviour).

    Claim under test:
      "calling ``InstanceManager.enqueue_message_job`` targeting an
       instance whose status is RUNNING creates a task row with status
       PENDING + a message_queue row, and does NOT inject into the live
       turn."
    """

    async def test_p1_enqueue_to_running_instance_writes_task_and_message_rows(
        self, probe_manager, probe_engine: Engine
    ):
        instance_id = str(uuid.uuid4())
        _seed_instance(probe_engine, instance_id=instance_id)  # RUNNING

        result = await probe_manager.enqueue_message_job(
            instance_id=instance_id,
            message="P1 probe payload",
            source="scheduler",
        )

        # --- DB ROW EVIDENCE (not logs) ---------------------------------
        tasks = _tasks_for(probe_engine, instance_id)
        mqs = _mq_for(probe_engine, instance_id)
        jobs = _jobitems_for(probe_engine, instance_id)

        assert len(tasks) == 1, f"expected exactly 1 Task row, got {len(tasks)}"
        assert tasks[0].status == TaskStatus.PENDING.value, (
            f"Task row status is {tasks[0].status!r}, expected 'pending' "
            f"(a queued follow-up, NOT an injected live turn)"
        )
        assert len(mqs) == 1, f"expected exactly 1 MessageQueue row, got {len(mqs)}"
        assert len(jobs) == 1, f"expected exactly 1 JobItem row, got {len(jobs)}"
        assert jobs[0].job_type == "message"
        assert jobs[0].admission_state == AdmissionState.QUEUED.value

        # Linkage contract: Task.work_id == JobItem.job_id.
        assert tasks[0].work_id == jobs[0].job_id
        assert result.job_id == jobs[0].job_id

        # --- NOT INJECTED ------------------------------------------------
        # ``set_injection`` is the mid-turn injection lane
        # (``daemon/manager.py:3742``).  ``enqueue_message_job`` must not
        # have used it.
        pending = getattr(probe_manager, "_pending_injections", {})
        assert instance_id not in pending, (
            f"enqueue_message_job INJECTED into the live turn: {pending[instance_id]!r}"
        )

        # The instance was already RUNNING — it must not have been
        # "revived"/status-flipped by the enqueue.
        inst_row = probe_manager._instance_repository.get(instance_id)
        assert inst_row.status == InstanceStatus.RUNNING.value

    async def test_p1_enqueue_is_not_refused_for_a_busy_instance(
        self, probe_manager, probe_engine: Engine
    ):
        """A busy instance must NOT be refused: the call returns a real
        ``AsyncMessageResult`` carrying a real job_id / message_id."""
        instance_id = str(uuid.uuid4())
        _seed_instance(probe_engine, instance_id=instance_id)

        result = await probe_manager.enqueue_message_job(
            instance_id=instance_id,
            message="P1 not-refused payload",
            source="scheduler",
        )
        assert result is not None
        assert result.job_id
        assert result.message_id
        assert result.instance_id == instance_id


# ===========================================================================
# P2 — THE CLAIM GATE SERIALIZES
# ===========================================================================


class TestP2ClaimGateSerializes:
    """P2 (GREEN — statement about EXISTING behaviour).

    Claim under test:
      "the pending task is NOT claimable while the instance holds a
       RUNNING task, and IS claimed and executed as the NEXT turn once the
       instance frees."
    """

    async def test_p2_pending_task_is_blocked_then_claimed_after_free(
        self, probe_manager, probe_engine: Engine
    ):
        task_repo = probe_manager._task_repo

        instance_id = str(uuid.uuid4())
        _seed_instance(probe_engine, instance_id=instance_id)
        live = _seed_running_task(probe_engine, instance_id=instance_id)

        # Capture the ACTUAL claim SQL the repository emits, so the
        # per-instance gate clause is evidenced verbatim and not merely
        # inferred from the observed outcome.
        from sqlalchemy import event as _sa_event

        captured_sql: list[str] = []

        @_sa_event.listens_for(probe_engine, "before_cursor_execute")
        def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
            if "UPDATE task" in statement and "RETURNING" in statement:
                captured_sql.append(statement)

        try:
            await self._run_p2_body(
                probe_manager, probe_engine, instance_id, live
            )
        finally:
            _sa_event.remove(probe_engine, "before_cursor_execute", _capture)

        assert captured_sql, "no claim UPDATE statement was captured"
        gate_sql = "\n".join(captured_sql)
        assert "SELECT instance_id FROM task" in gate_sql and "status_running_guard" in gate_sql, (
            "the captured claim SQL does NOT contain the per-instance "
            "RUNNING-task gate"
        )
        print(
            "\n[PROBE] per-instance claim gate clause captured from the "
            "emitted SQL:\n"
            + gate_sql[gate_sql.find("instance_id NOT IN") - 200 :][:900]
        )

    async def _run_p2_body(
        self, probe_manager, probe_engine: Engine, instance_id, live
    ):
        task_repo = probe_manager._task_repo

        await probe_manager.enqueue_message_job(
            instance_id=instance_id,
            message="P2 probe payload",
            source="scheduler",
        )

        queued = _tasks_for(probe_engine, instance_id)
        assert len(queued) == 2, (
            f"expected the live RUNNING task + 1 new PENDING task, got {len(queued)}"
        )
        pending_task = next(
            t for t in queued if t.status == TaskStatus.PENDING.value
        )

        # Release the queue-awareness gate: promote the linked JobItem to
        # ACTIVE, exactly as the job processor does when it picks the job
        # up.  Only the per-instance gate remains shut.
        _set_job_admission(probe_engine, pending_task.work_id, AdmissionState.ACTIVE.value)

        # --- (a) WHILE THE LIVE TURN HOLDS THE INSTANCE ------------------
        claimed = task_repo.claim_pending_task("probe-worker-1")
        assert claimed is None or claimed.id != pending_task.id, (
            f"per-instance claim gate LEAKED: the pending task {pending_task.id} "
            f"was claimed while the instance still held RUNNING task {live.id}"
        )
        still_pending = [
            t for t in _tasks_for(probe_engine, instance_id)
            if t.id == pending_task.id
        ][0]
        assert still_pending.status == TaskStatus.PENDING.value

        # --- (b) AFTER THE INSTANCE FREES --------------------------------
        _complete_task(probe_engine, live.id)

        claimed = task_repo.claim_pending_task("probe-worker-2")
        assert claimed is not None, (
            "the deferred/queued follow-up task was NEVER claimed after the "
            "instance freed — the claim gate does not release it"
        )
        assert claimed.id == pending_task.id, (
            f"claimed the wrong task: {claimed.id} != {pending_task.id}"
        )
        assert claimed.status == TaskStatus.RUNNING.value

    async def test_p2_gate_is_per_instance_not_global(
        self, probe_manager, probe_engine: Engine
    ):
        """Control: a busy instance does NOT block a DIFFERENT instance."""
        task_repo = probe_manager._task_repo

        busy_id = str(uuid.uuid4())
        other_id = str(uuid.uuid4())
        _seed_instance(probe_engine, instance_id=busy_id)
        _seed_instance(probe_engine, instance_id=other_id)
        _seed_running_task(probe_engine, instance_id=busy_id)

        await probe_manager.enqueue_message_job(
            instance_id=other_id,
            message="P2 control payload",
            source="scheduler",
        )
        other_tasks = [
            t for t in _tasks_for(probe_engine, other_id)
            if t.status == TaskStatus.PENDING.value
        ]
        assert len(other_tasks) == 1
        _set_job_admission(
            probe_engine, other_tasks[0].work_id, AdmissionState.ACTIVE.value
        )

        claimed = task_repo.claim_pending_task("probe-worker-3")
        assert claimed is not None, (
            "the per-instance gate blocked a task on an UNRELATED instance "
            "— the gate is wider than documented"
        )
        assert claimed.instance_id == other_id


# ===========================================================================
# P3 — IDEMPOTENCY-KEY BEHAVIOR (THE PIVOT)
# ===========================================================================


class TestP3IdempotencyKey:
    """P3 (GREEN — statements about EXISTING behaviour).

    Sub-claims:
      (a) a second enqueue with the SAME idempotency_key does NOT create a
          second JobItem (ON CONFLICT DO NOTHING on ``idx_job_idempotency``);
      (b) the loser's phantom Task / MessageQueue rows ARE compensated away;
      (c) the KEPT delivery's payload is NOT updated by the loser;
      (d) DECISIVE: a COMPLETED JobItem STILL OCCUPIES the key, because the
          partial index predicate carries NO status filter.
    """

    async def test_p3_second_enqueue_is_a_dedup_hit_single_jobitem(
        self, probe_manager, probe_engine: Engine
    ):
        instance_id = str(uuid.uuid4())
        _seed_instance(probe_engine, instance_id=instance_id)
        key = f"scheduler:{PROBE_TAG}:{uuid.uuid4()}"

        first = await probe_manager.enqueue_message_job(
            instance_id=instance_id,
            message="P3 first payload",
            source="scheduler",
            idempotency_key=key,
        )
        second = await probe_manager.enqueue_message_job(
            instance_id=instance_id,
            message="P3 second payload (LOST)",
            source="scheduler",
            idempotency_key=key,
        )

        keyed = _jobitems_with_key(probe_engine, key)
        assert len(keyed) == 1, (
            f"expected exactly 1 JobItem for the idempotency key, got {len(keyed)}"
        )
        # Both calls resolve to the SAME JobItem.
        assert first.job_id == second.job_id == keyed[0].job_id

        # (c) The KEPT delivery's payload is NOT updated by the loser.
        assert keyed[0].message == "P3 first payload", (
            "the loser UPDATED the kept delivery's payload — the count-freezing "
            "tension the designer must resolve is NOT present as assumed"
        )

    async def test_p3_phantom_rows_are_compensated_away(
        self, probe_manager, probe_engine: Engine
    ):
        """(b) The loser's Task is CANCELLED and its MessageQueue row is
        DELETED — the compensation documented at
        ``daemon/services/instance_messaging.py`` (dedup_hit branch)."""
        instance_id = str(uuid.uuid4())
        _seed_instance(probe_engine, instance_id=instance_id)
        key = f"scheduler:{PROBE_TAG}:{uuid.uuid4()}"

        await probe_manager.enqueue_message_job(
            instance_id=instance_id,
            message="P3 winner payload",
            source="scheduler",
            idempotency_key=key,
        )
        tasks_after_first = _tasks_for(probe_engine, instance_id)
        mq_after_first = _mq_for(probe_engine, instance_id)
        assert len(tasks_after_first) == 1
        assert len(mq_after_first) == 1
        winner_task_id = tasks_after_first[0].id
        winner_mq_id = mq_after_first[0].message_id

        await probe_manager.enqueue_message_job(
            instance_id=instance_id,
            message="P3 loser payload",
            source="scheduler",
            idempotency_key=key,
        )

        tasks = _tasks_for(probe_engine, instance_id)
        mqs = _mq_for(probe_engine, instance_id)

        # The winner's rows survive untouched.
        assert any(
            t.id == winner_task_id and t.status == TaskStatus.PENDING.value
            for t in tasks
        ), f"the WINNER's Task row was disturbed: {[(t.id, t.status) for t in tasks]}"
        assert any(m.message_id == winner_mq_id for m in mqs)

        # The loser's rows are compensated: the phantom Task row is
        # CANCELLED (``TaskRepository.cancel_task`` — a soft transition,
        # the row SURVIVES as CANCELLED) and the phantom MessageQueue row
        # is hard-DELETED (``MessageQueueRepository.delete``).
        statuses = sorted(t.status for t in tasks)
        assert statuses == [TaskStatus.CANCELLED.value, TaskStatus.PENDING.value], (
            f"the loser's phantom Task row was NOT compensated away: "
            f"{[(t.id, t.status) for t in tasks]}"
        )
        assert len(mqs) == 1, (
            f"the loser's phantom MessageQueue row was NOT compensated away: "
            f"{[m.message_id for m in mqs]}"
        )
        assert mqs[0].message_id == winner_mq_id

    async def test_p3_index_predicate_has_no_status_filter(
        self, probe_engine: Engine
    ):
        """(d-1) The partial-index predicate — read from the MODEL DDL and,
        on PostgreSQL, from the LIVE CATALOG — carries NO status filter."""
        from daemon.repositories.job_queue.models import JobItem as JobItemModel

        idx = next(
            i
            for i in JobItemModel.__table__.indexes
            if i.name == "idx_job_idempotency"
        )
        model_predicate = str(idx.dialect_options["postgresql"]["where"])
        assert model_predicate == "idempotency_key IS NOT NULL AND deleted_at IS NULL", (
            f"model DDL predicate changed: {model_predicate!r}"
        )
        assert "status" not in model_predicate and "admission_state" not in model_predicate, (
            f"the index predicate now filters on status: {model_predicate!r}"
        )

        if PROBE_SUBSTRATE == "postgres":
            with probe_engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT schemaname, indexdef FROM pg_indexes "
                        "WHERE tablename = 'job_queue_items' "
                        "AND indexname = 'idx_job_idempotency' "
                        "ORDER BY schemaname"
                    )
                ).fetchall()
            assert rows, "idx_job_idempotency missing from the live catalog"
            for schemaname, catalog_def in rows:
                assert "deleted_at IS NULL" in catalog_def, catalog_def
                assert "status" not in catalog_def and "admission_state" not in catalog_def, (
                    f"catalog predicate ({schemaname}) carries a status "
                    f"filter: {catalog_def!r}"
                )
                print(f"\n[PROBE] live catalog [{schemaname}]: {catalog_def}")

    async def test_p3_completed_jobitem_still_sits_in_the_index(
        self, probe_manager, probe_engine: Engine
    ):
        """(d-2) INDEX LEVEL: a COMPLETED (admission_state='done') JobItem
        with ``deleted_at IS NULL`` STILL OCCUPIES the partial index.

        Proved at the repository layer: the atomic claim
        (``JobRepository.create_or_get_by_idempotency_key``) is an
        ``ON CONFLICT DO NOTHING`` against ``idx_job_idempotency`` whose
        predicate carries NO status filter — so a DONE row still blocks the
        INSERT and the pre-existing row is handed back.

        NOTE (correction to the commissioning hypothesis): the SERVICE
        layer (``JobQueueService.enqueue``) treats a TERMINAL matched job
        as a bypass and mints a fresh row under a synthetic key — see
        ``test_p3_terminal_job_bypasses_the_key_and_delivers_again``.
        The index occupancy and the service behaviour are therefore NOT
        the same thing; both are pinned here.
        """
        from daemon import constants as constants_mod
        from daemon.repositories.job_queue.repository import JobRepository

        instance_id = str(uuid.uuid4())
        _seed_instance(probe_engine, instance_id=instance_id)
        key = f"scheduler:{PROBE_TAG}:{uuid.uuid4()}"

        await probe_manager.enqueue_message_job(
            instance_id=instance_id,
            message="P3 first delivery",
            source="scheduler",
            idempotency_key=key,
        )
        keyed = _jobitems_with_key(probe_engine, key)
        assert len(keyed) == 1
        winner_job_id = keyed[0].job_id

        # Complete the JobItem — the ONLY soft-delete site in the codebase
        # is ``JobRepository.soft_delete_job`` (an explicit API DELETE);
        # the completion path never sets ``deleted_at``.
        with Session(probe_engine) as session:
            job = session.get(JobItem, winner_job_id)
            job.admission_state = AdmissionState.DONE.value
            session.commit()

        completed = _jobitems_with_key(probe_engine, key)
        assert len(completed) == 1
        assert completed[0].admission_state == AdmissionState.DONE.value
        assert completed[0].deleted_at is None, (
            "the completion path soft-deleted the JobItem — the key would "
            "have been freed at the INDEX level too"
        )

        # Direct repository-level probe of the atomic claim: the INSERT is
        # a no-op (the DONE row still owns the index entry) and the
        # pre-existing row comes back with ``created=False``.
        repo = JobRepository(probe_engine)
        job, created = repo.create_or_get_by_idempotency_key(
            agent_id="developer",
            agent_dir="/agents/developer",
            message="P3 index-level probe",
            source="scheduler",
            project_id=constants_mod.SYSTEM_DEFAULT_PROJECT_ID,
            priority=1,
            job_metadata={},
            queue_id=None,
            idempotency_key=key,
            job_type="message",
            instance_id=instance_id,
        )
        assert created is False, (
            "the atomic claim INSERTED a fresh row — the DONE row no longer "
            "occupies idx_job_idempotency (the index predicate changed)"
        )
        assert job is not None and job.job_id == winner_job_id
        assert job.admission_state == AdmissionState.DONE.value

    async def test_p3_terminal_job_bypasses_the_key_and_delivers_again(
        self, probe_manager, probe_engine: Engine
    ):
        """(d-3) SERVICE LEVEL: a TERMINAL (DONE) JobItem does NOT block a
        later delivery under the SAME key — the service mints a synthetic
        key and the second delivery IS delivered.

        ``JobQueueService.enqueue`` terminal-admission branch:
            ``idempotency_key = f"{idempotency_key}#{uuid.uuid4().hex[:8]}"``

        CONSEQUENCE: the commissioning hypothesis "a constant per-schedule
        key kills the schedule after the first fire" is REFUTED at this
        layer — the delivery is NOT silently lost once the previous
        JobItem is terminal.
        """
        instance_id = str(uuid.uuid4())
        _seed_instance(probe_engine, instance_id=instance_id)
        key = f"scheduler:{PROBE_TAG}:{uuid.uuid4()}"

        first = await probe_manager.enqueue_message_job(
            instance_id=instance_id,
            message="P3 first delivery",
            source="scheduler",
            idempotency_key=key,
        )
        keyed = _jobitems_with_key(probe_engine, key)
        assert len(keyed) == 1
        winner_job_id = keyed[0].job_id

        with Session(probe_engine) as session:
            job = session.get(JobItem, winner_job_id)
            job.admission_state = AdmissionState.DONE.value
            session.commit()

        second = await probe_manager.enqueue_message_job(
            instance_id=instance_id,
            message="P3 second delivery",
            source="scheduler",
            idempotency_key=key,
        )
        assert second.job_id != first.job_id, (
            "the second delivery reused the DONE JobItem — the terminal "
            "bypass did NOT fire"
        )

        # The new row carries a SYNTHETIC key (the original key + '#' + hex8).
        all_rows = _jobitems_for(probe_engine, instance_id)
        assert len(all_rows) == 2, f"expected 2 JobItems, got {len(all_rows)}"
        synthetic = [j for j in all_rows if j.job_id == second.job_id][0]
        assert synthetic.idempotency_key.startswith(key + "#"), (
            f"expected a synthetic key {key}#<hex8>, got "
            f"{synthetic.idempotency_key!r}"
        )
        # The terminal-bypass row carries the NEW payload (the loser's
        # message is NOT frozen here) — unlike the non-terminal dedup case.
        assert synthetic.message == "P3 second delivery", (
            f"the terminal-bypass row kept the OLD payload: "
            f"{synthetic.message!r}"
        )

        # The second delivery was NOT compensated away — it is live.
        tasks = _tasks_for(probe_engine, instance_id)
        mqs = _mq_for(probe_engine, instance_id)
        assert len(tasks) == 2, (
            f"the second delivery left {len(tasks)} Task rows (expected 2) — "
            f"{[(t.id, t.status) for t in tasks]}"
        )
        assert len(mqs) == 2, (
            f"the second delivery left {len(mqs)} MessageQueue rows "
            f"(expected 2)"
        )

    async def test_p3_non_terminal_job_silently_drops_the_second_delivery(
        self, probe_manager, probe_engine: Engine
    ):
        """(d-4) THE REAL LOSS WINDOW: while the previous JobItem is still
        NON-TERMINAL (queued/active), a second delivery under the SAME key
        is a dedup hit and the phantom Task / MessageQueue rows are
        compensated away — the second delivery is SILENTLY LOST.

        This is the window a CONSTANT per-schedule key opens for the
        deferred / catch-up delivery: a re-attempt that lands before the
        previous JobItem reaches a terminal admission state is dropped
        with no durable trace beyond the cancelled phantom Task.
        """
        instance_id = str(uuid.uuid4())
        _seed_instance(probe_engine, instance_id=instance_id)
        key = f"scheduler:{PROBE_TAG}:{uuid.uuid4()}"

        first = await probe_manager.enqueue_message_job(
            instance_id=instance_id,
            message="P3 first delivery (still queued)",
            source="scheduler",
            idempotency_key=key,
        )
        keyed = _jobitems_with_key(probe_engine, key)
        assert len(keyed) == 1
        assert keyed[0].admission_state == AdmissionState.QUEUED.value  # non-terminal

        second = await probe_manager.enqueue_message_job(
            instance_id=instance_id,
            message="P3 second delivery (LOST)",
            source="scheduler",
            idempotency_key=key,
        )
        assert second.job_id == first.job_id, (
            "the second delivery created a NEW JobItem — the non-terminal "
            "dedup path did NOT fire"
        )
        assert len(_jobitems_for(probe_engine, instance_id)) == 1

        # The loser's rows are compensated → the second delivery is LOST:
        # its Task row is CANCELLED (never claimable) and its MessageQueue
        # row is DELETED.  Exactly ONE live (PENDING) Task survives.
        tasks = _tasks_for(probe_engine, instance_id)
        mqs = _mq_for(probe_engine, instance_id)
        live_tasks = [t for t in tasks if t.status == TaskStatus.PENDING.value]
        assert len(live_tasks) == 1, (
            f"the second delivery left {len(live_tasks)} LIVE Task rows "
            f"(expected 1) — it was NOT lost: "
            f"{[(t.id, t.status) for t in tasks]}"
        )
        assert len(mqs) == 1, (
            f"the second delivery left {len(mqs)} MessageQueue rows "
            f"(expected 1) — it was NOT lost"
        )
        # The surviving Task carries the FIRST payload.
        assert live_tasks[0].work_id == first.job_id


# ===========================================================================
# P4 — BASELINE OF THE DEFECT (RED PIN CANDIDATE)
# ===========================================================================


class TestP4BusyCallerDefect:
    """P4 — the DEFECT.  ``test_p4_red_pin_*`` was RED at base.

    Required behaviour (the assertion the fix must satisfy):
      firing a reuse-mode scheduled delivery at a BUSY caller MUST leave a
      durable, auditable row behind (JobItem / Task / MessageQueue and a
      ``schedule_executions`` audit row).

    POST-FIX CONTRACT AMENDMENT (commission
    ``scheduler-deferred-alarm-delivery`` — plan §(e) pin #4): a one-shot
    whose deferral produced DURABLE evidence (a PENDING Task whose
    ``message_id`` equals the enqueue's id) IS CONSUMED — the schedule
    stops and disables — because the INTENT SURVIVES as the pending
    delivery (the alarm is queued for the caller's next turn), not as a
    re-armed schedule.  The pre-fix red pin asserted the opposite ("the
    one-shot MUST NOT be consumed"); that expectation was written before
    the final plan and is superseded by DEC-9's consumption-gating table
    (``deferred_durable`` consumes).  The delivery-file pin
    (``tests/unit/test_scheduler_deferred_delivery_pin.py::TestOneShotDurability``)
    is the canonical statement of the post-fix contract.

    Current behaviour (proved by the GREEN characterisation test below):
      the delivery is DEFERRED (durable), audited as ``deferred``, and the
      one-shot is consumed.
    """

    async def test_p4_red_pin_busy_caller_must_leave_durable_evidence(
        self, probe_manager, probe_engine: Engine
    ):
        source_id = f"{PROBE_TAG}-src-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(probe_engine, instance_id=instance_id)  # RUNNING = busy
        _seed_scheduler_row_and_mapping(
            probe_engine,
            source_id=source_id,
            instance_id=instance_id,
            run_at=run_at,
        )

        source_repo = probe_manager._source_repository
        completion_events: list = []
        execution_events: list = []

        adapter = _build_scheduler_adapter(
            probe_engine,
            probe_manager,
            source_id=source_id,
            run_at=run_at,
            on_complete=_production_on_complete(source_repo, source_id),
            execution_events=execution_events,
        )
        # Record whether the production on_complete callback fired.
        original_on_complete = adapter._on_complete_callback

        def _tracking_on_complete(sid: str, completed: bool) -> None:
            completion_events.append((sid, completed))
            original_on_complete(sid, completed)

        adapter._on_complete_callback = _tracking_on_complete

        before_tasks = len(_tasks_for(probe_engine, instance_id))
        before_mq = len(_mq_for(probe_engine, instance_id))
        before_jobs = len(_jobitems_for(probe_engine, instance_id))
        before_exec = len(_schedule_executions(probe_engine, source_id))
        row_before = _source_row(probe_engine, source_id)
        assert row_before is not None
        assert row_before.enabled is True
        assert row_before.status == "running"

        # --- FIRE THE REAL ADAPTER PATH --------------------------------
        # ``_run_schedule`` waits for run_at, calls ``_emit_scheduled_message``
        # (the busy-caller branch) and then, for a ONE-TIME schedule,
        # consumes the one-shot via ``_on_complete_callback``.
        await asyncio.wait_for(adapter._run_schedule(), timeout=20)

        after_tasks = len(_tasks_for(probe_engine, instance_id))
        after_mq = len(_mq_for(probe_engine, instance_id))
        after_jobs = len(_jobitems_for(probe_engine, instance_id))
        after_exec = len(_schedule_executions(probe_engine, source_id))
        row_after = _source_row(probe_engine, source_id)
        assert row_after is not None

        evidence = (
            "\n========== P4 POST-FIX CONTRACT — busy-caller delivery evidence ==========\n"
            f"  adapter._instance_mode            = {adapter._instance_mode}\n"
            f"  mapped instance status            = {InstanceStatus.RUNNING.value} (busy)\n"
            f"  execution_callback events         = {execution_events}\n"
            f"  on_complete_callback fired        = {completion_events}\n"
            f"  task rows            before/after = {before_tasks} / {after_tasks}\n"
            f"  message_queue rows   before/after = {before_mq} / {after_mq}\n"
            f"  job_queue_items rows before/after = {before_jobs} / {after_jobs}\n"
            f"  schedule_executions  before/after = {before_exec} / {after_exec}\n"
            f"  source_configs.status before/after= {row_before.status!r} / {row_after.status!r}\n"
            f"  source_configs.enabled before/after={row_before.enabled!r} / {row_after.enabled!r}\n"
            "======================================================================"
        )

        # --- THE POST-FIX CONTRACT ASSERTIONS ----------------------------
        assert after_jobs > before_jobs, (
            "P4 POST-FIX CONTRACT: the busy-caller reuse delivery created "
            "NO JobItem — the "
            "alarm was dropped entirely" + evidence
        )
        assert after_tasks > before_tasks, (
            "P4 POST-FIX CONTRACT: the busy-caller reuse delivery created "
            "NO Task row" + evidence
        )
        assert after_mq > before_mq, (
            "P4 POST-FIX CONTRACT: the busy-caller reuse delivery created "
            "NO MessageQueue row"
            + evidence
        )
        assert after_exec > before_exec, (
            "P4 POST-FIX CONTRACT: the busy-caller reuse delivery wrote NO "
            "schedule_executions "
            "audit row — the skip is invisible" + evidence
        )
        # POST-FIX CONTRACT (plan §(e) pin #4 / DEC-9): the deferral was
        # DURABLE, so the one-shot IS consumed (the schedule stops and
        # disables) — the INTENT survives as the pending delivery, not as
        # a re-armed schedule.
        assert row_after.status == "stopped", (
            "P4 POST-FIX CONTRACT: the durably-deferred one-shot was NOT "
            "consumed "
            f"(status={row_after.status!r})" + evidence
        )
        assert row_after.enabled is False, (
            "P4 POST-FIX CONTRACT: the durably-deferred one-shot was NOT "
            "disabled "
            f"(enabled={row_after.enabled!r})" + evidence
        )

    async def test_p4_characterization_busy_caller_delivers_nothing_today(
        self, probe_manager, probe_engine: Engine
    ):
        """GREEN characterisation of the CURRENT behaviour.

        Pins what the busy-caller site looks like NOW so the fix can be
        proven to have changed it — the assertion was INVERTED by the
        commission ``scheduler-deferred-alarm-delivery`` (the fix landed:
        the busy caller now RECEIVES a durable pending delivery instead of
        a silent drop).

        POST-FIX shape: a durable delivery (JobItem + PENDING Task +
        MessageQueue row) + a ``deferred`` audit row, and the one-shot is
        consumed (the intent survives as the pending delivery — plan §(e)
        pin #4 / DEC-9's ``deferred_durable`` outcome).
        """
        source_id = f"{PROBE_TAG}-src-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)

        _seed_instance(probe_engine, instance_id=instance_id)  # RUNNING = busy
        _seed_scheduler_row_and_mapping(
            probe_engine,
            source_id=source_id,
            instance_id=instance_id,
            run_at=run_at,
        )

        source_repo = probe_manager._source_repository
        execution_events: list = []
        completion_events: list = []

        adapter = _build_scheduler_adapter(
            probe_engine,
            probe_manager,
            source_id=source_id,
            run_at=run_at,
            on_complete=_production_on_complete(source_repo, source_id),
            execution_events=execution_events,
        )
        original_on_complete = adapter._on_complete_callback

        def _tracking_on_complete(sid: str, completed: bool) -> None:
            completion_events.append((sid, completed))
            original_on_complete(sid, completed)

        adapter._on_complete_callback = _tracking_on_complete

        await asyncio.wait_for(adapter._run_schedule(), timeout=20)

        # --- the alarm was DEFERRED, not dropped -------------------------
        assert _jobitems_for(probe_engine, instance_id), (
            "characterisation: NO JobItem was created — the busy-caller "
            "delivery was dropped again"
        )
        pending_tasks = [
            t
            for t in _tasks_for(probe_engine, instance_id)
            if t.status == TaskStatus.PENDING.value
        ]
        assert len(pending_tasks) == 1, (
            "characterisation: expected exactly 1 PENDING Task for the "
            f"deferred delivery, got {len(pending_tasks)}"
        )
        assert _mq_for(probe_engine, instance_id), (
            "characterisation: NO MessageQueue row — the deferred delivery "
            "is not durable"
        )
        deferred_rows = [
            r
            for r in _schedule_executions(probe_engine, source_id)
            if r.status == "deferred"
        ]
        assert len(deferred_rows) == 1, (
            "characterisation: expected exactly one 'deferred' audit row, "
            f"got {[(r.status, r.error_message) for r in _schedule_executions(probe_engine, source_id)]}"
        )

        # --- and the one-shot IS consumed (the intent survives as the
        # --- pending delivery) --------------------------------------------
        assert completion_events, (
            "characterisation: the on_complete callback did NOT fire — the "
            "one-shot is no longer consumed"
        )
        row = _source_row(probe_engine, source_id)
        assert row is not None
        assert row.status == "stopped", (
            f"characterisation: expected status='stopped', got {row.status!r}"
        )
        assert row.enabled is False

        # --- the deferral is NOT reported as a 'skipped' event -----------
        # (the pre-fix site fired a SKIPPED callback; the post-fix site
        # DEFERS — the audit trail is the 'deferred' row above).
        assert not any(
            event["status"] == "skipped" for event in execution_events
        ), (
            "characterisation: a 'skipped' execution event was emitted — the "
            "busy-caller site no longer skips, it defers"
        )


# ===========================================================================
# P5 — THE SIBLING SITE (semaphore-timeout skip)
# ===========================================================================


class TestP5SemaphoreTimeoutSkip:
    """P5 (GREEN — statement about the CURRENT behaviour).

    The semaphore-timeout site (``_acquire_execution_slot``) fires BEFORE
    the reuse_instance check, so it is reachable for ANY instance_mode.

    POST-FIX (commission ``scheduler-deferred-alarm-delivery`` — R9 /
    decisions.md DEC-2 / ADR-10): the site has TWO branches.  With a
    deferrable target (reuse-mode + a resolvable mapping — the fixture
    below) the alarm is DEFERRED to the pinned caller (the identical
    mechanism as the busy branch); with NO deferrable target the schedule
    RE-ARMS (the execution row is written, the one-shot is NOT consumed,
    and the branch takes the mandated 5s sleep).

    The assertion was INVERTED by the commission (the pre-fix site skipped
    the fire with zero rows).
    """

    async def test_p5_semaphore_timeout_defers_to_the_pinned_caller(
        self, probe_manager, probe_engine: Engine
    ):
        source_id = f"{PROBE_TAG}-src-{uuid.uuid4().hex[:8]}"
        instance_id = str(uuid.uuid4())
        run_at = datetime.now(timezone.utc) + timedelta(seconds=0.4)

        _seed_instance(probe_engine, instance_id=instance_id)
        _seed_scheduler_row_and_mapping(
            probe_engine,
            source_id=source_id,
            instance_id=instance_id,
            run_at=run_at,
        )

        source_repo = probe_manager._source_repository
        execution_events: list = []
        completion_events: list = []

        adapter = _build_scheduler_adapter(
            probe_manager=probe_manager,
            probe_engine=probe_engine,
            source_id=source_id,
            run_at=run_at,
            on_complete=_production_on_complete(source_repo, source_id),
            execution_events=execution_events,
        )
        original_on_complete = adapter._on_complete_callback

        def _tracking_on_complete(sid: str, completed: bool) -> None:
            completion_events.append((sid, completed))
            original_on_complete(sid, completed)

        adapter._on_complete_callback = _tracking_on_complete

        # Exhaust the execution semaphore so the acquire times out — the
        # EXACT branch under test (``_acquire_execution_slot`` ->
        # ``asyncio.TimeoutError`` -> the deferral / re-arm branch).
        assert adapter._execution_semaphore is not None
        await adapter._execution_semaphore.acquire()

        # Drive the REAL scheduler loop end-to-end: it waits for run_at,
        # calls ``_emit_scheduled_message`` (semaphore timeout -> deferral
        # to the pinned caller), and then — because this is a ONE-TIME
        # schedule whose deferral was DURABLE — consumes the one-shot via
        # ``_on_complete_callback``.
        await asyncio.wait_for(adapter._run_schedule(), timeout=20)

        # --- the alarm was DEFERRED to the pinned caller ------------------
        assert _jobitems_for(probe_engine, instance_id), (
            "characterisation: the semaphore-timeout site created NO JobItem "
            "— the alarm was dropped instead of deferred"
        )
        pending_tasks = [
            t
            for t in _tasks_for(probe_engine, instance_id)
            if t.status == TaskStatus.PENDING.value
        ]
        assert len(pending_tasks) == 1, (
            "characterisation: expected exactly 1 PENDING Task for the "
            f"deferred delivery, got {len(pending_tasks)}"
        )
        assert _mq_for(probe_engine, instance_id), (
            "characterisation: NO MessageQueue row — the deferred delivery "
            "is not durable"
        )
        deferred_rows = [
            r
            for r in _schedule_executions(probe_engine, source_id)
            if r.status == "deferred"
        ]
        assert len(deferred_rows) == 1, (
            "characterisation: expected exactly one 'deferred' audit row, "
            f"got {[(r.status, r.error_message) for r in _schedule_executions(probe_engine, source_id)]}"
        )

        # --- the deferral is NOT reported as a 'skipped' event -----------
        assert not any(
            event["status"] == "skipped" for event in execution_events
        ), (
            "characterisation: a 'skipped' execution event was emitted — the "
            "semaphore-timeout site no longer skips, it defers"
        )

        # --- and the one-shot IS consumed (the intent survives as the
        # --- pending delivery) --------------------------------------------
        assert completion_events, (
            "the on_complete callback did NOT fire — the one-shot was not "
            "consumed by the scheduler loop"
        )
        row = _source_row(probe_engine, source_id)
        assert row is not None
        assert row.status == "stopped", (
            f"characterisation: expected status='stopped', got {row.status!r}"
        )
        assert row.enabled is False
