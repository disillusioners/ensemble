"""IntegrityError subtype classification for ``ensure_deferred`` (2026-09-08).

Regression coverage for the b7ead8a4/d90b18f9 production incident
follow-on: production logs 2026-09-08 15:27:09 +07 showed the sweep
hitting ``psycopg.errors.NotNullViolation: null value in column
"content" of relation "report_injections" violates not-null constraint``
on every pass — leader b7ead8a4 / children aae1539c / 8629bc77 /
50b7c9a9 stayed DEFERRED forever.

Root cause: the pre-fix ``ensure_deferred`` routed ALL
``IntegrityError`` instances through the same phantom-conflict
re-read + insert-on-missing path. The phantom-conflict path is
designed exclusively for the obligation-triple unique violation
(W6 — concurrent INSERTs racing the partial unique index). A
deterministic constraint violation (NOT NULL / FOREIGN KEY / CHECK /
a different UNIQUE) routed through that path produced two
production-visible failures:

1. The misleading "phantom conflict (row deleted or escalated between
   INSERT and re-read)" log fired for what was actually a
   schema-mismatch NotNull violation.
2. The insert-on-missing retry ALSO raised NotNull (same INSERT
   params → same violation), so the row was stranded forever.

Corrected contract pinned here:

* **Obligation-triple unique violation** → existing convergence
  path (W6 / Debug Phase 4 insert-on-missing; absorbs a legitimate
  concurrent INSERT race).
* **Any OTHER IntegrityError** → immediate truthful re-raise with
  a deterministic-error log; NO phantom-conflict retry; NO
  insert-on-missing second-INSERT; the per-row caller (sweep /
  Site 1 dispatcher) logs+counts errors so the row is retried next
  cycle after the schema mismatch is fixed.

Discriminator: ``_is_obligation_triple_unique_violation`` (module
helper in ``daemon/repositories/report_injection/repository.py``)
matches the constraint NAME (PG renders it in
``exc.orig``/``exc.orig.diag.constraint_name``) OR the obligation-
triple column SET (SQLite renders the columns). Mirrors the
contract at ``daemon/services/child_reports.py::
_is_obligation_triple_integrity_error``.

Engine: file-backed SQLite at ``tmp_path`` with NullPool + WAL +
busy_timeout=10000 (repo conventions; never StaticPool+WriteGuard —
QUARANTINE).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, text as sa_text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select as sm_select

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.constants import (
    DEFERRED_REASON_PENDING_MESSAGES,
    DEFERRED_REASON_RESUME_ROUTER,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout)."""
    db_path = tmp_path / "ensure-deferred-classification-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repo(engine: Engine) -> ReportInjectionRepository:
    return ReportInjectionRepository(engine=engine)


def _triple() -> tuple[str, str, str]:
    """Fresh (parent, child, child_message_id) obligation triple."""
    return (
        f"parent-{uuid.uuid4().hex[:8]}",
        f"child-{uuid.uuid4().hex[:8]}",
        f"msg-{uuid.uuid4().hex[:8]}",
    )


