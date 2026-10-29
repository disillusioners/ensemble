"""Unit tests for the checkpoint READ instrumentation (plan Track-1 item 4).

The instrumentation lives at the saver-retry-proxy chokepoint
(``daemon/checkpoint_adapter.py`` — ``_SaverRetryProxy.aget`` /
``.aget_tuple``): every SETTLED read is measured (payload bytes + the
derived row count) and recorded in ``daemon/checkpoint_metrics.py``.

THE ABSOLUTE CONTRACT: the measurement is FAIL-OPEN. A measurement
failure must NEVER break or slow-fail a read — the read result is
always returned as-is — and the failure must be OBSERVABLE (the
``checkpoint_read_measurement_errors_total`` tripwire: the measurement
is observability, so a silent measurement failure would be a silent
observability failure).

Coverage:
    * the read forwarders record bytes + rows, per op;
    * a MISS (no checkpoint) is still a read (bytes 0 / rows 0);
    * a measurement failure never breaks the read + the tripwire fires;
    * the size histogram's log2 buckets (64 KiB → 8 MiB);
    * ``reset_for_tests()`` zeroes the new metrics and
      ``render_metrics()`` renders their names.
"""

from __future__ import annotations

import asyncio
from collections import namedtuple
from typing import Any

import pytest

from daemon import checkpoint_metrics as metrics
from daemon.checkpoint_adapter import (
    _derive_read_rows,
    _wrap_saver_with_connection_retry,
)
from daemon.checkpoint_perf import log_checkpoint_read

#: The LangGraph ``CheckpointTuple`` shape (config, checkpoint,
#: metadata, parent_config, pending_writes).
CheckpointTuple = namedtuple(
    "CheckpointTuple", "config checkpoint metadata parent_config pending_writes"
)

CONFIG = {"configurable": {"thread_id": "thread-abcdef1234567890"}}


class _FakeSaver:
    """A minimal saver surface — the proxy only needs the two reads."""

    def __init__(self, checkpoint: Any, pending_writes: Any = ()) -> None:
        self._checkpoint = checkpoint
        self._pending_writes = list(pending_writes)

    async def aget(self, config: Any) -> Any:
        return self._checkpoint

    async def aget_tuple(self, config: Any) -> Any:
        return CheckpointTuple(
            {"configurable": {"thread_id": "t-1"}},
            self._checkpoint,
            {},
            None,
            self._pending_writes,
        )


def _checkpoint() -> dict:
    """A checkpoint with one blob channel, one primitive and one bool."""
    return {
        "v": 4,
        "id": "ck-1",
        "channel_values": {
            "messages": ["m" * 256],  # a blob row (not a primitive)
            "step": 7,  # inlined primitive — no blob row
            "done": True,  # inlined primitive — no blob row
        },
    }


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.reset_for_tests()
    yield
    metrics.reset_for_tests()


class TestReadForwarderMeasurement:
    """The forwarders measure every settled read."""

    def test_aget_records_bytes_and_rows(self):
        proxy = _wrap_saver_with_connection_retry(_FakeSaver(_checkpoint()))
        result = asyncio.run(proxy.aget(CONFIG))
        assert result is not None
        assert metrics.checkpoint_read_ops_total.get(op="aget") == 1
        # 1 checkpoint row + 1 blob row (the ``messages`` channel) — the
        # two primitives are inlined in the checkpoint row.
        assert metrics.checkpoint_read_rows_total.get(op="aget") == 2
        assert metrics.checkpoint_read_bytes_total.get(op="aget") > 0
        assert metrics.checkpoint_read_size_bytes.get_count(op="aget") == 1

    def test_aget_tuple_counts_pending_write_rows(self):
        proxy = _wrap_saver_with_connection_retry(
            _FakeSaver(_checkpoint(), pending_writes=[{"a": 1}, {"b": 2}])
        )
        result = asyncio.run(proxy.aget_tuple(CONFIG))
        assert result is not None
        assert metrics.checkpoint_read_ops_total.get(op="aget_tuple") == 1
        # 1 checkpoint row + 1 blob row + 2 pending-write rows.
        assert metrics.checkpoint_read_rows_total.get(op="aget_tuple") == 4
        assert metrics.checkpoint_read_bytes_total.get(op="aget_tuple") > 0

    def test_a_miss_is_still_a_read(self):
        proxy = _wrap_saver_with_connection_retry(_FakeSaver(None))
        result = asyncio.run(proxy.aget(CONFIG))
        assert result is None
        # The read happened (a DB round trip that found nothing) — it is
        # counted, with zero bytes and zero rows.
        assert metrics.checkpoint_read_ops_total.get(op="aget") == 1
        assert metrics.checkpoint_read_rows_total.get(op="aget") == 0
        assert metrics.checkpoint_read_bytes_total.get(op="aget") == 0

    def test_row_derivation_ignores_a_foreign_shape(self):
        """A payload the deriver cannot read yields 0 rows — never a raise."""
        assert _derive_read_rows("aget", object()) == 0
        assert _derive_read_rows("aget_tuple", object()) == 0
        assert _derive_read_rows("aget", None) == 0


