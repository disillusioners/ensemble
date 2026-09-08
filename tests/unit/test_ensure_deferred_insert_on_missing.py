"""Insert-on-missing semantics for ``ensure_deferred`` (Debug Phase 4).

Regression coverage for the b7ead8a4/d90b18f9 production incident
(2026-09-07): the pre-fix ``ensure_deferred`` conflated "the
post-rollback SELECT found no non-terminal row" with "a racing
delivery won" — a FALSE-POSITIVE no-op that (a) stranded the parent's
completion gate forever (``pending_children=1`` with zero
``report_injections`` rows) and (b) made the recovery sweep self-heal
impossible (the same-triple false-positive repeated every ~300s
sweep).

Corrected contract pinned here:

* **Insert-on-missing** — an ``IntegrityError`` whose post-rollback
  re-read finds ZERO rows for the obligation triple must INSERT a
  fresh DEFERRED marker (the phantom-conflict shape: the row was
  deleted by the drift sweep's dead-parent Pattern (e) DELETE, or
  escalated+removed between INSERT and re-read).
* **Positive evidence only** — "already delivered" is concluded ONLY
  from a TERMINAL row (INJECTED / TASK_DELIVERED / FAILED) being
  present; a non-terminal row is a benign duplicate (W6); absence of
  rows is NEVER delivery.
* **Convergence** — concurrent double-call converges to exactly one
  non-terminal row (the partial unique index
  ``uq_report_injections_oblig_triple`` gates; the loser's SELECT now
  FINDS the winner's row).
* **Persistent failure is loud** — a repeated IntegrityError with
  still-zero rows re-raises (a real DB failure must not be silently
  interpreted as delivery).

Engine: file-backed SQLite at ``tmp_path`` with NullPool + WAL +
busy_timeout=10000 (repo conventions; never StaticPool+WriteGuard —
QUARANTINE).
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
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
    DEFERRED_REASON_IDEMPOTENCY_SKIP,
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


# ─── Fixtures + helpers ─────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout)."""
    db_path = tmp_path / "ensure-deferred-test.sqlite"
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
    content: str | None = None,
    deferred_reason: str | None = None,
    delivered_at: str | None = None,
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
                delivered_at=delivered_at,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        session.commit()
    return injection_id


def _all_rows(
    engine: Engine, *, parent: str, child: str, msg: str
) -> list[ReportInjection]:
    """Every row for the triple, any state."""
    with Session(engine) as session:
        return list(
            session.exec(
                sm_select(ReportInjection)
                .where(ReportInjection.parent_instance_id == parent)
                .where(ReportInjection.child_instance_id == child)
                .where(ReportInjection.child_message_id == msg)
            ).all()
        )


# ─── Insert-on-missing: empty state ─────────────────────────────────────────


def test_empty_state_inserts_deferred_row(repo, engine):
    parent, child, msg = _triple()
    row = repo.ensure_deferred(
        parent_instance_id=parent,
        child_instance_id=child,
        child_message_id=msg,
        deferred_reason=DEFERRED_REASON_PENDING_MESSAGES,
    )
    assert row is not None, "empty state must INSERT, not no-op"
    assert row.state == ReportInjectionState.DEFERRED.value
    assert row.deferred_reason == DEFERRED_REASON_PENDING_MESSAGES
    assert row.report_message_id is None  # marker shape (no artifact)
    rows = _all_rows(engine, parent=parent, child=child, msg=msg)
    assert len(rows) == 1
    assert rows[0].state == ReportInjectionState.DEFERRED.value


# ─── Legitimate no-ops: existing non-terminal rows (W6) ─────────────────────


def test_existing_deferred_same_reason_is_legitimate_noop(repo, engine):
    parent, child, msg = _triple()
    _seed_row(
        engine,
        parent=parent,
        child=child,
        msg=msg,
        state=ReportInjectionState.DEFERRED.value,
        deferred_reason=DEFERRED_REASON_PENDING_MESSAGES,
    )
    result = repo.ensure_deferred(
        parent_instance_id=parent,
        child_instance_id=child,
        child_message_id=msg,
        deferred_reason=DEFERRED_REASON_PENDING_MESSAGES,
    )
    assert result is None, "duplicate DEFERRED (same reason) is a no-op"
    rows = _all_rows(engine, parent=parent, child=child, msg=msg)
    assert len(rows) == 1, "must never duplicate"


