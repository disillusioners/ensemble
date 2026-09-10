"""RESUME_ROUTER duplicate-report regression coverage (2026-09-10).

Production incident ca14e233/c3ac30f7: after an answer-gate pause/
resume interrupted the parent's report-consuming turn, the sweep's
Lane 2 (``no_row_backstop``, reason=``RESUME_ROUTER``) re-selected the
already-delivered child as a "no delivery" candidate EVERY ~300s pass
— once per NON-report completed message on the child (grandchild
reports, the initial dispatch) — and re-delivered the child's terminal
report byte-identically with a fresh ``report_message_id`` each time
(4 copies in prod).

Why every existing guard was bypassed (pinned by these tests):

1. The Lane-2 candidate query keyed delivery evidence PER ANCHOR
   (exact ``internal_report:{child}:{anchor}`` equality + a
   PENDING/DEFERRED-only injection join) — but the true terminal
   anchor is the child's checkpoint message, NOT a child
   ``message_queue`` row, so the delivered evidence was invisible and
   every other completed child message became a fresh wrong-anchor
   obligation.
2. ``idempotency_skip`` (``child_reports.py``) keys on
   ``internal_report:{child}:{completed_message_id}`` — a different
   anchor per marker never matches.
3. The obligation-triple partial unique index only gates
   PENDING/DEFERRED — fresh deliverable rows stay legal after
   TASK_DELIVERED, and the fresh ``report_message_id`` defeated FE
   id-dedup.

Fix semantics pinned here:

* 5a (trigger repro): a child whose terminal report WAS delivered is
  NOT a Lane-2 candidate and produces NO markers / NO re-deliveries
  — per-child delivery evidence (parent-side
  ``internal_report:{child}:%`` prefix + ANY-state injection row).
* 5b (true recovery): a genuinely-lost report (consumer died
  pre-injection — zero evidence rows) is recovered EXACTLY ONCE,
  with ONE anchor per child even when the child has multiple
  completed messages, and the second sweep pass does not re-hit.
* 5c (clean path): normal completion → exactly ONE delivery, and the
  sweep never adds a second.
* Defense-in-depth content-identity dedup: a delivery claim whose
  (parent, child, content) matches an already-terminal row for the
  same child is a DUPLICATE obligation — dead-lettered (FAILED),
  never re-delivered, regardless of anchor id.

Engine: file-backed SQLite at ``tmp_path`` with NullPool + WAL +
busy_timeout=10000 (repo conventions; never StaticPool+WriteGuard —
QUARANTINE).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, update as sa_update
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select as sm_select

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.constants import DEFERRED_REASON_RESUME_ROUTER
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.services.report_delivery_recovery import (
    ReportDeliveryRecoveryService,
)


# ─── Fixtures + helpers ──────────────────────────────────────────────────────

_REPO_LOGGER = "daemon.repositories.report_injection.repository"


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout)."""
    db_path = tmp_path / "resume-router-dup-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repo(engine) -> ReportInjectionRepository:
    return ReportInjectionRepository(engine=engine)


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str,
    parent_id: str | None,
    status: str,
    agent_name: str,
) -> str:
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id=f"agent-{agent_name}",
                agent_name=agent_name,
                agent_dir=f"/tmp/{agent_name}",
                parent_id=parent_id,
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _seed_child_message(
    engine: Engine,
    *,
    instance_id: str,
    message_id: str,
    source: str = "agent",
    msg_type: str = MessageType.AGENT.value,
    status: str = MessageStatus.COMPLETED.value,
    enqueued_at: datetime | None = None,
) -> str:
    """A CHILD-side message_queue row (dispatch / injected grandchild
    report / completed answer)."""
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content=f"content of {message_id}",
                source=source,
                type=msg_type,
                status=status,
                priority=0,
                enqueued_at=enqueued_at or datetime.now(timezone.utc),
            )
        )
        session.commit()
    return message_id


def _seed_parent_report_row(
    engine: Engine,
    *,
    parent_id: str,
    child_id: str,
    anchor: str,
    status: str = MessageStatus.COMPLETED.value,
    content: str = "report body",
    report_msg: str | None = None,
) -> str:
    """The PARENT-side ``internal_report:{child}:{anchor}`` completion
    row — the durable delivery evidence (any status)."""
    rid = report_msg or f"rmsg-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=rid,
                instance_id=parent_id,
                content=content,
                source=f"internal_report:{child_id}:{anchor}",
                type=MessageType.COMPLETION_REPORT.value,
                status=status,
                priority=0,
                enqueued_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    return rid


def _seed_delivered_pair(
    repo: ReportInjectionRepository,
    engine: Engine,
    *,
    parent_id: str,
    child_id: str,
    anchor: str,
    content: str = "report body",
) -> ReportInjection:
    """Natural-path delivery, terminal: PENDING enqueue (with artifact)
    + companion parent-side message row, then a delivery claim."""
    rid = _seed_parent_report_row(
        engine,
        parent_id=parent_id,
        child_id=child_id,
        anchor=anchor,
        status=MessageStatus.READY.value,
        content=content,
    )
    row = repo.enqueue(
        parent_instance_id=parent_id,
        child_instance_id=child_id,
        child_message_id=anchor,
        report_message_id=rid,
        content=content,
    )
    # Deliver via the fallback task path → TASK_DELIVERED (the natural
    # consuming turn's outcome in the incident shape).
    claim = repo.claim_for_task_delivery(rid)
    assert claim.status == "claimed"
    with Session(engine) as session:
        row = session.exec(
            sm_select(ReportInjection).where(
                ReportInjection.injection_id == row.injection_id
            )
        ).one()
    return row


def _all_injection_rows(engine: Engine) -> list[ReportInjection]:
    with Session(engine) as session:
        return list(
            session.exec(sm_select(ReportInjection)).all()
        )


def _row_state(engine: Engine, injection_id: str) -> str:
    """Re-query a row's state (rows returned by ``enqueue`` are
    detached — never ``refresh`` them in a fresh session)."""
    with Session(engine) as session:
        return (
            session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.injection_id == injection_id
                )
            )
            .one()
            .state
        )


