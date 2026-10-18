"""P1/P2 — end-to-end finalize-lane pins for subtree-error retraction
(2026-10-18, ``fix/finalize-subtree-error-payload`` — RCA job 6465391d
follow-up; standing S6 coverage-gap item).

The existing pin ``tests/unit/services/test_subtree_error_recovery_retraction.py``
drives the REAL ``DependencyBus`` directly and asserts bus-level state
only (``had_parent_error`` / ``_unrecovered_child_errors``). That
leaves the FINALIZE leg transitively enforced: nothing proved that a
retraction actually changes the ROOT's finalized terminal outcome when
the real finalize lane runs. This file closes that gap.

Both pins drive the REAL lane end-to-end:

    real DependencyBus emit seams (task-keyed
    ``emit_terminal`` + instance-pair
    ``emit_terminal_for_child_instance``)
        → real ``JobFeedbackObserver._process_event``
            → real ``_resolve_finalize_status`` (the sole
              finalize decision point — consults
              ``bus.had_parent_error``)
                → real ``_finalize_job`` → post-commit outbox.

Only ``_finalize_job_db_sync`` is stubbed (the same seam the N8 /
D2 reference harnesses stub — it would otherwise open a Session
against the harness's MagicMock instance_manager engine). The stub is
CAPTURING: it records the ``(terminal_status, error_message,
result_summary)`` the finalize lane committed AND the
``had_parent_error`` snapshot the decision point consulted, so the
pins assert the FINALIZED ROOT OUTCOME, not the bus flag.

  * **P1** — the incident shape: child A errors (task-keyed emit,
    watcher payload carries ``metadata.child_id``) → revive happens
    outside the bus → the SAME child completes cleanly (instance-pair
    emit) → the pair retracts, the flag clears → the root's own
    ``completed`` lifecycle event must finalize ``completed`` with NO
    inherited ``error_message``. Pre-fix, the sticky flag forced
    ``terminal_status="error"`` at ``_resolve_finalize_status`` (the
    false-FAILED root stamp — job 6465391d closed FAILED though the
    work completed).

  * **P2** — the counter-constraint, MIXED shape: sibling A's error is
    genuinely UNRECOVERED while sibling B errored-then-recovered (B's
    pair retracts, A's pair survives) → the flag stays True → the
    root's own clean ``completed`` lifecycle event must STILL finalize
    ``error`` with the last captured child error threaded as
    ``error_message``.

Differential proof convention: copy ONLY this file into a worktree at
the pre-fix base (``8b8b32dcc``) — P1 FAILS there (the finalize lane
stamps ``terminal_status="error"`` because the pre-fix bus has no
retraction; the flag survives the clean completion) while P2 PASSES
(the pre-fix sticky behavior already kept genuine errors failed).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

# Register table models so the watcher table exists for create_all.
import daemon.repositories.dependency_bus.models  # noqa: F401

from daemon.repositories.dependency_bus import (
    DependencyWatcher,
    DependencyWatcherRepository,
)
from daemon.services.dependency_bus import (
    DependencyBus,
    FollowUp,
    Outcome,
    set_dependency_bus,
)
from daemon.services.job_feedback_observer import _FinalizeJobResult

from tests.job_queue.test_n8_hot_path_pin import (
    _build_observer_for_hot_path,
)


ROOT_FINAL_REPORT = "root-final real report (authoritative assistant message)"
ERROR_A = "sibling A unrecovered failure"
ERROR_B = "child B transient failure"


# ── Fixtures (in-memory SQLite, same strategy as the bus-level pin) ──────


@pytest.fixture(autouse=True)
def _reset_bus_singleton():
    """Tear down the DependencyBus singleton after each test so a
    failing assertion can't leak the real bus into the next test."""
    yield
    set_dependency_bus(None)


@pytest.fixture
def bus_repo():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    watcher_table = SQLModel.metadata.tables.get("dependency_watchers")
    if watcher_table is not None:
        watcher_table.create(eng, checkfirst=True)
    return DependencyWatcherRepository(eng)


@pytest.fixture
async def real_bus(bus_repo):
    """A REAL DependencyBus (not the N8 harness's mock) against the
    in-memory watcher table — the same construction the bus-level
    retraction pin uses, here wired under the observer's finalize
    lane."""
    bus = DependencyBus(bus_repo)
    await bus.start()
    try:
        yield bus
    finally:
        set_dependency_bus(None)
        await bus.stop()


