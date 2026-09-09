"""``repair_log`` audit contract (W1-P2, task 2.10b).

Pins:

1. Every committed repair writes a ``repair_log`` row stamped with
   ``created_by_instance_id == current_instance_id`` (mirrors
   ``InfraAssetHistory.changed_by`` audit pattern at
   ``infra/models.py:412``).
2. The audit ``INSERT`` runs in the SAME ``BEGIN/COMMIT`` as the
   repair on the SAME connection — fail-closed on audit-write
   failure (architect §3.2). We assert this by forcing an
   audit-write failure (e.g. via a session that raises on
   ``session.add``) and confirming the repair is NOT committed.

Council disagreement pin (2.10b — checklist item 4): the spec
directs the implementer to pin the precedent location between
``daemon/repositories/infra.py:226-234`` and
``daemon/repositories/infra/models.py:256 + infra/repository.py:540-541``.
This module pins the location at
``daemon/repositories/ens_db/models.py:RepairLog`` because the
changed-by audit pattern matches ``InfraAssetHistory.changed_by``
(nullable string ``created_by_instance_id`` set at write time,
indexed on ``(target, timestamp)``). The ``infra.py:226-234``
asset-audit path is in-process audit emitted by a service, not a
persisted SQLModel row — the wrong precedent for an
append-only-audit table.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session

from daemon.repositories.ens_db.models import RepairLog
from daemon.tools.ens_db_tools import create_ens_db_tools


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    """File-backed SQLite at tmp_path + NullPool + WAL + busy_timeout."""
    db_path = tmp_path / "ens_db_audit.db"
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

    from sqlmodel import SQLModel
    SQLModel.metadata.create_all(eng)
    return eng


def _manager_with(engine: Engine) -> MagicMock:
    mgr = MagicMock()
    mgr.engine = engine
    return mgr


# ── Part 1: precedent-location pin ──────────────────────────────────────────


class TestPrecedentLocationPin:
    """Pin the precedent location the spec adjudicated.

    The spec says the council disagreement is between
    ``daemon/repositories/infra.py:226-234`` and
    ``daemon/repositories/infra/models.py:256 + infra/repository.py:540-541``.
    This test asserts the chosen location matches the chosen
    pattern (nullable string ``created_by_instance_id`` at write time,
    indexed on ``(target, timestamp)``) — exactly mirroring
    ``InfraAssetHistory.changed_by``.
    """

    def test_repair_log_has_changed_by_pattern(self) -> None:
        """The chosen model mirrors ``InfraAssetHistory.changed_by``."""
        # ``changed_by`` → our field is ``created_by_instance_id``.
        assert "created_by_instance_id" in RepairLog.model_fields
        # Nullable string (matches infra pattern).
        field = RepairLog.model_fields["created_by_instance_id"]
        assert field.default is None
        assert field.annotation is str | None or "str" in str(field.annotation)
        # Indexed on ``(target_table, created_at)`` — same shape as
        # ``idx_infra_asset_history_asset_timestamp``.
        idx_names = {i.name for i in RepairLog.__table__.indexes}
        assert "idx_repair_log_target_timestamp" in idx_names


# ── Part 2: created_by stamping on committed repair ─────────────────────────


class TestCreatedByStamping:
    """Committed repairs stamp ``created_by_instance_id``.

    We construct a tool invocation where the agent is given a
    nonce (dry-run-minted), then asserts the persisted row carries
    the agent's instance id verbatim.
    """

    @pytest.mark.asyncio
    async def test_committed_repair_stamps_caller_instance_id(
        self, engine: Engine
    ) -> None:
        # Seed: a row to mutate.
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE foo (id INTEGER PRIMARY KEY, n INTEGER)"))
            conn.execute(text("INSERT INTO foo (id, n) VALUES (1, 10)"))

        mgr = _manager_with(engine)
        caller = "instance-abc-123"
        tools = create_ens_db_tools(mgr, caller, agent_id="maintenancer")
        repair_tool = next(t for t in tools if t.name == "ens_db_repair_execute")

        # Step 1: dry-run mints the nonce.
        out_dry = await repair_tool.ainvoke({
            "sql": "UPDATE foo SET n = 20 WHERE id = 1",
            "target_label": "foo:update-n",
            "dry_run": True,
        })
        # Extract the CONFIRM NONCE line.
        nonce = None
        for line in out_dry.splitlines():
            if line.startswith("CONFIRM NONCE:"):
                nonce = line.split(":", 1)[1].strip().split()[0]
                break
        assert nonce is not None, f"No nonce minted; dry-run output:\n{out_dry}"
        assert nonce.startswith("CONFIRM-")

        # Step 2: confirm-commit with the nonce.
        out_commit = await repair_tool.ainvoke({
            "sql": "UPDATE foo SET n = 20 WHERE id = 1",
            "target_label": "foo:update-n",
            "confirm": True,
            "nonce": nonce,
            "dry_run": False,
        })
        assert "REPAIR COMMITTED" in out_commit

        # Step 3: assert the audit row.
        with Session(engine) as s:
            rows = s.exec(__import__("sqlmodel").select(RepairLog)).all()
        assert len(rows) >= 1
        committed = [r for r in rows if r.outcome == "committed"]
        assert len(committed) >= 1
        row = committed[-1]
        assert row.created_by_instance_id == caller, (
            f"audit created_by_instance_id mismatch: got {row.created_by_instance_id!r} "
            f"want {caller!r}"
        )
        assert row.sql_class == "DML"
        assert row.target_table == "foo:update-n"


# ── Part 3: same-tx audit fail-closed (architect §3.2) ──────────────────────


class TestSameTxAuditFailClosed:
    """If the audit ``INSERT`` raises, the repair MUST roll back.

    We force an audit-write failure by monkey-patching the
    :class:`RepairLog` constructor (via a side-effect ``session.add``
    hook that raises). The repair row then must NOT be visible.
    """

    @pytest.mark.asyncio
    async def test_audit_write_failure_rolls_back_repair(
        self, engine: Engine
    ) -> None:
        # Seed.
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE bar (id INTEGER PRIMARY KEY, n INTEGER)"))
            conn.execute(text("INSERT INTO bar (id, n) VALUES (1, 100)"))

        mgr = _manager_with(engine)
        tools = create_ens_db_tools(mgr, "inst-fc", agent_id="maintenancer")
        repair_tool = next(t for t in tools if t.name == "ens_db_repair_execute")

        # Patch :func:`daemon.tools.ens_db_tools.RepairLog` to raise
        # at construction — forces the audit row to fail.
        import daemon.tools.ens_db_tools as t

        original_init = t.RepairLog.__init__

        def _boom_init(self, **kwargs: Any) -> None:
            raise RuntimeError("simulated audit-write failure")

        t.RepairLog.__init__ = _boom_init
        try:
            # Mint a nonce via dry-run, then attempt confirm-commit.
            out_dry = await repair_tool.ainvoke({
                "sql": "UPDATE bar SET n = 999 WHERE id = 1",
                "target_label": "bar:update-n",
                "dry_run": True,
            })
            nonce = None
            for line in out_dry.splitlines():
                if line.startswith("CONFIRM NONCE:"):
                    nonce = line.split(":", 1)[1].strip().split()[0]
                    break
            assert nonce is not None, out_dry

            out_commit = await repair_tool.ainvoke({
                "sql": "UPDATE bar SET n = 999 WHERE id = 1",
                "target_label": "bar:update-n",
                "confirm": True,
                "nonce": nonce,
                "dry_run": False,
            })
        finally:
            t.RepairLog.__init__ = original_init

        # The tool returns an error string (the rolled-back path
        # writes a separate audit row marked ``error`` after
        # session.rollback(), but the REPAIR itself is rolled back).
        assert "ERROR" in out_commit, f"Expected ERROR, got: {out_commit!r}"

        # Assert: the repair row was rolled back — bar.n must still be 100.
        with engine.connect() as conn:
            n_after = conn.execute(text("SELECT n FROM bar WHERE id = 1")).scalar()
        assert n_after == 100, (
            f"Fail-closed violated: repair was committed despite audit failure "
            f"(n_after={n_after}, expected 100)"
        )
