"""E2E replay: promote stop-frozen heartbeat amnesty (r-20260929-170301-0cb2).

Incident shape, reproduced end-to-end against a real engine:

1. A RUNNING task beats freshly; the daemon is stopped BY A PROMOTE —
   the beat freezes mid-turn.
2. The replacement daemon boots (boot epoch bump). Boot-time
   JobRecovery DELIBERATELY leaves the job PROCESSING ("instance
   alive (running)") — unchanged semantics.
3. Through a simulated soak window (two refresh cycles past the old
   ~120s queue_freshness breach point), /readyz queue_freshness must
   stay GREEN — the frozen pre-boot beat is invisible to freshness
   accounting. ACCEPTANCE: "promote commits with a busy daemon".
4. Negative regression: the SAME stale beat on a CONTINUOUSLY-UP
   daemon (no boot after the beat) still degrades queue_freshness
   exactly as before the amnesty.

The wall-clock compression: the incident's 120s breach is recreated
by backdating the frozen beat relative to the probe's DB-side
``now()`` — the arithmetic the probe performs is identical to the
real timeline, only the calendar is shifted. Cycle separation uses
real elapsed time between two refresh cycles so the age genuinely
advances past the breach point.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel import Session as SQLModelSession

import daemon.repositories.task.models  # noqa: F401 — register Task in metadata
import daemon.repositories.instance.models  # noqa: F401 — register Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.task.models import TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.boot_epoch import set_boot_epoch
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.readiness import (
    evaluate_queue_freshness,
    make_db_probe,
    make_queue_probe,
    refresh_readiness_composite,
)

_READINESS_THRESHOLD_S = 120  # production default (incident config)

_TEST_INSTANCE = "promote-amnesty-e2e-instance"
_TEST_JOB = "promote-amnesty-e2e-job"
_TEST_TASK_WORK_ID = "promote-amnesty-e2e-work"


@pytest.fixture
def engine():
    """In-memory SQLite with the full SQLModel schema (StaticPool so

    asyncio.to_thread workers share the connection)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _isolated_boot_epoch():
    set_boot_epoch(None)
    yield
    set_boot_epoch(None)


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _seed_busy_daemon_state(engine, *, beat_age_s: float) -> None:
    """The busy-daemon world at promote-stop time.

    One RUNNING instance driving one active JobItem with one RUNNING
    Task whose heartbeat froze ``beat_age_s`` ago.
    """
    now = datetime.now(timezone.utc)
    frozen_beat = now - timedelta(seconds=beat_age_s)

    # Instance: alive and RUNNING (the "instance alive (running)" branch).
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO instances (instance_id, project_id, agent_id, agent_dir,"
                " status, metadata, version, created_at, updated_at)"
                " VALUES (:iid, 'proj-e2e', 'developer', '/tmp/agents/developer',"
                " :status, '{}', 1, :created, :updated)"
            ),
            {
                "iid": _TEST_INSTANCE,
                "status": InstanceStatus.RUNNING.value,
                "created": now.isoformat(),
                "updated": now.isoformat(),
            },
        )
        conn.execute(
            text(
                "INSERT INTO task (task_type, instance_id, message_id, status,"
                " retry_count, created_at, cancel_requested, retry_scheduled,"
                " work_id, is_deferred, is_background, worker_id, started_at,"
                " last_heartbeat_at)"
                " VALUES (:task_type, :iid, NULL, :status, 0, :created, 0, 0,"
                " :work_id, 0, 0, 'worker-e2e', :started, :beat)"
            ),
            {
                "task_type": TaskType.PROCESS_MESSAGE.value,
                "iid": _TEST_INSTANCE,
                "status": TaskStatus.RUNNING.value,
                "created": frozen_beat,
                "work_id": _TEST_TASK_WORK_ID,
                "started": frozen_beat,
                "beat": frozen_beat,
            },
        )

    # JobItem: admission-active, type task — JobRecovery's alive branch.
    with SQLModelSession(engine) as db:
        db.add(
            JobItem(
                job_id=_TEST_JOB,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="promote-amnesty-e2e",
                source="api",
                project_id="proj-e2e",
                priority=5,
                job_metadata={},
                queue_id="system_parallel_queue",
                job_type="task",
                instance_id=_TEST_INSTANCE,
                admission_state="active",
                status="processing",
            )
        )
        db.commit()


async def _one_refresh_cycle(
    engine, *, boot_epoch, threshold_s: int
) -> ReadinessComposite:
    """One /readyz refresh cycle with the real probes."""
    return await refresh_readiness_composite(
        db_probe=make_db_probe(engine),
        queue_probe=make_queue_probe(
            engine,
            boot_epoch=boot_epoch,
            freshness_threshold_seconds=threshold_s,
        ),
        services_ok=True,
        queue_freshness_threshold_seconds=threshold_s,
    )