def _parent_report_rows(engine: Engine, parent_id: str) -> list[MessageQueue]:
    with Session(engine) as session:
        return list(
            session.exec(
                sm_select(MessageQueue)
                .where(MessageQueue.instance_id == parent_id)
                .where(MessageQueue.type == MessageType.COMPLETION_REPORT.value)
            ).all()
        )


def _stamp_recovery_attempted_at(
    engine: Engine, injection_id: str
) -> None:
    """Mark a terminal ``ReportInjection`` row as sweep-recovered.

    Models the stamp side-effect of
    :meth:`ReportInjectionRepository.transition_deferred_to_pending`
    (the ONLY writer of ``recovery_attempted_at`` in production). Used
    by the dedup tests to pin the intended dedup class — sweep-
    recovered wrong-anchor rows — without invoking the full DEFERRED→
    PENDING round-trip. Fresh natural enqueues (``enqueue`` / the
    inline path in
    ``child_reports._process_child_completion_db_sync``) MUST leave
    the column NULL.
    """
    with Session(engine) as session:
        session.execute(
            sa_update(ReportInjection)
            .where(ReportInjection.injection_id == injection_id)
            .values(recovery_attempted_at=datetime.now(timezone.utc).isoformat())
        )
        session.commit()


def _injection_id_for_report(engine: Engine, report_message_id: str) -> str:
    """Return the ``injection_id`` of the row keyed by ``report_message_id``.

    The drain (``claim_for_injection``) returns ``content`` /
    ``report_message_id`` / ``created_at`` / ``child_instance_id`` —
    not ``injection_id`` — so the dedup tests look the row up
    directly when they need to stamp ``recovery_attempted_at``.
    """
    with Session(engine) as session:
        return (
            session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.report_message_id == report_message_id
                )
            )
            .one()
            .injection_id
        )


def _build_service(
    engine: Engine,
    *,
    busy_ids: set[str] | None = None,
) -> tuple[ReportDeliveryRecoveryService, MagicMock]:
    """Real sweep service + mock manager (per-row calls asserted)."""
    ri_repo = ReportInjectionRepository(engine=engine)
    task_repo = MagicMock()
    task_repo.has_instance_busy = MagicMock(
        side_effect=lambda instance_id: instance_id in (busy_ids or set())
    )
    manager = MagicMock()
    manager.engine = engine
    manager._handle_recover_deferred_report = MagicMock()

    service = ReportDeliveryRecoveryService(
        task_repo=task_repo,
        report_injection_repo=ri_repo,
        queue_repo=MagicMock(),
        instance_repo=MagicMock(),
        manager_ref=manager,
        interval_seconds=300,
        age_bound_minutes=10,
        batch_cap=100,
        recovery_retry_minutes=1,
        enabled=True,
        lane_orphan=False,
    )
    return service, manager


# ─── 5a. Exact trigger repro: delivered child is NOT re-selected ────────────


class TestDeliveredChildNotRecandidate:
    """The ca14e233/c3ac30f7 trigger shape: report delivered →
    consuming turn cancelled by answer-gate pause → resume. The child
    is COMPLETED, the parent non-terminal, and the child carries other
    completed non-report messages. The sweep must recognize the
    terminal report as already delivered — ZERO candidates, ZERO new
    markers, ZERO re-deliveries."""

    @pytest.fixture
    def delivered_child(self, repo, engine):
        """Child with an initial dispatch + an injected grandchild
        report; the terminal report was delivered keyed to the TRUE
        terminal anchor (the child's checkpoint message id — never a
        child message_queue row)."""
        parent_id = f"leader-{uuid.uuid4().hex[:8]}"
        child_id = f"wanderer-{uuid.uuid4().hex[:8]}"
        grandchild_id = f"giter-{uuid.uuid4().hex[:8]}"
        t0 = datetime.now(timezone.utc)
        _seed_instance(
            engine, instance_id=parent_id, parent_id=None,
            status=InstanceStatus.WAITING_CHILDREN.value,
            agent_name="leader",
        )
        _seed_instance(
            engine, instance_id=child_id, parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value, agent_name="wanderer",
        )
        # The child's own completed non-report messages (the wrong
        # anchors the pre-fix query walked): initial dispatch + the
        # grandchild report injected INTO the child.
        dispatch_id = _seed_child_message(
            engine, instance_id=child_id,
            message_id=f"msg-dispatch-{uuid.uuid4().hex[:8]}",
            source=f"user:{parent_id}", enqueued_at=t0,
        )
        grandchild_msg = _seed_child_message(
            engine, instance_id=child_id,
            message_id=f"msg-grandchild-{uuid.uuid4().hex[:8]}",
            source=f"internal_report:{grandchild_id}:gmsg",
            msg_type=MessageType.COMPLETION_REPORT.value,
            enqueued_at=t0 + timedelta(seconds=5),
        )
        # TRUE terminal anchor: the child's final checkpoint message —
        # NOT a message_queue row of the child.
        true_terminal = f"msg-final-{uuid.uuid4().hex[:8]}"
        _seed_delivered_pair(
            repo, engine,
            parent_id=parent_id, child_id=child_id,
            anchor=true_terminal, content="the final report",
        )
        return {
            "parent_id": parent_id,
            "child_id": child_id,
            "dispatch_id": dispatch_id,
            "grandchild_msg": grandchild_msg,
            "true_terminal": true_terminal,
        }

    def test_query_returns_no_candidates_for_delivered_child(
        self, repo, engine, delivered_child
    ):
        """REPO-LEVEL 5a: the delivered child must not be a Lane-2
        candidate — per-child evidence, not per-anchor."""
        rows = repo.find_completed_children_without_delivery(
            parent_not_terminal=True,
            limit=100,
        )
        assert rows == [], (
            "a child whose terminal report was already delivered must "
            f"NOT be a Lane-2 candidate; got {rows}"
        )

    def test_lane_produces_no_markers_and_no_redelivery(
        self, engine, delivered_child
    ):
        """SERVICE-LEVEL 5a: a full Lane-2 pass over the delivered
        child writes NO new marker rows and re-enters recovery ZERO
        times. Pre-fix this delivered one duplicate per sweep pass."""
        service, manager = _build_service(engine)
        lane = service._run_no_row_backstop_lane()

        assert lane.recovered == 0, (
            "no duplicate obligation may be recovered for an "
            "already-delivered child"
        )
        assert lane.errors == 0
        assert manager._handle_recover_deferred_report.call_count == 0, (
            "re-entry must never fire for an already-delivered child"
        )
        rows = _all_injection_rows(engine)
        assert len(rows) == 1, (
            "exactly the pre-existing terminal delivery row must exist "
            f"— no new markers; got {len(rows)}"
        )
        assert rows[0].state == ReportInjectionState.TASK_DELIVERED.value
        # No new parent-side completion_report rows either.
        assert len(_parent_report_rows(engine, delivered_child["parent_id"])) == 1


