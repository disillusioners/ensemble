"""Refuse non-idempotent repair inputs (W1-P2, task 2.10d — normalized).

Original spec typo was ``test_ens_db_repair_idempotent_doller.py``;
the approver-normalized name is ``test_ens_db_repair_refuses_non_idempotent.py``.

Required assertions:

* Non-idempotent DML (``INSERT INTO ... VALUES (random())`` etc.)
  is REFUSED at tool input validation — the tool returns a
  structured refusal naming the rule violated.
* Any input matching the ``*.sql`` filename pattern is REFUSED
  (migration-runner trap per ``daemon/migrations/runner.py:486-491``).
* The tool returns a structured refusal (error message naming the
  rule violated). No row is created, no audit row is written, no
  connection is opened to the DB.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from daemon.tools.ens_db_tools import create_ens_db_tools


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    db_path = tmp_path / "ens_db_refuse.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng, "connect")
    def _set_pragma(dbapi_conn, connection_record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    return eng


def _manager_with(engine: Engine) -> MagicMock:
    mgr = MagicMock()
    mgr.engine = engine
    return mgr


class TestRefusesNonIdempotentDML:
    """Non-idempotent DML is refused at input validation."""

    @pytest.mark.parametrize(
        "non_idempotent_dml",
        [
            "INSERT INTO foo (id, n) VALUES (gen_random_uuid(), 1)",
            "INSERT INTO foo (id, n) VALUES (uuid_generate_v4(), 1)",
            "INSERT INTO foo (id, n) VALUES (random(), 1)",
            "INSERT INTO foo (id, n) VALUES (nextval('foo_seq'), 1)",
            "INSERT INTO foo (id, n) VALUES (1, now())",
            "UPDATE foo SET updated_at = clock_timestamp() WHERE id = 1",
        ],
    )
    @pytest.mark.asyncio
    async def test_non_idempotent_dml_refused(
        self, engine: Engine, non_idempotent_dml: str
    ) -> None:
        mgr = _manager_with(engine)
        tools = create_ens_db_tools(mgr, "inst-refuse", agent_id="maintenancer")
        repair_tool = next(t for t in tools if t.name == "ens_db_repair_execute")

        out = await repair_tool.ainvoke({
            "sql": non_idempotent_dml,
            "target_label": "foo:non-idem",
            "dry_run": True,
        })

        assert "ERROR" in out
        assert "non-idempotent-DML" in out, (
            f"Expected non-idempotent-DML refusal; got: {out[:200]}"
        )
        # The rule name must be named verbatim.
        assert "R14 idempotent DO$$-only" in out


class TestRefusesSqlFilenameInput:
    """``*.sql`` filename input is refused (migration-runner trap).

    The trap: the migrations runner is SQLite-only by design
    (``daemon/migrations/runner.py:486-491``). If the agent submits
    ``--file repair.sql``, the runner would silently no-op on PG
    and fail on SQLite. The tool refuses such input outright.
    """

    @pytest.mark.parametrize(
        "filename",
        [
            "repair.sql",
            "fix_data.sql",
            "/tmp/migration.sql",
            "./apply.sql",
            "subdir/run.sql",
        ],
    )
    @pytest.mark.asyncio
    async def test_sql_filename_refused(
        self, engine: Engine, filename: str
    ) -> None:
        mgr = _manager_with(engine)
        tools = create_ens_db_tools(mgr, "inst-sqlref", agent_id="maintenancer")
        repair_tool = next(t for t in tools if t.name == "ens_db_repair_execute")

        # Inject the filename as if the LLM passed it.
        out = await repair_tool.ainvoke({
            "sql": f"SELECT * FROM {filename}",
            "target_label": "sql-file",
            "dry_run": True,
        })

        assert "ERROR" in out
        assert "migration-runner-trap" in out, (
            f"Expected migration-runner-trap refusal; got: {out[:200]}"
        )


class TestRefusesEmptySql:
    """Empty SQL is refused with a clear message."""

    @pytest.mark.asyncio
    async def test_empty_sql_refused(self, engine: Engine) -> None:
        mgr = _manager_with(engine)
        tools = create_ens_db_tools(mgr, "inst-empty", agent_id="maintenancer")
        repair_tool = next(t for t in tools if t.name == "ens_db_repair_execute")

        for empty in ("", "   ", "\n\n  \n"):
            out = await repair_tool.ainvoke({
                "sql": empty,
                "target_label": "empty",
                "dry_run": True,
            })
            assert "ERROR" in out
            assert "empty" in out.lower(), f"Expected 'empty' in refusal; got: {out!r}"


class TestSelfSurgeryRefused:
    """Self-surgery refusal (architect §7.2) — the calling agent's
    own instance id appearing in SQL, or audit-sensitive table
    targets, are refused.
    """

    @pytest.mark.asyncio
    async def test_caller_instance_id_in_sql_refused(
        self, engine: Engine
    ) -> None:
        mgr = _manager_with(engine)
        caller = "caller-inst-xyz"
        tools = create_ens_db_tools(mgr, caller, agent_id="maintenancer")
        repair_tool = next(t for t in tools if t.name == "ens_db_repair_execute")

        out = await repair_tool.ainvoke({
            "sql": f"UPDATE foo SET x = 1 WHERE id = '{caller}'",
            "target_label": "self-surgery",
            "dry_run": True,
        })

        assert "ERROR" in out
        assert "self-surgery-refused" in out

    @pytest.mark.parametrize(
        "audit_table",
        ["instances", "messages", "message_queue", "tasks", "events", "schema_migrations"],
    )
    @pytest.mark.asyncio
    async def test_audit_sensitive_table_target_refused(
        self, engine: Engine, audit_table: str
    ) -> None:
        mgr = _manager_with(engine)
        tools = create_ens_db_tools(mgr, "inst-audit", agent_id="maintenancer")
        repair_tool = next(t for t in tools if t.name == "ens_db_repair_execute")

        # Idempotent UPDATE against an audit-sensitive table — refused.
        # The DML itself is idempotent (constant value), but the
        # TARGET TABLE is audit-sensitive (architect §7.2 self-surgery
        # refusal standing guard).
        sql = (
            f"UPDATE {audit_table} SET name = 'noop' "
            f"WHERE id = '00000000-0000-0000-0000-000000000000'"
        )
        out = await repair_tool.ainvoke({
            "sql": sql,
            "target_label": f"{audit_table}:noop-update",
            "dry_run": True,
        })

        assert "ERROR" in out
        assert "self-surgery-refused" in out
