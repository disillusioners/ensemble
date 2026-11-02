"""The W1 FAMILY PACK — F0.3, F1, F3, F4/F5, F6.

The registered pack ids of this file (the PACKS.md rows the tester lane
registers):

* ``metrics_family_f0_3`` — the resource spine (the WS-A ancestry
  conditional family);
* ``metrics_family_f1`` — the loop-lag quantiles;
* ``metrics_family_f3`` — the wedge capture counters;
* ``metrics_family_f4_f5`` — the pool families (native vs derived);
* ``metrics_family_f6`` — the PostgreSQL census.

WHAT EACH PACK PROVES
---------------------
* the family renders in BOTH surfaces — the ``/metrics`` exposition AND
  the query door (the provider's ``families()`` carries the family);
* every label key declares ``allowed_values`` (AM-2);
* the family carries its registration-time metadata (AM-3);
* the OUT metrics of the cluster are ABSENT — F2 (the value ruling) is
  nowhere in the registry;
* the adapters READ the producer and never re-probe it.
"""

from __future__ import annotations

from typing import Any

import pytest

from daemon import checkpoint_metrics as cm
from daemon.services.metrics_query.wiring import sources as wiring_sources
from daemon.services.metrics_query.wiring import vocab
from daemon.services.metrics_query.wiring.w1 import (
    adapt_f0_3,
    adapt_f1,
    adapt_f3,
    adapt_f4_f5,
    adapt_f6,
)


class _FakePool:
    """A psycopg-pool-shaped fake (``get_stats`` + ``min_size``)."""

    def __init__(self, stats: dict[str, Any]) -> None:
        self._stats = stats
        self.min_size = 1

    def get_stats(self) -> dict[str, Any]:
        return dict(self._stats)


class _FakeLagProbe:
    """A lag-probe-shaped fake (the computed quantiles + the state)."""

    def __init__(self, histogram: dict[str, Any], state: str = "ok") -> None:
        self._histogram = histogram
        self.probe_state = state

    def snapshot_histogram(self) -> dict[str, Any]:
        return dict(self._histogram)


class _FakeWatchdog:
    def __init__(self, probe: Any) -> None:
        self._lag_probe = probe


class _FakeCounters:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _FakeMonitor:
    def __init__(self, *, ticks: int = 3, errors: int = 0) -> None:
        self._sampler = _FakeCounters(ticks=ticks, ticks_skipped=0, missed_samples=0)
        self._flusher = _FakeCounters(batches=2, bytes_written=10, errors=errors)

    def sampler_counters(self) -> Any:
        return self._sampler

    def flusher_counters(self) -> Any:
        return self._flusher


class _FakeSnapshot:
    def __init__(
        self,
        *,
        state_counts: dict[str, int] | None = None,
        idle_by_site: dict[str, int] | None = None,
        idle_age: dict[str, float] | None = None,
        last_success: float | None = 1_700_000_000.0,
    ) -> None:
        self.state_counts = state_counts or {}
        self.idle_in_txn_by_site = idle_by_site or {}
        self.idle_in_txn_max_age_by_site = idle_age or {}
        self.last_success_epoch = last_success


class _FakeCensus:
    def __init__(self, snapshot: Any) -> None:
        self.snapshot = snapshot


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.sql = sql  # type: ignore[attr-defined]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeConnection:
    """A connection fake that records the SQL it was asked for."""

    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows
        self.queries: list[str] = []
        self.closed = False

    def cursor(self) -> _FakeCursor:
        outer = self

        class _C(_FakeCursor):
            def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
                outer.queries.append(sql)
                super().execute(sql, params)

        return _C(self._rows)

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    cm.reset_for_tests()
    yield
    cm.reset_for_tests()
    wiring_sources.unbind_sources()


def _manager_with_saver_pool(pool: Any) -> Any:
    """A manager fake whose CHECKPOINTER carries the psycopg pool.

    The saver pool is reached through ``resolve_psycopg_pool`` — the
    checkpointer's ``raw_saver.conn`` — which is the daemon's own
    resolution.
    """

    class _Saver:
        def __init__(self, conn: Any) -> None:
            self.conn = conn

    class _Checkpointer:
        def __init__(self, conn: Any) -> None:
            self.raw_saver = _Saver(conn)

    class _Engine:
        pool = None

    class _Manager:
        engine = _Engine()
        checkpointer = None

    manager = _Manager()
    manager.checkpointer = _Checkpointer(pool)
    return manager


