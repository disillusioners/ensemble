"""P2 "OFF means OFF" behavioral probe — WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED.

Gate-authored evidence (P2 report-integrity gate, branch
feature/wc-wake-report-integrity). The shipping posture is the flag OFF
(unset). This probe proves BEHAVIORALLY — on the REAL completion path,
not via flag-reading introspection — that OFF means OFF:

1. Incident shape seeded in durable rows (report_injections PENDING with a
   terminal child + a FIRED-unenqueued dependency_watchers row), then the
   REAL observer finalize site (``JobFeedbackObserver._finalize_job`` —
   the production parent-COMPLETED path at
   ``daemon/services/job_feedback_observer.py`` observer_finalize_job)
   runs with the flag UNSET:
     a. exactly ONE ``[ReportIntegrityGuard]`` log line (the stage-ii
        declared-waiting violation WARNING) — zero predicate-FAILED /
        MALFORMED / enforcement lines;
     b. ZERO durable writes by the guard: no ``message_queue`` rows
        (there are no rows at all, and none with source
        ``system:report-integrity-guard``), no Task/JobItem rows created,
        ``_B_NOTICE_LEDGER`` stays empty, and the seeded
        report_injections / dependency_watchers rows are byte-identical
        before/after;
     c. completion PROCEEDS: the COMPLETED stamp lands on the parent;
     d. NO gate delay: the enforcement short-circuit returns near-
        instantly (< 100 ms) and ``asyncio.wait_for`` (the
        NOTICE_ENQUEUE_BUDGET wrapper) is NEVER awaited — the flag-off
        branch returns before the budget.
3. Parity: the same scenario with the flag explicitly ``0`` behaves
   identically (same single log line, same zero writes, same completion).
4. ON-state contrast control: flag ON + the SAME shape → the enforcement
   DOES fire (one notice, ledger populated) — giving the OFF assertions
   teeth (they are discriminating, not tautological).
5. S3 scoping spot: with the flag OFF, the ALWAYS-ON Wave-1 instruments
   still fire — a junk-shape report gets the ``[REPORT SANITY: ...]``
   marker appended (suffix-append only, content semantics unchanged,
   nothing blocked) and the NR-3 junk counter increments — proving the
   B-flag gates ONLY the (b) enforcement, NOT (c)/NR-3.

HERMETIC: file-backed SQLite in a tmp_path, real repositories, real
WriteGuardSession/predicate/enforcement code paths; the observer is built
via ``__new__`` with a minimal manager stub carrying ONLY the attributes
the live site reads (mirror tests/unit/services/test_observer_finalize_no_job.py);
no full daemon start, no network. Unmarked — runs in the default pytest
gate (mirror test_report_integrity_repro.py).

W1-pollution lesson: module-global state (resolver cache ``_B_GUARD_ENABLED``,
notice ledger ``_B_NOTICE_LEDGER``, junk counter) is reset via the SAME
module object the production site uses (``import daemon.services
.report_integrity_guard as rig`` — the observer imports the functions from
that module, so attribute writes are observed at call time).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select as sm_select

# Model imports — required so SQLModel.metadata sees the tables when
# create_all() runs on the test engine (mirror the repro scaffold).
from daemon.constants import (
    REPORT_SANITY_MARKER,
    WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED,
)
from daemon.repositories.dependency_bus.models import (  # noqa: F401
    DependencyWatcher,
    DependencyWatcherState,
)
from daemon.repositories.event.models import Event  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import JobItem  # noqa: F401
from daemon.repositories.message_queue.models import MessageQueue  # noqa: F401
from daemon.repositories.report_injection.models import ReportInjection  # noqa: F401
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.repositories.task.models import Task  # noqa: F401
from daemon.services import job_feedback_observer as _observer_module
from daemon.services import report_integrity_guard as rig
from daemon.services import report_integrity_metrics as rim
from daemon.services.child_reports import ChildReportsService
from daemon.services.job_feedback_observer import (
    JobFeedbackObserver,
    _ProcessingJobContext,
)
from daemon.write_pause_guard import WritePauseGuard

# The reserved system source the guard would enqueue under — imported (NOT
# forked) so a constant rename fails this probe loudly.
_GUARD_NOTICE_SOURCE = rig.REPORT_INTEGRITY_GUARD_NOTICE_SOURCE

# Delay ceiling for the enforcement SHORT-CIRCUIT path with the flag OFF.
# The flag-off branch is a boolean AND + return False — microseconds in
# practice; 100 ms is the generous gate bound from the probe spec.
_SHORT_CIRCUIT_BUDGET_S = 0.100

# Sanity ceiling for the whole guarded finalize call. The failure mode this
# ceiling catches is the NOTICE_ENQUEUE_BUDGET (5 s) being awaited — i.e.
# the flag-off branch NOT short-circuiting.
_FULL_FINALIZE_SANITY_S = 5.0


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — hermetic engine + module-global hygiene (W1 lesson)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path):
    """FILE-BACKED SQLite engine (tmp dir) — real on-disk durable rows."""
    db_path = tmp_path / "ri_off_probe.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(autouse=True)
def _rig_module_hygiene(monkeypatch):
    """Reset the guard's module-global caches around EVERY test (W1 lesson).

    ``_B_GUARD_ENABLED`` is the resolve-once cache; ``_B_NOTICE_LEDGER`` is
    the per-episode dedupe map. Both live as module attributes on the SAME
    module object the production site imports from, so resetting via
    ``rig.<attr>`` is observed by the real call path. monkeypatch restores
    the original attributes on teardown.
    """
    monkeypatch.setattr(rig, "_B_GUARD_ENABLED", None)
    monkeypatch.setattr(rig, "_B_NOTICE_LEDGER", {})
    yield


@pytest.fixture(autouse=True)
def _junk_counter_hygiene():
    """Zero the NR-3 counter around every test — clean deltas only."""
    rim.reset_junk_report_total()
    yield
    rim.reset_junk_report_total()


def _force_flag_off(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """Put the kill-switch env in one of the two OFF shapes."""
    assert mode in ("unset", "explicit_zero")
    if mode == "unset":
        monkeypatch.delenv(WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED, raising=False)
    else:
        monkeypatch.setenv(WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED, "0")
    # The resolver cache is None (fixture) — the NEXT real read resolves
    # from THIS env state, exactly as a fresh daemon boot would.


@pytest.fixture
def wire_bus_gate():
    """Wire the minimal bus-gate surface the REAL finalize path requires.

    The in-session bus gate inside ``_finalize_job_db_sync`` HARD-FAILS on a
    None singleton (A9 — fail-CLOSED), and the async wrapper takes the
    per-parent lock + generation snapshot from the bus. The stub carries
    exactly those members: ``get_generation`` → stable 0 (no orphan-race
    re-arm), ``_get_parent_lock`` → a real per-test asyncio.Lock,
    ``count_pending_for_target_sync`` → 0. The PENDING-watcher COUNT itself
    runs as a REAL in-session SQL query against the test engine (my seeded
    watcher is FIRED, not PENDING, so the gate reads zero and the finalize
    proceeds to the COMPLETED stamp).
    """
    from daemon.services.dependency_bus import set_dependency_bus

    bus = MagicMock(name="DependencyBus")
    bus.get_generation = lambda _iid: 0
    bus.count_pending_for_target_sync = lambda _iid: 0
    parent_lock = asyncio.Lock()

    async def _get_parent_lock(_iid: str) -> asyncio.Lock:
        return parent_lock

    bus._get_parent_lock = _get_parent_lock
    set_dependency_bus(bus)
    try:
        yield bus
    finally:
        set_dependency_bus(None)


# ─────────────────────────────────────────────────────────────────────────────
# Seed builders — the incident shape, in durable rows
# ─────────────────────────────────────────────────────────────────────────────


def _seed_parent_declared_waiting(engine: Engine) -> str:
    """Parent root instance in the declared-waiting shape (non-terminal)."""
    pid = f"parent-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=pid,
                agent_id="leader",
                agent_name="leader",
                agent_dir="/tmp/agents/leader",
                project_id="probe-project",
                parent_id=None,
                status=InstanceStatus.WAITING_CHILDREN.value,
                version=1,
                instance_metadata={},
                created_at=now_iso,
                updated_at=now_iso,
                paused_at=None,
            )
        )
        session.commit()
    return pid


def _seed_terminal_child(engine: Engine, parent_id: str) -> str:
    """Child instance ALREADY terminal (COMPLETED) — the incident shape."""
    cid = f"child-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=cid,
                agent_id="worker",
                agent_name="worker",
                agent_dir="/tmp/agents/worker",
                project_id="probe-project",
                parent_id=parent_id,
                status=InstanceStatus.COMPLETED.value,
                version=2,
                instance_metadata={},
                created_at=now_iso,
                updated_at=now_iso,
                paused_at=None,
            )
        )
        session.commit()
    return cid


def _seed_pending_injection(engine: Engine, parent_id: str, child_id: str) -> str:
    """Stage a PENDING report_injections row (the PRIMARY predicate signal)."""
    repo = ReportInjectionRepository(engine)
    row = repo.enqueue(
        parent_instance_id=parent_id,
        child_instance_id=child_id,
        child_message_id=f"msg-{uuid.uuid4().hex[:8]}",
        report_message_id=f"rmsg-{uuid.uuid4().hex[:8]}",
        content="junk opener body",
    )
    return row.injection_id


def _seed_fired_unenqueued_watcher(engine: Engine, parent_id: str) -> str:
    """Stage a FIRED ∧ enqueued_at IS NULL watcher (the CORROBORATING signal)."""
    watch_id = f"watch-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            DependencyWatcher(
                watch_id=watch_id,
                source_task_id=f"task-{uuid.uuid4().hex[:8]}",
                target_instance_id=parent_id,
                state=DependencyWatcherState.FIRED.value,
                fired_at=now_iso,
                enqueued_at=None,
                follow_up_payload={"kind": "ri_off_probe"},
                watcher_metadata={"kind": "ri_off_probe"},
            )
        )
        session.commit()
    return watch_id


def _snapshot_guard_touchable_state(engine: Engine) -> dict[str, Any]:
    """Capture every durable surface the guard could possibly mutate."""
    with Session(engine) as session:
        injections = [
            (r.injection_id, r.state, r.child_message_id, r.report_message_id)
            for r in session.exec(sm_select(ReportInjection)).all()
        ]
        watchers = [
            (w.watch_id, w.state, w.fired_at, w.enqueued_at)
            for w in session.exec(sm_select(DependencyWatcher)).all()
        ]
        return {
            "injections": injections,
            "watchers": watchers,
            "message_queue_rows": len(
                list(session.exec(sm_select(MessageQueue)).all())
            ),
            "message_queue_guard_source": len(
                list(
                    session.exec(
                        sm_select(MessageQueue).where(
                            MessageQueue.source == _GUARD_NOTICE_SOURCE
                        )
                    ).all()
                )
            ),
            "task_rows": len(list(session.exec(sm_select(Task)).all())),
            "jobitem_rows": len(list(session.exec(sm_select(JobItem)).all())),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Harness — the REAL observer finalize site with a minimal manager stub
# ─────────────────────────────────────────────────────────────────────────────


def _build_observer(engine: Engine):
    """Real ``JobFeedbackObserver`` driving the REAL async finalize site.

    ``__new__`` construction (mirror test_observer_finalize_no_job.py): the
    stub carries ONLY the attributes the live site actually reads —
    ``engine`` / ``write_guard`` / ``is_write_paused`` (WriteGuardSession),
    ``_get_last_assistant_message_raw`` (pre-fetch), ``_task_repo=None``
    (post-D13 MESSAGE path skips it). ``job_id=None`` keeps Step 1,
    notify_watchers, and _trigger_next_job skipped. The SSE/registry
    fan-out dispatcher is stubbed — it is NOT part of the (b) surface.
    The bus singleton stays unwired (legacy path — no lock needed).
    """
    observer = JobFeedbackObserver.__new__(JobFeedbackObserver)
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager.is_write_paused = False
    manager._task_repo = None
    manager._get_last_assistant_message_raw = AsyncMock(
        return_value="parent wrap-up text"
    )
    # Recorder (NOT a raise-trap): if the guard ever enqueues despite OFF,
    # an await_count > 0 is EVIDENCE — an exception here would be absorbed
    # by the guard's fail-OPEN and could mask the violation.
    enqueue_spy = AsyncMock(name="enqueue_message", return_value=None)
    manager.enqueue_message = enqueue_spy

    observer._instance_manager = manager
    observer._bus_count_pending_for_target_sync = lambda _iid: 0
    dispatch_stub = AsyncMock(name="dispatch_instance_post_commit")
    observer._dispatch_instance_post_commit_side_effects = dispatch_stub
    # N8 (mission-class, 2026-09-03) unconditional getattr at
    # job_feedback_observer.py:1862-1864 reads self._job_queue_service
    # BEFORE the per-attr getattr default — the production __init__ at
    # :317-328 (api.py:649-657) sets it; __new__ construction here does
    # not. Stub it with a minimal namespace whose _work_resolver is None
    # so the legacy fallback path runs (per-kind token = default token).
    observer._job_queue_service = SimpleNamespace(_work_resolver=None)
    return observer, manager, enqueue_spy, dispatch_stub


@contextmanager
def _spy_asyncio_wait_for():
    """Observe EVERY ``asyncio.wait_for`` await in a tight window.

    The enforcement wraps its enqueue in
    ``asyncio.wait_for(..., timeout=NOTICE_ENQUEUE_BUDGET_SECONDS)``. With
    the flag OFF the branch must return BEFORE the budget — so the spy must
    record ZERO calls. Manually scoped swap (not monkeypatch) so pytest-
    asyncio internals outside the window are untouched.
    """
    calls: list[Any] = []
    real = asyncio.wait_for

    async def _spy(fut, timeout=None):
        calls.append(timeout)
        return await real(fut, timeout=timeout)

    asyncio.wait_for = _spy
    try:
        yield calls
    finally:
        asyncio.wait_for = real


async def _drive_real_finalize(observer: JobFeedbackObserver, parent_id: str):
    """Run the REAL async finalize path (observer_finalize_job site)."""
    ctx = _ProcessingJobContext(instance_id=parent_id, job_id=None)
    await observer._finalize_job(
        ctx,
        parent_id,
        InstanceStatus.COMPLETED.value,
        None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Sub-cases 1–3: OFF means OFF on the REAL completion path (unset ∥ "0")
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("off_mode", ["unset", "explicit_zero"])
async def test_off_means_off_on_real_completion_path(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    wire_bus_gate,
    off_mode: str,
):
    """Flag OFF (unset AND explicit "0") + incident shape → stage-ii log
    ONLY: one guard line, zero durable writes, completion proceeds, no
    budget await, ledger empty."""
    _force_flag_off(monkeypatch, off_mode)

    # ── Seed the incident shape ──────────────────────────────────────────
    parent_id = _seed_parent_declared_waiting(engine)
    child_id = _seed_terminal_child(engine, parent_id)
    _seed_pending_injection(engine, parent_id, child_id)
    watch_id = _seed_fired_unenqueued_watcher(engine, parent_id)

    # Precondition sanity: the REAL predicate fires on this shape (if this
    # fails the probe is testing nothing — fail loud, not vacuously).
    with Session(engine) as session:
        report = rig.evaluate_declared_waiting_violations(session, parent_id)
    assert report.is_violation is True, (
        "probe precondition broken: seeded incident shape must make the "
        "declared-waiting predicate fire (PRIMARY + CORROBORATING rows)"
    )
    assert any(
        d["child_instance_id"] == child_id
        for d in report.pending_with_terminal_child
    )
    assert any(d["watch_id"] == watch_id for d in report.fired_unenqueued)

    observer, manager, enqueue_spy, dispatch_stub = _build_observer(engine)
    before = _snapshot_guard_touchable_state(engine)

    # ── Drive the REAL completion path with the flag OFF ─────────────────
    with caplog.at_level(logging.DEBUG), _spy_asyncio_wait_for() as wait_calls:
        t0 = time.perf_counter()
        await _drive_real_finalize(observer, parent_id)
        full_s = time.perf_counter() - t0
        assert wait_calls == [], (
            "the flag-OFF branch returned before the NOTICE budget in "
            "production — but asyncio.wait_for WAS awaited inside the "
            "finalize window; this is a GATE BLOCKER (hidden enforcement)"
        )

    # (2c) Completion PROCEEDS — the COMPLETED stamp landed on the parent.
    with Session(engine) as session:
        parent_row = session.get(Instance, parent_id)
        assert parent_row is not None
        assert parent_row.status == InstanceStatus.COMPLETED.value, (
            "guard must NEVER block completion (D2.6 fail-OPEN); parent "
            f"status={parent_row.status!r}"
        )

    # (2a) Exactly ONE [ReportIntegrityGuard] line — the stage-ii soak
    # WARNING. Zero predicate-FAILED / MALFORMED / enforcement lines.
    guard_records = [
        r for r in caplog.records if "[ReportIntegrityGuard]" in r.getMessage()
    ]
    assert len(guard_records) == 1, (
        f"expected exactly ONE [ReportIntegrityGuard] line with the flag "
        f"{off_mode}; got {len(guard_records)}: "
        f"{[r.getMessage() for r in guard_records]}"
    )
    only = guard_records[0]
    assert only.levelno == logging.WARNING
    assert "declared-waiting violation" in only.getMessage()
    assert "observer_finalize_job" in only.getMessage(), (
        "the single guard line must be the stage-ii soak log attributed to "
        "the REAL completion site"
    )
    assert not [
        r for r in caplog.records if "predicate FAILED" in r.getMessage()
    ]
    assert not [
        r for r in caplog.records if "MALFORMED" in r.getMessage()
    ]
    assert not [
        r
        for r in caplog.records
        if "enforcement notice enqueued" in r.getMessage()
    ]

    # (2b) ZERO durable writes by the guard.
    after = _snapshot_guard_touchable_state(engine)
    assert after["message_queue_guard_source"] == 0, (
        "no message_queue row may carry the guard's reserved source with "
        f"the flag {off_mode}"
    )
    assert after["message_queue_rows"] == before["message_queue_rows"] == 0, (
        "the guarded completion must not enqueue ANY message with the "
        f"flag {off_mode}"
    )
    assert after["task_rows"] == before["task_rows"], (
        "no Task row may be created by the guard path"
    )
    assert after["jobitem_rows"] == before["jobitem_rows"] == 0, (
        "no JobItem row may be created by the guard path"
    )
    assert after["injections"] == before["injections"], (
        "report_injections rows must be untouched by the guard "
        f"(flag {off_mode})"
    )
    assert after["watchers"] == before["watchers"], (
        "dependency_watchers rows must be untouched by the guard "
        f"(flag {off_mode})"
    )
    assert enqueue_spy.await_count == 0, (
        "manager.enqueue_message must never be awaited with the flag OFF"
    )
    assert dict(rig._B_NOTICE_LEDGER) == {}, (
        "_B_NOTICE_LEDGER must stay EMPTY — no notice episode recorded"
    )
    # The stage-ii evaluation RAN (always-on) and rode the result out.
    assert dispatch_stub.await_count == 1, (
        "the instance-side post-commit fan-out still ran — completion "
        "proceeded through the normal path"
    )

    # (2d) No gate delay — the short-circuit is near-instant. Measured
    # DIRECTLY on the enforcement entry point with the SAME violation
    # report the site just produced, so the measurement isolates the
    # flag-off branch (no DB, no enqueue, no budget).
    with _spy_asyncio_wait_for() as wait_calls_direct:
        t1 = time.perf_counter()
        enforced = await rig.enforce_declared_waiting_violations(
            report,
            manager=manager,
            parent_instance_id=parent_id,
            context_tag="ri_off_probe_short_circuit",
        )
        direct_s = time.perf_counter() - t1
    assert enforced is False, (
        "flag OFF → enforcement must report 'no notice enqueued'"
    )
    assert direct_s < _SHORT_CIRCUIT_BUDGET_S, (
        f"flag-OFF short-circuit took {direct_s * 1000:.1f}ms "
        f"(budget {_SHORT_CIRCUIT_BUDGET_S * 1000:.0f}ms) — GATE BLOCKER: "
        "the OFF path is not near-instant"
    )
    assert wait_calls_direct == [], (
        "NOTICE_ENQUEUE_BUDGET was never awaited — the flag-off branch "
        "must return BEFORE the budget wrapper"
    )
    assert full_s < _FULL_FINALIZE_SANITY_S, (
        f"guarded finalize took {full_s:.2f}s (sanity "
        f"{_FULL_FINALIZE_SANITY_S:.0f}s) — consistent with the 5s NOTICE "
        "budget being awaited: GATE BLOCKER"
    )
    # Post-direct-call hygiene: still zero side effects.
    assert dict(rig._B_NOTICE_LEDGER) == {}
    assert enqueue_spy.await_count == 0
    assert _snapshot_guard_touchable_state(engine) == after, (
        "the direct short-circuit call must not mutate any durable row"
    )


# ─────────────────────────────────────────────────────────────────────────────
# ON-state contrast control — gives the OFF assertions TEETH
# ─────────────────────────────────────────────────────────────────────────────


async def test_flag_on_contrast_enforcement_actually_fires(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    wire_bus_gate,
):
    """Control (not a shipping-posture claim): flag ON + the SAME incident
    shape → the enforcement DOES fire (notice enqueued, ledger populated).
    This proves the OFF assertions above are DISCRIMINATING — they fail if
    the OFF branch ever regressed into enforcement — i.e. "OFF means OFF"
    is a real behavioral difference, not a tautology."""
    monkeypatch.setenv(WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED, "1")

    parent_id = _seed_parent_declared_waiting(engine)
    child_id = _seed_terminal_child(engine, parent_id)
    _seed_pending_injection(engine, parent_id, child_id)
    _seed_fired_unenqueued_watcher(engine, parent_id)

    observer, manager, enqueue_spy, _dispatch_stub = _build_observer(engine)

    await _drive_real_finalize(observer, parent_id)

    assert enqueue_spy.await_count == 1, (
        "flag ON control: the adjudication notice must be enqueued — "
        "if this fails the OFF-state zero-write assertions are not "
        "discriminating"
    )
    kwargs = enqueue_spy.await_args.kwargs
    assert kwargs["source"] == _GUARD_NOTICE_SOURCE
    assert kwargs["instance_id"] == parent_id
    assert parent_id in rig._B_NOTICE_LEDGER, (
        "flag ON control: the notice episode must be recorded in the ledger"
    )
    # The stamp still landed — enforcement never blocks (D2.6).
    with Session(engine) as session:
        assert session.get(Instance, parent_id).status == (
            InstanceStatus.COMPLETED.value
        )


# ─────────────────────────────────────────────────────────────────────────────
# Sub-case 4: S3 scoping — flag OFF does NOT touch the ALWAYS-ON instruments
# ─────────────────────────────────────────────────────────────────────────────


async def test_s3_always_on_instruments_fire_with_flag_off(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
):
    """With the B-flag OFF, the Wave-1 (c) marker + NR-3 junk counter still
    fire on a junk-shape report — the B-flag gates ONLY the (b)
    enforcement. The marker is a SUFFIX APPEND only: content semantics are
    unchanged beyond the suffix, and nothing is blocked."""
    _force_flag_off(monkeypatch, "unset")
    # Lock the OFF resolution for this test's scope (cache was reset by the
    # autouse fixture; this pre-resolves so any accidental ON would show).
    assert rig.resolve_report_integrity_b_guard_enabled() is False

    parent_id = _seed_parent_declared_waiting(engine)
    child_id = _seed_child_running(engine, parent_id)
    junk_history = _junk_checkpoint_history()
    service = _build_child_reports_service(engine)

    counter_before = rim.get_junk_report_total()

    with patch(
        "daemon.services.child_reports.get_instance_messages",
        new=AsyncMock(return_value=junk_history),
    ):
        # The REAL instrument site — child_reports
        # ._get_last_assistant_message_raw (where record_junk_report fires
        # and the (c) marker suffix is appended). Driven directly so the
        # suffix-append semantics are observed WITHOUT the caller-side
        # "Worker agent (id=...) has done, below is the response:"
        # envelope that _get_last_assistant_message adds around it.
        content = await service._get_last_assistant_message_raw(child_id)

    counter_after = rim.get_junk_report_total()

    # NR-3 counter fired (always-on, flag-independent).
    assert counter_after == counter_before + 1, (
        "NR-3 junk counter must increment with the B-flag OFF (S3 "
        "scoping: the flag gates ONLY the (b) enforcement)"
    )
    assert rim.get_junk_report_total() >= 1

    # (c) marker appended — suffix-append ONLY, nothing blocked.
    assert content is not None, "the report fetch must not be blocked"
    base_content = "I'll take a look at this now."
    assert content == f"{base_content}\n\n{REPORT_SANITY_MARKER}", (
        "the marker must be a pure suffix append "
        f"(\\n\\n + marker); got {content!r}"
    )
    assert content.replace(f"\n\n{REPORT_SANITY_MARKER}", "") == base_content, (
        "marker must NOT change report content semantics beyond the "
        "suffix append"
    )
    # Nothing blocked: the call returned normally (implicit above) and no
    # exception escaped the instrument site.


# ─────────────────────────────────────────────────────────────────────────────
# S3 helpers (mirror the repro scaffold's hermetic harness)
# ─────────────────────────────────────────────────────────────────────────────


def _seed_child_running(engine: Engine, parent_id: str) -> str:
    """Child RUNNING with NO live work rows (finished its turn — junk shape)."""
    cid = f"child-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=cid,
                agent_id="worker",
                agent_name="worker",
                agent_dir="/tmp/agents/worker",
                project_id="probe-project",
                parent_id=parent_id,
                status=InstanceStatus.RUNNING.value,
                version=2,
                instance_metadata={},
                created_at=now_iso,
                updated_at=now_iso,
                paused_at=None,
            )
        )
        session.commit()
    return cid


def _junk_checkpoint_history() -> list[dict]:
    """Zero-tool no-work opener — the silent-death evidence shape (D2.18)."""
    return [
        {"role": "user", "content": "Investigate the flaky queue test"},
        {
            "role": "assistant",
            "content": "I'll take a look at this now.",
            "tool_calls": [],
        },
    ]


def _build_child_reports_service(engine: Engine) -> ChildReportsService:
    """Real ChildReportsService over the test engine (report fetch only)."""
    from daemon.config import Config

    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager.config = Config()
    adapter = MagicMock(name="CheckpointerAdapter")
    adapter.raw_saver = MagicMock(name="RawSaver")
    manager._checkpointer = adapter
    manager._live_hub = None
    manager._queue_repository = MagicMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None
    return service
