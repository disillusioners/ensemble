"""MB-1: PG-dialect SAVEPOINT-path test for child_reports _process_child_completion_db_sync.

Merge blocker (cycle-3 review, 2026-08-20): the C-DiD ``begin_nested()``
SAVEPOINT block in ``daemon/services/child_reports.py`` (lines ~2685-2851
in head 73bfe0ed) — pre-SAVEPOINT outer flush at ~2725-2737, SAVEPOINT
at ~2739, broadened catch ``except Exception`` at ~2755 with
``nested.rollback()`` at ~2790 — MUST be exercised on REAL PostgreSQL,
not just SQLite. SQLite quirks (no true constraint-name emission, lenient
flush semantics) make the SQLite-only green weaker evidence.

Target runtime:

  timeout 300 .venv/bin/pytest tests/postgres/test_report_deferred_savepoint_pg.py \\
      --override-ini="addopts=" -m postgres -q --tb=short

Reference: ``.agents/shared/planning/pause-report-recovery/phase3-plan.md``
section "MERGE BLOCKERS" — MB-1.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError as SAIntegrityError
from sqlmodel import Session, select as sm_select

# Model imports — required so SQLModel.metadata sees the tables when
# ``create_all()`` runs on the PG engine (matches the
# ``tests/postgres/conftest.py`` convention).
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401  (event table)
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.dependency_bus import (
    DependencyWatcher,
)
from daemon.repositories.dependency_bus.repository import (
    DependencyWatcherRepository,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.services.dependency_bus import DependencyBus, set_dependency_bus
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.services.child_reports import (
    ChildReportsService,
    _is_obligation_triple_integrity_error,
)
from daemon.write_pause_guard import WritePauseGuard


# Auto-apply the postgres marker so ``pytest -m postgres`` selects these
# tests and the default ``-m 'not integration and not postgres'`` addopts
# skips them unless overridden.
pytestmark = pytest.mark.postgres


# =============================================================================
# Service / row helpers
# =============================================================================


def _build_child_reports_service(engine: Engine) -> ChildReportsService:
    """Build a real ``ChildReportsService`` with a mock manager that
    exposes only the attributes ``_process_child_completion_db_sync``
    needs.

    Mirrors the helper in ``tests/postgres/test_wanderer_completion_reporting_pg.py``
    (uses ``__new__`` to skip ``__init__`` and bind attributes manually).
    """
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._checkpointer = None
    manager._live_hub = None  # SSE no-op (guarded on truthiness)
    manager._queue_repository = MagicMock()
    manager._instance_repository = MagicMock()
    manager._task_repo = None  # disables task lookup in bus hook path
    manager._worker_pool = None  # disables notify_work()

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None  # lifecycle event publish is guarded
    return service


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str,
    parent_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    agent_id: str = "worker",
) -> Instance:
    """Insert an Instance row with the given parent_id and status."""
    inst = Instance(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=f"/tmp/{agent_id}",
        parent_id=parent_id,
        status=status,
        version=1,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        instance_metadata={},
    )
    with Session(engine) as session:
        session.add(inst)
        session.commit()
        session.refresh(inst)
    return inst


def _seed_pending_obligation(
    engine: Engine,
    *,
    parent_instance_id: str,
    child_instance_id: str,
    child_message_id: str,
    state: str = ReportInjectionState.PENDING.value,
) -> ReportInjection:
    """Seed a non-terminal ``ReportInjection`` row with the same
    obligation triple the natural completion path will try to INSERT.

    This simulates the Phase 2 sweep / router having ALREADY
    transitioned a DEFERRED marker → PENDING via
    ``transition_deferred_to_pending`` in a previous transaction. The
    partial unique index ``uq_report_injections_oblig_triple`` (PG
    branch at ``daemon/services/child_reports.py:119-133``) will fire
    on the natural completion INSERT.
    """
    injection_id = f"inj-{uuid.uuid4().hex[:8]}"
    report_message_id = f"report-{uuid.uuid4().hex[:8]}"
    row = ReportInjection(
        injection_id=injection_id,
        parent_instance_id=parent_instance_id,
        child_instance_id=child_instance_id,
        child_message_id=child_message_id,
        report_message_id=report_message_id,
        content="previously recovered obligation",
        state=state,
        recovery_attempted_at=datetime.now(timezone.utc).isoformat(),
    )
    with Session(engine) as session:
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def _seed_dependency_watcher(
    engine: Engine,
    *,
    target_instance_id: str,
    source_task_id: str,
) -> None:
    """Insert a DependencyWatcher row targeting the parent — wires the
    bus gate so the ``regular_child_completed`` branch passes.

    The bus is the SOLE completion authority post-Phase 5; the
    ``regular_child_completed`` branch's bus gate (``_bus_count_pending_for_target_sync``)
    checks the watcher count, and a pending watcher must exist for the
    child to be considered deliverable.
    """
    watcher = DependencyWatcher(
        source_task_id=source_task_id,
        target_instance_id=target_instance_id,
        follow_up_payload={"kind": "follow_up"},
        watcher_metadata={"child_id": source_task_id},
    )
    with Session(engine) as session:
        session.add(watcher)
        session.commit()


def _count_completion_reports(
    engine: Engine, *, parent_id: str, child_id: str
) -> int:
    """Count COMPLETION_REPORT messages enqueued for ``parent_id`` from
    ``child_id`` (any status — READY, PROCESSING, COMPLETED).
    """
    with Session(engine) as session:
        stmt = (
            select(func.count())
            .select_from(MessageQueue)
            .where(MessageQueue.instance_id == parent_id)
            .where(
                MessageQueue.source.like(f"internal_report:{child_id}:%")
            )
        )
        return int(session.scalar(stmt) or 0)


def _count_process_report_tasks(
    engine: Engine, *, parent_id: str
) -> int:
    """Count PROCESS_REPORT ``task`` rows attached to ``parent_id``
    (the report-lane fallback that the SAVEPOINT block must preserve).
    """
    with Session(engine) as session:
        stmt = (
            select(func.count())
            .select_from(Task)
            .where(Task.instance_id == parent_id)
            .where(Task.task_type == TaskType.PROCESS_REPORT.value)
        )
        return int(session.scalar(stmt) or 0)


def _read_instance_status(engine: Engine, instance_id: str) -> str | None:
    """Read current instance status from the DB (fresh-session)."""
    with Session(engine) as session:
        inst = session.get(Instance, instance_id)
        return inst.status if inst else None


def _count_injection_rows_for_triple(
    engine: Engine,
    *,
    parent_instance_id: str,
    child_instance_id: str,
    child_message_id: str,
) -> int:
    """Count ``ReportInjection`` rows for the obligation triple (any
    state). Used to assert only the pre-seeded row exists after the
    SAVEPOINT rollback — the natural INSERT was discarded.
    """
    with Session(engine) as session:
        stmt = (
            select(func.count())
            .select_from(ReportInjection)
            .where(ReportInjection.parent_instance_id == parent_instance_id)
            .where(ReportInjection.child_instance_id == child_instance_id)
            .where(ReportInjection.child_message_id == child_message_id)
        )
        return int(session.scalar(stmt) or 0)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def bus_repo(pg_repository_factory):
    """Real DependencyWatcherRepository bound to the PG engine."""
    return pg_repository_factory(DependencyWatcherRepository)


@pytest.fixture(autouse=True)
def bus(bus_repo):
    """Real DependencyBus singleton; auto-clears on teardown.

    The bus must be wired because ``_process_child_completion_db_sync``
    raises the A8/A9 hard error when the bus singleton is None (bus is
    the sole completion authority post-Phase 5). Starting the bus is
    not required — only ``count_pending_for_target_sync`` is exercised
    by the code under test (no ``watch()`` / ``emit_terminal()`` calls
    needed).
    """
    from daemon.services.dependency_bus import DependencyBus as _Bus

    b = _Bus(bus_repo)
    set_dependency_bus(b)
    try:
        yield b
    finally:
        set_dependency_bus(None)


# =============================================================================
# MB-1 — positive case: SAVEPOINT discards the duplicate injection INSERT
# =============================================================================


class TestSavepointRollbackDiscardsInjectionInsert:
    """MB-1 POSITIVE: the SAVEPOINT block in the C-DiD
    ``_process_child_completion_db_sync`` path rolls back ONLY the
    duplicate injection INSERT triggered by the natural completion
    path racing a recovered PENDING marker. The outer transaction
    commits the COMPLETED transition + completion_report message +
    PROCESS_REPORT task so the parent's report delivery is not
    silently dropped.

    The pre-F3 ``session.rollback()`` discarded the WHOLE transaction
    — wedging the child non-terminal forever and silencing the parent
    deferral. The F3 SAVEPOINT-scoped rollback is the canonical fix.

    PG-dialect contract: the partial unique index
    ``uq_report_injections_oblig_triple`` raises a real
    ``sqlalchemy.exc.IntegrityError`` whose ``str(exc.orig)`` carries
    the constraint name. The discriminator
    ``_is_obligation_triple_integrity_error`` narrows the catch to
    obligation-triple violations only — the SAVEPOINT rollback is
    applied but the outer transaction COMMITS.
    """

    def test_savepoint_rollback_discards_only_injection_insert(
        self, pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
    ):
        """Natural completion racing a recovered PENDING marker:
        SAVEPOINT boundary discards ONLY the duplicate injection
        INSERT; the outer commit preserves the child's COMPLETED
        transition + completion_report message + PROCESS_REPORT task.

        De-vacuous reach: the discriminator MUST be invoked
        (the C-DiD ``except`` block was entered). This is the same
        F1-de-vacuous pattern used by the SQLite unit test
        (``tests/unit/services/test_child_reports.py``
        ``test_natural_completion_races_recovered_pending_marker``).
        Without the discriminator invocation the test would pass
        even if the try/except were removed.
        """
        service = _build_child_reports_service(pg_engine)
        parent_id = "leader-mb1"
        child_id = "worker-mb1"
        child_msg_id = "msg-mb1-race"

        # Seed parent (root) + child (RUNNING — head guard at
        # ``child_reports.py:1754-1768`` falls through to the natural
        # C-DiD INSERT). Seed a bus watcher so the bus gate in
        # ``regular_child_completed`` passes.
        _seed_instance(pg_engine, instance_id=parent_id, parent_id=None)
        _seed_instance(
            pg_engine,
            instance_id=child_id,
            parent_id=parent_id,
            status=InstanceStatus.RUNNING.value,
        )
        _seed_dependency_watcher(
            pg_engine,
            target_instance_id=parent_id,
            source_task_id=f"task-{uuid.uuid4().hex[:8]}",
        )

        # Pre-seed the recovered PENDING row at the same obligation
        # triple. The natural completion INSERT will hit
        # ``uq_report_injections_oblig_triple`` and raise.
        seeded = _seed_pending_obligation(
            pg_engine,
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=child_msg_id,
        )

        # Reach instrumentation — wrap the discriminator with a
        # counting shim. The C-DiD ``except`` block MUST invoke the
        # discriminator; if the try/except is removed the count stays
        # at 0 and the test fails. Mirrors the F1-de-vacuous pattern.
        discriminator_calls = {"n": 0}
        real_discriminator = _is_obligation_triple_integrity_error

        def counting_discriminator(exc):
            discriminator_calls["n"] += 1
            return real_discriminator(exc)

        monkeypatch.setattr(
            "daemon.services.child_reports._is_obligation_triple_integrity_error",
            counting_discriminator,
        )

        # Run the natural completion path — the SAVEPOINT block is
        # the C-DiD INSERT site. F3 rollback: nested.rollback()
        # discards the duplicate INSERT; outer commit preserves the
        # COMPLETED transition + completion_report + PROCESS_REPORT.
        result = service._process_child_completion_db_sync(
            instance_id=child_id,
            completed_message_id=child_msg_id,
            last_content="natural completion, racing recovered row",
        )

        # Reach assertion: the discriminator MUST have been invoked.
        # The C-DiD ``except`` block was entered and consulted
        # ``_is_obligation_triple_integrity_error``. Without this, the
        # try/except could be removed and the test would still pass
        # on the outcome alone (the head guard also emits
        # ``idempotency_skip`` — but the head guard is short-circuited
        # by the RUNNING child status).
        assert discriminator_calls["n"] >= 1, (
            "the C-DiD IntegrityError catch was NOT entered — the "
            "discriminator was never consulted. Either the head guard "
            "short-circuited (vacuous test) or the try/except was "
            "removed. The branch must be exercised on PG."
        )

        # Outcome: natural × recovered PENDING race returns
        # ``idempotency_skip`` — the recovered row owns delivery via
        # worker_pool / claim_for_task_delivery.
        assert result.outcome == "idempotency_skip", (
            "natural completion racing recovered PENDING MUST return "
            "idempotency_skip on PG (the SAVEPOINT rollback absorbs "
            "the duplicate INSERT); got "
            f"outcome={result.outcome}"
        )

        # SAVEPOINT DISCARDS ONLY the injection INSERT — the pre-seeded
        # PENDING row is the SOLE row for the obligation triple. PG
        # constraint-name match MUST have fired (the discriminator
        # counted >= 1).
        post_count = _count_injection_rows_for_triple(
            pg_engine,
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=child_msg_id,
        )
        assert post_count == 1, (
            f"SAVEPOINT boundary broke: expected exactly 1 "
            f"ReportInjection row for the obligation triple "
            f"(the pre-seeded PENDING); got {post_count}"
        )

        # The pre-seeded PENDING row is preserved (no duplicate).
        with Session(pg_engine) as session:
            rows = session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.parent_instance_id == parent_id
                ).where(
                    ReportInjection.child_instance_id == child_id
                ).where(
                    ReportInjection.child_message_id == child_msg_id
                )
            ).all()
            assert len(rows) == 1
            assert rows[0].injection_id == seeded.injection_id, (
                "the pre-seeded PENDING row's PK MUST be preserved — "
                "no new row should have been added (SAVEPOINT "
                "discarded the natural INSERT)"
            )
            assert rows[0].state == ReportInjectionState.PENDING.value, (
                "the recovered PENDING row MUST remain PENDING — the "
                "natural path no-ops via idempotency_skip"
            )

        # OUTER COMMIT PRESERVES the COMPLETED transition.
        # Pre-fix (``session.rollback()``) this would be wedged
        # non-terminal — the F3 SAVEPOINT fix is load-bearing.
        with Session(pg_engine) as session:
            child_row = session.get(Instance, child_id)
            assert child_row.status == InstanceStatus.COMPLETED.value, (
                "F3 REGRESSION: child instance must transition to "
                "COMPLETED even when the C-DiD race fires (the "
                "SAVEPOINT preserves the outer transaction — only "
                "the injection INSERT was rolled back). Got "
                f"status={child_row.status!r}."
            )

        # OUTER COMMIT PRESERVES the completion_report message.
        # Pre-fix this would be lost (the whole transaction rolled
        # back). The completion_report is keyed by the
        # ``internal_report:{child_id}:{child_msg_id}`` source.
        assert _count_completion_reports(
            pg_engine, parent_id=parent_id, child_id=child_id
        ) == 1, (
            "the completion_report message MUST survive the SAVEPOINT "
            "rollback (it was flushed to the outer transaction in the "
            "pre-SAVEPOINT outer flush at ~:2725-2737)"
        )

        # OUTER COMMIT PRESERVES the PROCESS_REPORT task row.
        # Pre-fix this would be lost (whole transaction rolled
        # back). The task is the fallback delivery path when the
        # parent has no live turn.
        assert _count_process_report_tasks(
            pg_engine, parent_id=parent_id
        ) == 1, (
            "the PROCESS_REPORT task MUST survive the SAVEPOINT "
            "rollback (it was flushed to the outer transaction in the "
            "pre-SAVEPOINT outer flush at ~:2725-2737)"
        )

    def test_pg_integrity_error_carries_constraint_name_in_message(
        self, pg_engine: Engine
    ):
        """DE-VACUOUS support: a REAL PG IntegrityError raised by the
        partial unique index MUST carry the constraint name in
        ``str(exc.orig)``. The discriminator's PG branch
        (constraint-name match at ``child_reports.py:124``) depends on
        this PG-dialect emission.

        This test fires the same partial unique index that the
        SAVEPOINT block hits, captures the actual error, and asserts
        the constraint name is present. PG's emission format is
        ``duplicate key value violates unique constraint
        "uq_report_injections_oblig_triple"`` — locks the contract
        the discriminator depends on.
        """
        # Seed a PENDING row at one triple.
        parent_id = "leader-mb1-cons"
        child_id = "child-mb1-cons"
        child_msg_id = "msg-mb1-cons"
        _seed_instance(pg_engine, instance_id=parent_id, parent_id=None)
        _seed_instance(
            pg_engine,
            instance_id=child_id,
            parent_id=parent_id,
            status=InstanceStatus.RUNNING.value,
        )
        _seed_pending_obligation(
            pg_engine,
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=child_msg_id,
        )

        # Attempt a duplicate INSERT via the ORM (this is what the
        # SAVEPOINT block does). The partial unique index raises
        # ``sqlalchemy.exc.IntegrityError``; ``str(exc.orig)`` MUST
        # carry the constraint name on PG.
        try:
            with Session(pg_engine) as session:
                dup = ReportInjection(
                    parent_instance_id=parent_id,
                    child_instance_id=child_id,
                    child_message_id=child_msg_id,
                    report_message_id="report-dup",
                    content="dup",
                )
                session.add(dup)
                session.flush()
        except SAIntegrityError as exc:
            assert "uq_report_injections_oblig_triple" in str(exc.orig), (
                "PG IntegrityError MUST carry the constraint "
                "name in str(exc.orig) — the discriminator's PG "
                "branch (constraint-name match) depends on this. "
                f"Got: {str(exc.orig)!r}"
            )
            # Discriminator MUST return True for the real PG error.
            assert _is_obligation_triple_integrity_error(exc) is True, (
                "the PG branch of the discriminator MUST return True "
                "for a real obligation-triple IntegrityError"
            )
        else:
            pytest.fail(
                "expected a real PG IntegrityError from the partial "
                "unique index uq_report_injections_oblig_triple — "
                "the duplicate INSERT did not raise"
            )


# =============================================================================
# MB-1 — negative case: non-obligation IntegrityError re-raises
# =============================================================================


class TestUnrelatedIntegrityErrorPropagates:
    """MB-1 NEGATIVE: a non-obligation IntegrityError inside the
    SAVEPOINT block re-raises out of the block. The Y2 discriminator
    narrows the catch to obligation-triple violations only — FK or
    NOT NULL violations must surface so the bug is visible.

    The test injects a synthetic FK-style IntegrityError via a
    selective ``Session.flush`` patch so the SAVEPOINT block fires
    the discriminator. The original SQLite test
    (``tests/unit/services/test_child_reports.py``
    ``test_unrelated_integrity_error_propagates``) uses the same
    pattern. PG-specific: the biased error message contains NO
    obligation-triple column names, so the PG branch's
    constraint-name check returns False and the SQLite branch's
    column-set check also returns False.
    """

    def test_unrelated_integrity_error_reraises_out_of_savepoint(
        self, pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
    ):
        """An unrelated ``IntegrityError`` on the SAVEPOINT INSERT
        (e.g. an FK violation) MUST propagate — NOT be swallowed as
        ``idempotency_skip``. The discriminator
        ``_is_obligation_triple_integrity_error`` narrows the catch.

        Reach instrumentation: the discriminator MUST be called
        (the C-DiD ``except`` block was entered); the count must
        stay at 1, and the actor MUST re-raise the unrelated error.
        """
        from daemon.repositories.report_injection.models import ReportInjection as _RI
        from sqlmodel import Session as SQLModelSession

        # Reach instrumentation — wrap the discriminator with a
        # counting shim.
        discriminator_calls = {"n": 0}
        real_discriminator = _is_obligation_triple_integrity_error

        def counting_discriminator(exc):
            discriminator_calls["n"] += 1
            return real_discriminator(exc)

        monkeypatch.setattr(
            "daemon.services.child_reports._is_obligation_triple_integrity_error",
            counting_discriminator,
        )

        service = _build_child_reports_service(pg_engine)
        parent_id = "leader-mb1-neg"
        child_id = "child-mb1-neg"
        child_msg_id = "msg-mb1-neg"

        _seed_instance(pg_engine, instance_id=parent_id, parent_id=None)
        _seed_instance(
            pg_engine,
            instance_id=child_id,
            parent_id=parent_id,
            status=InstanceStatus.RUNNING.value,
        )
        _seed_dependency_watcher(
            pg_engine,
            target_instance_id=parent_id,
            source_task_id=f"task-{uuid.uuid4().hex[:8]}",
        )

        # Synthetic FK-style IntegrityError. Driver-side message
        # contains NO obligation-triple index name (PG FK emission
        # is ``insert or update on table "report_injections"
        # violates foreign key constraint "<name>"`` — different
        # constraint name).
        fk_orig = RuntimeError(
            'insert or update on table "report_injections" violates '
            'foreign key constraint "fake_parent_fk"'
        )
        synthetic_err = SAIntegrityError(
            "INSERT INTO report_injections (...)",
            params={},
            orig=fk_orig,
        )

        # Selective flush: only raises the synthetic error when a
        # ReportInjection is in session.new (the C-DiD INSERT
        # site). Earlier flushes (head-guard autoflush, F3 outer
        # flush for message_queue / task INSERTs) pass through to
        # the real flush so execution can reach the C-DiD INSERT.
        original_flush = SQLModelSession.flush
        flush_calls = {"n": 0}

        def _selective_flush(self, *args, **kwargs):
            flush_calls["n"] += 1
            new_objs = list(getattr(self, "new", set()) or set())
            if any(isinstance(obj, _RI) for obj in new_objs):
                raise synthetic_err
            return original_flush(self, *args, **kwargs)

        monkeypatch.setattr(SQLModelSession, "flush", _selective_flush)

        # The natural completion path MUST propagate the unrelated
        # error — NOT swallow it as ``idempotency_skip``.
        with pytest.raises(SAIntegrityError) as exc_info:
            service._process_child_completion_db_sync(
                instance_id=child_id,
                completed_message_id=child_msg_id,
                last_content="would race if it could",
            )

        # Reach assertion: the C-DiD ``except`` block MUST have been
        # entered — the discriminator was consulted.
        assert discriminator_calls["n"] >= 1, (
            "the C-DiD IntegrityError catch was NOT entered — the "
            "discriminator was never consulted. Either the head guard "
            "short-circuited (vacuous test) or the try/except was "
            "removed. The branch must be exercised."
        )

        # The propagated error is the SAME synthetic error we
        # injected (no wrapping — bare ``raise``).
        assert exc_info.value is synthetic_err, (
            "unrelated IntegrityError MUST be re-raised as-is (bare "
            "``raise``); wrapping would hide the original exception"
        )

        # The outer flush MUST have run at least once before the
        # C-DiD selective flush fired (the F3 outer flush at
        # ~:2725-2737 sets up the message_queue + task INSERTs).
        assert flush_calls["n"] >= 1, (
            "the patched Session.flush MUST have been invoked at "
            "least once before the error propagated (the selective "
            "flush only raises on ReportInjection in session.new; "
            "this confirms the outer F3 flush ran first)"
        )

        # Belt-and-braces: the synthetic FK error does NOT match
        # the obligation-triple rule.
        assert real_discriminator(synthetic_err) is False, (
            "the synthetic FK error MUST NOT be mis-classified as "
            "obligation-triple (it does not contain the obligation-"
            "triple index name)"
        )

    def test_non_integrity_error_inside_savepoint_rolls_back_and_propagates(
        self, pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
    ):
        """F4 hardening (73bfe0ed): a non-IntegrityError raised
        inside the SAVEPOINT block (e.g. an ``OperationalError`` on
        DB disconnect, a ``RuntimeError`` from a downstream invariant)
        MUST:

        1. Trigger ``nested.rollback()`` (the broadened ``except
           Exception`` restores the SAVEPOINT boundary for ANY
           exception class, not just IntegrityError).
        2. Propagate the exception with identity preserved (bare
           ``raise`` — no wrapping).
        3. Result in coarse-rollback containment: the WHOLE outer
           transaction is rolled back by the WriteGuardSession close
           (no orphaned injection row, no half-flushed message_queue
           / task rows, child status stays non-terminal).

        Pre-F4: the SAVEPOINT was left open and was contained only
        by the outer ``WriteGuardSession`` close — data-safe but
        coarse, and the leaked SAVEPOINT relied on close-time
        cleanup. The fix broadens the catch to ``except Exception``
        so ``nested.rollback()`` fires for ANY exception raised
        inside the inner try BEFORE the SAVEPOINT leaks.

        Reach instrumentation: the broadened ``except Exception``
        block MUST emit its warning log line; if the catch is
        removed or narrowed back to ``IntegrityError``, the count
        stays at 0 and the test fails.
        """
        from daemon.repositories.report_injection.models import ReportInjection as _RI
        from sqlmodel import Session as SQLModelSession
        from daemon.services import child_reports as cr_module

        # Reach instrumentation — count warning emissions from the
        # broadened ``except Exception`` block. The fix emits a
        # ``logger.warning`` with the prefix "non-IntegrityError
        # raised during ReportInjection INSERT" whenever the
        # broadened catch fires.
        non_ie_warning_calls = {"n": 0}
        original_warning = cr_module.logger.warning

        def counting_warning(msg, *args, **kwargs):
            if "non-IntegrityError raised during ReportInjection INSERT" in str(msg):
                non_ie_warning_calls["n"] += 1
            return original_warning(msg, *args, **kwargs)

        monkeypatch.setattr(cr_module.logger, "warning", counting_warning)

        service = _build_child_reports_service(pg_engine)
        parent_id = "leader-mb1-f4"
        child_id = "child-mb1-f4"
        child_msg_id = "msg-mb1-f4"

        _seed_instance(pg_engine, instance_id=parent_id, parent_id=None)
        _seed_instance(
            pg_engine,
            instance_id=child_id,
            parent_id=parent_id,
            status=InstanceStatus.RUNNING.value,
        )
        _seed_dependency_watcher(
            pg_engine,
            target_instance_id=parent_id,
            source_task_id=f"task-{uuid.uuid4().hex[:8]}",
        )

        # Synthetic non-IntegrityError (RuntimeError is the simplest
        # non-IE exception that models a downstream invariant
        # failure). Identity-preservation is asserted below via
        # ``is``.
        synthetic_err = RuntimeError(
            "synthetic transient failure on connection.execute"
        )

        # Selective flush: only raises the synthetic error when a
        # ReportInjection is in session.new (the C-DiD INSERT
        # site). Earlier flushes (head-guard autoflush, F3 outer
        # flush for message_queue / task INSERTs) pass through to
        # the real flush so execution can reach the C-DiD INSERT.
        original_flush = SQLModelSession.flush
        flush_calls = {"n": 0}

        def _selective_flush(self, *args, **kwargs):
            flush_calls["n"] += 1
            new_objs = list(getattr(self, "new", set()) or set())
            if any(isinstance(obj, _RI) for obj in new_objs):
                raise synthetic_err
            return original_flush(self, *args, **kwargs)

        monkeypatch.setattr(SQLModelSession, "flush", _selective_flush)

        # The natural completion path MUST surface the
        # non-IntegrityError — NOT swallow it. Bare ``raise``
        # preserves identity.
        with pytest.raises(RuntimeError) as exc_info:
            service._process_child_completion_db_sync(
                instance_id=child_id,
                completed_message_id=child_msg_id,
                last_content="would fail at flush if it could",
            )

        # Exception identity preserved — bare ``raise``, no wrapping.
        assert exc_info.value is synthetic_err, (
            "non-IntegrityError MUST be re-raised as-is (bare "
            "``raise``); wrapping would hide the original exception"
        )

        # Reach assertion: the broadened ``except Exception`` block
        # MUST have been entered — the warning was emitted. If the
        # except is removed or narrowed back to IntegrityError, the
        # count stays at 0 and the test fails.
        assert non_ie_warning_calls["n"] >= 1, (
            "the C-DiD except-Exception was NOT entered — the "
            "non-IntegrityError was never caught. The broadened catch "
            "is required so nested.rollback() fires for ANY "
            "exception, not just IntegrityError. If the except "
            "Exception was removed or narrowed, the SAVEPOINT leak "
            "returns."
        )

        # Reach assertion: the patched flush MUST have been invoked
        # at least once before the error propagated (the selective
        # flush only raises on ReportInjection in session.new; this
        # confirms the outer F3 flush ran first).
        assert flush_calls["n"] >= 1, (
            "the patched Session.flush MUST have been invoked at "
            "least once before the error propagated (the selective "
            "flush only raises on ReportInjection in session.new; "
            "this confirms the outer F3 flush ran first)"
        )

        # SAVEPOINT ROLLBACK CONTAINMENT: the broadened catch
        # triggered ``nested.rollback()`` AND the outer
        # WriteGuardSession close rolled back the WHOLE transaction
        # (the non-IntegrityError escapes the SAVEPOINT block, the
        # outer session's rollback() takes the rest). Verifies:
        #
        # 1. No orphan ReportInjection row for the obligation triple.
        # 2. No completion_report message_queue row appended.
        # 3. No PROCESS_REPORT task row appended.
        # 4. Child did NOT transition to COMPLETED.
        with Session(pg_engine) as session:
            inj_rows = session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.parent_instance_id == parent_id
                ).where(
                    ReportInjection.child_instance_id == child_id
                ).where(
                    ReportInjection.child_message_id == child_msg_id
                )
            ).all()
            assert len(inj_rows) == 0, (
                "F4 REGRESSION: no ReportInjection row should exist "
                "for the obligation triple (SAVEPOINT was rolled "
                f"back). Got {len(inj_rows)} orphan row(s)."
            )

        assert _count_completion_reports(
            pg_engine, parent_id=parent_id, child_id=child_id
        ) == 0, (
            "F4 REGRESSION: no completion_report message_queue row "
            "should exist (outer tx rolled back)"
        )

        assert _count_process_report_tasks(
            pg_engine, parent_id=parent_id
        ) == 0, (
            "F4 REGRESSION: no PROCESS_REPORT task row should exist "
            "(outer tx rolled back)"
        )

        child_row_status = _read_instance_status(pg_engine, child_id)
        assert child_row_status == InstanceStatus.RUNNING.value, (
            "F4 REGRESSION: child must NOT transition to COMPLETED on "
            "a non-IntegrityError (outer tx rolled back, COMPLETED "
            f"transition lost). Got status={child_row_status!r}."
        )
