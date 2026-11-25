"""ITEM 2 pins — the checkpoint_blobs BYTES guard (SCOPE (c)).

The guard exists because the checkpoint-blob bloat class is
DEAD-TUP-BLIND: the measured incident (2026-11-23, the PG18 host) found
``n_dead_tup`` reading ZERO on BOTH ``checkpoint_blobs`` and its TOAST
relation while the relation carried ~29MB of heap + ~39.1GiB of TOAST
(5,128,590 pages, ~87% empty) and autovacuum ran on it without
reclaiming anything. A dead-tuple-driven threshold therefore reads
"healthy" on exactly the class it must catch.

So the guard measures BYTES — ``pg_total_relation_size`` plus the
TOAST size via ``reltoastrelid`` (the house SQL precedent:
``docs/runbooks/checkpoint-maintenance.md`` §P6b and
``daemon/tools/ens_db_tools.py``'s inspect arm) — and ALERTS when the
total is above the threshold. It does not reclaim and it does not
enforce: the space returns only via a rewrite.

Pins:

* the measurement is BYTES (the production SQL reads the relation
  size and the TOAST size);
* the measurement is NOT dead-tuple-driven (no ``n_dead_tup`` in the
  guard's SQL — the dead-tuple-blindness is asserted, not assumed);
* the threshold fires the operator-visible alert;
* the kill-switch disables the measurement;
* a failed measurement never wedges the cycle (the block names the
  skip);
* the block rides the maintenance_runs audit row.

Harness: a REAL PostgreSQL (the repo's disposable-database harness —
it skips loudly when PostgreSQL is unreachable; a mock is never
substituted for the real server).
"""

from __future__ import annotations

import asyncio
import inspect
import logging

import pytest
from sqlalchemy import text

from daemon.services import checkpoint_prune as cp
from daemon.services import maintenance as mt

from tests.helpers.checkpoint_prune_pg import (
    create_disposable_db,
    drop_database,
    evict_langgraph_mocks,
    real_pg_checkpointer,
    require_postgres,
    restore_langgraph_mocks,
)


@pytest.fixture(autouse=True)
def _real_langgraph():
    """Evict the root-conftest langgraph mocks so the REAL postgres
    saver loads (the house pattern)."""
    saved = evict_langgraph_mocks()
    try:
        yield
    finally:
        restore_langgraph_mocks(saved)


@pytest.fixture(scope="module")
def pg_db_dsn():
    """A disposable PG database for the bytes-guard fixture."""
    require_postgres()
    loop = asyncio.new_event_loop()
    name, dsn = loop.run_until_complete(create_disposable_db())
    try:
        yield name, dsn
    finally:
        loop.run_until_complete(drop_database(name))


@pytest.fixture
async def pg_adapter(pg_db_dsn):
    name, dsn = pg_db_dsn
    async with real_pg_checkpointer(name, dsn) as (_saver, _pool, adapter):
        yield adapter


def _pg_engine(dsn: str):
    """A SQLAlchemy engine on the disposable database (the guard's
    measurement runs on its OWN dedicated per-cycle engine, but the
    numbers are the same relation)."""
    from sqlalchemy import create_engine

    return create_engine(dsn.replace("postgresql://", "postgresql+psycopg://"))


class TestBlobRelationBytesMeasurement:
    @pytest.mark.asyncio
    async def test_the_measurement_reads_the_relation_and_toast_size(
        self, pg_adapter, pg_db_dsn,
    ):
        """The production SQL measures the RELATION size and the TOAST
        size — the two numbers the incident's occupancy signal needs."""
        name, dsn = pg_db_dsn
        async with pg_adapter._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, "
                "channel, version, type, blob) "
                "VALUES ('t', '', 'messages', 'v1', 'msgpack', $1)",
                b"\x01" * 4096,
            )

        engine = _pg_engine(dsn)
        try:
            total, heap, toast = cp._collect_blob_relation_bytes_sync(engine)
        finally:
            engine.dispose()

        assert total > 0, "the relation must have a measurable size"
        assert heap > 0, "the heap must have a measurable size"
        assert toast >= 0
        assert total >= heap, (
            "pg_total_relation_size (heap + toast + indexes) must be at "
            "least the heap size"
        )

    def test_the_measurement_is_not_dead_tuple_driven(self):
        """THE POINT OF THE WHOLE ITEM: the guard's SQL must not read
        ``n_dead_tup`` (it reads ZERO on the bloat class)."""
        sql = cp._BLOB_RELATION_BYTES_SQL.lower()
        assert "n_dead_tup" not in sql
        assert "pg_total_relation_size" in sql
        assert "reltoastrelid" in sql

    def test_the_guard_module_states_the_dead_tup_blindness(self):
        """The reason the guard is bytes-based is recorded IN CODE."""
        import inspect

        source = inspect.getsource(cp)
        assert "DEAD-TUP-BLIND" in source
        assert "n_dead_tup" in source