class TestPromoteStopFrozenAmnestyE2E:
    async def test_promote_commits_with_busy_daemon(self, engine):
        """THE ACCEPTANCE: a RUNNING task frozen by the promote's stop

        must NOT degrade readyz through the soak window — busy-daemon
        promotes are legal."""
        # ── t0: busy daemon, fresh beat (30s old), then the promote
        # stops the daemon mid-turn. The beat is now frozen at
        # t0 (no worker exists to advance it).
        _seed_busy_daemon_state(engine, beat_age_s=135)

        # ── the replacement daemon boots ~10s after the freeze
        # (incident: freeze 17:05:08, boot 17:05:51) and captures its
        # boot epoch in DB time via the same SQL the probe uses.
        boot = _now_naive() - timedelta(seconds=125)
        set_boot_epoch(boot)

        # ── boot-time JobRecovery: the deliberate PROCESSING semantics
        # are UNCHANGED — the alive instance keeps its active job.
        recovery = JobRecoveryService(
            job_repository=JobRepository(engine),
            lock_repository=LockRepository(engine),
            instance_repository=SQLModelInstanceRepository(engine),
        )
        stats = await recovery.recover_on_startup()
        assert stats == {"recovered": 0, "alive": 1, "total": 1}
        job_repo = JobRepository(engine)
        jobs = job_repo.find_processing_jobs()
        assert [j.job_id for j in jobs] == [_TEST_JOB]

        # ── Soak cycle 1 (past the old 120s breach: the frozen beat is
        # ~135s old and aging). Legacy semantics WOULD breach here.
        legacy_result = make_queue_probe(engine)()
        assert legacy_result.max_age_seconds is not None
        assert legacy_result.max_age_seconds > _READINESS_THRESHOLD_S
        legacy_fresh, _ = evaluate_queue_freshness(
            legacy_result.max_age_seconds,
            threshold_seconds=_READINESS_THRESHOLD_S,
        )
        assert legacy_fresh is False, (
            "control witness failed: the frozen beat must be stale "
            "evidence under legacy (no-epoch) semantics"
        )

        composite = await _one_refresh_cycle(
            engine, boot_epoch=boot, threshold_s=_READINESS_THRESHOLD_S
        )
        assert composite.ready is True, (
            f"readyz degraded mid-soak: {composite.reasons}"
        )
        assert composite.queue_freshness is True
        assert composite.queue_max_age_seconds is None  # beat invisible
        assert composite.inflight_turns == 0  # frozen ≠ alive-and-working

        # ── Soak cycle 2: real time advanced; the beat is older still
        # (≥2 probe cycles past the breach point). Still green.
        await asyncio.sleep(1.0)
        composite_2 = await _one_refresh_cycle(
            engine, boot_epoch=boot, threshold_s=_READINESS_THRESHOLD_S
        )
        assert composite_2.ready is True, (
            f"readyz degraded mid-soak (cycle 2): {composite_2.reasons}"
        )
        assert composite_2.queue_freshness is True
        assert composite_2.queue_max_age_seconds is None

        # Advisory surface: present, advisory-only, in detail.
        payload = composite_2.to_payload()
        assert payload["detail"]["inflight_turns"] == 0
        assert payload["components"]["queue_freshness"] is True

        # ── the frozen task is NOT reaped instantly: the 10-minute
        # StaleTaskRecovery backstop clocks stop-frozen tasks from the
        # boot epoch (boot+10min, never instantly post-boot).
        task_repo = TaskRepository(engine)
        young_boot = _now_naive() - timedelta(minutes=1)
        assert task_repo.find_stale_running_tasks(
            threshold_minutes=10, boot_epoch=young_boot
        ) == []
        assert task_repo.find_cancellable_tasks(
            threshold_minutes=10, boot_epoch=young_boot
        ) == []
        # ...and never "never": once max(beat, boot) outlives the
        # threshold, the backstop reaps it. Simulate the timeline
        # advanced ~11 minutes (beat still frozen — frozen 12min ago,
        # daemon booted 11min ago): effective clock = boot+10min has
        # passed → reaped.
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE task SET last_heartbeat_at = :hb"),
                {"hb": _now_naive() - timedelta(minutes=12)},
            )
        old_boot = _now_naive() - timedelta(minutes=11)
        stale = task_repo.find_cancellable_tasks(
            threshold_minutes=10, boot_epoch=old_boot
        )
        assert [t.work_id for t in stale] == [_TEST_TASK_WORK_ID]
        # ...and the same advanced timeline under the CURRENT (2min-old)
        # epoch still waits: max(beat, boot=2min-ago) = boot < 10min.
        stale_now = task_repo.find_cancellable_tasks(
            threshold_minutes=10, boot_epoch=_now_naive() - timedelta(minutes=2)
        )
        assert stale_now == []

    async def test_stale_while_continuously_up_still_degrades(self, engine):
        """NEGATIVE REGRESSION (the invariant the amnesty may not

        break): the same stale beat on a daemon that has been up since
        before the beat degrades queue_freshness exactly as today."""
        _seed_busy_daemon_state(engine, beat_age_s=135)

        # Daemon booted an hour ago — the beat is POST-boot; a live
        # worker should have kept it fresh and did not.
        boot = _now_naive() - timedelta(hours=1)
        set_boot_epoch(boot)

        composite = await _one_refresh_cycle(
            engine, boot_epoch=boot, threshold_s=_READINESS_THRESHOLD_S
        )
        assert composite.ready is False
        assert composite.queue_freshness is False
        assert composite.queue_max_age_seconds is not None
        assert composite.queue_max_age_seconds > _READINESS_THRESHOLD_S
        assert any("queue_freshness" in r for r in composite.reasons)

        # Same data, epoch never captured → identical degradation.
        set_boot_epoch(None)
        composite_legacy = await _one_refresh_cycle(
            engine, boot_epoch=None, threshold_s=_READINESS_THRESHOLD_S
        )
        assert composite_legacy.ready is False
        assert composite_legacy.queue_freshness is False