class TestFailOpen:
    """THE contract: a measurement failure never breaks the read."""

    def test_measurement_failure_keeps_the_read_result_and_counts(
        self, monkeypatch
    ):
        def _boom(*args, **kwargs):
            raise RuntimeError("measurement exploded")

        monkeypatch.setattr(
            "daemon.checkpoint_adapter.bounded_deep_size", _boom
        )
        proxy = _wrap_saver_with_connection_retry(_FakeSaver(_checkpoint()))
        result = asyncio.run(proxy.aget(CONFIG))
        # The read result is returned AS-IS.
        assert result is not None
        assert result["id"] == "ck-1"
        # The failure is observable (the fail-open tripwire).
        assert metrics.checkpoint_read_measurement_errors_total.get() == 1
        # …and the read metrics were NOT recorded (the measurement died
        # before it could record anything).
        assert metrics.checkpoint_read_ops_total.get(op="aget") == 0

    def test_metric_failure_also_fails_open(self, monkeypatch):
        """A broken metric registry must not break the read either."""

        def _boom(*args, **kwargs):
            raise RuntimeError("metric registry exploded")

        monkeypatch.setattr(
            "daemon.checkpoint_adapter.log_checkpoint_read", _boom
        )
        proxy = _wrap_saver_with_connection_retry(_FakeSaver(_checkpoint()))
        result = asyncio.run(proxy.aget(CONFIG))
        assert result is not None
        assert result["id"] == "ck-1"
        assert metrics.checkpoint_read_measurement_errors_total.get() == 1


class TestSizeHistogram:
    """The size histogram uses log2 buckets from 64 KiB to 8 MiB."""

    def test_bucket_bounds(self):
        assert metrics.READ_SIZE_BUCKETS == (
            65536.0,
            131072.0,
            262144.0,
            524288.0,
            1048576.0,
            2097152.0,
            4194304.0,
            8388608.0,
        )

    def test_bucket_label_matches_the_histogram_rendering(self):
        assert metrics.read_size_bucket_label(1024) == "65536"
        assert metrics.read_size_bucket_label(65536) == "65536"
        assert metrics.read_size_bucket_label(65537) == "131072"
        assert metrics.read_size_bucket_label(20 * 1024 * 1024) == "+Inf"

    def test_observation_lands_in_the_right_bucket(self):
        log_checkpoint_read("aget", "t-1", bytes_=100 * 1024, rows=3)
        counts = metrics.checkpoint_read_size_bytes.get_bucket_counts(op="aget")
        # 100 KiB ⇒ the 131072 bucket (and every larger one).
        assert counts[0] == 0
        assert counts[1] == 1
        assert metrics.checkpoint_read_size_bytes.get_count(op="aget") == 1
        assert metrics.checkpoint_read_size_bytes.get_sum(op="aget") == 100 * 1024
        # The per-observation bucket attribution counter mirrors it.
        assert (
            metrics.checkpoint_read_size_bucket_hits_total.get(
                op="aget", bucket="131072"
            )
            == 1
        )


