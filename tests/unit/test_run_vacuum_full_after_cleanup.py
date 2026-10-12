"""Cluster 2.3/2.4/2.7/2.9/2.10 — ``CheckpointCleanupJob``
``_run_vacuum_full_after_cleanup`` unit-behavioral lanes.

Per test-plan.md §2.3 + the N-4 lane-placement rule: unit-behavioral
tests live in the unit file (stubbed engines/connections — per N-4
the plan's "real PG" phrasing in two rows is served by the e2e
maintenance lane, which self-provisions a disposable PG; see the
be-vacuum-full.md Exit Criterion). 9 tests:

1.  noop-on-False — zero DB work, no engine built;
2.  True → exactly 3 ``VACUUM FULL <table>`` in canonical order on
    checkpoints / checkpoint_writes / checkpoint_blobs;
3.  dedicated engine carries the long timeouts (60min / 5min / 2min)
    AND the trailing ``PG_SESSION_CONNECT_ARGS['options']`` UTC frame;
4.  engine disposed in ``finally`` even when a table raises;
5.  ``VacuumFullResult.to_summary_dict()`` canonical 8-key order
    (N2' — the order is load-bearing for audit-row consumers);
6.  spy-order (F8a) — the destructive prune arms (Op E blob arm →
    Op D row arm) complete BEFORE the FULL phase;
7.  headroom insufficient → refuse-to-escalate (no FULL issued);
8.  headroom degrade-to-skip (unresolvable tablespace path OR
    ``os.statvfs`` OSError) → WARNING + skip + FULL proceeds;
9.  residual ENOSPC race — the OperationalError PROPAGATES (the
    caller ``MaintenanceApiService._execute_run`` stamps the
    ``vacuum_full_enospc`` retriable error — service-level half lives
    in ``tests/unit/services/test_maintenance_checkpoint_cleanup_service.py``).
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import sqlalchemy.exc
from sqlalchemy import create_engine

import daemon.services.maintenance as maintenance_module
from daemon.config import PersistenceConfig
from daemon.services.checkpoint_prune import BlobPruneSummary
from daemon.services.maintenance import (
    CheckpointCleanupJob,
    CheckpointRowPruneSummary,
    VacuumFullResult,
)

# The canonical FULL table order (decisions.md §D-7.5 — the 3 tables
# the cleanup deletes from, in that order).
FULL_TABLES = ("checkpoints", "checkpoint_writes", "checkpoint_blobs")

MB = 1024 * 1024


def _job(shared_engine=None) -> CheckpointCleanupJob:
    """Real ``CheckpointCleanupJob`` with mock dependencies.

    ``shared_engine`` optional: the FULL engine builder renders its URL
    from it; a lazy (never-connected) PG engine is enough for the
    builder tests.
    """
    engine = shared_engine or create_engine(
        "postgresql+psycopg://user:pass@localhost:5432/db"
    )
    return CheckpointCleanupJob(
        config=PersistenceConfig(),
        checkpointer=MagicMock(),
        instance_repo=MagicMock(),
        shared_engine=engine,
    )


def _scripted_engine(handler):
    """Stub engine whose ``connect().execute(stmt)`` dispatches on the
    SQL text via ``handler(sql) -> scalar`` (a raised Exception
    propagates). Records every executed SQL string on
    ``conn.sql_log``."""
    conn = MagicMock()

    def _execute(stmt, *args, **kwargs):
        sql = str(stmt)
        conn.sql_log.append(sql)
        ret = handler(sql)
        result = MagicMock()
        result.scalar.return_value = ret
        result.fetchone.return_value = (ret,) if ret is not None else None
        result.fetchall.return_value = [(ret,)] if ret is not None else []
        return result

    conn.execute.side_effect = _execute
    conn.sql_log = []
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=conn)
    ctx.__exit__ = MagicMock(return_value=False)
    engine = MagicMock()
    engine.connect.return_value = ctx
    engine.dispose = MagicMock()
    return engine, conn


def _happy_headroom_handler(*, free_bytes: int, table_bytes: int,
                            ts_path: str | None = "/var/lib/postgresql/17/main",
                            data_dir: str | None = "/var/lib/postgresql/17/main",
                            statvfs=None, full_raises: Exception | None = None):
    """SQL script for the headroom queries (+ optional FULL failure)."""

    def _handler(sql: str):
        if "pg_tablespace_location" in sql:
            return ts_path
        if "data_directory" in sql:
            return data_dir
        if "pg_relation_size" in sql:
            return table_bytes
        if "VACUUM FULL" in sql.upper():
            if full_raises is not None:
                raise full_raises
            return None
        return None

    return _handler


# ── 1 — noop arm ─────────────────────────────────────────────────────────────


class TestVacuumFullFalseIsNoop:
    def test_vacuum_full_false_is_noop(self, monkeypatch):
        """``vacuum_full=False`` → immediate
        ``VacuumFullResult(escalated=False, vacuumed_tables=(),
        duration_ms=0)`` — zero DB work, no engine built."""
        job = _job()

        def _bomb():
            raise AssertionError("no engine may be built on the noop arm")

        monkeypatch.setattr(job, "_build_vacuum_full_engine", _bomb)
        result = asyncio.run(
            job._run_vacuum_full_after_cleanup(vacuum_full=False)
        )
        assert isinstance(result, VacuumFullResult)
        assert result.escalated is False
        assert result.vacuumed_tables == ()
        assert result.duration_ms == 0
        assert result.refusal_reason is None
        assert result.headroom_check_skipped is False


# ── 2 — FULL arm: exactly 3 statements in order ──────────────────────────────


class TestVacuumFullTrueRunsFullOnThreeTables:
    def test_vacuum_full_true_runs_full_on_three_tables(self, monkeypatch):
        """``vacuum_full=True`` with ample headroom → EXACTLY 3
        ``VACUUM FULL <table>`` statements, on the 3 tables, in
        canonical order; the engine is autocommit + NullPool."""
        engine, conn = _scripted_engine(
            _happy_headroom_handler(
                free_bytes=100 * 1024**3, table_bytes=10 * MB
            )
        )
        job = _job()
        monkeypatch.setattr(job, "_build_vacuum_full_engine", lambda: engine)
        # Daemon-side statvfs over the resolved tablespace path.
        monkeypatch.setattr(
            os, "statvfs",
            lambda path: SimpleNamespace(f_bavail=100 * 1024, f_frsize=MB),
        )

        result = asyncio.run(
            job._run_vacuum_full_after_cleanup(vacuum_full=True)
        )

        assert result.escalated is True
        assert result.vacuumed_tables == FULL_TABLES
        assert result.refusal_reason is None
        assert result.headroom_check_skipped is False
        full_sqls = [
            s for s in conn.sql_log if "VACUUM FULL" in s.upper()
        ]
        assert full_sqls == [f"VACUUM FULL {t}" for t in FULL_TABLES]


# ── 3 — engine long timeouts ─────────────────────────────────────────────────


class TestVacuumFullEngineHasLongTimeouts:
    def test_vacuum_full_engine_has_long_timeouts(self, monkeypatch):
        """The dedicated engine mirrors ``VacuumMaintenanceJob
        ._build_vacuum_engine``: options string carries
        statement_timeout=3600000 (60min), lock_timeout=300000 (5min),
        idle_in_transaction_session_timeout=120000 (2min) AND the
        trailing ``PG_SESSION_CONNECT_ARGS['options']`` (UTC session
        frame); autocommit=True; NullPool; echo=False."""
        captured: dict = {}

        def _fake_create_engine(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return MagicMock()

        monkeypatch.setattr(
            maintenance_module, "create_engine", _fake_create_engine
        )
        job = _job()
        built = job._build_vacuum_full_engine()
        assert built is not None

        options_str = captured["connect_args"]["options"]
        assert "statement_timeout=3600000" in options_str
        assert "lock_timeout=300000" in options_str
        assert "idle_in_transaction_session_timeout=120000" in options_str
        # The trailing UTC session options ride the SAME string (the
        # libpq ``options`` param merges server command-line options).
        assert options_str.rstrip().endswith(
            maintenance_module.PG_SESSION_CONNECT_ARGS["options"]
        )
        assert captured["connect_args"]["autocommit"] is True
        from sqlalchemy.pool import NullPool

        assert captured["poolclass"] is NullPool
        assert captured["echo"] is False
        assert "postgresql+psycopg://" in captured["url"]


# ── 4 — dispose in finally ───────────────────────────────────────────────────


class TestVacuumFullEngineDisposedInFinally:
    def test_vacuum_full_engine_disposed_in_finally(self, monkeypatch):
        """The dedicated engine is per-cycle: ``dispose()`` fires even
        when a table's VACUUM FULL raises."""
        enospc = sqlalchemy.exc.OperationalError(
            "VACUUM FULL checkpoint_writes",
            {},
            _pg_error("53100"),
        )
        engine, _conn = _scripted_engine(
            _happy_headroom_handler(
                free_bytes=100 * 1024**3,
                table_bytes=10 * MB,
                full_raises=enospc,
            )
        )
        job = _job()
        monkeypatch.setattr(job, "_build_vacuum_full_engine", lambda: engine)
        monkeypatch.setattr(
            os, "statvfs",
            lambda path: SimpleNamespace(f_bavail=100 * 1024, f_frsize=MB),
        )

        with pytest.raises(sqlalchemy.exc.OperationalError):
            asyncio.run(job._run_vacuum_full_after_cleanup(vacuum_full=True))

        engine.dispose.assert_called_once()