def test_existing_pending_row_is_legitimate_noop(repo, engine):
    parent, child, msg = _triple()
    _seed_row(
        engine,
        parent=parent,
        child=child,
        msg=msg,
        state=ReportInjectionState.PENDING.value,
        report_message_id="rm-1",
        content="queued report",
        deferred_reason=DEFERRED_REASON_RESUME_ROUTER,
    )
    result = repo.ensure_deferred(
        parent_instance_id=parent,
        child_instance_id=child,
        child_message_id=msg,
        deferred_reason=DEFERRED_REASON_RESUME_ROUTER,
    )
    # Existing PENDING row with the SAME deferred_reason → benign
    # duplicate, no-op. Exactly-once delivery semantics unchanged.
    assert result is None
    rows = _all_rows(engine, parent=parent, child=child, msg=msg)
    assert len(rows) == 1
    assert rows[0].state == ReportInjectionState.PENDING.value


def test_existing_pending_row_reason_differs_stamps_in_place(repo, engine):
    """PENDING row (artifact present) + different reason → in-place
    reason stamp, never a second row, never an escalation."""
    parent, child, msg = _triple()
    _seed_row(
        engine,
        parent=parent,
        child=child,
        msg=msg,
        state=ReportInjectionState.PENDING.value,
        report_message_id="rm-1",
        content="queued report",
    )
    result = repo.ensure_deferred(
        parent_instance_id=parent,
        child_instance_id=child,
        child_message_id=msg,
        deferred_reason=DEFERRED_REASON_RESUME_ROUTER,
    )
    assert result is not None, "reason stamp returns the row"
    rows = _all_rows(engine, parent=parent, child=child, msg=msg)
    assert len(rows) == 1, "never duplicate on a reason stamp"
    assert rows[0].state == ReportInjectionState.PENDING.value, (
        "reason stamp must NOT change the row's state"
    )
    assert rows[0].deferred_reason == DEFERRED_REASON_RESUME_ROUTER


def test_reason_differs_updates_in_place(repo, engine):
    parent, child, msg = _triple()
    _seed_row(
        engine,
        parent=parent,
        child=child,
        msg=msg,
        state=ReportInjectionState.DEFERRED.value,
        deferred_reason=DEFERRED_REASON_IDEMPOTENCY_SKIP,
    )
    result = repo.ensure_deferred(
        parent_instance_id=parent,
        child_instance_id=child,
        child_message_id=msg,
        deferred_reason=DEFERRED_REASON_PENDING_MESSAGES,
    )
    assert result is not None, "reason update returns the row"
    rows = _all_rows(engine, parent=parent, child=child, msg=msg)
    assert len(rows) == 1, "update in place — no second row"
    assert rows[0].deferred_reason == DEFERRED_REASON_PENDING_MESSAGES


# ─── Positive-evidence no-op: terminal row present ──────────────────────────


@pytest.mark.parametrize(
    "terminal_state",
    [
        ReportInjectionState.INJECTED.value,
        ReportInjectionState.TASK_DELIVERED.value,
        ReportInjectionState.FAILED.value,
    ],
)
def test_terminal_row_is_positive_delivery_evidence(
    repo, engine, caplog, terminal_state
):
    parent, child, msg = _triple()
    _seed_row(
        engine,
        parent=parent,
        child=child,
        msg=msg,
        state=terminal_state,
        report_message_id="rm-1",
        content="delivered",
        delivered_at=datetime.now(timezone.utc).isoformat(),
    )
    with caplog.at_level("INFO", logger=(
        "daemon.repositories.report_injection.repository"
    )):
        result = repo.ensure_deferred(
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id=msg,
            deferred_reason=DEFERRED_REASON_PENDING_MESSAGES,
        )
    assert result is None, "terminal row = legitimate no-op"
    rows = _all_rows(engine, parent=parent, child=child, msg=msg)
    assert len(rows) == 1, "no fresh marker on top of a terminal row"
    # The no-op log must cite the terminal row and use state-appropriate
    # evidence wording. INJECTED / TASK_DELIVERED = positive delivery
    # evidence; FAILED = dead-letter abandonment (NOT delivery, per
    # models.py:113-118 sentinel semantics). Never the retired zero-row
    # "racing delivery won" claim.
    noop_records = [
        r for r in caplog.records if "ensure_deferred no-op" in r.message
    ]
    assert noop_records, "terminal no-op must be logged"
    evidence_word = (
        "dead-letter" if terminal_state == ReportInjectionState.FAILED.value
        else "positive"
    )
    assert any(
        f"state={terminal_state}" in r.message and evidence_word in r.message
        for r in noop_records
    )


# ─── Phantom IntegrityError → insert-on-missing ─────────────────────────────


