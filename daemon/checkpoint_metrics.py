"""Minimal internal metrics collector for v2 (FR-5 AC-5.2 / T5.3).

The v2 initiative's PR5 closure gate requires exposing two metrics:

* ``message_api_checkpoint_list_total`` — a **counter** whose expected
  normal value is ``0`` per §32 source-doc. Every invocation of
  ``saver.alist(…)`` on a LIVE path increments it; post-PR3 the live
  path contains ZERO alist calls, so the counter stays at 0 in
  production. Used by the FR-2 invariant test
  (``tests/integration/test_get_instance_messages_observed_count_zero.py``)
  and by the §32 binding-intent metrics surface.

* ``message_api_saver_op_latency_seconds`` — a **histogram** of saver
  op latency, labeled by ``op`` (``aget`` / ``aput`` / ``adelete`` /
  ``alist``-migration-only). Used by the FR-5 AC-5.2 surface and as
  the basis for any future SLO. Buckets are deliberately Prometheus-
  style (10ms..10s) so the metric shape matches what a Prometheus
  scrape would expect if one is ever wired in.

Why not ``prometheus_client``? — Gap-5 / Assumption A-8 (requirements.md):
"the metrics surface is the existing daemon internal collector (no
Prometheus endpoint in v1)". v1 ships no Prometheus endpoint, no
``prometheus_client`` dependency, and the v2 plan's Q5 disposition
("SETTLED — Phase 5 T5.3") explicitly extends the existing daemon
internal collector if one exists — there is none — and otherwise
implements a minimal one. This module is that minimal surface:

* No external dependency.
* Thread-safe under ``asyncio`` (each accessor takes the lock).
* Test-pinnable: tests can reset the registry between runs and
  assert exact counts / bucket observations without HTTP scraping.
* Render-able: ``render_metrics()`` emits the Prometheus text-exposition
  format IF a `/metrics` endpoint is ever wired in (future work; not a
  v2 deliverable per Gap-5 / A-8 — but the render API is cheap and
  keeps the metric surface legitimate).

Lifecycle / C-14 compliance. Every accessor path is ``except Exception:``
or narrower — NEVER ``except BaseException:`` (CancelledError must
propagate on Python 3.13; per ``daemon/services/message_tap.py:146-220``
containment rule and requirements.md C-14). The metric surface is a
side-effect of the saver-op callsite, NOT a control-flow point; a
counter-collect failure must not abort the saver op.

Compatibility. The module exposes module-level singletons
(``checkpoint_list_total``, ``saver_op_latency_seconds``) so call
sites and tests import them by name — no global registration dance.
A ``reset_for_tests()`` function clears all metrics; tests that need
isolation call it in their own setup / fixture.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Iterable

# Prometheus-style default latency buckets (seconds). Chosen to cover
# the expected range for sa-ver ops on PG 14.22 (post-PR3 the path
# should be <50ms; the wider buckets exist for diagnostic spikes).
DEFAULT_LATENCY_BUCKETS: tuple[float, ...] = (
    0.001, 0.0025, 0.005, 0.01, 0.025, 0.05,
    0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)


class Counter:
    """A single counter — monotonic, never decreases.

    The name is the Prometheus metric name; ``help`` is the description
    that ships with the metric text-exposition. ``inc()`` adds to the
    total; ``get()`` returns the cumulative count.
    """

    __slots__ = ("_name", "_help", "_value", "_lock")

    def __init__(self, name: str, help: str = "") -> None:
        self._name = name
        self._help = help
        self._value = 0
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def help(self) -> str:
        return self._help

    def inc(self, amount: int = 1) -> None:
        if amount < 0:
            raise ValueError(
                f"Counter.inc requires a non-negative amount (got {amount}); "
                f"use a Gauge for values that can decrease."
            )
        with self._lock:
            self._value += amount

    def get(self) -> int:
        with self._lock:
            return self._value

    def reset(self) -> None:
        """Clear the counter. Test-only — NOT called in production paths."""
        with self._lock:
            self._value = 0


# ─── Histogram ────────────────────────────────────────────────────────────────


class _LabeledHistogram:
    """A thin handle pre-bound to a fixed label combo (see ``Histogram.labels``).

    Forwards ``observe(value)`` and the read accessors (``get_count``,
    ``get_sum``, ``get_bucket_counts``). The underlying series is
    looked up per call from the parent histogram — safe under resets.
    """

    __slots__ = ("_hist", "_labels")

    def __init__(self, hist: "Histogram", labels: dict[str, str]) -> None:
        self._hist = hist
        self._labels = labels

    def observe(self, value: float) -> None:
        self._hist.observe(value, **self._labels)

    def get_count(self) -> int:
        return self._hist.get_count(**self._labels)

    def get_sum(self) -> float:
        return self._hist.get_sum(**self._labels)

    def get_bucket_counts(self) -> tuple[int, ...]:
        return self._hist.get_bucket_counts(**self._labels)


class _HistogramSeries:
    """One (label-combo) bucket of observations."""

    __slots__ = ("count", "sum_seconds", "bucket_counts")

    def __init__(self, bucket_upper_bounds: tuple[float, ...]) -> None:
        # bucket_counts[i] counts observations where value <= bounds[i]
        # The implicit +Inf bucket is appended at render time.
        self.bucket_counts: list[int] = [0] * len(bucket_upper_bounds)
        self.count: int = 0
        self.sum_seconds: float = 0.0


class Histogram:
    """A labeled histogram — observations bucketed by a fixed set of upper bounds.

    ``observe(value, **labels)`` records an observation. ``get_count(**labels)``
    and ``get_sum(**labels)`` read back the cumulative count and sum for the
    given label combo; ``get_bucket_counts(**labels)`` returns the per-bucket
    counts (in the order of ``buckets``).
    """

    __slots__ = ("_name", "_help", "_label_keys", "_buckets", "_series", "_lock")

    def __init__(
        self,
        name: str,
        label_keys: tuple[str, ...],
        buckets: Iterable[float] = DEFAULT_LATENCY_BUCKETS,
        help: str = "",
    ) -> None:
        self._name = name
        self._help = help
        self._label_keys = label_keys
        # Sanitize: bucket bounds must be strictly increasing; +Inf bucket
        # is implicit and appended at render time.
        bs = sorted(set(buckets))
        for prev, cur in zip(bs, bs[1:]):
            if cur <= prev:
                raise ValueError(
                    f"Histogram buckets must be strictly increasing; got {prev} >= {cur}."
                )
        self._buckets: tuple[float, ...] = tuple(bs)
        self._series: dict[tuple[tuple[str, str], ...], _HistogramSeries] = {}
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def help(self) -> str:
        return self._help

    @property
    def label_keys(self) -> tuple[str, ...]:
        return self._label_keys

    @property
    def buckets(self) -> tuple[float, ...]:
        return self._buckets

    def _key(self, labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(labels.items()))

    def observe(self, value: float, **labels: str) -> None:
        # Validate labels BEFORE taking the lock — keeps the hot path lean
        # AND raises on programmer error (unknown label key).
        unknown = set(labels) - set(self._label_keys)
        if unknown:
            raise KeyError(
                f"Histogram {self._name!r} got unknown label keys {sorted(unknown)}; "
                f"declared label_keys={self._label_keys}."
            )
        key = self._key(labels)
        with self._lock:
            series = self._series.get(key)
            if series is None:
                series = _HistogramSeries(self._buckets)
                self._series[key] = series
            series.count += 1
            series.sum_seconds += value
            for i, ub in enumerate(self._buckets):
                if value <= ub:
                    series.bucket_counts[i] += 1

    def get_count(self, **labels: str) -> int:
        key = self._key(labels)
        with self._lock:
            series = self._series.get(key)
            return 0 if series is None else series.count

    def get_sum(self, **labels: str) -> float:
        key = self._key(labels)
        with self._lock:
            series = self._series.get(key)
            return 0.0 if series is None else series.sum_seconds

    def get_bucket_counts(self, **labels: str) -> tuple[int, ...]:
        key = self._key(labels)
        with self._lock:
            series = self._series.get(key)
            return tuple(series.bucket_counts) if series is not None else (0,) * len(self._buckets)

    def series_keys(self) -> list[tuple[tuple[str, str], ...]]:
        with self._lock:
            return list(self._series.keys())

    def reset(self) -> None:
        """Clear all series. Test-only — NOT called in production paths."""
        with self._lock:
            self._series.clear()

    def labels(self, **labels: str) -> "_LabeledHistogram":
        """Return a thin handle pre-bound to the given label combo.

        Prometheus-style ergonomics for the hot path::

            saver_op_latency_seconds.labels(op="aget").observe(0.003)

        Returns a fresh handle per call; the underlying series is
        keyed by the kwargs so the handle just forwards.
        """
        unknown = set(labels) - set(self._label_keys)
        if unknown:
            raise KeyError(
                f"Histogram {self._name!r}.labels got unknown label keys "
                f"{sorted(unknown)}; declared label_keys={self._label_keys}."
            )
        return _LabeledHistogram(self, labels)


# ─── Module-level singletons (the surface FR-5 AC-5.2 promises) ──────────────


checkpoint_list_total = Counter(
    name="message_api_checkpoint_list_total",
    help=(
        "Cumulative number of saver.alist(…) invocations observed on the "
        "live message-API path. Expected normal value: 0 (post-PR3 read flip)."
    ),
)

saver_op_latency_seconds = Histogram(
    name="message_api_saver_op_latency_seconds",
    label_keys=("op",),
    buckets=DEFAULT_LATENCY_BUCKETS,
    help=(
        "Saver op latency in seconds, labeled by op. Ops: aget, aput, "
        "adelete, alist (migration-only). Buckets are Prometheus-style."
    ),
)


# ─── Test-only reset ─────────────────────────────────────────────────────────


def reset_for_tests() -> None:
    """Reset every module-level metric. Test-only."""
    checkpoint_list_total.reset()
    saver_op_latency_seconds.reset()


# ─── Text exposition (cheap; not wired to an HTTP endpoint yet) ──────────────


def render_metrics() -> str:
    """Render the metric registry in Prometheus text-exposition format.

    Future work: wire this to a ``/metrics`` HTTP endpoint. Per Gap-5 /
    A-8, v2 does NOT add an HTTP endpoint; this helper exists so the
    metric surface is legitimate (operators can ``import`` and print)
    and so a future wiring is a one-line addition.

    Format reference: https://prometheus.io/docs/instrumenting/exposition_formats/
    """
    lines: list[str] = []
    lines.append(f"# HELP {checkpoint_list_total.name} {checkpoint_list_total.help}")
    lines.append(f"# TYPE {checkpoint_list_total.name} counter")
    lines.append(f"{checkpoint_list_total.name} {checkpoint_list_total.get()}")

    hist = saver_op_latency_seconds
    lines.append(f"# HELP {hist.name} {hist.help}")
    lines.append(f"# TYPE {hist.name} histogram")
    for key in sorted(hist.series_keys(), key=lambda k: dict(k).get("op", "")):
        labels = dict(key)
        # Re-emit le="..." for every bucket, including the implicit +Inf.
        for ub, count in zip(hist.buckets, hist.get_bucket_counts(**labels)):
            bucket_labels = {**labels, "le": _format_le(ub)}
            lines.append(f"{_format_line(hist.name + "_bucket", bucket_labels)} {count}")
        # +Inf bucket = total count.
        inf_labels = {**labels, "le": "+Inf"}
        lines.append(f"{_format_line(hist.name + "_bucket", inf_labels)} {hist.get_count(**labels)}")
        lines.append(f"{_format_line(hist.name + "_count", labels)} {hist.get_count(**labels)}")
        lines.append(f"{_format_line(hist.name + "_sum", labels)} {hist.get_sum(**labels)}")

    return "\n".join(lines) + "\n"


def _format_le(value: float) -> str:
    if value == float("inf"):
        return "+Inf"
    # Prometheus convention: bucket bounds are floats; emit as "1.0", "0.05"
    # not as "1" / "5.0e-2".
    if value >= 1:
        return f"{value:g}"
    return f"{value:g}"


def _format_line(metric_name: str, labels: dict[str, str]) -> str:
    if not labels:
        return metric_name
    parts = ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(labels.items()))
    return f"{metric_name}{{{parts}}}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