def _pg_error(pgcode: str) -> Exception:
    """A duck-typed DBAPI error carrying a PG SQLSTATE."""
    orig = Exception(
        "could not extend file: No space left on device"
    )
    orig.pgcode = pgcode
    return orig


# ── 5 — canonical 8-key summary order (N2') ─────────────────────────────────


class TestVacuumFullResultToSummaryDictCanonicalOrder:
    def test_vacuum_full_result_to_summary_dict_canonical_order_8_keys(self):
        """``to_summary_dict`` emits EXACTLY the 8 keys in the canonical
        order — explicit dict literal in field order, NOT
        ``dataclasses.asdict`` (the order is load-bearing for the
        audit-row ``summary_json.vacuum_full`` consumers)."""
        result = VacuumFullResult(
            vacuumed_tables=FULL_TABLES,
            duration_ms=12345,
            bytes_reclaimed=67890,
            escalated=True,
            refusal_reason=None,
            free_bytes=None,
            required_bytes=None,
            headroom_check_skipped=False,
        )
        got = result.to_summary_dict()
        assert list(got.keys()) == [
            "vacuumed_tables",
            "duration_ms",
            "bytes_reclaimed",
            "escalated",
            "refusal_reason",
            "free_bytes",
            "required_bytes",
            "headroom_check_skipped",
        ]
        assert got["vacuumed_tables"] == list(FULL_TABLES)
        assert got["duration_ms"] == 12345
        assert got["bytes_reclaimed"] == 67890
        assert got["escalated"] is True

        # Refusal shape — the same 8 keys, refusal populated.
        refused = VacuumFullResult(
            escalated=False,
            refusal_reason="insufficient_free_space",
            free_bytes=MB,
            required_bytes=15 * MB,
            headroom_check_skipped=False,
        ).to_summary_dict()
        assert list(refused.keys()) == list(got.keys())
        assert refused["refusal_reason"] == "insufficient_free_space"


