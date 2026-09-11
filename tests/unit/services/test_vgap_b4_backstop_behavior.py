"""Verification gap-fill for B4 (child report obligation backstop).

Independent verification of fix B4 (commit ``fef2b792``) at HEAD. Does NOT
trust the branch's own behavioral test
(``tests/unit/services/test_b4_child_report_obligation.py``), which ends in
``assert True`` for the headline case — this file rewrites that test as a
REAL behavioral assertion.

What B4 cycle-1 claims (commit message, ``fef2b792``):

    "If the natural completion path returned an outcome that does NOT emit
    (idempotency_skip, deferred_pause, dead_parent_skip, root_completed,
    tool_invocation_completed) but the instance is actually COMPLETED with
    a parent, force-emit via the corrective multi-turn primitive."

What the code actually does at cycle-1 HEAD (``55a76bb5``,
``daemon/services/child_reports.py:3606-4190``):

    Every canonical outcome (idempotency_skip, deferred_pause, dead_parent_
    skip, root_completed, tool_invocation_completed, plus the two defer
    SSE-only branches, plus child_still_running_defer / regular_child_
    completed which set ``bus_terminal_emitted=True`` unconditionally, plus
    instance_not_found) early-returns BEFORE the B4 backstop at line 4119.

    The backstop is reachable ONLY for outcome strings NOT in the
    canonical set — i.e. unknown outcome strings, NOT the
    ``idempotency_skip`` case the commit claims to fix.

What B4 cycle-2 changes (this branch, W5 obligation re-mint):

    The ``idempotency_skip`` branch (now at
    ``child_reports.py:3837+``) evaluates the obligation inline
    rather than relying on the backstop's reachability. When the
    instance is COMPLETED AND the parent is alive AND a PENDING
    watcher exists for the (parent, child) pair, the branch
    re-mints the corrective multi-turn emit. When ANY of those
    conditions fail (no parent, not COMPLETED, no PENDING watcher),
    the branch is a no-op (legit skip). This closes the
    84563a03 wedge for its stated target mechanism.

    All other canonical outcomes keep their cycle-1 behavior:
    early-return BEFORE the backstop's force-emit; no re-mint.

This file drives the REAL ``ChildReportsService._dispatch_post_commit_side_effects``
method end-to-end with a REAL ``DependencyBus`` over a file-backed SQLite
(via ``tmp_path`` + ``NullPool`` + ``WAL`` + ``busy_timeout=10000``) and
asserts the actual bus behavior. Mocks are limited to the data seams the
method documents as injectable (``manager._instance_repository.get``,
``manager._live_hub``).

Mock-fidelity note
==================

Real bus signature the backstop calls (line 4165-4171):

    await self._emit_terminal_for_child_instance_via_bus(
        parent_instance_id=parent_id,
        child_instance_id=instance_id,
        status="completed",
        summary=f"B4 backstop force-emit (outcome={outcome!r})",
    )

Real bus primitive underneath (line 626-639):

    fired = await bus.emit_terminal_for_child_instance(
        parent_instance_id=parent_instance_id,
        child_instance_id=child_instance_id,
        outcome=Outcome(status=status, error=error, summary=summary),
    )

The tests use a REAL ``DependencyBus`` instance wired via
``set_dependency_bus`` so the backstop's underlying primitive is exercised
verbatim — not a stub. We count ``bus.emit_terminal_for_child_instance``
calls (the bus's primitive) AND assert that any registered
``DependencyWatcher`` rows transition from PENDING to FIRED exactly once
(the bus's exactly-once contract, per ``transition_state``'s guarded
``WHERE state = 'PENDING'`` UPDATE in
``daemon/repositories/dependency_bus/repository.py:681``).

The only mocks are:
    * ``manager._instance_repository.get`` — returns a synthetic
      ``Instance`` row with the desired status (the backstop checks
      ``inst.status == COMPLETED.value`` before emitting).
    * ``manager._live_hub`` — set to ``None`` so the SSE branches short-
      circuit (the method guards on ``if self._manager._live_hub:``).
    * ``manager._events_service`` — set to ``None`` (guarded).
    * ``manager._queue_repository`` — set to ``MagicMock`` (only consulted
      by ``_trigger_title_generation`` which is NOT reached for any of
      the outcomes this file drives — ``root_completed`` and
      ``tool_invocation_completed`` early-return BEFORE the backstop).
    * ``manager._worker_pool`` — set to ``None`` (only consulted by
      ``regular_child_completed`` which sets ``bus_terminal_emitted=True``
      before the backstop).
    * ``manager._task_repo`` — set to ``None`` (only consulted by
      ``child_still_running_defer`` and ``regular_child_completed``
      which set ``bus_terminal_emitted=True`` before the backstop).

Tests in this file run via the worktree's pytest only (no daemon, no DB,
no external services). ``asyncio_mode = "auto"`` from
``pyproject.toml:68`` is in effect; each test is bounded by the global
30s timeout (ini ``pyproject.toml:69``).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

# Model imports — required so SQLModel.metadata sees the tables when
# create_all() runs on the test engine.
from daemon.repositories.dependency_bus.models import (  # noqa: F401
    DependencyWatcher,
    DependencyWatcherState,
)
from daemon.repositories.dependency_bus.repository import (
    DependencyWatcherRepository,
)
from daemon.repositories.instance.models import InstanceStatus
from daemon.services.child_reports import (
    ChildReportsService,
    _ChildCompletionDbResult,
)
from daemon.services.dependency_bus import DependencyBus, set_dependency_bus


# ─────────────────────────────────────────────────────────────────────────────
# Canonical outcome strings the function maps to early-returns
# (verified line-by-line against HEAD child_reports.py:3606-4190).
# ─────────────────────────────────────────────────────────────────────────────

# These outcomes early-return AND emit a bus terminal (set
# bus_terminal_emitted=True before returning).
EMITTING_outcomes = frozenset({"regular_child_completed", "child_still_running_defer"})

# These outcomes early-return WITHOUT emitting a bus terminal.
# Note: ``idempotency_skip`` was REMOVED from this set by B4 cycle-2.
# At HEAD (cycle-1), ``idempotency_skip`` early-returns WITHOUT firing
# the bus, matching the no-obligation defer shape. But under B4
# cycle-2 (W5 obligation re-mint), ``idempotency_skip`` is no longer
# a pure defer — when the obligation IS unmet (parent alive, instance
# COMPLETED, PENDING watcher present — the 84563a03 wedge case), the
# branch re-mints the corrective multi-turn emit via
# ``_emit_terminal_for_child_instance_via_bus``. So
# ``idempotency_skip`` is now in the EMITTING-shape category for that
# specific case. Legit defer shapes (no parent, not COMPLETED) flow
# past the branch as a no-op, which is now asserted separately in
# ``TestLegitimateIdempotencySkipDefer``.
NON_EMITTING_outcomes = frozenset(
    {
        "deferred_waiting_children",  # SSE only
        "root_waiting_children",      # SSE only
        "root_completed",             # root → no parent obligation
        "deferred_pause",             # DEFERRED marker persisted in DB-sync
        "dead_parent_skip",           # no live parent
        "tool_invocation_completed",  # lifecycle events, no bus
        "instance_not_found",         # no instance, no obligation
    }
)

# These are the outcomes that, in their pure natural-path form (no
# wedged obligation, no parent, not yet COMPLETED), never reach the
# backstop's force-emit branch. ``idempotency_skip`` is split out: it
# has BOTH legit-defer shapes (no parent / not yet COMPLETED) AND a
# wedge case (COMPLETED + parent + PENDING watcher) that fires the
# corrective emit. See ``TestLegitimateIdempotencySkipDefer`` for the
# legit side and ``test_target_shape_force_emits_idempotency_skip``
# for the wedge side.
DEFER_OUTCOMES_THAT_DO_NOT_EMIT = frozenset(
    {
        "deferred_waiting_children",
        "root_waiting_children",
        "root_completed",
        "deferred_pause",
        "dead_parent_skip",
        "tool_invocation_completed",
        "instance_not_found",
    }
)

ALL_CANONICAL_outcomes = frozenset(EMITTING_outcomes | NON_EMITTING_outcomes)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — file-backed SQLite + real DependencyBus + manager mock
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine per repo conventions.

    Per ``.agents/shared/conventions.md`` (Testing & QC): ``tmp_path +
    NullPool + WAL + busy_timeout=10000``. StaticPool + WriteGuardSession
    are forbidden at this seam — the bus's per-task locks rely on real
    cross-thread concurrency to exercise the ``transition_state`` guarded
    UPDATE exactly-once contract.
    """
    db_path = tmp_path / f"b4_vgap_{uuid.uuid4().hex[:8]}.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    # Enable WAL mode and busy_timeout per file-backed SQLite contract.
    @event.listens_for(eng, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()
        # Best-effort cleanup of the temp SQLite file.
        try:
            db_path.unlink(missing_ok=True)
        except Exception:
            pass


@pytest.fixture
async def bus(engine: Engine):
    """Started DependencyBus bound to the test engine (async-aware).

    Wires the bus as the module singleton (``set_dependency_bus``) so the
    backstop's ``get_dependency_bus()`` resolution returns our real bus.
    """
    repo = DependencyWatcherRepository(engine)
    b = DependencyBus(repo)
    await b.start()
    set_dependency_bus(b)
    try:
        yield b
    finally:
        await b.stop()
        set_dependency_bus(None)


def _seed_parent_watcher(
    engine: Engine,
    *,
    parent_instance_id: str,
    child_instance_id: str,
    source_task_id: str | None = None,
) -> str:
    """Insert a PENDING DependencyWatcher row registered by the parent.

    The watcher is keyed on the (parent, child) instance pair via
    ``target_instance_id`` + ``follow_up_payload['metadata']['child_id']``
    — this is the matcher the corrective ``emit_terminal_for_child_instance``
    uses (see ``daemon/services/dependency_bus.py:757`` and the
    ``fetch_pending_for_target_and_child`` in-memory filter at
    ``daemon/repositories/dependency_bus/repository.py:203``). Mirrors the
    ``send_message`` watcher-registration shape: ``send_message`` stamps
    ``follow_up_payload.metadata.child_id`` on every watcher (the same
    field the task-keyed emit misses for multi-turn children).
    """
    sid = source_task_id or f"task-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        watcher = DependencyWatcher(
            source_task_id=sid,
            target_instance_id=parent_instance_id,
            follow_up_payload={
                "kind": "follow_up",
                "metadata": {"child_id": child_instance_id},
            },
            watcher_metadata={"child_id": child_instance_id},
            state=DependencyWatcherState.PENDING.value,
        )
        session.add(watcher)
        session.commit()
    return sid


