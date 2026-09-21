"""Mid-flight QA channel — acceptance + failure-mode tests.

Design §9 (``.agents/shared/planning/midflight-qa-channel/design.md``).

Coverage map:

§9.1 acceptance:
  (a) test_question_surfaces_to_watcher — ask_questions emission lands
      a ``[JOB_EVENT] Job {work_id}... question requested ❓`` line in
      the watcher's instance (via notify_work_watchers → enqueue) with
      the pack payload carried in the body.
  (b) test_answer_resumes_asker — POST-answer helper resumes the asker
      with the Q↔A message in-context (NIT-17: the payload text reaches
      the resume call).
  (c) test_report_non_blocking — mid_flight_report emits; no pause flag.
  (d) test_no_polling_* — grep-clean checks as REAL test functions
      (NIT-16: covers midflight_qa.py, midflight_report.py,
      question_tools.py, the wedge-guard processor, AND the
      task/repository.py carve-out site).
  (e) test_completed_event_regression — terminal completed notify still
      carries the Result body AND claims the row; new non-terminal
      statuses preserve the row (v0.13.9 acceptance surface).

§9.2 failure modes:
  watcher-terminated (answer is watcher-independent), duplicate pending
  pack, question-with-children (paused_by_parent stamping), duplicate
  answer CAS no-op, CAS-winner/lost-handle race, QUESTION_PACK_MISMATCH
  hijack, event-emission failure containment, wedge-guard one-shot
  (emission + re-arm + escalation), daemon-restart rehydration.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session
from sqlmodel import select as _select

from daemon.constants import STUCK_HEARTBEAT_AFTER_SECONDS
from daemon.models.common import ErrorCodes
from daemon.repositories.event.models import Event, EventKind
from daemon.repositories.event.repository import EventRepository
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.watcher_models import (
    ALL_WATCHABLE_EVENTS,
    JobWatcher,
)
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import (
    SuspensionReason,
    Task,
    TaskStatus,
    TaskType,
)
from daemon.repositories.task.repository import TaskRepository
from daemon.routers.answer_helper import answer_questions_via_instance
from daemon.routers.instances import router as _instances_router
from daemon.services.event_bus import EventBus
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.services.midflight_qa import (
    emit_question_requested,
    mint_stuck_heartbeat_one_shot,
)
from daemon.services.question_manager import QuestionManager, pack_to_dict
from daemon.services.task_processor import HeartbeatEmitStuckProcessor
from daemon.services.work_resolver import WorkResolverService
from daemon.services.work_notifier import notify_work_watchers
from daemon.tools.midflight_report import create_midflight_tools
from daemon.tools.question_tools import create_question_tools
from daemon.write_pause_guard import WritePauseGuard

REPO_ROOT = Path(__file__).resolve().parents[2]


# =============================================================================
# Fixtures — real repos on an in-memory engine, mock manager facade
# =============================================================================


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


def _seed_instance(engine, iid=None, status=InstanceStatus.RUNNING.value, parent_id=None):
    iid = iid or f"inst-{uuid.uuid4()}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=iid,
                agent_id="leader",
                agent_dir="/tmp",
                project_id="p",
                status=status,
                version=1,
                instance_metadata={},
                parent_id=parent_id,
            )
        )
        session.commit()
    return iid


def _seed_task(engine, iid, work_id=None, status=TaskStatus.RUNNING.value,
               task_type=TaskType.PROCESS_MESSAGE.value, message_id=None):
    work_id = work_id or str(uuid.uuid4())
    with Session(engine) as session:
        session.add(
            Task(
                work_id=work_id,
                task_type=task_type,
                instance_id=iid,
                message_id=message_id,
                status=status,
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    return work_id


def _consume_handle(engine, work_id, *, to_pending=False):
    """Clear the awaiting_answer handle the way ResumeTurn does; with
    ``to_pending`` also flip the task back to PENDING."""
    sql = (
        "UPDATE task SET status='pending', "
        "suspension_reason=NULL, resume_target_turn_id=NULL "
        "WHERE work_id=:w"
        if to_pending
        else "UPDATE task SET suspension_reason=NULL, "
        "resume_target_turn_id=NULL WHERE work_id=:w"
    )
    with Session(engine) as session:
        session.execute(text(sql), {"w": work_id})
        session.commit()


def _fast_forward_pending_heartbeats(engine):
    """Fast-forward future-dated PENDING wedge-guard rows to due (the
    claim's next_retry_at gate holds them until then)."""
    with Session(engine) as session:
        session.execute(
            text(
                "UPDATE task SET next_retry_at=:past WHERE task_type="
                "'heartbeat_emit_stuck' AND status='pending'"
            ),
            {"past": "2000-01-01T00:00:00.000000+0000"},
        )
        session.commit()


def _pending_wedge_rows(task_repo, asker):
    """The asker's PENDING heartbeat_emit_stuck rows."""
    return [
        t
        for t in task_repo.get_by_instance(asker)
        if t.task_type == TaskType.HEARTBEAT_EMIT_STUCK.value
        and t.status == TaskStatus.PENDING.value
    ]


def _seed_wedge_asker(harness):
    """Seed a wedged asker: PAUSED instance + PAUSED task stamped with
    the awaiting_answer handle + the durable pack shadow."""
    asker = _seed_instance(harness.engine, status=InstanceStatus.PAUSED.value)
    work_id = _seed_task(
        harness.engine, asker, status=TaskStatus.PAUSED.value
    )
    # Stamp the awaiting_answer handle the way SuspendTurn does.
    with Session(harness.engine) as session:
        session.execute(
            text(
                "UPDATE task SET suspension_reason='awaiting_answer', "
                "resume_target_turn_id=:w WHERE work_id=:w"
            ),
            {"w": work_id},
        )
        session.commit()
    # Stamp the durable pack-id shadow (context carrier).
    pack = harness.qm.set_question_pack(asker, [{"id": "q1", "text": "Q?"}])
    harness.instance_repo.set_metadata(asker, "question_pack_id", pack.id)
    harness.instance_repo.set_metadata(
        asker, "question_pack_payload", pack_to_dict(pack)
    )
    return asker, work_id, pack


@pytest.fixture
def harness(engine):
    """Real repos + a manager facade mock with async seams recorded."""
    instance_repo = SQLModelInstanceRepository(engine)
    task_repo = TaskRepository(engine)
    job_repo = JobRepository(engine)
    watcher_repo = JobWatcherRepository(engine)
    event_repo = EventRepository(engine)
    event_bus = EventBus(event_repo=event_repo)
    work_resolver = WorkResolverService(
        task_repo=task_repo, job_repo=job_repo, instance_repo=instance_repo
    )
    qm = QuestionManager()

    manager = MagicMock()
    manager.engine = engine
    manager.is_write_paused = False
    manager._instance_repository = instance_repo
    manager._task_repo = task_repo
    manager._work_resolver = work_resolver
    manager._watcher_repo = watcher_repo
    manager._event_repo = event_repo
    manager._event_bus = event_bus
    manager._question_manager = qm
    manager._live_hub = MagicMock()
    manager._live_hub.stream_question_pack = AsyncMock()
    manager._live_hub.stream_midflight_report = AsyncMock()
    manager._live_hub.stream_stuck_awaiting_answer = AsyncMock()
    manager._live_hub.stream_answer_received = AsyncMock()
    manager._live_hub.stream_child_question_still_pending = AsyncMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager.enqueue_message = AsyncMock()
    manager.resume_processing_job = AsyncMock(
        return_value={"status": "resumed", "instance_id": "x"}
    )
    manager.resume_instance_cascade = AsyncMock(
        return_value={
            "target_id": "x",
            "resumed_ids": ["x"],
            "skipped_ids": [],
        }
    )
    manager.terminate_instance = AsyncMock(return_value=True)
    manager._notification_broadcaster = MagicMock()
    manager._notification_broadcaster.emit_question_escalation = AsyncMock(
        return_value=1
    )

    return SimpleNamespace(
        engine=engine,
        manager=manager,
        instance_repo=instance_repo,
        task_repo=task_repo,
        job_repo=job_repo,
        watcher_repo=watcher_repo,
        event_repo=event_repo,
        event_bus=event_bus,
        work_resolver=work_resolver,
        qm=qm,
    )


@pytest.fixture
def paused_asker(harness):
    """The standard answer-path prologue: a PAUSED asker with a PAUSED
    task (the awaiting_answer carrier) and one pending question pack.
    Returns ``(asker, work_id, pack)``."""
    asker = _seed_instance(harness.engine, status=InstanceStatus.PAUSED.value)
    work_id = _seed_task(harness.engine, asker, status=TaskStatus.PAUSED.value)
    pack = harness.qm.set_question_pack(asker, [{"id": "q1", "text": "Q?"}])
    return asker, work_id, pack


# =============================================================================
# §9.1 (a) — question surfaces to watcher
# =============================================================================


class TestQuestionSurfacesToWatcher:
    def test_question_surfaces_to_watcher(self, harness):
        """ask_questions emission → [JOB_EVENT] question requested ❓ in the
        watcher's instance, with the pack payload in the body."""
        asker = _seed_instance(harness.engine)
        work_id = _seed_task(harness.engine, asker)
        watcher_iid = _seed_instance(harness.engine, status=InstanceStatus.IDLE.value)
        harness.watcher_repo.add_watch(work_id, watcher_iid, None)

        pack = harness.qm.set_question_pack(
            asker, [{"id": "q1", "text": "Approach A or B?"}]
        )
        assert pack is not None

        notified = asyncio.run(
            emit_question_requested(harness.manager, asker, pack)
        )
        assert notified == 1

        # The watcher's instance received the [JOB_EVENT] line.
        enqueue_calls = harness.manager.enqueue_message.await_args_list
        assert len(enqueue_calls) == 1
        msg = enqueue_calls[0].kwargs["message"]
        assert "[JOB_EVENT] Job" in msg
        assert "question requested ❓" in msg
        assert "Approach A or B?" in msg  # pack payload rides the body
        assert enqueue_calls[0].kwargs["source"].startswith(
            f"internal_agent:job_event:{work_id}:question_requested"
        )

        # The event row persisted (durable EventBus trail).
        events = harness.event_repo.get_by_instance(asker)
        kinds = [e.kind for e in events]
        assert EventKind.QUESTION_REQUESTED.value in kinds

        # Non-terminal: the watch row SURVIVES for the terminal event.
        remaining = harness.watcher_repo.get_watchers_for_job(work_id)
        assert len(remaining) == 1

    def test_fan_out_reaches_every_live_work_id(self, harness):
        """MAJOR-1: an asker with an active JobItem AND a live Task gets
        notifications on BOTH work_ids."""
        asker = _seed_instance(harness.engine)
        task_work = _seed_task(harness.engine, asker)
        job_work = str(uuid.uuid4())
        with Session(harness.engine) as session:
            session.add(
                JobItem(
                    job_id=job_work,
                    agent_id="leader",
                    agent_dir="/tmp",
                    message="x",
                    source="api",
                    project_id="p",
                    priority=5,
                    job_metadata={},
                    queue_id="system_parallel_queue",
                    job_type="task",
                    instance_id=asker,
                    admission_state=AdmissionState.ACTIVE.value,
                )
            )
            session.commit()

        watcher_a = _seed_instance(harness.engine, status=InstanceStatus.IDLE.value)
        watcher_b = _seed_instance(harness.engine, status=InstanceStatus.IDLE.value)
        harness.watcher_repo.add_watch(task_work, watcher_a, None)
        harness.watcher_repo.add_watch(job_work, watcher_b, None)

        pack = harness.qm.set_question_pack(asker, [{"text": "Proceed?"}])
        asyncio.run(emit_question_requested(harness.manager, asker, pack))

        sources = [
            c.kwargs["source"]
            for c in harness.manager.enqueue_message.await_args_list
        ]
        assert any(task_work in s for s in sources), sources
        assert any(job_work in s for s in sources), sources


# =============================================================================
# §9.1 (b) — answer resumes asker with answer in-context
# =============================================================================


class TestAnswerResumesAsker:
    def test_answer_resumes_asker(self, harness):
        """POST-answer helper stores answers, notifies, and resumes the
        asker with the Q↔A payload in-context (NIT-17)."""
        asker = _seed_instance(harness.engine, status=InstanceStatus.PAUSED.value)
        _seed_task(  # the awaiting_answer handle
            harness.engine,
            asker,
            status=TaskStatus.PAUSED.value,
        )
        pack = harness.qm.set_question_pack(
            asker, [{"id": "q1", "text": "Approach A or B?"}]
        )

        result = asyncio.run(
            answer_questions_via_instance(
                harness.manager,
                asker,
                {"q1": "Approach A"},
                None,
                harness.manager._live_hub,
            )
        )
        assert result["status"] == "answered"
        assert result["resume_route"] == "answer_gate_existing_turn"

        # NIT-17: the answer PAYLOAD reached the resume call.
        resume_args = harness.manager.resume_processing_job.await_args
        delivered = resume_args.kwargs.get("message") or resume_args.args[1]
        assert "Approach A" in delivered
        assert "Approach A or B?" in delivered  # F7 echo

        # QUESTION_ANSWERED event persisted; watcher notified.
        kinds = [e.kind for e in harness.event_repo.get_by_instance(asker)]
        assert EventKind.QUESTION_ANSWERED.value in kinds

    def test_answer_via_job_route_resolves_work_id(self, paused_asker, harness):
        """The job-addressed surface resolves work_id → instance before
        delegating to the shared helper."""
        asker, work_id, _ = paused_asker

        record = harness.work_resolver.resolve_work(work_id)
        assert record is not None
        assert record.instance_id == asker

        result = asyncio.run(
            answer_questions_via_instance(
                harness.manager,
                record.instance_id,
                {"q1": "Yes"},
                None,
                None,
            )
        )
        assert result["status"] == "answered"


# =============================================================================
# §9.1 (c) — report non-blocking
# =============================================================================


class TestReportNonBlocking:
    def test_report_non_blocking(self, harness):
        asker = _seed_instance(harness.engine)
        work_id = _seed_task(harness.engine, asker)
        watcher_iid = _seed_instance(harness.engine, status=InstanceStatus.IDLE.value)
        harness.watcher_repo.add_watch(work_id, watcher_iid, None)

        # Pause flag must NOT be set by the report tool.
        flag_state: dict[str, bool] = {}
        harness.manager.set_question_pause_requested = MagicMock(
            side_effect=lambda iid: flag_state.__setitem__(iid, True)
        )

        tool = create_midflight_tools(harness.manager, asker)[0]
        out = asyncio.run(
            tool.coroutine(summary="50% done", details="phase 2 of 4", level="info")
        )
        assert out.startswith("Reported.")
        assert "1 watcher" in out

        # No pause flag set.
        assert flag_state == {}

        # Watcher got the midflight_report [JOB_EVENT] line.
        msg = harness.manager.enqueue_message.await_args_list[0].kwargs["message"]
        assert "mid-flight report ⟳" in msg
        assert "50% done" in msg

        # Event row persisted; watch row survives.
        kinds = [e.kind for e in harness.event_repo.get_by_instance(asker)]
        assert EventKind.MIDFLIGHT_REPORT.value in kinds
        assert len(harness.watcher_repo.get_watchers_for_job(work_id)) == 1


# =============================================================================
# §9.1 (d) — no polling (grep-clean as REAL test functions; NIT-16)
# =============================================================================


class TestNoPollingIntroduced:
    """AC-(d): grep-clean as real test functions.

    NIT-16 (binding): the grep-clean check must ALSO cover the
    wedge-guard processor file and the task/repository.py carve-out
    site — implemented here as assertions on the actual source text.
    """

    NEW_CODE_FILES = [
        "daemon/services/midflight_qa.py",
        "daemon/tools/midflight_report.py",
        "daemon/tools/question_tools.py",
    ]

    def _read(self, rel: str) -> str:
        return (REPO_ROOT / rel).read_text(encoding="utf-8")

    def test_no_asyncio_sleep_in_new_code(self):
        for rel in self.NEW_CODE_FILES:
            src = self._read(rel)
            assert "asyncio.sleep" not in src, f"asyncio.sleep found in {rel}"

    def test_no_sleep_loops_in_wedge_guard_processor(self):
        """The HeartbeatEmitStuckProcessor class body has no sleep/loop."""
        body = inspect.getsource(HeartbeatEmitStuckProcessor)
        assert "asyncio.sleep" not in body
        assert "while True" not in body
        assert "time.sleep" not in body

    def test_no_loops_in_midflight_service(self):
        src = self._read("daemon/services/midflight_qa.py")
        assert "while True" not in src
        assert "asyncio.sleep" not in src

    def test_event_stream_poll_interval_unchanged(self):
        """EVENT_STREAM_POLL_INTERVAL has no new consumers. On this
        lineage (v0.13.9) the constant lives only in constants.py —
        jobs_streaming.py hardcodes the 2s poll literal. Pin the
        current set so any NEW consumer fails this test."""
        hits = []
        for path in REPO_ROOT.glob("daemon/**/*.py"):
            if "EVENT_STREAM_POLL_INTERVAL" in path.read_text(encoding="utf-8"):
                hits.append(str(path.relative_to(REPO_ROOT)))
        assert sorted(hits) == ["daemon/constants.py"], hits

    def test_carve_out_site_is_type_scoped_disjunct(self):
        """The claim-gate carve-out in task/repository.py is exactly the
        §1.4 spec: a type-scoped OR disjunct WRAPPING the pause gate —
        and the concurrency gate stays running-only."""
        src = self._read("daemon/repositories/task/repository.py")
        claim_sql = src[src.index("def claim_pending_task"):src.index("RETURNING *")]
        # The carve-out wraps the pause gate.
        assert "task.task_type = :heartbeat_emit_stuck" in claim_sql
        # Bind literal present (same convention as process_message_type).
        assert '"heartbeat_emit_stuck": TaskType.HEARTBEAT_EMIT_STUCK.value' in src
        # The one-shot minting is a single future-dated insert, no scan.
        assert "create_one_shot_heartbeat" in src
        # No while-loop in the carve-out region.
        assert "while True" not in claim_sql

    def test_stuck_scheduling_is_one_shot_only(self):
        """instance_lifecycle.py's stuck context mints a single one-shot
        Task (no loops around the mint)."""
        src = self._read("daemon/services/instance_lifecycle.py")
        anchor = src.index("mint_stuck_heartbeat_one_shot")
        region = src[anchor - 2000 : anchor + 2000]
        assert "STUCK_HEARTBEAT_AFTER_SECONDS" in region
        assert "while True" not in region


# =============================================================================
# §9.1 (e) — completed-event Result bodies regression (v0.13.9 surface)
# =============================================================================


class TestCompletedEventRegression:
    def test_terminal_completed_still_claims_and_carries_result(self, harness):
        """The terminal notify path still claims the watcher row
        (exactly-once) and renders the Result body — the v0.13.9
        completed-event acceptance surface."""
        asker = _seed_instance(harness.engine)
        work_id = _seed_task(harness.engine, asker)
        watcher_iid = _seed_instance(harness.engine, status=InstanceStatus.IDLE.value)
        harness.watcher_repo.add_watch(work_id, watcher_iid, None)

        notified = asyncio.run(
            notify_work_watchers(
                work_id=work_id,
                status="completed",
                instance_manager=harness.manager,
                work_resolver=harness.work_resolver,
                watcher_repo=harness.watcher_repo,
                result_summary="Final assistant text — the result body",
            )
        )
        assert notified == 1
        msg = harness.manager.enqueue_message.await_args_list[0].kwargs["message"]
        assert "completed ✓" in msg
        assert "Result:" in msg
        assert "Final assistant text — the result body" in msg
        # Terminal: the row is CLAIMED (deleted) — exactly-once.
        assert harness.watcher_repo.get_watchers_for_job(work_id) == []

    def test_nonterminal_statuses_preserve_watch_rows(self, harness):
        """All four new non-terminal statuses preserve the watcher row."""
        asker = _seed_instance(harness.engine)
        work_id = _seed_task(harness.engine, asker)
        watcher_iid = _seed_instance(harness.engine, status=InstanceStatus.IDLE.value)
        harness.watcher_repo.add_watch(work_id, watcher_iid, None)

        for status in (
            "question_requested",
            "answer_received",
            "midflight_report",
            "stuck_awaiting_answer",
        ):
            asyncio.run(
                notify_work_watchers(
                    work_id=work_id,
                    status=status,
                    instance_manager=harness.manager,
                    work_resolver=harness.work_resolver,
                    watcher_repo=harness.watcher_repo,
                )
            )
            assert len(harness.watcher_repo.get_watchers_for_job(work_id)) == 1, status

    def test_new_event_kinds_never_reach_job_feedback_observer(self):
        """Lane-safety invariant: JobFeedbackObserver._process_event
        hard-filters non-instance_lifecycle — the 5 new kinds return
        before any transition logic."""
        src = inspect.getsource(JobFeedbackObserver._process_event)
        assert '!= "instance_lifecycle"' in src

    def test_watch_job_accepts_new_event_names(self):
        """watch_job's validation vocabulary includes the four new
        non-terminal statuses (approver H1)."""
        for name in (
            "question_requested",
            "answer_received",
            "midflight_report",
            "stuck_awaiting_answer",
        ):
            assert name in ALL_WATCHABLE_EVENTS


# =============================================================================
# §9.2 — failure-mode tests
# =============================================================================


class TestWatcherTerminatedMidFlight:
    def test_answer_path_is_watcher_independent(self, paused_asker, harness):
        """A TERMINATED watcher does not affect answer delivery — the
        pack lives on the asker; the resolver maps work_id → asker."""
        asker, work_id, _ = paused_asker
        watcher_iid = _seed_instance(
            harness.engine, status=InstanceStatus.TERMINATED.value
        )
        harness.watcher_repo.add_watch(work_id, watcher_iid, None)

        result = asyncio.run(
            answer_questions_via_instance(
                harness.manager, asker, {"q1": "Yes"}, None, None
            )
        )
        assert result["status"] == "answered"


class TestDuplicatePendingPackRejected:
    def test_second_ask_questions_rejected(self, harness):
        asker = _seed_instance(harness.engine)
        tool = create_question_tools(harness.manager, asker)[0]
        first = asyncio.run(
            tool.coroutine(questions=[{"text": "Q1?"}])
        )
        assert first.startswith("Asked the user:")
        second = asyncio.run(
            tool.coroutine(questions=[{"text": "Q2?"}])
        )
        assert "Already have a pending question pack" in second


class TestQuestionWithRunningChildren:
    def test_pause_cascade_stamps_paused_by_parent_on_artifacts(self, engine):
        """MAJOR-4: only the originator's task keeps awaiting_answer;
        cascade-inherited children get the distinct paused_by_parent."""
        parent = _seed_instance(engine)
        child = _seed_instance(engine, parent_id=parent)
        parent_work = _seed_task(engine, parent)
        child_work = _seed_task(engine, child)

        task_repo = TaskRepository(engine)
        manager = MagicMock()
        manager.engine = engine
        manager.write_guard = WritePauseGuard()
        manager._task_repo = task_repo

        service = InstanceLifecycleService.__new__(InstanceLifecycleService)
        service._manager = manager

        result = service._pause_cascade_db_sync(
            engine,
            manager.write_guard,
            tree_ids=[parent, child],
            paused_at_iso=datetime.now(timezone.utc).isoformat(),
            paused_instances_data=[(parent, "leader"), (child, "worker")],
            suspension_reason=SuspensionReason.AWAITING_ANSWER.value,
            originator_instance_id=parent,
        )

        assert set(result.awaiting_answer_suspensions) == {(parent, parent_work)}
        parent_row = task_repo.get_by_work_id(parent_work)
        child_row = task_repo.get_by_work_id(child_work)
        assert parent_row.suspension_reason == SuspensionReason.AWAITING_ANSWER.value
        assert child_row.suspension_reason == SuspensionReason.PAUSED_BY_PARENT.value

        # find_suspended_turn_for_answer sees ONLY the originator.
        assert task_repo.find_suspended_turn_for_answer(parent) is not None
        assert task_repo.find_suspended_turn_for_answer(child) is None

    def test_resume_cascade_emits_child_question_still_pending(self, harness):
        """OQ-2 decision B: parent resume wipes the child's handle while
        its pack is pending → CHILD_QUESTION_STILL_PENDING fires."""
        parent = _seed_instance(harness.engine, status=InstanceStatus.PAUSED.value)
        child = _seed_instance(
            harness.engine, status=InstanceStatus.PAUSED.value, parent_id=parent
        )
        # Both paused; the CHILD owns its own pending pack (its handle
        # was set by its own earlier ask).
        harness.qm.set_question_pack(child, [{"id": "cq", "text": "Child Q?"}])

        repo = harness.instance_repo
        manager = harness.manager
        manager._instance_repository.get_cascade_tree_ids = MagicMock(
            return_value=[parent, child]
        )
        manager._instance_repository.get_tree_root_id = MagicMock(return_value=parent)
        manager._instance_repository.get_ancestor_ids = MagicMock(return_value=[])

        harness.manager.write_guard = WritePauseGuard()
        service = InstanceLifecycleService.__new__(InstanceLifecycleService)
        service._manager = manager

        async def run():
            return await service.resume_instance_cascade(parent)

        asyncio.run(run())

        kinds = [e.kind for e in harness.event_repo.get_by_instance(child)]
        assert EventKind.CHILD_QUESTION_STILL_PENDING.value in kinds
        harness.manager._live_hub.stream_child_question_still_pending.assert_awaited()


class TestDuplicateAnswerCasNoop:
    def test_duplicate_answer_returns_already_delivered(self, paused_asker, harness):
        """First POST wins the CAS and resumes; the second short-circuits
        to 200 already_delivered with NO second resume/events."""
        asker, _, _ = paused_asker

        first = asyncio.run(
            answer_questions_via_instance(
                harness.manager, asker, {"q1": "Yes"}, None, None
            )
        )
        assert first["resume_route"] == "answer_gate_existing_turn"

        resumes_before = harness.manager.resume_processing_job.await_count
        second = asyncio.run(
            answer_questions_via_instance(
                harness.manager, asker, {"q1": "Yes-AGAIN"}, None, None
            )
        )
        assert second["status"] == "already_delivered"
        assert second["resume_route"] == "already_delivered"
        # NO second resume, NO Defect-3 enqueue.
        assert harness.manager.resume_processing_job.await_count == resumes_before
        harness.manager.enqueue_message.assert_not_awaited()

        # CAS loser did not clobber the winner's answers.
        pack = harness.qm.get_question_pack(asker)
        assert pack.answers["q1"] == "Yes"


class TestCasWinnerLostHandle:
    def test_winner_resume_and_loser_noop_on_lost_handle(self, paused_asker, harness):
        """§9.2 CAS-winner/lost-handle race: A wins CAS + consumes the
        handle; B (concurrent) loses the entry CAS → no-op, never
        reaches the finder, never double-delivers."""
        asker, work_id, _ = paused_asker

        # Simulate A winning and the handle being consumed by its
        # ResumeTurn (status flips PAUSED → PENDING, handle cleared).
        def a_resume(iid, **kwargs):
            assert hasattr(
                harness.task_repo, "force_cancel_and_schedule_retry"
            )
            # Consume the handle the way ResumeTurn does.
            _consume_handle(harness.engine, work_id, to_pending=True)
            return {"status": "resumed"}

        harness.manager.resume_processing_job = AsyncMock(side_effect=a_resume)

        a = asyncio.run(
            answer_questions_via_instance(
                harness.manager, asker, {"q1": "A"}, None, None
            )
        )
        assert a["resume_route"] == "answer_gate_existing_turn"

        # The durable finder now returns None (handle consumed) — but a
        # concurrent B is short-circuited at the ENTRY CAS before it
        # ever reaches the finder.
        assert harness.task_repo.find_suspended_turn_for_answer(asker) is None
        b = asyncio.run(
            answer_questions_via_instance(
                harness.manager, asker, {"q1": "B"}, None, None
            )
        )
        assert b["resume_route"] == "already_delivered"
        assert harness.manager.resume_processing_job.await_count == 1


class TestQuestionPackMismatch:
    def test_stale_pack_id_rejected_400(self, paused_asker, harness):
        """T1″: body question_pack_id ≠ current pack id → 400 hijack guard."""
        asker, _, pack = paused_asker

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                answer_questions_via_instance(
                    harness.manager,
                    asker,
                    {"q1": "stale answer"},
                    question_pack_id=str(uuid.uuid4()),  # the OLD pack id
                    live_hub=None,
                )
            )
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["code"] == ErrorCodes.QUESTION_PACK_MISMATCH.value
        # The pack is still pending — the hijack did NOT stamp it.
        assert harness.qm.get_question_pack(asker).status == "pending"

    def test_matching_pack_id_accepted(self, paused_asker, harness):
        asker, _, pack = paused_asker
        result = asyncio.run(
            answer_questions_via_instance(
                harness.manager, asker, {"q1": "yes"}, pack.id, None
            )
        )
        assert result["status"] == "answered"