class TestF03ResourceSpine:
    """``metrics_family_f0_3`` — the resource spine."""

    def test_the_family_renders_with_its_metadata(self) -> None:
        from daemon.services.metrics_query.wiring import registered_families

        spec = registered_families()["F0.3"]
        assert spec.verdict == "IN"
        metric = cm.registered_metrics()
        names = {m.name for m in metric}
        assert "ensemble_resource_spine_tables_accepting_rows" in names
        assert "ensemble_resource_spine_rows_written_total" in names

    def test_every_label_key_declares_allowed_values(self) -> None:
        from daemon.services.metrics_query.wiring.w1 import spine_tables_accepting_rows

        assert set(spine_tables_accepting_rows.allowed_values) == {"table"}
        assert spine_tables_accepting_rows.allowed_values["table"] == frozenset(
            vocab.SPINE_TABLES
        )

    def test_the_adapter_reads_the_spine_counters(self) -> None:
        from daemon.services.metrics_query.wiring.w1 import spine_tables_accepting_rows

        wiring_sources.bind_sources(
            wiring_sources.WiringSources(resource_monitor=_FakeMonitor(ticks=5, errors=0))
        )
        adapt_f0_3(None)
        for table in vocab.SPINE_TABLES:
            assert spine_tables_accepting_rows.get(table=table) == 1.0

    def test_a_broken_spine_renders_zero_not_one(self) -> None:
        from daemon.services.metrics_query.wiring.w1 import spine_tables_accepting_rows

        wiring_sources.bind_sources(
            wiring_sources.WiringSources(resource_monitor=_FakeMonitor(ticks=0, errors=2))
        )
        adapt_f0_3(None)
        assert spine_tables_accepting_rows.get(table="resource_daemon_bucket") == 0.0
        # The policy table accepts ZERO rows BY DESIGN — it stays 1.
        assert spine_tables_accepting_rows.get(table="resource_limit_policy") == 1.0

    def test_no_monitor_renders_no_series(self) -> None:
        from daemon.services.metrics_query.wiring.w1 import spine_tables_accepting_rows

        wiring_sources.bind_sources(wiring_sources.WiringSources())
        adapt_f0_3(None)
        assert spine_tables_accepting_rows.series_keys() == []


class TestF1LoopLag:
    """``metrics_family_f1`` — the loop-lag quantiles."""

    def test_the_quantiles_are_gauges_not_a_histogram(self) -> None:
        from daemon.services.metrics_query.wiring.w1 import loop_lag_seconds

        assert loop_lag_seconds.kind == "gauge"
        assert loop_lag_seconds.allowed_values["stat"] == frozenset(vocab.LAG_STATS)

    def test_the_adapter_publishes_the_computed_quantiles(self) -> None:
        from daemon.services.metrics_query.wiring.w1 import (
            loop_lag_overflow_total,
            loop_lag_probe_state,
            loop_lag_samples,
            loop_lag_seconds,
        )

        probe = _FakeLagProbe(
            {"samples": 12, "max": 0.9, "p95": 0.5, "p50": 0.1, "overflow_count": 3},
            state="ok",
        )
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(wedge_watchdog=_FakeWatchdog(probe))
        )
        adapt_f1(None)
        assert loop_lag_seconds.get(stat="p50") == 0.1
        assert loop_lag_seconds.get(stat="p95") == 0.5
        assert loop_lag_seconds.get(stat="max") == 0.9
        assert loop_lag_samples.get() == 12.0
        assert loop_lag_overflow_total.get() == 3.0
        assert loop_lag_probe_state.get(state="ok") == 1.0
        assert loop_lag_probe_state.get(state="stopped") == 0.0

    def test_a_missing_probe_renders_nothing(self) -> None:
        from daemon.services.metrics_query.wiring.w1 import loop_lag_seconds

        wiring_sources.bind_sources(wiring_sources.WiringSources())
        adapt_f1(None)
        assert loop_lag_seconds.series_keys() == []


class TestF3WedgeCaptures:
    """``metrics_family_f3`` — the wedge capture counters."""

    def test_the_adapter_reads_the_durable_table(self) -> None:
        from daemon.services.metrics_query.wiring.w1 import (
            wedge_capture_total,
            wedge_capture_write_total,
        )

        conn = _FakeConnection([("sustained_cpu", 3), ("manual", 1)])
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(
                dsn="postgresql://fake",
                db_connect=lambda dsn, **kwargs: conn,
            )
        )
        adapt_f3(None)
        assert wedge_capture_total.get(reason="sustained_cpu") == 3.0
        assert wedge_capture_total.get(reason="manual") == 1.0
        assert wedge_capture_write_total.get(outcome="ok") == 4.0
        assert conn.closed is True

    def test_no_dsn_renders_nothing(self) -> None:
        from daemon.services.metrics_query.wiring.w1 import wedge_capture_total

        wiring_sources.bind_sources(wiring_sources.WiringSources())
        adapt_f3(None)
        assert wedge_capture_total.series_keys() == []


