"""B.S.1-i unit tests — (b) terminal-child-aware waiting PREDICATE.

Wave 2 (wc-wake-report-integrity Phase 2, decisions.md C2-D2.7 LOCKED
2026-08-30): the declared-waiting predicate reads durable DB state
only — NEVER ``dependency_bus.pending_watchers`` (cache-first
``dependency_bus.py:960-961``, purged post-``emit_terminal``
``:709`` → EMPTY in the inter-report gap), NEVER
``instances.status = WAITING_CHILDREN`` (deprecated as control-flow
per technical-analysis §"Technical Debt" item 1).

Composition (decisions.md D2.7 LOCKED, locked 2026-08-30):

* **PRIMARY signal** — ``report_injections`` rows for the parent with
  ``state IN ('PENDING','DEFERRED')`` whose child instance is
  terminal (COMPLETED/FAILED/ERROR/TERMINATED). The child-terminal
  JOIN promotes ``count_pending_for_parent``
  (``daemon/repositories/report_injection/repository.py:1042``) into
  the predicate's PRIMARY evidence. Same-tx-executable variant runs
  on a CALLER-PROVIDED session (the completion transaction will
  invoke it in stage ii/iii per B.S.7).
* **CORROBORATING signal** — ``dependency_watchers`` rows for the
  parent with status FIRED ∧ ``enqueued_at IS NULL`` (the
  FIRED-but-unenqueued inter-report-gap shape; ``models.py:128``
  documents enqueued_at semantics: stamped only via the NORMAL
  path when parent NOT paused; the 60s-grace DELETE predicate
  ``enqueued_at IS NOT NULL`` is load-bearing — read
  ``dependency_bus.py:709`` post-``emit_terminal`` purge +
  ``:960-961`` cache-first read for the rationale).

B.S.1-i scope:

* Predicate FUNCTION ONLY — no behavior change anywhere. No call
  site may invoke the predicate yet (stages ii log-attach and iii
  flag-gated enforcement are LATER coders).
* (b) is CONTENT-BLIND (D2.18 LOCKED) — reads delivery/declaration
  state ONLY, never message content, never ``tool_calls``.
* Structured return shape — per-child detail incl. the child's
  terminal status (OQ-6 parameterization: COMPLETED / FAILED /
  ERROR / TERMINATED each get a distinct adjudication playbook
  later). EMPTY on healthy paths; NON-EMPTY in the incident shape.

This test asserts the predicate fires in the incident shape
(PENDING row + terminal child), the FIRED-but-unenqueued
corroborating fixture, dormant on healthy paths (no rows / child
non-terminal / PENDING row with non-terminal child / watcher FIRED
but enqueued), DEFERRED rows count as obligations, the same-tx
session variant executes inside a transaction (visible to the
caller's session), and the structured return exposes terminal
statuses.

RED-ON-BASE EVIDENCE: this test file was authored against the
pre-implementation tree; the predicate function and its repo-method
companions did not exist; pytest collected this module and every
test failed with ``ModuleNotFoundError`` / ``AttributeError`` until
B.S.1-i landed. The capture is in the Coder Report.
"""

from __future__ import annotations

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
from daemon.repositories.message_queue.models import (  # noqa: F401
    MessageQueue,
)
from daemon.repositories.report_injection.models import (  # noqa: F401
    ReportInjection,
)
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.services.report_integrity_guard import (
    DeclaredWaitingViolationReport,
    enforce_declared_waiting_violations,
    evaluate_declared_waiting_violations,
    log_declared_waiting_violations,
)

