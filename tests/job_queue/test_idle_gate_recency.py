"""Recency-bounded idle gates — regression pins (fix/idle-gate-recency).

**What this pins.** The two idle gates — the BACKGROUND-queue gate
(``JobProcessor._background_idle_check``) and the MAINTENANCE-registry gate
(``MaintenanceService._is_idle``) — used to answer "is the nest busy?" with
*"does a non-terminal row exist?"*. That question is UNSATISFIABLE on a
nest whose normal shape is a set of always-on sessions: every settled
message mirror of a non-terminal instance witnesses FOREVER.

Measured on live ``ensemble_prod`` (2026-11-23T18:56Z): **67** background-gate
witnesses, **52 of them on TWO long-lived ari sessions**
(``77ddcb9f-…`` waiting_children since 2026-11-18 with 41 settled mirrors;
``30aa400e-…`` running since 2026-11-23 with 11). The background queue had
not drained since **2026-11-14T17:44:35Z**; the blueprint pending-lease
reaper (registered without an ``idle_policy`` ⇒ ``IDLE_GATED``, and with NO
``max_staleness_hours`` escape hatch) could never fire.

**The fix.** "Idle" now means "no RECENT meaningful activity": a witness only
counts when the WORK ITEM ITSELF was created within the activity window
(``ENSEMBLE_IDLE_GATE_ACTIVITY_WINDOW_MINUTES``, default 30 minutes, read per
evaluation; ``0`` disables the bound — the kill-switch).

**Pin map.**

===  ====================================================================
P1   A long-lived QUIET session (waiting_children for days, no recent
     activity, settled mirror rows) does NOT hold either gate closed.
P2   A RECENTLY-ACTIVE workload DOES hold both gates closed.
P3   A load spike (multiple recently-active jobs) defers both gates.
P4   Fail-CLOSED on ambiguity ⇒ BUSY (unparseable timestamp, absent
     timestamp, probe error, invalid knob value).
P5   The carve-outs survive (Fix-2B deadlock carve-out; the defer gate's
     WS1 requester-instance carve-out is untouched).
P6   BOTH gates change together (one witness set, two scopes).
===  ====================================================================

**No live DB contact** — file-backed SQLite per test.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel

from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue import _idle_predicate_sql
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import Task, TaskStatus, TaskType  # noqa: F401
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_processor import JobProcessor
from daemon.services.job_queue_service import JobQueueService
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.maintenance import MaintenanceService


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fb_engine(tmp_path):
    """File-backed SQLite engine (NEVER StaticPool in-memory)."""
    db_path = tmp_path / "idle_gate_recency.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def job_repo(fb_engine):
    return JobRepository(fb_engine)


@pytest.fixture
def task_repo(fb_engine):
    return TaskRepository(fb_engine)


# ─────────────────────────────────────────────────────────────────────────────
# Seed helpers
# ─────────────────────────────────────────────────────────────────────────────


def _iso(minutes_ago: float) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    ).isoformat()


def _insert_instance(
    engine: Engine,
    *,
    instance_id: str,
    project_id: str = "proj-recency",
    status: str = InstanceStatus.WAITING_CHILDREN.value,
    agent_id: str = "ari",
    last_activity_minutes_ago: float | None = 5.0,
) -> None:
    now = _iso(0)
    last_activity = (
        None
        if last_activity_minutes_ago is None
        else _iso(last_activity_minutes_ago)
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, last_activity_at, version)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status, :project_id,
                     :created_at, :updated_at, :last_activity_at, 1)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": agent_id,
                "agent_dir": f"agents/{agent_id}",
                "status": status,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
                "last_activity_at": last_activity,
            },
        )


def _insert_queue(
    engine: Engine,
    *,
    queue_id: str,
    project_id: str = "proj-recency",
    queue_type: str = "parallel",
) -> None:
    now = _iso(0)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_queues
                    (queue_id, project_id, queue_name, queue_name_lower,
                     queue_type, concurrency_limit, is_system, is_paused,
                     description, created_at, updated_at)
                VALUES
                    (:queue_id, :project_id, :queue_name, :queue_name_lower,
                     :queue_type, :concurrency_limit, 0, 0,
                     NULL, :created_at, :updated_at)
                """
            ),
            {
                "queue_id": queue_id,
                "project_id": project_id,
                "queue_name": queue_id,
                "queue_name_lower": queue_id.lower(),
                "queue_type": queue_type,
                "concurrency_limit": 1,
                "created_at": now,
                "updated_at": now,
            },
        )


