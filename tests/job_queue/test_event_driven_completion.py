"""Per-site hook tests for Shape (b) — Event-Driven Completion.

Engine phase (``watch-notification-reliability/architecture-recommendation.md``,
"Engine Phase — Shape (b) Re-Cut" §2/§7): the structurally-silent terminal
writes are hooked at their service/processor callers with the canonical
``JobQueueService.notify_watchers`` facade. These tests drive the REAL
caller code against real repositories (the conftest in-memory
SQLite engine, StaticPool) and a real
notify chain (real ``notify_work_watchers`` over real
``WorkResolverService`` + ``JobWatcherRepository``), asserting:

* the notify FIRES with the canonical token,
* the watcher row is CAS-claimed exactly once,
* the ``[JOB_EVENT]`` is enqueued with the canonical source string,
* hook + already-delivered dual-fire is deduped to a single delivery,
* the site-4 re-SELECT race guard rejects stale captures,
* re-entrant recovery runs notify once, never twice.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_queue_service import JobQueueService
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.task_processor import ProcessMessageProcessor
from daemon.services.work_resolver import WorkResolverService

WATCHER_INSTANCE = "watcher-inst-0000-0000-00000000000f"


# ── Fixtures / harness ────────────────────────────────────────────────────


# NOTE: no local ``engine`` fixture — this directory's conftest provides
# the session-scoped in-memory SQLite engine (StaticPool for the
# asyncio.to_thread workers) with the autouse ``_truncate_tables``
# isolation. Defining a competing file-backed engine here would put the
# conftest truncate + this module's seeds on two different databases.


@pytest.fixture
def watcher_repo(engine) -> JobWatcherRepository:
    return JobWatcherRepository(engine)


@pytest.fixture
def job_repo(engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def task_repo(engine) -> TaskRepository:
    return TaskRepository(engine)


@pytest.fixture
def instance_repo(engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine)


@pytest.fixture(autouse=True)
def _seed_watcher_instance(engine):
    """The watching instance row (job_watchers.instance_id FK)."""
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(Instance(
            instance_id=WATCHER_INSTANCE,
            agent_id="jober",
            agent_dir="agents/jober",
            project_id="test-project",
            status=InstanceStatus.RUNNING.value,
            created_at=now,
            updated_at=now,
        ))
        s.commit()


@pytest.fixture
def enqueue_mock():
    manager = MagicMock(name="instance_manager")
    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-test")
    )
    return manager


@pytest.fixture
def service(job_repo, watcher_repo, task_repo, instance_repo, enqueue_mock):
    """Minimal REAL JobQueueService: the canonical notify chain
    (``notify_watchers`` → ``notify_work_watchers`` → CAS claim →
    ``[JOB_EVENT]`` enqueue) over real repositories. Built via
    ``__new__`` + the exact attributes ``notify_watchers`` reads, so the
    tests exercise the production notify path without standing up the
    full service graph."""
    resolver = WorkResolverService(task_repo, job_repo, instance_repo)
    svc = JobQueueService.__new__(JobQueueService)
    svc._repository = job_repo
    svc._watcher_repo = watcher_repo
    svc._instance_manager = enqueue_mock
    svc._work_resolver = resolver
    return svc


def _seed_instance(engine, instance_id, status=InstanceStatus.RUNNING.value):
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(Instance(
            instance_id=instance_id,
            agent_id="developer",
            agent_dir="agents/developer",
            project_id="test-project",
            status=status,
            created_at=now,
            updated_at=now,
        ))
        s.commit()
    return instance_id


def _seed_task(engine, work_id, instance_id, status=TaskStatus.RUNNING.value):
    with Session(engine) as s:
        s.add(Task(
            work_id=work_id,
            task_type="process_message",
            instance_id=instance_id,
            status=status,
            created_at=datetime.now(timezone.utc),
            is_deferred=False,
        ))
        s.commit()
    return work_id


def _seed_mirror_job(engine, job_id, instance_id, admission_state="active"):
    with Session(engine) as s:
        s.add(JobItem(
            job_id=job_id,
            agent_id="developer",
            agent_dir="agents/developer",
            message="mirror receipt",
            source="agent:test",
            instance_id=instance_id,
            admission_state=admission_state,
            job_type="message",
        ))
        s.commit()
    return job_id


def _seed_task_job(
    engine,
    job_id,
    instance_id,
    admission_state="queued",
    project_id="test-project",
    created_days_ago: float = 0.0,
):
    from datetime import timedelta

    created_at = datetime.now(timezone.utc) - timedelta(days=created_days_ago)
    with Session(engine) as s:
        s.add(JobItem(
            job_id=job_id,
            agent_id="developer",
            agent_dir="agents/developer",
            message="task work",
            source="agent:test",
            instance_id=instance_id,
            project_id=project_id,
            admission_state=admission_state,
            job_type="task",
            created_at=created_at,
        ))
        s.commit()
    return job_id


def _seed_watcher(watcher_repo, job_id, events=None):
    watcher_repo.add_watch(job_id, WATCHER_INSTANCE, events)
    return job_id


def _task_pk(engine: Engine, work_id: str) -> int:
    with Session(engine) as s:
        row = s.exec(select(Task).where(Task.work_id == work_id)).first()
        return row.id


def _build_task_processor(task_repo, resolver, watcher_repo, manager):
    """Minimal ``ProcessMessageProcessor`` carrying exactly the
    attributes the ``on_success`` closure reads."""
    tp = ProcessMessageProcessor.__new__(ProcessMessageProcessor)
    tp._task_repo = task_repo
    tp._work_resolver = resolver
    tp._watcher_repo = watcher_repo
    tp._manager = manager
    tp._contention_counts = {}
    tp._last_info_at = {}
    return tp


def _delivered(enqueue_mock) -> list[str]:
    """The canonical source strings of every delivered [JOB_EVENT]."""
    return [
        call.kwargs["source"]
        for call in enqueue_mock.enqueue_message.await_args_list
    ]


# ── Site 1: Fix-B inline mirror finalize (task_processor on_success) ──────


class TestSite1InlineMirrorFinalize:
    @pytest.mark.asyncio
    async def test_hook_fires_settled_and_dual_fire_is_deduped(
        self, engine, task_repo, job_repo, watcher_repo, enqueue_mock, service
    ):
        """on_success: the per-kind task-terminal notify AND the new
        post-commit mirror hook both fire for the same work_id — the CAS
        claim dedups to exactly ONE delivery; the mirror JobItem lands
        ``done``; the canonical 'settled' source string is on the wire."""
        instance_id = _seed_instance(engine, f"inst-{uuid4().hex[:8]}")
        work_id = str(uuid4())
        _seed_task(engine, work_id, instance_id, TaskStatus.RUNNING.value)
        _seed_mirror_job(engine, work_id, instance_id, admission_state="active")
        _seed_watcher(watcher_repo, work_id)

        # instance_manager reach: on_success reads _job_queue_service
        # (the hook) and _instance_repository (W6 anchor clear).
        manager = SimpleNamespace(
            _job_queue_service=service, _instance_repository=None
        )
        tp = _build_task_processor(
            task_repo, service._work_resolver, watcher_repo, manager
        )

        callbacks = tp._build_callbacks(
            Session(engine).get(Task, _task_pk(engine, work_id))
        )
        await callbacks.on_success(MagicMock(name="ProcessingResult"))

        deliveries = _delivered(enqueue_mock)
        assert len(deliveries) == 1, (
            f"site 1: hook + task-terminal notify must dedup to exactly "
            f"one delivery; got {deliveries}"
        )
        assert deliveries[0] == f"internal_agent:job_event:{work_id}:settled"
        # The mirror JobItem followed the task to done/completed.
        with Session(engine) as s:
            row = s.get(JobItem, work_id)
            assert row.admission_state == AdmissionState.DONE.value
            assert row.terminal_reason == "completed"
        # Watcher row CAS-claimed exactly once.
        assert watcher_repo.get_watchers_for_job(work_id) == []

    @pytest.mark.asyncio
    async def test_pre_terminal_task_completes_on_success_fully(
        self, engine, task_repo, job_repo, watcher_repo, enqueue_mock,
        service, instance_repo,
    ):
        """CRITICAL regression (merge-blocker round 2026-09-20): a Task
        that is ALREADY terminal when ``on_success`` runs (the designed-
        for concurrent-finalizer race — ``complete_task`` → None) must
        NOT raise ``UnboundLocalError`` from the mirror hook's binding.
        ``on_success`` must run to completion INCLUDING the W6
        usage-limit anchor clear that follows the hook."""
        from daemon.services.usage_limit_schedule import (
            USAGE_LIMIT_FIRST_SEEN_METADATA_KEY,
        )
        instance_id = _seed_instance(engine, f"inst-{uuid4().hex[:8]}")
        # Pre-seed the W6 episode anchor — its CLEAR is the tail work
        # the UnboundLocalError used to abort.
        with Session(engine) as s:
            inst = s.get(Instance, instance_id)
            inst.instance_metadata = {
                USAGE_LIMIT_FIRST_SEEN_METADATA_KEY: datetime.now(
                    timezone.utc
                ).isoformat()
            }
            s.add(inst)
            s.commit()

        work_id = str(uuid4())
        # ALREADY terminal: complete_task will return None and the
        # finalize block is skipped entirely.
        _seed_task(engine, work_id, instance_id, TaskStatus.COMPLETED.value)
        _seed_mirror_job(engine, work_id, instance_id, admission_state="active")
        _seed_watcher(watcher_repo, work_id)

        manager = SimpleNamespace(
            _job_queue_service=service, _instance_repository=instance_repo
        )
        tp = _build_task_processor(
            task_repo, service._work_resolver, watcher_repo, manager
        )

        callbacks = tp._build_callbacks(
            Session(engine).get(Task, _task_pk(engine, work_id))
        )
        # MUST NOT raise (pre-fix: UnboundLocalError: finalized_mirror).
        await callbacks.on_success(MagicMock(name="ProcessingResult"))

        # on_success reached the W6 anchor clear — the code AFTER the
        # hook site — and cleared the episode anchor.
        with Session(engine) as s:
            inst = s.get(Instance, instance_id)
            metadata = inst.instance_metadata or {}
        assert USAGE_LIMIT_FIRST_SEEN_METADATA_KEY not in metadata, (
            "W6 anchor clear did not run — on_success was aborted before "
            "its tail (the UnboundLocalError regression)"
        )
        # The skipped finalize block also means no mirror event fired.
        assert _delivered(enqueue_mock) == []

    @pytest.mark.asyncio
    async def test_hook_skips_when_finalize_races_to_none(
        self, engine, task_repo, job_repo, watcher_repo, enqueue_mock, service
    ):
        """Phantom-event guard: when the mirror write returns None
        (race-loss / task-kind), the hook must NOT notify."""
        instance_id = _seed_instance(engine, f"inst-{uuid4().hex[:8]}")
        work_id = str(uuid4())
        # TASK-kind JobItem: finalize_mirror_job_at_completion returns
        # None for task rows (the row IS its own mission).
        _seed_task(engine, work_id, instance_id, TaskStatus.RUNNING.value)
        with Session(engine) as s:
            s.add(JobItem(
                job_id=work_id,
                agent_id="developer",
                agent_dir="agents/developer",
                message="task work",
                source="agent:test",
                instance_id=instance_id,
                admission_state="active",
                job_type="task",
            ))
            s.commit()
        _seed_watcher(watcher_repo, work_id)

        # instance_manager reach: on_success reads _job_queue_service
        # (the hook) and _instance_repository (W6 anchor clear).
        manager = SimpleNamespace(
            _job_queue_service=service, _instance_repository=None
        )
        tp = _build_task_processor(
            task_repo, service._work_resolver, watcher_repo, manager
        )

        callbacks = tp._build_callbacks(
            Session(engine).get(Task, _task_pk(engine, work_id))
        )
        await callbacks.on_success(MagicMock(name="ProcessingResult"))

        # The pin: finalize returned None (task-kind row) ⇒ the hook
        # did NOT fire — no 'settled' phantom event exists on the wire.
        # (The per-kind task-terminal notify may itself resolve
        # non-terminal for a still-active task-kind JobItem row and
        # legitimately deliver nothing; either way the HOOK adds no
        # 'settled' delivery.)
        deliveries = _delivered(enqueue_mock)
        assert not any(d.endswith(":settled") for d in deliveries), (
            f"phantom 'settled' event from the mirror hook on a None "
            f"write: {deliveries}"
        )


# ── Site 2: F-1 reconcile_terminal_message_mirrors (recovery caller) ─────


class TestSite2ReconcileTerminalMirrors:
    @pytest.mark.asyncio
    async def test_silent_mirror_follow_fires_settled(
        self, engine, job_repo, task_repo, instance_repo, watcher_repo,
        enqueue_mock, service,
    ):
        recovery = JobRecoveryService(
            job_repository=job_repo,
            lock_repository=MagicMock(name="lock_repo"),
            instance_repository=instance_repo,
            job_queue_service=service,
            task_repository=task_repo,
        )
        instance_id = _seed_instance(engine, f"inst-{uuid4().hex[:8]}")
        work_id = str(uuid4())
        _seed_task(engine, work_id, instance_id, TaskStatus.COMPLETED.value)
        _seed_mirror_job(engine, work_id, instance_id, admission_state="active")
        _seed_watcher(watcher_repo, work_id)

        details = await recovery._reconcile_terminal_message_mirrors()

        assert len(details) == 1 and details[0]["job_id"] == work_id
        deliveries = _delivered(enqueue_mock)
        assert deliveries == [f"internal_agent:job_event:{work_id}:settled"]
        assert watcher_repo.get_watchers_for_job(work_id) == []

    @pytest.mark.asyncio
    async def test_reentrant_sweep_notifies_once(
        self, engine, job_repo, task_repo, instance_repo, watcher_repo,
        enqueue_mock, service,
    ):
        """Re-entrancy: a second sweep pass finds nothing left to
        transition → zero additional notifications."""
        recovery = JobRecoveryService(
            job_repository=job_repo,
            lock_repository=MagicMock(name="lock_repo"),
            instance_repository=instance_repo,
            job_queue_service=service,
            task_repository=task_repo,
        )
        instance_id = _seed_instance(engine, f"inst-{uuid4().hex[:8]}")
        work_id = str(uuid4())
        _seed_task(engine, work_id, instance_id, TaskStatus.COMPLETED.value)
        _seed_mirror_job(engine, work_id, instance_id, admission_state="active")
        _seed_watcher(watcher_repo, work_id)

        await recovery._reconcile_terminal_message_mirrors()
        await recovery._reconcile_terminal_message_mirrors()

        assert len(_delivered(enqueue_mock)) == 1


# ── Sites 4 + 5: cleanup_non_terminal_jobs (batch cancel + orphan) ────────


class TestSite4BatchCancelQueued:
    @pytest.mark.asyncio
    async def test_cancelled_queued_jobs_fire_once(
        self, engine, job_repo, task_repo, instance_repo, watcher_repo,
        enqueue_mock, service,
    ):
        work_id = str(uuid4())
        instance_id = _seed_instance(engine, f"inst-{uuid4().hex[:8]}")
        _seed_task_job(engine, work_id, instance_id, admission_state="queued")
        _seed_watcher(watcher_repo, work_id)

        stats = await service.cleanup_non_terminal_jobs()

        assert stats["cancelled_queued"] >= 1
        deliveries = _delivered(enqueue_mock)
        assert f"internal_agent:job_event:{work_id}:cancelled" in deliveries
        assert watcher_repo.get_watchers_for_job(work_id) == []

    @pytest.mark.asyncio
    async def test_double_cleanup_empty_capture_notifies_nothing(
        self, engine, job_repo, task_repo, instance_repo, watcher_repo,
        enqueue_mock, service,
    ):
        """Re-entrancy (minor #4): the SECOND cleanup pass has an empty
        pre-SELECT capture (nothing queued left) → zero notifications —
        double-cleanup is trivially the empty-capture case."""
        instance_id = _seed_instance(engine, f"inst-{uuid4().hex[:8]}")
        work_id = str(uuid4())
        _seed_task_job(engine, work_id, instance_id, admission_state="queued")
        _seed_watcher(watcher_repo, work_id)

        await service.cleanup_non_terminal_jobs()
        await service.cleanup_non_terminal_jobs()

        # Exactly one delivery total — the second pass captured nothing.
        assert len(_delivered(enqueue_mock)) == 1
        assert job_repo.find_batch_cancel_queued_ids() == []

    @pytest.mark.asyncio
    async def test_race_guard_stale_capture_never_notified(
        self, engine, job_repo, task_repo, instance_repo, watcher_repo,
        enqueue_mock, service,
    ):
        """Site-4 race (design §8 hazard): the pre-SELECT captures a job
        that a concurrent actor moves out of ``queued`` before the bulk
        UPDATE — the re-SELECT must EXCLUDE it (no notify, no phantom
        event, its watcher row untouched)."""
        instance_id = _seed_instance(engine, f"inst-{uuid4().hex[:8]}")
        winner = str(uuid4())
        raced = str(uuid4())
        _seed_task_job(engine, winner, instance_id, admission_state="queued")
        _seed_task_job(engine, raced, instance_id, admission_state="queued")
        _seed_watcher(watcher_repo, winner)
        _seed_watcher(watcher_repo, raced)

        # Stale capture: simulate the pre-SELECT having run BEFORE the
        # concurrent actor transitioned `raced` out of queued.
        stale_capture = [winner, raced]
        job_repo.find_batch_cancel_queued_ids = lambda: list(stale_capture)

        # Concurrent actor wins the race: `raced` goes queued → active.
        job_repo.atomic_transition(
            raced, from_status="queued", to_status="active",
            terminal_reason=None,
        )

        await service.cleanup_non_terminal_jobs()

        deliveries = _delivered(enqueue_mock)
        assert f"internal_agent:job_event:{winner}:cancelled" in deliveries
        assert not any(
            src.startswith(f"internal_agent:job_event:{raced}:")
            for src in deliveries
        ), f"raced job must NOT be notified (false-terminal guard): {deliveries}"
        # The raced job's watcher row SURVIVES (it is still active).
        assert len(watcher_repo.get_watchers_for_job(raced)) == 1


class TestSite5ForceFinalizeOrphan:
    @pytest.mark.asyncio
    async def test_orphan_finalize_fires_cancelled(
        self, engine, job_repo, task_repo, instance_repo, watcher_repo,
        enqueue_mock, service,
    ):
        """Orphan (active task-job, instance terminal) finalize → notify
        fires with the caller's terminal_reason token."""
        # NOTE: find_orphan_active_jobs' local terminal-instance
        # vocabulary is ("completed", "failed", "cancelled", "dead") —
        # seed a status that predicate treats as terminal (pre-existing
        # repo behavior; not this change's scope).
        instance_id = _seed_instance(
            engine, f"inst-{uuid4().hex[:8]}", "completed"
        )
        work_id = str(uuid4())
        _seed_task_job(engine, work_id, instance_id, admission_state="active")
        _seed_watcher(watcher_repo, work_id)

        stats = await service.cleanup_non_terminal_jobs()

        assert stats["orphaned_reaped"] >= 1
        deliveries = _delivered(enqueue_mock)
        assert f"internal_agent:job_event:{work_id}:cancelled" in deliveries
        assert watcher_repo.get_watchers_for_job(work_id) == []

    @pytest.mark.asyncio
    async def test_orphan_reaper_is_reentrant(self, engine, job_repo, task_repo,
            instance_repo, watcher_repo, enqueue_mock, service):
        """Re-entrancy: the second cleanup pass finds no orphans → zero
        additional notifications."""
        instance_id = _seed_instance(
            engine, f"inst-{uuid4().hex[:8]}", "completed"
        )
        work_id = str(uuid4())
        _seed_task_job(engine, work_id, instance_id, admission_state="active")
        _seed_watcher(watcher_repo, work_id)

        await service.cleanup_non_terminal_jobs()
        await service.cleanup_non_terminal_jobs()

        assert len(_delivered(enqueue_mock)) == 1


# ── Site 6: f1-DEAD pattern finalize (in-function hook) ───────────────────


class TestSite6PatternFDead:
    @pytest.mark.asyncio
    async def test_dead_transition_fires_dead_letter(
        self, engine, job_repo, task_repo, instance_repo, watcher_repo,
        enqueue_mock, service,
    ):
        lock_repo = LockRepository(engine)
        recovery = JobRecoveryService(
            job_repository=job_repo,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=service,
            task_repository=task_repo,
        )
        instance_id = _seed_instance(engine, f"inst-{uuid4().hex[:8]}")
        work_id = str(uuid4())
        # Active task-job with NO Task linked (the f1 predicate), alive
        # instance, past grace.
        _seed_task_job(
            engine, work_id, instance_id, admission_state="active",
            created_days_ago=30.0,
        )
        with Session(engine) as s:
            job_item = s.get(JobItem, work_id)
        _seed_watcher(watcher_repo, work_id)

        ok, reason = await recovery._pattern_f_finalize_dead(
            job=job_repo, lock=lock_repo, job_item=job_item,
        )

        assert ok, reason
        deliveries = _delivered(enqueue_mock)
        assert deliveries == [f"internal_agent:job_event:{work_id}:dead_letter"]
        assert watcher_repo.get_watchers_for_job(work_id) == []

    @pytest.mark.asyncio
    async def test_concurrent_finalize_notifies_never_twice(
        self, engine, job_repo, task_repo, instance_repo, watcher_repo,
        enqueue_mock, service,
    ):
        """Re-entrancy: a second f1 pass on an already-DEAD job takes the
        InvalidTransitionError no-op path → zero additional notify."""
        lock_repo = LockRepository(engine)
        recovery = JobRecoveryService(
            job_repository=job_repo,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=service,
            task_repository=task_repo,
        )
        instance_id = _seed_instance(engine, f"inst-{uuid4().hex[:8]}")
        work_id = str(uuid4())
        _seed_task_job(
            engine, work_id, instance_id, admission_state="active",
            created_days_ago=30.0,
        )
        with Session(engine) as s:
            job_item = s.get(JobItem, work_id)
        _seed_watcher(watcher_repo, work_id)

        ok1, _ = await recovery._pattern_f_finalize_dead(
            job=job_repo, lock=lock_repo, job_item=job_item,
        )
        ok2, _ = await recovery._pattern_f_finalize_dead(
            job=job_repo, lock=lock_repo, job_item=job_item,
        )

        assert ok1 and ok2
        assert len(_delivered(enqueue_mock)) == 1


# ── Site 3: exemption pin ─────────────────────────────────────────────────


class TestSite3Exemption:
    def test_reap_legacy_mirror_zombies_docstring_carve_out(self):
        """The site-3 carve-out lives as a ``#`` block above the def
        (medium #1 relocation) — pin it at its new home."""
        repo_src = (Path(__file__).resolve().parents[2] /
                    "daemon/repositories/job_queue/repository.py").read_text()
        marker = repo_src.find("# Notify carve-out")
        assert marker != -1, (
            "site-3 carve-out comment missing from repository.py — the "
            "census breadcrumb must live at the site"
        )
        def_block = repo_src[marker:marker + 900]
        assert "def reap_legacy_mirror_zombies(" in def_block, (
            "carve-out block no longer sits directly above the def"
        )
        assert "orphan_retired" in def_block
