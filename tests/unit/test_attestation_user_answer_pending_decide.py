"""FIX-2 unit pins: the ``user_answer_pending`` arm in ``decide()`` + the
DB-backed detection predicate.

Two layers:

1. Pure ``decide()`` arm tests — the FIFTH legitimate-pending input
   plain-allows with ZERO counter movement, positioned so it wins over
   the attested reset (trigger 1), blocks the bound escalation, and
   leaves dry-mode untouched. The default (kwarg absent) is
   byte-identical to the pre-FIX-2 behavior.

2. ``TaskRepository.has_open_answer_handle_for_gate`` — the DB-backed
   detection (real file-backed SQLite, mirroring the attestation E2E
   fixture): the open-handle shape reads True; a consumed handle
   (``ResumeTurn`` cleared it) reads False — no stale-allow; a NEWER
   task row expires a leaked handle (freshness guard — no permanent
   allow bypass); the ambiguous >1-handle invariant violation refuses
   the bypass.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.event import listens_for
from sqlalchemy.pool import NullPool
from sqlmodel import Session as SQLModelSession
from sqlmodel import SQLModel

from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.attestation_gate import (
    Decision,
    GateSettings,
    decide,
)


def _decide(**overrides):
    """Call ``decide()`` with the canonical deny-shape defaults
    (enforce, delegated, not attested, nothing pending, count 0,
    bound 3) plus per-test overrides.

    Stage 3 (2026-09-17, R2/R3): decide() lost its meta params
    (scope_applicable / mode / attestation_enabled) — those branches
    moved to evaluate()'s composition layer. Historical kwargs are
    dropped here so the arm tests keep pinning the pure enforce
    tree; the retired-branch tests below were re-contracted."""
    kwargs = dict(
        attested=False,
        pending_children=0,
        queued_or_expected_wakeups=0,
        live_descendants=0,
        denied_count=0,
        bound=3,
        attestation_required=True,
    )
    kwargs.update(overrides)
    kwargs.pop("scope_applicable", None)
    kwargs.pop("mode", None)
    kwargs.pop("attestation_enabled", None)
    return decide(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# Pure decide() arm tests
# ─────────────────────────────────────────────────────────────────────────────


class TestUserAnswerPendingArm:
    def test_answer_pending_plain_allow_counter_unchanged(self):
        result = _decide(user_answer_pending=True)
        assert result.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        assert result.next_denied_count == 0
        assert result.should_inject_nudge is False

    def test_answer_pending_wins_over_attested_reset(self):
        """ZERO counter movement — the arm fires BEFORE the attested
        check, so an attested-allow reset (trigger 1) does not run
        while an answer is pending."""
        result = _decide(
            attested=True, denied_count=5, user_answer_pending=True
        )
        assert result.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        assert result.next_denied_count == 5, (
            "answer pending: counter UNCHANGED even when attested "
            "(no counter movement at all — no reset to 0)"
        )

    def test_answer_pending_blocks_bound_escalation(self):
        """A leader at/over the bound with an open answer is NOT
        terminal — the pending user answer holds the gate open."""
        result = _decide(denied_count=5, user_answer_pending=True)
        assert result.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        assert result.next_denied_count == 5

    def test_answer_pending_blocks_deny(self):
        result = _decide(denied_count=2, user_answer_pending=True)
        assert result.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        assert result.next_denied_count == 2

    def test_conditional_off_with_answer_pending_composition_allow(self):
        """Stage 3 (R4) re-contract: the no-delegation arm retired
        from decide() into evaluate()'s composition layer. At the
        decide() level the answer-pending arm is now the FIRST branch
        (ALLOWED_LEGITIMATE); the historical plain-ALLOWED shape for
        non-delegated missions is produced by the composition bypass
        BEFORE decide() runs (pinned at the evaluate seam in
        test_attestation_conditional_gate_outcomes)."""
        result = _decide(
            attestation_required=False, user_answer_pending=True
        )
        # Pure enforce tree: the answer arm wins.
        assert result.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        assert result.next_denied_count == 0
        assert result.should_inject_nudge is False

    def test_dry_mode_unaffected(self):
        """Stage 3 (R3) re-contract: the DRY_LOG mapping moved to
        evaluate()'s mode layer. At the decide() level the answer arm
        produces its plain-allow shape; the DRY_LOG mapping is pinned
        at the evaluate seam (test_attestation_gate dry-mode class +
        test_attestation_dry_logging)."""
        result = _decide(user_answer_pending=True)
        assert result.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        assert result.next_denied_count == 0

    def test_meta_conditions_still_win(self):
        """Stage 3 (R2) re-contract: the gate-off / out-of-scope
        bypasses retired from decide() into evaluate()'s composition
        layer (predicate Term-0 mirror) — pinned at the evaluate seam
        in test_attestation_gate.TestMetaConditionsAtCompositionLayer.
        At the decide() level the answer arm produces its plain-allow
        shape regardless (the meta flags are no longer inputs)."""
        result = _decide(user_answer_pending=True)
        assert result.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        assert result.next_denied_count == 0

    def test_default_absent_is_legacy_behavior(self):
        """Kwarg absent ≡ False ≡ pre-FIX-2 behavior on every arm."""
        for kwargs in (
            dict(attested=True),
            dict(attestation_required=False),
            dict(pending_children=2),
            dict(denied_count=3),
            dict(denied_count=2),
        ):
            absent = _decide(**kwargs)
            explicit = _decide(**kwargs, user_answer_pending=False)
            assert absent == explicit, f"drift on {kwargs}"


# ─────────────────────────────────────────────────────────────────────────────
# DB-backed detection — TaskRepository.has_open_answer_handle_for_gate
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def gate_task_engine(tmp_path):
    """File-backed SQLite with the Task table (mirrors the attestation
    E2E fixture's narrow-schema approach)."""
    db_path = tmp_path / "answer_handle_gate.sqlite"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    SQLModel.metadata.create_all(engine, tables=[Task.__table__])
    try:
        yield engine
    finally:
        engine.dispose()


def _open_handle(repo: TaskRepository, instance_id: str) -> Task:
    """Create a task and suspend it with the awaiting_answer handle —
    the exact row shape ``SuspendTurn`` leaves behind."""
    task = repo.create(
        task_type="process_message",
        instance_id=instance_id,
        message_id="m-1",
    )
    with SQLModelSession(repo.engine) as session:
        row = session.get(Task, task.id)
        row.status = TaskStatus.PAUSED.value
        row.suspension_reason = "awaiting_answer"
        row.resume_target_turn_id = task.work_id
        session.add(row)
        session.commit()
    return task


class TestHasOpenAnswerHandleForGate:
    def test_open_handle_reads_true(self, gate_task_engine):
        repo = TaskRepository(gate_task_engine)
        _open_handle(repo, "leader-fix2")
        assert repo.has_open_answer_handle_for_gate("leader-fix2") is True

    def test_no_handle_reads_false(self, gate_task_engine):
        repo = TaskRepository(gate_task_engine)
        repo.create(task_type="process_message", instance_id="leader-fix2")
        assert repo.has_open_answer_handle_for_gate("leader-fix2") is False

    def test_consumed_handle_reads_false_no_stale_allow(
        self, gate_task_engine
    ):
        """After the answer is consumed (``ResumeTurn``: status
        paused→pending, handle columns nulled in one UPDATE) the
        detection reads False — NO stale-allow window on the
        post-answer turn."""
        repo = TaskRepository(gate_task_engine)
        task = _open_handle(repo, "leader-fix2")
        assert repo.has_open_answer_handle_for_gate("leader-fix2") is True

        # ResumeTurn's guarded UPDATE shape.
        with SQLModelSession(repo.engine) as session:
            row = session.get(Task, task.id)
            row.status = TaskStatus.PENDING.value
            row.suspension_reason = None
            row.resume_target_turn_id = None
            session.add(row)
            session.commit()

        assert repo.has_open_answer_handle_for_gate("leader-fix2") is False

    def test_freshness_guard_newer_row_expires_leaked_handle(
        self, gate_task_engine
    ):
        """A leaked pre-revive handle must NOT arm a permanent bypass:
        any NEWER task row for the instance (the revived instance's
        next turn) expires the handle."""
        repo = TaskRepository(gate_task_engine)
        _open_handle(repo, "leader-fix2")
        assert repo.has_open_answer_handle_for_gate("leader-fix2") is True

        # The instance was revived and started a NEWER turn.
        repo.create(
            task_type="process_message",
            instance_id="leader-fix2",
            message_id="m-after-revive",
        )
        assert repo.has_open_answer_handle_for_gate("leader-fix2") is False

    def test_ambiguous_multiple_handles_refuse_the_bypass(
        self, gate_task_engine
    ):
        """>1 open handles = invariant violation — the gate refuses the
        allow bypass (False) instead of resolving by recency. The
        resume path raises ValueError on the same shape; the read-only
        gate predicates refuses instead."""
        repo = TaskRepository(gate_task_engine)
        _open_handle(repo, "leader-fix2")
        _open_handle(repo, "leader-fix2")
        assert repo.has_open_answer_handle_for_gate("leader-fix2") is False

    def test_other_instance_unaffected(self, gate_task_engine):
        repo = TaskRepository(gate_task_engine)
        _open_handle(repo, "leader-a")
        repo.create(task_type="process_message", instance_id="leader-b")
        assert repo.has_open_answer_handle_for_gate("leader-a") is True
        assert repo.has_open_answer_handle_for_gate("leader-b") is False

    def test_awaiting_children_handle_is_not_a_user_answer(
        self, gate_task_engine
    ):
        """Only ``awaiting_answer`` arms the bypass — a different
        suspension reason (e.g. ``awaiting_children``) must not."""
        repo = TaskRepository(gate_task_engine)
        task = repo.create(
            task_type="process_message", instance_id="leader-fix2"
        )
        with SQLModelSession(repo.engine) as session:
            row = session.get(Task, task.id)
            row.status = TaskStatus.PAUSED.value
            row.suspension_reason = "awaiting_children"
            row.resume_target_turn_id = task.work_id
            session.add(row)
            session.commit()
        assert repo.has_open_answer_handle_for_gate("leader-fix2") is False
