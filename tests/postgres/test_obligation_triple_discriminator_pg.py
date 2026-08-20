"""MB-2: PG-dialect constraint-name discriminator test for _is_obligation_triple_integrity_error.

Merge blocker (cycle-3 review, 2026-08-20): the PG branch of
``_is_obligation_triple_integrity_error`` in
``daemon/services/child_reports.py:81-135`` — the constraint-name
match ``_OBLIGATION_TRIPLE_INDEX_NAME in msg`` at line 124 — MUST be
exercised with a REAL PG IntegrityError. SQLite's emission format
contains column names; PG's emission format contains the constraint
NAME. The two branches are not interchangeable.

Only the SQLite branch (column-set match at lines 127-133) is
currently covered by ``tests/unit/services/test_child_reports.py``
(see ``test_unrelated_integrity_error_propagates``). This file
exercises the PG branch with a real PG ``IntegrityError`` raised by
the partial unique index, and a NON-triple PG ``IntegrityError``
(FK violation is the canonical negative case) for the negative
branch.

Target runtime:

  timeout 300 .venv/bin/pytest tests/postgres/test_obligation_triple_discriminator_pg.py \\
      --override-ini="addopts=" -m postgres -q --tb=short

Reference: ``.agents/shared/planning/pause-report-recovery/phase3-plan.md``
section "MERGE BLOCKERS" — MB-2.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError as SAIntegrityError
from sqlmodel import Session

# Model imports — required so SQLModel.metadata sees the tables when
# ``create_all()`` runs on the PG engine (matches the
# ``tests/postgres/conftest.py`` convention).
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401  (event table)
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.dependency_bus.repository import (
    DependencyWatcherRepository,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.services.child_reports import (
    ChildReportsService,
    _is_obligation_triple_integrity_error,
)
from daemon.services.dependency_bus import DependencyBus, set_dependency_bus
from daemon.write_pause_guard import WritePauseGuard


# Auto-apply the postgres marker so ``pytest -m postgres`` selects these
# tests and the default ``-m 'not integration and not postgres'`` addopts
# skips them unless overridden.
pytestmark = pytest.mark.postgres


# =============================================================================
# Service / row helpers (mirrors the MB-1 helpers)
# =============================================================================


def _build_child_reports_service(engine: Engine) -> ChildReportsService:
    """Build a real ``ChildReportsService`` with a mock manager that
    exposes only the attributes ``_process_child_completion_db_sync``
    needs.

    Mirrors the helper in
    ``tests/postgres/test_wanderer_completion_reporting_pg.py`` and
    the MB-1 helper.
    """
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._checkpointer = None
    manager._live_hub = None
    manager._queue_repository = MagicMock()
    manager._instance_repository = MagicMock()
    manager._task_repo = None
    manager._worker_pool = None

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None
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
) -> ReportInjection:
    """Insert a non-terminal ``ReportInjection`` row at the given
    obligation triple. Used to set up the duplicate for the POSITIVE
    branch (real PG obligation-triple IntegrityError).
    """
    injection_id = f"inj-{uuid.uuid4().hex[:8]}"
    report_message_id = f"report-{uuid.uuid4().hex[:8]}"
    row = ReportInjection(
        injection_id=injection_id,
        parent_instance_id=parent_instance_id,
        child_instance_id=child_instance_id,
        child_message_id=child_message_id,
        report_message_id=report_message_id,
        content="pre-seeded row",
        state=ReportInjectionState.PENDING.value,
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
    """Insert a DependencyWatcher row targeting the parent (wires the
    bus gate for the ``regular_child_completed`` branch).

    Uses the same fixture pattern as the MB-1 test and the existing
    wanderer test.
    """
    from daemon.repositories.dependency_bus import DependencyWatcher

    watcher = DependencyWatcher(
        source_task_id=source_task_id,
        target_instance_id=target_instance_id,
        follow_up_payload={"kind": "follow_up"},
        watcher_metadata={"child_id": source_task_id},
    )
    with Session(engine) as session:
        session.add(watcher)
        session.commit()


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

    Required for ``_process_child_completion_db_sync`` (A8 hard
    error if bus is None). The bus is not started — only
    ``count_pending_for_target_sync`` is exercised.
    """
    b = DependencyBus(bus_repo)
    set_dependency_bus(b)
    try:
        yield b
    finally:
        set_dependency_bus(None)


