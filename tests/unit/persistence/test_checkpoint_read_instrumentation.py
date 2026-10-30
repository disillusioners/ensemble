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
      ``render_metrics()`` renders their names;
    * the CANONICAL/ALIAS double exposition — one measurement, two names,
      the SAME value at the same instant (the metrics-collection WS-B
      re-homing moved this family under ``ensemble_checkpoint_*``
      canonical names with the historical names as deprecated aliases).
"""

from __future__ import annotations

import asyncio
import re
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


# ── the canonical/alias double exposition ────────────────────────────────────
#
# The metrics-collection WS-B catch-up merge re-homed this family INTO the
# canonical registry under ``ensemble_checkpoint_*`` names: the registry's
# frozen ``^ensemble_`` naming rule does not admit the historical
# ``checkpoint_read_*`` names as canonical.  The historical names ride as
# DEPRECATED ALIASES — a second EXPOSITION of the SAME series, never a
# second measurement (the FR-5 legacy pair's pattern, reused).
#
# The distinction is load-bearing and the three outcomes are materially
# different, so the pins must distinguish them:
#
#   * a COPY      — two independent counters, both incremented ⇒ the
#                   exposition DOUBLE-COUNTS (every read counted twice);
#   * a SILENT
#     ALIAS       — only the canonical name renders ⇒ the historical
#                   dashboards go blank with no deprecation trail;
#   * the contract — ONE measurement, TWO names, the SAME value at the
#                   same instant.
#
# Asserting only "both strings exist" cannot tell the first from the
# third, so these pins compare the VALUES numerically.

#: The 7 metrics of this family: canonical name → (historical alias, kind).
_FAMILY: tuple[tuple[str, str, str], ...] = (
    ("ensemble_checkpoint_read_ops_total", "checkpoint_read_ops_total", "counter"),
    ("ensemble_checkpoint_read_rows_total", "checkpoint_read_rows_total", "counter"),
    ("ensemble_checkpoint_read_bytes_total", "checkpoint_read_bytes_total", "counter"),
    (
        "ensemble_checkpoint_read_size_bucket_hits_total",
        "checkpoint_read_size_bucket_hits_total",
        "counter",
    ),
    (
        "ensemble_checkpoint_read_measurement_errors_total",
        "checkpoint_read_measurement_errors_total",
        "counter",
    ),
    ("ensemble_checkpoint_leak_signal_total", "checkpoint_leak_signal_total", "counter"),
    ("ensemble_checkpoint_read_size_bytes", "checkpoint_read_size_bytes", "histogram"),
)

#: A Prometheus text-exposition sample line.
_SAMPLE_RE = re.compile(r"^([A-Za-z_:][A-Za-z0-9_:]*)(\{.*\})?\s+(\S+)$")


def _exposition_samples(body: str) -> dict[str, dict[str, float]]:
    """Parse the exposition into ``{metric_name: {label_set: value}}``."""
    out: dict[str, dict[str, float]] = {}
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE_RE.match(line)
        if not match:
            continue
        name, labels, value = match.group(1), match.group(2) or "", match.group(3)
        try:
            numeric = float(value)
        except ValueError:
            continue
        out.setdefault(name, {})[labels] = numeric
    return out


class TestCanonicalAliasDoubleExposition:
    """ONE measurement, TWO names, the SAME value (never a copy)."""

    def test_each_metric_exposes_exactly_one_deprecated_alias(self):
        for canonical, alias, kind in _FAMILY:
            metric = getattr(metrics, canonical)
            assert metric.kind == kind, f"{canonical} must stay a {kind}"
            assert list(metric.alias_names) == [
                alias
            ], f"{canonical} must expose exactly the historical name {alias}"

    def test_the_historical_symbol_is_the_canonical_object(self):
        """The Python symbol names were KEPT — the deprecation is the NAME."""
        for canonical, alias, _kind in _FAMILY:
            assert getattr(metrics, alias) is getattr(metrics, canonical), (
                f"{alias} must be the SAME object as {canonical} — a copy "
                f"would double-count every read"
            )

    def test_one_increment_moves_both_expositions(self):
        """Incrementing the HISTORICAL symbol moves the canonical series.

        This is the anti-COPY assertion: with two independent counters the
        alias would stay at 0 (or the canonical would).
        """
        metrics.checkpoint_read_ops_total.inc(op="aget")
        assert metrics.ensemble_checkpoint_read_ops_total.get(op="aget") == 1

        metrics.ensemble_checkpoint_read_ops_total.inc(op="aget")
        assert metrics.checkpoint_read_ops_total.get(op="aget") == 2

    def test_the_alias_tracks_the_canonical_value_numerically(self):
        """Same label-set ⇒ SAME value in the rendered exposition."""
        metrics.checkpoint_read_ops_total.inc(op="aget")
        metrics.checkpoint_read_rows_total.inc(7, op="aget")
        metrics.checkpoint_read_bytes_total.inc(4096, op="aget")
        metrics.checkpoint_read_size_bucket_hits_total.inc(
            op="aget", bucket="65536"
        )
        metrics.checkpoint_read_measurement_errors_total.inc()
        metrics.checkpoint_leak_signal_total.inc()
        metrics.checkpoint_read_size_bytes.labels(op="aget").observe(4096)

        samples = _exposition_samples(metrics.render_metrics())
        for canonical, alias, kind in _FAMILY:
            if kind == "histogram":
                # A histogram renders three sub-names; compare all of them.
                for suffix in ("_bucket", "_count", "_sum"):
                    canonical_samples = samples.get(canonical + suffix, {})
                    alias_samples = samples.get(alias + suffix, {})
                    assert canonical_samples, f"{canonical}{suffix} must render"
                    assert alias_samples, f"{alias}{suffix} must render"
                    assert canonical_samples == alias_samples, (
                        f"{alias}{suffix} must carry the SAME values as "
                        f"{canonical}{suffix} — a copy would drift"
                    )
            else:
                canonical_samples = samples.get(canonical, {})
                alias_samples = samples.get(alias, {})
                assert canonical_samples, f"{canonical} must render"
                assert alias_samples, f"{alias} must render"
                assert canonical_samples == alias_samples, (
                    f"{alias} must carry the SAME values as {canonical} — "
                    f"a copy would drift"
                )

    def test_the_registry_holds_one_series_per_label_combination(self):
        """One measurement ⇒ ONE series, exposed under TWO names."""
        metrics.checkpoint_read_ops_total.inc(op="aget")
        metric = metrics.ensemble_checkpoint_read_ops_total
        assert len(metric.series_keys()) == 1, (
            "the canonical + alias exposition must not create a second series"
        )

    def test_the_bucket_label_key_is_registered(self):
        """The ``bucket`` label key is part of the registry's closed enum."""
        assert "bucket" in metrics.known_label_keys()
        metric = metrics.ensemble_checkpoint_read_size_bucket_hits_total
        assert set(metric.label_keys) == {"op", "bucket"}
        assert metric.allowed_values["bucket"] == metrics._READ_SIZE_BUCKET_LABELS

    def test_the_alias_histogram_renders_the_same_bucket_vocabulary(self):
        """The alias histogram's ``le=`` labels equal the canonical's."""
        metrics.checkpoint_read_size_bytes.labels(op="aget").observe(100 * 1024)
        samples = _exposition_samples(metrics.render_metrics())
        canonical_le = {
            labels
            for labels in samples.get(
                "ensemble_checkpoint_read_size_bytes_bucket", {}
            )
        }
        alias_le = {
            labels
            for labels in samples.get("checkpoint_read_size_bytes_bucket", {})
        }
        assert canonical_le
        assert canonical_le == alias_le, (
            "the alias histogram must expose the SAME bucket labels — the "
            "join-by-label surface (read_size_bucket_label) depends on it"
        )