def _seed_row(
    engine: Engine,
    *,
    parent: str,
    child: str,
    msg: str,
    state: str,
    report_message_id: str | None = None,
    content: str | None = "queued report",
    deferred_reason: str | None = None,
) -> str:
    """Insert one ReportInjection row directly. Returns injection_id."""
    injection_id = str(uuid.uuid4())
    with Session(engine) as session:
        session.add(
            ReportInjection(
                injection_id=injection_id,
                parent_instance_id=parent,
                child_instance_id=child,
                child_message_id=msg,
                report_message_id=report_message_id,
                content=content,
                state=state,
                deferred_reason=deferred_reason,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        session.commit()
    return injection_id


def _all_rows(
    engine: Engine, *, parent: str, child: str, msg: str
) -> list[ReportInjection]:
    with Session(engine) as session:
        return list(
            session.exec(
                sm_select(ReportInjection)
                .where(ReportInjection.parent_instance_id == parent)
                .where(ReportInjection.child_instance_id == child)
                .where(ReportInjection.child_message_id == msg)
            ).all()
        )


# ─── Deterministic violations → immediate truthful re-raise ─────────────────


def test_not_null_violation_raises_immediately_no_retry(
    repo, engine, caplog, monkeypatch
):
    """NOT NULL violation re-raises IMMEDIATELY.

    The 2026-09-08 prod incident: leader b7ead8a4 children
    aae1539c / 8629bc77 / 50b7c9a9 stayed DEFERRED forever because
    ``content=None`` raised NotNullViolation on every sweep pass.
    The pre-fix code routed the error through the phantom-conflict
    re-read + insert-on-missing retry — both raised NotNullViolation
    (same INSERT params → same error) and the row was stranded.

    Pin: NOT NULL → immediate re-raise, NO second INSERT,
    the misleading "phantom conflict" log never fires.
    """
    parent, child, msg = _triple()
    real_insert = repo._insert_deferred_marker
    insert_calls = {"n": 0}

    def not_null_failing_insert(**_kwargs):
        insert_calls["n"] += 1
        # Realistic PG-format message — same shape psycopg emits.
        raise IntegrityError(
            "INSERT INTO report_injections ...",
            {},
            Exception(
                'null value in column "content" of relation '
                '"report_injections" violates not-null constraint'
            ),
        )

    monkeypatch.setattr(repo, "_insert_deferred_marker", not_null_failing_insert)

    with caplog.at_level("ERROR", logger=(
        "daemon.repositories.report_injection.repository"
    )):
        with pytest.raises(IntegrityError) as exc_info:
            repo.ensure_deferred(
                parent_instance_id=parent,
                child_instance_id=child,
                child_message_id=msg,
                deferred_reason=DEFERRED_REASON_RESUME_ROUTER,
            )

    # EXACTLY ONE insert attempt — no phantom-conflict retry.
    assert insert_calls["n"] == 1, (
        "deterministic NOT NULL violation must not trigger a "
        "second INSERT (insert-on-missing is for unique-violation "
        "races only)"
    )
    # No row landed.
    assert _all_rows(engine, parent=parent, child=child, msg=msg) == []
    # The truthful log fired.
    assert any(
        "deterministic IntegrityError" in r.message
        and "NOT a delivery race" in r.message
        for r in caplog.records
    ), "the deterministic-error log must fire for operator visibility"
    # The misleading phantom-conflict log did NOT fire.
    assert not any(
        "phantom conflict" in r.message
        or "insert-on-missing" in r.message
        for r in caplog.records
    ), "the misleading phantom-conflict log must NEVER fire for a "
    "deterministic constraint violation"
    # The exception propagated (no swallowing).
    assert "not-null constraint" in str(exc_info.value.orig)


def test_foreign_key_violation_raises_immediately_no_retry(
    repo, engine, caplog, monkeypatch
):
    """FK violation → immediate re-raise (no phantom-conflict retry).

    A FK violation is a deterministic storage-shape defect — retrying
    with the same INSERT params yields the same error. The pre-fix
    code wasted a second INSERT and logged a misleading phantom-
    conflict warning. Pinned: immediate re-raise + truthful log.
    """
    parent, child, msg = _triple()
    real_insert = repo._insert_deferred_marker
    insert_calls = {"n": 0}

    def fk_failing_insert(**_kwargs):
        insert_calls["n"] += 1
        # Realistic PG-format message.
        raise IntegrityError(
            "INSERT INTO report_injections ...",
            {},
            Exception(
                'insert or update on table "report_injections" '
                'violates foreign key constraint '
                '"fk_report_injections_child_instance_id_instances"'
            ),
        )

    monkeypatch.setattr(repo, "_insert_deferred_marker", fk_failing_insert)

    with caplog.at_level("ERROR", logger=(
        "daemon.repositories.report_injection.repository"
    )):
        with pytest.raises(IntegrityError):
            repo.ensure_deferred(
                parent_instance_id=parent,
                child_instance_id=child,
                child_message_id=msg,
                deferred_reason=DEFERRED_REASON_RESUME_ROUTER,
            )

    assert insert_calls["n"] == 1
    assert _all_rows(engine, parent=parent, child=child, msg=msg) == []
    assert any(
        "deterministic IntegrityError" in r.message
        for r in caplog.records
    ), "the deterministic-error log must fire"
    assert not any(
        "phantom conflict" in r.message
        or "insert-on-missing" in r.message
        for r in caplog.records
    ), "FK violation must NOT trigger phantom-conflict retry"


def test_check_violation_raises_immediately_no_retry(
    repo, engine, caplog, monkeypatch
):
    """CHECK violation → immediate re-raise.

    A CHECK constraint violation is a deterministic value-shape
    defect (the row's data violates a domain rule). The pre-fix
    code routed it through the phantom-conflict retry. Pinned:
    immediate re-raise + truthful log.
    """
    parent, child, msg = _triple()
    insert_calls = {"n": 0}

    def check_failing_insert(**_kwargs):
        insert_calls["n"] += 1
        # Realistic PG-format message.
        raise IntegrityError(
            "INSERT INTO report_injections ...",
            {},
            Exception(
                'new row for relation "report_injections" '
                'violates check constraint '
                '"ck_report_injections_state_valid"'
            ),
        )

    monkeypatch.setattr(repo, "_insert_deferred_marker", check_failing_insert)

    with caplog.at_level("ERROR", logger=(
        "daemon.repositories.report_injection.repository"
    )):
        with pytest.raises(IntegrityError):
            repo.ensure_deferred(
                parent_instance_id=parent,
                child_instance_id=child,
                child_message_id=msg,
                deferred_reason=DEFERRED_REASON_RESUME_ROUTER,
            )

    assert insert_calls["n"] == 1
    assert _all_rows(engine, parent=parent, child=child, msg=msg) == []
    assert any(
        "deterministic IntegrityError" in r.message
        for r in caplog.records
    ), "the deterministic-error log must fire"


def test_unique_violation_on_non_triple_constraint_raises_immediately(
    repo, engine, caplog, monkeypatch
):
    """UNIQUE violation on a NON-obligation-triple constraint
    (e.g. ``injection_id`` PK collision) re-raises immediately.

    The PK on ``injection_id`` is a UNIQUE constraint. A UUID4
    collision is effectively impossible, but the contract still
    applies: a UNIQUE violation that does NOT match the
    obligation-triple index is a deterministic caller defect
    (PK collision) — retrying with a fresh INSERT would not help.

    Pinned: immediate re-raise + truthful log; NOT routed through
    the obligation-triple convergence path.
    """
    parent, child, msg = _triple()
    insert_calls = {"n": 0}

    def pk_failing_insert(**_kwargs):
        insert_calls["n"] += 1
        # Realistic PG-format PK collision message — distinct from
        # the obligation-triple unique-violation (the constraint
        # NAME is on the PK, not on
        # ``uq_report_injections_oblig_triple``).
        raise IntegrityError(
            "INSERT INTO report_injections ...",
            {},
            Exception(
                'duplicate key value violates unique constraint '
                '"report_injections_pkey"'
            ),
        )

    monkeypatch.setattr(repo, "_insert_deferred_marker", pk_failing_insert)

    with caplog.at_level("ERROR", logger=(
        "daemon.repositories.report_injection.repository"
    )):
        with pytest.raises(IntegrityError):
            repo.ensure_deferred(
                parent_instance_id=parent,
                child_instance_id=child,
                child_message_id=msg,
                deferred_reason=DEFERRED_REASON_RESUME_ROUTER,
            )

    assert insert_calls["n"] == 1
    assert _all_rows(engine, parent=parent, child=child, msg=msg) == []
    assert any(
        "deterministic IntegrityError" in r.message
        for r in caplog.records
    ), "the deterministic-error log must fire"
    assert not any(
        "phantom conflict" in r.message
        or "insert-on-missing" in r.message
        for r in caplog.records
    ), "PK collision must NOT trigger phantom-conflict retry"


# ─── Obligation-triple unique violation → existing convergence path ──────


def test_obligation_triple_unique_violation_triggers_insert_on_missing(
    repo, engine, caplog, monkeypatch
):
    """UNIQUE violation on the obligation-triple index → existing
    convergence path.

    This is the LEGITIMATE case for the phantom-conflict re-read +
    insert-on-missing retry (Debug Phase 4, e9aac370). A concurrent
    INSERT raced our row and the partial unique index
    ``uq_report_injections_oblig_triple`` rejected us; the
    insert-on-missing retry inserts a fresh DEFERRED marker when
    the post-rollback re-read finds zero rows.

    Pinned: existing convergence path still works (no regression).
    The realistic message carries the obligation-triple column set
    (SQLite format) OR the index name (PG format) so the
    discriminator accepts it.
    """
    parent, child, msg = _triple()
    real_insert = repo._insert_deferred_marker
    insert_calls = {"n": 0}

    def triple_unique_failing_insert(**kwargs):
        insert_calls["n"] += 1
        if insert_calls["n"] == 1:
            # PG-format message with the obligation-triple INDEX NAME
            # (driver renders it in ``exc.orig``).
            raise IntegrityError(
                "INSERT INTO report_injections ...",
                {},
                Exception(
                    'duplicate key value violates unique constraint '
                    '"uq_report_injections_oblig_triple"'
                ),
            )
        return real_insert(**kwargs)

    monkeypatch.setattr(repo, "_insert_deferred_marker", triple_unique_failing_insert)

    with caplog.at_level("WARNING", logger=(
        "daemon.repositories.report_injection.repository"
    )):
        row = repo.ensure_deferred(
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id=msg,
            deferred_reason=DEFERRED_REASON_PENDING_MESSAGES,
        )

    # Phantom-conflict retry happened (1 + 1 = 2 calls).
    assert insert_calls["n"] == 2
    assert row is not None
    rows = _all_rows(engine, parent=parent, child=child, msg=msg)
    assert len(rows) == 1
    assert rows[0].state == ReportInjectionState.DEFERRED.value
    # The phantom-conflict log fired (legitimate).
    assert any(
        "insert-on-missing" in r.message or "phantom" in r.message
        for r in caplog.records
    ), "the obligation-triple path's phantom-conflict log must fire"


def test_obligation_triple_unique_violation_persistent_zero_rows_raises(
    repo, engine, caplog, monkeypatch
):
    """Obligation-triple unique violation + still-zero rows after
    the insert-on-missing retry → re-raise (e9aac370 contract
    preserved).

    A persistent conflict with zero rows means the constraint
    violated is NOT the obligation-triple (otherwise the row would
    be visible) OR a storage-level fault. The method re-raises —
    silently claiming "already delivered" was the b7ead8a4 bug
    class. The classification unit tests for NOT NULL / FK / CHECK /
    non-triple UNIQUE cover the deterministic case; this test
    pins that the obligation-triple path still surfaces a persistent
    failure loudly when retry + re-read both fail.
    """
    parent, child, msg = _triple()
    insert_calls = {"n": 0}

    def always_triple_unique(**_kwargs):
        insert_calls["n"] += 1
        # Even after insert-on-missing, the violation persists.
        raise IntegrityError(
            "INSERT INTO report_injections ...",
            {},
            Exception(
                'duplicate key value violates unique constraint '
                '"uq_report_injections_oblig_triple"'
            ),
        )

    monkeypatch.setattr(repo, "_insert_deferred_marker", always_triple_unique)

    with caplog.at_level("ERROR", logger=(
        "daemon.repositories.report_injection.repository"
    )):
        with pytest.raises(IntegrityError):
            repo.ensure_deferred(
                parent_instance_id=parent,
                child_instance_id=child,
                child_message_id=msg,
                deferred_reason=DEFERRED_REASON_PENDING_MESSAGES,
            )

    # insert-on-missing fired exactly once (2 total calls).
    assert insert_calls["n"] == 2
    assert _all_rows(engine, parent=parent, child=child, msg=msg) == []
    # Persistent-failure log fired (the loud-failure path).
    assert any(
        "persistent conflict" in r.message
        and "re-raising" in r.message
        for r in caplog.records
    ), "the persistent-failure loud log must fire"


# ─── Discriminator direct test ──────────────────────────────────────────────


def test_discriminator_accepts_pg_format_constraint_name():
    """PG renders the constraint name in ``exc.orig`` — the
    discriminator accepts this shape."""
    from daemon.repositories.report_injection.repository import (
        _is_obligation_triple_unique_violation,
    )

    exc = IntegrityError(
        "INSERT ...",
        {},
        Exception(
            'duplicate key value violates unique constraint '
            '"uq_report_injections_oblig_triple"'
        ),
    )
    assert _is_obligation_triple_unique_violation(exc) is True


def test_discriminator_accepts_sqlite_format_column_set():
    """SQLite renders the index columns — the discriminator accepts
    this shape (the obligation-triple columns appear together)."""
    from daemon.repositories.report_injection.repository import (
        _is_obligation_triple_unique_violation,
    )

    exc = IntegrityError(
        "INSERT ...",
        {},
        Exception(
            "UNIQUE constraint failed: "
            "report_injections.parent_instance_id, "
            "report_injections.child_instance_id, "
            "report_injections.child_message_id"
        ),
    )
    assert _is_obligation_triple_unique_violation(exc) is True


def test_discriminator_rejects_not_null_violation():
    """NOT NULL → NOT obligation-triple (deterministic re-raise)."""
    from daemon.repositories.report_injection.repository import (
        _is_obligation_triple_unique_violation,
    )

    exc = IntegrityError(
        "INSERT ...",
        {},
        Exception(
            'null value in column "content" of relation '
            '"report_injections" violates not-null constraint'
        ),
    )
    assert _is_obligation_triple_unique_violation(exc) is False


def test_discriminator_rejects_pk_collision():
    """UNIQUE violation on the PK → NOT obligation-triple (the
    discriminator keys on the obligation-triple index name OR its
    column set; the PK is on ``injection_id``, distinct columns)."""
    from daemon.repositories.report_injection.repository import (
        _is_obligation_triple_unique_violation,
    )

    exc = IntegrityError(
        "INSERT ...",
        {},
        Exception(
            'duplicate key value violates unique constraint '
            '"report_injections_pkey"'
        ),
    )
    assert _is_obligation_triple_unique_violation(exc) is False


def test_discriminator_rejects_partial_column_overlap():
    """A UNIQUE on a strict SUBSET of the obligation-triple columns
    (hypothetical; doesn't exist today) would not match all three
    columns → NOT classified as obligation-triple. This is the
    soundness of the column-set discriminator — it can't
    mis-classify a partial-overlap UNIQUE."""
    from daemon.repositories.report_injection.repository import (
        _is_obligation_triple_unique_violation,
    )

    exc = IntegrityError(
        "INSERT ...",
        {},
        Exception(
            "UNIQUE constraint failed: "
            "report_injections.parent_instance_id, "
            "report_injections.child_instance_id"
            # child_message_id is missing → partial overlap
        ),
    )
    assert _is_obligation_triple_unique_violation(exc) is False