# ── 6 — spy-order (F8a): prune arms BEFORE the FULL phase ───────────────────


class TestVacuumFullTrueStrictlyFollowsDestructivePrune:
    def test_vacuum_full_true_strictly_follows_destructive_prune(self):
        """Order pin: inside ``run_checkpoint_prunes(destructive=True,
        vacuum_full=True)`` the destructive prune arms run FIRST —
        (a) the blob arm (Op E), (b) the row arm (Op D) — and the FULL
        phase (c) runs THIRD, only after the destructive prune
        completes. Spies record the real internal arm methods.

        NOTE (spy-order adaptation): the plan-cited internal names
        ``_prune_unreferenced_blobs`` / ``_prune_per_thread_checkpoints``
        MATCH the real method names in ``daemon/services/maintenance.py``
        (verified — no drift)."""
        job = _job()
        calls: list[str] = []

        async def _blob_arm(*, destructive):
            calls.append("blob_arm")
            return BlobPruneSummary()

        async def _row_arm():
            calls.append("row_arm")
            return CheckpointRowPruneSummary()

        async def _full_phase(*, vacuum_full):
            calls.append("full_phase")
            assert vacuum_full is True
            return VacuumFullResult(
                escalated=True, vacuumed_tables=FULL_TABLES, duration_ms=5
            )

        job._prune_unreferenced_blobs = AsyncMock(side_effect=_blob_arm)
        job._prune_per_thread_checkpoints = AsyncMock(side_effect=_row_arm)
        job._run_vacuum_full_after_cleanup = AsyncMock(
            side_effect=_full_phase
        )

        result = asyncio.run(
            job.run_checkpoint_prunes(destructive=True, vacuum_full=True)
        )

        assert calls == ["blob_arm", "row_arm", "full_phase"], (
            "the FULL phase must run strictly AFTER the destructive "
            "prune arms (Op E blob arm → Op D row arm → FULL)"
        )
        job._prune_unreferenced_blobs.assert_awaited_once_with(
            destructive=True
        )
        job._prune_per_thread_checkpoints.assert_awaited_once_with()
        job._run_vacuum_full_after_cleanup.assert_awaited_once_with(
            vacuum_full=True
        )
        # The FULL block rides the returned result for the audit row.
        assert result.vacuum_full is not None
        assert result.vacuum_full.escalated is True