# ─── 5b. True recovery: lost report recovered EXACTLY ONCE ──────────────────


class TestLostReportRecoveredExactlyOnce:
    """A genuinely-lost report (consuming turn died BEFORE the report
    was injected/consumed — zero evidence rows) must still be
    recovered, exactly once, even when the child has multiple
    completed messages (ONE anchor per child — no fan-out)."""

    @pytest.fixture
    def lost_child(self, repo, engine):
        parent_id = f"leader-{uuid.uuid4().hex[:8]}"
        child_id = f"wanderer-{uuid.uuid4().hex[:8]}"
        grandchild_id = f"giter-{uuid.uuid4().hex[:8]}"
        t0 = datetime.now(timezone.utc)
        _seed_instance(
            engine, instance_id=parent_id, parent_id=None,
            status=InstanceStatus.WAITING_CHILDREN.value,
            agent_name="leader",
        )
        _seed_instance(
            engine, instance_id=child_id, parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value, agent_name="wanderer",
        )
        dispatch_id = _seed_child_message(
            engine, instance_id=child_id,
            message_id=f"msg-dispatch-{uuid.uuid4().hex[:8]}",
            source=f"user:{parent_id}", enqueued_at=t0,
        )
        grandchild_msg = _seed_child_message(
            engine, instance_id=child_id,
            message_id=f"msg-grandchild-{uuid.uuid4().hex[:8]}",
            source=f"internal_report:{grandchild_id}:gmsg",
            msg_type=MessageType.COMPLETION_REPORT.value,
            enqueued_at=t0 + timedelta(seconds=5),
        )
        return {
            "parent_id": parent_id,
            "child_id": child_id,
            "dispatch_id": dispatch_id,
            "grandchild_msg": grandchild_msg,
        }

    def test_lost_child_is_candidate_with_single_anchor(
        self, repo, engine, lost_child
    ):
        """The zero-evidence child IS a candidate — exactly ONE row,
        anchored to its latest completed message."""
        rows = repo.find_completed_children_without_delivery(
            parent_not_terminal=True,
            limit=100,
        )
        assert len(rows) == 1, (
            f"one child = at most one obligation row; got {rows}"
        )
        assert rows[0]["child_id"] == lost_child["child_id"]
        assert rows[0]["parent_id"] == lost_child["parent_id"]
        # Deterministic anchor: the LATEST completed child message
        # (the grandchild report, enqueued 5s after the dispatch).
        assert rows[0]["child_msg_id"] == lost_child["grandchild_msg"]

    def test_recovery_is_exactly_once_across_sweep_passes(
        self, engine, lost_child
    ):
        """SERVICE-LEVEL 5b: pass 1 recovers exactly once (one marker,
        one re-entry); pass 2 does not re-hit."""
        service, manager = _build_service(engine)

        lane1 = service._run_no_row_backstop_lane()
        assert lane1.recovered == 1, (
            f"the lost report must be recovered exactly once per "
            f"child; got recovered={lane1.recovered}"
        )
        assert lane1.errors == 0
        assert manager._handle_recover_deferred_report.call_count == 1
        kwargs = manager._handle_recover_deferred_report.call_args.kwargs
        assert kwargs["child_instance_id"] == lost_child["child_id"]
        assert kwargs["child_message_id"] == lost_child["grandchild_msg"]

        rows = _all_injection_rows(engine)
        assert len(rows) == 1, (
            f"exactly ONE obligation row for one child; got {len(rows)}"
        )
        assert rows[0].deferred_reason == DEFERRED_REASON_RESUME_ROUTER
        assert rows[0].state == ReportInjectionState.PENDING.value

        # ── Pass 2 (the ~300s re-hit): the non-terminal marker row
        # excludes the child — no flap, no second obligation.
        candidates2 = (
            service._report_injection_repo
            .find_completed_children_without_delivery(
                parent_not_terminal=True,
                limit=100,
            )
        )
        assert candidates2 == []
        lane2 = service._run_no_row_backstop_lane()
        assert lane2.recovered == 0
        assert len(_all_injection_rows(engine)) == 1


# ─── 5c. Clean path: normal completion → exactly one delivery ───────────────


class TestCleanPathSingleDelivery:
    """No regression on the natural path: a normally-completing child
    delivers exactly once, and the sweep never adds a second."""

    def test_normal_completion_single_delivery_then_sweep_silent(
        self, repo, engine
    ):
        parent_id = f"leader-{uuid.uuid4().hex[:8]}"
        child_id = f"giter-{uuid.uuid4().hex[:8]}"
        anchor = f"msg-final-{uuid.uuid4().hex[:8]}"
        _seed_instance(
            engine, instance_id=parent_id, parent_id=None,
            status=InstanceStatus.WAITING_CHILDREN.value,
            agent_name="leader",
        )
        _seed_instance(
            engine, instance_id=child_id, parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value, agent_name="giter",
        )
        _seed_child_message(
            engine, instance_id=child_id,
            message_id=anchor, source=f"user:{parent_id}",
        )

        # Natural completion: PENDING enqueue with artifact + companion
        # parent-side row (READY), then the live drain delivers it.
        rid = _seed_parent_report_row(
            engine, parent_id=parent_id, child_id=child_id,
            anchor=anchor, status=MessageStatus.READY.value,
            content="clean report",
        )
        repo.enqueue(
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=anchor,
            report_message_id=rid,
            content="clean report",
        )

        drained = repo.claim_for_injection(parent_id)
        assert len(drained) == 1, "exactly one delivery on the clean path"
        # Idempotent: the second drain (next LLM call) gets nothing.
        assert repo.claim_for_injection(parent_id) == []

        # The delivered child is no longer a Lane-2 candidate, and a
        # full sweep pass writes nothing new.
        assert repo.find_completed_children_without_delivery(
            parent_not_terminal=True, limit=100
        ) == []
        service, manager = _build_service(engine)
        lane = service._run_no_row_backstop_lane()
        assert lane.recovered == 0
        assert manager._handle_recover_deferred_report.call_count == 0
        assert len(_all_injection_rows(engine)) == 1


