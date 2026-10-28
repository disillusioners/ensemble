"""F3 pins — the repair-tool dry-run transaction hygiene (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``.agents/shared/planning/db-pool-idle-txn/final-implementation-spec.md``
§1 F3.1–F3.5 and §5.2.

What these pins assert (the mechanism item of the lane's phase-1 scope):

* **F3-a** — the module carries **no** ``text("BEGIN")`` any more, and a
  dry-run whose statement FAILS leaves the connection **not** in a
  transaction (a further statement on it does not raise *"current
  transaction is aborted"*).
* **F3-b** — the dry-run's failure path performs a **rollback** (the
  session's transaction is ``None`` AND a rollback spy fired).
* **F3-c** — the DDL-branch **rollback itself failing** also leaves the
  connection clean (the ``finally:`` covers the rollback-error path —
  the plantable site is the ``session.rollback()`` inside the DDL
  branch's ``finally:``).
* **F3-d** — the dry-run's ``previewed`` audit row is written in a
  **separate** session and is **not** rolled back by the dry-run
  transaction.
* **F3-e** — the agent-visible preview strings + docstrings reflect the
  new dry-run wording (and the audit-contract wording at the module
  docstring is unchanged).

Every pin has a concrete plantable mutation (see the docstrings).

Run (house form — the certified interpreter, the six-variable per-variable
scrub, a ``mktemp -d`` HOME, and the worktree as CWD)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/unit/tools/test_dbpool_txn_hygiene.py --tb=short -q
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from daemon.repositories.ens_db.models import RepairLog
from daemon.tools.ens_db_tools import (
    _RepairNonceStore,
    _dry_run_sqlite_safe,
)

#: The pack-local declared collected count (NEW packs only — the
#: independent baseline lives in ``expected-collected.md``; this constant
#: is the pack's own declaration for the gate fence).
EXPECTED_COLLECTED = 9

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MODULE_PATH = _REPO_ROOT / "daemon" / "tools" / "ens_db_tools.py"


# ── fixtures / helpers ───────────────────────────────────────────────────


@pytest.fixture()
def repair_engine():
    """A file-free SQLite engine carrying the ``repair_log`` table."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine, tables=[RepairLog.__table__]
    )
    try:
        yield engine
    finally:
        engine.dispose()


def _dry_run(
    engine,
    *,
    sql: str,
    sql_class: str,
    session: Any | None = None,
    target_label: str = "dbpool_f3_target",
):
    """Drive ``_dry_run_sqlite_safe`` with a real session + nonce store."""
    nonce_store = _RepairNonceStore()
    own = session is None
    sess = session if session is not None else Session(engine)
    try:
        preview = _dry_run_sqlite_safe(
            sess,
            sql,
            sql_class,
            target_label,
            nonce_store,
            "inst-f3",
            "hash-f3",
            engine,
            None,  # manager — unused by the dry-run path
            "agent-f3",
        )
    finally:
        if own:
            sess.close()
    return preview