class TestKillSwitch:
    """``CHECKPOINT_PERF_LOGS=0`` turns the WHOLE read recording off (M-2).

    The read counters are a DIAGNOSTIC surface: with the kill-switch
    off, neither the log line NOR the metric series is recorded, and
    the hot path does not pay the O(payload) walk at all (when the
    resource-charge seam is off too).
    """

    def test_kill_switch_suppresses_every_metric_record(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_PERF_LOGS", "0")
        log_checkpoint_read("aget", "t-1", bytes_=1234, rows=2)
        assert metrics.checkpoint_read_ops_total.get(op="aget") == 0
        assert metrics.checkpoint_read_bytes_total.get(op="aget") == 0
        assert metrics.checkpoint_read_rows_total.get(op="aget") == 0
        assert metrics.checkpoint_read_size_bytes.get_count(op="aget") == 0
        assert (
            metrics.checkpoint_read_size_bucket_hits_total.get(
                op="aget", bucket="65536"
            )
            == 0
        )

    def test_kill_switch_on_keeps_recording(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_PERF_LOGS", "1")
        log_checkpoint_read("aget", "t-1", bytes_=1234, rows=2)
        assert metrics.checkpoint_read_ops_total.get(op="aget") == 1
        assert metrics.checkpoint_read_bytes_total.get(op="aget") == 1234

    def test_kill_switch_off_skips_the_payload_walk(self, monkeypatch):
        """With the kill-switch off and the charge seam off, NOTHING needs
        the payload size ⇒ the O(payload) walk is skipped entirely."""
        calls: list = []

        def _counting_walk(obj, **kwargs):
            calls.append(obj)
            return 1, 1, False

        monkeypatch.setenv("CHECKPOINT_PERF_LOGS", "0")
        monkeypatch.setattr(
            "daemon.checkpoint_adapter._charge_enabled", lambda: False
        )
        monkeypatch.setattr(
            "daemon.checkpoint_adapter.bounded_deep_size", _counting_walk
        )
        proxy = _wrap_saver_with_connection_retry(_FakeSaver(_checkpoint()))
        result = asyncio.run(proxy.aget(CONFIG))
        assert result is not None
        assert calls == [], (
            "the payload must NOT be walked when no consumer needs the "
            "size (the kill-switch is off and the charge seam is off)"
        )
        assert metrics.checkpoint_read_ops_total.get(op="aget") == 0

    def test_charge_seam_is_independent_of_the_kill_switch(self, monkeypatch):
        """The CHARGE seam keeps working while the perf kill-switch is off
        — the resource budget is not a diagnostic surface. The size is
        therefore still measured (ONCE) and handed to the charge."""
        calls: list = []

        def _counting_walk(obj, **kwargs):
            calls.append(obj)
            return 1234, 1, False

        monkeypatch.setenv("CHECKPOINT_PERF_LOGS", "0")
        monkeypatch.setattr(
            "daemon.checkpoint_adapter._charge_enabled", lambda: True
        )
        monkeypatch.setattr(
            "daemon.checkpoint_adapter.bounded_deep_size", _counting_walk
        )
        proxy = _wrap_saver_with_connection_retry(_FakeSaver(_checkpoint()))
        result = asyncio.run(proxy.aget(CONFIG))
        assert result is not None
        # Exactly ONE walk (M-2 — the payload is never walked twice).
        assert len(calls) == 1
        # …and the instrumentation recorded NOTHING (the kill-switch).
        assert metrics.checkpoint_read_ops_total.get(op="aget") == 0


class TestCounterWiring:
    """``reset_for_tests()`` + ``render_metrics()`` cover the new surface."""

    def test_reset_for_tests_zeroes_the_new_metrics(self):
        log_checkpoint_read("aget", "t-1", bytes_=1234, rows=2)
        metrics.checkpoint_read_measurement_errors_total.inc()
        assert metrics.checkpoint_read_ops_total.get(op="aget") == 1
        metrics.reset_for_tests()
        assert metrics.checkpoint_read_ops_total.get(op="aget") == 0
        assert metrics.checkpoint_read_rows_total.get(op="aget") == 0
        assert metrics.checkpoint_read_bytes_total.get(op="aget") == 0
        assert (
            metrics.checkpoint_read_size_bucket_hits_total.get(
                op="aget", bucket="65536"
            )
            == 0
        )
        assert metrics.checkpoint_read_measurement_errors_total.get() == 0
        assert metrics.checkpoint_read_size_bytes.get_count(op="aget") == 0

    def test_render_metrics_includes_the_new_names(self):
        rendered = metrics.render_metrics()
        for name in (
            "checkpoint_read_ops_total",
            "checkpoint_read_rows_total",
            "checkpoint_read_bytes_total",
            "checkpoint_read_size_bucket_hits_total",
            "checkpoint_read_measurement_errors_total",
            "checkpoint_read_size_bytes",
        ):
            assert name in rendered, f"{name} must render"

    def test_render_metrics_includes_the_observed_samples(self):
        log_checkpoint_read("aget", "t-1", bytes_=1234, rows=2)
        rendered = metrics.render_metrics()
        assert 'checkpoint_read_ops_total{op="aget"} 1' in rendered
        assert 'checkpoint_read_rows_total{op="aget"} 2' in rendered
        assert 'checkpoint_read_bytes_total{op="aget"} 1234' in rendered
        assert 'checkpoint_read_size_bytes_count{op="aget"} 1' in rendered