# ── 7 — headroom insufficient → refuse-to-escalate (R-B) ────────────────────


class TestVacuumFullHeadroomInsufficientRefusesToEscalate:
    def test_vacuum_full_headroom_insufficient_refuses_to_escalate(
        self, monkeypatch
    ):
        """Free 1MB vs required 15MB (10MB × 1.5 safety factor) →
        refuse: escalated=False, refusal_reason='insufficient_free_space',
        free/required populated, headroom_check_skipped=False, and ZERO
        ``VACUUM FULL`` statements issued."""
        free_bytes = 1 * MB
        table_bytes = 10 * MB
        engine, conn = _scripted_engine(
            _happy_headroom_handler(
                free_bytes=free_bytes, table_bytes=table_bytes
            )
        )
        job = _job()
        monkeypatch.setattr(job, "_build_vacuum_full_engine", lambda: engine)
        monkeypatch.setattr(
            os, "statvfs",
            lambda path: SimpleNamespace(
                f_bavail=free_bytes // MB, f_frsize=MB
            ),
        )

        result = asyncio.run(
            job._run_vacuum_full_after_cleanup(vacuum_full=True)
        )

        assert result.escalated is False
        assert result.refusal_reason == "insufficient_free_space"
        assert result.free_bytes == free_bytes
        assert result.required_bytes == int(table_bytes * 1.5)
        assert result.headroom_check_skipped is False
        assert result.vacuumed_tables == ()
        full_sqls = [s for s in conn.sql_log if "VACUUM FULL" in s.upper()]
        assert full_sqls == []


# ── 8 — headroom degrade-to-skip (R-B, B3 re-spec) ──────────────────────────


