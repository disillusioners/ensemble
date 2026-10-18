"""Sticky-subtree-error recovery retraction (2026-10-18,
fix/finalize-subtree-error-payload — RCA job 6465391d).

Incident shape (RCA): child p4-finisher hit GraphRecursionError
04:04:35Z → leader-side revive 04:50:52Z → clean completion
05:10:27Z → the bus's ``_parent_errored`` marker was NEVER cleared
→ at root completion the observer finalized with inherited
``terminal_status=error`` (job 6465391d closed FAILED though the
work completed and the judge verdict was ``complete``).

Fix semantics under test — the ``(parent, child)`` recovery pair
set added to :class:`DependencyBus`:

  * error stamps flag + message + pair (both emit seams);
  * ``status="completed"`` from the SAME child retracts the pair
    and, when the parent has no remaining unrecovered pairs,
    clears the flag + message (both emit seams — the instance-pair
    emit carries the child identity directly; the task-keyed emit
    derives it from the watcher payload's ``metadata.child_id``);
  * ``status="terminated"`` does NOT retract (termination is not
    clean completion);
  * a sibling's unrecovered error keeps the flag set (counter-
    constraint: genuine unrecovered child errors still stamp
    error);
  * an error AFTER a recovery re-stamps (revive → error again is
    a fresh unrecovered error);
  * legacy watcher rows without ``metadata.child_id`` keep the
    pre-fix permanently-sticky behavior (conservative direction).

In-memory SQLite, no daemon, no PostgreSQL — same fixture strategy
as ``tests/unit/services/test_dependency_bus_fire_for_terminated.py``
(StaticPool + check_same_thread=False so ``asyncio.to_thread``
shares the connection).
"""

from __future__ import annotations

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
)


PARENT_ID = "parent-root-6465391d"
CHILD_A = "child-p4-finisher-a"
CHILD_B = "child-sibling-b"
TASK_T1 = "9001"  # child A's first (erroring) task
TASK_T2 = "9002"  # child A's revived task (clean completion)


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------


@pytest.fixture
def repo():
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
async def bus(repo):
    b = DependencyBus(repo)
    await b.start()
    try:
        yield b
    finally:
        await b.stop()


def _seed_watcher(
    repo: DependencyWatcherRepository,
    *,
    source_task_id: str,
    target_instance_id: str,
    child_id: str | None,
) -> str:
    """Insert a PENDING watcher row; return its watch_id.

    ``child_id=None`` produces the LEGACY payload shape (no
    ``metadata.child_id`` key) that predates ``send_message``
    stamping the child identity.
    """
    metadata: dict = {
        "kind": "child_complete",
        "parent_id": target_instance_id,
        "message_id": f"msg-{source_task_id}",
    }
    if child_id is not None:
        metadata["child_id"] = child_id
    fu = FollowUp(
        target_instance_id=target_instance_id,
        message=f"[dependency_bus] child {child_id} completed",
        source=f"internal_agent:{target_instance_id}",
        metadata=metadata,
    )
    row = DependencyWatcher(
        source_task_id=source_task_id,
        target_instance_id=target_instance_id,
        follow_up_payload=fu.to_payload(),
        state="PENDING",
    )
    repo.insert(row)
    return row.watch_id


# -------------------------------------------------------------------------
# Defect-1 core: error → revive → clean completion must NOT leave the
# sticky marker (the false-FAILED root stamp).
# -------------------------------------------------------------------------


async def test_error_then_clean_completion_via_instance_pair_retracts(bus):
    """The RCA incident shape: error → revive → clean complete.

    The instance-pair emit (``emit_terminal_for_child_instance``)
    carries the child identity directly — the retraction fires even
    when the task-keyed emit has no pending watcher to read
    ``metadata.child_id`` from (the error's watcher already fired,
    and this test deliberately does NOT seed a new one).
    """
    bus._record_child_error_state(PARENT_ID, CHILD_A, "GraphRecursionError")
    assert bus.had_parent_error(PARENT_ID) is True
    assert bus.parent_error_message(PARENT_ID) == "GraphRecursionError"

    # Revive happens OUTSIDE the bus (send_message); the recovery FACT
    # is the later clean completion, observed here by the instance-pair
    # emit with no pending watcher rows at all.
    await bus.emit_terminal_for_child_instance(
        parent_instance_id=PARENT_ID,
        child_instance_id=CHILD_A,
        outcome=Outcome(status="completed", summary="revived run finished"),
    )
    assert bus.had_parent_error(PARENT_ID) is False
    assert bus.parent_error_message(PARENT_ID) is None


