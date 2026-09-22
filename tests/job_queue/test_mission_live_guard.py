"""Mission-live guard tests (2026-09-22 premature ``completed ✓`` fix).

Bug class: a task-kind LEADER job is legitimately
``JobItem ACTIVE + backing Task COMPLETED`` mid-mission (child_reports
defers the JobItem finalize behind still-running children). Two
finalize sites force-fired a terminal on that shape:

* drift Pattern f2 (``job_recovery_service.reconcile_drift_states``)
  → false ``completed ✓`` + F10 notify arm CAS-deleted the mission
  watcher row (live repro: events 79328/79349, lag 183s/232s);
* boot sweep ``job_queue_service.reconcile_terminal_watches`` fired
  terminal for mission-keyed watches on settled work rows with no
  liveness consult.

The fix inserts a shared mission-live guard
(``daemon/services/mission_live_guard.py``) at BOTH sites:

* LIVE legs: (a) bus pending watchers, (b) any non-terminal
  descendant, (c) non-terminal root — non-terminal = NOT IN
  ``TERMINAL_INSTANCE_STATUSES`` (the mission resolver canonicalizes
  IDLE → ``processing``, so idle counts LIVE);
* zombie backstop: ``completed_at`` older than
  ``MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS`` (6h) falls through to
  finalize (at-least-once delivery, starvation impossible);
* fail-open: any guard error → finalize proceeds.

HARD CONSTRAINT under test: at-least-once terminal delivery — the
mission-dead shapes MUST still finalize + notify (the original
missing-report bug must not regress).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.instance.models import Instance  # noqa: F401
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.watcher_models import JobWatcher  # noqa: F401
from daemon.repositories.job_queue.watcher_repository import (
    JobWatcherRepository,
)
from daemon.repositories.task.models import Task as TaskModel  # noqa: F401
from daemon.repositories.task.models import TaskStatus, TaskType  # noqa: F401
from daemon.repositories.task.repository import TaskRepository
from daemon.services import mission_live_guard as _mlg
from daemon.services.job_queue_service import JobQueueService
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.stale_task_recovery import StaleTaskRecovery
from daemon.services.timestamps import now_utc_naive


# ─────────────────────────────────────────────────────────────────────────────
# Engine + seed helpers (file-local engine so ``job_watchers`` exists;
# mirrors tests/job_queue/test_jober_watch_integration.py).
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def task_repository(engine) -> TaskRepository:
    return TaskRepository(engine)


@pytest.fixture
def instance_repo(engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine=engine)


def _insert_instance(
    engine,
    instance_id: str,
    *,
    status: str = "running",
    project_id: str = "test-project",
    parent_id: str | None = None,
    created_at: datetime | None = None,
) -> None:
    now = (created_at or datetime.now(timezone.utc)).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, version, parent_id)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status,
                     :project_id, :created_at, :updated_at, 1, :parent_id)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": "developer",
                "agent_dir": "agents/developer",
                "status": status,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
                "parent_id": parent_id,
            },
        )


def _insert_job_item(
    engine,
    *,
    job_id: str,
    instance_id: str,
    project_id: str = "test-project",
    queue_id: str = "queue-mlg-1",
    admission_state: str = AdmissionState.ACTIVE.value,
    job_type: str = "task",
) -> None:
    now = datetime.now(timezone.utc).isoformat()
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
                    (:job_id, :agent_id, :agent_dir, :message, :source,
                     :project_id, :queue_id, :priority, :admission_state,
                     :created_at, :instance_id, :job_type, 0, :metadata)
                """
            ),
            {
                "job_id": job_id,
                "agent_id": "developer",
                "agent_dir": "agents/developer",
                "message": "mission",
                "source": "api",
                "project_id": project_id,
                "queue_id": queue_id,
                "priority": 0,
                "admission_state": admission_state,
                "created_at": now,
                "instance_id": instance_id,
                "job_type": job_type,
                "metadata": json.dumps({}),
            },
        )