# ─── Defense-in-depth: content-identity dedup at the claim seams ────────────


class TestContentIdentityDedup:
    """A delivery claim whose (parent, child, content) matches an
    already-terminal row for the same child is a DUPLICATE obligation:
    dead-lettered, never re-delivered — regardless of anchor id. This
    is the belt to 5a's suspenders: even a legacy wrong-anchor marker
    that reaches PENDING cannot produce a byte-identical re-delivery."""

    def test_drain_dead_letters_duplicate_pending_row(
        self, repo, engine
    ):
        parent_id = f"leader-{uuid.uuid4().hex[:8]}"
        child_id = f"wanderer-{uuid.uuid4().hex[:8]}"
        other_child = f"giter-{uuid.uuid4().hex[:8]}"
        _seed_instance(
            engine, instance_id=parent_id, parent_id=None,
            status=InstanceStatus.WAITING_CHILDREN.value,
            agent_name="leader",
        )
        _seed_instance(
            engine, instance_id=child_id, parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value, agent_name="wanderer",
        )
        _seed_instance(
            engine, instance_id=other_child, parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value, agent_name="giter",
        )

        # Already-delivered (TASK_DELIVERED) under the true anchor —
        # NATURAL delivery (``recovery_attempted_at IS NULL`` on the
        # twin, the original turn-1 shape). The wrong-anchor sweep-
        # recovered DUP (the only stamping-eligible class per
        # council 🟠 round 2) is stamped on the dup row. See the
        # council-block comment at the drain seam for the full
        # re-verified premise.
        _seed_delivered_pair(
            repo, engine, parent_id=parent_id, child_id=child_id,
            anchor=f"msg-final-{uuid.uuid4().hex[:8]}",
            content="identical terminal report",
        )
        # A duplicate PENDING obligation: WRONG anchor, fresh
        # report_message_id, SAME content (the wrong-anchor artifact).
        # Stamp ``recovery_attempted_at`` on the DUP row to model the
        # sweep-recovery class — the dedup MUST match stamped dups.
        dup_rid = _seed_parent_report_row(
            engine, parent_id=parent_id, child_id=child_id,
            anchor=f"msg-dispatch-{uuid.uuid4().hex[:8]}",
            status=MessageStatus.READY.value,
            content="identical terminal report",
        )
        dup_row = repo.enqueue(
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=f"msg-dispatch-{uuid.uuid4().hex[:8]}",
            report_message_id=dup_rid,
            content="identical terminal report",
        )
        _stamp_recovery_attempted_at(engine, dup_row.injection_id)
        # A legitimate NEW report from a DIFFERENT child must still
        # drain (dedup is scoped per (parent, child), never global).
        new_rid = _seed_parent_report_row(
            engine, parent_id=parent_id, child_id=other_child,
            anchor=f"msg-final-{uuid.uuid4().hex[:8]}",
            status=MessageStatus.READY.value,
            content="a different child's report",
        )
        new_row = repo.enqueue(
            parent_instance_id=parent_id,
            child_instance_id=other_child,
            child_message_id=f"msg-final-{uuid.uuid4().hex[:8]}",
            report_message_id=new_rid,
            content="a different child's report",
        )

        drained = repo.claim_for_injection(parent_id)
        assert len(drained) == 1, (
            "only the legit row drains — the content-duplicate must "
            "not be re-delivered"
        )
        assert drained[0]["content"] == "a different child's report"
        assert drained[0]["report_message_id"] == new_rid

        assert _row_state(engine, dup_row.injection_id) == (
            ReportInjectionState.FAILED.value
        ), (
            "the duplicate obligation is dead-lettered (abandoned, "
            "not delivered)"
        )
        assert _row_state(engine, new_row.injection_id) == (
            ReportInjectionState.INJECTED.value
        )
        # The duplicate's companion READY row is completed (same
        # hygiene as a real claim) — no lingering READY report row.
        with Session(engine) as session:
            dup_msg = session.exec(
                sm_select(MessageQueue).where(
                    MessageQueue.message_id == dup_rid
                )
            ).one()
        assert dup_msg.status == MessageStatus.COMPLETED.value

    def test_task_claim_dead_letters_content_duplicate(
        self, repo, engine
    ):
        parent_id = f"leader-{uuid.uuid4().hex[:8]}"
        child_id = f"reviewer-{uuid.uuid4().hex[:8]}"
        _seed_instance(
            engine, instance_id=parent_id, parent_id=None,
            status=InstanceStatus.WAITING_CHILDREN.value,
            agent_name="leader",
        )
        _seed_instance(
            engine, instance_id=child_id, parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value, agent_name="reviewer",
        )
        # Delivered via the live drain (INJECTED terminal twin) —
        # NATURAL delivery (``recovery_attempted_at IS NULL`` on the
        # twin, the original turn-1 shape). The wrong-anchor
        # sweep-recovered DUP is stamped on the dup row. See the
        # council-block comment at the task seam for the full
        # re-verified premise.
        true_rid = _seed_parent_report_row(
            engine, parent_id=parent_id, child_id=child_id,
            anchor=f"msg-final-{uuid.uuid4().hex[:8]}",
            status=MessageStatus.READY.value, content="the same report",
        )
        repo.enqueue(
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=f"msg-final-{uuid.uuid4().hex[:8]}",
            report_message_id=true_rid,
            content="the same report",
        )
        drained = repo.claim_for_injection(parent_id)
        assert len(drained) == 1

        # Duplicate PENDING obligation (wrong anchor, fresh id) —
        # stamp ``recovery_attempted_at`` on the DUP row to model the
        # sweep-recovery class (the only stamping-eligible class
        # per council 🟠 round 2).
        dup_rid = _seed_parent_report_row(
            engine, parent_id=parent_id, child_id=child_id,
            anchor=f"msg-grandchild-{uuid.uuid4().hex[:8]}",
            status=MessageStatus.READY.value, content="the same report",
        )
        dup_row = repo.enqueue(
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=f"msg-grandchild-{uuid.uuid4().hex[:8]}",
            report_message_id=dup_rid,
            content="the same report",
        )
        _stamp_recovery_attempted_at(engine, dup_row.injection_id)

        claim = repo.claim_for_task_delivery(dup_rid)
        assert claim.status == "already_delivered", (
            "the content-duplicate claim must resolve to the existing "
            "already_delivered tri-state (task skips)"
        )
        assert _row_state(engine, dup_row.injection_id) == (
            ReportInjectionState.FAILED.value
        )

    def test_task_claim_distinct_content_still_claims(
        self, repo, engine
    ):
        """Over-suppression guard: a PENDING row for the same
        (parent, child) with DIFFERENT content is a distinct
        obligation and claims normally."""
        parent_id = f"leader-{uuid.uuid4().hex[:8]}"
        child_id = f"coder-{uuid.uuid4().hex[:8]}"
        _seed_instance(
            engine, instance_id=parent_id, parent_id=None,
            status=InstanceStatus.WAITING_CHILDREN.value,
            agent_name="leader",
        )
        _seed_instance(
            engine, instance_id=child_id, parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value, agent_name="coder",
        )
        _seed_delivered_pair(
            repo, engine, parent_id=parent_id, child_id=child_id,
            anchor=f"msg-final-{uuid.uuid4().hex[:8]}",
            content="first report",
        )
        rid = _seed_parent_report_row(
            engine, parent_id=parent_id, child_id=child_id,
            anchor=f"msg-final-2-{uuid.uuid4().hex[:8]}",
            status=MessageStatus.READY.value, content="a later, different report",
        )
        row = repo.enqueue(
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=f"msg-final-2-{uuid.uuid4().hex[:8]}",
            report_message_id=rid,
            content="a later, different report",
        )
        claim = repo.claim_for_task_delivery(rid)
        assert claim.status == "claimed"
        assert _row_state(engine, row.injection_id) == (
            ReportInjectionState.TASK_DELIVERED.value
        )

    def test_drain_dedup_scoped_per_child_not_per_content(
        self, repo, engine
    ):
        """Two DIFFERENT children may legitimately produce identical
        content — the dedup must never cross the child boundary."""
        parent_id = f"leader-{uuid.uuid4().hex[:8]}"
        child_a = f"worker-a-{uuid.uuid4().hex[:8]}"
        child_b = f"worker-b-{uuid.uuid4().hex[:8]}"
        _seed_instance(
            engine, instance_id=parent_id, parent_id=None,
            status=InstanceStatus.WAITING_CHILDREN.value,
            agent_name="leader",
        )
        for cid in (child_a, child_b):
            _seed_instance(
                engine, instance_id=cid, parent_id=parent_id,
                status=InstanceStatus.COMPLETED.value, agent_name="worker",
            )
        _seed_delivered_pair(
            repo, engine, parent_id=parent_id, child_id=child_a,
            anchor=f"msg-final-{uuid.uuid4().hex[:8]}",
            content="same text from two workers",
        )
        rid_b = _seed_parent_report_row(
            engine, parent_id=parent_id, child_id=child_b,
            anchor=f"msg-final-{uuid.uuid4().hex[:8]}",
            status=MessageStatus.READY.value,
            content="same text from two workers",
        )
        row_b = repo.enqueue(
            parent_instance_id=parent_id,
            child_instance_id=child_b,
            child_message_id=f"msg-final-{uuid.uuid4().hex[:8]}",
            report_message_id=rid_b,
            content="same text from two workers",
        )
        drained = repo.claim_for_injection(parent_id)
        assert len(drained) == 1
        assert drained[0]["report_message_id"] == rid_b
        assert _row_state(engine, row_b.injection_id) == (
            ReportInjectionState.INJECTED.value
        )