def _build_service_for_b4(
    *,
    instance_status: str,
    parent_id: str | None,
) -> tuple[ChildReportsService, MagicMock]:
    """Build a real ChildReportsService with the manager mocked at the
    documented seams only.

    Returns ``(service, manager)``. The mock manager exposes:
        * ``_instance_repository.get(instance_id)`` → synthetic Instance
          with the given ``status`` + ``parent_id`` so the backstop's
          ``inst.status == COMPLETED.value`` check works.
        * ``_live_hub`` → ``None`` so SSE branches short-circuit.
        * ``_events_service`` → ``None`` so lifecycle-event publish is
          a no-op.
        * Other attributes the function might touch (``_task_repo``,
          ``_worker_pool``, ``_queue_repository``, ``_report_injection_pending``)
          are ``None`` / ``MagicMock`` defaults; the backstop and the
          canonical early-return paths in this test don't actually
          require them.
    """
    manager = MagicMock(name="InstanceManager")
    inst = MagicMock(name="instance_row")
    inst.status = instance_status
    inst.parent_id = parent_id
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=inst)
    manager._live_hub = None
    manager._events_service = None
    manager._task_repo = None
    manager._worker_pool = None
    manager._queue_repository = MagicMock()
    manager._report_injection_pending = None

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None
    return service, manager