def _insert_job_item(
    engine: Engine,
    *,
    job_id: str,
    instance_id: str | None,
    project_id: str = "proj-recency",
    queue_id: str = "queue-parallel",
    admission_state: str,
    job_type: str = "task",
    created_at: str | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source,
                     project_id, queue_id, priority, admission_state,
                     created_at, instance_id, job_type, retry_count,
                     metadata)
                VALUES
                    (:job_id, 'ari', 'agents/ari', 'm', 'api',
                     :project_id, :queue_id, 0, :admission_state,
                     :created_at, :instance_id, :job_type, 0, :metadata)
                """
            ),
            {
                "job_id": job_id,
                "project_id": project_id,
                "queue_id": queue_id,
                "admission_state": admission_state,
                "created_at": _iso(0) if created_at is None else created_at,
                "instance_id": instance_id,
                "job_type": job_type,
                "metadata": json.dumps({}),
            },
        )


def _insert_task(
    engine: Engine,
    *,
    work_id: str,
    instance_id: str,
    status: str = TaskStatus.RUNNING.value,
    created_at: str | None = None,
    is_deferred: bool = False,
    is_background: bool = False,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                     retry_count, created_at, cancel_requested,
                     retry_scheduled, work_id, is_deferred, is_background)
                VALUES
                    (:task_type, :instance_id, :message_id, :status,
                     0, :created_at, 0, 0, :work_id, :is_deferred,
                     :is_background)
                """
            ),
            {
                "task_type": TaskType.PROCESS_MESSAGE.value,
                "instance_id": instance_id,
                "message_id": str(uuid.uuid4()),
                "status": status,
                "created_at": _iso(0) if created_at is None else created_at,
                "work_id": work_id,
                "is_deferred": is_deferred,
                "is_background": is_background,
            },
        )


def _make_job_processor(engine: Engine) -> JobProcessor:
    """A JobProcessor wired to the REAL repository stack."""
    job_repository = JobRepository(engine)
    queue_repository = JobQueueRepository(engine)
    task_repository = TaskRepository(engine)
    lock_manager = JobLockManager(lock_repo=MagicMock())
    queue_service = JobQueueService(job_repository, lock_manager, queue_repository)
    instance_manager = MagicMock()
    instance_manager._task_repo = task_repository
    queue_service.set_instance_manager(instance_manager)
    return JobProcessor(
        queue_service=queue_service,
        instance_manager=instance_manager,
        project_repo=MagicMock(),
        queue_repo=queue_repository,
    )


# ─────────────────────────────────────────────────────────────────────────────
# P1 — a long-lived QUIET session does NOT hold the gate closed
# ─────────────────────────────────────────────────────────────────────────────