class TestVacuumFullHeadroomDegradesToSkip:
    def test_vacuum_full_headroom_degrades_to_skip_on_unresolvable_path(
        self, monkeypatch, caplog
    ):
        """DEGRADE-TO-SKIP: (a) unresolvable tablespace path (empty
        tablespace location AND unresolvable data directory), or (b)
        ``os.statvfs`` raising OSError → the check logs WARNING, is
        SKIPPED (``headroom_check_skipped=True``), and the FULL phase
        STILL PROCEEDS (refusal_reason stays None — the residual
        ENOSPC path is the only safety net). Also covers the remote-DB
        arm (non-local engine host)."""
        job = _job()

        # (a) both path probes unresolvable.
        engine_a, conn_a = _scripted_engine(
            _happy_headroom_handler(
                free_bytes=0, table_bytes=0, ts_path=None, data_dir=None
            )
        )
        monkeypatch.setattr(job, "_build_vacuum_full_engine", lambda: engine_a)
        with caplog.at_level("WARNING", logger="daemon.services.maintenance"):
            result_a = asyncio.run(
                job._run_vacuum_full_after_cleanup(vacuum_full=True)
            )
        assert result_a.escalated is True
        assert result_a.vacuumed_tables == FULL_TABLES
        assert result_a.headroom_check_skipped is True
        assert result_a.refusal_reason is None
        assert any(
            "VACUUM FULL" in s.upper() for s in conn_a.sql_log
        )

        # (b) os.statvfs raises OSError (permission / remote mount).
        def _raise_statvfs(path):
            raise OSError(2, "No such file or directory")

        engine_b, conn_b = _scripted_engine(
            _happy_headroom_handler(
                free_bytes=0, table_bytes=10 * MB
            )
        )
        monkeypatch.setattr(job, "_build_vacuum_full_engine", lambda: engine_b)
        monkeypatch.setattr(os, "statvfs", _raise_statvfs)
        with caplog.at_level("WARNING", logger="daemon.services.maintenance"):
            result_b = asyncio.run(
                job._run_vacuum_full_after_cleanup(vacuum_full=True)
            )
        assert result_b.escalated is True
        assert result_b.headroom_check_skipped is True
        assert result_b.refusal_reason is None
        assert any(
            "VACUUM FULL" in s.upper() for s in conn_b.sql_log
        )

        # (c) remote DB (engine host not local) → skip, FULL proceeds.
        remote_engine = create_engine(
            "postgresql+psycopg://user:pass@db.remote.internal:5432/db"
        )
        remote_job = CheckpointCleanupJob(
            config=PersistenceConfig(),
            checkpointer=MagicMock(),
            instance_repo=MagicMock(),
            shared_engine=remote_engine,
        )
        engine_c, conn_c = _scripted_engine(
            _happy_headroom_handler(
                free_bytes=0, table_bytes=10 * MB
            )
        )
        monkeypatch.setattr(
            remote_job, "_build_vacuum_full_engine", lambda: engine_c
        )
        monkeypatch.setattr(
            os, "statvfs",
            lambda path: SimpleNamespace(f_bavail=1, f_frsize=MB),
        )
        with caplog.at_level("WARNING", logger="daemon.services.maintenance"):
            result_c = asyncio.run(
                remote_job._run_vacuum_full_after_cleanup(vacuum_full=True)
            )
        assert result_c.escalated is True
        assert result_c.headroom_check_skipped is True
        assert result_c.refusal_reason is None
        assert any(
            "VACUUM FULL" in s.upper() for s in conn_c.sql_log
        )

        # At least one WARNING mentions the skipped headroom check.
        assert any(
            "headroom" in r.message.lower() or "statvfs" in r.message.lower()
            or "skipped" in r.message.lower()
            for r in caplog.records
        )


# ── 9 — residual ENOSPC race (R-B, B4 error-code contract) ──────────────────


class TestVacuumFullResidualEnospcAfterCheckRace:
    def test_vacuum_full_residual_enospc_after_check_race(
        self, monkeypatch
    ):
        """Headroom PASSES (free 100GB > required 15MB) but the FULL
        phase hits SQLSTATE 53100 anyway (check-race / concurrent
        growth) → the OperationalError PROPAGATES out of the method
        (never swallowed here); the caller
        ``MaintenanceApiService._execute_run`` stamps
        ``error_json={'code': 'vacuum_full_enospc', 'retriable': True}``
        (asserted service-side in
        ``tests/unit/services/test_maintenance_checkpoint_cleanup_service.py``)."""
        enospc = sqlalchemy.exc.OperationalError(
            "VACUUM FULL checkpoints", {}, _pg_error("53100")
        )
        engine, _conn = _scripted_engine(
            _happy_headroom_handler(
                free_bytes=100 * 1024**3,
                table_bytes=10 * MB,
                full_raises=enospc,
            )
        )
        job = _job()
        monkeypatch.setattr(job, "_build_vacuum_full_engine", lambda: engine)
        monkeypatch.setattr(
            os, "statvfs",
            lambda path: SimpleNamespace(f_bavail=100 * 1024, f_frsize=MB),
        )

        with pytest.raises(sqlalchemy.exc.OperationalError) as excinfo:
            asyncio.run(job._run_vacuum_full_after_cleanup(vacuum_full=True))
        assert getattr(getattr(excinfo.value, "orig", None), "pgcode", None) == (
            "53100"
        )
        # The per-cycle engine is STILL disposed on the raise path.
        engine.dispose.assert_called_once()