class TestBlobBytesGuardBehaviour:
    @pytest.mark.asyncio
    async def test_the_threshold_fires_the_operator_visible_alert(
        self, pg_adapter, pg_db_dsn, caplog, monkeypatch,
    ):
        name, dsn = pg_db_dsn
        # A threshold of 1 byte ⇒ the (non-empty) relation is over it.
        monkeypatch.setenv(mt.BLOB_BYTES_ALERT_THRESHOLD_ENV, "1")
        engine = _pg_engine(dsn)
        try:
            job = mt.CheckpointCleanupJob(
                config=mt.PersistenceConfig(),
                checkpointer=None,
                instance_repo=None,
                shared_engine=engine,
            )
            # A threshold of 1 byte ⇒ the (non-empty) relation is over it.
            with caplog.at_level(logging.WARNING):
                guard = await job._collect_blob_bytes_guard()
        finally:
            engine.dispose()

        assert guard.ran is True, guard
        assert guard.total_bytes > 0, guard
        assert guard.over_threshold is True, guard
        assert guard.alerted is True, guard
        assert any(
            "CHECKPOINT_BLOB_BYTES_OVER_THRESHOLD" in r.message
            for r in caplog.records
        ), "the alert must be operator-visible"

    @pytest.mark.asyncio
    async def test_the_kill_switch_disables_the_measurement(
        self, pg_db_dsn, monkeypatch,
    ):
        name, dsn = pg_db_dsn
        engine = _pg_engine(dsn)
        try:
            job = mt.CheckpointCleanupJob(
                config=mt.PersistenceConfig(),
                checkpointer=None,
                instance_repo=None,
                shared_engine=engine,
            )
            monkeypatch.setenv(mt.BLOB_BYTES_GUARD_KILL_SWITCH_ENV, "0")
            guard = await job._collect_blob_bytes_guard()
        finally:
            engine.dispose()

        assert guard.ran is False, guard
        assert guard.skipped_reason == "kill_switch", guard
        assert guard.over_threshold is False, guard
        assert guard.alerted is False, guard

    @pytest.mark.asyncio
    async def test_a_failed_measurement_never_wedges(self, monkeypatch):
        """No engine ⇒ no measurement, and the block SAYS so (an absent
        measurement must never read as "zero bytes")."""
        job = mt.CheckpointCleanupJob(
            config=mt.PersistenceConfig(),
            checkpointer=None,
            instance_repo=None,
            shared_engine=None,
        )
        guard = await job._collect_blob_bytes_guard()
        assert guard.ran is False, guard
        assert guard.skipped_reason == "no_shared_engine", guard
        assert guard.total_bytes == 0, guard
        assert guard.over_threshold is False, guard

    @pytest.mark.asyncio
    async def test_a_raising_measurement_is_contained(self, monkeypatch):
        """The guard never raises out of the cycle."""
        job = mt.CheckpointCleanupJob(
            config=mt.PersistenceConfig(),
            checkpointer=None,
            instance_repo=None,
            shared_engine=None,
        )

        async def _boom(*args, **kwargs):
            raise RuntimeError("measurement exploded")

        monkeypatch.setattr(
            cp, "collect_blob_relation_bytes", _boom,
        )
        guard = await job._collect_blob_bytes_guard()
        assert guard.ran is False, guard
        assert guard.skipped_reason == "query_failed", guard

    def test_the_summary_block_is_json_passthrough(self):
        """The block rides ``maintenance_runs.summary_json`` as a plain
        dict (the audit row is JSON — a dataclass would not serialise)."""
        guard = mt.BlobBytesGuard(
            ran=True, total_bytes=10, heap_bytes=4, toast_bytes=6,
            threshold_bytes=5, over_threshold=True, alerted=True,
        )
        block = guard.to_summary_dict()
        assert block["ran"] is True
        assert block["total_bytes"] == 10
        assert block["toast_bytes"] == 6
        assert block["over_threshold"] is True
        assert block["alerted"] is True
        assert block["skipped_reason"] is None
        import json

        json.dumps(block)  # must be JSON-serialisable