def _seed_watcher(
    repo: DependencyWatcherRepository,
    *,
    source_task_id: str,
    target_instance_id: str,
    child_id: str,
) -> str:
    """Insert a PENDING watcher row carrying ``metadata.child_id`` (the
    field ``send_message`` stamps at watcher registration)."""
    fu = FollowUp(
        target_instance_id=target_instance_id,
        message=f"[dependency_bus] child {child_id} completed",
        source=f"internal_agent:{target_instance_id}",
        metadata={
            "kind": "child_complete",
            "parent_id": target_instance_id,
            "message_id": f"msg-{source_task_id}",
            "child_id": child_id,
        },
    )
    row = DependencyWatcher(
        source_task_id=source_task_id,
        target_instance_id=target_instance_id,
        follow_up_payload=fu.to_payload(),
        state="PENDING",
    )
    repo.insert(row)
    return row.watch_id


def _build_finalize_lane_observer(*, instance_id: str, job_id: str, bus):
    """Build the observer via the N8 hot-path harness, then:

    1. swap the harness's MOCK bus for the REAL bus under test (the
       observer reads the singleton live at ``_process_event`` and
       ``_finalize_job`` time);
    2. re-stub ``_finalize_job_db_sync`` with a 6-arg-capable
       CAPTURING fake (the harness's own ``_fake_sync`` predates the
       ``already_finalized_job_id`` parameter and raises TypeError on
       the current call shape — same re-stub the D2 pins perform).
    """
    observer, mock_jqs, resolver = _build_observer_for_hot_path(
        instance_id=instance_id,
        mirror_work_id=job_id,
        task_work_ids=[],
        job_id_for_instance=job_id,
    )
    # Unresolve the root's work_id: the outbox's per-kind dispatch then
    # falls through to the DEFAULT terminal token ("completed" /
    # "failed") per ``WorkResolverService.per_kind_status_for``'s
    # documented unresolvable-work contract — the tokens the pins
    # assert on the delivered receipts.
    resolver.resolve_work = MagicMock(return_value=None)
    # The REAL bus replaces the harness mock — every bus consult the
    # finalize lane makes (count_pending_for_target / _get_parent_lock /
    # get_generation / had_parent_error / parent_error_message /
    # clear_parent_error) now hits production code.
    set_dependency_bus(bus)

    finalize_calls: list[dict] = []

    def _fake_sync(
        job_id,
        instance_id,
        terminal_status,
        result_summary,
        error_message,
        _already_finalized=None,
    ):
        finalize_calls.append(
            {
                "job_id": job_id,
                "instance_id": instance_id,
                "terminal_status": terminal_status,
                "result_summary": result_summary,
                "error_message": error_message,
                "already_finalized_job_id": _already_finalized,
                # The value the sole finalize decision point
                # (``_resolve_finalize_status``) consulted — captured
                # INSIDE the sync write so the pin sees the pre-clear
                # sticky state at the exact decision moment.
                "subtree_error_flag_at_finalize": bus.had_parent_error(
                    instance_id
                ),
            }
        )
        return _FinalizeJobResult(
            skip=False,
            terminal_status=terminal_status,
            job_id=job_id,
            instance_id=instance_id,
            parent_id=None,
            agent_id="worker",
            result_summary=result_summary,
            error_message=error_message,
            locks_released=1,
            instance_was_terminal=False,
        )

    observer._finalize_job_db_sync = MagicMock(side_effect=_fake_sync)
    observer._instance_manager._get_last_assistant_message_raw = AsyncMock(
        return_value=ROOT_FINAL_REPORT
    )
    return observer, mock_jqs, finalize_calls


def _root_completed_event(instance_id: str) -> dict:
    """The parent's own clean-turn lifecycle event (the incident's
    root-completion shape — the parent's OWN turn succeeded; only the
    inherited subtree-error state can flip the outcome)."""
    return {
        "event_type": "instance_lifecycle",
        "data": {
            "instance_id": instance_id,
            "status": "completed",
            "error": None,
        },
    }


# ── P1: error → revive → clean complete ⇒ ROOT NOT failed ───────────────