class TestP1LongLivedQuietSession:
    """P1: the ``77ddcb9f-…`` shape — waiting_children for days, settled
    mirror rows, no recent activity — must NOT hold either gate closed."""

    @staticmethod
    def _seed_ari_shape(engine: Engine) -> None:
        _insert_instance(
            engine,
            instance_id="inst-ari-quiet",
            status=InstanceStatus.WAITING_CHILDREN.value,
            last_activity_minutes_ago=2.0,
        )
        _insert_queue(engine, queue_id="queue-parallel")
        # 41 settled message mirrors spanning four days — the exact shape
        # that held 52 of the 67 measured witnesses.
        for idx in range(41):
            _insert_job_item(
                engine,
                job_id=f"job-mirror-{idx}",
                instance_id="inst-ari-quiet",
                admission_state="done",
                job_type="message",
                created_at=_iso(60 * 24 * (1 + idx * 0.1)),
            )

    def test_p1_legacy_predicate_still_sees_the_witness(self, fb_engine, job_repo):
        """The fixture IS the starvation shape: the UNBOUNDED predicate
        still reports BUSY (the pre-fix behaviour)."""
        self._seed_ari_shape(fb_engine)
        assert job_repo.has_active_non_background_work(None) is True
        assert job_repo.has_active_non_deferred_work(None) is True

    def test_p1_background_gate_opens(self, fb_engine, job_repo):
        self._seed_ari_shape(fb_engine)
        assert job_repo.has_recent_non_background_work() is False

    def test_p1_maintenance_gate_opens(self, fb_engine, job_repo):
        self._seed_ari_shape(fb_engine)
        assert job_repo.has_recent_active_work() is False

    def test_p1_kill_switch_restores_the_legacy_witness_set(self, fb_engine, job_repo):
        """``window_minutes=0`` disables the recency bound ⇒ the old
        (starving) semantics come back. This is the kill-switch."""
        self._seed_ari_shape(fb_engine)
        assert job_repo.has_recent_non_background_work(window_minutes=0) is True
        assert job_repo.has_recent_active_work(window_minutes=0) is True

    def test_p1_env_kill_switch_restores_the_legacy_witness_set(
        self, fb_engine, job_repo, monkeypatch
    ):
        monkeypatch.setenv(
            _idle_predicate_sql.ENV_IDLE_ACTIVITY_WINDOW_MINUTES, "0"
        )
        self._seed_ari_shape(fb_engine)
        assert job_repo.has_recent_non_background_work() is True
        assert job_repo.has_recent_active_work() is True

    async def test_p1_job_processor_background_gate_returns_zero(
        self, fb_engine
    ):
        """The service-level gate (Gate A) opens on the quiet-session
        shape."""
        self._seed_ari_shape(fb_engine)
        processor = _make_job_processor(fb_engine)
        assert await processor._background_idle_check() == 0

    async def test_p1_maintenance_service_reports_idle(self, fb_engine):
        """The maintenance registry gate opens on the quiet-session shape."""
        self._seed_ari_shape(fb_engine)
        job_repository = JobRepository(fb_engine)
        queue_repository = JobQueueRepository(fb_engine)
        queue_service = JobQueueService(
            job_repository, JobLockManager(lock_repo=MagicMock()), queue_repository
        )
        service = MaintenanceService()
        service.set_job_queue_service(queue_service)
        service.set_request_registry({})
        assert await service._is_idle() is True


# ─────────────────────────────────────────────────────────────────────────────
# P2 — a recently-active workload DOES hold the gate closed
# ─────────────────────────────────────────────────────────────────────────────


class TestP2RecentWorkloadHolds:
    """P2: recent activity still defers both gates."""

    def test_p2_recent_active_job_row_holds_both_gates(self, fb_engine, job_repo):
        _insert_instance(
            engine=fb_engine,
            instance_id="inst-live",
            status=InstanceStatus.RUNNING.value,
            last_activity_minutes_ago=0.1,
        )
        _insert_queue(fb_engine, queue_id="queue-parallel")
        _insert_job_item(
            fb_engine,
            job_id="job-live",
            instance_id="inst-live",
            admission_state="active",
            created_at=_iso(1.0),
        )
        assert job_repo.has_recent_non_background_work() is True
        assert job_repo.has_recent_active_work() is True

    def test_p2_recent_settled_mirror_holds_both_gates(self, fb_engine, job_repo):
        """A settled mirror of a LIVE mission is recent ⇒ it holds the
        gate (the post-Fix-B window stays covered)."""
        _insert_instance(
            engine=fb_engine,
            instance_id="inst-live-mirror",
            status=InstanceStatus.WAITING_CHILDREN.value,
            last_activity_minutes_ago=0.1,
        )
        _insert_queue(fb_engine, queue_id="queue-parallel")
        _insert_job_item(
            fb_engine,
            job_id="job-live-mirror",
            instance_id="inst-live-mirror",
            admission_state="done",
            job_type="message",
            created_at=_iso(2.0),
        )
        assert job_repo.has_recent_non_background_work() is True
        assert job_repo.has_recent_active_work() is True

    def test_p2_recent_running_task_holds_both_gates(self, fb_engine, job_repo):
        _insert_instance(
            engine=fb_engine,
            instance_id="inst-task-live",
            status=InstanceStatus.RUNNING.value,
            last_activity_minutes_ago=0.1,
        )
        _insert_task(
            fb_engine,
            work_id="work-live",
            instance_id="inst-task-live",
            status=TaskStatus.RUNNING.value,
            created_at=_iso(1.0),
        )
        assert job_repo.has_recent_non_background_work() is True
        assert job_repo.has_recent_active_work() is True

    def test_p2_recent_queued_job_row_holds_the_maintenance_gate(
        self, fb_engine, job_repo
    ):
        """A queued job row (no instance) is pending work ⇒ the sweeps
        defer behind it."""
        _insert_queue(fb_engine, queue_id="queue-parallel")
        _insert_job_item(
            fb_engine,
            job_id="job-queued",
            instance_id=None,
            admission_state="queued",
            created_at=_iso(1.0),
        )
        assert job_repo.has_recent_active_work() is True

    async def test_p2_job_processor_background_gate_returns_one(self, fb_engine):
        _insert_instance(
            engine=fb_engine,
            instance_id="inst-live",
            status=InstanceStatus.RUNNING.value,
            last_activity_minutes_ago=0.1,
        )
        _insert_queue(fb_engine, queue_id="queue-parallel")
        _insert_job_item(
            fb_engine,
            job_id="job-live",
            instance_id="inst-live",
            admission_state="active",
            created_at=_iso(1.0),
        )
        processor = _make_job_processor(fb_engine)
        assert await processor._background_idle_check() == 1

    async def test_p2_maintenance_service_reports_busy(self, fb_engine):
        _insert_instance(
            engine=fb_engine,
            instance_id="inst-live",
            status=InstanceStatus.RUNNING.value,
            last_activity_minutes_ago=0.1,
        )
        _insert_queue(fb_engine, queue_id="queue-parallel")
        _insert_job_item(
            fb_engine,
            job_id="job-live",
            instance_id="inst-live",
            admission_state="active",
            created_at=_iso(1.0),
        )
        job_repository = JobRepository(fb_engine)
        queue_repository = JobQueueRepository(fb_engine)
        queue_service = JobQueueService(
            job_repository, JobLockManager(lock_repo=MagicMock()), queue_repository
        )
        service = MaintenanceService()
        service.set_job_queue_service(queue_service)
        service.set_request_registry({})
        assert await service._is_idle() is False


