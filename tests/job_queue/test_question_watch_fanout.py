"""Question/watch fan-out regression — mission-scoped, emission-time QA delivery.

Commission ``feature/question-watch-fanout`` (2026-10-05), RCA-ratified.

## The defect

QA fan-out (``question requested ❓`` / ``stuck awaiting answer ⏳``) was
RECIPIENT-ADDRESSED PER RECEIPT: ``emit_question_requested`` /
``emit_stuck_awaiting_answer`` fanned out over the asker's LIVE work_ids
only (``enumerate_live_work_ids``) and let ``notify_work_watchers``
match rows per receipt, filtered by each row's ``watch_events``. Two
compounding holes:

1. **Spontaneous receipts mint with zero coverage** — a child-report
   wake (``PROCESS_REPORT`` Task mint) creates a NEW receipt that no
   watcher row covers. The asking turn rides that receipt; the
   per-receipt lookup finds ZERO watcher rows → zero emissions.
2. **``mission_terminal``-only rows never match QA events** —
   ``standard_match=False`` + mission still live → the row lands in
   ``held_for_mission`` and is skipped, even when the fan-out DID
   enumerate the receipt the row sits on.

Incident geometry (mission e71d133d): the watcher armed
``mission_terminal``-only on receipt 4d31ddbe (settled 15 min prior);
a child-report wake minted receipt 6d8765f2 (kind=report, no watch
row); the parent asked on that turn → ZERO emissions. The mission sat
paused awaiting a human answer no human ever heard about.

## The fix (decided by leader — pinned here)

QA emissions resolve recipients at EMISSION TIME by MISSION SCOPE:
every watcher holding an UNCLAIMED row on ANY receipt ever associated
with the asking instance's mission — regardless of (i) which receipt
the asking turn rides and (ii) the row's ``events`` subscription
(events-filter-EXEMPT). Delivery is NON-CLAIMING (the three-bucket
partition, the ``mission_live`` HOLD, and the N1 CAS are untouched);
dedupe is per watcher (rows on multiple receipts → ONE emission).
Escalation (≥3 heartbeats) adds the mission watcher as a delivery
target in addition to the FE SSE broadcast.

``mission_terminal`` delivery itself is UNCHANGED: per-receipt
delta-arm, C1 ``evaluate_mission_live`` gate, N1 claim-first CAS,
C3 fail-CLOSED, F1 no-replay, boot sweep (36be8aef family). A
spontaneous receipt still needs a ``watch_mission`` re-arm for
TERMINAL coverage — QA reach does not create terminal coverage.

Recipe: real repos on in-memory SQLite + mock manager facade (same as
``test_midflight_qa.py``).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.constants import STUCK_HEARTBEAT_AFTER_SECONDS
from daemon.repositories.event.models import EventKind
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import (
    Task,
    TaskStatus,
    TaskType,
)
from daemon.repositories.task.repository import TaskRepository
from daemon.repositories.event.repository import EventRepository
from daemon.services.event_bus import EventBus
from daemon.services.midflight_qa import (
    emit_question_escalation_notification,
    emit_question_requested,
    emit_stuck_awaiting_answer,
)
from daemon.services.question_manager import QuestionManager, pack_to_dict
from daemon.services.task_processor import HeartbeatEmitStuckProcessor
from daemon.services.work_notifier import notify_work_watchers
from daemon.services.work_resolver import WorkResolverService


# =============================================================================
# Fixtures — real repos on an in-memory engine, mock manager facade
# (mirror of tests/job_queue/test_midflight_qa.py)
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


def _seed_task(
    engine,
    iid,
    work_id=None,
    status=TaskStatus.RUNNING.value,
    task_type=TaskType.PROCESS_MESSAGE.value,
    created_at=None,
    message_id=None,
):
    work_id = work_id or str(uuid.uuid4())
    with Session(engine) as session:
        session.add(
            Task(
                work_id=work_id,
                task_type=task_type,
                instance_id=iid,
                message_id=message_id,
                status=status,
                created_at=created_at or datetime.now(timezone.utc),
            )
        )
        session.commit()
    return work_id


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
    manager._instance_repository = instance_repo
    manager._task_repo = task_repo
    manager._work_resolver = work_resolver
    manager._watcher_repo = watcher_repo
    manager._event_repo = event_repo
    manager._event_bus = event_bus
    manager._question_manager = qm
    manager._live_hub = MagicMock()
    manager._live_hub.stream_question_pack = AsyncMock()
    manager._live_hub.stream_stuck_awaiting_answer = AsyncMock()
    manager.enqueue_message = AsyncMock()
    manager._notification_broadcaster = MagicMock()
    manager._notification_broadcaster.emit_question_escalation = AsyncMock(
        return_value=1
    )
    manager.terminate_instance = AsyncMock(return_value=True)

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


def _seed_incident(harness):
    """The EXACT incident geometry (RCA, mission e71d133d).

    * mission M — the asker (chat-mission leader instance).
    * receipt R1 — a Task row of M that SETTLED 15 minutes prior.
    * watcher W — armed ``mission_terminal``-ONLY on R1 (its ONLY live
      row; the delta-arm skipped the then-settled receipts).
    * receipt R2 — the child-report wake minted a NEW ``PROCESS_REPORT``
      Task row on M (PENDING) with NO watcher row (spontaneous receipt,
      zero coverage). The asking turn rides R2.

    Returns ``(mission, r1_settled, r2_fresh, watcher)``.
    """
    mission = _seed_instance(harness.engine)
    r1_settled = _seed_task(
        harness.engine,
        mission,
        status=TaskStatus.COMPLETED.value,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=15),
    )
    watcher = _seed_instance(harness.engine, status=InstanceStatus.IDLE.value)
    harness.watcher_repo.add_watch(r1_settled, watcher, ["mission_terminal"])
    r2_fresh = _seed_task(
        harness.engine,
        mission,
        status=TaskStatus.PENDING.value,
        task_type=TaskType.PROCESS_REPORT.value,
    )
    return mission, r1_settled, r2_fresh, watcher


def _enqueues_for(harness, instance_id):
    return [
        c
        for c in harness.manager.enqueue_message.await_args_list
        if c.kwargs.get("instance_id") == instance_id
    ]


# =============================================================================
# THE incident regression — child-report wake receipt + mission watcher
# =============================================================================


class TestIncidentRegression:
    def test_question_on_wake_receipt_reaches_mission_watcher(
        self, harness
    ):
        """Child-report wake mints a new receipt (no watch row) → the
        parent asks → the mission watcher receives the
        ``question requested ❓`` [JOB_EVENT].

        Exact incident geometry: the watcher's only live row is
        ``mission_terminal``-ONLY on an ALREADY-SETTLED receipt. The
        envelope names the watcher's OWN armed receipt (the identity it
        holds), and that receipt still resolves to the asker for the
        answer route (pack resolution is watcher-independent).
        """
        mission, r1_settled, r2_fresh, watcher = _seed_incident(harness)
        pack = harness.qm.set_question_pack(
            mission, [{"id": "q1", "text": "Approach A or B?"}]
        )
        assert pack is not None

        notified = asyncio.run(
            emit_question_requested(harness.manager, mission, pack)
        )

        # Exactly ONE emission to the mission watcher, deduped.
        assert notified == 1
        calls = _enqueues_for(harness, watcher)
        assert len(calls) == 1
        msg = calls[0].kwargs["message"]
        assert "[JOB_EVENT] Job" in msg
        assert "question requested ❓" in msg
        assert "Approach A or B?" in msg  # pack payload rides the body
        # The envelope names the watcher's OWN armed receipt (R1).
        assert calls[0].kwargs["source"] == (
            f"internal_agent:job_event:{r1_settled}:question_requested"
        )

        # The named receipt still resolves to the asker — the answer
        # route (POST /api/jobs/{work_id}/answer, job_answer) needs no
        # watcher row: the pack lives on the asker.
        record = harness.work_resolver.resolve_work(r1_settled)
        assert record is not None
        assert record.instance_id == mission

        # NON-CLAIMING: the mission_terminal row SURVIVES the QA fire —
        # it must still be there for the future mission-terminal event.
        remaining = harness.watcher_repo.get_watchers_for_job(r1_settled)
        assert len(remaining) == 1
        assert remaining[0].instance_id == watcher
        assert remaining[0].watch_events == ["mission_terminal"]

    def test_question_event_row_persisted(self, harness):
        """The durable EventBus trail still carries QUESTION_REQUESTED
        for the asker (lane 1 unchanged)."""
        mission, _r1, _r2, _watcher = _seed_incident(harness)
        pack = harness.qm.set_question_pack(mission, [{"text": "Q?"}])
        asyncio.run(emit_question_requested(harness.manager, mission, pack))
        kinds = [e.kind for e in harness.event_repo.get_by_instance(mission)]
        assert EventKind.QUESTION_REQUESTED.value in kinds

    def test_mission_terminal_only_row_receives_question_events_exempt(
        self, harness
    ):
        """QA events are EVENTS-FILTER-EXEMPT: a ``mission_terminal``-only
        row (standard_match=False pre-fix) receives the question event.
        Explicit variant of the incident with the row on the receipt the
        emission enumerates (R2), isolating the exemption from the
        spontaneous-receipt hole."""
        mission, r1_settled, r2_fresh, watcher = _seed_incident(harness)
        # Second row: same watcher ALSO armed on the fresh receipt.
        harness.watcher_repo.add_watch(r2_fresh, watcher, ["mission_terminal"])
        pack = harness.qm.set_question_pack(mission, [{"text": "Proceed?"}])

        notified = asyncio.run(
            emit_question_requested(harness.manager, mission, pack)
        )

        # DEDUPE: rows on MULTIPLE receipts → ONE emission (not two).
        assert notified == 1
        calls = _enqueues_for(harness, watcher)
        assert len(calls) == 1
        # Envelope names the watcher's highest-priority receipt — the
        # live asking-turn receipt R2 (mission candidate order).
        assert calls[0].kwargs["source"] == (
            f"internal_agent:job_event:{r2_fresh}:question_requested"
        )
        # Both rows survive (non-claiming).
        assert len(harness.watcher_repo.get_watchers_for_job(r1_settled)) == 1
        assert len(harness.watcher_repo.get_watchers_for_job(r2_fresh)) == 1

    def test_transport_only_row_receives_question_events_exempt(
        self, harness
    ):
        """Events-exempt applies to ANY subscription shape: a
        transport-only row (``completed``) on a settled mission receipt
        receives the question event too — a mission blocked awaiting a
        human answer is state every mission watcher needs."""
        mission, r1_settled, _r2, watcher = _seed_incident(harness)
        # Re-arm the SAME watcher row as transport-only.
        harness.watcher_repo.add_watch(r1_settled, watcher, ["completed"])
        pack = harness.qm.set_question_pack(mission, [{"text": "Q?"}])

        notified = asyncio.run(
            emit_question_requested(harness.manager, mission, pack)
        )
        assert notified == 1
        calls = _enqueues_for(harness, watcher)
        assert len(calls) == 1
        assert "question requested ❓" in calls[0].kwargs["message"]
        # Row survives for its own subscribed terminal event.
        assert len(harness.watcher_repo.get_watchers_for_job(r1_settled)) == 1


# =============================================================================
# Stuck-awaiting-answer (pause-time #1 + 1800s heartbeats) reach the watcher
# =============================================================================


class TestStuckAwaitingAnswerFanout:
    def test_stuck_awaiting_answer_reaches_mission_watcher_on_spontaneous_receipt(
        self, harness
    ):
        """Pause-time stuck emission #1 reaches the mission watcher even
        though the asking turn rides the spontaneous (unwatched)
        receipt."""
        mission, r1_settled, r2_fresh, watcher = _seed_incident(harness)
        harness.instance_repo.set_metadata(mission, "question_pack_id", "pack-1")

        emission_index, notified = asyncio.run(
            emit_stuck_awaiting_answer(
                harness.manager,
                mission,
                "pack-1",
                waiting_for_seconds=0,
                paused_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        assert emission_index == 1
        assert notified == 1
        calls = _enqueues_for(harness, watcher)
        assert len(calls) == 1
        assert "[JOB_EVENT] Job" in calls[0].kwargs["message"]
        assert "stuck awaiting answer ⏳" in calls[0].kwargs["message"]
        assert calls[0].kwargs["source"] == (
            f"internal_agent:job_event:{r1_settled}:stuck_awaiting_answer"
        )
        # Non-claiming: the mission_terminal row survives.
        assert len(harness.watcher_repo.get_watchers_for_job(r1_settled)) == 1

    def test_heartbeat_stuck_reaches_mission_watcher(self, harness):
        """The 1800s wedge-guard heartbeat (emission #2) reaches the
        mission watcher through the HeartbeatEmitStuckProcessor — the
        heartbeat emission rides the same mission-scoped fan-out."""
        mission, r1_settled, _r2, watcher = _seed_incident(harness)
        # Wedge shape: asker PAUSED with the awaiting_answer handle.
        with Session(harness.engine) as session:
            session.execute(
                text("UPDATE instances SET status='paused' WHERE instance_id=:i"),
                {"i": mission},
            )
            session.commit()
        handle_task = _seed_task(
            harness.engine,
            mission,
            status=TaskStatus.PAUSED.value,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        with Session(harness.engine) as session:
            session.execute(
                text(
                    "UPDATE task SET suspension_reason='awaiting_answer', "
                    "resume_target_turn_id=:w WHERE work_id=:w"
                ),
                {"w": handle_task},
            )
            session.commit()
        pack = harness.qm.set_question_pack(mission, [{"text": "Q?"}])
        harness.instance_repo.set_metadata(mission, "question_pack_id", pack.id)
        # One prior stuck event → this heartbeat is emission #2.
        harness.event_repo.create_event(
            instance_id=mission,
            kind=EventKind.STUCK_AWAITING_ANSWER.value,
            data={"question_pack_id": pack.id, "emission_index": 1},
        )
        harness.task_repo.create_one_shot_heartbeat(
            TaskType.HEARTBEAT_EMIT_STUCK.value,
            mission,
            datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        processor = HeartbeatEmitStuckProcessor(
            harness.manager, harness.task_repo, harness.event_repo
        )
        task = harness.task_repo.claim_pending_task("w1")
        assert task is not None

        result = asyncio.run(processor.process(task))

        assert result["emission"] == "stuck_awaiting_answer"
        calls = _enqueues_for(harness, watcher)
        assert len(calls) == 1
        assert "stuck awaiting answer ⏳" in calls[0].kwargs["message"]
        # Not escalated yet: asker survives, successor re-armed.
        harness.manager.terminate_instance.assert_not_awaited()


# =============================================================================
# Escalation (≥3 heartbeats) — mission watcher in ADDITION to FE SSE
# =============================================================================


class TestEscalationReachesMissionWatcher:
    def test_escalation_envelope_reaches_mission_watcher(self, harness):
        """At escalation (emission_index ≥ 3) the mission watcher
        receives the escalation [JOB_EVENT] in addition to the FE SSE
        broadcast — even though the asker has just been terminated."""
        mission, r1_settled, _r2, watcher = _seed_incident(harness)

        asyncio.run(
            emit_question_escalation_notification(
                harness.manager,
                mission,
                "leader",
                "pack-1",
                3,
            )
        )

        calls = _enqueues_for(harness, watcher)
        assert len(calls) == 1
        msg = calls[0].kwargs["message"]
        assert "[JOB_EVENT] Job" in msg
        assert "question escalation ⏳" in msg
        assert calls[0].kwargs["source"] == (
            f"internal_agent:job_event:{r1_settled}:question_escalation"
        )
        # FE SSE lane still fired (IN ADDITION, not instead).
        harness.manager._notification_broadcaster.emit_question_escalation.assert_awaited_once()
        # Non-claiming: the mission_terminal row survives.
        assert len(harness.watcher_repo.get_watchers_for_job(r1_settled)) == 1

    def test_full_wedge_chain_escalation_delivers_stuck_and_escalation(
        self, harness
    ):
        """End-to-end wedge chain on the incident geometry: heartbeat #3
        escalates → the watcher receives the escalated stuck emission
        (fires BEFORE the terminate flip) AND the escalation envelope,
        and both rows/lanes stay intact."""
        mission, r1_settled, _r2, watcher = _seed_incident(harness)
        with Session(harness.engine) as session:
            session.execute(
                text("UPDATE instances SET status='paused' WHERE instance_id=:i"),
                {"i": mission},
            )
            session.commit()
        handle_task = _seed_task(
            harness.engine,
            mission,
            status=TaskStatus.PAUSED.value,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        with Session(harness.engine) as session:
            session.execute(
                text(
                    "UPDATE task SET suspension_reason='awaiting_answer', "
                    "resume_target_turn_id=:w WHERE work_id=:w"
                ),
                {"w": handle_task},
            )
            session.commit()
        pack = harness.qm.set_question_pack(mission, [{"text": "Q?"}])
        harness.instance_repo.set_metadata(mission, "question_pack_id", pack.id)
        # Two prior stuck events → this heartbeat is emission #3 (escalation).
        for idx in (1, 2):
            harness.event_repo.create_event(
                instance_id=mission,
                kind=EventKind.STUCK_AWAITING_ANSWER.value,
                data={"question_pack_id": pack.id, "emission_index": idx},
            )
        harness.task_repo.create_one_shot_heartbeat(
            TaskType.HEARTBEAT_EMIT_STUCK.value,
            mission,
            datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        processor = HeartbeatEmitStuckProcessor(
            harness.manager, harness.task_repo, harness.event_repo
        )
        task = harness.task_repo.claim_pending_task("w1")
        assert task is not None

        result = asyncio.run(processor.process(task))

        assert result["emission"] == "escalated"
        harness.manager.terminate_instance.assert_awaited_once_with(
            mission, terminal_reason="wedge_guard_terminated"
        )
        harness.manager._notification_broadcaster.emit_question_escalation.assert_awaited_once()
        calls = _enqueues_for(harness, watcher)
        assert len(calls) == 2  # stuck emission + escalation envelope
        bodies = [c.kwargs["message"] for c in calls]
        assert any("stuck awaiting answer ⏳" in b for b in bodies)
        assert any("question escalation ⏳" in b for b in bodies)
        # The mission_terminal row SURVIVES the escalation deliveries.
        assert len(harness.watcher_repo.get_watchers_for_job(r1_settled)) == 1


# =============================================================================
# Dedupe + scope correctness
# =============================================================================


class TestDedupeAndScope:
    def test_unrelated_watcher_not_notified(self, harness):
        """Scope is the ASKING mission: a watcher holding rows only on
        an unrelated instance's receipt receives NOTHING."""
        mission, _r1, _r2, _watcher = _seed_incident(harness)
        outsider_owner = _seed_instance(harness.engine)
        unrelated = _seed_task(harness.engine, outsider_owner)
        outsider = _seed_instance(harness.engine, status=InstanceStatus.IDLE.value)
        harness.watcher_repo.add_watch(unrelated, outsider, ["mission_terminal"])
        pack = harness.qm.set_question_pack(mission, [{"text": "Q?"}])

        asyncio.run(emit_question_requested(harness.manager, mission, pack))

        assert _enqueues_for(harness, outsider) == []

    def test_question_from_descendant_reaches_mission_scoped_watcher(
        self, harness
    ):
        """The asker is a DESCENDANT (child in the mission tree): the
        mission scope resolves through ``get_tree_root_id`` — the
        watcher on the ROOT mission's receipt receives the question."""
        root_mission = _seed_instance(harness.engine)
        r1 = _seed_task(
            harness.engine,
            root_mission,
            status=TaskStatus.COMPLETED.value,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=15),
        )
        watcher = _seed_instance(harness.engine, status=InstanceStatus.IDLE.value)
        harness.watcher_repo.add_watch(r1, watcher, ["mission_terminal"])
        child_asker = _seed_instance(
            harness.engine, parent_id=root_mission
        )
        pack = harness.qm.set_question_pack(child_asker, [{"text": "Q?"}])

        notified = asyncio.run(
            emit_question_requested(harness.manager, child_asker, pack)
        )

        assert notified == 1
        calls = _enqueues_for(harness, watcher)
        assert len(calls) == 1
        assert "question requested ❓" in calls[0].kwargs["message"]
        assert calls[0].kwargs["source"] == (
            f"internal_agent:job_event:{r1}:question_requested"
        )