class TestP1ReviveCleanCompleteFinalizesRootCompleted:
    """The RCA incident shape, asserted at the FINALIZED ROOT OUTCOME.

    The bus-level pin
    (``test_error_then_clean_completion_via_instance_pair_retracts``)
    proves the flag clears; THIS pin proves the clear actually reaches
    the finalize decision — the root finalizes ``completed``, not the
    inherited ``error``.
    """

    async def test_p1_error_then_revive_clean_complete_finalizes_root_completed(
        self, real_bus, bus_repo
    ):
        bus = real_bus
        root = f"root-{uuid.uuid4().hex[:8]}"
        child_a = f"child-a-{uuid.uuid4().hex[:8]}"
        root_job = f"job-{uuid.uuid4().hex[:8]}"
        t1 = f"t1-{uuid.uuid4().hex[:8]}"

        # 1. Child A errors on its first turn — REAL task-keyed emit;
        #    the watcher payload carries metadata.child_id so the
        #    (parent, child) pair is attributable.
        _seed_watcher(
            bus_repo,
            source_task_id=t1,
            target_instance_id=root,
            child_id=child_a,
        )
        fired = await bus.emit_terminal(
            t1, Outcome(status="error", error="GraphRecursionError")
        )
        assert len(fired) == 1
        assert bus.had_parent_error(root) is True, (
            "pre-condition: the error emit must stamp the parent's "
            "sticky flag before the recovery leg runs"
        )

        # 2. Revive happens OUTSIDE the bus (send_message); the
        #    recovery FACT is the same child's later clean completion,
        #    observed by the REAL instance-pair emit.
        await bus.emit_terminal_for_child_instance(
            parent_instance_id=root,
            child_instance_id=child_a,
            outcome=Outcome(status="completed", summary="revived run finished"),
        )
        # Retraction pre-conditions (transitive — the finalize assert
        # below is the pin):
        assert bus.had_parent_error(root) is False
        assert (root, child_a) not in bus._unrecovered_child_errors

        # 3. Drive the REAL finalize lane: the root's own completed
        #    lifecycle event → _process_event → _resolve_finalize_status
        #    → _finalize_job → captured sync write.
        observer, mock_jqs, finalize_calls = _build_finalize_lane_observer(
            instance_id=root, job_id=root_job, bus=bus
        )
        await observer._process_event(_root_completed_event(root))

        # THE P1 PIN — the FINALIZED root outcome is NOT failed.
        assert len(finalize_calls) == 1, (
            "exactly one finalize write expected for the root's "
            "terminal event"
        )
        (call,) = finalize_calls
        assert call["terminal_status"] == "completed", (
            "P1: after the (parent, child) error pair retracts via the "
            "child's clean completion, the root's own completed turn "
            "must finalize terminal_status='completed'. Pre-fix, the "
            "sticky _parent_errored flag survived the retraction-less "
            "bus and _resolve_finalize_status forced "
            "terminal_status='error' — the false-FAILED root stamp "
            "(job 6465391d closed FAILED though the work completed)."
        )
        assert call["error_message"] is None, (
            "P1: no inherited child-error text may flow into the "
            "finalized error slot of a recovered root (the "
            "'child agent error' fallback must not leak)."
        )
        assert call["subtree_error_flag_at_finalize"] is False, (
            "P1: the finalize decision point must have observed a "
            "CLEAN bus (had_parent_error=False) — the retraction, not "
            "a post-hoc finalize clear, is what saved the outcome."
        )
        assert call["result_summary"] == ROOT_FINAL_REPORT, (
            "P1: the COMPLETED lane must have run (assistant-message "
            "payload fetched), not the ERROR lane."
        )
        # No sticky marker survives the lane.
        assert bus.had_parent_error(root) is False
        assert (root, child_a) not in bus._unrecovered_child_errors
        # The finalize lane ran END-TO-END: the post-commit outbox
        # fired the terminal notify for the root's work_id.
        notify_calls = mock_jqs.notify_watchers.await_args_list
        assert any(
            c.args[0] == root_job and c.args[1] == "completed"
            for c in notify_calls
        ), (
            "P1: the post-commit outbox must deliver a 'completed' "
            "receipt for the root's work_id (the finalize lane ran "
            "past the sync write)."
        )


# ── P2: sibling UNRECOVERED error + sibling clean completion ⇒ FAILED ────


