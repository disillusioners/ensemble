"""B.S.3 — FAIL-OPEN suite for the (b) flag-gated ENFORCEMENT action.

Wave 2 stage iii (wc-wake-report-integrity, phase2-plan §4.2 B.S.3;
decisions.md C2-D2.6 LOCKED: "fail-OPEN + inject-notice, NEVER block").

THE FAIL-OPEN CONTRACT (what this module pins):

    The (b) declared-waiting guard MAY observe a completion; it may
    NEVER prevent one, delay one beyond a bounded budget, or raise
    into the completion path. Under EVERY failure shape below the
    stamp transaction has ALREADY committed (or proceeds untouched)
    and the completion stands:

      (1) PREDICATE RAISES    → completion proceeds + one WARNING
                                ("predicate FAILED … fail-OPEN") +
                                NO notice enqueued.
      (2) PREDICATE RETURNS A MALFORMED VALUE → completion proceeds +
                                WARNING + NO notice (the malformed
                                value is dropped, never trusted).
      (3) ENFORCEMENT EXCEEDS THE 5s BUDGET → completion proceeds +
                                WARNING (budget timeout absorbed) +
                                NO notice; the bounded wait NEVER
                                propagates TimeoutError upward.

    The 5s budget (build item B.S.1-iii) wraps ONLY the enforcement
    action (the async adjudication-notice enqueue) — the evaluation
    itself stays the stage-ii inline same-tx read (B.S.7, unchanged
    position) and the stamp ALWAYS proceeds first (D2.6 "never
    block").

    All three scenarios run with the kill-switch ON
    (``WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED=1``) in
    the incident shape — fail-OPEN is a property of the ON state; the
    OFF state is covered by the registry / revert-proof tests.

HERMETIC: real in-memory SQLite (StaticPool), real guard module, real
ChildReportsService root-completion path for scenario (1) (mocked
manager surface), mocked ``enqueue_message`` everywhere (AsyncMock —
never a live LLM or queue).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
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
from daemon.repositories.event.models import Event  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import JobItem  # noqa: F401
from daemon.repositories.message_queue.models import (  # noqa: F401
    MessageQueue,
)
from daemon.repositories.report_injection.models import (  # noqa: F401
    ReportInjection,
)
from daemon.repositories.task.models import Task  # noqa: F401
from daemon.services import child_reports as _child_reports_module
from daemon.services import report_integrity_guard as rig
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import DependencyBus, set_dependency_bus
from daemon.services.report_integrity_guard import (
    DeclaredWaitingViolationReport,
    enforce_declared_waiting_violations,
    log_declared_waiting_violations,
)
from daemon.write_pause_guard import WritePauseGuard

GUARD_LOGGER = "daemon.services.report_integrity_guard"
B_FLAG_ENV = "WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED"


# ─────────────────────────────────────────────────────────────────────────────
# Flag control (the kill-switch is cached at module level — reset per test)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the (b) enforcement kill-switch ON for this test."""
    monkeypatch.setenv(B_FLAG_ENV, "1")
    monkeypatch.setattr(rig, "_B_GUARD_ENABLED", None)  # reset boot cache
    yield