# =============================================================================
# Bounded lookup pins
# =============================================================================


class TestBoundedLookup:
    def test_mission_receipt_scan_is_capped_and_newest_first(self, harness):
        """The emission-time receipt scan is BOUNDED: a mission with
        more Task rows than the scan cap still resolves (no unbounded
        scan), returning at most the cap, newest-first."""
        from daemon.services.midflight_qa import (
            MISSION_RECEIPT_SCAN_CAP,
            enumerate_mission_work_ids,
        )

        mission = _seed_instance(harness.engine)
        now = datetime.now(timezone.utc)
        newest_work_id = None
        for i in range(MISSION_RECEIPT_SCAN_CAP + 20):
            newest_work_id = _seed_task(
                harness.engine,
                mission,
                created_at=now - timedelta(minutes=MISSION_RECEIPT_SCAN_CAP + 20 - i),
            )

        ids = enumerate_mission_work_ids(harness.manager, mission)

        assert len(ids) == MISSION_RECEIPT_SCAN_CAP
        assert ids[0] == newest_work_id  # newest first
        assert len(set(ids)) == len(ids)  # deduped

    def test_qa_watchers_multi_receipt_select_is_capped(self, harness):
        """The watcher IN-select carries a defensive LIMIT: a watcher
        population larger than the cap cannot explode the emission."""
        from daemon.services.work_notifier import _QA_WATCHER_ROW_CAP

        mission = _seed_instance(harness.engine)
        r1 = _seed_task(harness.engine, mission)
        r2 = _seed_task(harness.engine, mission)
        # Cap + extra watchers, all armed on the same two receipts.
        seeded = []
        for _ in range(_QA_WATCHER_ROW_CAP + 10):
            w = _seed_instance(harness.engine, status=InstanceStatus.IDLE.value)
            harness.watcher_repo.add_watch(r1, w, ["mission_terminal"])
            seeded.append(w)

        from daemon.services.work_notifier import notify_mission_qa_watchers

        pack_text = "Q?"
        notified = asyncio.run(
            notify_mission_qa_watchers(
                work_id=r2,
                status="question_requested",
                mission_work_ids=[r2, r1],
                instance_manager=harness.manager,
                work_resolver=harness.work_resolver,
                watcher_repo=harness.watcher_repo,
                result_summary=pack_text,
            )
        )

        assert notified == _QA_WATCHER_ROW_CAP


