"""Unit tests — post-prune ANALYZE hook (plan Track-1, item 3).

Covers the statistics-refresh sweep added at the END of the destructive
checkpoint-prune path:

* fires ONLY when the run actually deleted rows (a no-op sweep — the
  steady state — must not pay for an ANALYZE);
* targets exactly the three checkpoint tables
  (``checkpoints`` / ``checkpoint_blobs`` / ``checkpoint_writes``);
* runs on the DEDICATED per-cycle autocommit maintenance engine
  (``create_vacuum_engine``) — never on the hot saver pool and never on
  the shared engine's own connection;
* never raises: engine-build failures and per-table ANALYZE failures are
  swallowed (an ANALYZE failure must never fail the prune);
* the job-level wiring sums the prune's delete counters and forwards them
  to the helper.

Pure unit — no PostgreSQL server is contacted.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import daemon.services.checkpoint_prune as checkpoint_prune
import daemon.services.maintenance as maintenance
from daemon.services.checkpoint_prune import (
    ANALYZE_TARGET_TABLES,
    analyze_checkpoint_tables_after_prune,
)
from daemon.services.maintenance import (
    BlobPruneSummary,
    CheckpointCleanupJob,
    CheckpointRowPruneSummary,
)


# ── fakes ─────────────────────────────────────────────────────────────────────


class _FakeConn:
    def __init__(self, engine: "_FakeEngine") -> None:
        self._engine = engine

    def execute(self, stmt, *args, **kwargs):
        if self._engine.fail_on_execute:
            raise RuntimeError("analyze boom")
        self._engine.statements.append(str(stmt))
        return MagicMock()


class _FakeEngine:
    """Minimal stand-in for a SQLAlchemy Engine (the dedicated
    per-cycle autocommit engine). Records every executed statement."""

    def __init__(self, *, fail_on_connect: bool = False,
                 fail_on_execute: bool = False) -> None:
        self.statements: list[str] = []
        self.disposed = False
        self.fail_on_connect = fail_on_connect
        self.fail_on_execute = fail_on_execute

    def connect(self):
        if self.fail_on_connect:
            raise RuntimeError("connect boom")

        engine = self

        class _Ctx:
            def __enter__(self):
                return _FakeConn(engine)

            def __exit__(self, *exc):
                return False

        return _Ctx()

    def dispose(self) -> None:
        self.disposed = True


def _pg_engine() -> MagicMock:
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    return engine


@pytest.fixture
def fake_builder(monkeypatch):
    """Patch the dedicated-engine factory and hand back the recorded
    calls + the engine it returns."""
    calls: list[dict] = []
    engine = _FakeEngine()

    def _builder(shared_engine, **kwargs):
        calls.append({"shared_engine": shared_engine, **kwargs})
        return engine

    monkeypatch.setattr(checkpoint_prune, "create_vacuum_engine", _builder)
    return calls, engine


# ── the helper ────────────────────────────────────────────────────────────────


class TestPostPruneAnalyzeHelper:
    @pytest.mark.asyncio
    async def test_fires_when_rows_were_deleted(self, fake_builder):
        calls, engine = fake_builder
        outcome = await analyze_checkpoint_tables_after_prune(
            _pg_engine(), deleted_rows=7
        )
        assert outcome.ran is True
        assert outcome.skipped_reason is None
        assert outcome.deleted_rows == 7
        # Exactly the three checkpoint tables, in order.
        assert engine.statements == [
            "ANALYZE checkpoints",
            "ANALYZE checkpoint_blobs",
            "ANALYZE checkpoint_writes",
        ]
        assert outcome.analyzed_tables == list(ANALYZE_TARGET_TABLES)
        assert outcome.failed_tables == []
        # Per-cycle engine — always disposed.
        assert engine.disposed is True
        # The dedicated engine was built FROM the shared engine.
        assert len(calls) == 1
        assert calls[0]["shared_engine"] is not None

    @pytest.mark.asyncio
    async def test_no_op_sweep_never_analyzes(self, fake_builder):
        calls, engine = fake_builder
        outcome = await analyze_checkpoint_tables_after_prune(
            _pg_engine(), deleted_rows=0
        )
        assert outcome.ran is False
        assert outcome.skipped_reason == "no_rows_deleted"
        assert engine.statements == []
        assert calls == []  # no engine was even built
        assert engine.disposed is False

    @pytest.mark.asyncio
    async def test_analyze_failure_is_swallowed_per_table(
        self, monkeypatch
    ):
        engine = _FakeEngine(fail_on_execute=True)
        monkeypatch.setattr(
            checkpoint_prune,
            "create_vacuum_engine",
            lambda *a, **k: engine,
        )
        outcome = await analyze_checkpoint_tables_after_prune(
            _pg_engine(), deleted_rows=3
        )
        # No exception escaped — a failed ANALYZE never fails the prune.
        assert outcome.ran is True
        assert outcome.analyzed_tables == []
        assert outcome.failed_tables == list(ANALYZE_TARGET_TABLES)
        assert engine.disposed is True

    @pytest.mark.asyncio
    async def test_engine_build_failure_is_swallowed(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("engine build boom")

        monkeypatch.setattr(
            checkpoint_prune, "create_vacuum_engine", _boom
        )
        outcome = await analyze_checkpoint_tables_after_prune(
            _pg_engine(), deleted_rows=5
        )
        assert outcome.ran is False
        assert outcome.skipped_reason == "engine_build_failed"

    @pytest.mark.asyncio
    async def test_non_postgres_backend_is_skipped(self, fake_builder):
        calls, engine = fake_builder
        shared = MagicMock()
        shared.dialect.name = "sqlite"
        outcome = await analyze_checkpoint_tables_after_prune(
            shared, deleted_rows=5
        )
        assert outcome.ran is False
        assert outcome.skipped_reason == "not_postgresql"
        assert calls == []
        assert engine.statements == []

    @pytest.mark.asyncio
    async def test_missing_shared_engine_is_skipped(self, fake_builder):
        calls, engine = fake_builder
        outcome = await analyze_checkpoint_tables_after_prune(
            None, deleted_rows=5
        )
        assert outcome.ran is False
        assert outcome.skipped_reason == "no_shared_engine"
        assert calls == []
        assert engine.statements == []


# ── the job-level wiring ──────────────────────────────────────────────────────


def _job(shared_engine) -> CheckpointCleanupJob:
    return CheckpointCleanupJob(
        config=MagicMock(),
        checkpointer=MagicMock(),
        instance_repo=MagicMock(),
        shared_engine=shared_engine,
    )


class TestJobWiring:
    @pytest.mark.asyncio
    async def test_delete_counters_are_summed_and_forwarded(self, monkeypatch):
        seen: dict = {}

        async def _fake_helper(shared_engine, **kwargs):
            seen["shared_engine"] = shared_engine
            seen["kwargs"] = kwargs
            return MagicMock()

        monkeypatch.setattr(
            maintenance, "analyze_checkpoint_tables_after_prune", _fake_helper
        )
        shared = _pg_engine()
        job = _job(shared)
        rows = CheckpointRowPruneSummary(
            deleted_checkpoints=2, deleted_writes=1
        )
        blobs = BlobPruneSummary(total_deleted=3)
        await job._analyze_after_destructive_prune(
            rows_summary=rows, blobs_summary=blobs, deleted_threads=1
        )
        assert seen["shared_engine"] is shared
        assert seen["kwargs"]["deleted_rows"] == 7

    @pytest.mark.asyncio
    async def test_no_deletes_forward_zero(self, monkeypatch):
        seen: dict = {}

        async def _fake_helper(shared_engine, **kwargs):
            seen["kwargs"] = kwargs
            return MagicMock()

        monkeypatch.setattr(
            maintenance, "analyze_checkpoint_tables_after_prune", _fake_helper
        )
        job = _job(_pg_engine())
        await job._analyze_after_destructive_prune(
            rows_summary=CheckpointRowPruneSummary(),
            blobs_summary=BlobPruneSummary(),
        )
        assert seen["kwargs"]["deleted_rows"] == 0