# ─── Reviewer carve-out: fresh natural enqueues MUST still claim ────────────


class TestFreshSecondTurnObligationStillClaims:
    """Reviewer Finding #1 (2026-09-10): the terminal_twin content
    dedup MUST be scoped to sweep-recovered rows only
    (``recovery_attempted_at IS NOT NULL``).

    The wrong-anchor sweep-recovery class — rows that flowed through
    :meth:`transition_deferred_to_pending` and were re-driven by the
    recovery sweep — is the ONLY legitimate dedup target (those rows
    carry an artifact that duplicates a previously-delivered report).

    A FRESH natural enqueue (``recovery_attempted_at IS NULL``) is
    always a genuine delivery obligation, even when the content
    happens to byte-match a previously-delivered report. The carve-out
    scenario: the child completes turn 1 → ``row_1`` is delivered
    (terminal). The child is REVIVED via ``send_message`` → completes
    turn 2 → ``child_reports._process_child_completion_db_sync``
    enqueues ``row_2`` (PENDING, fresh ``child_message_id``, same
    content because the task is idempotent). The carve-out MUST let
    ``row_2`` claim — not dead-letter it as a duplicate obligation.
    """

    def test_fresh_second_turn_obligation_with_same_content_still_claims(
        self, repo, engine
    ):
        """Drain-seam carve-out: a fresh natural PENDING row whose
        content byte-matches a previously-delivered twin (terminal,
        ``recovery_attempted_at IS NULL`` — natural delivery, never
        sweep-recovered) MUST still drain and deliver. Pre-carve-out
        the dedup dead-lettered the legitimate second delivery."""
        parent_id = f"leader-{uuid.uuid4().hex[:8]}"
        child_id = f"wanderer-{uuid.uuid4().hex[:8]}"
        _seed_instance(
            engine, instance_id=parent_id, parent_id=None,
            status=InstanceStatus.WAITING_CHILDREN.value,
            agent_name="leader",
        )
        _seed_instance(
            engine, instance_id=child_id, parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value, agent_name="wanderer",
        )

        # Turn 1: delivered via the natural path. The terminal twin
        # has ``recovery_attempted_at IS NULL`` because natural
        # delivery never stamps the column (only
        # ``transition_deferred_to_pending`` does).
        turn1_anchor = f"msg-final-turn1-{uuid.uuid4().hex[:8]}"
        _seed_delivered_pair(
            repo, engine, parent_id=parent_id, child_id=child_id,
            anchor=turn1_anchor, content="identical turn content",
        )

        # Turn 2: child revived → fresh ``child_message_id``, fresh
        # ``report_message_id``, SAME content (idempotent task). The
        # ``recovery_attempted_at`` MUST stay NULL on this PENDING
        # row — that is the carve-out trigger.
        turn2_anchor = f"msg-final-turn2-{uuid.uuid4().hex[:8]}"
        turn2_rid = _seed_parent_report_row(
            engine, parent_id=parent_id, child_id=child_id,
            anchor=turn2_anchor,
            status=MessageStatus.READY.value,
            content="identical turn content",
        )
        turn2_row = repo.enqueue(
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=turn2_anchor,
            report_message_id=turn2_rid,
            content="identical turn content",
        )
        # Sanity: fresh row carries NULL ``recovery_attempted_at``.
        with Session(engine) as session:
            row = session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.injection_id == turn2_row.injection_id
                )
            ).one()
            assert row.recovery_attempted_at is None, (
                "fresh natural enqueues MUST leave recovery_attempted_at "
                "NULL — the carve-out discriminator"
            )

        drained = repo.claim_for_injection(parent_id)
        assert len(drained) == 1, (
            "the fresh turn-2 obligation MUST drain — the carved-out "
            "dedup must NOT match a non-stamped terminal twin"
        )
        assert drained[0]["report_message_id"] == turn2_rid
        assert drained[0]["content"] == "identical turn content"
        assert _row_state(engine, turn2_row.injection_id) == (
            ReportInjectionState.INJECTED.value
        ), (
            "the fresh turn-2 delivery MUST be INJECTED, not "
            "dead-lettered as a duplicate obligation"
        )

    def test_fresh_second_turn_obligation_at_task_claim_seam(
        self, repo, engine
    ):
        """Task-claim-seam carve-out (sister variant): a fresh
        natural PENDING row whose content byte-matches a
        previously-delivered twin (``recovery_attempted_at IS NULL``
        on the twin — natural delivery) MUST still claim via the
        fallback task path. Pre-carve-out the dedup returned
        ``already_delivered`` for the legitimate second delivery."""
        parent_id = f"leader-{uuid.uuid4().hex[:8]}"
        child_id = f"coder-{uuid.uuid4().hex[:8]}"
        _seed_instance(
            engine, instance_id=parent_id, parent_id=None,
            status=InstanceStatus.WAITING_CHILDREN.value,
            agent_name="leader",
        )
        _seed_instance(
            engine, instance_id=child_id, parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value, agent_name="coder",
        )

        # Turn 1: delivered via the natural drain. Terminal twin has
        # ``recovery_attempted_at IS NULL``.
        turn1_anchor = f"msg-final-turn1-{uuid.uuid4().hex[:8]}"
        _seed_delivered_pair(
            repo, engine, parent_id=parent_id, child_id=child_id,
            anchor=turn1_anchor, content="idempotent task output",
        )

        # Turn 2: fresh natural enqueue (NULL ``recovery_attempted_at``).
        turn2_anchor = f"msg-final-turn2-{uuid.uuid4().hex[:8]}"
        turn2_rid = _seed_parent_report_row(
            engine, parent_id=parent_id, child_id=child_id,
            anchor=turn2_anchor,
            status=MessageStatus.READY.value,
            content="idempotent task output",
        )
        turn2_row = repo.enqueue(
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=turn2_anchor,
            report_message_id=turn2_rid,
            content="idempotent task output",
        )
        with Session(engine) as session:
            row = session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.injection_id == turn2_row.injection_id
                )
            ).one()
            assert row.recovery_attempted_at is None

        # Task-path claim — MUST return ``claimed``, NOT
        # ``already_delivered``.
        claim = repo.claim_for_task_delivery(turn2_rid)
        assert claim.status == "claimed", (
            "the fresh turn-2 obligation MUST claim — the carved-out "
            "dedup must NOT match a non-stamped terminal twin. Got "
            f"{claim.status!r}."
        )
        assert _row_state(engine, turn2_row.injection_id) == (
            ReportInjectionState.TASK_DELIVERED.value
        )