# =============================================================================
# 36be8aef-family guard — mission_terminal semantics untouched
# =============================================================================


class TestMissionTerminalUntouched:
    def test_mission_terminal_only_row_still_held_on_transport_event(
        self, harness
    ):
        """The QA exemption must NOT leak into ``notify_work_watchers``:
        a ``mission_terminal``-only row with the mission still live stays
        HELD (skipped) on a transport-kind fire — held_for_mission
        semantics preserved verbatim (36be8aef family)."""
        mission, r1_settled, _r2, watcher = _seed_incident(harness)

        notified = asyncio.run(
            notify_work_watchers(
                work_id=r1_settled,
                status="question_requested",
                instance_manager=harness.manager,
                work_resolver=harness.work_resolver,
                watcher_repo=harness.watcher_repo,
                result_summary="x",
            )
        )

        # notify_work_watchers is receipt-keyed and events-filtered:
        # the mission_terminal-only row does NOT match the status and is
        # held for the future mission-terminal fire.
        assert notified == 0
        assert len(harness.watcher_repo.get_watchers_for_job(r1_settled)) == 1

    def test_question_requested_never_claims_rows(self, harness):
        """QA delivery through the mission-scoped path is NON-CLAIMING
        for every recipient row shape — no CAS, no row transitions."""
        mission, r1_settled, r2_fresh, watcher = _seed_incident(harness)
        harness.watcher_repo.add_watch(r2_fresh, watcher, None)  # default events
        pack = harness.qm.set_question_pack(mission, [{"text": "Q?"}])

        asyncio.run(emit_question_requested(harness.manager, mission, pack))

        rows_r1 = harness.watcher_repo.get_watchers_for_job(r1_settled)
        rows_r2 = harness.watcher_repo.get_watchers_for_job(r2_fresh)
        assert len(rows_r1) == 1 and len(rows_r2) == 1
        # And the rows were never mutated (watch_events intact).
        assert rows_r1[0].watch_events == ["mission_terminal"]
        assert rows_r2[0].watch_events == JobWatcher(
            job_id=r2_fresh, instance_id=watcher
        ).watch_events

    def test_mission_scoped_path_refuses_terminal_status(self, harness):
        """Fail-closed: the mission-scoped QA lane refuses non-QA
        statuses — ``mission_terminal`` (and any transport kind) can
        never ride it, so the C1/N1 terminal machinery is unreachable
        from the new path."""
        from daemon.services.work_notifier import notify_mission_qa_watchers

        mission, r1_settled, _r2, watcher = _seed_incident(harness)

        for status in ("mission_terminal", "completed", "settled"):
            notified = asyncio.run(
                notify_mission_qa_watchers(
                    work_id=r1_settled,
                    status=status,
                    mission_work_ids=[r1_settled],
                    instance_manager=harness.manager,
                    work_resolver=harness.work_resolver,
                    watcher_repo=harness.watcher_repo,
                )
            )
            assert notified == 0
        harness.manager.enqueue_message.assert_not_awaited()
        assert len(harness.watcher_repo.get_watchers_for_job(r1_settled)) == 1