@pytest.fixture
def flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the (b) enforcement kill-switch OFF (ship default)."""
    monkeypatch.delenv(B_FLAG_ENV, raising=False)
    monkeypatch.setattr(rig, "_B_GUARD_ENABLED", None)
    yield


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures (mirror tests/unit/services/test_child_reports.py — hermetic)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
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
    """Started DependencyBus bound to the test engine (autouse)."""
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
def _reset_notice_ledger():
    """Reset the (b) notice dedupe ledger around every test."""
    rig._B_NOTICE_LEDGER.clear()
    try:
        yield
    finally:
        rig._B_NOTICE_LEDGER.clear()


def _seed_root(engine: Engine, *, status: str = InstanceStatus.RUNNING.value) -> str:
    iid = f"root-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=iid,
                agent_id="leader",
                agent_name="leader",
                agent_dir="/tmp/leader",
                parent_id=None,
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return iid


def _build_service(engine: Engine) -> ChildReportsService:
    """Real ChildReportsService over the test engine; manager surface mocked.

    ``manager.enqueue_message`` is an AsyncMock so the enforcement action's
    enqueue attempt is observable WITHOUT any real delivery.
    """
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._checkpointer = None
    manager._live_hub = None
    manager._queue_repository = MagicMock()
    manager._instance_repository = MagicMock()
    manager.enqueue_message = AsyncMock()

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None
    return service


def _make_violation_report(parent_id: str, child_id: str) -> DeclaredWaitingViolationReport:
    return DeclaredWaitingViolationReport(
        parent_instance_id=parent_id,
        pending_with_terminal_child=[
            {
                "injection_id": f"inj-{uuid.uuid4().hex[:8]}",
                "child_instance_id": child_id,
                "state": "PENDING",
                "child_terminal_status": InstanceStatus.COMPLETED.value,
            }
        ],
        fired_unenqueued=[],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 1 — predicate RAISES → COMPLETED proceeds, WARNING, NO notice
# ─────────────────────────────────────────────────────────────────────────────


class TestFailOpenPredicateRaises:
    async def test_predicate_raise_completes_without_notice(
        self,
        engine: Engine,
        flag_on: None,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """(1) The predicate explodes mid-evaluation at the inline same-tx
        position: the root-COMPLETED stamp MUST still proceed (fail-OPEN,
        D2.6), exactly one WARNING names the failure, and NO notice is
        enqueued (the enforcement action never sees a report).
        """

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("db connection lost (simulated)")

        monkeypatch.setattr(rig, "evaluate_declared_waiting_violations", _boom)

        service = _build_service(engine)
        root_id = _seed_root(engine)

        with caplog.at_level(logging.WARNING, logger=GUARD_LOGGER):
            result = service._process_child_completion_db_sync(
                instance_id=root_id,
                completed_message_id="msg-different-id",
                last_content="assistant text",
            )

        # COMPLETED PROCEEDS — the stamp went through, outcome is the
        # clean root-completed shape.
        assert result.outcome == "root_completed", (
            f"fail-OPEN: a raising predicate must never deflect the "
            f"completion; got outcome={result.outcome!r}"
        )
        with Session(engine) as session:
            row = session.get(Instance, root_id)
            assert row.status == InstanceStatus.COMPLETED.value, (
                "fail-OPEN: the instance row must be stamped COMPLETED"
            )

        # One WARNING names the failure + the fail-OPEN policy.
        failed = [
            r for r in caplog.records if "predicate FAILED" in r.getMessage()
        ]
        assert len(failed) == 1, (
            f"expected exactly one predicate-FAILED warning, got "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        assert "RuntimeError" in failed[0].getMessage()
        assert "fail-OPEN" in failed[0].getMessage()

        # NO NOTICE — the enforcement action is never invoked with a
        # violation (the report is None), so the enqueue never happens.
        manager = service._manager
        manager.enqueue_message.assert_not_called()

        # The post-commit dispatch (which would carry the enforcement)
        # is also a no-op for a None report.
        await service._dispatch_post_commit_side_effects(
            result, "assistant text", "msg-different-id"
        )
        manager.enqueue_message.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 2 — predicate returns a MALFORMED value → COMPLETED proceeds
# ─────────────────────────────────────────────────────────────────────────────


class TestFailOpenMalformedPredicateResult:
    async def test_malformed_predicate_value_completes_without_notice(
        self,
        engine: Engine,
        flag_on: None,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """(2) The predicate returns garbage (not a
        ``DeclaredWaitingViolationReport``): the guard drops the value
        (never trusted, never attribute-probed into a raise), logs a
        WARNING, and the completion proceeds with NO notice.
        """
        monkeypatch.setattr(
            rig,
            "evaluate_declared_waiting_violations",
            lambda *a, **k: {"oops": "not a report"},  # malformed
        )

        service = _build_service(engine)
        root_id = _seed_root(engine)

        with caplog.at_level(logging.WARNING, logger=GUARD_LOGGER):
            result = service._process_child_completion_db_sync(
                instance_id=root_id,
                completed_message_id="msg-different-id",
                last_content="assistant text",
            )

        assert result.outcome == "root_completed"
        with Session(engine) as session:
            row = session.get(Instance, root_id)
            assert row.status == InstanceStatus.COMPLETED.value

        malformed = [
            r
            for r in caplog.records
            if "malformed" in r.getMessage().lower()
        ]
        assert len(malformed) >= 1, (
            "a malformed predicate result must be reported at WARNING, "
            f"got {[r.getMessage() for r in caplog.records]}"
        )
        service._manager.enqueue_message.assert_not_called()

    async def test_malformed_value_direct_to_enforcement_is_dropped(
        self,
        engine: Engine,
        flag_on: None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Defense in depth: the enforcement action itself refuses a
        malformed report object even when called directly with one.
        """
        manager = MagicMock(name="InstanceManager")
        manager.enqueue_message = AsyncMock()

        with caplog.at_level(logging.WARNING, logger=GUARD_LOGGER):
            outcome = await enforce_declared_waiting_violations(
                {"not": "a report"},  # type: ignore[arg-type]
                manager=manager,
                parent_instance_id="parent-x",
                context_tag="unit.malformed",
            )

        assert outcome is False
        manager.enqueue_message.assert_not_called()
        assert any(
            "malformed" in r.getMessage().lower() for r in caplog.records
        )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 3 — enforcement exceeds the 5s budget → COMPLETED proceeds
