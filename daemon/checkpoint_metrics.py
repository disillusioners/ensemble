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
  format. It is served over HTTP by ``GET /api/metrics``
  (``daemon/routers/metrics.py`` — a minimal single-endpoint wiring;
  there is NO Prometheus client dependency and NO scrape framework).

Read instrumentation (plan Track-1 item 4). The registry also carries
the checkpoint READ surface: ``checkpoint_read_ops_total`` /
``checkpoint_read_rows_total`` / ``checkpoint_read_bytes_total`` /
``checkpoint_read_size_bucket_hits_total`` (labeled by op) and the
``checkpoint_read_size_bytes`` histogram (log2 buckets, 64 KiB → 8 MiB),
plus ``checkpoint_read_measurement_errors_total`` — the FAIL-OPEN
tripwire: the measurement never breaks a read, so that counter is the
only signal that the instrumentation itself broke. The measurement seam
lives in ``daemon/checkpoint_adapter.py`` (the saver retry proxy).
Item 6 adds the leak-signal CONSUMER counter
``checkpoint_leak_signal_total`` (plan Track-1 item 6): the readiness
leak predicate had an emission but no consumer — this is the alertable
tally, consumed by ``daemon/services/readiness.py`` (fail-open).

Lifecycle / C-14 compliance. Every accessor path is ``except Exception:``
or narrower — NEVER ``except BaseException:`` (CancelledError must
propagate on Python 3.13; per ``daemon/services/message_tap.py:146-220``
containment rule and requirements.md C-14). The metric surface is a
side-effect of the saver-op callsite, NOT a control-flow point; a
counter-collect failure must not abort the saver op.

Compatibility. The module exposes module-level singletons
(``checkpoint_list_total``, ``saver_op_latency_seconds`` and the
checkpoint-read surface below) so call sites and tests import them by
name — no global registration dance.
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


# ─── Labeled counter ─────────────────────────────────────────────────────────


class LabeledCounter:
    """A labeled counter — monotonic per label combo, never decreases.

    Same shape as :class:`Histogram` (same lock discipline, same
    ``**labels`` accessor style) for the plain "count of events" case:
    the read-instrumentation surface needs "how many reads", "how many
    rows", "how many bytes" — each broken down by the op label
    (``aget`` / ``aget_tuple``). The unlabeled :class:`Counter` cannot
    express that split (it carries one value), and a histogram would
    be a misuse of the bucket machinery for a plain tally.

    Label validation happens BEFORE the lock is taken (same discipline
    as :meth:`Histogram.observe`): an unknown label key is a
    programmer error and raises ``KeyError`` rather than silently
    creating a second series.
    """

    __slots__ = ("_name", "_help", "_label_keys", "_values", "_lock")

    def __init__(
        self,
        name: str,
        label_keys: tuple[str, ...],
        help: str = "",
    ) -> None:
        self._name = name
        self._help = help
        self._label_keys = label_keys
        self._values: dict[tuple[tuple[str, str], ...], int] = {}
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

    def _key(self, labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(labels.items()))

    def _validate(self, labels: dict[str, str]) -> None:
        unknown = set(labels) - set(self._label_keys)
        if unknown:
            raise KeyError(
                f"Counter {self._name!r} got unknown label keys "
                f"{sorted(unknown)}; declared label_keys={self._label_keys}."
            )

    def inc(self, amount: int = 1, **labels: str) -> None:
        """Add ``amount`` to the series for ``labels``."""
        if amount < 0:
            raise ValueError(
                f"LabeledCounter.inc requires a non-negative amount "
                f"(got {amount}); use a Gauge for values that can decrease."
            )
        self._validate(labels)
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0) + amount

    def get(self, **labels: str) -> int:
        """The cumulative count for ``labels`` (0 when never observed)."""
        self._validate(labels)
        key = self._key(labels)
        with self._lock:
            return self._values.get(key, 0)

    def series_keys(self) -> list[tuple[tuple[str, str], ...]]:
        with self._lock:
            return list(self._values.keys())

    def reset(self) -> None:
        """Clear every series. Test-only — NOT called in production paths."""
        with self._lock:
            self._values.clear()


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


# ─── Checkpoint READ instrumentation (plan Track-1 item 4) ───────────────────
#
# The read-amplification audit's one adopted fix: instrument the read
# path at the PROXY chokepoint (``daemon/checkpoint_adapter.py`` —
# ``_SaverRetryProxy.aget`` / ``.aget_tuple``) so the "bytes per read"
# and "rows per read" question has a MEASURED answer instead of an
# estimate from a synthetic bench.
#
# WHAT IS MEASURED (and what is NOT):
#   * bytes — the SIZE of the decoded payload the read returned
#     (a bounded deep-size walk, the same estimator the resource-monitor
#     charge seam uses). It is the DECODED size, not the wire size: the
#     serializer's framing overhead is not included.
#   * rows — the number of rows the read RETURNED, derived from the
#     returned structure (1 checkpoint row + one blob row per non-primitive
#     channel value + one row per pending write). It is a DERIVED count,
#     not the server's row counter.
#
# The size histogram uses log2 buckets from 64 KiB to 8 MiB — the range
# the audit measured the payload amplification in (the "2 MB blob"
# read). Observations outside the range land in the first / ``+Inf``
# bucket.