# ─── Council 🟠 round-2 follow-up: stamp-side carve-out (windows a + b) ───────


class TestFreshSecondTurnAfterSweepRecoveryFirstTurnStillClaims:
    """Council 🟠 follow-up (2026-09-10, round 2) — window (a).

    The previous twin-side ``recovery_attempted_at IS NOT NULL``
    predicate (commit 5ce021cd) was on the wrong side: it filtered
    the terminal twin subquery instead of the dup row. When turn-1
    was delivered via sweep-recovery (the twin carries the stamp)
    and turn-2 was a fresh natural enqueue (the dup has NULL
    stamp), the predicate MATCHED the stamped twin and the
    legitimate second-turn obligation was silently dead-lettered.

    Fix: the stamp-side predicate now lives on the DUP row
    (``claim_for_injection`` filters ``dup_rows`` by
    ``recovery_attempted_at IS NOT NULL``;
    ``claim_for_task_delivery`` checks ``any_row.recovery_attempted_at
    is not None``). A fresh natural enqueue (NULL stamp) bypasses
    the dedup entirely.

    Both variants pinned here:
    """

    def test_fresh_second_turn_after_sweep_recovery_first_turn_drain(
        self, repo, engine
    ):
        """Drain-seam variant: turn-1 STAMPED twin (sweep-recovered
        delivery) + turn-2 fresh NATURAL dup (NULL stamp) with
        byte-identical content → turn-2 MUST drain and deliver.
        Pre-fix the twin-side stamp predicate MATCHED the stamped
        turn-1 twin and silently dead-lettered turn-2.
        """
        parent_id = f"leader-{uuid.uuid4().hex[:8]}"
        child_id = f"wanderer-{uuid.uuid4().hex[:8]}"
        _seed_instance(
            engine, instance_id=parent_id, parent_id=None,
            status=InstanceStatus.WAITING_CHILDREN.value,
            agent_name="leader",
        )
        _seed_instance(
            engine, instance_id=child_id, parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value, agent_name="wanderer",
        )

        # Turn 1: sweep-recovered delivery (twin carries the stamp).
        # We seed the natural path then stamp the terminal twin to
        # model the sweep-recovery transition
        # (:meth:`transition_deferred_to_pending` — sole stamping
        # writer).
        turn1_anchor = f"msg-final-turn1-{uuid.uuid4().hex[:8]}"
        turn1_twin = _seed_delivered_pair(
            repo, engine, parent_id=parent_id, child_id=child_id,
            anchor=turn1_anchor, content="identical turn content",
        )
        _stamp_recovery_attempted_at(engine, turn1_twin.injection_id)

        # Turn 2: fresh natural enqueue — ``recovery_attempted_at IS
        # NULL`` on this PENDING row (the carve-out discriminator).
        turn2_anchor = f"msg-final-turn2-{uuid.uuid4().hex[:8]}"
        turn2_rid = _seed_parent_report_row(
            engine, parent_id=parent_id, child_id=child_id,
            anchor=turn2_anchor,
            status=MessageStatus.READY.value,
            content="identical turn content",
        )
        turn2_row = repo.enqueue(
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=turn2_anchor,
            report_message_id=turn2_rid,
            content="identical turn content",
        )
        # Sanity: turn-2 dup carries NULL ``recovery_attempted_at``.
        with Session(engine) as session:
            row = session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.injection_id == turn2_row.injection_id
                )
            ).one()
            assert row.recovery_attempted_at is None, (
                "turn-2 fresh natural enqueue MUST leave "
                "recovery_attempted_at NULL — the dup-side carve-out"
            )

        drained = repo.claim_for_injection(parent_id)
        assert len(drained) == 1, (
            "the fresh turn-2 obligation MUST drain — the dup-side "
            "predicate MUST exclude NULL-stamped dups from dedup. "
            "Got 0 drains (false-suppression)."
        )
        assert drained[0]["report_message_id"] == turn2_rid
        assert _row_state(engine, turn2_row.injection_id) == (
            ReportInjectionState.INJECTED.value
        ), (
            "the fresh turn-2 delivery MUST be INJECTED, not "
            "dead-lettered"
        )

    def test_fresh_second_turn_after_sweep_recovery_first_turn_task(
        self, repo, engine
    ):
        """Task-claim-seam variant (sister): turn-1 STAMPED twin +
        turn-2 fresh NATURAL dup with byte-identical content → turn-2
        MUST claim via the fallback task path. Pre-fix the twin-side
        stamp predicate MATCHED the stamped turn-1 twin and returned
        ``already_delivered`` for the legitimate second delivery.
        """
        parent_id = f"leader-{uuid.uuid4().hex[:8]}"
        child_id = f"coder-{uuid.uuid4().hex[:8]}"
        _seed_instance(
            engine, instance_id=parent_id, parent_id=None,
            status=InstanceStatus.WAITING_CHILDREN.value,
            agent_name="leader",
        )
        _seed_instance(
            engine, instance_id=child_id, parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value, agent_name="coder",
        )

        # Turn 1: stamped twin (sweep-recovered delivery).
        turn1_anchor = f"msg-final-turn1-{uuid.uuid4().hex[:8]}"
        turn1_twin = _seed_delivered_pair(
            repo, engine, parent_id=parent_id, child_id=child_id,
            anchor=turn1_anchor, content="idempotent task output",
        )
        _stamp_recovery_attempted_at(engine, turn1_twin.injection_id)

        # Turn 2: fresh natural enqueue (NULL stamp).
        turn2_anchor = f"msg-final-turn2-{uuid.uuid4().hex[:8]}"
        turn2_rid = _seed_parent_report_row(
            engine, parent_id=parent_id, child_id=child_id,
            anchor=turn2_anchor,
            status=MessageStatus.READY.value,
            content="idempotent task output",
        )
        turn2_row = repo.enqueue(
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=turn2_anchor,
            report_message_id=turn2_rid,
            content="idempotent task output",
        )
        with Session(engine) as session:
            row = session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.injection_id == turn2_row.injection_id
                )
            ).one()
            assert row.recovery_attempted_at is None

        claim = repo.claim_for_task_delivery(turn2_rid)
        assert claim.status == "claimed", (
            "the fresh turn-2 obligation MUST claim — the dup-side "
            "predicate MUST skip the dedup for NULL-stamped dups. "
            f"Got {claim.status!r}."
        )
        assert _row_state(engine, turn2_row.injection_id) == (
            ReportInjectionState.TASK_DELIVERED.value
        )