class TestTerminalAskerGuards:
    @pytest.mark.parametrize(
        "terminal_status",
        [InstanceStatus.COMPLETED, InstanceStatus.TERMINATED],
        ids=["completed", "terminated"],
    )
    def test_terminal_asker_410(self, harness, terminal_status):
        """T3 pre-check: a COMPLETED/TERMINATED asker is refused with
        410 ANSWER_TARGET_TERMINAL (leader decision 1) — never silently
        revived."""
        asker = _seed_instance(harness.engine, status=terminal_status.value)
        harness.qm.set_question_pack(asker, [{"id": "q1", "text": "Q?"}])
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                answer_questions_via_instance(
                    harness.manager, asker, {"q1": "late"}, None, None
                )
            )
        assert exc_info.value.status_code == 410
        assert (
            exc_info.value.detail["code"] == ErrorCodes.ANSWER_TARGET_TERMINAL.value
        )

    def test_error_asker_revived_with_flag(self, harness):
        asker = _seed_instance(harness.engine, status=InstanceStatus.ERROR.value)
        _seed_task(harness.engine, asker, status=TaskStatus.PAUSED.value)
        harness.qm.set_question_pack(asker, [{"id": "q1", "text": "Q?"}])
        # No handle resolvable → Defect-3 fallback delivers as fresh msg.
        harness.manager.resume_processing_job = AsyncMock(return_value=None)
        result = asyncio.run(
            answer_questions_via_instance(
                harness.manager, asker, {"q1": "recovery"}, None, None
            )
        )
        assert result["resume_route"] == "revived_error_target"
        harness.manager.enqueue_message.assert_awaited_once()