def test_phantom_integrityerror_triggers_insert_on_missing(
    repo, engine, caplog, monkeypatch
):
    """First INSERT raises IntegrityError with ZERO rows present.

    The pre-fix code logged "already delivered (racing delivery won)"
    and returned None — the b7ead8a4 false-positive. The fix must
    INSERT the marker.

    The mocked ``IntegrityError`` carries the obligation-triple
    columns so the subtype classifier
    (``_is_obligation_triple_unique_violation``) accepts it as the
    legitimate unique-violation case — production emits the same
    SQLite-format column-set message. Any OTHER IntegrityError
    (NOT NULL / FK / non-triple UNIQUE) takes the deterministic
    re-raise path (covered by the classification unit tests in
    ``tests/unit/test_ensure_deferred_integrity_error_classification.py``).
    """
    parent, child, msg = _triple()
    real_insert = repo._insert_deferred_marker
    calls = {"n": 0}

    def phantom_first_insert(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # Simulate the phantom conflict (delete / escalation race)
            # WITHOUT any row existing. Message carries the obligation-
            # triple column set so the SQLite-format discriminator
            # accepts it — same shape production emits on PG/SQLite
            # ``uq_report_injections_oblig_triple`` partial unique
            # violation.
            raise IntegrityError(
                "INSERT INTO report_injections ...",
                {},
                Exception(
                    "UNIQUE constraint failed: "
                    "report_injections.parent_instance_id, "
                    "report_injections.child_instance_id, "
                    "report_injections.child_message_id (phantom)"
                ),
            )
        return real_insert(**kwargs)

    monkeypatch.setattr(repo, "_insert_deferred_marker", phantom_first_insert)

    with caplog.at_level("WARNING", logger=(
        "daemon.repositories.report_injection.repository"
    )):
        row = repo.ensure_deferred(
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id=msg,
            deferred_reason=DEFERRED_REASON_PENDING_MESSAGES,
        )

    assert calls["n"] == 2, "insert retried exactly once"
    assert row is not None, "insert-on-missing must land the marker"
    rows = _all_rows(engine, parent=parent, child=child, msg=msg)
    assert len(rows) == 1
    assert rows[0].state == ReportInjectionState.DEFERRED.value
    assert any(
        "insert-on-missing" in r.message or "phantom" in r.message
        for r in caplog.records
    ), "the recovery log line must be observable for forensics"


def test_persistent_integrityerror_with_zero_rows_raises(
    repo, engine, caplog, monkeypatch
):
    """Repeated IntegrityError with still-zero rows = real DB failure.

    Must RAISE — silently returning None (claiming delivery) is the
    bug class under fix.
    """
    parent, child, msg = _triple()

    def always_failing_insert(**_kwargs):
        raise IntegrityError(
            "INSERT INTO report_injections ...",
            {},
            Exception("persistent storage fault"),
        )

    monkeypatch.setattr(repo, "_insert_deferred_marker", always_failing_insert)

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

    rows = _all_rows(engine, parent=parent, child=child, msg=msg)
    assert len(rows) == 0
    assert any(
        "re-raising" in r.message for r in caplog.records
    ), "the loud-failure log must be observable"


# ─── Concurrency: exactly one row ────────────────────────────────────────────


def test_concurrent_double_call_converges_to_one_row(repo, engine):
    """Two concurrent ensure_deferred calls → exactly one non-terminal row.

    The winner INSERTs; the loser's IntegrityError path re-reads the
    triple and now FINDS the winner's row (the pre-fix code could
    misread the empty-prefix window as delivery).
    """
    parent, child, msg = _triple()
    barrier = threading.Barrier(2)
    results: list = []
    errors: list = []

    def _call() -> None:
        try:
            barrier.wait(timeout=10)
            results.append(
                repo.ensure_deferred(
                    parent_instance_id=parent,
                    child_instance_id=child,
                    child_message_id=msg,
                    deferred_reason=DEFERRED_REASON_PENDING_MESSAGES,
                )
            )
        except Exception as exc:  # pragma: no cover - diagnostic
            errors.append(exc)

    threads = [threading.Thread(target=_call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"no exception may escape the absorption: {errors}"
    rows = _all_rows(engine, parent=parent, child=child, msg=msg)
    non_terminal = [
        r for r in rows
        if r.state in (
            ReportInjectionState.PENDING.value,
            ReportInjectionState.DEFERRED.value,
        )
    ]
    assert len(non_terminal) == 1, (
        f"partial unique index must converge to ONE non-terminal row, "
        f"got {len(non_terminal)}"
    )
    # Exactly one caller owns the insert (row returned); the other is
    # a legitimate absorbed duplicate (None).
    inserted = [r for r in results if r is not None]
    assert len(inserted) == 1
    assert results.count(None) == 1
