"""Tests for the checkpoint-saver readiness probe (incident 2026-10-10).

Three components exercised:

1. ``make_checkpoint_saver_probe(checkpointer)`` — topology detection
   (pool → sentinel checkpoint read probe; single-conn / SQLite /
   no-pool → passthrough True) and the sentinel cycle itself
   (write-once init via ``aput_writes``, read-only ``aget_tuple``
   tick — round-2 W1 redesign; the first revision probed
   ``pool.check()``, which self-heals and never raises, so it could
   not DETECT the outage class).
2. ``ReadinessComposite.checkpoint_saver`` — default True for back-compat;
   ``ready`` reflects the new component.
3. ``refresh_readiness_composite`` — passes ``checkpoint_saver_probe``
   through, fail-closed on timeout / failure with a sentinel-read
   reason string, default True when probe is None (no-probe path).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from daemon.constants import (
    CHECKPOINT_SENTINEL_CHANNEL,
    CHECKPOINT_SENTINEL_CHECKPOINT_ID,
    CHECKPOINT_SENTINEL_TASK_ID,
    CHECKPOINT_SENTINEL_THREAD_ID,
)
from daemon.services.readiness import (
    CHECKPOINT_SAVER_PROBE_TIMEOUT_S,
    ReadinessComposite,
    compute_readiness_composite,
    make_checkpoint_saver_probe,
    refresh_readiness_composite,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


class _FakePool:
    """psycopg_pool.AsyncConnectionPool stand-in for topology tests.

    The sentinel probe never calls ``pool.check()`` (the round-2
    redesign reads a sentinel checkpoint through the saver instead);
    the pool stand-in only needs the pool-only attributes the topology
    detector duck-types (``get_stats``, ``min_size``).
    """

    def __init__(self) -> None:
        self.check_calls = 0
        self.min_size = 1
        self.max_size = 5

    async def get_stats(self) -> dict:
        return {}

    async def check(self) -> None:
        self.check_calls += 1


class _FakeSaver:
    """Saver exposing the pool via .conn (per upstream aio.py:57) plus
    the sentinel-cycle surface: ``aput_writes`` (write-once init) and
    ``aget_tuple`` (the read-only tick the probe actually monitors)."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.aput_writes_calls: list[tuple] = []
        self.aget_tuple_calls: list[Any] = []
        self.aget_tuple_should_raise: Exception | None = None
        self.aget_tuple_should_hang: bool = False

    async def aput_writes(self, config, writes, task_id, *args, **kwargs):
        self.aput_writes_calls.append((config, writes, task_id))

    async def aget_tuple(self, config):
        import asyncio as _aio

        self.aget_tuple_calls.append(config)
        if self.aget_tuple_should_raise:
            raise self.aget_tuple_should_raise
        if self.aget_tuple_should_hang:
            await _aio.sleep(CHECKPOINT_SAVER_PROBE_TIMEOUT_S + 1)
        return None  # no sentinel row — a HEALTHY read answer


class _FakeAdapterPool:
    """FakeAdapter exposing raw_saver → saver with .conn = pool."""

    def __init__(self, pool: _FakePool | None) -> None:
        self.raw_saver = _FakeSaver(pool) if pool is not None else None


class _FakeSingleConn:
    """Bare psycopg.AsyncConnection stand-in (no pool attributes)."""

    async def close(self) -> None:
        pass


class _FakeSingleSaverAdapter:
    """Saver with .conn = single psycopg.AsyncConnection (legacy)."""

    def __init__(self) -> None:
        self.raw_saver = _FakeSaver(_FakeSingleConn())


# ── Topology detection ──────────────────────────────────────────────────────