# ─────────────────────────────────────────────────────────────────────────────
# P3 — a load spike defers
# ─────────────────────────────────────────────────────────────────────────────


class TestP3LoadSpikeDefers:
    """P3: multiple recently-active work items ⇒ both gates defer."""

    def test_p3_spike_of_recent_jobs_holds_both_gates(self, fb_engine, job_repo):
        _insert_instance(
            engine=fb_engine,
            instance_id="inst-spike",
            status=InstanceStatus.RUNNING.value,
            last_activity_minutes_ago=0.1,
        )
        _insert_queue(fb_engine, queue_id="queue-parallel")
        for idx in range(6):
            _insert_job_item(
                fb_engine,
                job_id=f"job-spike-{idx}",
                instance_id="inst-spike",
                admission_state="active",
                created_at=_iso(1.0),
            )
        assert job_repo.has_recent_non_background_work() is True
        assert job_repo.has_recent_active_work() is True

    def test_p3_spike_of_recent_tasks_holds_both_gates(self, fb_engine, job_repo):
        _insert_instance(
            engine=fb_engine,
            instance_id="inst-spike-t",
            status=InstanceStatus.RUNNING.value,
            last_activity_minutes_ago=0.1,
        )
        for idx in range(6):
            _insert_task(
                fb_engine,
                work_id=f"work-spike-{idx}",
                instance_id="inst-spike-t",
                status=TaskStatus.RUNNING.value,
                created_at=_iso(1.0),
            )
        assert job_repo.has_recent_non_background_work() is True
        assert job_repo.has_recent_active_work() is True


# ─────────────────────────────────────────────────────────────────────────────
# P4 — fail-CLOSED on ambiguity
# ─────────────────────────────────────────────────────────────────────────────