class TestLegacyWrongAnchorDupDeadLettered:
    """Council 🟠 follow-up (2026-09-10, round 2) — window (b).

    The previous twin-side predicate left the FIRST wrong-anchor
    recovery to slip through: the legacy primary prod shape is
    a STAMPED dup row (came through
    :meth:`transition_deferred_to_pending`) against the UNSTAMPED
    natural turn-1 twin (the original delivery, ``recovery_attempted_at
    IS NULL``). The twin-side predicate MISSED the unstamped twin
    and one duplicate was delivered on the first recovery pass.

    Fix: the dup-side stamp predicate keeps the dedup armed — a
    stamped dup row matches ANY twin with same content regardless
    of the twin's stamp state. Pin the regression at both seams.
    """

    def test_legacy_wrong_anchor_dup_dead_lettered_drain(
        self, repo, engine
    ):
        """Drain-seam window (b): stamped wrong-anchor dup vs
        unstamped natural twin → dup MUST be dead-lettered (FAILED)
        and twin MUST NOT be re-delivered. The fresh natural twin
        exists with NULL ``recovery_attempted_at`` — the exact shape
        that slipped through the previous twin-side predicate.
        """
        parent_id = f"leader-{uuid.uuid4().hex[:8]}"
        child_id = f"wanderer-{uuid.uuid4().hex[:8]}"
        _seed_instance(
            engine, instance_id=parent_id, parent_id=None,
            status=InstanceStatus.WAITING_CHILDREN.value,
            agent_name="leader",
        )
        _seed_instance(
            engine, instance_id=child_id, parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value, agent_name="wanderer",
        )

        # Original turn-1: natural delivery — twin carries NULL
        # ``recovery_attempted_at`` (the pre-recovery shape).
        turn1_anchor = f"msg-final-turn1-{uuid.uuid4().hex[:8]}"
        _seed_delivered_pair(
            repo, engine, parent_id=parent_id, child_id=child_id,
            anchor=turn1_anchor, content="legacy report body",
        )
        # Sanity: natural twin has NULL stamp (the window-b premise).
        with Session(engine) as session:
            twins = list(
                session.exec(
                    sm_select(ReportInjection).where(
                        ReportInjection.parent_instance_id == parent_id
                    ).where(
                        ReportInjection.child_instance_id == child_id
                    ).where(
                        ReportInjection.state == (
                            ReportInjectionState.TASK_DELIVERED.value
                        )
                    )
                ).all()
            )
            assert len(twins) == 1
            assert twins[0].recovery_attempted_at is None, (
                "natural twin MUST carry NULL recovery_attempted_at "
                "— the window-b premise"
            )

        # Wrong-anchor sweep-recovered DUP: stamped, fresh
        # ``child_message_id``, fresh ``report_message_id``, SAME
        # content (the byte-identical wrong-anchor artifact).
        dup_anchor = f"msg-dispatch-wrong-{uuid.uuid4().hex[:8]}"
        dup_rid = _seed_parent_report_row(
            engine, parent_id=parent_id, child_id=child_id,
            anchor=dup_anchor,
            status=MessageStatus.READY.value,
            content="legacy report body",
        )
        dup_row = repo.enqueue(
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=dup_anchor,
            report_message_id=dup_rid,
            content="legacy report body",
        )
        _stamp_recovery_attempted_at(engine, dup_row.injection_id)

        drained = repo.claim_for_injection(parent_id)
        assert drained == [], (
            "the wrong-anchor sweep-recovered dup MUST be dead-"
            "lettered (FAILED), never delivered — pre-fix this dup "
            "slipped through the twin-side stamp predicate and was "
            "delivered as a duplicate. Got drained: "
            f"{[r['report_message_id'] for r in drained]!r}"
        )
        assert _row_state(engine, dup_row.injection_id) == (
            ReportInjectionState.FAILED.value
        ), (
            "the wrong-anchor dup MUST end up FAILED (dead-lettered), "
            "not INJECTED"
        )
        # The dup's companion READY row is completed (the same
        # hygiene as a real claim — no orphan READY messages).
        with Session(engine) as session:
            dup_msg = session.exec(
                sm_select(MessageQueue).where(
                    MessageQueue.message_id == dup_rid
                )
            ).one()
        assert dup_msg.status == MessageStatus.COMPLETED.value

    def test_legacy_wrong_anchor_dup_dead_lettered_task(
        self, repo, engine
    ):
        """Task-claim-seam window (b): stamped wrong-anchor dup vs
        unstamped natural twin → task claim MUST resolve to
        ``already_delivered`` and the dup MUST end up FAILED.
        """
        parent_id = f"leader-{uuid.uuid4().hex[:8]}"
        child_id = f"reviewer-{uuid.uuid4().hex[:8]}"
        _seed_instance(
            engine, instance_id=parent_id, parent_id=None,
            status=InstanceStatus.WAITING_CHILDREN.value,
            agent_name="leader",
        )
        _seed_instance(
            engine, instance_id=child_id, parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value, agent_name="reviewer",
        )

        # Original turn-1 natural twin (NULL stamp).
        turn1_anchor = f"msg-final-turn1-{uuid.uuid4().hex[:8]}"
        _seed_delivered_pair(
            repo, engine, parent_id=parent_id, child_id=child_id,
            anchor=turn1_anchor, content="legacy report body",
        )

        # Wrong-anchor sweep-recovered DUP (stamped).
        dup_anchor = f"msg-grandchild-wrong-{uuid.uuid4().hex[:8]}"
        dup_rid = _seed_parent_report_row(
            engine, parent_id=parent_id, child_id=child_id,
            anchor=dup_anchor,
            status=MessageStatus.READY.value,
            content="legacy report body",
        )
        dup_row = repo.enqueue(
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=dup_anchor,
            report_message_id=dup_rid,
            content="legacy report body",
        )
        _stamp_recovery_attempted_at(engine, dup_row.injection_id)

        claim = repo.claim_for_task_delivery(dup_rid)
        assert claim.status == "already_delivered", (
            "the wrong-anchor stamped dup MUST resolve to "
            "already_delivered — pre-fix it slipped through and "
            f"was claimed. Got {claim.status!r}."
        )
        assert _row_state(engine, dup_row.injection_id) == (
            ReportInjectionState.FAILED.value
        )