class TestF4F5Pools:
    """``metrics_family_f4_f5`` — the native / derived pool families."""

    def test_the_two_families_are_kept_apart(self) -> None:
        from daemon.services.metrics_query.wiring.w1 import (
            db_pool_checked_out,
            db_pool_native,
            db_pool_waiting_clients,
        )

        assert db_pool_native.family == "F4"
        assert db_pool_checked_out.family == "F5"
        assert db_pool_waiting_clients.family == "F5"
        assert db_pool_native.name != db_pool_checked_out.name

    def test_the_native_quantities_come_from_the_pool(self) -> None:
        from daemon.services.metrics_query.wiring.w1 import (
            db_pool_native,
            db_pool_requests_errors_total,
        )

        pool = _FakePool(
            {
                "pool_size": 5,
                "pool_max": 10,
                "pool_available": 3,
                "requests_waiting": 2,
                "requests_errors": 7,
            }
        )

        wiring_sources.bind_sources(
            wiring_sources.WiringSources(manager=_manager_with_saver_pool(pool))
        )
        adapt_f4_f5(None)

        assert db_pool_native.get(pool="checkpoint", quantity="size") == 5.0
        assert db_pool_native.get(pool="checkpoint", quantity="max") == 10.0
        assert db_pool_native.get(pool="checkpoint", quantity="available") == 3.0
        assert db_pool_requests_errors_total.get(
            pool="checkpoint", kind="unclassified"
        ) == 7.0

    def test_the_derived_checked_out_is_the_size_minus_available(self) -> None:
        from daemon.services.metrics_query.wiring.w1 import db_pool_checked_out

        pool = _FakePool(
            {
                "pool_size": 5,
                "pool_max": 10,
                "pool_available": 3,
                "requests_waiting": 2,
                "requests_errors": 0,
            }
        )

        wiring_sources.bind_sources(
            wiring_sources.WiringSources(manager=_manager_with_saver_pool(pool))
        )
        adapt_f4_f5(None)

        assert db_pool_checked_out.get(pool="checkpoint") == 2.0


class TestF6Census:
    """``metrics_family_f6`` — the PostgreSQL census."""

    def test_the_census_renders_the_state_vector(self) -> None:
        from daemon.services.metrics_query.wiring.w1 import (
            pg_backends,
            pg_census_last_success_timestamp_seconds,
            pg_idle_in_txn_oldest_seconds,
            pg_leaked_txn,
        )

        snapshot = _FakeSnapshot(
            state_counts={"active": 2, "idle": 3},
            idle_by_site={"instance_read": 1},
            idle_age={"instance_read": 12.5},
        )
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(census=_FakeCensus(snapshot))
        )
        adapt_f6(None)

        assert pg_backends.get(state="active") == 2.0
        assert pg_backends.get(state="idle") == 3.0
        # The census is COMPLETE — a state with no backend is a real zero.
        assert pg_backends.get(state="disabled") == 0.0
        assert pg_leaked_txn.get(site="instance_read") == 1.0
        assert pg_leaked_txn.get(site="agent_db_tool") == 0.0
        assert pg_idle_in_txn_oldest_seconds.get() == 12.5
        assert pg_census_last_success_timestamp_seconds.get() == 1_700_000_000.0

    def test_a_non_postgres_runtime_renders_nothing(self) -> None:
        """P4-M5 — the census family is dark without a census."""
        from daemon.services.metrics_query.wiring.w1 import pg_backends

        wiring_sources.bind_sources(wiring_sources.WiringSources(census=None))
        adapt_f6(None)
        assert pg_backends.series_keys() == []


class TestClusterOutAbsence:
    """F2 is OUT of v1 (the value ruling) — it must be ABSENT."""

    def test_f2_is_not_registered(self) -> None:
        from daemon.services.metrics_query.wiring import registered_families

        assert "F2" not in registered_families()

    def test_no_thread_cpu_metric_exists(self) -> None:
        names = {m.name for m in cm.registered_metrics()}
        assert "ensemble_thread_cpu_seconds" not in names
        assert "ensemble_thread_count" not in names