# ─────────────────────────────────────────────────────────────────────────────


class TestFailOpenEnforcementBudget:
    async def test_slow_enqueue_times_out_completion_stands(
        self,
        engine: Engine,
        flag_on: None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """(3) The adjudication-notice enqueue hangs: the 5s budget wraps
        the enforcement action, the timeout is ABSORBED (never raised
        into the caller), a WARNING names the budget, and the already-
        committed COMPLETED stamp stands untouched.
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        report = _make_violation_report(parent_id, child_id)

        manager = MagicMock(name="InstanceManager")
        manager.config = None  # no config section → env-only gate

        async def _slow_enqueue(*_args: object, **_kwargs: object) -> None:
            await asyncio.sleep(30)  # far beyond the 5s budget

        manager.enqueue_message = _slow_enqueue

        # The stamp has ALREADY proceeded (this is the post-commit action).
        with Session(engine) as session:
            session.add(
                Instance(
                    instance_id=parent_id,
                    agent_id="leader",
                    agent_name="leader",
                    agent_dir="/tmp/leader",
                    parent_id=None,
                    status=InstanceStatus.COMPLETED.value,
                    version=2,
                    instance_metadata={},
                )
            )
            session.commit()

        with caplog.at_level(logging.WARNING, logger=GUARD_LOGGER):
            outcome = await enforce_declared_waiting_violations(
                report,
                manager=manager,
                parent_instance_id=parent_id,
                context_tag="unit.budget",
            )

        assert outcome is False, (
            "a budget-broken enqueue must report 'not enqueued', not raise"
        )
        budget_warnings = [
            r
            for r in caplog.records
            if "budget" in r.getMessage().lower()
            or "timeout" in r.getMessage().lower()
        ]
        assert len(budget_warnings) >= 1, (
            f"the absorbed budget timeout must be logged at WARNING; got "
            f"{[r.getMessage() for r in caplog.records]}"
        )

        # COMPLETED PROCEEDS — the stamp the notice must never block is
        # exactly where it was.
        with Session(engine) as session:
            row = session.get(Instance, parent_id)
            assert row.status == InstanceStatus.COMPLETED.value

        # The dedupe ledger did NOT record a notice that never landed —
        # a later episode may retry.
        assert parent_id not in rig._B_NOTICE_LEDGER

    async def test_budget_is_five_seconds_constant(self) -> None:
        """Pin the budget constant: the bounded wait is 5.0s (B.S.3 spec)."""
        assert rig.NOTICE_ENQUEUE_BUDGET_SECONDS == 5.0


# ─────────────────────────────────────────────────────────────────────────────
# Cross-checks — the fail-OPEN edges the three scenarios imply
# ─────────────────────────────────────────────────────────────────────────────


class TestFailOpenEdges:
    async def test_none_report_is_a_noop(self, flag_on: None) -> None:
        """A None report (healthy path or swallowed failure) → no work,
        no raise, no enqueue.
        """
        manager = MagicMock(name="InstanceManager")
        manager.enqueue_message = AsyncMock()
        outcome = await enforce_declared_waiting_violations(
            None,
            manager=manager,
            parent_instance_id="parent-x",
            context_tag="unit.none",
        )
        assert outcome is False
        manager.enqueue_message.assert_not_called()

    async def test_healthy_report_is_a_noop(self, flag_on: None) -> None:
        """An EMPTY (healthy) report → no notice even with the flag ON."""
        manager = MagicMock(name="InstanceManager")
        manager.enqueue_message = AsyncMock()
        healthy = DeclaredWaitingViolationReport(parent_instance_id="p")
        outcome = await enforce_declared_waiting_violations(
            healthy,
            manager=manager,
            parent_instance_id="p",
            context_tag="unit.healthy",
        )
        assert outcome is False
        manager.enqueue_message.assert_not_called()

    async def test_enqueue_exception_is_absorbed(
        self, flag_on: None, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A raise from the enqueue itself → WARNING + False, never a
        propagation into the completion path (D2.6).
        """
        report = _make_violation_report("parent-x", "child-x")
        manager = MagicMock(name="InstanceManager")
        manager.config = None

        async def _exploding_enqueue(*_a: object, **_k: object) -> None:
            raise ConnectionError("queue backend down")

        manager.enqueue_message = _exploding_enqueue

        with caplog.at_level(logging.WARNING, logger=GUARD_LOGGER):
            outcome = await enforce_declared_waiting_violations(
                report,
                manager=manager,
                parent_instance_id="parent-x",
                context_tag="unit.enqueue_boom",
            )
        assert outcome is False
        assert any(
            "ConnectionError" in r.getMessage()
            or "queue backend down" in r.getMessage()
            for r in caplog.records
        )
        assert "parent-x" not in rig._B_NOTICE_LEDGER

    def test_log_helper_returns_report_for_enforcement(
        self, engine: Engine, flag_on: None
    ) -> None:
        """The same-tx evaluation (B.S.7 position, unchanged) hands its
        structured report to the post-commit enforcement via the log
        helper's return value — the stage-iii extension of the stage-ii
        helper.
        """
        parent_id = f"parent-{uuid.uuid4().hex[:8]}"
        child_id = f"child-{uuid.uuid4().hex[:8]}"
        with Session(engine) as session:
            session.add(
                Instance(
                    instance_id=parent_id,
                    agent_id="leader",
                    agent_name="leader",
                    agent_dir="/tmp/leader",
                    parent_id=None,
                    status=InstanceStatus.RUNNING.value,
                    version=1,
                    instance_metadata={},
                )
            )
            session.add(
                Instance(
                    instance_id=child_id,
                    agent_id="worker",
                    agent_name="worker",
                    agent_dir="/tmp/worker",
                    parent_id=parent_id,
                    status=InstanceStatus.COMPLETED.value,
                    version=1,
                    instance_metadata={},
                )
            )
            session.add(
                ReportInjection(
                    parent_instance_id=parent_id,
                    child_instance_id=child_id,
                    child_message_id=f"msg-{uuid.uuid4().hex[:8]}",
                    report_message_id=f"rmsg-{uuid.uuid4().hex[:8]}",
                    content="junk opener body",
                    state="PENDING",
                )
            )
            session.commit()

            report = log_declared_waiting_violations(
                session, parent_id, context_tag="unit.fail_open.return"
            )

        assert isinstance(report, DeclaredWaitingViolationReport)
        assert report.is_violation is True