class TestEventEmissionFailure:
    def test_asker_still_stores_pack_when_event_bus_fails(self, harness):
        """§8.6: EventBus failure never breaks the asker — pack stored,
        tool returns the normal echo."""
        asker = _seed_instance(harness.engine)
        harness.manager._event_bus.create_event = AsyncMock(
            side_effect=RuntimeError("bus down")
        )
        tool = create_question_tools(harness.manager, asker)[0]
        out = asyncio.run(tool.coroutine(questions=[{"text": "Q?"}]))
        assert out.startswith("Asked the user:")
        assert harness.qm.get_question_pack(asker).status == "pending"


class TestWedgeGuardOneShot:
    def test_heartbeat_claimable_while_asker_paused(self, harness):
        """R2: the carve-out lets the one-shot claim while the asker is
        PAUSED; ordinary task types stay excluded."""
        asker, _, _ = _seed_wedge_asker(harness)
        harness.task_repo.create_one_shot_heartbeat(
            TaskType.HEARTBEAT_EMIT_STUCK.value, asker,
            datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        claimed = harness.task_repo.claim_pending_task("worker-1")
        assert claimed is not None
        assert claimed.task_type == TaskType.HEARTBEAT_EMIT_STUCK.value

    def test_ordinary_task_still_blocked_for_paused_instance(self, harness):
        asker, _, _ = _seed_wedge_asker(harness)
        _seed_task(harness.engine, asker, status=TaskStatus.PENDING.value)
        harness.task_repo.create_one_shot_heartbeat(
            TaskType.HEARTBEAT_EMIT_STUCK.value, asker,
            datetime.now(timezone.utc) + timedelta(hours=1),
        )
        claimed = harness.task_repo.claim_pending_task("worker-1")
        # The future-dated heartbeat is not due; the ordinary PENDING
        # task is excluded by the pause gate → nothing claims.
        assert claimed is None

    def test_wedge_emission_rearms_and_escalates(self, harness):
        """Full chain: emission with index derivation, exactly-one
        successor re-arm while index<3, escalation at 3 (terminate +
        broadcaster + NO successor)."""
        processor = HeartbeatEmitStuckProcessor(
            harness.manager, harness.task_repo, harness.event_repo
        )
        asker, _, pack = _seed_wedge_asker(harness)

        async def claim_and_run():
            task = harness.task_repo.claim_pending_task("w1")
            assert task is not None
            return await processor.process(task)

        # ── Emission #2 (one prior stuck event from pause-time #1) ──
        harness.event_repo.create_event(
            instance_id=asker,
            kind=EventKind.STUCK_AWAITING_ANSWER.value,
            data={"question_pack_id": pack.id, "emission_index": 1},
        )
        harness.task_repo.create_one_shot_heartbeat(
            TaskType.HEARTBEAT_EMIT_STUCK.value, asker,
            datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        result = asyncio.run(claim_and_run())
        assert result["emission"] == "stuck_awaiting_answer"
        assert result["emission_index"] == 2
        assert result["re_armed"] is True
        # Exactly ONE successor minted (PENDING, future-dated).
        pending = _pending_wedge_rows(harness.task_repo, asker)
        assert len(pending) == 1

        # ── Emission #3 → escalation ────────────────────────────────
        harness.event_repo.create_event(
            instance_id=asker,
            kind=EventKind.STUCK_AWAITING_ANSWER.value,
            data={"question_pack_id": pack.id, "emission_index": 2},
        )
        # The successor is future-dated at now+1800s — fast-forward it
        # to due (the claim's next_retry_at gate holds it until then).
        _fast_forward_pending_heartbeats(harness.engine)
        task = harness.task_repo.claim_pending_task("w2")
        assert task is not None and task.task_type == TaskType.HEARTBEAT_EMIT_STUCK.value
        result = asyncio.run(processor.process(task))
        assert result["emission"] == "escalated"
        harness.manager.terminate_instance.assert_awaited_once_with(
            asker, terminal_reason="wedge_guard_terminated"
        )
        harness.manager._notification_broadcaster.emit_question_escalation.assert_awaited()
        # NO successor after escalation.
        pending = _pending_wedge_rows(harness.task_repo, asker)
        assert pending == []

    def test_noop_when_handle_consumed(self, harness):
        """Answered asker (handle cleared) → the heartbeat self-cancels."""
        processor = HeartbeatEmitStuckProcessor(
            harness.manager, harness.task_repo, harness.event_repo
        )
        asker, work_id, _ = _seed_wedge_asker(harness)
        # Consume the handle (answer landed).
        _consume_handle(harness.engine, work_id)

        harness.task_repo.create_one_shot_heartbeat(
            TaskType.HEARTBEAT_EMIT_STUCK.value, asker,
            datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        task = harness.task_repo.claim_pending_task("w3")
        result = asyncio.run(processor.process(task))
        assert result["emission"] == "no_op"
        harness.manager.terminate_instance.assert_not_awaited()
        pending = _pending_wedge_rows(harness.task_repo, asker)
        assert pending == []

    def test_drift_reconciler_excludes_heartbeat_rows(self, harness):
        """Leader decision 3: heartbeat rows don't surface as drift."""
        asker, _, _ = _seed_wedge_asker(harness)
        harness.task_repo.create_one_shot_heartbeat(
            TaskType.HEARTBEAT_EMIT_STUCK.value, asker,
            datetime.now(timezone.utc) + timedelta(hours=1),
        )
        drift = harness.task_repo.list_pending_tasks_older_than(age_seconds=300)
        assert drift == []


class TestDaemonRestartKeepsPack:
    def test_rehydration_from_metadata_then_answer_200(self, harness):
        """§9.2: RAM pack gone (restart) → rehydrate from the durable
        shadow → the answer succeeds (not 404/410)."""
        asker = _seed_instance(harness.engine, status=InstanceStatus.PAUSED.value)
        _seed_task(harness.engine, asker, status=TaskStatus.PAUSED.value)
        # Pre-restart state: pack was shadowed into metadata, then the
        # daemon restarted (in-memory store empty).
        old_qm = QuestionManager()
        pack = old_qm.set_question_pack(asker, [{"id": "q1", "text": "Q?"}])
        harness.instance_repo.set_metadata(asker, "question_pack_id", pack.id)
        harness.instance_repo.set_metadata(
            asker, "question_pack_payload", pack_to_dict(pack)
        )
        assert harness.qm.get_question_pack(asker) is None

        # Boot-time hydration pass (manager.__init__ wiring calls this).
        payload = harness.instance_repo.get_metadata_value(
            asker, "question_pack_payload"
        )
        restored = harness.qm.rehydrate_from_payloads({asker: payload})
        assert restored == 1

        result = asyncio.run(
            answer_questions_via_instance(
                harness.manager, asker, {"q1": "post-restart"}, None, None
            )
        )
        assert result["status"] == "answered"

    def test_lost_pack_after_restart_is_410(self, harness):
        asker = _seed_instance(harness.engine, status=InstanceStatus.PAUSED.value)
        # No RAM pack, no metadata shadow.
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                answer_questions_via_instance(
                    harness.manager, asker, {"q1": "x"}, None, None
                )
            )
        assert exc_info.value.status_code == 410
        assert exc_info.value.detail["code"] == ErrorCodes.QUESTION_PACK_LOST.value

    def test_metadata_cleared_on_answer_consumption(self, paused_asker, harness):
        """R3: both shadow keys are cleared when the answer lands."""
        asker, _, pack = paused_asker
        harness.instance_repo.set_metadata(asker, "question_pack_id", pack.id)

        asyncio.run(
            answer_questions_via_instance(
                harness.manager, asker, {"q1": "yes"}, None, None
            )
        )
        assert harness.instance_repo.get_metadata_value(asker, "question_pack_id") is None


# =============================================================================
# Fix-pass regression pins (code-review adjudicated, 2026-09-21)
# =============================================================================


def _mount_instances_router(harness):
    """Mount ONLY the instances router on a bare app with the harness
    manager injected — the lightweight pattern from
    tests/test_question_dismiss.py (middleware state injection)."""
    # The endpoints' existence check awaits manager.get_instance —
    # the MagicMock auto-attribute is not awaitable.
    harness.manager.get_instance = AsyncMock(
        return_value=SimpleNamespace(instance_id="exists")
    )

    app = FastAPI()
    app.include_router(_instances_router)

    @app.middleware("http")
    async def _inject(request, call_next):
        request.app.state.manager = harness.manager
        request.app.state.live_hub = None
        return await call_next(request)

    return TestClient(app)


class TestDismissClearsDurableShadow:
    """M1 pin (council-verified): dismiss must clear the DURABLE metadata
    shadow, not just the RAM pack — a late answer after dismiss must NOT
    rehydrate/CAS-win/inject into the running dismissed-past agent."""

    def test_dismiss_clears_shadow_then_late_answer_is_410(self, paused_asker, harness):
        asker, _, pack = paused_asker
        # The durable shadow exists (stamped at ask time, §5.4).
        harness.instance_repo.set_metadata(asker, "question_pack_id", pack.id)
        harness.instance_repo.set_metadata(
            asker, "question_pack_payload", pack_to_dict(pack)
        )
        harness.manager._deferred_question_pause = set()

        client = _mount_instances_router(harness)
        resp = client.post(f"/instances/{asker}/question/dismiss")
        assert resp.status_code == 200
        # M1: BOTH shadow keys are gone after dismiss — the pack cannot
        # be resurrected from instance_metadata.
        assert harness.instance_repo.get_metadata_value(
            asker, "question_pack_id"
        ) is None
        assert harness.instance_repo.get_metadata_value(
            asker, "question_pack_payload"
        ) is None

        # Late answer after dismiss: RAM pack gone AND shadow gone →
        # 410 QUESTION_PACK_LOST. NO rehydration, NO CAS win, NO
        # injection into the dismissed-past (now resumed) agent.
        harness.manager.resume_processing_job.reset_mock()
        harness.manager.enqueue_message.reset_mock()
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                answer_questions_via_instance(
                    harness.manager, asker, {"q1": "late"}, None, None
                )
            )
        assert exc_info.value.status_code == 410
        assert exc_info.value.detail["code"] == ErrorCodes.QUESTION_PACK_LOST.value
        harness.manager.enqueue_message.assert_not_awaited()
        harness.manager.resume_processing_job.assert_not_awaited()


class TestGateSupersessionClearsDurableShadow:
    """M1 pin (second unwind site): POST /resume gate-supersession must
    clear the durable metadata shadow alongside the RAM pack."""

    def test_resume_gate_supersession_clears_shadow(self, paused_asker, harness):
        asker, _, pack = paused_asker
        harness.instance_repo.set_metadata(asker, "question_pack_id", pack.id)
        harness.instance_repo.set_metadata(
            asker, "question_pack_payload", pack_to_dict(pack)
        )
        harness.manager._deferred_question_pause = set()

        client = _mount_instances_router(harness)
        resp = client.post(f"/instances/{asker}/resume")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("gate_superseded") is True
        # M1: the superseded pack's durable shadow is gone.
        assert harness.instance_repo.get_metadata_value(
            asker, "question_pack_id"
        ) is None
        assert harness.instance_repo.get_metadata_value(
            asker, "question_pack_payload"
        ) is None


class TestFallbackTerminalTOCTOU:
    """M2 pin (council-verified): the Defect-3 fallback re-reads the
    asker status before enqueueing — an asker that reached a terminal
    state in the pre-check→fallback window gets 410
    ANSWER_TARGET_TERMINAL, never a silent enqueue_message revive
    (instance_messaging.py:1898-1925 revive-on-terminal)."""

    def test_fallback_with_late_terminated_asker_410_no_revive(self, harness):
        asker = _seed_instance(harness.engine, status=InstanceStatus.RUNNING.value)
        _seed_task(harness.engine, asker, status=TaskStatus.PAUSED.value)
        harness.qm.set_question_pack(asker, [{"id": "q1", "text": "Q?"}])
        # No resumable handle → resume_processing_job returns None →
        # the request walks into the Defect-3 fallback.
        harness.manager.resume_processing_job = AsyncMock(return_value=None)

        # Terminal TOCTOU: read #1 (the step-2 pre-check) sees the
        # seeded RUNNING row; EVERY later read — including the fallback
        # re-read — sees TERMINATED (e.g. the wedge-guard escalation
        # terminated the chain at t≈3600s, exactly when late answers
        # land).
        real_get = harness.instance_repo.get
        calls = {"n": 0}

        def _get_flipping(iid):
            calls["n"] += 1
            if calls["n"] >= 2:
                return SimpleNamespace(status=InstanceStatus.TERMINATED.value)
            return real_get(iid)

        harness.instance_repo.get = _get_flipping

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                answer_questions_via_instance(
                    harness.manager, asker, {"q1": "late"}, None, None
                )
            )
        assert exc_info.value.status_code == 410
        assert exc_info.value.detail["code"] == ErrorCodes.ANSWER_TARGET_TERMINAL.value
        # The critical pin: NO enqueue → no silent revive of the
        # terminated asker.
        harness.manager.enqueue_message.assert_not_awaited()
        # The answer itself was CAS-consumed before the refusal (pack
        # answered in RAM, shadow cleared) — durable, just undeliverable.
        assert harness.qm.get_question_pack(asker).status == "answered"

    def test_fallback_still_enqueues_for_live_asker(self, paused_asker, harness):
        """Control: the re-read guard does not break the sanctioned
        fallback for a LIVE (non-terminal) asker."""
        asker, _, _ = paused_asker
        harness.manager.resume_processing_job = AsyncMock(return_value=None)

        result = asyncio.run(
            answer_questions_via_instance(
                harness.manager, asker, {"q1": "yes"}, None, None
            )
        )
        assert result["resume_route"] == "enqueue_as_fresh_message"
        harness.manager.enqueue_message.assert_awaited_once()