class _RollbackCountingSession(Session):
    """A Session whose ``rollback()`` is observable (F3-b spy)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rollback_calls = 0

    def rollback(self) -> None:
        self.rollback_calls += 1
        super().rollback()


class _RollbackFailingSession(Session):
    """A Session whose ``rollback()`` raises (F3-c — the rollback-error leg)."""

    def rollback(self) -> None:
        raise RuntimeError("synthetic rollback failure")


# ── F3-a ──────────────────────────────────────────────────────────────────


class TestF3aNoExplicitBegin:
    """F3-a — no explicit BEGIN remains; a failed dry-run leaves the
    connection clean.

    Plantable mutation: re-introduce ``session.execute(text("BEGIN"))``
    at the DDL or DML site in ``daemon/tools/ens_db_tools.py`` ⇒ the
    static leg REDS (and, with the pre-F3 shape, the behavioural leg
    REDs too — the outer ``except`` ran with no rollback).
    """

    def test_module_contains_no_explicit_begin(self):
        source = _MODULE_PATH.read_text()
        assert 'text("BEGIN")' not in source, (
            "F3-a: the dry-run path must not issue an explicit BEGIN "
            "(D-01) — a literal BEGIN leaves the standalone-BEGIN "
            "fingerprint in pg_stat_activity.last_query"
        )
        # The BEGIN-shaped SQL string must be gone entirely (any quote
        # style), not just the double-quoted spelling.
        assert not re.search(r"text\(\s*['\"]BEGIN['\"]", source), (
            "F3-a: a BEGIN literal survives in the module (any quoting)"
        )

    def test_failed_dry_run_leaves_connection_not_in_transaction(
        self, repair_engine
    ):
        with Session(repair_engine) as session:
            preview = _dry_run(
                repair_engine,
                sql="SELECT * FROM no_such_table_at_all",
                sql_class="DDL",
                session=session,
            )
            assert "DRY-RUN DDL FAILED" in preview
            # The session must NOT be inside a transaction any more.
            assert not session.in_transaction(), (
                "F3-a: the dry-run left the session in a transaction"
            )
            # A further statement must NOT raise 'current transaction is
            # aborted' (the aborted-transaction class). The statement
            # itself autobegins a fresh transaction — that is expected;
            # the assertion is that it does NOT raise.
            session.execute(text("SELECT 1"))
            session.rollback()
            assert not session.in_transaction()


# ── F3-b ──────────────────────────────────────────────────────────────────


class TestF3bFailurePathRollsBack:
    """F3-b — the failure path performs a rollback.

    Plantable mutation: delete the rollback from the failure path (the
    old outer-``except``-without-rollback shape) ⇒ RED.
    """

    def test_sql_error_path_rolls_back(self, repair_engine):
        session = _RollbackCountingSession(repair_engine)
        try:
            preview = _dry_run(
                repair_engine,
                sql="SELECT * FROM no_such_table_at_all",
                sql_class="DML",
                session=session,
            )
            assert "DRY-RUN DML FAILED" in preview
            assert session.rollback_calls >= 1, (
                "F3-b: the DML failure path performed no rollback"
            )
            assert not session.in_transaction()
        finally:
            session.close()

    def test_sql_success_path_also_rolls_back(self, repair_engine):
        session = _RollbackCountingSession(repair_engine)
        try:
            preview = _dry_run(
                repair_engine,
                sql="SELECT 1",
                sql_class="DML",
                session=session,
            )
            assert "DRY-RUN DML EXECUTED" in preview
            assert session.rollback_calls >= 1, (
                "F3-b: the DML success path must roll back too (the "
                "dry-run never commits)"
            )
            assert not session.in_transaction()
        finally:
            session.close()


# ── F3-c ──────────────────────────────────────────────────────────────────


class TestF3cRollbackFailureStillLeavesCleanConnection:
    """F3-c — a rollback that ITSELF fails still leaves the connection clean.

    Plantable mutation: remove the ``session.rollback()`` inside the DDL
    branch's ``finally:`` (the plantable site is the rollback call in
    the DDL branch) ⇒ RED.
    """

    def test_ddl_rollback_failure_is_contained(self, repair_engine):
        session = _RollbackFailingSession(repair_engine)
        try:
            preview = _dry_run(
                repair_engine,
                sql="CREATE TABLE f3_probe (id INTEGER)",
                sql_class="DDL",
                session=session,
            )
            # The rollback failure must not escape the dry-run: it is
            # reported as the wrapper error and the preview still
            # completes (the confirm nonce is minted).
            assert "DRY-RUN WRAPPER ERROR" in preview, (
                "F3-c: the rollback failure was not reported by the "
                "outer wrapper — the error escaped the dry-run"
            )
            assert "CONFIRM NONCE:" in preview
        finally:
            session.close()

        # The ENGINE must still be usable: a fresh session on the same
        # engine can execute a statement (the pool was not poisoned).
        with Session(repair_engine) as fresh:
            assert not fresh.in_transaction()
            fresh.execute(text("SELECT 1"))
            fresh.rollback()
            assert not fresh.in_transaction()


# ── F3-d ──────────────────────────────────────────────────────────────────


class TestF3dAuditRowIsSeparate:
    """F3-d — the ``previewed`` audit row survives the dry-run rollback.

    Plantable mutation: move the audit-row write INSIDE the rolled-back
    transaction ⇒ the audit row vanishes ⇒ RED.
    """

    def test_previewed_audit_row_survives_rollback(self, repair_engine):
        preview = _dry_run(
            repair_engine,
            sql="SELECT 1",
            sql_class="DML",
        )
        assert "DRY-RUN DML EXECUTED" in preview
        with Session(repair_engine) as session:
            rows = session.exec(select(RepairLog)).all()
        assert len(rows) == 1, (
            "F3-d: the previewed audit row was NOT written (or was "
            "rolled back with the dry-run transaction)"
        )
        assert rows[0].outcome == "previewed"


# ── F3-e ──────────────────────────────────────────────────────────────────


class TestF3eAgentVisibleWording:
    """F3-e — the agent-visible wording reflects the new dry-run shape.

    Plantable mutation: change the preview string at the DDL site back
    to a ``BEGIN/ROLLBACK`` wrapper claim ⇒ RED.
    """

    def test_preview_strings_say_rollback_only(self, repair_engine):
        ddl_preview = _dry_run(
            repair_engine,
            sql="CREATE TABLE f3_wording (id INTEGER)",
            sql_class="DDL",
        )
        assert "DRY-RUN DDL EXECUTED INSIDE ROLLBACK-ONLY TRANSACTION" in ddl_preview
        dml_preview = _dry_run(
            repair_engine,
            sql="SELECT 1",
            sql_class="DML",
        )
        assert "DRY-RUN DML EXECUTED INSIDE ROLLBACK-ONLY TRANSACTION" in dml_preview

    def test_no_agent_visible_begin_rollback_wrapper_claim(self):
        source = _MODULE_PATH.read_text()
        # The agent-visible dry-run surfaces must not claim a
        # ``BEGIN; ...; ROLLBACK`` wrapper any more.
        for banned in (
            "DRY-RUN DDL EXECUTED INSIDE BEGIN/ROLLBACK",
            "DRY-RUN DML EXECUTED INSIDE BEGIN/ROLLBACK",
        ):
            assert banned not in source, (
                f"F3-e: the agent-visible preview string still claims the "
                f"old wrapper: {banned!r}"
            )

    def test_audit_contract_wording_unchanged(self):
        """The module docstring's audit-contract wording (:37/:942)
        describes the COMMITTED path — it must stay UNCHANGED (F3.4)."""
        source = _MODULE_PATH.read_text()
        assert source.count("``BEGIN/COMMIT``") >= 2, (
            "F3-e: the audit-contract wording (BEGIN/COMMIT) was "
            "altered — it describes the committed path and must not "
            "change (contract F3.4)"
        )