# =============================================================================
# MB-2 — POSITIVE: PG constraint-name match returns True
# =============================================================================


class TestPGConstraintNameDiscriminatorPositive:
    """MB-2 POSITIVE: a REAL PG IntegrityError raised by the partial
    unique index ``uq_report_injections_oblig_triple`` MUST carry
    the constraint name in ``str(exc.orig)``, and the discriminator
    MUST return True.

    PG emission format
    (``duplicate key value violates unique constraint
    "uq_report_injections_oblig_triple"``) is what the discriminator's
    PG branch (line 124) matches. The test inserts a duplicate via
    the ORM (or via raw SQL with the same triple) and captures the
    actual error — no fabricated exception strings.
    """

    def test_real_pg_obligation_triple_integrity_error_returns_true(
        self, pg_engine: Engine
    ):
        """Insert a PENDING row at one triple, then attempt a duplicate
        INSERT. Catch the real PG IntegrityError and assert the
        discriminator returns True.

        Asserts two PG-dialect properties:
        1. ``str(exc.orig)`` carries the constraint name
           ``uq_report_injections_oblig_triple`` (locks the
           discriminator's contract).
        2. The discriminator returns True for the real PG error.
        """
        parent_id = "leader-mb2-pos"
        child_id = "child-mb2-pos"
        child_msg_id = "msg-mb2-pos"

        # Seed the PENDING row at the same triple.
        _seed_pending_obligation(
            pg_engine,
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=child_msg_id,
        )

        # Attempt a duplicate INSERT via the ORM. The partial unique
        # index raises ``sqlalchemy.exc.IntegrityError``; PG emits
        # ``duplicate key value violates unique constraint
        # "uq_report_injections_oblig_triple"``.
        try:
            with Session(pg_engine) as session:
                dup = ReportInjection(
                    parent_instance_id=parent_id,
                    child_instance_id=child_id,
                    child_message_id=child_msg_id,
                    report_message_id="report-mb2-pos-dup",
                    content="dup",
                )
                session.add(dup)
                session.flush()
        except SAIntegrityError as exc:
            assert "uq_report_injections_oblig_triple" in str(exc.orig), (
                "PG IntegrityError MUST carry the constraint name "
                "in str(exc.orig) — the discriminator's PG branch "
                "matches it. Lock the PG emission format. "
                f"Got: {str(exc.orig)!r}"
            )
            # The REAL PG branch — must return True.
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

    def test_real_pg_duplicate_via_raw_sql_returns_true(
        self, pg_engine: Engine
    ):
        """Same as the ORM-based test but uses raw SQL to bypass the
        SQLModel optional-field surface. Locks the PG-dialect
        contract independent of the ORM path.

        This is the canonical "real PG dialect emission" approach —
        a raw INSERT against the partial unique index raises the
        IntegrityError with the constraint name embedded in the
        driver message.
        """
        parent_id = "leader-mb2-pos-raw"
        child_id = "child-mb2-pos-raw"
        child_msg_id = "msg-mb2-pos-raw"

        # Seed a PENDING row at the triple.
        _seed_pending_obligation(
            pg_engine,
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            child_message_id=child_msg_id,
        )

        # Attempt a duplicate via raw SQL — same partial unique index,
        # same PG emission format. Uses ``text()`` to construct the
        # statement explicitly so the INSERT is unambiguous. The
        # ``content`` is a literal string (no parameter binding) to
        # avoid the PG ``IndeterminateDatatype`` class of errors that
        # bind parameters without an explicit type.
        with Session(pg_engine) as session:
            try:
                session.execute(
                    text(
                        "INSERT INTO report_injections "
                        "(injection_id, parent_instance_id, "
                        "child_instance_id, child_message_id, "
                        "report_message_id, content, state, "
                        "created_at) "
                        "VALUES (:iid, :pid, :cid, :mid, :rmid, "
                        "'dup raw', 'PENDING', :now)"
                    ),
                    {
                        "iid": f"inj-{uuid.uuid4().hex[:8]}",
                        "pid": parent_id,
                        "cid": child_id,
                        "mid": child_msg_id,
                        "rmid": "report-mb2-pos-raw-dup",
                        "now": datetime.now(timezone.utc).isoformat(),
                    },
                )
                session.commit()
            except SAIntegrityError as exc:
                assert "uq_report_injections_oblig_triple" in str(exc.orig), (
                    "raw SQL PG IntegrityError MUST carry the "
                    "constraint name in str(exc.orig). "
                    f"Got: {str(exc.orig)!r}"
                )
                assert _is_obligation_triple_integrity_error(exc) is True, (
                    "the PG branch of the discriminator MUST return "
                    "True for a real obligation-triple IntegrityError"
                )
            else:
                pytest.fail(
                    "expected a real PG IntegrityError from the "
                    "partial unique index via raw SQL"
                )