class TestInRequestRehydration:
    """MINOR-5: the helper's OWN on-the-fly rehydration branch
    (answer_helper §3, the :184-196 area) — metadata shadow seeded ONLY,
    RAM empty, and NO boot-time pre-hydration pass (unlike
    test_rehydration_from_metadata_then_answer_200, which pre-hydrates)."""

    def test_shadow_only_rehydrates_in_request_and_proceeds(self, harness):
        asker = _seed_instance(harness.engine, status=InstanceStatus.PAUSED.value)
        _seed_task(harness.engine, asker, status=TaskStatus.PAUSED.value)
        # Seed ONLY the durable shadow — a pre-restart manager held the
        # pack; THIS daemon's RAM store never saw it.
        old_qm = QuestionManager()
        pack = old_qm.set_question_pack(asker, [{"id": "q1", "text": "Q?"}])
        harness.instance_repo.set_metadata(asker, "question_pack_id", pack.id)
        harness.instance_repo.set_metadata(
            asker, "question_pack_payload", pack_to_dict(pack)
        )
        assert harness.qm.get_question_pack(asker) is None  # RAM empty

        result = asyncio.run(
            answer_questions_via_instance(
                harness.manager, asker, {"q1": "restored"}, None, None
            )
        )
        # The rehydration-success branch restored the pack mid-request
        # and the normal answer path proceeded through it.
        assert result["status"] == "answered"
        assert result["question_pack"]["pack_id"] == pack.id
        assert result["question_pack"]["status"] == "answered"
        restored = harness.qm.get_question_pack(asker)
        assert restored is not None and restored.status == "answered"