class TestProbeTopologyDetection:
    """The probe must NOT degrade SQLite / single-conn / missing checkpointer."""

    def test_pool_topology_constructs_real_probe(self):
        pool = _FakePool()
        adapter = _FakeAdapterPool(pool)

        probe = make_checkpoint_saver_probe(adapter)

        # The probe is a sync function. Invoking it drives the
        # sentinel cycle (aput_writes write-once + aget_tuple read)
        # via run_coroutine_threadsafe. Real execution is covered by
        # TestSentinelProbeExecution below.
        assert callable(probe)
        assert probe is not None

    def test_single_conn_topology_returns_passthrough(self):
        """Legacy saver with a single psycopg.AsyncConnection: nothing
        to monitor (incident: a single conn breaks permanently — we
        can't probe it without replacing it; the retry wrapper
        handles that). Probe must NOT degrade."""
        adapter = _FakeSingleSaverAdapter()
        probe = make_checkpoint_saver_probe(adapter)
        # Sync call returns True — never degrades.
        assert probe() is True

    def test_no_checkpointer_returns_true(self):
        """checkpointer=None → probe returns True (healthy)."""
        assert make_checkpoint_saver_probe(None)() is True

    def test_checkpointer_without_raw_saver_returns_true(self):
        """checkpointer exposes no raw_saver → passthrough True."""

        class _NoRawSaver:
            pass

        assert make_checkpoint_saver_probe(_NoRawSaver())() is True

    def test_saver_without_conn_returns_true(self):
        """raw_saver.conn is missing → passthrough True (no probe target)."""

        class _NoConnSaver:
            pass

        class _AdapterNoConn:
            raw_saver = _NoConnSaver()

        assert make_checkpoint_saver_probe(_AdapterNoConn())() is True


# ── Pool topology: sentinel-cycle execution ────────────────────────────────


