"""NR-1 — incident-repro integration scaffold: silent-death detectability.

Wave 1 no-regret item (phase2-plan §3.3 NR-1, §6 adjustment 2026-08-30):
reproduces the 11-hop premature-completion SHAPE (technical-analysis
§"11-Hop Premature-Completion Chain", compressed to its detectable core):

    hop shape  parent declared-waiting
            → child emits a zero-tool no-work opener
            → child goes COMPLETED
            → parent completes while the child is terminal-and-unnoticed

Coherence contract (§6 adjustment): this scaffold asserts the scenario is
**DETECTABLE** — the NR-3 junk-rate counter (``report_integrity_junk_report_total``)
fires and the parent-state shape shows the unnoticed residue — NOT that any
candidate prevents it. Prevention assertions belong to the Wave-2 (b) tests
(B.S.x), not here. The scaffold assumes the CLASS, not the fix: RED on base
code (no counter ⇒ detectability assert fails), GREEN once NR-3 lands.

HERMETIC: real in-memory SQLite (StaticPool), real ChildReportsService
completion path, mocked checkpoint fetch (no live LLM). Unmarked — runs in
the default pytest gate (mirror test_api_messages.py / test_boot_report_recovery.py).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlalchemy import update as sa_update
from sqlmodel import Session, SQLModel, select as sm_select

# Model imports — required so SQLModel.metadata sees the tables when
# create_all() runs on the test engine.
from daemon.repositories.dependency_bus.models import DependencyWatcher  # noqa: F401
from daemon.repositories.dependency_bus.repository import DependencyWatcherRepository
from daemon.repositories.event.models import Event  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import JobItem  # noqa: F401
from daemon.repositories.event.models import Event, EventKind  # noqa: F401
from daemon.repositories.task.models import Task  # noqa: F401
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import DependencyBus, set_dependency_bus
from daemon.write_pause_guard import WritePauseGuard


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures (mirror tests/unit/services/test_child_reports.py — hermetic)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite engine (StaticPool for cross-thread safety)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(autouse=True)
def bus(engine: Engine):
    """Started DependencyBus bound to the test engine (autouse).

    ``_process_child_completion_db_sync`` raises a hard error when the bus
    singleton is None (bus is the sole completion authority — A8).
    """
    import asyncio

    repo = DependencyWatcherRepository(engine)
    b = DependencyBus(repo)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                ex.submit(asyncio.run, b.start()).result()
        else:
            loop.run_until_complete(b.start())
    except RuntimeError:
        asyncio.run(b.start())
    set_dependency_bus(b)
    try:
        yield b
    finally:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    ex.submit(asyncio.run, b.stop()).result()
            else:
                loop.run_until_complete(b.stop())
        except RuntimeError:
            asyncio.run(b.stop())
        set_dependency_bus(None)


@pytest.fixture(autouse=True)
def _reset_junk_counter():
    """Zero the NR-3 counter around every scaffold test (clean deltas)."""
    from daemon.services import report_integrity_metrics as rim

    rim.reset_junk_report_total()
    try:
        yield
    finally:
        rim.reset_junk_report_total()


# ─────────────────────────────────────────────────────────────────────────────
# Seeds + service builder
# ─────────────────────────────────────────────────────────────────────────────


def _seed_parent_declared_waiting(engine: Engine) -> str:
    """Insert the parent root instance in the declared-waiting shape.

    WAITING_CHILDREN = the parent has declared it is waiting on child work
    (display status; the bus is the authoritative completion signal).
    """
    pid = f"parent-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=pid,
                agent_id="leader",
                agent_name="leader",
                agent_dir="/tmp/leader",
                parent_id=None,
                status=InstanceStatus.WAITING_CHILDREN.value,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return pid


def _seed_child_running(engine: Engine, parent_id: str) -> str:
    """Insert the child instance (RUNNING, parent-linked, no live work rows).

    No Task/message rows: the child has FINISHED its turn (that is the
    scenario — it terminated normally with a zero-tool junk report). A
    live task row would trip the ``child_still_running_defer`` guard and
    the child would never finalize.
    """
    cid = f"child-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=cid,
                agent_id="worker",
                agent_name="worker",
                agent_dir="/tmp/worker",
                parent_id=parent_id,
                status=InstanceStatus.RUNNING.value,
                version=2,
                instance_metadata={},
            )
        )
        session.commit()
    return cid


def _junk_checkpoint_history() -> list[dict]:
    """The child's checkpoint history: a task + a ZERO-TOOL no-work opener.

    This is the silent-death evidence shape (D2.18 LOCKED: work signal =
    last assistant message's ``tool_calls`` empty): the child's only
    assistant turn did no tool work, yet the turn terminated "normally".
    """
    return [
        {"role": "user", "content": "Investigate the flaky queue test"},
        {
            "role": "assistant",
            "content": "I'll take a look at this now.",
            "tool_calls": [],
        },
    ]


def _build_service(engine: Engine, history: list[dict]) -> ChildReportsService:
    """Real ChildReportsService + mock manager over the test engine.

    The checkpoint fetch is patched to return ``history``; everything else
    (engine, write guard, bus) is real — the completion path runs for real.
    """
    from daemon.config import Config

    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager.config = Config()
    adapter = MagicMock(name="CheckpointerAdapter")
    adapter.raw_saver = MagicMock(name="RawSaver")
    manager._checkpointer = adapter
    manager._live_hub = None  # SSE no-op (guarded on truthiness)
    manager._queue_repository = MagicMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None
    return service


# ─────────────────────────────────────────────────────────────────────────────
# The repro
# ─────────────────────────────────────────────────────────────────────────────


async def test_silent_death_zero_tool_child_report_is_detectable(
    engine: Engine,
):
    """Parent declared-waiting → child junk opener → child COMPLETED →
    the parent-side state shows the unnoticed residue — and the NR-3
    counter + (c) marker make the scenario DETECTABLE.

    RED on base code (no counter module ⇒ detectability unassertable);
    GREEN once NR-3/(c) land. This test does NOT assert prevention —
    Wave-2 (b) owns that.
    """
    from daemon.services import report_integrity_metrics as rim
    from daemon.constants import REPORT_SANITY_MARKER as SANITY_MARKER_LITERAL

    parent_id = _seed_parent_declared_waiting(engine)
    child_id = _seed_child_running(engine, parent_id)
    history = _junk_checkpoint_history()
    service = _build_service(engine, history)

    completed_message_id = f"msg-{uuid.uuid4().hex[:8]}"

    # ── Child emits its report: the zero-tool junk opener ──────────────
    # (Terminal completion path — skip_repair defaults False, exactly as
    # ``_process_child_completion_and_notify_parent`` calls it.)
    with patch(
        "daemon.services.child_reports.get_instance_messages",
        new=AsyncMock(return_value=history),
    ):
        report_content = await service._get_last_assistant_message(
            child_id, "worker"
        )

    # DETECTABILITY 1 — NR-3 counter fired for this terminal junk report.
    assert rim.get_junk_report_total() == 1, (
        "NR-3 junk-rate counter must fire when a terminal completion "
        "carries zero-tool-call evidence in a short history — without it "
        "the silent-death class is invisible (phase2-plan §3.3 NR-1 §6 "
        "adjustment)"
    )

    # DETECTABILITY 2 — the (c) marker rides the report envelope, so the
    # parent-side consumer CAN see the zero-evidence signal (descriptive
    # only — no behavior claim).
    assert report_content is not None
    assert SANITY_MARKER_LITERAL in report_content, (
        "(c) marker must ride the terminal report envelope so the junk "
        "shape is visible at the parent boundary (D2.9)"
    )

    # ── Child finalizes: the REAL completion path (bus authority) ──────
    result = service._process_child_completion_db_sync(
        instance_id=child_id,
        completed_message_id=completed_message_id,
        last_content=report_content,
    )

    # The junk report flowed through the normal completion path — the
    # system accepted it (this is the CLASS we are instrumenting; the
    # scaffold does not gate it — that is Wave-2 (b)).
    assert result.outcome == "regular_child_completed", (
        f"expected the regular child-completion outcome (report committed, "
        f"parent notify queued); got outcome={result.outcome!r}"
    )
    assert result.parent_id == parent_id

    # PARENT-STATE SHAPE — the unnoticed residue, in production order:
    # the junk report row is committed FIRST (above), and the child's
    # terminal stamp flows downstream through the report ``Task``
    # (PROCESS_REPORT) → observer lane (see
    # ``_dispatch_post_commit_side_effects`` docstring — "the observer
    # handles terminal transitions"). Reproduce that downstream stamp
    # with the same guarded transition shape the lifecycle paths use
    # (where-not-in terminal set), then assert the residue.
    with Session(engine) as session:
        session.execute(
            sa_update(Instance)
            .where(Instance.instance_id == child_id)
            .where(
                Instance.status.notin_([
                    InstanceStatus.PAUSED.value,
                    InstanceStatus.COMPLETED.value,
                    InstanceStatus.ERROR.value,
                ])
            )
            .values(status=InstanceStatus.COMPLETED.value)
        )
        session.commit()

    with Session(engine) as session:
        child_row = session.get(Instance, child_id)
        assert child_row.status == InstanceStatus.COMPLETED.value, (
            "child must reach COMPLETED (the silent-death shape: terminal "
            "with a junk report)"
        )
        parent_row = session.get(Instance, parent_id)
        assert parent_row.status == InstanceStatus.WAITING_CHILDREN.value, (
            "parent remains declared-waiting with a terminal child + junk "
            "report — the unnoticed state this scaffold exists to expose"
        )
        # The committed parent-side signal: a CHILD_COMPLETED event row for
        # the parent (the report delivery lane's remaining residue in this
        # hermetic harness — the report message/injection rows of the
        # regular lane are the daemon's pre-existing savepoint/idempotency
        # territory and are intentionally NOT re-litigated here; the
        # marker's presence is asserted on the CONTENT passed into the
        # machinery above, which is what every delivery lane carries).
        from daemon.repositories.event.models import EventKind

        parent_events = session.exec(
            sm_select(Event)
            .where(Event.instance_id == parent_id)
            .where(Event.kind == EventKind.CHILD_COMPLETED.value)
        ).all()
        assert len(parent_events) == 1, (
            "exactly one CHILD_COMPLETED event for the parent; got "
            f"{len(parent_events)}"
        )
        import json as _json

        payload = _json.loads(parent_events[0].data or "{}")
        assert payload.get("child_instance_id") == child_id, (
            "CHILD_COMPLETED event must reference the terminal child — the "
            "unnoticed-residue handle the parent never acted on"
        )


# ─────────────────────────────────────────────────────────────────────────────
# B.S.1-i — (b)-predicate scenario extension (NR-1 §4.0 Wave-2 row, 2026-08-30).
#
# In the same terminal-child-and-unnoticed state the Wave-1 scaffold
# reproduces above, the (b) predicate (decisions.md C2-D2.7 LOCKED)
# MUST return NON-EMPTY with the right child + terminal status. The
# Wave-1 detectability assertions remain intact; this is an
# additive extension. A healthy sibling path must yield EMPTY —
# proving the predicate is content-blind (D2.18) and only fires in
# the incident shape.
#
# Wave-1 detectability assertions above are NOT removed; this
# extension is layered on top per §4.0.
#
# Implementation note (hermetic harness): the regular completion
# path's report-injection INSERT is wrapped in a SAVEPOINT that
# absorbs concurrent-duplicate IntegrityErrors
# (``child_reports.py:3003+``); the existing Wave-1 harness does
# not always land a PENDING row in this fixture because the
# completion transaction's other dependencies
# (``instance_hierarchy``, message-queue ordering, etc.) are
# partial in the hermetic harness. For the (b) predicate scenario
# we stage the PENDING row EXPLICITLY before the predicate call —
# this is exactly the durable obligation the predicate reads, and
# it is the shape ``_process_child_completion_db_sync`` would
# produce in a live run.
# ─────────────────────────────────────────────────────────────────────────────


async def test_b_predicate_fires_in_unnoticed_state(engine: Engine):
    """In the repro's terminal-child-and-unnoticed state, the (b)
    predicate (D2.7 LOCKED) returns NON-EMPTY with the right child
    and terminal status — the Wave-2 B.S.1-i scenario.

    The scenario: parent declared-waiting → child emitted junk →
    child COMPLETED → a PENDING ``report_injections`` obligation
    sits on the durable queue → the parent has not acted. The
    predicate (D2.7 LOCKED) MUST fire here.

    Coherence: Wave-1 detectability assertions (NR-3 counter + (c)
    marker + parent-state shape) remain intact above; this test
    is an additive scenario, not a replacement.
    """
    from daemon.repositories.report_injection.repository import (
        ReportInjectionRepository,
    )
    from daemon.services.report_integrity_guard import (
        evaluate_declared_waiting_violations,
    )

    parent_id = _seed_parent_declared_waiting(engine)
    child_id = _seed_child_running(engine, parent_id)
    history = _junk_checkpoint_history()
    service = _build_service(engine, history)

    completed_message_id = f"msg-{uuid.uuid4().hex[:8]}"

    # Drive the regular completion path so the (c) marker + counter
    # asserts above remain intact (Wave-1 detectability surface).
    with patch(
        "daemon.services.child_reports.get_instance_messages",
        new=AsyncMock(return_value=history),
    ):
        report_content = await service._get_last_assistant_message(
            child_id, "worker"
        )

    result = service._process_child_completion_db_sync(
        instance_id=child_id,
        completed_message_id=completed_message_id,
        last_content=report_content,
    )
    assert result.outcome == "regular_child_completed"

    # Stamp the child COMPLETED (the silent-death residue shape) and
    # stage the PENDING obligation explicitly on the durable row.
    # In a live run, the regular completion path writes this row
    # itself (``child_reports.py:3003+``); here we stage it directly
    # so the predicate evaluates against the exact shape it sees in
    # production.
    report_repo = ReportInjectionRepository(engine)
    with Session(engine) as session:
        session.execute(
            sa_update(Instance)
            .where(Instance.instance_id == child_id)
            .where(
                Instance.status.notin_([
                    InstanceStatus.PAUSED.value,
                    InstanceStatus.COMPLETED.value,
                    InstanceStatus.ERROR.value,
                ])
            )
            .values(status=InstanceStatus.COMPLETED.value)
        )
        session.commit()
        report_repo.enqueue(
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=completed_message_id,
            report_message_id=f"rmsg-{uuid.uuid4().hex[:8]}",
            content="junk opener body",
        )

    # (b) PREDICATE — evaluated on the repro's session, the B.S.7
    # binding contract. The session is the repro's own (StaticPool
    # shared connection); the predicate reads durable rows on it.
    with Session(engine) as session:
        report = evaluate_declared_waiting_violations(session, parent_id)

    # NON-EMPTY with the right child + terminal status (OQ-6).
    assert report.is_violation is True
    assert report.count >= 1
    assert any(
        d["child_instance_id"] == child_id
        and d["child_terminal_status"] == InstanceStatus.COMPLETED.value
        for d in report.pending_with_terminal_child
    ), (
        "(b) predicate MUST surface the terminal child with the COMPLETED "
        "terminal status so stage iii can pick the COMPLETED adjudication "
        "playbook (OQ-6 disposition, decisions.md bottom)"
    )

    # Structured return shape — no leakage of message content /
    # tool_calls (D2.18 content-blind invariant).
    for detail in report.pending_with_terminal_child:
        assert set(detail.keys()) == {
            "injection_id",
            "child_instance_id",
            "state",
            "child_terminal_status",
        }, (
            "D2.18 LOCKED: (b) predicate is content-blind — the structured "
            "return must NOT expose message content or tool_calls"
        )


async def test_b_predicate_dormant_on_healthy_sibling(engine: Engine):
    """A healthy parent (no rows) → predicate returns EMPTY.

    The (b) predicate's healthy-path invariant (D2.7): EMPTY on
    every healthy shape. Asserted with a SIBLING parent that has
    NO report rows and NO watcher rows so the predicate does
    not over-fire on the live repro's parent.
    """
    from daemon.services.report_integrity_guard import (
        evaluate_declared_waiting_violations,
    )

    sibling_id = _seed_parent_declared_waiting(engine)  # distinct from the live parent

    with Session(engine) as session:
        report = evaluate_declared_waiting_violations(session, sibling_id)

    assert report.is_violation is False
    assert report.count == 0
    assert report.pending_with_terminal_child == []
    assert report.fired_unenqueued == []


# ─────────────────────────────────────────────────────────────────────────────
# B.S.1-iii / NR-1 — (b) ENFORCEMENT extension to the incident repro.
#
# Flag ON in the repro's incident shape → the completion stamp is NOT
# blocked (fail-OPEN, D2.6) and ONE adjudication notice is injected to
# the parent via the durable enqueue with the reserved system source.
# Flag OFF (ship default) → LOG-ONLY byte-parity with stage ii: the
# [ReportIntegrityGuard] WARNING still fires, NO notice, NO enqueue.
# ═════════════════════════════════════════════════════════════════════════════


async def test_b_enforcement_notice_injected_with_flag_on(
    engine: Engine, monkeypatch
):
    """Flag ON + incident shape → completion proceeds + notice injected.

    The parent's root completion flows the REAL sync path (stamp
    proceeds → outcome root_completed) and the REAL post-commit
    dispatch, which hands the SAME same-tx evaluation (B.S.7) to the
    enforcement action. The enqueue is observed on the manager mock —
    the durable path (MessageQueue + Task rows) it would drive is the
    ``system:report-integrity-guard`` provenance pinned here.
    """
    from unittest.mock import AsyncMock

    import daemon.services.report_integrity_guard as rig
    from daemon.constants import REPORT_SANITY_MARKER as SANITY_MARKER_LITERAL
    from daemon.repositories.report_injection.repository import (
        ReportInjectionRepository,
    )

    # Flip the kill-switch BEFORE building the service so the boot-loaded
    # config section AND the env resolver both read ON (dual-read gate).
    monkeypatch.setenv(
        "WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED", "1"
    )
    monkeypatch.setattr(rig, "_B_GUARD_ENABLED", None)
    monkeypatch.setattr(rig, "_B_NOTICE_LEDGER", {})
    assert rig.resolve_report_integrity_b_guard_enabled() is True

    parent_id = _seed_parent_declared_waiting(engine)
    child_id = _seed_child_running(engine, parent_id)
    history = _junk_checkpoint_history()
    service = _build_service(engine, history)
    service._manager.enqueue_message = AsyncMock()

    completed_message_id = f"msg-{uuid.uuid4().hex[:8]}"

    # The junk report (zero-tool opener) — the (c) marker rides it.
    with patch(
        "daemon.services.child_reports.get_instance_messages",
        new=AsyncMock(return_value=history),
    ):
        report_content = await service._get_last_assistant_message(
            child_id, "worker"
        )
    assert report_content is not None
    assert SANITY_MARKER_LITERAL in report_content

    # The child's regular completion + terminal stamp + staged PENDING
    # obligation (the same incident shape the Wave-1 scaffold builds).
    result = service._process_child_completion_db_sync(
        instance_id=child_id,
        completed_message_id=completed_message_id,
        last_content=report_content,
    )
    assert result.outcome == "regular_child_completed"

    report_repo = ReportInjectionRepository(engine)
    with Session(engine) as session:
        session.execute(
            sa_update(Instance)
            .where(Instance.instance_id == child_id)
            .where(
                Instance.status.notin_([
                    InstanceStatus.PAUSED.value,
                    InstanceStatus.COMPLETED.value,
                    InstanceStatus.ERROR.value,
                ])
            )
            .values(status=InstanceStatus.COMPLETED.value)
        )
        session.commit()
        report_repo.enqueue(
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=completed_message_id,
            report_message_id=f"rmsg-{uuid.uuid4().hex[:8]}",
            content="junk opener body",
        )

    # ── The parent's root completion through the REAL path ─────────────
    stamp_result = service._process_child_completion_db_sync(
        instance_id=parent_id,
        completed_message_id="msg-unrelated",
        last_content="parent wrap-up text",
    )

    # COMPLETION NOT BLOCKED (fail-OPEN): the stamp proceeded.
    assert stamp_result.outcome == "root_completed", (
        f"(b) must NEVER block the completion (D2.6); got "
        f"{stamp_result.outcome!r}"
    )
    with Session(engine) as session:
        row = session.get(Instance, parent_id)
        assert row.status == InstanceStatus.COMPLETED.value

    # The same-tx evaluation found the violation and rode the result.
    assert stamp_result.b_violation_report is not None
    assert stamp_result.b_violation_report.is_violation is True

    # Post-commit dispatch → enforcement → ONE notice, durable source.
    await service._dispatch_post_commit_side_effects(
        stamp_result, "parent wrap-up text", "msg-unrelated"
    )
    service._manager.enqueue_message.assert_awaited_once()
    kwargs = service._manager.enqueue_message.await_args.kwargs
    assert kwargs["instance_id"] == parent_id
    assert kwargs["source"] == "system:report-integrity-guard", (
        "the notice must carry the reserved system source "
        "(RESERVED_SOURCE_PREFIXES + the system:* dispatch-source guard)"
    )
    assert kwargs["priority"] == 0
    assert kwargs["metadata"]["report_integrity_notice"] is True
    assert child_id in kwargs["message"], (
        "the notice must name the terminal child (per-child detail)"
    )
    assert SANITY_MARKER_LITERAL in kwargs["message"], (
        "the notice must cite the (c) marker pattern (D2.9)"
    )
    assert "[SYSTEM NOTE]" not in kwargs["message"], (
        "C2-D2.2: never inside the [SYSTEM NOTE] frame"
    )


async def test_b_flag_off_log_only_byte_parity_with_stage_ii(
    engine: Engine, monkeypatch, caplog
):
    """Flag OFF (ship default) in the SAME incident shape → LOG-ONLY:
    the stage-ii [ReportIntegrityGuard] WARNING fires at the stamp, the
    completion proceeds exactly as stage ii, and NO notice is ever
    attempted (byte-parity with the stage-ii behavior).
    """
    import logging

    from unittest.mock import AsyncMock

    import daemon.services.report_integrity_guard as rig
    from daemon.repositories.report_injection.repository import (
        ReportInjectionRepository,
    )

    monkeypatch.delenv(
        "WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED",
        raising=False,
    )
    monkeypatch.setattr(rig, "_B_GUARD_ENABLED", None)
    monkeypatch.setattr(rig, "_B_NOTICE_LEDGER", {})

    parent_id = _seed_parent_declared_waiting(engine)
    child_id = _seed_child_running(engine, parent_id)
    history = _junk_checkpoint_history()
    service = _build_service(engine, history)
    service._manager.enqueue_message = AsyncMock()

    completed_message_id = f"msg-{uuid.uuid4().hex[:8]}"
    with patch(
        "daemon.services.child_reports.get_instance_messages",
        new=AsyncMock(return_value=history),
    ):
        report_content = await service._get_last_assistant_message(
            child_id, "worker"
        )

    result = service._process_child_completion_db_sync(
        instance_id=child_id,
        completed_message_id=completed_message_id,
        last_content=report_content,
    )
    assert result.outcome == "regular_child_completed"

    report_repo = ReportInjectionRepository(engine)
    with Session(engine) as session:
        session.execute(
            sa_update(Instance)
            .where(Instance.instance_id == child_id)
            .where(
                Instance.status.notin_([
                    InstanceStatus.PAUSED.value,
                    InstanceStatus.COMPLETED.value,
                    InstanceStatus.ERROR.value,
                ])
            )
            .values(status=InstanceStatus.COMPLETED.value)
        )
        session.commit()
        report_repo.enqueue(
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=completed_message_id,
            report_message_id=f"rmsg-{uuid.uuid4().hex[:8]}",
            content="junk opener body",
        )

    with caplog.at_level(
        logging.INFO, logger="daemon.services.report_integrity_guard"
    ):
        stamp_result = service._process_child_completion_db_sync(
            instance_id=parent_id,
            completed_message_id="msg-unrelated",
            last_content="parent wrap-up text",
        )
        await service._dispatch_post_commit_side_effects(
            stamp_result, "parent wrap-up text", "msg-unrelated"
        )

    # Stage-ii behavior preserved: stamp proceeded, soak log fired.
    assert stamp_result.outcome == "root_completed"
    guard_logs = [
        r
        for r in caplog.records
        if "declared-waiting violation" in r.getMessage()
    ]
    assert len(guard_logs) == 1, (
        f"stage-ii soak log must fire exactly once; got "
        f"{[r.getMessage() for r in caplog.records]}"
    )

    # NO notice: zero enforcement logs, zero enqueue attempts, empty ledger.
    assert not [
        r
        for r in caplog.records
        if "enforcement notice enqueued" in r.getMessage()
    ]
    service._manager.enqueue_message.assert_not_called()
    assert parent_id not in rig._B_NOTICE_LEDGER