#: Log2 size buckets (bytes) for the checkpoint read size histogram —
#: 64 KiB → 8 MiB. Prometheus-style upper bounds (cumulative).
READ_SIZE_BUCKETS: tuple[float, ...] = (
    65536.0, 131072.0, 262144.0, 524288.0,
    1048576.0, 2097152.0, 4194304.0, 8388608.0,
)

checkpoint_read_ops_total = LabeledCounter(
    name="checkpoint_read_ops_total",
    label_keys=("op",),
    help=(
        "Checkpoint read operations observed at the saver retry proxy, "
        "labeled by op (aget, aget_tuple). One increment per settled read."
    ),
)

checkpoint_read_rows_total = LabeledCounter(
    name="checkpoint_read_rows_total",
    label_keys=("op",),
    help=(
        "Rows returned by checkpoint reads at the saver retry proxy, "
        "labeled by op. DERIVED count: 1 checkpoint row + one blob row "
        "per non-primitive channel value + one row per pending write."
    ),
)

checkpoint_read_bytes_total = LabeledCounter(
    name="checkpoint_read_bytes_total",
    label_keys=("op",),
    help=(
        "Decoded payload bytes returned by checkpoint reads at the saver "
        "retry proxy, labeled by op. Bounded deep-size estimate (the "
        "resource-monitor charge estimator), not the wire size."
    ),
)

checkpoint_read_size_bucket_hits_total = LabeledCounter(
    name="checkpoint_read_size_bucket_hits_total",
    label_keys=("op", "bucket"),
    help=(
        "Per-observation bucket attribution for checkpoint read sizes, "
        "labeled by op and bucket (the bucket's upper bound in bytes, or "
        "+Inf). NOT cumulative — one increment per observation, in the "
        "bucket the observation fell into."
    ),
)

checkpoint_read_measurement_errors_total = Counter(
    name="checkpoint_read_measurement_errors_total",
    help=(
        "Read-instrumentation failures at the saver retry proxy. The "
        "measurement is FAIL-OPEN (a failure never breaks the read), so "
        "this counter is the ONLY signal that the instrumentation itself "
        "stopped working."
    ),
)

#: Leak-signal CONSUMER counter (plan Track-1 item 6). The readiness
#: leak predicate (``GraceTracker._evaluate_leak`` →
#: ``GraceObservation.leak_signal`` → ``/readyz`` ``detail``) has
#: published its verdict for every tick since the grace commission,
#: but nothing alerted on it — the 2026-10-28 idle-in-transaction
#: incident produced leak signals nobody saw. This is the alertable
#: tally: ONE increment per readiness tick the predicate reports
#: ``detected``. Plain (unlabeled) by design: the reason tokens embed
#: tick counts ("(3 ticks)"), so labeling by reason would explode
#: series cardinality; the WARN log line carries the reason detail.
checkpoint_leak_signal_total = Counter(
    name="checkpoint_leak_signal_total",
    help=(
        "Leak-signal fires consumed from the readiness leak predicate. "
        "One increment per readiness tick the predicate reports "
        "detected=true. The consumer is FAIL-OPEN: it never affects the "
        "emitter (the grace verdict and the /readyz detail)."
    ),
)

checkpoint_read_size_bytes = Histogram(
    name="checkpoint_read_size_bytes",
    label_keys=("op",),
    buckets=READ_SIZE_BUCKETS,
    help=(
        "Checkpoint read payload size in bytes, labeled by op "
        "(aget, aget_tuple). Log2 buckets from 64 KiB to 8 MiB."
    ),
)

#: Every labeled counter in the registry — the render path iterates
#: this tuple so a new labeled counter cannot be added without being
#: rendered (the same "no silent orphan" discipline the reset path
#: follows).
_LABELED_COUNTERS: tuple[LabeledCounter, ...] = (
    checkpoint_read_ops_total,
    checkpoint_read_rows_total,
    checkpoint_read_bytes_total,
    checkpoint_read_size_bucket_hits_total,
)


def read_size_bucket_label(value: float) -> str:
    """The bucket label (``le``-style) a size observation falls into.

    Returns the upper bound of the FIRST bucket that contains the
    value, or ``"+Inf"`` when the value is above the largest bucket.
    The label is the SAME string the histogram renders for its
    ``_bucket`` lines, so the two surfaces can be joined by label.
    """
    for ub in READ_SIZE_BUCKETS:
        if value <= ub:
            return _format_le(ub)
    return "+Inf"