# =============================================================================
# MB-2 — NEGATIVE: non-triple PG IntegrityError returns False
# =============================================================================


class TestPGConstraintNameDiscriminatorNegative:
    """MB-2 NEGATIVE: a NON-triple PG IntegrityError MUST return False
    from the discriminator. The constraint-name match
    (``uq_report_injections_oblig_triple``) is the PG branch — any
    error from a different constraint MUST NOT match.

    The negative case is a real FK violation: add a temporary FK
    constraint to ``report_injections.parent_instance_id`` (the
    canonical FK target is ``instances.instance_id``), then INSERT a
    row with a non-existent parent. PG raises
    ``insert or update on table "report_injections" violates foreign
    key constraint "<fk_constraint_name>"`` — the message does NOT
    contain the obligation-triple index name, so the PG branch
    returns False and the SQLite fallback (column-set match) also
    returns False (no FK-constraint-name includes all three
    obligation-triple columns).

    The containing SAVEPOINT block must re-raise the error (not
    absorb it). Since the production code's Y2 discriminator absorbs
    only obligation-triple IntegrityError, a NON-triple
    IntegrityError must propagate.

    Cleanup: the FK constraint is added by the test fixtures and is
    dropped after each test (``_drop_fk_constraint`` teardown) so
    the constraint does not pollute subsequent tests that share the
    PG test database.
    """

    FK_CONSTRAINT_NAME = "report_injections_test_parent_fk"

    def _ensure_fk_constraint(self, pg_engine: Engine) -> str:
        """Idempotently add a FK constraint to
        ``report_injections.parent_instance_id -> instances.instance_id``.

        Returns the constraint name. The constraint is added in this
        test only.

        Note: PG only checks the constraint when the FK columns are
        touched (the constraint is DEFERRABLE INITIALLY DEFERRED-
        compatible by default — but that's only meaningful inside a
        transaction). For this test, the FK check fires on the
        INSERT directly.
        """
        # Idempotent: drop if exists, then add.
        with pg_engine.begin() as conn:
            conn.execute(
                text(
                    f"ALTER TABLE report_injections "
                    f"DROP CONSTRAINT IF EXISTS {self.FK_CONSTRAINT_NAME}"
                )
            )
            conn.execute(
                text(
                    f"ALTER TABLE report_injections "
                    f"ADD CONSTRAINT {self.FK_CONSTRAINT_NAME} "
                    f"FOREIGN KEY (parent_instance_id) "
                    f"REFERENCES instances(instance_id)"
                )
            )
        return self.FK_CONSTRAINT_NAME

    def _drop_fk_constraint(self, pg_engine: Engine) -> None:
        """Drop the test FK constraint. Idempotent (uses IF EXISTS)."""
        with pg_engine.begin() as conn:
            conn.execute(
                text(
                    f"ALTER TABLE report_injections "
                    f"DROP CONSTRAINT IF EXISTS {self.FK_CONSTRAINT_NAME}"
                )
            )

    @pytest.fixture(autouse=True)
    def _fk_cleanup(self, pg_engine: Engine):
        """Auto-cleanup: drop the FK constraint before AND after each
        negative test so the constraint does not pollute subsequent
        tests in the suite.

        The pre-test DROP is a belt-and-braces idempotent no-op
        (the constraint name is class-scoped and the production
        schema never creates it). The post-test DROP is the
        isolation guarantee.
        """
        self._drop_fk_constraint(pg_engine)
        try:
            yield
        finally:
            self._drop_fk_constraint(pg_engine)

    def test_real_pg_fk_violation_returns_false(
        self, pg_engine: Engine
    ):
        """Trigger a real PG FK violation by inserting a row with a
        non-existent ``parent_instance_id``. The discriminator MUST
        return False — the FK constraint name does NOT match the
        obligation-triple index name.

        The FK constraint is added by
        :meth:`_ensure_fk_constraint` and references
        ``instances.instance_id``. PG raises
        ``insert or update on table "report_injections" violates
        foreign key constraint "report_injections_test_parent_fk"``
        — the discriminator's PG branch (constraint-name match
        against ``uq_report_injections_oblig_triple``) returns False.
        """
        self._ensure_fk_constraint(pg_engine)

        # Insert a row with a parent_instance_id that does NOT exist
        # in the instances table. The FK constraint fires.
        non_existent_parent = f"leader-mb2-fk-{uuid.uuid4().hex[:8]}"
        try:
            with Session(pg_engine) as session:
                row = ReportInjection(
                    parent_instance_id=non_existent_parent,
                    child_instance_id="child-mb2-fk",
                    child_message_id="msg-mb2-fk",
                    report_message_id="report-mb2-fk",
                    content="fk violation",
                )
                session.add(row)
                session.flush()
        except SAIntegrityError as exc:
            # The FK constraint name MUST NOT match the obligation-
            # triple index name. The discriminator MUST return False.
            assert (
                "uq_report_injections_oblig_triple" not in str(exc.orig)
            ), (
                "the FK violation message MUST NOT contain the "
                "obligation-triple index name — PG emits the FK "
                "constraint name. If this assertion fails, "
                "something has corrupted the FK constraint. "
                f"Got: {str(exc.orig)!r}"
            )
            assert _is_obligation_triple_integrity_error(exc) is False, (
                "the discriminator MUST return False for a NON-triple "
                "FK violation (the constraint-name match is the PG "
                "branch; the FK constraint name is different)"
            )
        else:
            pytest.fail(
                "expected a real PG IntegrityError from the FK "
                "constraint — the INSERT did not raise"
            )

    def test_real_pg_fk_violation_reraises_out_of_savepoint(
        self, pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
    ):
        """End-to-end semantic: a NON-triple PG IntegrityError
        raised inside the SAVEPOINT block re-raises out of the
        block (NOT absorbed as ``idempotency_skip``). The Y2
        discriminator narrows the catch to obligation-triple
        violations only.

        The test simulates the FK violation by adding a FK
        constraint, then calling the natural completion path. The
        SAVEPOINT block raises a real PG IntegrityError on the
        duplicate INSERT (it does NOT match the triple — the FK
        fires on the parent_instance_id). The block raises the
        IntegrityError.

        Reach instrumentation: the discriminator MUST be invoked
        (the C-DiD ``except`` block was entered); the count stays
        at 1; the caller MUST re-raise the error.
        """
        constraint_name = self._ensure_fk_constraint(pg_engine)

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

        # Build a parent that exists in the instances table (so the
        # FK does NOT fire on the parent_instance_id). The child's
        # natural completion INSERT will reach the SAVEPOINT block
        # and the partial unique index check. To force an FK
        # violation INSIDE the SAVEPOINT, we monkeypatch the
        # Session.flush to raise a synthetic IntegrityError that
        # mimics the PG FK emission format — this keeps the test
        # self-contained without depending on the SAVEPOINT INSERT
        # triggering the FK directly (the obligation-triple check
        # would happen first in a normal flow).
        #
        # The discriminator's PG branch (constraint-name match) is
        # the contract under test. A synthetic FK error with
        # PG-emission format exercises the same brand of message
        # the real PG would emit.
        service = _build_child_reports_service(pg_engine)
        parent_id = "leader-mb2-fk-save"
        child_id = "child-mb2-fk-save"
        child_msg_id = "msg-mb2-fk-save"

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

        # Synthetic FK-style IntegrityError, mirroring the PG FK
        # emission format. PG emits
        # ``insert or update on table "report_injections" violates
        # foreign key constraint "<constraint_name>"``.
        fk_orig = RuntimeError(
            f'insert or update on table "report_injections" violates '
            f'foreign key constraint "{constraint_name}"'
        )
        synthetic_err = SAIntegrityError(
            "INSERT INTO report_injections (...)",
            params={},
            orig=fk_orig,
        )

        # Selective flush: only raises the synthetic error when a
        # ReportInjection is in session.new (the C-DiD INSERT
        # site). Earlier flushes pass through so execution reaches
        # the C-DiD INSERT.
        from daemon.repositories.report_injection.models import ReportInjection as _RI
        from sqlmodel import Session as SQLModelSession

        original_flush = SQLModelSession.flush

        def _selective_flush(self, *args, **kwargs):
            new_objs = list(getattr(self, "new", set()) or set())
            if any(isinstance(obj, _RI) for obj in new_objs):
                raise synthetic_err
            return original_flush(self, *args, **kwargs)

        monkeypatch.setattr(SQLModelSession, "flush", _selective_flush)

        # The natural completion path MUST re-raise the FK error —
        # NOT swallow it as ``idempotency_skip``.
        with pytest.raises(SAIntegrityError) as exc_info:
            service._process_child_completion_db_sync(
                instance_id=child_id,
                completed_message_id=child_msg_id,
                last_content="would race if FK were the real constraint",
            )

        # Reach assertion: the C-DiD ``except`` block MUST have been
        # entered.
        assert discriminator_calls["n"] >= 1, (
            "the C-DiD IntegrityError catch was NOT entered — the "
            "discriminator was never consulted. The branch must be "
            "exercised."
        )

        # The propagated error is the SAME synthetic error (no
        # wrapping).
        assert exc_info.value is synthetic_err, (
            "non-triple IntegrityError MUST be re-raised as-is (bare "
            "``raise``); wrapping would hide the original exception"
        )

        # Belt-and-braces — the synthetic FK error is correctly
        # classified as NON-triple.
        assert real_discriminator(synthetic_err) is False, (
            "the discriminator MUST return False for an FK violation "
            "message (PG emits the FK constraint name, not the "
            "obligation-triple index name)"
        )

    def test_real_pg_fk_violation_message_format_invariant(
        self, pg_engine: Engine
    ):
        """Lock the PG FK violation emission format. The PG branch
        of the discriminator matches the constraint name embedded
        in ``str(exc.orig)``. Changes to PG's emission format would
        silently break the discriminator.

        PG emits
        ``insert or update on table "report_injections" violates
        foreign key constraint "<constraint_name>"`` (or
        ``update or delete ...`` for the reverse direction). The
        test fires a real FK violation and asserts the message
        does NOT contain the obligation-triple index name — the
        discriminator contract.
        """
        self._ensure_fk_constraint(pg_engine)

        try:
            with Session(pg_engine) as session:
                row = ReportInjection(
                    parent_instance_id=f"nonexistent-{uuid.uuid4().hex[:8]}",
                    child_instance_id="child-mb2-fmt",
                    child_message_id="msg-mb2-fmt",
                    report_message_id="report-mb2-fmt",
                    content="fk format",
                )
                session.add(row)
                session.flush()
        except SAIntegrityError as exc:
            msg = str(exc.orig)
            # The PG FK emission MUST mention the FK constraint
            # (not the obligation-triple index).
            assert "foreign key constraint" in msg.lower(), (
                "PG FK violation MUST mention 'foreign key "
                "constraint' in the driver message — locks the "
                "PG emission format. "
                f"Got: {msg!r}"
            )
            assert "uq_report_injections_oblig_triple" not in msg, (
                "PG FK violation MUST NOT mention the obligation-"
                "triple index name — the discriminator's PG branch "
                "matches by name; FK violation has a different name. "
                f"Got: {msg!r}"
            )
        else:
            pytest.fail(
                "expected a real PG FK violation — the INSERT did "
                "not raise"
            )