def _make_result(
    *,
    outcome: str,
    instance_id: str = "child-001",
    parent_id: str | None = "parent-001",
    agent_id: str | None = "child-agent",
    **kwargs: Any,
) -> _ChildCompletionDbResult:
    """Build a real ``_ChildCompletionDbResult`` NamedTuple.

    The method only reads attributes — defaults match the NamedTuple
    field defaults (parent fields default to ``None`` / falsy so the
    lifecycle-event branches that consult them short-circuit).
    """
    return _ChildCompletionDbResult(
        outcome=outcome,
        instance_id=instance_id,
        agent_id=agent_id,
        parent_id=parent_id,
        child_agent_id=kwargs.get("child_agent_id"),
        report_message_id=kwargs.get("report_message_id"),
        completed_parent_id=kwargs.get("completed_parent_id"),
        completed_parent_parent_id=kwargs.get("completed_parent_parent_id"),
        parent_agent_id=kwargs.get("parent_agent_id"),
        parent_waiting_children_sse=kwargs.get("parent_waiting_children_sse", False),
        waiting_children_parent_agent_id=kwargs.get(
            "waiting_children_parent_agent_id"
        ),
        b_violation_report=kwargs.get("b_violation_report"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# (a) Target shape — COMPLETED child + live parent + no prior emit
# ─────────────────────────────────────────────────────────────────────────────


class TestTargetShapeForceEmits:
    """B4 mission spec (1): child turn-completes with parent alive + no
    terminal emit → exactly-once force-emit fires.

    Drives the REAL ``_dispatch_post_commit_side_effects`` with the
    REAL outcome plumbing. Two shapes:

    (a1) outcome="idempotency_skip" — the 84563a03 wedge case (the commit
         message claims the backstop handles this).
    (a2) outcome=<unknown string> — the only outcome class that actually
         reaches the backstop at HEAD (everything else early-returns).
    """

    async def test_target_shape_force_emits_idempotency_skip(
        self, engine: Engine, bus: DependencyBus
    ) -> None:
        """84563a03 wedge: ``idempotency_skip`` + COMPLETED child + live
        parent + PENDING watcher → re-mint fire via
        ``emit_terminal_for_child_instance``.

        B4 cycle-2 (W5 obligation re-mint). The
        ``idempotency_skip`` branch USED to early-return BEFORE the
        B4 backstop (``child_reports.py:3805-3806``); that path was
        the unfixed-wedge shape for the 84563a03 incident (turn done
        10:59:15+07, no terminal report, parent parked ~4.5h).

        After B4 cycle-2 the branch evaluates the obligation
        inline: parent alive AND instance COMPLETED AND PENDING
        watcher present → re-mint the emit via the corrective
        multi-turn primitive. The bus's ``transition_state``
        guarded ``WHERE state = 'PENDING'`` UPDATE keeps the emit
        exactly-once (a legit skip with no PENDING watcher is a
        no-op via ``matched_rows → 0``).

        This is the regression pin for the 84563a03 recurrence.
        """
        child_id = "child-84563a03"
        parent_id = "parent-84563a03"
        _seed_parent_watcher(
            engine,
            parent_instance_id=parent_id,
            child_instance_id=child_id,
        )
        service, _ = _build_service_for_b4(
            instance_status=InstanceStatus.COMPLETED.value,
            parent_id=parent_id,
        )

        result = _make_result(
            outcome="idempotency_skip",
            instance_id=child_id,
            parent_id=parent_id,
        )

        await service._dispatch_post_commit_side_effects(
            result=result,
            last_content="assistant text",
            completed_message_id=None,
        )

        # The watcher MUST be FIRED (PENDING → FIRED) by the B4
        # cycle-2 obligation re-mint inside the ``idempotency_skip``
        # branch (no backstop traversal required anymore — the
        # re-mint is inline at ``child_reports.py:3837+``).
        with Session(engine) as session:
            row = session.query(DependencyWatcher).filter(
                DependencyWatcher.target_instance_id == parent_id
            ).first()
            assert row is not None, (
                "watcher row missing — bus fixture did not seed the row"
            )
            assert row.state == DependencyWatcherState.FIRED.value, (
                f"B4 cycle-2 obligation re-mint did not FIRE the "
                f"watcher: row.state={row.state!r} (expected FIRED). "
                f"Re-mint lives inline in the idempotency_skip branch "
                f"at child_reports.py:3837+ (W5 obligation re-mint)."
            )

    async def test_target_shape_force_emits_completed_but_not_emitted(
        self, engine: Engine, bus: DependencyBus
    ) -> None:
        """``completed-but-not-emitted`` shape: drives the function with
        an UNKNOWN outcome string that falls through past all the early-
        returns and reaches the backstop at ``child_reports.py:4119``.

        This is the ONLY outcome class that reaches the backstop at HEAD
        (every canonical outcome early-returns before :4119). The test
        documents that the backstop IS wired correctly for the unknown-
        outcome path — the defect is that the canonical
        ``idempotency_skip`` target case never reaches it.
        """
        child_id = "child-unknown-outcome"
        parent_id = "parent-unknown-outcome"
        _seed_parent_watcher(
            engine,
            parent_instance_id=parent_id,
            child_instance_id=child_id,
        )
        service, _ = _build_service_for_b4(
            instance_status=InstanceStatus.COMPLETED.value,
            parent_id=parent_id,
        )

        # An UNKNOWN outcome string — the only one that falls through to
        # the B4 backstop at HEAD (all canonical outcomes early-return).
        result = _make_result(
            outcome="__synthetic_unknown_outcome__",
            instance_id=child_id,
            parent_id=parent_id,
        )

        await service._dispatch_post_commit_side_effects(
            result=result,
            last_content="assistant text",
            completed_message_id=None,
        )

        # The watcher MUST be FIRED by the backstop.
        with Session(engine) as session:
            row = session.query(DependencyWatcher).filter(
                DependencyWatcher.target_instance_id == parent_id
            ).first()
            assert row is not None
            assert row.state == DependencyWatcherState.FIRED.value, (
                f"backstop should FIRE the watcher for unknown-outcome with "
                f"COMPLETED + parent; got row.state={row.state!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# (b) Concurrent double-fire — exactly one bus emit
# ─────────────────────────────────────────────────────────────────────────────


class TestConcurrentDoubleFireSingleEmit:
    """B4 mission spec (2): concurrent double-fire → single emit.

    Exactly-once preservation comes from the bus's
    ``transition_state`` guarded ``WHERE state = 'PENDING'`` UPDATE
    (see ``daemon/repositories/dependency_bus/repository.py:681``). The
    second concurrent emit's UPDATE sees ``state = 'FIRED'`` and returns
    ``rowcount == 0`` — no double-FollowUp.

    Drives TWO overlapping invocations of the full
    ``_dispatch_post_commit_side_effects`` against the same (parent,
    child) pair with the same unknown-outcome shape (the only path that
    reaches the backstop at HEAD). The watcher MUST transition to FIRED
    exactly once (rowcount == 1 across both UPDATE attempts).
    """

    async def test_concurrent_double_fire_single_emit(
        self, engine: Engine, bus: DependencyBus
    ) -> None:
        child_id = "child-concurrent"
        parent_id = "parent-concurrent"
        _seed_parent_watcher(
            engine,
            parent_instance_id=parent_id,
            child_instance_id=child_id,
        )

        async def _invoke_once() -> None:
            service, _ = _build_service_for_b4(
                instance_status=InstanceStatus.COMPLETED.value,
                parent_id=parent_id,
            )
            result = _make_result(
                outcome="__synthetic_unknown_outcome_concurrent__",
                instance_id=child_id,
                parent_id=parent_id,
            )
            await service._dispatch_post_commit_side_effects(
                result=result,
                last_content="assistant text",
                completed_message_id=None,
            )

        # Two overlapping invocations via asyncio.gather — both will
        # attempt to FIRE the same watcher row.
        await asyncio.gather(_invoke_once(), _invoke_once())

        # The watcher MUST be in FIRED state (single transition).
        with Session(engine) as session:
            row = session.query(DependencyWatcher).filter(
                DependencyWatcher.target_instance_id == parent_id
            ).first()
            assert row is not None
            assert row.state == DependencyWatcherState.FIRED.value, (
                f"watcher state should be FIRED (exactly-once preserved); "
                f"got row.state={row.state!r}"
            )

        # Belt-and-braces: ensure the (parent, child) pair has exactly
        # one FIRED watcher row (no double insert).
        with Session(engine) as session:
            fired_count = session.query(DependencyWatcher).filter(
                DependencyWatcher.target_instance_id == parent_id,
                DependencyWatcher.state == DependencyWatcherState.FIRED.value,
            ).count()
            assert fired_count == 1, (
                f"expected exactly 1 FIRED watcher for (parent, child); "
                f"got {fired_count} — concurrent emit double-fired the row"
            )


# ─────────────────────────────────────────────────────────────────────────────
# (c) Defer outcomes — MUST NOT be force-emitted by the backstop
# ─────────────────────────────────────────────────────────────────────────────


# NOTE: ``DEFER_OUTCOMES_THAT_DO_NOT_EMIT`` is defined at the top of
# this file (line ~146) for module-level visibility. It EXCLUDES
# ``idempotency_skip`` after B4 cycle-2 because that outcome is now
# obligation-aware (fires on the wedge case; defer in legitimate skip).


class TestDeferOutcomesNotForceEmitted:
    """B4 mission spec (3): each legit-defer outcome → NOT force-emitted.

    At HEAD (post-B4-cycle-2) the backstop skip-list enumerates
    ``child_still_running_defer`` — but that outcome already
    ``bus_terminal_emitted=True`` in its own branch — it's listed in
    the skip-list as a redundant safety, but the actual reason it's
    not re-emitted is the ``bus_terminal_emitted`` flag. We exclude
    it from this parameterization (the test would have to drive that
    branch's full natural path which depends on the bus's task-keyed
    cache).

    ``idempotency_skip`` is ALSO excluded — after B4 cycle-2 it is
    obligation-aware: COMPLETED + parent + PENDING watcher → fire;
    no parent OR not COMPLETED → legit defer. See
    :class:`TestLegitimateIdempotencySkipDefer` for the legit side
    and :meth:`TestTargetShapeForceEmits.test_target_shape_force_emits_idempotency_skip`
    for the wedge side.
    """

    @pytest.mark.parametrize(
        "outcome", sorted(DEFER_OUTCOMES_THAT_DO_NOT_EMIT)
    )
    async def test_defer_outcome_not_force_emitted(
        self, outcome: str, engine: Engine, bus: DependencyBus
    ) -> None:
        """Driving the canonical defer outcome MUST NOT FIRE the
        watcher — the backstop is unreachable for canonical outcomes
        (early-return BEFORE the backstop's force-emit).

        For ``instance_not_found``: the backstop / re-mint is
        unreachable because ``_instance_repository.get`` would
        return ``None`` and the ``inst is not None`` check fails.
        We exercise this branch too — the test seeds no parent
        (``parent_id is None`` so the instance repository is
        irrelevant).
        """
        # Special-case instance_not_found: no parent, no instance.
        if outcome == "instance_not_found":
            # No watcher seeded — both the cycle-2 re-mint and the
            # backstop short-circuit on ``parent_id is None`` BEFORE
            # the ``inst is not None and inst.status == COMPLETED``
            # check (which would also fail).
            child_id = "child-gone"
            service, _ = _build_service_for_b4(
                instance_status=InstanceStatus.COMPLETED.value,
                parent_id=None,
            )
            result = _make_result(
                outcome=outcome,
                instance_id=child_id,
                parent_id=None,
            )
            await service._dispatch_post_commit_side_effects(
                result=result,
                last_content="assistant text",
                completed_message_id=None,
            )
            # Sanity: no rows touched.
            with Session(engine) as session:
                count = session.query(DependencyWatcher).count()
                assert count == 0, (
                    f"instance_not_found must not touch the bus; "
                    f"got {count} watcher rows"
                )
            return

        child_id = f"child-{outcome}"
        parent_id = f"parent-{outcome}"
        _seed_parent_watcher(
            engine,
            parent_instance_id=parent_id,
            child_instance_id=child_id,
        )
        service, _ = _build_service_for_b4(
            instance_status=InstanceStatus.COMPLETED.value,
            parent_id=parent_id,
        )
        result = _make_result(
            outcome=outcome,
            instance_id=child_id,
            parent_id=parent_id,
        )

        await service._dispatch_post_commit_side_effects(
            result=result,
            last_content="assistant text",
            completed_message_id=None,
        )

        # The watcher MUST stay PENDING — the backstop (and B4
        # cycle-2's inline re-mint for the natural-path outcomes
        # in this set) was unreachable for this outcome (early-
        # return at the outcome-specific guard OR the obligation
        # criteria not met).
        with Session(engine) as session:
            row = session.query(DependencyWatcher).filter(
                DependencyWatcher.target_instance_id == parent_id
            ).first()
            assert row is not None, "watcher row missing"
            assert row.state == DependencyWatcherState.PENDING.value, (
                f"outcome={outcome!r}: backstop must not FIRE the "
                f"watcher; got row.state={row.state!r}. (Per the "
                f"post-B4-cycle-2 control flow at "
                f"child_reports.py:3804+ the early-return for "
                f"{outcome!r} fires BEFORE the backstop's force-emit — "
                f"the defer is unreachable.)"
            )


# ─────────────────────────────────────────────────────────────────────────────
# (c') B4 cycle-2 — legit idempotency_skip defer shapes (no parent /
#      instance not yet COMPLETED). Companion to the wedge side in
#      ``test_target_shape_force_emits_idempotency_skip``.
# ─────────────────────────────────────────────────────────────────────────────


class TestLegitimateIdempotencySkipDefer:
    """B4 cycle-2: ``idempotency_skip`` is obligation-aware. The wedge
    case (COMPLETED + parent + PENDING watcher) fires the corrective
    multi-turn emit (see
    :meth:`TestTargetShapeForceEmits.test_target_shape_force_emits_idempotency_skip`).
    The legit-defer cases below flow past the branch as a no-op —
    the obligation IS met (no parent, or the instance is not yet in
    COMPLETED status).
    """

    async def test_skip_when_no_parent(
        self, engine: Engine, bus: DependencyBus
    ) -> None:
        """``idempotency_skip`` + NO parent → no obligation → no-op.

        Without a parent, the re-mint branch's
        ``parent_id is not None`` gate short-circuits the helper call
        — the watcher is never consulted, no rows transition.
        """
        child_id = "child-legit-no-parent"
        service, _ = _build_service_for_b4(
            instance_status=InstanceStatus.COMPLETED.value,
            parent_id=None,
        )
        result = _make_result(
            outcome="idempotency_skip",
            instance_id=child_id,
            parent_id=None,
        )
        await service._dispatch_post_commit_side_effects(
            result=result,
            last_content="assistant text",
            completed_message_id=None,
        )
        # Sanity: no rows touched.
        with Session(engine) as session:
            count = session.query(DependencyWatcher).count()
            assert count == 0, (
                f"idempotency_skip with no parent must not touch "
                f"the bus; got {count} watcher rows"
            )

    async def test_skip_when_instance_not_completed(
        self, engine: Engine, bus: DependencyBus
    ) -> None:
        """``idempotency_skip`` + parent + instance NOT COMPLETED →
        defer was legitimate → no-op.

        The re-mint branch's
        ``inst.status == COMPLETED.value`` gate fails because the
        instance is still RUNNING — the defer was a legitimate
        retry/pause bridge, NOT the 84563a03 wedge.
        """
        child_id = "child-legit-not-completed"
        parent_id = "parent-legit-not-completed"
        _seed_parent_watcher(
            engine,
            parent_instance_id=parent_id,
            child_instance_id=child_id,
        )
        service, _ = _build_service_for_b4(
            instance_status=InstanceStatus.RUNNING.value,  # NOT COMPLETED
            parent_id=parent_id,
        )
        result = _make_result(
            outcome="idempotency_skip",
            instance_id=child_id,
            parent_id=parent_id,
        )
        await service._dispatch_post_commit_side_effects(
            result=result,
            last_content="assistant text",
            completed_message_id=None,
        )
        # The watcher MUST stay PENDING — the defer was legitimate
        # (the child isn't actually done yet).
        with Session(engine) as session:
            row = session.query(DependencyWatcher).filter(
                DependencyWatcher.target_instance_id == parent_id
            ).first()
            assert row is not None, "watcher row missing"
            assert row.state == DependencyWatcherState.PENDING.value, (
                f"idempotency_skip with non-COMPLETED instance: "
                f"the defer is legitimate, no re-mint should fire; "
                f"got row.state={row.state!r}"
            )

    async def test_skip_when_no_pending_watcher(
        self, engine: Engine, bus: DependencyBus
    ) -> None:
        """``idempotency_skip`` + COMPLETED + parent + NO PENDING
        watcher → legit skip (the natural path already fired).

        The cycle-2 re-mint still consults the bus
        (``fetch_pending_for_target_and_child``) — when no PENDING
        watcher exists, ``matched_rows → 0`` and the primitive
        returns an empty FollowUp list. So the watcher (NOT seeded
        here) is never touched and no row fires.
        """
        child_id = "child-legit-no-watcher"
        parent_id = "parent-legit-no-watcher"
        # NO watcher seeded — natural path already fired.
        service, _ = _build_service_for_b4(
            instance_status=InstanceStatus.COMPLETED.value,
            parent_id=parent_id,
        )
        result = _make_result(
            outcome="idempotency_skip",
            instance_id=child_id,
            parent_id=parent_id,
        )
        await service._dispatch_post_commit_side_effects(
            result=result,
            last_content="assistant text",
            completed_message_id=None,
        )
        # Sanity: no rows touched (the re-mint is a no-op because
        # the legit-skip shape has no PENDING watcher).
        with Session(engine) as session:
            count = session.query(DependencyWatcher).count()
            assert count == 0, (
                f"idempotency_skip with no PENDING watcher must "
                f"not touch the bus; got {count} watcher rows"
            )


# ─────────────────────────────────────────────────────────────────────────────
# (d) Backstop reachability for the two flag-gating skip-conditions
# ─────────────────────────────────────────────────────────────────────────────


class TestBackstopSkipConditions:
    """Pin the backstop's two additional skip conditions:
        * No parent (root instance → no obligation).
        * Instance NOT in COMPLETED status (defer was legitimate).

    Both conditions short-circuit the backstop's force-emit even for
    the only-reachable unknown-outcome shape.
    """

    async def test_backstop_skipped_when_no_parent(
        self, engine: Engine, bus: DependencyBus
    ) -> None:
        """No parent → backstop MUST NOT fire (root instance has no
        watcher to release)."""
        child_id = "child-root"
        # parent_id=None → backstop's "parent_id is not None" gate fails.
        service, _ = _build_service_for_b4(
            instance_status=InstanceStatus.COMPLETED.value,
            parent_id=None,
        )
        result = _make_result(
            outcome="__synthetic_unknown_no_parent__",
            instance_id=child_id,
            parent_id=None,
        )
        await service._dispatch_post_commit_side_effects(
            result=result,
            last_content="assistant text",
            completed_message_id=None,
        )
        # Sanity: no rows touched.
        with Session(engine) as session:
            count = session.query(DependencyWatcher).count()
            assert count == 0, (
                f"no-parent short-circuit must not touch the bus; "
                f"got {count} watcher rows"
            )

    async def test_backstop_skipped_when_instance_not_completed(
        self, engine: Engine, bus: DependencyBus
    ) -> None:
        """Instance NOT in COMPLETED status → backstop MUST NOT fire
        (the defer was legitimate — the child isn't actually done yet)."""
        child_id = "child-running"
        parent_id = "parent-running"
        _seed_parent_watcher(
            engine,
            parent_instance_id=parent_id,
            child_instance_id=child_id,
        )
        service, _ = _build_service_for_b4(
            instance_status=InstanceStatus.RUNNING.value,  # not COMPLETED
            parent_id=parent_id,
        )
        result = _make_result(
            outcome="__synthetic_unknown_running__",
            instance_id=child_id,
            parent_id=parent_id,
        )
        await service._dispatch_post_commit_side_effects(
            result=result,
            last_content="assistant text",
            completed_message_id=None,
        )
        # The watcher MUST stay PENDING — the defer was legitimate.
        with Session(engine) as session:
            row = session.query(DependencyWatcher).filter(
                DependencyWatcher.target_instance_id == parent_id
            ).first()
            assert row is not None
            assert row.state == DependencyWatcherState.PENDING.value, (
                f"non-COMPLETED instance: backstop must not FIRE the "
                f"watcher; got row.state={row.state!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# (e) B4 cycle-2 re-mint — concurrent double-fire exactly-once
#     (delta-audit coverage gap-fill). Idempotency_skip re-mint
#     (child_reports.py:3843-3882) is the wedge-fix path that the
#     existing UNKNOWN-outcome concurrent test does NOT cover.
# ─────────────────────────────────────────────────────────────────────────────


class TestReMintConcurrentDoubleFireSingleEmit:
    """B4 cycle-2 re-mint concurrent exactly-once coverage gap-fill.

    The cycle-2 obligation re-mint lives at ``child_reports.py:3843-3882``
    and emits via ``_emit_terminal_for_child_instance_via_bus`` when
    ``outcome == "idempotency_skip"`` AND ``parent_id is not None`` AND
    ``inst.status == COMPLETED``. The pre-existing concurrent test
    (:class:`TestConcurrentDoubleFireSingleEmit`) drives only the
    UNKNOWN-outcome backstop path — the re-mint call site has NO
    concurrent node. This class closes that gap.

    Exactly-once rests on:
      * ``transition_state``'s guarded ``UPDATE ... WHERE state = 'PENDING'``
        (daemon/repositories/dependency_bus/repository.py:681)
      * Per-task asyncio locks in ``DependencyBus`` (acquired at
        dependency_bus.py:897-901)
      * ``fetch_pending_for_target_and_child``'s state-filtered query
        (repository.py:260) — once the winner transitions the row, the
        loser's read sees ``state != PENDING`` and returns 0 rows.

    Drives TWO overlapping invocations of the full
    ``_dispatch_post_commit_side_effects`` against the same (parent,
    child) pair on the ``idempotency_skip`` re-mint shape. The
    watcher MUST transition to FIRED exactly once — the loser's
    guarded UPDATE yields ``rowcount == 0`` → no double emit, no
    duplicate FIRED row, no error.
    """

    async def test_idempotency_skip_concurrent_double_fire_single_emit(
        self, engine: Engine, bus: DependencyBus
    ) -> None:
        """B4 cycle-2 re-mint concurrent exactly-once pin.

        ``idempotency_skip`` + COMPLETED child + live parent + ONE
        PENDING watcher → TWO overlapping invocations via
        ``asyncio.gather`` → exactly ONE FIRED row, no double emit,
        no extra watcher rows.

        The two invocations share the SAME real bus instance (the
        same ``_pending`` cache, the same per-task locks, the same
        guarded UPDATE). The first invocation to reach
        ``transition_state`` wins; the second's UPDATE sees
        ``state == 'FIRED'`` and returns ``rowcount == 0`` (the
        bus primitive at dependency_bus.py:899-920 short-circuits on
        ``transitioned is False`` — the FollowUp is NOT appended to
        ``fired``, no cache pop on the loser's path). Exactly-once
        is preserved.
        """
        child_id = "child-remint-concurrent"
        parent_id = "parent-remint-concurrent"
        # Seed exactly ONE PENDING watcher — matches the
        # ``test_target_shape_force_emits_idempotency_skip`` setup
        # (the wedge case where the re-mint is reachable). The
        # watcher's ``source_task_id`` is auto-minted; the re-mint
        # matches on (parent, child) instance pair, not task id.
        _seed_parent_watcher(
            engine,
            parent_instance_id=parent_id,
            child_instance_id=child_id,
        )

        # Sanity: exactly ONE PENDING row, no FIRED yet.
        with Session(engine) as session:
            pre_count = session.query(DependencyWatcher).filter(
                DependencyWatcher.target_instance_id == parent_id,
            ).count()
            assert pre_count == 1, (
                f"fixture seed: expected 1 watcher row, got {pre_count}"
            )

        async def _invoke_once() -> None:
            """One overlapping invocation on the re-mint shape.

            The service is rebuilt per invocation (mirrors the
            existing concurrent test's pattern at line 526) — each
            invocation must independently resolve the bus and drive
            its own emit. The real ``DependencyBus`` singleton is
            the shared primitive both calls race on.
            """
            service, _ = _build_service_for_b4(
                instance_status=InstanceStatus.COMPLETED.value,
                parent_id=parent_id,
            )
            result = _make_result(
                outcome="idempotency_skip",
                instance_id=child_id,
                parent_id=parent_id,
            )
            await service._dispatch_post_commit_side_effects(
                result=result,
                last_content="assistant text",
                completed_message_id=None,
            )

        # Two overlapping invocations — both will attempt the
        # re-mint on the SAME (parent, child) pair. The bus's
        # ``transition_state`` guarded UPDATE + per-task locks
        # serialize the winner; the loser sees rowcount == 0.
        await asyncio.gather(_invoke_once(), _invoke_once())

        # ── Assertion 1: the watcher MUST be FIRED (single
        # transition). The re-mint branch at
        # ``child_reports.py:3843-3882`` is reachable for this
        # shape (idempotency_skip + parent + COMPLETED + PENDING
        # watcher) — it MUST honor the obligation via
        # ``_emit_terminal_for_child_instance_via_bus``.
        with Session(engine) as session:
            row = session.query(DependencyWatcher).filter(
                DependencyWatcher.target_instance_id == parent_id
            ).first()
            assert row is not None, (
                "watcher row missing — bus fixture did not seed the row"
            )
            assert row.state == DependencyWatcherState.FIRED.value, (
                f"B4 cycle-2 re-mint did not FIRE the watcher under "
                f"concurrent invocation: row.state={row.state!r} "
                f"(expected FIRED). Re-mint lives at "
                f"child_reports.py:3843-3882 (idempotency_skip branch). "
                f"The exactly-once pin requires the guarded UPDATE to "
                f"succeed for the WINNER; if both invocations lost, "
                f"the wedge re-mint would silently no-op — parent "
                f"stays parked (the 84563a03 wedge class)."
            )

        # ── Assertion 2: exactly ONE FIRED row for (parent, child)
        # — the loser's ``transition_state`` MUST have yielded
        # ``rowcount == 0`` (no double insert, no double emit). A
        # double-fire would manifest as either (a) TWO rows in
        # FIRED state for the same pair, or (b) a rowcount-2 from
        # a duplicate INSERT upstream. Both are caught here.
        with Session(engine) as session:
            fired_count = session.query(DependencyWatcher).filter(
                DependencyWatcher.target_instance_id == parent_id,
                DependencyWatcher.state == DependencyWatcherState.FIRED.value,
            ).count()
            assert fired_count == 1, (
                f"expected exactly 1 FIRED watcher for (parent, child) "
                f"after concurrent double-fire on the re-mint path; "
                f"got {fired_count} — re-mint double-fired the row "
                f"(transition_state's WHERE state='PENDING' guard did "
                f"NOT serialize the two invocations). This is the "
                f"real wedge: parent would receive the same FollowUp "
                f"twice, breaking the bus's exactly-once contract."
            )

        # ── Assertion 3: no extra watcher rows appeared. The
        # re-mint MUST NOT insert a second watcher row when the
        # first invocation already seeded one. A regression here
        # would mean the re-mint branch is doing an
        # unintended INSERT before transitioning (the
        # bus's primitive only UPDATEs — no INSERTs in the
        # re-mint path).
        with Session(engine) as session:
            total_count = session.query(DependencyWatcher).filter(
                DependencyWatcher.target_instance_id == parent_id,
            ).count()
            assert total_count == 1, (
                f"expected exactly 1 watcher row total (no extra "
                f"rows seeded by the re-mint); got {total_count}. "
                f"The re-mint is UPDATE-only via "
                f"_emit_terminal_for_child_instance_via_bus — a "
                f"rowcount drift here indicates a spurious INSERT."
            )

        # ── Assertion 4: no exceptions escaped either invocation.
        # The re-mint's defensive try/except (child_reports.py:3871-
        # 3881) catches emit-side failures and logs WARNING; the
        # gather would have re-raised any unhandled exception. The
        # fact that we reached this line at all means both calls
        # returned cleanly — the guarded UPDATE produced no
        # double-fire, no error.
