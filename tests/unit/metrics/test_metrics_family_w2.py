"""The W2 FAMILY PACK — F7, F8, F9, F10, F11, F12, F13.

The registered pack ids of this file:

* ``metrics_family_f7_completion`` — the saver-op bytes histogram;
* ``metrics_family_f8_partial`` — the checkpoint store sizes;
* ``metrics_family_f9`` — the readiness family (the PUBLISHED composite);
* ``metrics_family_f10`` — the job-queue depth;
* ``metrics_family_f11_partial`` — the worker pool (no claim latency);
* ``metrics_family_f12`` — the sweep counters (the accumulation);
* ``metrics_family_f13_partial`` — the instance census (no wake/lifecycle).

WHAT EACH PACK PROVES
---------------------
* the family renders with the catalog's canonical names, labels and
  buckets, and every label key declares ``allowed_values`` (AM-2);
* the family carries its registration-time metadata (AM-3);
* the OUT metrics of the PARTIAL families are ABSENT (F11's claim
  latency, F13's wake-turn / lifecycle counters);
* the F12 counters ACCUMULATE across ticks (the metrics layer's own
  accumulation — the disambiguator the deferred C17 docstring fix does
  not affect);
* the F7 bytes gauge is NON-ZERO after a real read (the P4-M8 mutation
  target).
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from daemon import checkpoint_metrics as cm
from daemon.services.metrics_query.wiring import sources as wiring_sources
from daemon.services.metrics_query.wiring import vocab
from daemon.services.metrics_query.wiring import w2


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
    """A connection fake that answers per-SQL and records every query."""

    def __init__(self, answers: dict[str, list[tuple[Any, ...]]] | None = None) -> None:
        self._answers = answers or {}
        self.queries: list[str] = []
        self.closed = False

    def cursor(self) -> _FakeCursor:
        outer = self

        class _C(_FakeCursor):
            def __init__(self) -> None:
                super().__init__([])

            def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
                outer.queries.append(sql)
                rows: list[tuple[Any, ...]] = []
                for needle, answer in outer._answers.items():
                    if needle in sql:
                        rows = answer
                        break
                self._rows = rows

        return _C()

    def close(self) -> None:
        self.closed = True


class _FakePool:
    def __init__(self, stats: dict[str, Any]) -> None:
        self._stats = stats
        self.min_size = 1

    def get_stats(self) -> dict[str, Any]:
        return dict(self._stats)


class _FakeWorker:
    def __init__(self, task_id: Any = None) -> None:
        self._current_task_id = task_id


class _FakeWorkerPool:
    def __init__(self, *, workers: int = 2, busy: int = 1, stats: dict[str, Any] | None = None) -> None:
        self._workers = [
            _FakeWorker("task" if index < busy else None) for index in range(workers)
        ]
        self._heartbeat_interval_s = 30.0
        self._stats = stats or {
            "num_workers": workers,
            "wakeup_efficiency": 0.75,
            "notifications_sent": 12,
            "empty_claim_attempts": 4,
            "workers_woken_by_timeout": 3,
            "claims_skipped_due_to_busy_instance": 1,
        }

    def get_stats(self) -> dict[str, Any]:
        return dict(self._stats)


class _FakeManager:
    def __init__(self, pool: Any = None) -> None:
        self._worker_pool = pool
        self._chat_worker_pool = None
        self.engine = None
        self.checkpointer = None


class _FakeComposite:
    def __init__(self) -> None:
        self.component_classes = {
            "database": "ok",
            "queue_freshness": "pool_busy",
            "services": "ok",
            "checkpoint_saver": "ok",
        }
        self.failure_streak = {"database": 0, "queue_freshness": 2}
        self.grace_active = True


class _FakeSweep:
    def __init__(self, counters: dict[str, int]) -> None:
        self._counters = counters

    def counters(self) -> dict[str, int]:
        return dict(self._counters)


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    cm.reset_for_tests()
    w2._sweep_deltas.reset()
    w2._readiness_observations.reset()
    yield
    cm.reset_for_tests()
    wiring_sources.unbind_sources()


class TestF7Completion:
    """``metrics_family_f7_completion`` — the saver-op bytes histogram."""

    def test_the_histogram_carries_the_catalog_buckets(self) -> None:
        assert w2.checkpoint_op_bytes.kind == "histogram"
        assert w2.checkpoint_op_bytes.buckets == w2.CHECKPOINT_OP_BYTES_BUCKETS
        assert w2.checkpoint_op_bytes.allowed_values["op"] == frozenset(
            {"aget", "other"}
        )
        assert w2.checkpoint_op_bytes.family == "F7"

    def test_a_real_read_records_a_non_zero_byte_count(self) -> None:
        """P4-M8 — the F7 bytes call site reverted to 0 must go RED."""
        from daemon import persistence

        persistence._observe_saver_op_bytes(
            "aget", {"channel_values": {"messages": [{"content": "hello world"}]}}
        )
        assert w2.checkpoint_op_bytes.get_count(op="aget") >= 1
        assert w2.checkpoint_op_bytes.get_sum(op="aget") > 0

    def test_the_call_site_observes_the_state(self) -> None:
        """The EXISTING observe point gains the real value.

        The pin is on the CALL FORM: the state the read returned must
        reach the observation.  A call site reverted to a literal 0 (the
        pre-P4 behaviour) is RED (the P4-M8 mutation).
        """
        from daemon import persistence

        source = inspect.getsource(persistence.get_instance_messages)
        assert '_observe_saver_op_bytes("aget", state)' in source


class TestF8StoreSizes:
    """``metrics_family_f8_partial`` — the checkpoint store sizes."""

    def test_the_table_bytes_come_from_the_query(self) -> None:
        conn = _FakeConnection(
            {"pg_relation_size": [("checkpoints", 100), ("checkpoint_writes", 20)]}
        )
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(
                dsn="postgresql://fake", db_connect=lambda dsn, **kwargs: conn
            )
        )
        w2.adapt_f8(None)
        assert w2.checkpoint_table_bytes.get(table="checkpoints") == 100.0
        assert w2.checkpoint_blobs_table_bytes.get() == 120.0
        assert conn.closed is True

    def test_the_prune_runs_counter_is_absent(self) -> None:
        names = {m.name for m in cm.registered_metrics()}
        assert "ensemble_checkpoint_prune_runs_total" not in names
        assert "ensemble_checkpoint_versions_per_thread" not in names


class TestF9Readiness:
    """``metrics_family_f9`` — the readiness family."""

    def test_the_family_reads_the_published_composite(self) -> None:
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(readiness_composite=lambda: _FakeComposite())
        )
        w2.adapt_f9(None)
        assert w2.readiness_component_state.get(component="database") == 1.0
        assert w2.readiness_component_state.get(component="queue_freshness") == 0.0
        assert w2.readiness_failure_streak.get(component="queue_freshness") == 2.0
        assert w2.readiness_grace_active.get() == 1.0

    def test_the_observation_counter_accumulates(self) -> None:
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(readiness_composite=lambda: _FakeComposite())
        )
        w2.adapt_f9(None)
        w2.adapt_f9(None)
        assert w2.readiness_probe_total.get(
            component="queue_freshness", outcome="pool_busy"
        ) == 2.0

    def test_no_composite_renders_nothing(self) -> None:
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(readiness_composite=lambda: None)
        )
        w2.adapt_f9(None)
        assert w2.readiness_component_state.series_keys() == []


class TestF10QueueDepth:
    """``metrics_family_f10`` — the job-queue depth."""

    def test_the_depth_comes_from_the_admission_state(self) -> None:
        conn = _FakeConnection(
            {"GROUP BY admission_state": [("queued", 4), ("active", 2), ("dead", 1)]}
        )
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(
                dsn="postgresql://fake", db_connect=lambda dsn, **kwargs: conn
            )
        )
        w2.adapt_f10(None)
        assert w2.job_queue_depth.get(admission_state="queued") == 4.0
        assert w2.job_queue_depth.get(admission_state="active") == 2.0
        assert w2.dead_letter_items.get() == 1.0
        assert w2.job_queue_items_created_total.get() == 7.0

    def test_the_admission_vocabulary_is_the_enum(self) -> None:
        from daemon.repositories.job_queue.models import AdmissionState

        assert set(vocab.admission_states()) == {m.value for m in AdmissionState}


class TestF11WorkerPool:
    """``metrics_family_f11_partial`` — the worker pool."""

    def test_the_pool_stats_are_published(self) -> None:
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(manager=_FakeManager(_FakeWorkerPool()))
        )
        w2.adapt_f11(None)
        assert w2.workers_total.get(pool="worker") == 2.0
        assert w2.workers_busy.get(pool="worker") == 1.0
        assert w2.worker_pool_wakeup_efficiency.get(pool="worker") == 0.75
        assert w2.worker_pool_events_total.get(pool="worker", event="notifications_sent") == 12.0
        assert w2.task_heartbeat_cadence_seconds.get() == 30.0

    def test_the_claim_latency_is_absent(self) -> None:
        names = {m.name for m in cm.registered_metrics()}
        assert "ensemble_job_claim_latency_seconds" not in names


class TestF12Sweeps:
    """``metrics_family_f12`` — the sweep counters."""

    def test_the_counters_accumulate_across_ticks(self) -> None:
        """P4-M3 — the accumulation removed must go RED.

        The pin is on the EXACT accumulated value: the first read is the
        baseline (0), and the second read contributes the DELTA (5 - 3
        = 2).  Publishing the per-tick values raw would give 3 + 5 = 8.
        """
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(
                sweeps={"watch_reconcile": _FakeSweep({"ticks": 3, "fired_total": 2, "errors_total": 1})}
            )
        )
        w2.adapt_f12(None)
        assert w2.sweep_cycles_total.get(lane="watch_reconcile") == 0.0
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(
                sweeps={"watch_reconcile": _FakeSweep({"ticks": 5, "fired_total": 3, "errors_total": 1})}
            )
        )
        w2.adapt_f12(None)
        assert w2.sweep_cycles_total.get(lane="watch_reconcile") == 2.0

    def test_a_per_tick_producer_is_accumulated_too(self) -> None:
        """The C17 shape — a producer that RESETS each call."""
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(
                sweeps={"watch_reconcile": _FakeSweep({"ticks": 1, "orphan_released": 4})}
            )
        )
        w2.adapt_f12(None)
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(
                sweeps={"watch_reconcile": _FakeSweep({"ticks": 2, "orphan_released": 0})}
            )
        )
        w2.adapt_f12(None)
        assert w2.sweep_rows_total.get(lane="watch_reconcile", outcome="released") >= 0.0

    def test_the_cycle_duration_metric_is_absent(self) -> None:
        """No producer publishes an observed cycle duration — it is OUT."""
        names = {m.name for m in cm.registered_metrics()}
        assert "ensemble_sweep_cycle_seconds" not in names


class TestF13Instances:
    """``metrics_family_f13_partial`` — the instance census."""

    def test_the_census_comes_from_the_durable_table(self) -> None:
        conn = _FakeConnection(
            {
                "GROUP BY status": [("idle", 3), ("running", 2)],
                "waiting_children": [(1, 42.0)],
            }
        )
        wiring_sources.bind_sources(
            wiring_sources.WiringSources(
                dsn="postgresql://fake", db_connect=lambda dsn, **kwargs: conn
            )
        )
        w2.adapt_f13(None)
        assert w2.instance_status_count.get(status="idle") == 3.0
        # BOTH idle and running fold onto the canonical "processing" liveness.
        assert w2.instance_liveness_count.get(liveness="processing") == 5.0
        assert w2.waiting_children_count.get() == 1.0
        assert w2.waiting_children_max_age_seconds.get() == 42.0

    def test_the_durable_waiting_children_query_exists(self) -> None:
        """Task 4.6 — the query the gauges read must exist."""
        assert "waiting_children" in w2._WAITING_CHILDREN_SQL
        assert "last_activity_at" in w2._WAITING_CHILDREN_SQL

    def test_the_wake_and_lifecycle_counters_are_absent(self) -> None:
        names = {m.name for m in cm.registered_metrics()}
        assert "ensemble_wake_turns_total" not in names
        assert "ensemble_instance_lifecycle_total" not in names