# ─── Legacy wrong-anchor rows: dead-lettered rows are terminal ──────────────


class TestDeadLetteredRowsAreTerminalEvidence:
    """A FAILED (dead-lettered) injection row is terminal evidence for
    (parent, child): the no-row backstop must not re-select the child
    (the obligation was consciously abandoned, not dropped)."""

    def test_failed_row_excludes_child_from_lane2(self, repo, engine):
        parent_id = f"leader-{uuid.uuid4().hex[:8]}"
        child_id = f"wanderer-{uuid.uuid4().hex[:8]}"
        _seed_instance(
            engine, instance_id=parent_id, parent_id=None,
            status=InstanceStatus.WAITING_CHILDREN.value,
            agent_name="leader",
        )
        _seed_instance(
            engine, instance_id=child_id, parent_id=parent_id,
            status=InstanceStatus.COMPLETED.value, agent_name="wanderer",
        )
        _seed_child_message(
            engine, instance_id=child_id,
            message_id=f"msg-{uuid.uuid4().hex[:8]}",
        )
        row = repo.ensure_deferred(
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=f"msg-{uuid.uuid4().hex[:8]}",
            deferred_reason=DEFERRED_REASON_RESUME_ROUTER,
        )
        assert row is not None
        with Session(engine) as session:
            session.execute(
                sa_update(ReportInjection)
                .where(ReportInjection.injection_id == row.injection_id)
                .values(state=ReportInjectionState.FAILED.value)
            )
            session.commit()
        assert repo.find_completed_children_without_delivery(
            parent_not_terminal=True, limit=100
        ) == [], (
            "a dead-lettered obligation must not re-arm the no-row "
            "backstop"
        )