async def test_error_via_task_keyed_emit_stamps_pair_with_child_id(bus, repo):
    """The task-keyed error stamp records the (parent, child) pair.

    The child identity comes from the watcher payload's
    ``metadata.child_id`` — the field ``send_message`` stamps at
    watcher registration.
    """
    _seed_watcher(
        repo,
        source_task_id=TASK_T1,
        target_instance_id=PARENT_ID,
        child_id=CHILD_A,
    )
    fired = await bus.emit_terminal(
        task_id=TASK_T1,
        outcome=Outcome(status="error", error="GraphRecursionError"),
    )
    assert len(fired) == 1
    assert bus.had_parent_error(PARENT_ID) is True
    assert (PARENT_ID, CHILD_A) in bus._unrecovered_child_errors

    # Clean completion of the SAME child via the instance-pair seam
    # retracts — even though the error's watcher is already FIRED.
    await bus.emit_terminal_for_child_instance(
        parent_instance_id=PARENT_ID,
        child_instance_id=CHILD_A,
        outcome=Outcome(status="completed"),
    )
    assert bus.had_parent_error(PARENT_ID) is False
    assert (PARENT_ID, CHILD_A) not in bus._unrecovered_child_errors


async def test_clean_completion_via_task_keyed_emit_retracts(bus, repo):
    """The task-keyed completion emit also retracts.

    The revived child's clean turn runs on a NEW task; the parent's
    revive-time ``send_message`` registered a fresh watcher on that
    task with ``metadata.child_id`` — the completion emit derives
    the child identity from those pending rows.
    """
    bus._record_child_error_state(PARENT_ID, CHILD_A, "boom")
    assert bus.had_parent_error(PARENT_ID) is True

    _seed_watcher(
        repo,
        source_task_id=TASK_T2,
        target_instance_id=PARENT_ID,
        child_id=CHILD_A,
    )
    fired = await bus.emit_terminal(
        task_id=TASK_T2,
        outcome=Outcome(status="completed"),
    )
    assert len(fired) == 1
    assert bus.had_parent_error(PARENT_ID) is False
    assert bus.parent_error_message(PARENT_ID) is None


# -------------------------------------------------------------------------
# Counter-constraint: genuine unrecovered errors still stamp error.
# -------------------------------------------------------------------------


async def test_genuine_unrecovered_error_stays_sticky(bus):
    """No recovery → the flag never clears (pre-fix behavior kept)."""
    bus._record_child_error_state(PARENT_ID, CHILD_A, "real failure")
    assert bus.had_parent_error(PARENT_ID) is True
    # Nothing retracts; the flag survives arbitrary other traffic.
    await bus.emit_terminal_for_child_instance(
        parent_instance_id=PARENT_ID,
        child_instance_id=CHILD_B,
        outcome=Outcome(status="completed"),
    )
    assert bus.had_parent_error(PARENT_ID) is True


async def test_sibling_unrecovered_error_keeps_flag(bus):
    """One child recovers, the sibling does not → parent still error."""
    bus._record_child_error_state(PARENT_ID, CHILD_A, "error A")
    bus._record_child_error_state(PARENT_ID, CHILD_B, "error B")
    assert bus.had_parent_error(PARENT_ID) is True

    await bus.emit_terminal_for_child_instance(
        parent_instance_id=PARENT_ID,
        child_instance_id=CHILD_A,
        outcome=Outcome(status="completed"),
    )
    # B is still unrecovered → flag + B's message remain.
    assert bus.had_parent_error(PARENT_ID) is True
    assert bus.parent_error_message(PARENT_ID) == "error B"

    # B recovers too → nothing unrecovered → clear.
    await bus.emit_terminal_for_child_instance(
        parent_instance_id=PARENT_ID,
        child_instance_id=CHILD_B,
        outcome=Outcome(status="completed"),
    )
    assert bus.had_parent_error(PARENT_ID) is False


async def test_error_again_after_recovery_restamps(bus):
    """Revive → clean complete → error AGAIN is a fresh unrecovered error."""
    bus._record_child_error_state(PARENT_ID, CHILD_A, "first error")
    await bus.emit_terminal_for_child_instance(
        parent_instance_id=PARENT_ID,
        child_instance_id=CHILD_A,
        outcome=Outcome(status="completed"),
    )
    assert bus.had_parent_error(PARENT_ID) is False

    await bus.emit_terminal_for_child_instance(
        parent_instance_id=PARENT_ID,
        child_instance_id=CHILD_A,
        outcome=Outcome(status="error", error="second error"),
    )
    assert bus.had_parent_error(PARENT_ID) is True
    assert bus.parent_error_message(PARENT_ID) == "second error"


async def test_terminated_does_not_retract(bus):
    """Termination is not clean completion — the error stays sticky."""
    bus._record_child_error_state(PARENT_ID, CHILD_A, "error then terminate")
    await bus.emit_terminal_for_child_instance(
        parent_instance_id=PARENT_ID,
        child_instance_id=CHILD_A,
        outcome=Outcome(status="terminated"),
    )
    assert bus.had_parent_error(PARENT_ID) is True