class TestP2SiblingUnrecoveredKeepsRootFailed:
    """Counter-constraint, MIXED shape, asserted at the finalize level.

    The bus-level pin
    (``test_sibling_unrecovered_error_keeps_flag``) proves the flag
    survives at bus level; THIS pin proves the surviving flag actually
    stamps the ROOT's finalized terminal_status='error' through the
    real finalize decision point.
    """

    async def test_p2_sibling_unrecovered_error_with_clean_sibling_completion_keeps_root_failed(
        self, real_bus, bus_repo
    ):
        bus = real_bus
        root = f"root-{uuid.uuid4().hex[:8]}"
        child_a = f"child-a-{uuid.uuid4().hex[:8]}"
        child_b = f"child-b-{uuid.uuid4().hex[:8]}"
        root_job = f"job-{uuid.uuid4().hex[:8]}"
        t1 = f"t1-{uuid.uuid4().hex[:8]}"
        t2 = f"t2-{uuid.uuid4().hex[:8]}"

        # 1. Sibling A errors — genuinely unrecovered for the whole
        #    scenario. REAL task-keyed emit.
        _seed_watcher(
            bus_repo,
            source_task_id=t1,
            target_instance_id=root,
            child_id=child_a,
        )
        await bus.emit_terminal(
            t1, Outcome(status="error", error=ERROR_A)
        )
        # 2. Sibling B errors too — but then RECOVERS. REAL task-keyed
        #    emit for the error, REAL instance-pair emit for the clean
        #    completion (the revival recovery fact).
        _seed_watcher(
            bus_repo,
            source_task_id=t2,
            target_instance_id=root,
            child_id=child_b,
        )
        await bus.emit_terminal(
            t2, Outcome(status="error", error=ERROR_B)
        )
        await bus.emit_terminal_for_child_instance(
            parent_instance_id=root,
            child_instance_id=child_b,
            outcome=Outcome(status="completed", summary="B recovered"),
        )
        # Mixed-shape pre-conditions at bus level: B retracted, A did
        # NOT — the flag must still be set.
        assert (root, child_b) not in bus._unrecovered_child_errors, (
            "pre-condition: B's clean completion retracts B's pair"
        )
        assert (root, child_a) in bus._unrecovered_child_errors, (
            "pre-condition: A's unrecovered pair must survive B's "
            "recovery (the counter-constraint's structural guarantee)"
        )
        assert bus.had_parent_error(root) is True

        # 3. Drive the REAL finalize lane with the root's own clean
        #    turn. The inherited subtree-error state must flip the
        #    outcome to error DESPITE the root completing cleanly.
        observer, mock_jqs, finalize_calls = _build_finalize_lane_observer(
            instance_id=root, job_id=root_job, bus=bus
        )
        await observer._process_event(_root_completed_event(root))

        # THE P2 PIN — the root STILL finalizes failed/error.
        assert len(finalize_calls) == 1
        (call,) = finalize_calls
        assert call["terminal_status"] == "error", (
            "P2: one sibling's UNRECOVERED error must keep the root's "
            "finalized terminal_status='error' even though ANOTHER "
            "sibling completed cleanly (and even though the root's "
            "own turn succeeded). The finalize-level retraction must "
            "only clear the outcome when the parent has ZERO "
            "remaining unrecovered (parent, child) pairs."
        )
        assert call["subtree_error_flag_at_finalize"] is True, (
            "P2: the finalize decision point must have observed the "
            "STICKY flag — the survival comes from A's unrecovered "
            "pair, not from the finalize path itself."
        )
        assert call["error_message"] == ERROR_B, (
            "P2: the last captured child error message threads into "
            "the finalized error slot (Phase-1 message flow; "
            "retracting B's pair must NOT resurrect A's older text "
            "nor clear B's message while A's pair survives)."
        )
        # The outbox rendered the FAILED receipt family for the root's
        # work_id (the finalize lane ran end-to-end on the error lane).
        notify_calls = mock_jqs.notify_watchers.await_args_list
        assert any(c.args[0] == root_job for c in notify_calls)
        failed_receipt = next(
            c for c in notify_calls if c.args[0] == root_job
        )
        assert failed_receipt.args[1] == "failed", (
            "P2: the post-commit outbox must render the FAILED "
            "receipt family (per-kind default for terminal_status="
            "'error') for the root's work_id."
        )
        # NOTE (pre-existing Phase-1 semantics, NOT this branch): the
        # finalize lane calls ``clear_parent_error`` AFTER the outcome
        # is decided, so post-event bus state is wholesale-cleared —
        # the pins above deliberately assert the CAPTURED decision-
        # point state, not post-clear residue.