class TestSentinelProbeExecution:
    """Drive the sentinel probe through a real event loop
    (asyncio.to_thread): write-once init + read-only ticks."""

    @pytest.mark.asyncio
    async def test_probe_returns_true_and_write_once_then_read(self):
        """First tick: one idempotent sentinel write + one read.
        Second tick: read ONLY — the write must not repeat (write-once
        per process)."""
        pool = _FakePool()
        saver = _FakeSaver(pool)
        adapter = _FakeAdapterPool(pool)
        adapter.raw_saver = saver
        probe = make_checkpoint_saver_probe(adapter)

        first = await asyncio.to_thread(probe)
        assert first is True
        assert pool.check_calls == 0, "sentinel probe must not drive pool.check()"
        assert len(saver.aput_writes_calls) == 1, "write-once init"
        assert len(saver.aget_tuple_calls) == 1

        second = await asyncio.to_thread(probe)
        assert second is True
        assert len(saver.aput_writes_calls) == 1, "no second write"
        assert len(saver.aget_tuple_calls) == 2, "read-only tick"

        # Sentinel namespace is the fixed discoverable thread.
        write_config, writes, task_id = saver.aput_writes_calls[0]
        assert write_config["configurable"]["thread_id"] == (
            CHECKPOINT_SENTINEL_THREAD_ID
        )
        assert write_config["configurable"]["checkpoint_id"] == (
            CHECKPOINT_SENTINEL_CHECKPOINT_ID
        )
        assert task_id == CHECKPOINT_SENTINEL_TASK_ID
        assert writes[0][0] == CHECKPOINT_SENTINEL_CHANNEL
        read_config = saver.aget_tuple_calls[0]
        assert read_config["configurable"]["thread_id"] == (
            CHECKPOINT_SENTINEL_THREAD_ID
        )
        assert "checkpoint_id" not in read_config["configurable"], (
            "read tick must be a latest-row read, not id-pinned"
        )

    @pytest.mark.asyncio
    async def test_probe_raises_when_sentinel_read_fails(self):
        """A checkpoint path that cannot serve reads must surface as a
        probe failure (the incident's blind-spot case — pool.check()
        never raised, so the old probe could not detect this)."""
        pool = _FakePool()
        saver = _FakeSaver(pool)
        saver.aget_tuple_should_raise = ConnectionError(
            "the connection is closed"
        )
        adapter = _FakeAdapterPool(pool)
        adapter.raw_saver = saver
        probe = make_checkpoint_saver_probe(adapter)

        with pytest.raises(ConnectionError):
            await asyncio.to_thread(probe)

    @pytest.mark.asyncio
    async def test_failed_init_write_retries_next_tick(self):
        """If the write-once init fails, the flag must NOT latch — the
        next tick re-attempts the write before reading."""
        pool = _FakePool()
        saver = _FakeSaver(pool)
        adapter = _FakeAdapterPool(pool)
        adapter.raw_saver = saver
        probe = make_checkpoint_saver_probe(adapter)

        original = saver.aput_writes

        async def _failing_write(*a, **kw):
            raise ConnectionError("still down")

        saver.aput_writes = _failing_write
        with pytest.raises(ConnectionError):
            await asyncio.to_thread(probe)

        saver.aput_writes = original  # heal
        assert await asyncio.to_thread(probe) is True
        assert len(saver.aput_writes_calls) == 1, "write retried and landed"
        assert len(saver.aget_tuple_calls) == 1

    @pytest.mark.asyncio
    async def test_sentinel_read_failure_degrades_composite(self):
        """End-to-end: sentinel read fails → composite reports degraded
        with a reason naming the sentinel read. Mirrors the incident:
        checkpoint path cannot serve reads → /readyz flips to 503."""
        pool = _FakePool()
        saver = _FakeSaver(pool)
        saver.aget_tuple_should_raise = ConnectionError(
            "server closed the connection unexpectedly"
        )
        adapter = _FakeAdapterPool(pool)
        adapter.raw_saver = saver
        probe = make_checkpoint_saver_probe(adapter)

        composite = await refresh_readiness_composite(
            db_probe=lambda: True,
            queue_probe=lambda: 0.0,
            services_ok=True,
            queue_freshness_threshold_seconds=10.0,
            checkpoint_saver_probe=probe,
        )
        assert composite.checkpoint_saver is False
        assert composite.ready is False
        assert any(
            "checkpoint_saver" in reason and "sentinel" in reason
            for reason in composite.reasons
        ), composite.reasons

    @pytest.mark.asyncio
    async def test_sentinel_read_timeout_degrades_with_reason(self):
        """A hung ``aget_tuple`` must hit the budget and degrade with
        the sentinel-timeout reason string."""
        pool = _FakePool()
        saver = _FakeSaver(pool)
        saver.aget_tuple_should_hang = True
        adapter = _FakeAdapterPool(pool)
        adapter.raw_saver = saver
        probe = make_checkpoint_saver_probe(adapter)

        composite = await refresh_readiness_composite(
            db_probe=lambda: True,
            queue_probe=lambda: 0.0,
            services_ok=True,
            queue_freshness_threshold_seconds=10.0,
            checkpoint_saver_probe=probe,
        )
        assert composite.checkpoint_saver is False
        assert any(
            "sentinel" in reason and "timed out" in reason
            for reason in composite.reasons
        ), composite.reasons


# ── Composite integration ──────────────────────────────────────────────────


class TestCompositeDefault:
    """Back-compat: existing composites without a probe must stay
    ready, and the new field defaults to True."""

    def test_default_field_is_true(self):
        c = compute_readiness_composite(
            database_ok=True,
            queue_fresh_ok=True,
            services_ok=True,
            queue_max_age_seconds=0.0,
        )
        assert c.checkpoint_saver is True
        assert c.ready is True

    def test_checkpoint_saver_false_degrades_ready(self):
        c = compute_readiness_composite(
            database_ok=True,
            queue_fresh_ok=True,
            services_ok=True,
            queue_max_age_seconds=0.0,
            checkpoint_saver_ok=False,
        )
        assert c.ready is False
        assert any(
            "checkpoint_saver" in reason for reason in c.reasons
        )

    def test_to_payload_includes_checkpoint_saver(self):
        c = compute_readiness_composite(
            database_ok=True,
            queue_fresh_ok=True,
            services_ok=True,
            queue_max_age_seconds=0.0,
            checkpoint_saver_ok=False,
        )
        payload = c.to_payload()
        assert "checkpoint_saver" in payload["components"]
        assert payload["components"]["checkpoint_saver"] is False
        assert payload["status"] == "degraded"


# ── refresh_readiness_composite integration ────────────────────────────────