# =============================================================================
# One reconcile-mint path (manager.py mint sites, RCA)
# =============================================================================


class TestReconcileMintPath:
    def test_reconcile_minted_receipt_question_reaches_watcher(self, harness):
        """manager.py reconcile mint (sub-shape b — message + task
        recreated for the parent): the minted receipt has no coverage,
        the parent asks on the next turn, the mission watcher still
        receives the question."""
        mission, r1_settled, _r2, watcher = _seed_incident(harness)
        # Reconcile-mint shape: PROCESS_REPORT Task row WITH the
        # recreated message_id (manager.py mint sites).
        minted = _seed_task(
            harness.engine,
            mission,
            status=TaskStatus.PENDING.value,
            task_type=TaskType.PROCESS_REPORT.value,
            message_id=f"msg-{uuid.uuid4()}",
            created_at=datetime.now(timezone.utc) + timedelta(seconds=5),
        )
        pack = harness.qm.set_question_pack(mission, [{"text": "Q?"}])

        notified = asyncio.run(
            emit_question_requested(harness.manager, mission, pack)
        )

        assert notified == 1
        calls = _enqueues_for(harness, watcher)
        assert len(calls) == 1
        assert "question requested ❓" in calls[0].kwargs["message"]
        # Envelope names the watcher's armed receipt R1 (its only row).
        assert calls[0].kwargs["source"] == (
            f"internal_agent:job_event:{r1_settled}:question_requested"
        )