def _insert_completed_task(
    engine,
    *,
    work_id: str,
    instance_id: str,
    completed_at: datetime | None = None,
) -> int:
    completed_iso = (
        completed_at or now_utc_naive()
    ).isoformat()
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, status, retry_count,
                     created_at, cancel_requested, retry_scheduled,
                     work_id, is_deferred, is_background, completed_at)
                VALUES
                    ('process_message', :instance_id, 'completed', 0,
                     :created_at, 0, 0, :work_id, 0, 0, :completed_at)
                """
            ),
            {
                "instance_id": instance_id,
                "created_at": completed_iso,
                "work_id": work_id,
                "completed_at": completed_iso,
            },
        )
        return result.lastrowid


def _add_watch(
    watcher_repo: JobWatcherRepository,
    job_id: str,
    instance_id: str = "watcher-inst",
    events: list[str] | None = None,
) -> None:
    watcher_repo.add_watch(job_id, instance_id, watch_events=events)


def _make_recovery_service(
    repository, task_repository, lock_repo, instance_repo,
    job_queue_service_mock,
) -> JobRecoveryService:
    return JobRecoveryService(
        job_repository=repository,
        lock_repository=lock_repo,
        instance_repository=instance_repo,
        job_queue_service=job_queue_service_mock,
        task_repository=task_repository,
        stale_task_recovery=None,
    )


class _EmptyBusStub:
    async def pending_watchers(self, source_task_id):
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Part A — guard unit semantics (``evaluate_mission_live``)
# ─────────────────────────────────────────────────────────────────────────────


class TestMissionLiveGuardUnit:
    def _repo_with(self, engine, rows: dict[str, str]):
        for iid, status in rows.items():
            parent = None
            _insert_instance(engine, iid, status=status, parent_id=parent)
        return SQLModelInstanceRepository(engine=engine)

    @pytest.mark.asyncio
    async def test_leg_c_root_running_is_live(self, engine):
        repo = self._repo_with(engine, {"root-1": "running"})
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-1",
            task_completed_at=now_utc_naive(),
        )
        assert verdict.live and not verdict.error
        assert "c (root instance)" in verdict.reason

    @pytest.mark.asyncio
    async def test_leg_b_descendant_running_is_live(self, engine):
        _insert_instance(engine, "root-2", status="completed")
        _insert_instance(engine, "child-2", status="running", parent_id="root-2")
        repo = SQLModelInstanceRepository(engine=engine)
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-2",
            task_completed_at=now_utc_naive(),
        )
        assert verdict.live and not verdict.error
        assert "b (descendant" in verdict.reason

    @pytest.mark.asyncio
    async def test_idle_root_is_live_resolver_parity(self, engine):
        """The mission resolver canonicalizes IDLE → ``processing``
        (non-terminal). The guard must match: idle = LIVE."""
        _insert_instance(engine, "root-idle", status="idle")
        repo = SQLModelInstanceRepository(engine=engine)
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-idle",
            task_completed_at=now_utc_naive(),
        )
        assert verdict.live, (
            f"idle must be LIVE per the resolver's IDLE→processing "
            f"mapping; got {verdict}"
        )

    @pytest.mark.asyncio
    async def test_all_terminal_bus_quiet_is_not_live(self, engine):
        _insert_instance(engine, "root-3", status="completed")
        _insert_instance(
            engine, "child-3", status="completed", parent_id="root-3"
        )
        repo = SQLModelInstanceRepository(engine=engine)
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-3",
            task_completed_at=now_utc_naive(),
            bus_pending_count=0,
        )
        assert not verdict.live
        assert not verdict.timed_out

    @pytest.mark.asyncio
    async def test_leg_a_bus_pending_is_live(self, engine):
        _insert_instance(engine, "root-4", status="completed")
        repo = SQLModelInstanceRepository(engine=engine)
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-4",
            task_completed_at=now_utc_naive(),
            bus_pending_count=2,
        )
        assert verdict.live
        assert "leg a" in verdict.reason

    @pytest.mark.asyncio
    async def test_timeout_overrides_live_legs(self, engine):
        """(v) guard still seeing live + anchor older than the window
        → finalize door opens (starvation impossible)."""
        _insert_instance(engine, "root-5", status="running")
        repo = SQLModelInstanceRepository(engine=engine)
        old_anchor = now_utc_naive() - timedelta(
            seconds=_mlg.MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 60
        )
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-5",
            task_completed_at=old_anchor,
        )
        assert not verdict.live
        assert verdict.timed_out and not verdict.error

    @pytest.mark.asyncio
    async def test_missing_anchor_does_not_open_the_door(self, engine):
        """No ``completed_at`` anchor = data gap, not zombie evidence —
        the backstop must NOT fire; the live legs still hold."""
        _insert_instance(engine, "root-6", status="waiting_children")
        repo = SQLModelInstanceRepository(engine=engine)
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-6",
            task_completed_at=None,
        )
        assert verdict.live and not verdict.timed_out

    @pytest.mark.asyncio
    async def test_repo_error_fails_open(self, engine):
        repo = MagicMock(spec=SQLModelInstanceRepository)
        repo.get_tree_ids_permanent = MagicMock(
            side_effect=RuntimeError("db exploded")
        )
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-7",
            task_completed_at=now_utc_naive(),
        )
        assert not verdict.live
        assert verdict.error, "guard errors must fail OPEN (finalize)"

    @pytest.mark.asyncio
    async def test_unwired_repo_fails_open(self):
        verdict = await _mlg.evaluate_mission_live(
            instance_repository=None,
            instance_id="root-8",
            task_completed_at=now_utc_naive(),
        )
        assert not verdict.live
        assert verdict.error


# ─────────────────────────────────────────────────────────────────────────────
# Part B — drift Pattern f2 site
# ─────────────────────────────────────────────────────────────────────────────


class TestPatternF2MissionLiveGuard:
    @pytest.mark.asyncio
    async def test_mid_mission_leader_not_finalized(
        self, engine, repository, task_repository, lock_repo,
        instance_repo,
    ):
        """(i) Leader turn ends with children running → f2 sweep must
        NOT emit ``completed ✓``, must leave the JobItem ACTIVE, and
        must NOT claim/delete the mission watcher row. The turn-end
        ``in progress ⟳`` lane is untouched (notify_watchers never
        called by the sweep here)."""
        from unittest.mock import patch

        watcher_repo = JobWatcherRepository(engine)
        job_queue_mock = MagicMock()
        job_queue_mock.notify_watchers = AsyncMock(return_value=0)

        _insert_instance(engine, "leader-1", status="running")
        _insert_instance(
            engine, "worker-1", status="running", parent_id="leader-1"
        )
        _insert_job_item(engine, job_id="job-mlg-1", instance_id="leader-1")
        task_id = _insert_completed_task(
            engine,
            work_id="job-mlg-1",
            instance_id="leader-1",
            completed_at=now_utc_naive() - timedelta(seconds=300),
        )
        _add_watch(
            watcher_repo,
            "job-mlg-1",
            events=["mission_terminal", "completed"],
        )

        service = _make_recovery_service(
            repository, task_repository, lock_repo, instance_repo,
            job_queue_mock,
        )
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        # No finalize.
        assert not [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
        ]
        # The observable skip seam.
        skips = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_skipped_mission_live"
            and d.get("job_id") == "job-mlg-1"
        ]
        assert skips, f"mission-live skip must be observable: {stats['details']}"
        assert "b (descendant" in skips[0]["reason"] or (
            "c (root instance)" in skips[0]["reason"]
        )
        # JobItem stays ACTIVE — correct-by-design mid-mission.
        job_after = repository.get("job-mlg-1")
        assert job_after.admission_state == AdmissionState.ACTIVE.value
        # Watcher row NOT claimed; no terminal/in_progress emission.
        assert watcher_repo.get_watchers_for_job("job-mlg-1"), (
            "mission watcher row must survive the guard-held sweep"
        )
        job_queue_mock.notify_watchers.assert_not_called()

    @pytest.mark.asyncio
    async def test_true_terminal_finalizes_and_notifies_once(
        self, engine, repository, task_repository, lock_repo,
        instance_repo,
    ):
        """(ii) Root completes, bus empty, descendants terminal → the
        terminal event is delivered exactly once via the normal
        fan-out seam; no double-fire."""
        from unittest.mock import patch

        watcher_repo = JobWatcherRepository(engine)
        job_queue_mock = MagicMock()
        job_queue_mock.notify_watchers = AsyncMock(return_value=0)

        _insert_instance(engine, "leader-2", status="completed")
        _insert_job_item(engine, job_id="job-mlg-2", instance_id="leader-2")
        _insert_completed_task(
            engine,
            work_id="job-mlg-2",
            instance_id="leader-2",
            completed_at=now_utc_naive() - timedelta(seconds=300),
        )
        _add_watch(watcher_repo, "job-mlg-2")

        service = _make_recovery_service(
            repository, task_repository, lock_repo, instance_repo,
            job_queue_mock,
        )
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        done = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-mlg-2"
        ]
        assert done, f"mission-dead shape must finalize: {stats['details']}"
        assert repository.get("job-mlg-2").admission_state == (
            AdmissionState.DONE.value
        )
        # Exactly ONE terminal emission through the canonical seam.
        assert job_queue_mock.notify_watchers.await_count == 1
        args = job_queue_mock.notify_watchers.await_args
        assert args.args[0] == "job-mlg-2"
        assert args.args[1] == "completed"

    @pytest.mark.asyncio
    async def test_crash_after_mission_end_still_delivers_backstop(
        self, engine, repository, task_repository, lock_repo,
        instance_repo,
    ):
        """(iii) Mission dead + JobItem stuck ACTIVE (crash after the
        mission ended) → f2 finalizes + notifies. The ORIGINAL
        missing-report bug's fix must NOT regress.

        Revive variant: the instance was revived mid-mission (root
        RUNNING again) → the guard skips; once the true terminal
        lands, the terminal is still emitted."""
        from unittest.mock import patch

        watcher_repo = JobWatcherRepository(engine)
        job_queue_mock = MagicMock()
        job_queue_mock.notify_watchers = AsyncMock(return_value=0)

        _insert_job_item(engine, job_id="job-mlg-3", instance_id="leader-3")
        _insert_completed_task(
            engine,
            work_id="job-mlg-3",
            instance_id="leader-3",
            completed_at=now_utc_naive() - timedelta(seconds=300),
        )
        _add_watch(watcher_repo, "job-mlg-3")

        service = _make_recovery_service(
            repository, task_repository, lock_repo, instance_repo,
            job_queue_mock,
        )

        # Phase 1 — root REVIVED mid-mission (running): guard skips.
        _insert_instance(engine, "leader-3", status="running")
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )
        assert repository.get("job-mlg-3").admission_state == (
            AdmissionState.ACTIVE.value
        )
        job_queue_mock.notify_watchers.assert_not_called()

        # Phase 2 — the true terminal lands (root completes): the
        # terminal is delivered (at-least-once preserved).
        instance_repo.transition_status_if(
            "leader-3",
            new_status="completed",
            allowed_from=("running",),
        )
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )
        assert repository.get("job-mlg-3").admission_state == (
            AdmissionState.DONE.value
        )
        assert job_queue_mock.notify_watchers.await_count == 1
        assert [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
        ], "crash-after-mission-end must finalize (backstop preserved)"

    @pytest.mark.asyncio
    async def test_orphan_timeout_fires_while_guard_sees_live(
        self, engine, repository, task_repository, lock_repo,
        instance_repo,
    ):
        """(v) Guard still seeing live (root running) but the backing
        task's ``completed_at`` is older than the zombie window → the
        finalize fires (starvation impossible)."""
        from unittest.mock import patch

        job_queue_mock = MagicMock()
        job_queue_mock.notify_watchers = AsyncMock(return_value=0)

        _insert_instance(engine, "leader-5", status="running")
        _insert_job_item(engine, job_id="job-mlg-5", instance_id="leader-5")
        _insert_completed_task(
            engine,
            work_id="job-mlg-5",
            instance_id="leader-5",
            completed_at=now_utc_naive()
            - timedelta(
                seconds=_mlg.MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 300
            ),
        )

        service = _make_recovery_service(
            repository, task_repository, lock_repo, instance_repo,
            job_queue_mock,
        )
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        done = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-mlg-5"
        ]
        assert done, (
            f"zombie backstop must open the finalize door past the "
            f"timeout: {stats['details']}"
        )
        assert repository.get("job-mlg-5").admission_state == (
            AdmissionState.DONE.value
        )
        job_queue_mock.notify_watchers.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_guard_error_fails_open_to_finalize(
        self, engine, repository, task_repository, lock_repo,
    ):
        """(vi) Resolver/DB error inside the guard → fail-open: the
        finalize fires (an extra premature event is acceptable; a
        missing terminal is NOT)."""
        from unittest.mock import patch

        broken_repo = MagicMock(spec=SQLModelInstanceRepository)
        broken_repo.get_tree_ids_permanent = MagicMock(
            side_effect=RuntimeError("resolver exploded")
        )
        job_queue_mock = MagicMock()
        job_queue_mock.notify_watchers = AsyncMock(return_value=0)

        _insert_instance(engine, "leader-6", status="running")
        _insert_job_item(engine, job_id="job-mlg-6", instance_id="leader-6")
        _insert_completed_task(
            engine,
            work_id="job-mlg-6",
            instance_id="leader-6",
            completed_at=now_utc_naive() - timedelta(seconds=300),
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=broken_repo,
            job_queue_service=job_queue_mock,
            task_repository=task_repository,
            stale_task_recovery=None,
        )
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=_EmptyBusStub(),
        ):
            stats = await service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=0,
            )

        done = [
            d for d in stats["details"]
            if d.get("pattern") == "orphan_active_completed_task_done"
            and d.get("job_id") == "job-mlg-6"
        ]
        assert done, (
            f"guard errors must FAIL OPEN to finalize: {stats['details']}"
        )
        job_queue_mock.notify_watchers.assert_awaited_once()


# ─────────────────────────────────────────────────────────────────────────────
# Part C — boot-sweep parity (``reconcile_terminal_watches``)
# ─────────────────────────────────────────────────────────────────────────────


def _terminal_job_record(
    *,
    job_id: str,
    instance_id: str,
    completed_at: datetime,
) -> SimpleNamespace:
    """WorkRecord-shaped resolver return: a settled task-kind JOB row
    whose mission instance is still to be consulted by the guard."""
    return SimpleNamespace(
        work_id=job_id,
        kind="job",
        status="completed",
        instance_id=instance_id,
        project_id="test-project",
        agent_id="developer",
        result_summary=None,
        error=None,
        created_at=completed_at - timedelta(minutes=5),
        started_at=None,
        completed_at=completed_at.isoformat(),
        job_type="task",
        mission_liveness=None,
        mission_terminal_reason=None,
        message_id=None,
        metadata={},
    )


class TestBootSweepMissionLiveGuard:
    def _service(
        self,
        engine,
        repository,
        watcher_repo,
        resolver,
    ) -> tuple[JobQueueService, MagicMock]:
        instance_manager = MagicMock()
        instance_manager._instance_repository = (
            SQLModelInstanceRepository(engine=engine)
        )
        instance_manager.enqueue_message = AsyncMock(
            return_value=MagicMock(message_id="msg-1")
        )
        lock_repo = LockRepository(engine)
        service = JobQueueService(
            repository=repository,
            lock_manager=MagicMock(),
            queue_repo=MagicMock(),
            instance_manager=instance_manager,
        )
        service.set_watcher_repo(watcher_repo)
        service._work_resolver = resolver
        return service, instance_manager

    @pytest.mark.asyncio
    async def test_boot_sweep_holds_live_mission(
        self, engine, repository
    ):
        """(iv)a Mission live + settled receipt + active
        mission_terminal watch → NO false terminal at boot, watcher
        row not claimed."""
        watcher_repo = JobWatcherRepository(engine)
        _insert_instance(engine, "leader-boot-1", status="waiting_children")
        _insert_job_item(
            engine,
            job_id="job-boot-1",
            instance_id="leader-boot-1",
            admission_state=AdmissionState.ACTIVE.value,
        )
        _add_watch(
            watcher_repo,
            "job-boot-1",
            events=["mission_terminal", "completed"],
        )
        resolver = MagicMock()
        resolver.resolve_work = MagicMock(
            return_value=_terminal_job_record(
                job_id="job-boot-1",
                instance_id="leader-boot-1",
                completed_at=now_utc_naive() - timedelta(seconds=120),
            )
        )
        service, instance_manager = self._service(
            engine, repository, watcher_repo, resolver
        )
        with patch.object(
            service, "notify_watchers", new=AsyncMock(return_value=0)
        ) as notify_mock:
            count = await service.reconcile_terminal_watches()

        assert count == 0
        notify_mock.assert_not_awaited()
        assert watcher_repo.get_watchers_for_job("job-boot-1"), (
            "boot sweep must NOT claim the watcher row of a live mission"
        )
        instance_manager.enqueue_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_boot_sweep_fires_for_dead_mission(
        self, engine, repository
    ):
        """(iv)b Mission dead → the boot sweep DOES fire the terminal
        (at-least-once across restarts)."""
        watcher_repo = JobWatcherRepository(engine)
        _insert_instance(engine, "leader-boot-2", status="completed")
        _insert_job_item(
            engine,
            job_id="job-boot-2",
            instance_id="leader-boot-2",
            admission_state=AdmissionState.ACTIVE.value,
        )
        _add_watch(
            watcher_repo,
            "job-boot-2",
            events=["mission_terminal", "completed"],
        )
        resolver = MagicMock()
        resolver.resolve_work = MagicMock(
            return_value=_terminal_job_record(
                job_id="job-boot-2",
                instance_id="leader-boot-2",
                completed_at=now_utc_naive() - timedelta(seconds=120),
            )
        )
        service, _ = self._service(
            engine, repository, watcher_repo, resolver
        )
        with patch.object(
            service, "notify_watchers", new=AsyncMock(return_value=0)
        ) as notify_mock:
            count = await service.reconcile_terminal_watches()

        assert count == 1
        notify_mock.assert_awaited_once()
        args = notify_mock.await_args
        assert args.args[0] == "job-boot-2"
        assert args.args[1] == "completed"

    @pytest.mark.asyncio
    async def test_boot_sweep_receipt_kind_semantics_unchanged(
        self, engine, repository
    ):
        """(iv)c Receipt-kind watches (no ``mission_terminal`` event)
        are UNCHANGED: the terminal fires even when the mission
        instance is still live."""
        watcher_repo = JobWatcherRepository(engine)
        _insert_instance(engine, "leader-boot-3", status="running")
        _insert_job_item(
            engine,
            job_id="job-boot-3",
            instance_id="leader-boot-3",
            admission_state=AdmissionState.ACTIVE.value,
        )
        # Default (receipt) events only — NO mission_terminal.
        _add_watch(watcher_repo, "job-boot-3")
        resolver = MagicMock()
        resolver.resolve_work = MagicMock(
            return_value=_terminal_job_record(
                job_id="job-boot-3",
                instance_id="leader-boot-3",
                completed_at=now_utc_naive() - timedelta(seconds=120),
            )
        )
        service, _ = self._service(
            engine, repository, watcher_repo, resolver
        )
        with patch.object(
            service, "notify_watchers", new=AsyncMock(return_value=0)
        ) as notify_mock:
            count = await service.reconcile_terminal_watches()

        assert count == 1
        notify_mock.assert_awaited_once()
