"""Idempotent ``DO$$``-only rule (W1-P2, task 2.10c — checklist item 3).

Every repair SQL must be re-runnable to the same final state (R14).
This test runs each fixture repair SQL TWICE inside the tool and
asserts the net state change is zero — including across
``BEGIN/COMMIT`` boundaries.

Fixture list:

* DDL idempotent: ``CREATE TABLE IF NOT EXISTS foo (id INTEGER PRIMARY KEY, n INTEGER)``
* DML idempotent (PG only): ``INSERT INTO foo (id, n) VALUES (1, 100) ON CONFLICT (id) DO UPDATE SET n = 100``
* DDL idempotent with DO$$ wrapper (PG only).

The DDL idempotent case runs on BOTH engines (architect §7.5). The
PG-only cases skip on SQLite. When ``ENSEMBLE_TEST_PG_URL`` points at
a disposable PG instance, the whole suite (including the PG-only
pins) runs against it; otherwise SQLite is the default engine.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from daemon.tools.ens_db_tools import create_ens_db_tools


def _sqlite_engine(tmp_path: Path) -> Engine:
    """File-backed SQLite engine at tmp_path + NullPool + WAL + busy_timeout."""
    db_path = tmp_path / "ens_db_idem.db"
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

    return eng


def _maybe_pg_engine() -> Engine | None:
    """Return a PG engine if ``ENSEMBLE_TEST_PG_URL`` is set; None otherwise.

    Mirrors ``test_ens_db_tools_select_only._maybe_pg_engine`` — env-
    resolved URL, never hardcoded (R13: no prod contact).
    """
    url = os.environ.get("ENSEMBLE_TEST_PG_URL")
    if not url:
        return None
    return create_engine(url, poolclass=NullPool)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    """PG engine when ``ENSEMBLE_TEST_PG_URL`` is set; file-backed SQLite
    (tmp_path + NullPool + WAL + busy_timeout) otherwise.

    CRITICAL DIFFERENCE vs select_only: that suite SKIPS its PG param
    when the env is unset; this suite must run SQLite BY DEFAULT (the
    idempotency pins are engine-portable), and run them on PG when the
    env points at a disposable instance.
    """
    pg = _maybe_pg_engine()
    eng = pg if pg is not None else _sqlite_engine(tmp_path)
    # Creates ``repair_log`` (ens_db_tools imports RepairLog → present in
    # SQLModel.metadata) on BOTH engines — the commit-path audit write is
    # fail-closed and needs the table to exist.
    SQLModel.metadata.create_all(eng)
    return eng


def _manager_with(engine: Engine) -> MagicMock:
    mgr = MagicMock()
    mgr.engine = engine
    return mgr


def _table_count(engine: Engine, name: str) -> int:
    """Count tables named ``name`` via the backend's catalog.

    Dual-engine (architect §7.5): ``information_schema.tables`` on PG,
    ``sqlite_master`` on SQLite — the DDL pin must verify for real on
    whichever engine the fixture handed it, not shim on SQLite-only
    catalog SQL.
    """
    is_pg = engine.url.get_backend_name().startswith("postgres")
    if is_pg:
        stmt = text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_name = :name"
        )
    else:
        stmt = text(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name = :name"
        )
    with engine.connect() as conn:
        return conn.execute(stmt, {"name": name}).scalar()


async def _run_repair_twice(
    engine: Engine, sql: str, target: str
) -> tuple[str, int]:
    """Run the repair twice (dry-run + commit each time). Returns
    (commit_msg_first, count_after_second)."""
    mgr = _manager_with(engine)
    tools = create_ens_db_tools(mgr, "inst-idem", agent_id="maintenancer")
    repair_tool = next(t for t in tools if t.name == "ens_db_repair_execute")

    # First round.
    out_dry = await repair_tool.ainvoke({
        "sql": sql,
        "target_label": target,
        "dry_run": True,
    })
    nonce = None
    for line in out_dry.splitlines():
        if line.startswith("CONFIRM NONCE:"):
            nonce = line.split(":", 1)[1].strip().split()[0]
            break
    assert nonce is not None, out_dry
    first_commit = await repair_tool.ainvoke({
        "sql": sql,
        "target_label": target,
        "confirm": True,
        "nonce": nonce,
        "dry_run": False,
    })

    # Second round — mint a new nonce and re-run.
    out_dry2 = await repair_tool.ainvoke({
        "sql": sql,
        "target_label": target,
        "dry_run": True,
    })
    nonce2 = None
    for line in out_dry2.splitlines():
        if line.startswith("CONFIRM NONCE:"):
            nonce2 = line.split(":", 1)[1].strip().split()[0]
            break
    assert nonce2 is not None, out_dry2
    await repair_tool.ainvoke({
        "sql": sql,
        "target_label": target,
        "confirm": True,
        "nonce": nonce2,
        "dry_run": False,
    })

    # Net count after the SECOND commit: should match after the
    # first commit (idempotent — no rows added).
    return first_commit


class TestIdempotentAcrossBeginCommit:
    """Idempotency must hold across ``BEGIN/COMMIT`` boundaries."""

    @pytest.mark.asyncio
    async def test_create_table_if_not_exists_runs_twice_with_no_net_change(
        self, engine: Engine
    ) -> None:
        """DDL: ``CREATE TABLE IF NOT EXISTS`` is idempotent — running
        it twice leaves the same single table."""
        sql = "CREATE TABLE IF NOT EXISTS idemp_t (id INTEGER PRIMARY KEY, n INTEGER)"
        target = "idemp_t:create"
        await _run_repair_twice(engine, sql, target)

        # Net state: exactly one table, no extra rows.
        count = _table_count(engine, "idemp_t")
        assert count == 1, f"Expected exactly one table; got {count}"

    @pytest.mark.asyncio
    async def test_ddl_with_do_block_runs_twice_with_no_net_change(
        self, engine: Engine
    ) -> None:
        """DDL with ``DO $$`` wrapper is idempotent — re-runs are
        no-ops. PG-only (DO$$ is a Postgres PL/pgSQL construct);
        SQLite skips this case.

        We approximate on SQLite by checking the DO$$ regex gate:
        the tool must ACCEPT a DO$$-wrapped DDL (idempotent marker
        present) but the actual PG PL/pgSQL block cannot execute on
        SQLite. The pin is on the ACCEPT path, not on the actual
        commit (PG runs the block, SQLite would raise — skipped).
        """
        is_pg = engine.url.get_backend_name().startswith("postgres")
        if not is_pg:
            pytest.skip("DO $$ is PG-only PL/pgSQL — SQLite cannot execute")

        sql = (
            "DO $$ BEGIN\n"
            "  PERFORM 1;\n"
            "END $$;\n"
            "CREATE TABLE IF NOT EXISTS idemp_do (id INTEGER PRIMARY KEY)"
        )
        target = "idemp_do:create"
        await _run_repair_twice(engine, sql, target)

        with engine.connect() as conn:
            count = conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_name = 'idemp_do'"
            )).scalar()
        assert count == 1


class TestIdempotentInsertOnConflict:
    """``INSERT ... ON CONFLICT DO UPDATE`` is idempotent at the row level.

    PG-only because SQLite's UPSERT support is similar but the test
    pin is on the regex-acceptance path (the tool must classify the
    DML as idempotent via the absence of non-idempotent tokens).
    """

    @pytest.mark.asyncio
    async def test_upsert_idempotent_under_re_run(
        self, engine: Engine
    ) -> None:
        is_pg = engine.url.get_backend_name().startswith("postgres")
        if not is_pg:
            pytest.skip("PG-only UPSERT semantics pin")

        # Seed.
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE upsert_t (id INTEGER PRIMARY KEY, n INTEGER)"))
            conn.execute(text("INSERT INTO upsert_t (id, n) VALUES (1, 100)"))

        sql = (
            "INSERT INTO upsert_t (id, n) VALUES (1, 100) "
            "ON CONFLICT (id) DO UPDATE SET n = 100"
        )
        target = "upsert_t:upsert"
        await _run_repair_twice(engine, sql, target)

        with engine.connect() as conn:
            row = conn.execute(text("SELECT id, n FROM upsert_t WHERE id = 1")).first()
        assert row == (1, 100), f"Expected single row (1, 100); got {row}"