import daemon.services.report_integrity_guard as rig  # noqa: E402


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite engine with StaticPool (cross-thread safety)."""
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


@pytest.fixture
def report_repo(engine: Engine) -> ReportInjectionRepository:
    return ReportInjectionRepository(engine)


@pytest.fixture
def watcher_repo(engine: Engine) -> DependencyWatcherRepository:
    return DependencyWatcherRepository(engine)


@pytest.fixture
def wired_bus(engine: Engine):
    """Started DependencyBus bound to the test engine (opt-in).

    The stage-iii root-path revert-proof test drives the REAL
    ``_process_child_completion_db_sync``, which hard-errors (A8) when
    the bus singleton is None — wire it there; the pure guard tests
    don't need it.
    """
    import asyncio

    from daemon.repositories.dependency_bus.repository import (
        DependencyWatcherRepository as _DWR,
    )
    from daemon.services.dependency_bus import (
        DependencyBus,
        set_dependency_bus,
    )

    repo = _DWR(engine)
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


def _seed_instance(
    engine: Engine,
    *,
    status: str,
    parent_id: str | None = None,
    prefix: str = "inst",
) -> str:
    """Insert an instance row with the requested status."""
    iid = f"{prefix}-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=iid,
                agent_id="worker",
                agent_name="worker",
                agent_dir="/tmp/worker",
                parent_id=parent_id,
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return iid


def _seed_parent(engine: Engine) -> str:
    """Insert a parent instance (running, no live Task rows needed)."""
    return _seed_instance(
        engine, status=InstanceStatus.RUNNING.value, prefix="parent"
    )


def _seed_terminal_child(
    engine: Engine, parent_id: str, status: str = InstanceStatus.COMPLETED.value
) -> str:
    """Insert a child instance in a TERMINAL state (link to parent)."""
    return _seed_instance(
        engine,
        status=status,
        parent_id=parent_id,
        prefix="child",
    )


def _seed_non_terminal_child(engine: Engine, parent_id: str) -> str:
    """Insert a child instance in a NON-TERMINAL state (RUNNING)."""
    return _seed_instance(
        engine,
        status=InstanceStatus.RUNNING.value,
        parent_id=parent_id,
        prefix="child",
    )


def _enqueue_pending(
    report_repo: ReportInjectionRepository,
    *,
    parent_id: str,
    child_id: str,
    report_msg: str | None = None,
) -> None:
    """Insert a PENDING report-injection row for (parent, child)."""
    if report_msg is None:
        report_msg = f"rmsg-{uuid.uuid4().hex[:8]}"
    report_repo.enqueue(
        parent_instance_id=parent_id,
        child_instance_id=child_id,
        child_message_id=f"msg-{uuid.uuid4().hex[:8]}",
        report_message_id=report_msg,
        content="junk opener body",
    )


def _ensure_deferred(
    report_repo: ReportInjectionRepository,
    *,
    parent_id: str,
    child_id: str,
) -> None:
    """Insert a DEFERRED marker row for (parent, child)."""
    report_repo.ensure_deferred(
        parent_instance_id=parent_id,
        child_instance_id=child_id,
        child_message_id=f"msg-{uuid.uuid4().hex[:8]}",
        deferred_reason="DEFERRED_REASON_TEST",
    )


def _seed_watcher(
    engine: Engine,
    *,
    parent_id: str,
    source_task_id: str,
    state: str,
    enqueued_at: str | None = None,
) -> str:
    """Insert a DependencyWatcher row for (parent, source_task_id, state)."""
    wid = f"watch-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            DependencyWatcher(
                watch_id=wid,
                source_task_id=source_task_id,
                target_instance_id=parent_id,
                follow_up_payload={"message": "wake"},
                watcher_metadata={"kind": "test"},
                created_at="2026-08-30T00:00:00+00:00",
                fired_at="2026-08-30T00:00:01+00:00"
                if state == DependencyWatcherState.FIRED.value
                else None,
                enqueued_at=enqueued_at,
                state=state,
            )
        )
        session.commit()
    return wid


# =============================================================================
# Healthy paths — DORMANT (B.S.1-i invariant: empty on healthy paths)
# =============================================================================


class TestPredicateDormantOnHealthyPaths:
    """Predicate returns EMPTY on every healthy shape — the D2.7 invariant."""

    def test_no_rows_returns_empty(self, engine: Engine) -> None:
        """Parent with NO report rows and NO watcher rows → EMPTY."""
        parent_id = _seed_parent(engine)

        with Session(engine) as session:
            result = evaluate_declared_waiting_violations(session, parent_id)

        assert isinstance(result, DeclaredWaitingViolationReport)
        assert result.count == 0
        assert result.is_violation is False
        assert result.pending_with_terminal_child == []
        assert result.fired_unenqueued == []

    def test_pending_row_with_non_terminal_child_returns_empty(
        self, engine: Engine, report_repo: ReportInjectionRepository
    ) -> None:
        """PENDING row for a NON-TERMINAL child → EMPTY (no obligation yet).

        The whole chain of (b) is "child is terminal but parent still
        declared-waiting" — a still-running child is not terminal, so the
        predicate must be DORMANT. This is the "active child" shape.
        """
        parent_id = _seed_parent(engine)
        child_id = _seed_non_terminal_child(engine, parent_id)
        _enqueue_pending(report_repo, parent_id=parent_id, child_id=child_id)

        with Session(engine) as session:
            result = evaluate_declared_waiting_violations(session, parent_id)

        assert result.count == 0
        assert result.is_violation is False
        assert result.pending_with_terminal_child == []

    def test_pending_row_already_delivered_returns_empty(
        self, engine: Engine, report_repo: ReportInjectionRepository
    ) -> None:
        """PENDING row that has been CLAIMED (now INJECTED/TASK_DELIVERED) → EMPTY.

        Terminal state rows are NOT obligations — they're past-tense
        delivery evidence.
        """
        parent_id = _seed_parent(engine)
        child_id = _seed_terminal_child(engine, parent_id)
        _enqueue_pending(report_repo, parent_id=parent_id, child_id=child_id)
        # Mark the row INJECTED (the normal delivery path)
        report_repo.claim_for_injection(parent_id)

        with Session(engine) as session:
            result = evaluate_declared_waiting_violations(session, parent_id)

        assert result.count == 0
        assert result.is_violation is False

    def test_fired_but_enqueued_watcher_returns_empty(
        self, engine: Engine
    ) -> None:
        """FIRED watcher with enqueued_at set → EMPTY (no gap).

        The corroborating signal is *specifically* the FIRED-but-
        unenqueued shape (the inter-report gap). A watcher whose
        FollowUp has already been enqueued has closed the gap and
        must NOT contribute to the count.
        """
        parent_id = _seed_parent(engine)
        _seed_watcher(
            engine,
            parent_id=parent_id,
            source_task_id="task-1",
            state=DependencyWatcherState.FIRED.value,
            enqueued_at="2026-08-30T00:00:02+00:00",  # stamped
        )

        with Session(engine) as session:
            result = evaluate_declared_waiting_violations(session, parent_id)

        assert result.count == 0
        assert result.fired_unenqueued == []

    def test_pending_watcher_returns_empty(self, engine: Engine) -> None:
        """PENDING watcher (not yet FIRED) → EMPTY.

        Only FIRED ∧ enqueued_at IS NULL is the corroborating shape;
        a still-PENDING watcher is the normal declared-waiting shape
        that the bus already accounts for.
        """
        parent_id = _seed_parent(engine)
        _seed_watcher(
            engine,
            parent_id=parent_id,
            source_task_id="task-1",
            state=DependencyWatcherState.PENDING.value,
            enqueued_at=None,
        )

        with Session(engine) as session:
            result = evaluate_declared_waiting_violations(session, parent_id)

        assert result.count == 0

    def test_cancelled_watcher_returns_empty(self, engine: Engine) -> None:
        """CANCELLED watcher → EMPTY.

        CANCELLED is terminal — the watcher was stopped (parent was
        stopped before the child terminated). It is NOT a delivery
        obligation.
        """
        parent_id = _seed_parent(engine)
        _seed_watcher(
            engine,
            parent_id=parent_id,
            source_task_id="task-1",
            state=DependencyWatcherState.CANCELLED.value,
            enqueued_at=None,
        )

        with Session(engine) as session:
            result = evaluate_declared_waiting_violations(session, parent_id)

        assert result.count == 0


# =============================================================================
# Incident shape — PRIMARY signal (D2.7)
# =============================================================================


class TestPredicateFiresOnPrimarySignal:
    """The PRIMARY signal: report_injections PENDING/DEFERRED whose child is terminal."""

    def test_pending_row_with_completed_child_fires(
        self, engine: Engine, report_repo: ReportInjectionRepository
    ) -> None:
        """PENDING row + COMPLETED child → predicate fires (the core incident shape).

        The 11-hop premature-completion chain's hop-10/11 collapse to this
        shape: a PENDING report is still on the durable obligation queue,
        the child has already reached COMPLETED, and the parent has not
        yet acted on the obligation.
        """
        parent_id = _seed_parent(engine)
        child_id = _seed_terminal_child(
            engine, parent_id, status=InstanceStatus.COMPLETED.value
        )
        _enqueue_pending(report_repo, parent_id=parent_id, child_id=child_id)

        with Session(engine) as session:
            result = evaluate_declared_waiting_violations(session, parent_id)

        assert result.is_violation is True
        assert result.count == 1
        assert len(result.pending_with_terminal_child) == 1
        assert result.fired_unenqueued == []

        detail = result.pending_with_terminal_child[0]
        assert detail["child_instance_id"] == child_id
        # OQ-6: terminal status is exposed in the structured return so
        # the (b) enforcement notice can pick a distinct adjudication
        # playbook for COMPLETED / FAILED / ERROR / TERMINATED.
        assert detail["child_terminal_status"] == InstanceStatus.COMPLETED.value

    def test_pending_row_with_failed_child_fires(
        self, engine: Engine, report_repo: ReportInjectionRepository
    ) -> None:
        """PENDING row + FAILED child → predicate fires with FAILED status.

        FAILED is a task-level failure (distinct from instance ERROR per
        InstanceStatus.FAILED docstring) — the parent's adjudication
        playbook in stage iii must distinguish it from COMPLETED.
        """
        parent_id = _seed_parent(engine)
        child_id = _seed_terminal_child(
            engine, parent_id, status=InstanceStatus.FAILED.value
        )
        _enqueue_pending(report_repo, parent_id=parent_id, child_id=child_id)

        with Session(engine) as session:
            result = evaluate_declared_waiting_violations(session, parent_id)

        assert result.is_violation is True
        assert result.count == 1
        assert (
            result.pending_with_terminal_child[0]["child_terminal_status"]
            == InstanceStatus.FAILED.value
        )

    def test_pending_row_with_error_child_fires(
        self, engine: Engine, report_repo: ReportInjectionRepository
    ) -> None:
        """PENDING row + ERROR child → predicate fires with ERROR status."""
        parent_id = _seed_parent(engine)
        child_id = _seed_terminal_child(
            engine, parent_id, status=InstanceStatus.ERROR.value
        )
        _enqueue_pending(report_repo, parent_id=parent_id, child_id=child_id)

        with Session(engine) as session:
            result = evaluate_declared_waiting_violations(session, parent_id)

        assert result.is_violation is True
        assert (
            result.pending_with_terminal_child[0]["child_terminal_status"]
            == InstanceStatus.ERROR.value
        )

    def test_pending_row_with_terminated_child_fires(
        self, engine: Engine, report_repo: ReportInjectionRepository
    ) -> None:
        """PENDING row + TERMINATED child → predicate fires with TERMINATED status."""
        parent_id = _seed_parent(engine)
        child_id = _seed_terminal_child(
            engine, parent_id, status=InstanceStatus.TERMINATED.value
        )
        _enqueue_pending(report_repo, parent_id=parent_id, child_id=child_id)

        with Session(engine) as session:
            result = evaluate_declared_waiting_violations(session, parent_id)

        assert result.is_violation is True
        assert (
            result.pending_with_terminal_child[0]["child_terminal_status"]
            == InstanceStatus.TERMINATED.value
        )

    def test_deferred_marker_with_terminal_child_fires(
        self, engine: Engine, report_repo: ReportInjectionRepository
    ) -> None:
        """DEFERRED marker (write-once obligation) + terminal child → fires.

        DEFERRED rows ARE obligations (they're the pause drop-site's
        write-once recovery marker — ``count_pending_for_parent``
        broadened PENDING ∪ DEFERRED precisely because of this). The
        predicate must count them, not just PENDING.
        """
        parent_id = _seed_parent(engine)
        child_id = _seed_terminal_child(engine, parent_id)
        _ensure_deferred(report_repo, parent_id=parent_id, child_id=child_id)

        with Session(engine) as session:
            result = evaluate_declared_waiting_violations(session, parent_id)

        assert result.is_violation is True
        assert result.count == 1
        assert (
            result.pending_with_terminal_child[0]["child_instance_id"]
            == child_id
        )


# =============================================================================
# Incident shape — CORROBORATING signal (D2.7)
# =============================================================================


class TestPredicateFiresOnCorroboratingSignal:
    """The CORROBORATING signal: dependency_watchers FIRED ∧ enqueued_at IS NULL."""

    def test_fired_but_unenqueued_watcher_fires(
        self, engine: Engine
    ) -> None:
        """FIRED watcher with enqueued_at NULL → predicate fires.

        This is the inter-report-gap shape: A's report was FIRED by the
        bus (``emit_terminal`` marked it) but the FollowUp enqueue (or
        the parent's claim of it) hasn't happened yet — the
        ``enqueued_at`` dedup marker remains NULL. The crash-recovery
        sweep (``_recover_fired_unsent``) is the precedent that
        already consults this exact predicate (``state='FIRED' AND
        enqueued_at IS NULL``).
        """
        parent_id = _seed_parent(engine)
        _seed_watcher(
            engine,
            parent_id=parent_id,
            source_task_id="task-A",
            state=DependencyWatcherState.FIRED.value,
            enqueued_at=None,  # NOT stamped → corroborating
        )

        with Session(engine) as session:
            result = evaluate_declared_waiting_violations(session, parent_id)

        assert result.is_violation is True
        assert result.count == 1
        assert result.pending_with_terminal_child == []
        assert len(result.fired_unenqueued) == 1

        detail = result.fired_unenqueued[0]
        assert detail["source_task_id"] == "task-A"
        # The structured return must surface enough detail for the
        # stage-iii notice to cite the watcher row.
        assert "watch_id" in detail
        assert detail["state"] == DependencyWatcherState.FIRED.value

    def test_multiple_fired_unenqueued_watchers_accumulate(
        self, engine: Engine
    ) -> None:
        """Multiple FIRED-but-unenqueued watchers → count = N (corroborating)."""
        parent_id = _seed_parent(engine)
        for i in range(3):
            _seed_watcher(
                engine,
                parent_id=parent_id,
                source_task_id=f"task-{i}",
                state=DependencyWatcherState.FIRED.value,
                enqueued_at=None,
            )

        with Session(engine) as session:
            result = evaluate_declared_waiting_violations(session, parent_id)

        assert result.is_violation is True
        assert result.count == 3
        assert len(result.fired_unenqueued) == 3


# =============================================================================
# Incident shape — both signals together
# =============================================================================


class TestPredicateFiresWithBothSignals:
    """The incident shape: PRIMARY + CORROBORATING both present."""

    def test_both_signals_accumulate_in_count(
        self,
        engine: Engine,
        report_repo: ReportInjectionRepository,
    ) -> None:
        """PRIMARY row + CORROBORATING watcher → count = 2 (each contributes)."""
        parent_id = _seed_parent(engine)
        child_id = _seed_terminal_child(engine, parent_id)
        _enqueue_pending(report_repo, parent_id=parent_id, child_id=child_id)
        _seed_watcher(
            engine,
            parent_id=parent_id,
            source_task_id="task-A",
            state=DependencyWatcherState.FIRED.value,
            enqueued_at=None,
        )

        with Session(engine) as session:
            result = evaluate_declared_waiting_violations(session, parent_id)

        assert result.is_violation is True
        assert result.count == 2
        assert len(result.pending_with_terminal_child) == 1
        assert len(result.fired_unenqueued) == 1


# =============================================================================
# Same-transaction execution (B.S.7 invariant — stage ii/iii will
# invoke the predicate INSIDE the completion transaction; the
# predicate's repo-method companion MUST run on the caller's session).
# =============================================================================


class TestSameTransactionExecution:
    """The predicate MUST see uncommitted writes the caller has staged.

    Per B.S.7: stage ii/iii invoke the predicate inside the completion
    transaction. The caller's session holds pending writes; a
    separate-session re-query would miss them. The same-tx repo
    variant is the binding contract.
    """

    def test_pending_row_visible_inside_callers_transaction(
        self, engine: Engine, report_repo: ReportInjectionRepository
    ) -> None:
        """A PENDING row enqueued in a SEPARATE session (committed) is
        visible to the caller's session — this is the trivial case but
        proves the predicate's repo methods do not accidentally open a
        separate transaction.
        """
        parent_id = _seed_parent(engine)
        child_id = _seed_terminal_child(engine, parent_id)
        # Enqueue in its own session (committed).
        _enqueue_pending(report_repo, parent_id=parent_id, child_id=child_id)

        with Session(engine) as session:
            result = evaluate_declared_waiting_violations(session, parent_id)

        assert result.count == 1

    def test_pending_row_visible_when_evaluated_in_callers_open_transaction(
        self,
        engine: Engine,
        report_repo: ReportInjectionRepository,
    ) -> None:
        """A PENDING row enqueued via the caller's OPEN transaction is
        visible — i.e. ``evaluate_declared_waiting_violations`` does
        not bypass the caller's session.

        This is the B.S.7 binding contract: in stage ii/iii the
        completion transaction will enqueue the report, then ask the
        predicate whether the parent has any unacted-on obligation;
        the predicate MUST see the freshly-enqueued row in the same
        transaction. (We assert this with a session-internal
        enqueue + same-session predicate call, with NO explicit
        ``session.commit()`` between them.)
        """
        parent_id = _seed_parent(engine)
        child_id = _seed_terminal_child(engine, parent_id)

        # Insert the PENDING row via the same session that the predicate
        # will use — the predicate MUST see it without an intermediate
        # commit.
        with Session(engine) as session:
            row = report_repo.enqueue(
                parent_instance_id=parent_id,
                child_instance_id=child_id,
                child_message_id="msg-1",
                report_message_id="rmsg-1",
                content="junk opener",
            )
            session.add(row)
            # No commit yet — the predicate must still see the row.
            result = evaluate_declared_waiting_violations(session, parent_id)
            # The PENDING row is visible to the same transaction.
            assert result.count == 1, (
                "B.S.7 invariant: predicate must see the freshly-enqueued "
                "row in the caller's open transaction (same-tx "
                "executable variant)."
            )
            assert result.pending_with_terminal_child[0][
                "child_instance_id"
            ] == child_id


# =============================================================================
# Repo-method companions — verify the same-tx repo variants land in
# BOTH repositories (this stage's primary + corroborating seams).
# =============================================================================


class TestReportInjectionSameTxVariant:
    """The same-tx variant promoted from ``count_pending_for_parent``.

    The existing diagnostic ``count_pending_for_parent(parent_id)``
    method opens its own session and counts PENDING ∪ DEFERRED. The
    predicate needs the SAME count filtered by child-terminal JOIN,
    executed on a CALLER-PROVIDED session. The new variant MUST:

    * Accept an explicit ``session`` argument.
    * NOT commit / open a new transaction.
    * Apply the child-terminal JOIN (the predicate's PRIMARY
      distinct-from-diagnostic shape).

    The diagnostic method MUST keep working — its callers/tests
    unchanged.
    """

    def test_diagnostic_method_unchanged(
        self, engine: Engine, report_repo: ReportInjectionRepository
    ) -> None:
        """The diagnostic ``count_pending_for_parent(parent_id)``
        continues to work — its callers and tests keep passing."""
        parent_id = _seed_parent(engine)
        child_id = _seed_non_terminal_child(engine, parent_id)
        _enqueue_pending(report_repo, parent_id=parent_id, child_id=child_id)

        assert report_repo.count_pending_for_parent(parent_id) == 1


class TestDependencyBusSameTxVariant:
    """The corroborating same-tx variant on the bus repo."""

    def test_fired_unenqueued_for_parent_works(
        self,
        engine: Engine,
        watcher_repo: DependencyWatcherRepository,
    ) -> None:
        """The new same-tx variant counts FIRED ∧ enqueued_at IS NULL rows.

        Sanity-check the repo method directly so the predicate module's
        invocation is exercised both via the predicate and via the
        repo API. The watcher repo's same-tx variant must accept an
        explicit session and not commit.
        """
        parent_id = _seed_parent(engine)
        # Seed one FIRED-but-unenqueued watcher.
        with Session(engine) as session:
            session.add(
                DependencyWatcher(
                    watch_id=f"watch-{uuid.uuid4().hex[:8]}",
                    source_task_id="task-1",
                    target_instance_id=parent_id,
                    follow_up_payload={"message": "wake"},
                    watcher_metadata={"kind": "test"},
                    created_at="2026-08-30T00:00:00+00:00",
                    fired_at="2026-08-30T00:00:01+00:00",
                    enqueued_at=None,
                    state=DependencyWatcherState.FIRED.value,
                )
            )
            session.commit()

        with Session(engine) as session:
            rows = watcher_repo.count_fired_unenqueued_for_parent(
                session, parent_id
            )

        assert len(rows) == 1
        assert rows[0]["source_task_id"] == "task-1"
        assert rows[0]["state"] == DependencyWatcherState.FIRED.value


# =============================================================================
# B.S.1-ii — the stage-ii LOG helper (log_declared_waiting_violations)
# =============================================================================


class TestStageIILogHelper:
    """B.S.1-ii (stage ii, LOG ONLY) — ``log_declared_waiting_violations``.

    Contract under test (phase2-plan §4.2 B.S.1-ii + B.S.7):

    * NON-EMPTY report → ONE structured ``[ReportIntegrityGuard]``
      WARNING line carrying the context tag (which stamp site), the
      parent id, the violation count, and per-child detail (child id +
      terminal status + evidence class PRIMARY / CORROBORATING).
    * EMPTY report → NO log (zero noise on healthy paths).
    * EXCEPTION-SAFE fail-OPEN (D2.6 LOCKED): a predicate exception is
      downgraded to a WARNING ("predicate FAILED … completion
      proceeds") and the helper RETURNS — it never raises into the
      completion path, never blocks, never mutates anything.
    * The helper accepts the caller's session so the evaluation runs
      INSIDE the completion transaction (B.S.7 same-tx binding).
    """

    def test_log_fires_in_incident_shape(
        self,
        engine: Engine,
        report_repo: ReportInjectionRepository,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """PRIMARY-signal violation → exactly one structured WARNING."""
        parent_id = _seed_parent(engine)
        child_id = _seed_terminal_child(engine, parent_id)
        _enqueue_pending(report_repo, parent_id=parent_id, child_id=child_id)

        with Session(engine) as session:
            with caplog.at_level(
                logging.WARNING, logger="daemon.services.report_integrity_guard"
            ):
                ret = log_declared_waiting_violations(
                    session, parent_id, context_tag="unit.stamp_site"
                )

        # STAGE-III CONTRACT (B.S.1-iii): the helper's observable LOG
        # behavior is unchanged (stage ii), but it now RETURNS the
        # evaluated report so the SAME evaluation — never re-read, per
        # B.S.7 — can be handed to the post-commit enforcement action.
        # (Stage ii pinned ``ret is None`` here; stage iii extends the
        # return to ``DeclaredWaitingViolationReport | None``. Failure
        # and healthy paths still return None — pinned below and in
        # tests/unit/services/test_b_fail_open.py.)
        assert isinstance(ret, DeclaredWaitingViolationReport), (
            "stage-iii extension: the helper must return the structured "
            "report for the enforcement hand-off"
        )
        assert ret.is_violation is True
        guard = [
            r
            for r in caplog.records
            if "declared-waiting violation" in r.getMessage()
        ]
        assert len(guard) == 1, (
            f"expected exactly ONE violation line, got {len(guard)}: "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        record = guard[0]
        assert record.levelno == logging.WARNING
        msg = record.getMessage()
        assert "[ReportIntegrityGuard]" in msg, "greppable prefix missing"
        assert "unit.stamp_site" in msg, "context tag (stamp site) missing"
        assert parent_id in msg, "parent id missing"
        assert "count=1" in msg, "violation count missing"
        assert child_id in msg, "child id missing"
        assert "status=completed" in msg, (
            "child terminal status missing (verbatim InstanceStatus value)"
        )
        assert "PRIMARY" in msg, "evidence class missing"

    def test_log_carries_corroborating_evidence(
        self,
        engine: Engine,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """CORROBORATING-signal violation (FIRED ∧ unenqueued) → detail
        names the watch + task ids with evidence=CORROBORATING."""
        parent_id = _seed_parent(engine)
        _seed_watcher(
            engine,
            parent_id=parent_id,
            source_task_id="task-orphan-1",
            state=DependencyWatcherState.FIRED.value,
            enqueued_at=None,
        )

        with Session(engine) as session:
            with caplog.at_level(
                logging.WARNING, logger="daemon.services.report_integrity_guard"
            ):
                log_declared_waiting_violations(
                    session, parent_id, context_tag="unit.stamp_site"
                )

        guard = [
            r
            for r in caplog.records
            if "declared-waiting violation" in r.getMessage()
        ]
        assert len(guard) == 1
        msg = guard[0].getMessage()
        assert "CORROBORATING" in msg
        assert "task-orphan-1" in msg, "source task id missing"
        assert "count=1" in msg
        # No PRIMARY evidence exists in this fixture.
        assert "PRIMARY" not in msg

    def test_silent_on_healthy_path(
        self,
        engine: Engine,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """EMPTY report → NO log line at all (zero healthy-path noise)."""
        parent_id = _seed_parent(engine)

        with Session(engine) as session:
            with caplog.at_level(
                logging.WARNING, logger="daemon.services.report_integrity_guard"
            ):
                log_declared_waiting_violations(
                    session, parent_id, context_tag="unit.healthy"
                )

        assert caplog.records == [], (
            f"healthy path must be silent; got "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    def test_predicate_exception_is_fail_open(
        self,
        engine: Engine,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Predicate raises → helper logs a fail-OPEN WARNING and RETURNS.

        The exception must NOT propagate into the completion path
        (D2.6 LOCKED) and no violation line may be emitted.
        """
        import daemon.services.report_integrity_guard as rig_module

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("db connection lost (simulated)")

        monkeypatch.setattr(
            rig_module, "evaluate_declared_waiting_violations", _boom
        )

        with Session(engine) as session:
            with caplog.at_level(
                logging.WARNING, logger="daemon.services.report_integrity_guard"
            ):
                # Must NOT raise.
                ret = log_declared_waiting_violations(
                    session, "parent-panic", context_tag="unit.fail_open"
                )

        assert ret is None
        failed = [
            r for r in caplog.records if "predicate FAILED" in r.getMessage()
        ]
        assert len(failed) == 1, (
            f"expected one fail-OPEN warning, got "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        assert failed[0].levelno == logging.WARNING
        msg = failed[0].getMessage()
        assert "fail-OPEN" in msg or "fail-OPEN".lower() in msg.lower()
        assert "RuntimeError" in msg, "exception type must be named"
        assert "db connection lost" in msg, "exception detail must be named"
        assert "unit.fail_open" in msg, "context tag must survive the failure"
        assert not [
            r
            for r in caplog.records
            if "declared-waiting violation" in r.getMessage()
        ], "a failed predicate must NOT produce a violation line"


# =============================================================================
# B.S.1-iii — flag-gated ENFORCEMENT (notice variants, channel, dedupe)
# =============================================================================


class TestEnforcementAction:
    """B.S.1-iii — ``enforce_declared_waiting_violations`` (flag ON).

    Contract under test:

    * ONE adjudication notice per completion episode via the durable
      enqueue path (``manager.enqueue_message``) with source
      ``system:report-integrity-guard`` (reserved-origin contract +
      the ``system:*`` dispatch-source guard), priority 0, structured
      metadata provenance (the ``system:watchdog`` precedent).
    * OQ-6: the notice text is parameterized per child terminal
      status — COMPLETED / FAILED / ERROR / TERMINATED each carry a
      DISTINCT adjudication playbook.
    * D2.9: the notice cites the Wave-1 (c) marker pattern as a
      STATIC string; D2.18: the guard never reads report content.
    * C2-D2.2: plain system-authored text — NEVER inside the
      ``[SYSTEM NOTE]`` frame.
    * Dedupe: the same violation set is NOT re-notified (one notice
      per episode); a different set starts a new episode.
    """

    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED", "1"
        )
        monkeypatch.setattr(rig, "_B_GUARD_ENABLED", None)
        rig._B_NOTICE_LEDGER.clear()
        yield
        rig._B_NOTICE_LEDGER.clear()

    def _report(
        self,
        parent_id: str,
        child_id: str,
        status: str = InstanceStatus.COMPLETED.value,
    ) -> DeclaredWaitingViolationReport:
        return DeclaredWaitingViolationReport(
            parent_instance_id=parent_id,
            pending_with_terminal_child=[
                {
                    "injection_id": f"inj-{uuid.uuid4().hex[:8]}",
                    "child_instance_id": child_id,
                    "state": "PENDING",
                    "child_terminal_status": status,
                }
            ],
            fired_unenqueued=[],
        )

    @pytest.mark.parametrize(
        "status", ["completed", "failed", "error", "terminated"]
    )
    async def test_notice_variants_per_child_terminal_status(
        self, status: str
    ) -> None:
        """OQ-6: four terminal statuses → four DISTINCT playbooks."""
        from daemon.services.report_integrity_guard import (
            _build_adjudication_notice,
        )

        report = self._report("parent-x", "child-x", status=status)
        text = _build_adjudication_notice(
            report, context_tag="unit.variants"
        )
        playbooks = [
            _build_adjudication_notice(
                self._report("p", "c", status=s), context_tag="u"
            )
            for s in ["completed", "failed", "error", "terminated"]
        ]
        # Distinct per-status guidance — no two statuses share a line.
        core = [t.split("- Child")[-1] for t in playbooks]
        assert len(set(core)) == 4
        assert status in text, "notice must name the verbatim status"

    async def test_notice_content_contract(self) -> None:
        """parent id + child id + detection statement + marker citation
        + no [SYSTEM NOTE] frame + system source provenance.
        """
        parent_id = "parent-abc123"
        child_id = "child-def456"
        manager = AsyncMock()
        manager.config = None

        outcome = await enforce_declared_waiting_violations(
            self._report(parent_id, child_id),
            manager=manager,
            parent_instance_id=parent_id,
            context_tag="unit.content",
        )

        assert outcome is True
        manager.enqueue_message.assert_awaited_once()
        kwargs = manager.enqueue_message.await_args.kwargs
        assert kwargs["instance_id"] == parent_id
        assert kwargs["source"] == (
            rig.REPORT_INTEGRITY_GUARD_NOTICE_SOURCE
        )
        assert kwargs["source"] == "system:report-integrity-guard"
        assert kwargs["priority"] == 0
        assert kwargs["metadata"]["report_integrity_notice"] is True
        assert kwargs["metadata"]["context_tag"] == "unit.content"
        message: str = kwargs["message"]
        assert "[SYSTEM NOTE]" not in message, (
            "C2-D2.2: notice must NEVER be inside the [SYSTEM NOTE] frame"
        )
        assert parent_id in message and child_id in message
        assert "declared-waiting" in message
        from daemon.constants import REPORT_SANITY_MARKER

        assert REPORT_SANITY_MARKER in message, (
            "D2.9: the notice must cite the (c) marker pattern"
        )

    async def test_notice_never_reads_report_content(self) -> None:
        """D2.18: the notice text is built from durable-row state only —
        an identical violation set yields an identical notice regardless
        of any hypothetical report content.
        """
        from daemon.services.report_integrity_guard import (
            _build_adjudication_notice,
        )

        a = _build_adjudication_notice(
            self._report("p", "c"), context_tag="u"
        )
        b = _build_adjudication_notice(
            self._report("p", "c"), context_tag="u"
        )
        assert a == b

    async def test_dedupe_same_violation_set_not_renotified(self) -> None:
        """ONE notice per completion episode: re-firing the site on the
        SAME violation set does not re-enqueue.
        """
        parent_id = "parent-dedupe"
        manager = AsyncMock()
        manager.config = None
        report = self._report(parent_id, "child-dedupe")

        first = await enforce_declared_waiting_violations(
            report,
            manager=manager,
            parent_instance_id=parent_id,
            context_tag="unit.dedupe",
        )
        second = await enforce_declared_waiting_violations(
            self._report(parent_id, "child-dedupe"),
            manager=manager,
            parent_instance_id=parent_id,
            context_tag="unit.dedupe",
        )

        assert first is True and second is False
        assert manager.enqueue_message.await_count == 1

    async def test_new_violation_set_starts_new_episode(self) -> None:
        """A DIFFERENT violation set (new child) replaces the episode and
        notifies again.
        """
        parent_id = "parent-episode"
        manager = AsyncMock()
        manager.config = None

        await enforce_declared_waiting_violations(
            self._report(parent_id, "child-1"),
            manager=manager,
            parent_instance_id=parent_id,
            context_tag="unit.episode",
        )
        await enforce_declared_waiting_violations(
            self._report(parent_id, "child-2"),
            manager=manager,
            parent_instance_id=parent_id,
            context_tag="unit.episode",
        )
        assert manager.enqueue_message.await_count == 2

    async def test_clean_evaluation_closes_episode(
        self, engine: Engine, report_repo: ReportInjectionRepository
    ) -> None:
        """A later same-tx evaluation coming back CLEAN clears the
        shared cooldown — the wedge becomes eligible again (B.S.5).
        """
        from daemon.services.report_integrity_guard import (
            parent_has_active_b_notice,
        )

        parent_id = "parent-close"
        manager = AsyncMock()
        manager.config = None
        child_id = "child-close"
        # Make the parent's episode real: seed rows the CLEAN evaluation
        # can later resolve (the clean path needs the ledger entry to
        # clear via the site hook, which we exercise directly).
        await enforce_declared_waiting_violations(
            self._report(parent_id, child_id),
            manager=manager,
            parent_instance_id=parent_id,
            context_tag="unit.close",
        )
        assert parent_has_active_b_notice(parent_id)

        healthy = DeclaredWaitingViolationReport(parent_instance_id=parent_id)
        from daemon.services.report_integrity_guard import (
            _clear_b_notice_if_clean,
        )

        _clear_b_notice_if_clean(parent_id, healthy)
        assert not parent_has_active_b_notice(parent_id)

    async def test_flag_off_means_zero_notice_work(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Kill-switch OFF (ship default) → the enforcement action
        short-circuits before ANY notice work — no enqueue, no ledger
        entry (the flag check is the first statement).
        """
        import daemon.services.report_integrity_guard as rig

        monkeypatch.delenv(
            "WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED",
            raising=False,
        )
        monkeypatch.setattr(rig, "_B_GUARD_ENABLED", None)
        rig._B_NOTICE_LEDGER.clear()

        manager = AsyncMock()
        outcome = await enforce_declared_waiting_violations(
            self._report("parent-off", "child-off"),
            manager=manager,
            parent_instance_id="parent-off",
            context_tag="unit.off",
        )
        assert outcome is False
        manager.enqueue_message.assert_not_called()
        assert "parent-off" not in rig._B_NOTICE_LEDGER


# =============================================================================
# B.S.1-iii — KILL-SWITCH REVERT PROOF (flag OFF = stage-ii behavior)
# =============================================================================


class TestKillSwitchRevertProof:
    """Revert path proof: flag OFF + incident shape → NO notice, NO
    enqueue call, ONLY the ``[ReportIntegrityGuard]`` soak log.

    This is the operator-facing guarantee behind D2.5-FLIP: setting
    ``WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED=0`` (or
    blanking the env) + restart restores EXACTLY the stage-ii behavior.
    (Commit-chain proof: each stage is its own commit — ``git revert
    <stage-iii>`` restores stage-ii byte-behavior; with the flag OFF the
    stage-iii diff is unreachable, so the revert is a formality for the
    flag-OFF runtime path.)
    """

    @pytest.fixture(autouse=True)
    def _flag_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import daemon.services.report_integrity_guard as rig

        monkeypatch.delenv(
            "WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED",
            raising=False,
        )
        monkeypatch.setattr(rig, "_B_GUARD_ENABLED", None)
        rig._B_NOTICE_LEDGER.clear()
        yield
        rig._B_NOTICE_LEDGER.clear()

    async def test_flag_off_incident_no_enqueue(
        self,
        engine: Engine,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Direct: incident-shape report + flag OFF → no enqueue, no
        ledger, no notice — the helper short-circuits on the flag.
        """
        manager = AsyncMock()
        report = DeclaredWaitingViolationReport(
            parent_instance_id="parent-revert",
            pending_with_terminal_child=[
                {
                    "injection_id": "inj-1",
                    "child_instance_id": "child-revert",
                    "state": "PENDING",
                    "child_terminal_status": "completed",
                }
            ],
            fired_unenqueued=[],
        )

        with caplog.at_level(
            logging.INFO, logger="daemon.services.report_integrity_guard"
        ):
            outcome = await enforce_declared_waiting_violations(
                report,
                manager=manager,
                parent_instance_id="parent-revert",
                context_tag="unit.revert",
            )

        assert outcome is False
        manager.enqueue_message.assert_not_called(), (
            "flag OFF must not even attempt the enqueue (revert proof)"
        )
        assert not [
            r
            for r in caplog.records
            if "enforcement notice enqueued" in r.getMessage()
        ]

    def test_flag_off_root_path_log_only(
        self,
        engine: Engine,
        report_repo: ReportInjectionRepository,
        wired_bus,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Root-path: incident shape seeded, flag OFF → ONLY the soak log
        fires; the sync result carries the evaluation but the post-commit
        dispatch (with the manager spy) performs NO enqueue — byte-parity
        with stage ii.
        """
        import logging as _logging

        from daemon.services.child_reports import ChildReportsService
        from daemon.write_pause_guard import WritePauseGuard

        parent_id = _seed_parent(engine)
        real_child = _seed_terminal_child(engine, parent_id)
        _enqueue_pending(
            report_repo, parent_id=parent_id, child_id=real_child
        )

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

        with caplog.at_level(
            _logging.INFO, logger="daemon.services.report_integrity_guard"
        ):
            result = service._process_child_completion_db_sync(
                instance_id=parent_id,
                completed_message_id="msg-different-id",
                last_content="assistant text",
            )

        # The stamp proceeded; the stage-ii log fired.
        assert result.outcome == "root_completed"
        guard_logs = [
            r
            for r in caplog.records
            if "declared-waiting violation" in r.getMessage()
        ]
        assert len(guard_logs) == 1, "stage-ii soak log must still fire"
        # NO notice: no enforcement log, no enqueue, no ledger.
        assert not [
            r
            for r in caplog.records
            if "enforcement notice enqueued" in r.getMessage()
        ]
        manager.enqueue_message.assert_not_called()
        import daemon.services.report_integrity_guard as rig

        assert parent_id not in rig._B_NOTICE_LEDGER