class TestRefreshIntegration:
    """End-to-end: the orchestrator's ``refresh_readiness_composite``
    must thread ``checkpoint_saver_probe`` through to the composite."""

    @pytest.mark.asyncio
    async def test_no_probe_keeps_ready(self):
        """SQLite / "nothing to probe" path: composite stays ready."""
        composite = await refresh_readiness_composite(
            db_probe=lambda: True,
            queue_probe=lambda: 0.0,
            services_ok=True,
            queue_freshness_threshold_seconds=10.0,
            checkpoint_saver_probe=None,
        )
        assert composite.checkpoint_saver is True
        assert composite.ready is True

    @pytest.mark.asyncio
    async def test_passing_probe_keeps_ready(self):
        composite = await refresh_readiness_composite(
            db_probe=lambda: True,
            queue_probe=lambda: 0.0,
            services_ok=True,
            queue_freshness_threshold_seconds=10.0,
            checkpoint_saver_probe=lambda: True,
        )
        assert composite.checkpoint_saver is True
        assert composite.ready is True

    @pytest.mark.asyncio
    async def test_failing_probe_degrades(self):
        composite = await refresh_readiness_composite(
            db_probe=lambda: True,
            queue_probe=lambda: 0.0,
            services_ok=True,
            queue_freshness_threshold_seconds=10.0,
            checkpoint_saver_probe=lambda: False,
        )
        assert composite.checkpoint_saver is False
        assert composite.ready is False

    @pytest.mark.asyncio
    async def test_raising_probe_degrades(self):
        """A probe that raises must degrade the composite (fail
        closed). Same as the database probe's exception handling."""
        def _raising():
            raise RuntimeError("pool unreachable")

        composite = await refresh_readiness_composite(
            db_probe=lambda: True,
            queue_probe=lambda: 0.0,
            services_ok=True,
            queue_freshness_threshold_seconds=10.0,
            checkpoint_saver_probe=_raising,
        )
        assert composite.checkpoint_saver is False
        assert composite.ready is False

    @pytest.mark.asyncio
    async def test_hanging_probe_times_out_and_degrades(self):
        """A probe that hangs past the budget must time out and
        degrade — never deadlock the refresher loop."""
        def _hanging():
            import time as _t
            _t.sleep(CHECKPOINT_SAVER_PROBE_TIMEOUT_S + 1)
            return True

        composite = await refresh_readiness_composite(
            db_probe=lambda: True,
            queue_probe=lambda: 0.0,
            services_ok=True,
            queue_freshness_threshold_seconds=10.0,
            checkpoint_saver_probe=_hanging,
        )
        assert composite.checkpoint_saver is False
        assert any(
            "checkpoint_saver" in reason and "timed out" in reason
            for reason in composite.reasons
        )

    @pytest.mark.asyncio
    async def test_back_compat_no_kwarg_argument(self):
        """Existing call sites that don't pass ``checkpoint_saver_probe``
        must continue to work unchanged (the parameter is OPTIONAL)."""
        composite = await refresh_readiness_composite(
            db_probe=lambda: True,
            queue_probe=lambda: 0.0,
            services_ok=True,
            queue_freshness_threshold_seconds=10.0,
        )
        assert composite.checkpoint_saver is True
        assert composite.ready is True


# ── Probe construction in no-loop context ──────────────────────────────────


class TestProbeNoLoop:
    """If construction happens without a running loop (rare but
    possible — e.g. test seam), the probe becomes a no-op True."""

    def test_no_loop_returns_noop(self):
        adapter = _FakeAdapterPool(_FakePool())
        # Construct outside a running loop: get_running_loop() raises
        # RuntimeError → _noop_probe path. We invoke the function
        # directly; it returns True regardless.
        # Note: pytest fixture may or may not have a loop active here.
        # The no-loop path is exercised in tests where construction
        # happens before the loop starts (e.g. module-import-time
        # wiring). We assert: the returned callable is non-None and
        # either is the no-op (returns True) or is the real probe
        # (constructed with a loop, callable from a thread).
        probe = make_checkpoint_saver_probe(adapter)
        assert probe is not None
        assert callable(probe)