# ─── Test-only reset ─────────────────────────────────────────────────────────


def reset_for_tests() -> None:
    """Reset every module-level metric. Test-only."""
    checkpoint_list_total.reset()
    saver_op_latency_seconds.reset()
    checkpoint_read_ops_total.reset()
    checkpoint_read_rows_total.reset()
    checkpoint_read_bytes_total.reset()
    checkpoint_read_size_bucket_hits_total.reset()
    checkpoint_read_measurement_errors_total.reset()
    checkpoint_leak_signal_total.reset()
    checkpoint_read_size_bytes.reset()


# ─── Text exposition (cheap; not wired to an HTTP endpoint yet) ──────────────


def render_metrics() -> str:
    """Render the metric registry in Prometheus text-exposition format.

    Served over HTTP by ``GET /api/metrics``
    (``daemon/routers/metrics.py``) — the minimal wiring: one endpoint
    calling this function, no scrape framework, no Prometheus client.

    Format reference: https://prometheus.io/docs/instrumenting/exposition_formats/
    """
    lines: list[str] = []
    lines.append(f"# HELP {checkpoint_list_total.name} {checkpoint_list_total.help}")
    lines.append(f"# TYPE {checkpoint_list_total.name} counter")
    lines.append(f"{checkpoint_list_total.name} {checkpoint_list_total.get()}")

    # The unlabeled read-instrumentation tripwire — always rendered
    # (value 0 included) so a scraper can see "the instrumentation
    # never failed" without a series existing.
    err = checkpoint_read_measurement_errors_total
    lines.append(f"# HELP {err.name} {err.help}")
    lines.append(f"# TYPE {err.name} counter")
    lines.append(f"{err.name} {err.get()}")

    # The leak-signal consumer counter — also ALWAYS rendered (value 0
    # included): a healthy daemon shows `checkpoint_leak_signal_total 0`
    # and any non-zero value is the alertable tally of leak-signal
    # fires (plan Track-1 item 6).
    leak = checkpoint_leak_signal_total
    lines.append(f"# HELP {leak.name} {leak.help}")
    lines.append(f"# TYPE {leak.name} counter")
    lines.append(f"{leak.name} {leak.get()}")

    # Labeled counters — the read-instrumentation surface. The HELP /
    # TYPE header is ALWAYS emitted (the metric exists even before its
    # first observation); the sample lines are emitted per OBSERVED
    # label series (Prometheus semantics: an unobserved series has no
    # value yet).
    for counter in _LABELED_COUNTERS:
        lines.append(f"# HELP {counter.name} {counter.help}")
        lines.append(f"# TYPE {counter.name} counter")
        for key in sorted(counter.series_keys()):
            labels = dict(key)
            lines.append(
                f"{_format_line(counter.name, labels)} {counter.get(**labels)}"
            )

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

    # The checkpoint READ size histogram (plan Track-1 item 4) — same
    # shape as the latency histogram above (cumulative ``_bucket``
    # lines + ``_count`` + ``_sum``).
    read_hist = checkpoint_read_size_bytes
    lines.append(f"# HELP {read_hist.name} {read_hist.help}")
    lines.append(f"# TYPE {read_hist.name} histogram")
    for key in sorted(read_hist.series_keys(), key=lambda k: dict(k).get("op", "")):
        labels = dict(key)
        for ub, count in zip(read_hist.buckets, read_hist.get_bucket_counts(**labels)):
            bucket_labels = {**labels, "le": _format_le(ub)}
            lines.append(
                f"{_format_line(read_hist.name + "_bucket", bucket_labels)} {count}"
            )
        inf_labels = {**labels, "le": "+Inf"}
        lines.append(
            f"{_format_line(read_hist.name + "_bucket", inf_labels)} "
            f"{read_hist.get_count(**labels)}"
        )
        lines.append(
            f"{_format_line(read_hist.name + "_count", labels)} "
            f"{read_hist.get_count(**labels)}"
        )
        lines.append(
            f"{_format_line(read_hist.name + "_sum", labels)} "
            f"{read_hist.get_sum(**labels)}"
        )

    return "\n".join(lines) + "\n"


def _format_le(value: float) -> str:
    if value == float("inf"):
        return "+Inf"
    # Prometheus convention: bucket bounds are floats; emit as "1.0", "0.05"
    # not as "1" / "5.0e-2".
    #
    # INTEGRAL bounds render as plain integers ("65536", "1048576") —
    # the byte buckets of the checkpoint-read size histogram would
    # otherwise render in scientific notation ("1.04858e+06"), which is
    # unreadable for an operator and inconsistent with the byte values
    # the metric reports. The latency histogram's integral bounds
    # (1.0 / 5.0 / 10.0) are UNCHANGED by this branch — ``%g`` already
    # rendered them as "1" / "5" / "10".
    if value.is_integer():
        return str(int(value))
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