class TestWedgeGuardFixPassPins:
    """MINOR-1 (telemetry truth) + MINOR-2/3 (PENDING-row cap)."""

    def test_waiting_for_seconds_matches_elapsed_intervals(self, harness):
        """MINOR-1: heartbeat #2 (ONE prior persisted event = ONE elapsed
        1800s interval) reports 1800s — the old ``× (prior+1)`` reported
        3600s and drifted from the ~60-min escalation prose."""
        processor = HeartbeatEmitStuckProcessor(
            harness.manager, harness.task_repo, harness.event_repo
        )
        asker, _, pack = _seed_wedge_asker(harness)
        # Pause-time emission #1 already persisted (transition-time site).
        harness.event_repo.create_event(
            instance_id=asker,
            kind=EventKind.STUCK_AWAITING_ANSWER.value,
            data={"question_pack_id": pack.id, "emission_index": 1},
        )
        harness.task_repo.create_one_shot_heartbeat(
            TaskType.HEARTBEAT_EMIT_STUCK.value, asker,
            datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        task = harness.task_repo.claim_pending_task("w-minor1")
        assert task is not None
        result = asyncio.run(processor.process(task))
        assert result["emission_index"] == 2

        # The EMITTED payload reports the true elapsed wait: 1 interval.
        with Session(harness.engine) as session:
            rows = session.exec(
                _select(Event).where(Event.instance_id == asker)
            ).all()

        def _data_of(e):
            d = e.data
            if isinstance(d, str):
                try:
                    d = json.loads(d)
                except Exception:  # noqa: BLE001
                    d = {}
            return d or {}

        stuck = [
            e
            for e in rows
            if getattr(e.kind, "value", e.kind) == EventKind.STUCK_AWAITING_ANSWER.value
            and _data_of(e).get("emission_index") == 2
        ]
        assert len(stuck) == 1
        assert _data_of(stuck[0])["waiting_for_seconds"] == STUCK_HEARTBEAT_AFTER_SECONDS

    def test_mint_cap_skips_when_pending_row_exists(self, harness):
        """MINOR-2/3: at most ONE pending wedge-guard row per asker — a
        re-mint while a PENDING row exists is skipped (re-ask stale-link
        fix + chain finiteness bound)."""
        asker, _, _ = _seed_wedge_asker(harness)
        # Pause-site mint (first link) — future-dated, PENDING.
        first = mint_stuck_heartbeat_one_shot(
            harness.manager, asker,
            fire_after_seconds=STUCK_HEARTBEAT_AFTER_SECONDS,
        )
        assert first is not None
        # Re-ask before the first link fired: the cap SKIPS the second
        # mint (two live chains would double-fire emissions for the new
        # pack id → early escalation).
        second = mint_stuck_heartbeat_one_shot(harness.manager, asker)
        assert second is None
        pending = _pending_wedge_rows(harness.task_repo, asker)
        assert len(pending) == 1