async def test_null_error_outcome_does_not_stamp(bus):
    """F-1 gate regression guard: Outcome(error=None) never stamps."""
    await bus.emit_terminal_for_child_instance(
        parent_instance_id=PARENT_ID,
        child_instance_id=CHILD_A,
        outcome=Outcome(status="error", error=None),
    )
    assert bus.had_parent_error(PARENT_ID) is False
    assert bus._unrecovered_child_errors == set()


# -------------------------------------------------------------------------
# Legacy + lifecycle edges.
# -------------------------------------------------------------------------


async def test_legacy_watcher_without_child_id_stays_sticky(bus, repo):
    """Watcher rows without ``metadata.child_id`` stay sticky forever
    (council S1, 2026-10-18).

    The task-keyed emit cannot attribute the error to a child, so it
    records the SENTINEL pair ``(parent_id, None)`` instead of a bare
    skip — the sentinel is what makes the legacy error VISIBLE to the
    retraction predicate (so an unrelated attributable child's clean
    completion can never clear the flag while this error is
    unrecovered). A later clean completion of any child — attributable
    or unattributable — must NOT retract the sentinel (exact-equality
    membership: ``None`` matches no attributable child id).
    """
    _seed_watcher(
        repo,
        source_task_id=TASK_T1,
        target_instance_id=PARENT_ID,
        child_id=None,  # legacy payload shape
    )
    await bus.emit_terminal(
        task_id=TASK_T1,
        outcome=Outcome(status="error", error="legacy row error"),
    )
    assert bus.had_parent_error(PARENT_ID) is True
    assert bus._unrecovered_child_errors == {(PARENT_ID, None)}

    # A later clean completion of an attributable child (sibling) must
    # NOT clear the sentinel — exact-equality membership preserves it.
    _seed_watcher(
        repo,
        source_task_id=TASK_T2,
        target_instance_id=PARENT_ID,
        child_id=CHILD_A,  # attributable — different child
    )
    await bus.emit_terminal(
        task_id=TASK_T2,
        outcome=Outcome(status="completed"),
    )
    assert bus.had_parent_error(PARENT_ID) is True
    assert bus.parent_error_message(PARENT_ID) == "legacy row error"
    assert bus._unrecovered_child_errors == {(PARENT_ID, None)}

    # An explicit clear_parent_error (the FULL clear at finalize time
    # / S1 escape hatch) drops the sentinel.
    bus.clear_parent_error(PARENT_ID)
    assert bus._unrecovered_child_errors == set()


async def test_clear_parent_error_drops_pairs(bus):
    """The finalize-time wholesale clear leaves no residual pair state."""
    bus._record_child_error_state(PARENT_ID, CHILD_A, "err")
    assert (PARENT_ID, CHILD_A) in bus._unrecovered_child_errors

    bus.clear_parent_error(PARENT_ID)
    assert bus.had_parent_error(PARENT_ID) is False
    assert bus.parent_error_message(PARENT_ID) is None
    assert bus._unrecovered_child_errors == set()

    # The next incarnation of the same instance id starts clean.
    assert bus.had_parent_error(PARENT_ID) is False


async def test_retraction_only_affects_recovered_child(bus):
    """A clean completion of child A never retracts child B's pair."""
    bus._record_child_error_state(PARENT_ID, CHILD_A, "error A")
    bus._record_child_error_state("other-parent", CHILD_B, "error B")

    await bus.emit_terminal_for_child_instance(
        parent_instance_id=PARENT_ID,
        child_instance_id=CHILD_A,
        outcome=Outcome(status="completed"),
    )
    assert ("other-parent", CHILD_B) in bus._unrecovered_child_errors
    assert bus.had_parent_error("other-parent") is True
    assert bus.had_parent_error(PARENT_ID) is False


# -------------------------------------------------------------------------
# Council S1 (2026-10-18) — mixed-shape + child_id-is-not-None invariants.
# -------------------------------------------------------------------------


