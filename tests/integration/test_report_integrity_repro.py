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