class TestP4FailClosed:
    """P4: an undeterminable idle state must be treated as BUSY."""

    def test_p4_unparseable_timestamp_holds_the_gate(self, fb_engine, job_repo):
        _insert_instance(
            engine=fb_engine,
            instance_id="inst-garbage",
            status=InstanceStatus.RUNNING.value,
        )
        _insert_queue(fb_engine, queue_id="queue-parallel")
        _insert_job_item(
            fb_engine,
            job_id="job-garbage",
            instance_id="inst-garbage",
            admission_state="active",
            created_at="not-a-timestamp",
        )
        assert job_repo.has_recent_non_background_work() is True
        assert job_repo.has_recent_active_work() is True

    def test_p4_absent_timestamp_holds_the_gate(self, fb_engine, job_repo):
        _insert_instance(
            engine=fb_engine,
            instance_id="inst-null-ts",
            status=InstanceStatus.RUNNING.value,
        )
        _insert_queue(fb_engine, queue_id="queue-parallel")
        _insert_job_item(
            fb_engine,
            job_id="job-null-ts",
            instance_id="inst-null-ts",
            admission_state="active",
            created_at="",
        )
        assert job_repo.has_recent_non_background_work() is True
        assert job_repo.has_recent_active_work() is True

    def test_p4_db_error_holds_the_gate(self, fb_engine, job_repo):
        """A probe error is BUSY (the safe direction)."""
        from sqlalchemy.exc import OperationalError as SAOperationalError

        class _BoomCM:
            def __enter__(self):
                raise SAOperationalError("boom", {}, Exception("injected"))

            def __exit__(self, exc_type, exc, tb):
                return False

        original_begin = job_repo.engine.begin
        job_repo.engine.begin = lambda: _BoomCM()  # noqa: ARG005
        try:
            assert job_repo.has_recent_non_background_work() is True
            assert job_repo.has_recent_active_work() is True
        finally:
            job_repo.engine.begin = original_begin

    async def test_p4_maintenance_probe_error_is_busy(self, fb_engine):
        """The maintenance gate fails CLOSED on a probe error."""
        job_repository = JobRepository(fb_engine)
        queue_repository = JobQueueRepository(fb_engine)
        queue_service = JobQueueService(
            job_repository, JobLockManager(lock_repo=MagicMock()), queue_repository
        )
        service = MaintenanceService()
        service.set_job_queue_service(queue_service)
        service.set_request_registry({})

        def _boom(*args, **kwargs):
            raise RuntimeError("injected probe failure")

        job_repository.has_recent_active_work = _boom  # type: ignore[method-assign]
        assert await service._is_idle() is False

    def test_p4_invalid_knob_value_keeps_the_bound_on(self, monkeypatch):
        """An unparseable / negative knob value resolves to the DEFAULT
        window — it must never silently disable the recency bound."""
        for raw in ("abc", "-5", "", "  "):
            monkeypatch.setenv(
                _idle_predicate_sql.ENV_IDLE_ACTIVITY_WINDOW_MINUTES, raw
            )
            assert (
                _idle_predicate_sql.resolve_idle_activity_window_minutes()
                == _idle_predicate_sql.DEFAULT_IDLE_ACTIVITY_WINDOW_MINUTES
            )


# ─────────────────────────────────────────────────────────────────────────────
# P5 — the carve-outs survive
# ─────────────────────────────────────────────────────────────────────────────