async def test_mixed_shape_sentinel_preserves_legacy_against_unrelated_retraction(
    bus, repo,
):
    """Council S1 — the counter-constraint that was structurally broken.

    Sequence:
      1. Legacy watcher on T1 (no ``metadata.child_id``) errors →
         stamps flag + SENTINEL pair ``(P, None)`` (the new
         behavior; pre-fix the helper was a bare skip).
      2. Attributable watcher on T2 (child_id=CHILD_B) errors →
         stamps flag + attributable pair ``(P, CHILD_B)``.
      3. Real instance-pair clean completion for CHILD_B retracts
         only the attributable pair — the sentinel must SURVIVE
         and keep the parent flag set (counter-constraint: a
         genuinely unrecovered legacy error must still stamp
         error; pre-sentinel the flag would have cleared at
         step 3 because the sentinel was absent).

    Pre-fix: assertion fails (flag cleared / sentinel absent).
    """
    # 1) Legacy error via the REAL task-keyed emit path.
    _seed_watcher(
        repo,
        source_task_id=TASK_T1,
        target_instance_id=PARENT_ID,
        child_id=None,  # legacy payload shape
    )
    await bus.emit_terminal(
        task_id=TASK_T1,
        outcome=Outcome(status="error", error="legacy row error"),
    )
    assert bus.had_parent_error(PARENT_ID) is True
    assert bus._unrecovered_child_errors == {(PARENT_ID, None)}

    # 2) Attributable sibling error via the REAL task-keyed emit path.
    _seed_watcher(
        repo,
        source_task_id=TASK_T2,
        target_instance_id=PARENT_ID,
        child_id=CHILD_B,
    )
    await bus.emit_terminal(
        task_id=TASK_T2,
        outcome=Outcome(status="error", error="attributable error"),
    )
    assert (PARENT_ID, CHILD_B) in bus._unrecovered_child_errors
    assert (PARENT_ID, None) in bus._unrecovered_child_errors

    # 3) Unrelated attributable clean completion retracts ONLY the
    #    attributable pair; the sentinel survives.
    await bus.emit_terminal_for_child_instance(
        parent_instance_id=PARENT_ID,
        child_instance_id=CHILD_B,
        outcome=Outcome(status="completed"),
    )
    assert (PARENT_ID, CHILD_B) not in bus._unrecovered_child_errors
    assert (PARENT_ID, None) in bus._unrecovered_child_errors, (
        "Council S1: the legacy sentinel must survive the unrelated "
        "attributable clean completion — the structural guarantee "
        "that a genuinely unrecovered legacy error still stamps "
        "terminal_status=error."
    )
    assert bus.had_parent_error(PARENT_ID) is True, (
        "Council S1: parent's flag must stay set while the sentinel "
        "is present, regardless of how many UNRELATED attributable "
        "child errors recover."
    )
    # Message reflects the LAST captured stamp (existing
    # ``_parent_error_message`` semantics — the per-pair message
    # is not a closed mapping). The counter-constraint this pin
    # guards is the FLAG survival, not the message text.
    assert bus.parent_error_message(PARENT_ID) == "attributable error"

    # The escape hatch: explicit clear_parent_error drops the
    # sentinel (the full clear at finalize time).
    bus.clear_parent_error(PARENT_ID)
    assert bus._unrecovered_child_errors == set()
    assert bus.had_parent_error(PARENT_ID) is False


async def test_child_id_is_not_none_invariant_at_real_emit_seams(bus, repo):
    """Council S1 — invariant: every production emit path passes
    ``child_id is not None`` to ``_record_child_error_state``.

    Exercise the REAL task-keyed + instance-pair emit paths with
    PRODUCTION-shaped followers (the ``_send_message`` /
    ``FollowUp`` shape at ``daemon/tools/instance.py:1181-1194``
    always stamps ``metadata.child_id``) and assert via a spy on
    the helper that every captured ``child_id`` is non-None. The
    sentinel exists for legacy defensive reasons but is not the
    production path — a regression that re-introduced a None
    child_id through any emit site would land here as a False.
    """
    captured: list[tuple[str, str | None, str]] = []
    _original = bus._record_child_error_state
    bus._record_child_error_state = (
        lambda parent_id, child_id, message: (
            captured.append((parent_id, child_id, message))
        )
        or _original(parent_id, child_id, message)
    )
    try:
        # Production-shaped task-keyed error (instance.py stamps
        # child_id in the watcher metadata at registration time).
        _seed_watcher(
            repo,
            source_task_id=TASK_T1,
            target_instance_id=PARENT_ID,
            child_id=CHILD_A,
        )
        await bus.emit_terminal(
            task_id=TASK_T1,
            outcome=Outcome(status="error", error="A"),
        )
        # Production-shaped instance-pair error (parameter is
        # required str — no path passes None here).
        await bus.emit_terminal_for_child_instance(
            parent_instance_id=PARENT_ID,
            child_instance_id=CHILD_B,
            outcome=Outcome(status="error", error="B"),
        )
    finally:
        bus._record_child_error_state = _original

    assert captured, "no error emit observed"
    none_child_ids = [(p, c, m) for (p, c, m) in captured if c is None]
    assert not none_child_ids, (
        f"Council S1 invariant: no production emit path should pass "
        f"child_id=None to _record_child_error_state. Captured: "
        f"{captured!r}; offenders (None child_id): {none_child_ids!r}. "
        f"Re-introducing a None child_id through a stamp site would "
        f"create a sentinel pair the retraction path could later "
        f"accidentally over-reach on (counter-constraint regression)."
    )
    assert {c for (_p, c, _m) in captured} == {CHILD_A, CHILD_B}