class TestP5CarveOutsSurvive:
    """P5: the Fix-2B deadlock carve-out and the WS1 requester-instance
    carve-out keep their exact semantics."""

    def test_p5_fix2b_queued_job_with_pending_task_does_not_hold_the_gate(
        self, fb_engine, job_repo
    ):
        """A ``queued`` job row whose linked Task is still ``pending`` is
        UNCLAIMABLE and must not hold the background gate (the Fix-2B
        deadlock carve-out) — even when the row is RECENT."""
        _insert_instance(
            engine=fb_engine,
            instance_id="inst-fix2b",
            status=InstanceStatus.RUNNING.value,
        )
        _insert_queue(fb_engine, queue_id="queue-parallel")
        _insert_job_item(
            fb_engine,
            job_id="job-fix2b",
            instance_id="inst-fix2b",
            admission_state="queued",
            created_at=_iso(1.0),
        )
        _insert_task(
            fb_engine,
            work_id="job-fix2b",
            instance_id="inst-fix2b",
            status=TaskStatus.PENDING.value,
            created_at=_iso(1.0),
        )
        assert job_repo.has_recent_non_background_work() is False

    def test_p5_fix2b_carve_out_survives_in_the_shared_body(self):
        """The carve-out text is byte-preserved in the recency-bounded
        background body."""
        body = _idle_predicate_sql.IDLE_JOB_WITNESS_BODY_BACKGROUND
        assert "(j.admission_state != 'queued'" in body
        assert "OR t.status IS NULL" in body
        assert "OR t.status != 'pending')" in body

    def test_p5_defer_gate_bodies_are_untouched(self):
        """The DEFER gate is a third consumer of the shared bodies and is
        deliberately NOT re-scoped — its witness surface
        (``GET /api/queues/defer-blocked``) derives from these constants,
        so they must not carry a recency qualifier."""
        for body in (
            _idle_predicate_sql.JOB_DEFER_BUSY_BODY_PROJECT,
            _idle_predicate_sql.JOB_DEFER_BUSY_BODY_SYSTEM,
            _idle_predicate_sql.JOB_DEFER_BUSY_BODY_PROJECT_WITH_CARVEOUT,
            _idle_predicate_sql.JOB_DEFER_BUSY_BODY_SYSTEM_WITH_CARVEOUT,
            _idle_predicate_sql.JOB_BACKGROUND_BUSY_BODY,
        ):
            assert "created_at" not in body
            assert "witness_limit" not in body

    def test_p5_ws1_requester_carve_out_still_works(self, fb_engine, job_repo):
        """The WS1 requester-instance carve-out: a candidate's OWN settled
        mirrors do not witness against it (defer self-witness)."""
        _insert_instance(
            engine=fb_engine,
            instance_id="inst-ws1",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _insert_queue(fb_engine, queue_id="queue-parallel")
        _insert_job_item(
            fb_engine,
            job_id="job-ws1",
            instance_id="inst-ws1",
            admission_state="done",
            job_type="message",
        )
        assert job_repo.has_active_non_deferred_work(
            None, requester_instance_id="inst-ws1"
        ) is False
        assert job_repo.has_active_non_deferred_work(
            None, requester_instance_id="other-inst"
        ) is True


# ─────────────────────────────────────────────────────────────────────────────
# P6 — both gates change together (one witness set, two scopes)
# ─────────────────────────────────────────────────────────────────────────────


class TestP6BothGatesTogether:
    """P6: the background gate and the maintenance gate are driven by the
    SAME witness set (one scope difference: the background lane's own rows
    are invisible to the background gate but visible to the sweeps)."""

    def test_p6_quiet_session_opens_both(self, fb_engine, job_repo):
        TestP1LongLivedQuietSession._seed_ari_shape(fb_engine)
        assert job_repo.has_recent_non_background_work() is False
        assert job_repo.has_recent_active_work() is False

    def test_p6_recent_work_holds_both(self, fb_engine, job_repo):
        _insert_instance(
            engine=fb_engine,
            instance_id="inst-both",
            status=InstanceStatus.RUNNING.value,
            last_activity_minutes_ago=0.1,
        )
        _insert_queue(fb_engine, queue_id="queue-parallel")
        _insert_job_item(
            fb_engine,
            job_id="job-both",
            instance_id="inst-both",
            admission_state="active",
            created_at=_iso(1.0),
        )
        assert job_repo.has_recent_non_background_work() is True
        assert job_repo.has_recent_active_work() is True

    def test_p6_background_lane_rows_are_invisible_to_the_background_gate(
        self, fb_engine, job_repo
    ):
        """A recent row ON a background queue does not block the
        background gate (the lane cannot block itself) but DOES count for
        the maintenance sweeps."""
        _insert_queue(fb_engine, queue_id="queue-bg", queue_type="background")
        _insert_job_item(
            fb_engine,
            job_id="job-bg",
            instance_id=None,
            queue_id="queue-bg",
            admission_state="queued",
            created_at=_iso(1.0),
        )
        assert job_repo.has_recent_non_background_work() is False
        assert job_repo.has_recent_active_work() is True


# ─────────────────────────────────────────────────────────────────────────────
# The window knob
# ─────────────────────────────────────────────────────────────────────────────


class TestActivityWindowKnob:
    """The env switch: read per evaluation, default 30, ``0`` = legacy."""

    def test_default_is_thirty_minutes(self, monkeypatch):
        monkeypatch.delenv(
            _idle_predicate_sql.ENV_IDLE_ACTIVITY_WINDOW_MINUTES, raising=False
        )
        assert (
            _idle_predicate_sql.resolve_idle_activity_window_minutes()
            == 30
        )
        assert (
            _idle_predicate_sql.DEFAULT_IDLE_ACTIVITY_WINDOW_MINUTES == 30
        )

    def test_read_per_evaluation(self, monkeypatch):
        monkeypatch.setenv(
            _idle_predicate_sql.ENV_IDLE_ACTIVITY_WINDOW_MINUTES, "45"
        )
        assert _idle_predicate_sql.resolve_idle_activity_window_minutes() == 45
        monkeypatch.setenv(
            _idle_predicate_sql.ENV_IDLE_ACTIVITY_WINDOW_MINUTES, "5"
        )
        assert _idle_predicate_sql.resolve_idle_activity_window_minutes() == 5

    def test_zero_means_legacy(self, monkeypatch):
        monkeypatch.setenv(
            _idle_predicate_sql.ENV_IDLE_ACTIVITY_WINDOW_MINUTES, "0"
        )
        assert _idle_predicate_sql.resolve_idle_activity_window_minutes() == 0

    def test_window_actually_bounds_the_witness_set(self, fb_engine, job_repo):
        """A 40-minute-old witness counts at W=60 and not at W=30."""
        _insert_instance(
            engine=fb_engine,
            instance_id="inst-aged",
            status=InstanceStatus.RUNNING.value,
        )
        _insert_queue(fb_engine, queue_id="queue-parallel")
        _insert_job_item(
            fb_engine,
            job_id="job-aged",
            instance_id="inst-aged",
            admission_state="active",
            created_at=_iso(40.0),
        )
        assert job_repo.has_recent_non_background_work(window_minutes=60) is True
        assert job_repo.has_recent_non_background_work(window_minutes=30) is False


# ─────────────────────────────────────────────────────────────────────────────
# Drift pin — the recency-bounded TASK witness set vs the task repository
# ─────────────────────────────────────────────────────────────────────────────


class TestTaskWitnessDriftPin:
    """The recency-bounded probe re-implements the task-side busy shape in
    SQL (the task repository is owned by another lane). This pin proves
    the two agree — an unbounded (``window_minutes=0``) comparison over a
    fixture matrix.

    The comparison is made against the TASK witness STATEMENT directly
    (not the combined repository probe) so the job side cannot
    contaminate the verdict.
    """

    @staticmethod
    def _task_witnesses(engine: Engine, scope: str) -> bool:
        """True iff the recency-bounded TASK witness set is non-empty."""
        if scope == "background":
            statement = _idle_predicate_sql.idle_task_witness_statement_background()
            binds = _idle_predicate_sql.idle_task_witness_binds_background()
        else:
            statement = _idle_predicate_sql.idle_task_witness_statement_all_queues()
            binds = _idle_predicate_sql.idle_task_witness_binds_all_queues()
        with engine.begin() as conn:
            rows = conn.execute(statement, binds).fetchall()
        return bool(rows)

    @pytest.mark.parametrize(
        "status,is_deferred,is_background,linked_job_state",
        [
            (TaskStatus.RUNNING.value, False, False, None),
            (TaskStatus.PAUSED.value, False, False, None),
            (TaskStatus.PENDING.value, False, False, None),
            (TaskStatus.PENDING.value, False, False, "queued"),
            (TaskStatus.RUNNING.value, False, False, "done"),
            (TaskStatus.RUNNING.value, True, False, None),
            (TaskStatus.RUNNING.value, False, True, None),
            (TaskStatus.PENDING.value, True, False, None),
        ],
    )
    def test_task_witness_set_matches_the_task_repository(
        self,
        fb_engine,
        job_repo,
        task_repo,
        status,
        is_deferred,
        is_background,
        linked_job_state,
    ):
        instance_id = f"inst-drift-{status}-{is_deferred}-{is_background}"
        _insert_instance(
            engine=fb_engine,
            instance_id=instance_id,
            status=InstanceStatus.RUNNING.value,
        )
        work_id = f"work-drift-{uuid.uuid4()}"
        _insert_task(
            fb_engine,
            work_id=work_id,
            instance_id=instance_id,
            status=status,
            is_deferred=is_deferred,
            is_background=is_background,
        )
        if linked_job_state is not None:
            _insert_queue(fb_engine, queue_id="queue-parallel")
            _insert_job_item(
                fb_engine,
                job_id=work_id,
                instance_id=instance_id,
                admission_state=linked_job_state,
            )

        # BACKGROUND scope: is_background = false (the background gate).
        assert self._task_witnesses(fb_engine, "background") is (
            task_repo.has_active_non_background_work(None)
        )
        # MAINTENANCE scope: is_deferred = false (the sweeps).
        assert self._task_witnesses(fb_engine, "maintenance") is (
            task_repo.has_active_non_deferred_work(None)
        )
