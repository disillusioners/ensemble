"""Canonical metrics registry for v2 (metrics-collection P0, T4/T6).

This module started as the minimal internal collector that exposed two
legacy metrics (``message_api_checkpoint_list_total`` and
``message_api_saver_op_latency_seconds``).  The v2 metrics-collection
plan generalises it into **the** daemon-side registry: every metric
family in the catalog is registered here and rendered by
:func:`render_metrics` in Prometheus **classic** text exposition format.

Design constraints (plan: ``metric-catalog.md`` §0.2/§0.5, ``§8``;
``architecture-recommendation.md`` §1.3/§2.1-§2.4):

* **No dependency.** stdlib only — no ``prometheus_client``, no OTel, no
  new PyInstaller surface.
* **Per-metric locks only.** There is deliberately NO registry-level
  lock: one wedged writer must not stall every family.
* **Render never blocks.** The renderer uses a non-blocking
  try-acquire (bounded wait, 50 ms per family, 250 ms for the whole
  render).  When the lock is acquired the render serves the CURRENT
  state (the lock makes it a consistent snapshot); on contention it
  serves the last published snapshot and emits
  ``# <family> unavailable: lock_contended``.
* **Copy-on-publish.** Writers update their own family's state under
  their own short lock and flip a dirty flag (O(1)); the O(series)
  snapshot copy happens at most once per publisher cadence window
  (``set_publisher_cadence_provider``), never per increment.  The
  published pointer is the contention fallback, so its staleness is
  bounded by the cadence window (≤ N seconds).
* **Non-cumulative bucket storage.** ``Histogram.observe`` touches a
  single bucket (O(log B) via ``bisect``) instead of scanning every
  bucket (O(B)); the cumulative series is accumulated at render time.
  The rendered output for a given set of observations is identical to
  the pre-generalisation implementation.
* **Three-tier never-exported enforcement** (catalog §0.5):
  1. registration-time enum pin — a wrong label *key* raises;
  2. observe-time reserved bucket — a wrong label *value* folds to
     ``other`` and is counted in
     ``ensemble_metrics_invalid_label_total{metric,label}``;
  3. render-time allowlist predicate — per-family label-name
     allowlist + per-label value membership (defence in depth).
* **Hard cardinality cap** — an observation that would exceed the cap
  is *rejected*, not warned.  The check-then-increment happens inside
  the metric's own lock (no TOCTOU) and the rejection is counted in
  ``ensemble_metrics_cap_rejected_total{metric}`` within the same
  critical section.

Alias mechanics (catalog §0.3, "one measurement, one name"): the two
legacy metric names are re-registered under canonical ``ensemble_*``
names and ride along as **one-release deprecated aliases** — a second
exposition of the SAME series, never a second measurement.  The alias
is registered at registry-construction time (cold start still emits
it) and logs one deprecation line per process at first emission.

Legacy module-level singletons (``checkpoint_list_total``,
``saver_op_latency_seconds``) are preserved because
``daemon/checkpoint_perf.py`` imports them — the collectors stay wired.
"""

from __future__ import annotations

import bisect
import logging
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional

logger = logging.getLogger("daemon.checkpoint_metrics")

# ─── Canonical name rules (catalog §0.5) ────────────────────────────────────

_NAME_RE = re.compile(r"^ensemble_[a-z0-9_]+$")

#: The legacy names are *alias-only*: they violate the canonical name
#: rule on purpose and are never registered as canonical metrics.
LEGACY_ALIAS_NAMES: frozenset[str] = frozenset(
    {"message_api_checkpoint_list_total", "message_api_saver_op_latency_seconds"}
)

#: Characters that may never appear in a label value (they would break
#: the text exposition format).
_ILLEGAL_LABEL_CHARS: tuple[str, ...] = ('=', ',', '"', "\n")

#: The reserved residual a wrong label value folds to.
RESERVED_LABEL_VALUE = "other"


# ─── Publisher cadence / cardinality cap providers (R-m5, R-m3) ──────────────

DEFAULT_PUBLISHER_CADENCE_SECONDS = 15.0
DEFAULT_CARDINALITY_CAP = 2000

#: The per-family try-acquire budget of a render (milliseconds) and the
#: render-wide budget (seconds).  The per-family budget bounds one
#: contended family; the render-wide budget bounds the WHOLE scrape so
#: many contended families can never stretch it (RK-5: the render never
#: blocks).
RENDER_TRY_ACQUIRE_SECONDS = 0.05
RENDER_TOTAL_BUDGET_SECONDS = 0.25

_publisher_cadence_provider: Callable[[], float] = lambda: DEFAULT_PUBLISHER_CADENCE_SECONDS
_cardinality_cap_provider: Callable[[], int] = lambda: DEFAULT_CARDINALITY_CAP


def set_publisher_cadence_provider(fn: Callable[[], float]) -> None:
    """Install the publisher-cadence provider (seconds between publishes).

    The parallel P0 coder wires the managed key
    ``limits.metrics_scrape_interval_hint_s`` here at boot.  The default
    provider returns :data:`DEFAULT_PUBLISHER_CADENCE_SECONDS` (15.0).
    """
    global _publisher_cadence_provider
    _publisher_cadence_provider = fn


def set_cardinality_cap_provider(fn: Callable[[], int]) -> None:
    """Install the hard cardinality-cap provider (max series per metric)."""
    global _cardinality_cap_provider
    _cardinality_cap_provider = fn


def _publisher_cadence() -> float:
    try:
        value = float(_publisher_cadence_provider())
    except Exception:  # pragma: no cover - provider misbehaviour must not break metrics
        return DEFAULT_PUBLISHER_CADENCE_SECONDS
    return value if value >= 0 else DEFAULT_PUBLISHER_CADENCE_SECONDS


def _cardinality_cap() -> int:
    try:
        value = int(_cardinality_cap_provider())
    except Exception:  # pragma: no cover
        return DEFAULT_CARDINALITY_CAP
    return value if value >= 0 else DEFAULT_CARDINALITY_CAP


# ─── Vocabulary version marker (F0.4) ───────────────────────────────────────

#: The frozen vocabulary version.  Any change to a frozen vocabulary set
#: bumps this integer (the F0.4/A24 bump contract).
VOCABULARY_VERSION: int = 1


# ─── Vocabulary freeze (T6, catalog §8 rule A1) ─────────────────────────────
#
# Every closed vocabulary is DERIVED FROM CODE, never hand-listed, and is
# pinned to its runtime source.  Each set below carries the exact source
# it was frozen from; ``tests/unit/persistence/test_checkpoint_metrics_vocabulary.py``
# re-derives every set from that source and fails when the frozen
# constant has drifted (the A1 cross-check).
#
# The constants are literals (not import-time derivations) for two
# reasons: (a) this module is a leaf imported by ``daemon.persistence``,
# so importing the repositories/services layer here would create an
# import cycle, and (b) a frozen set must be a stable value, not a
# value that changes with a code edit.  The cross-check test is what
# keeps them honest.

#: Instance status (10) — frozen from
#: ``daemon/repositories/instance/models.py`` → ``InstanceStatus``
#: (the enum's ``.value`` set).  No ``other`` residual: the enum is
#: exhaustive for the instance status axis.
INSTANCE_STATUS_VOCABULARY: frozenset[str] = frozenset(
    {
        "idle",
        "running",
        "waiting",
        "paused",
        "completed",
        "error",
        "terminated",
        "queued",
        "waiting_children",
        "failed",
    }
)

#: Sweep lane (13) — frozen from TWO code sources (a count that took
#: only the RDRS lanes would drop the standalone sweeps' series):
#:
#: 1. the seven RDRS lanes of
#:    ``daemon/services/report_delivery_recovery.py``, named by the
#:    ``report_delivery_recovery_lane_*`` kill-switch fields of
#:    ``daemon/config.py`` (``lane_<x>`` → ``rdrs_<x>``);
#: 2. the six standalone sweep services that own a
#:    ``DEFAULT_*_INTERVAL_SECONDS`` constant in ``daemon/services/``
#:    (``<name>_sweep.py`` → ``<name>``): watch_reconcile,
#:    eligible_pending, stale_pause, orphan_watcher, job_lock,
#:    upgrade_journal.
SWEEP_LANE_VOCABULARY: tuple[str, ...] = (
    # RDRS lanes (7)
    "rdrs_deferred",
    "rdrs_no_row_backstop",
    "rdrs_pending_age",
    "rdrs_recovery_retry",
    "rdrs_orphan",
    "rdrs_stuck_wake",
    "rdrs_mission_reconciliation",
    # standalone sweeps (6)
    "watch_reconcile",
    "eligible_pending",
    "stale_pause",
    "orphan_watcher",
    "job_lock",
    "upgrade_journal",
)
SWEEP_LANES = frozenset(SWEEP_LANE_VOCABULARY)

#: MCP roster (5) — frozen from the built-in MCP server registry
#: (``daemon/mcp/builtin_servers/__init__.py`` → the registered
#: definitions' ``name``: context7, plane, webfetch) plus the
#: user-created bucket (``custom``) and the reserved overflow bucket
#: (``other``).  User-created servers are NEVER named individually.
MCP_ROSTER_VOCABULARY: tuple[str, ...] = (
    "context7",
    "plane",
    "webfetch",
    "custom",
    "other",
)
MCP_ROSTER = frozenset(MCP_ROSTER_VOCABULARY)

#: Version-label policy (catalog open item OQ-3): the version label is
#: carried ONLY by the current-release gauge
#: (``ensemble_metrics_vocabulary_version``), never by any other series.
VERSION_LABEL_POLICY = "current-release gauge only"

# ─── Tool-category vocabulary (V-4 generated artifact) ─────────────────────
#
# The tool-category vocabulary is process-dependent and therefore NOT
# hand-listed: it is generated by ``tests/unit/tools/test_tool_category_artifact.py``
# into the committed artifact below, and the same test re-runs the
# generator and FAILS when the on-disk artifact drifts (the drift
# check).  The artifact is canonical; the prose number is not.

TOOL_CATEGORY_ARTIFACT_RELPATH = ".agents/shared/metrics/tool-categories.json"

#: The reserved overflow bucket — any category outside the derived set
#: folds here (catalog §8.6), so the cardinality is bounded by
#: (derived count + 1).
TOOL_CATEGORY_RESERVED = RESERVED_LABEL_VALUE


def _repo_root() -> "Path":
    """The repository root (the directory that owns ``daemon/``)."""
    from pathlib import Path

    return Path(__file__).resolve().parents[1]


def tool_category_artifact_path() -> "Path":
    """Absolute path of the committed tool-category artifact."""
    from pathlib import Path

    return _repo_root() / TOOL_CATEGORY_ARTIFACT_RELPATH


_TOOL_CATEGORY_CACHE: Optional[frozenset[str]] = None


def tool_category_vocabulary() -> frozenset[str]:
    """The frozen tool-category set (derived set + the reserved bucket).

    Reads the committed V-4 artifact.  When the artifact is absent the
    set is derived from the live tool registry; when that is
    unavailable too, the set is empty so every category folds to the
    reserved ``other`` bucket — the safe degradation (bounded
    cardinality, no drift).
    """
    global _TOOL_CATEGORY_CACHE
    if _TOOL_CATEGORY_CACHE is not None:
        return _TOOL_CATEGORY_CACHE
    categories: set[str] = set()
    try:
        import json

        path = tool_category_artifact_path()
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            for value in payload.get("categories", []):
                categories.add(str(value))
    except Exception:  # pragma: no cover - the artifact is optional at runtime
        categories = set()
    if not categories:
        # Degraded fallback: derive the category keys from the live tool
        # registry (lazy import — the registry imports this module
        # indirectly through daemon.persistence).
        categories = _derive_runtime_category_keys()
    _TOOL_CATEGORY_CACHE = frozenset(categories | {TOOL_CATEGORY_RESERVED})
    return _TOOL_CATEGORY_CACHE


def _derive_runtime_category_keys() -> set[str]:
    """The category keys the live tool registry knows (best effort)."""
    try:
        from daemon.tools import _tool_registry as registry

        keys = {str(key) for key in registry.CATEGORY_MODULES.keys()}
        for meta in registry._tool_metadata.values():
            category = meta.get("category")
            if category:
                keys.add(str(category))
        return keys
    except Exception:  # pragma: no cover - never break metrics on a registry error
        return set()


# ─── Bucket sets ────────────────────────────────────────────────────────────

#: Legacy default latency buckets (kept for compatibility).
DEFAULT_LATENCY_BUCKETS: tuple[float, ...] = (
    0.001, 0.0025, 0.005, 0.01, 0.025, 0.05,
    0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)

#: Checkpoint op latency buckets (catalog §8.11) — the legacy set is
#: EXTENDED (not replaced) with 30/60/120/300 s so a 73-99 s read lands
#: in a finite bucket instead of collapsing into ``+Inf``.
CHECKPOINT_OP_BUCKETS: tuple[float, ...] = (
    0.001, 0.0025, 0.005, 0.01, 0.025, 0.05,
    0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
    30.0, 60.0, 120.0, 300.0,
)


# ─── Registry ───────────────────────────────────────────────────────────────

_METRICS: list["Metric"] = []
_METRICS_LOCK = threading.Lock()

_cap_metric: Optional["Metric"] = None
_invalid_metric: Optional["Metric"] = None


def _register(metric: "Metric") -> "Metric":
    with _METRICS_LOCK:
        _METRICS.append(metric)
    return metric


def registered_metrics() -> list["Metric"]:
    """Return the registered metric objects (test/diagnostic helper)."""
    with _METRICS_LOCK:
        return list(_METRICS)


def known_label_keys() -> frozenset[str]:
    """Every label key declared by a registered metric (closed enum)."""
    keys: set[str] = set()
    with _METRICS_LOCK:
        for metric in _METRICS:
            keys.update(metric.label_keys)
    return frozenset(keys)


def _validate_metric_name(name: str) -> None:
    if not isinstance(name, str) or not name:
        raise ValueError("metric name must be a non-empty string")
    if name in LEGACY_ALIAS_NAMES:
        return
    if not _NAME_RE.match(name):
        raise ValueError(
            f"metric name {name!r} is not canonical; canonical names match "
            f"^ensemble_[a-z0-9_]+$ (legacy names are alias-only)"
        )


def _normalize_allowed_values(
    allowed_values: Optional[Mapping[str, Iterable[str]]],
    label_keys: tuple[str, ...],
) -> dict[str, frozenset[str]]:
    if not allowed_values:
        return {}
    normalized: dict[str, frozenset[str]] = {}
    for key, values in allowed_values.items():
        if key not in label_keys:
            raise ValueError(
                f"allowed_values key {key!r} is not a declared label key "
                f"{list(label_keys)}"
            )
        normalized[key] = frozenset(str(v) for v in values)
    return normalized


def _label_key(labels: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


class Metric:
    """A registered measurement object (counter / gauge / histogram).

    The object doubles as the *handle* the producers use::

        counter = register_counter("ensemble_x_total", label_keys=("outcome",))
        counter.inc(labels={"outcome": "ok"})

    Labels are accepted either as a mapping (``labels={...}``) or as
    keyword arguments (``outcome="ok"``) — the latter keeps the legacy
    ``saver_op_latency_seconds.observe(0.003, op="aget")`` call style
    working.
    """

    __slots__ = (
        "name",
        "kind",
        "label_keys",
        "allowed_values",
        "help",
        "buckets",
        "internal",
        "_lock",
        "_series",
        "_dirty",
        "_published",
        "_last_publish",
        "_alias_names",
        "_dropped_logged",
    )

    def __init__(
        self,
        name: str,
        kind: str,
        label_keys: Iterable[str] = (),
        allowed_values: Optional[Mapping[str, Iterable[str]]] = None,
        help: str = "",
        buckets: Optional[Iterable[float]] = None,
        internal: bool = False,
    ) -> None:
        self.name = name
        self.kind = kind
        self.label_keys = tuple(label_keys)
        self.allowed_values = _normalize_allowed_values(allowed_values, self.label_keys)
        self.help = help or ""
        self.buckets = tuple(float(b) for b in buckets) if buckets is not None else None
        self.internal = internal
        self._lock = threading.Lock()
        self._series: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}
        self._dirty = False
        self._published: Optional[dict[tuple[tuple[str, str], ...], dict[str, Any]]] = None
        self._last_publish = 0.0
        self._alias_names: list[str] = []
        self._dropped_logged = False

    # ── introspection ───────────────────────────────────────────────────────

    @property
    def lock(self) -> threading.Lock:
        """The metric's own lock (per-metric locks only — no global lock)."""
        return self._lock

    @property
    def alias_names(self) -> list[str]:
        """Deprecated alias names exposed for this metric (alias-only)."""
        return list(self._alias_names)

    @alias_names.setter
    def alias_names(self, value: Iterable[str]) -> None:
        self._alias_names = [str(v) for v in value]

    def _empty_series(self) -> dict[str, Any]:
        if self.kind == "histogram":
            return {"count": 0, "sum": 0.0, "buckets": [0] * len(self.buckets or ())}
        if self.kind == "counter":
            return {"value": 0}
        return {"value": 0.0}

    # ── label handling (tiers 1 + 2) ────────────────────────────────────────

    def _merge_labels(
        self,
        labels: Optional[Mapping[str, str]],
        kwargs: Mapping[str, str],
    ) -> dict[str, str]:
        merged: dict[str, str] = {}
        if labels:
            merged.update({str(k): str(v) for k, v in labels.items()})
        if kwargs:
            merged.update({str(k): str(v) for k, v in kwargs.items()})
        unknown = set(merged) - set(self.label_keys)
        if unknown:
            raise KeyError(
                f"{self.kind} {self.name!r} got unknown label keys "
                f"{sorted(unknown)}; declared label_keys={self.label_keys}."
            )
        return merged

    def _fold_labels(self, labels: dict[str, str]) -> tuple[dict[str, str], list[str]]:
        """Tier 2 — fold illegal / disallowed values to ``other``.

        Returns the folded labels plus the list of label keys whose
        value was folded (each folded key is counted in
        ``ensemble_metrics_invalid_label_total{metric,label}``).
        """
        folded = dict(labels)
        invalid_keys: list[str] = []
        for key, value in list(folded.items()):
            illegal = any(ch in value for ch in _ILLEGAL_LABEL_CHARS)
            allowed = self.allowed_values.get(key)
            disallowed = allowed is not None and value not in allowed
            if illegal or disallowed:
                folded[key] = RESERVED_LABEL_VALUE
                invalid_keys.append(key)
        return folded, invalid_keys

    def _count_invalid_label(self, label_key: str) -> None:
        """Count a folded label value (called under the metric's own lock)."""
        metric = _invalid_metric
        if metric is None:
            return
        try:
            metric._inc_raw(1, {"metric": self.name, "label": label_key})
        except Exception:  # pragma: no cover
            return

    # ── internal, guard-free update path ───────────────────────────────────

    def _inc_raw(self, value: float = 1, labels: Optional[Mapping[str, str]] = None) -> None:
        """Update state without label validation or the cardinality cap.

        Used by the registry's own bookkeeping counters (F0.2) so the
        guard paths can never recurse into themselves.
        """
        merged = {str(k): str(v) for k, v in (labels or {}).items()}
        key = _label_key(merged)
        with self._lock:
            series = self._series.get(key)
            if series is None:
                series = self._empty_series()
                self._series[key] = series
            if self.kind == "histogram":
                series["count"] += 1
                series["sum"] += value
            else:
                series["value"] += value
            self._dirty = True
            self._maybe_publish_locked()

    def _set_raw(self, value: float, labels: Optional[Mapping[str, str]] = None) -> None:
        merged = {str(k): str(v) for k, v in (labels or {}).items()}
        key = _label_key(merged)
        with self._lock:
            series = self._series.get(key)
            if series is None:
                series = self._empty_series()
                self._series[key] = series
            series["value"] = value
            self._dirty = True
            self._maybe_publish_locked()

    # ── producer API ────────────────────────────────────────────────────────

    def inc(
        self,
        value: float = 1,
        labels: Optional[Mapping[str, str]] = None,
        **kwargs: str,
    ) -> None:
        """Increment a counter by ``value`` (default 1)."""
        if value < 0:
            raise ValueError(
                f"Counter.inc requires a non-negative amount (got {value}); "
                f"use a Gauge for values that can decrease."
            )
        if "amount" in kwargs and "amount" not in self.label_keys:
            # Legacy keyword alias (``Counter.inc(amount=3)``).
            value = kwargs.pop("amount")
        merged = self._merge_labels(labels, kwargs)
        if self.internal:
            self._inc_raw(value, merged)
            return
        folded, invalid_keys = self._fold_labels(merged)
        key = _label_key(folded)
        rejected = False
        with self._lock:
            if key not in self._series:
                if len(self._series) >= _cardinality_cap():
                    rejected = True
                else:
                    self._series[key] = self._empty_series()
            if not rejected:
                self._series[key]["value"] += value
                self._dirty = True
                self._maybe_publish_locked()
            if rejected:
                self._count_cap_rejection()
        if rejected:
            return
        for label_key in invalid_keys:
            self._count_invalid_label(label_key)

    def set(
        self,
        value: float,
        labels: Optional[Mapping[str, str]] = None,
        **kwargs: str,
    ) -> None:
        """Set a gauge to ``value``."""
        merged = self._merge_labels(labels, kwargs)
        if self.internal:
            self._set_raw(value, merged)
            return
        folded, invalid_keys = self._fold_labels(merged)
        key = _label_key(folded)
        rejected = False
        with self._lock:
            if key not in self._series:
                if len(self._series) >= _cardinality_cap():
                    rejected = True
                else:
                    self._series[key] = self._empty_series()
            if not rejected:
                self._series[key]["value"] = value
                self._dirty = True
                self._maybe_publish_locked()
            if rejected:
                self._count_cap_rejection()
        if rejected:
            return
        for label_key in invalid_keys:
            self._count_invalid_label(label_key)

    def observe(
        self,
        value: float,
        labels: Optional[Mapping[str, str]] = None,
        **kwargs: str,
    ) -> None:
        """Record a histogram observation."""
        merged = self._merge_labels(labels, kwargs)
        if self.kind != "histogram":
            # A counter/gauge handle tolerates observe() as a convenience.
            if self.kind == "counter":
                self.inc(value, merged)
            else:
                self.set(value, merged)
            return
        if self.internal:
            with self._lock:
                key = _label_key(merged)
                series = self._series.get(key)
                if series is None:
                    series = self._empty_series()
                    self._series[key] = series
                series["count"] += 1
                series["sum"] += value
                self._record_bucket(series, value)
                self._dirty = True
                self._maybe_publish_locked()
            return
        folded, invalid_keys = self._fold_labels(merged)
        key = _label_key(folded)
        rejected = False
        with self._lock:
            if key not in self._series:
                if len(self._series) >= _cardinality_cap():
                    rejected = True
                else:
                    self._series[key] = self._empty_series()
            if not rejected:
                series = self._series[key]
                series["count"] += 1
                series["sum"] += value
                self._record_bucket(series, value)
                self._dirty = True
            if rejected:
                self._count_cap_rejection()
        if rejected:
            return
        for label_key in invalid_keys:
            self._count_invalid_label(label_key)

    def _record_bucket(self, series: dict[str, Any], value: float) -> None:
        """Store the observation in ONE bucket (non-cumulative)."""
        if not self.buckets:
            return
        index = bisect.bisect_left(self.buckets, value)
        if index < len(self.buckets):
            series["buckets"][index] += 1
        # A value above every bound lives only in the implicit +Inf bucket.

    def _count_cap_rejection(self) -> None:
        """Count a cardinality-cap rejection (called under the metric lock)."""
        metric = _cap_metric
        if metric is None:
            return
        try:
            metric._inc_raw(1, {"metric": self.name})
        except Exception:  # pragma: no cover
            return

    # ── read API (legacy compatibility) ─────────────────────────────────────

    def get(self, labels: Optional[Mapping[str, str]] = None, **kwargs: str) -> float:
        """Current value (sum across series when no labels are given)."""
        merged = self._merge_labels(labels, kwargs) if (labels or kwargs) else {}
        with self._lock:
            if merged:
                series = self._series.get(_label_key(merged))
                if series is None:
                    return 0.0
                if self.kind == "histogram":
                    return float(series["count"])
                if self.kind == "counter":
                    value = series["value"]
                    return int(value) if float(value).is_integer() else float(value)
                return float(series["value"])
            if not self._series:
                return 0 if self.kind == "counter" else 0.0
            if self.kind == "histogram":
                return float(sum(s["count"] for s in self._series.values()))
            total_value = sum(s["value"] for s in self._series.values())
            if self.kind == "counter":
                return int(total_value) if float(total_value).is_integer() else float(total_value)
            return float(total_value)

    def get_count(self, labels: Optional[Mapping[str, str]] = None, **kwargs: str) -> int:
        merged = self._merge_labels(labels, kwargs) if (labels or kwargs) else {}
        with self._lock:
            if merged:
                series = self._series.get(_label_key(merged))
                return 0 if series is None else int(series["count"])
            return int(sum(s["count"] for s in self._series.values()))

    def get_sum(self, labels: Optional[Mapping[str, str]] = None, **kwargs: str) -> float:
        merged = self._merge_labels(labels, kwargs) if (labels or kwargs) else {}
        with self._lock:
            if merged:
                series = self._series.get(_label_key(merged))
                return 0.0 if series is None else float(series["sum"])
            return float(sum(s["sum"] for s in self._series.values()))

    def get_bucket_counts(
        self, labels: Optional[Mapping[str, str]] = None, **kwargs: str
    ) -> tuple[int, ...]:
        """Cumulative per-bucket counts (accumulated from non-cumulative storage)."""
        merged = self._merge_labels(labels, kwargs) if (labels or kwargs) else {}
        with self._lock:
            if merged:
                series = self._series.get(_label_key(merged))
                raw = list(series["buckets"]) if series else [0] * len(self.buckets or ())
            else:
                raw = [0] * len(self.buckets or ())
                for series in self._series.values():
                    for index, count in enumerate(series["buckets"]):
                        raw[index] += count
        cumulative: list[int] = []
        running = 0
        for count in raw:
            running += count
            cumulative.append(running)
        return tuple(cumulative)

    def get_raw_bucket_counts(
        self, labels: Optional[Mapping[str, str]] = None, **kwargs: str
    ) -> tuple[int, ...]:
        """Non-cumulative per-bucket counts (the raw stored state)."""
        merged = self._merge_labels(labels, kwargs) if (labels or kwargs) else {}
        with self._lock:
            if merged:
                series = self._series.get(_label_key(merged))
                return tuple(series["buckets"]) if series else (0,) * len(self.buckets or ())
            raw = [0] * len(self.buckets or ())
            for series in self._series.values():
                for index, count in enumerate(series["buckets"]):
                    raw[index] += count
            return tuple(raw)

    def series_keys(self) -> list[tuple[tuple[str, str], ...]]:
        with self._lock:
            return list(self._series.keys())

    def labels(self, **labels: str) -> "_LabeledHandle":
        """Pre-bound handle (legacy ergonomics)::

            saver_op_latency_seconds.labels(op="aget").observe(0.003)
        """
        return _LabeledHandle(self, dict(labels))

    def reset(self) -> None:
        """Clear all series (test-only)."""
        with self._lock:
            self._series.clear()
            self._dirty = False
            self._published = None
            self._last_publish = 0.0

    # ── publishing ──────────────────────────────────────────────────────────

    def _snapshot(self) -> dict[tuple[tuple[str, str], ...], dict[str, Any]]:
        with self._lock:
            snapshot: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}
            for key, series in self._series.items():
                copy = dict(series)
                if "buckets" in copy:
                    copy["buckets"] = list(copy["buckets"])
                snapshot[key] = copy
            return snapshot

    def _copy_locked(self) -> dict[tuple[tuple[str, str], ...], dict[str, Any]]:
        """Copy the live series state (caller holds the lock)."""
        snapshot: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}
        for key, series in self._series.items():
            copy = dict(series)
            if "buckets" in copy:
                copy["buckets"] = list(copy["buckets"])
            snapshot[key] = copy
        return snapshot

    def _publish_locked(self, snapshot: dict[tuple[tuple[str, str], ...], dict[str, Any]]) -> bool:
        """Swap the published pointer (caller holds the lock).

        The O(series) copy is paid at most once per publisher-cadence
        window (R-m5) — never per increment.
        """
        self._published = snapshot
        self._dirty = False
        self._last_publish = time.monotonic()
        return True

    def _maybe_publish_locked(self) -> bool:
        """Re-publish if the cadence window elapsed (caller holds the lock).

        This is the writer-side copy-on-publish: a writer flips the dirty
        flag on every observation (O(1)) and the O(series) copy is paid
        at most once per cadence window, never per increment.
        """
        if self._published is None:
            # First publish — a contended render must have something to serve.
            return self._publish_locked(self._copy_locked())
        if not self._dirty:
            return False
        if (time.monotonic() - self._last_publish) < _publisher_cadence():
            return False
        return self._publish_locked(self._copy_locked())

    def publish(self, force: bool = False) -> bool:
        """Publish the live state as the last-published snapshot.

        Returns True when a snapshot was published.  The publish is
        rate-limited to one per publisher-cadence window unless
        ``force`` is set.
        """
        with self._lock:
            snapshot = self._copy_locked()
            if not force:
                if not self._dirty and self._published is not None:
                    return False
                return self._publish_locked(snapshot)
            self._published = snapshot
            self._dirty = False
            self._last_publish = time.monotonic()
            return True

    def published_snapshot(self) -> Optional[dict[tuple[tuple[str, str], ...], dict[str, Any]]]:
        """The last published snapshot (never None after the first publish)."""
        return self._published


class _LabeledHandle:
    """A thin handle pre-bound to a fixed label combination."""

    __slots__ = ("_metric", "_labels")

    def __init__(self, metric: Metric, labels: dict[str, str]) -> None:
        self._metric = metric
        self._labels = dict(labels)

    def observe(self, value: float) -> None:
        self._metric.observe(value, self._labels)

    def inc(self, value: float = 1) -> None:
        self._metric.inc(value, self._labels)

    def set(self, value: float) -> None:
        self._metric.set(value, self._labels)


# ─── Registration API (the pinned contract) ────────────────────────────────


def register_counter(
    name: str,
    label_keys: Iterable[str] = (),
    allowed_values: Optional[Mapping[str, Iterable[str]]] = None,
    help: Optional[str] = None,
) -> Metric:
    """Register a counter and return its handle."""
    _validate_metric_name(name)
    keys = tuple(label_keys)
    metric = Metric(name, "counter", keys, allowed_values, help or "")
    return _register(metric)


def register_gauge(
    name: str,
    label_keys: Iterable[str] = (),
    allowed_values: Optional[Mapping[str, Iterable[str]]] = None,
    help: Optional[str] = None,
) -> Metric:
    """Register a gauge and return its handle."""
    _validate_metric_name(name)
    keys = tuple(label_keys)
    metric = Metric(name, "gauge", keys, allowed_values, help or "")
    return _register(metric)


def register_histogram(
    name: str,
    label_keys: Iterable[str] = (),
    buckets: Optional[Iterable[float]] = None,
    allowed_values: Optional[Mapping[str, Iterable[str]]] = None,
    help: Optional[str] = None,
) -> Metric:
    """Register a histogram and return its handle."""
    _validate_metric_name(name)
    keys = tuple(label_keys)
    bounds = tuple(float(b) for b in buckets) if buckets is not None else DEFAULT_LATENCY_BUCKETS
    ordered = sorted(set(bounds))
    for previous, current in zip(ordered, ordered[1:]):
        if current <= previous:
            raise ValueError(
                f"Histogram buckets must be strictly increasing; got {previous} >= {current}."
            )
    metric = Metric(name, "histogram", keys, allowed_values, help or "", ordered)
    return _register(metric)


def register_summary(
    name: str,
    label_keys: Iterable[str] = (),
    allowed_values: Optional[Mapping[str, Iterable[str]]] = None,
    help: Optional[str] = None,
) -> Metric:
    """Register a summary-style metric (rendered as a histogram)."""
    _validate_metric_name(name)
    keys = tuple(label_keys)
    metric = Metric(name, "histogram", keys, allowed_values, help or "", DEFAULT_LATENCY_BUCKETS)
    return _register(metric)


# ─── Module-level singletons (the FR-5 AC-5.2 surface) ─────────────────────
#
# The legacy names are DEPRECATED ALIASES of these canonical metrics:
# a second exposition of the SAME series, never a second measurement.

#: The canonical counter handle (the canonical metric name is
#: ``ensemble_checkpoint_list_total``).
ensemble_checkpoint_list_total = register_counter(
    "ensemble_checkpoint_list_total",
    help=(
        "Cumulative number of saver.alist(…) invocations observed on the "
        "live message-API path. Expected normal value: 0 (post-PR3 read flip)."
    ),
)
ensemble_checkpoint_list_total.alias_names = ["message_api_checkpoint_list_total"]

#: The canonical histogram handle (the canonical metric name is
#: ``ensemble_checkpoint_op_seconds``).
ensemble_checkpoint_op_seconds = register_histogram(
    "ensemble_checkpoint_op_seconds",
    label_keys=("op",),
    buckets=CHECKPOINT_OP_BUCKETS,
    allowed_values={"op": frozenset({"aget", "aput", "adelete", "alist", "other"})},
    help=(
        "Saver op latency in seconds, labeled by op. Ops: aget, aput, "
        "adelete, alist (migration-only). Buckets are Prometheus-style."
    ),
)
ensemble_checkpoint_op_seconds.alias_names = ["message_api_saver_op_latency_seconds"]

# Legacy PYTHON symbol names — the same objects under their historical
# names.  ``daemon/checkpoint_perf.py`` imports these two names and the
# collectors stay wired; the DEPRECATED part is the exposition NAME
# (the alias), not the Python symbol.
checkpoint_list_total = ensemble_checkpoint_list_total
saver_op_latency_seconds = ensemble_checkpoint_op_seconds

# F0.2 — canonical registry family.
_cap_metric = register_counter(
    "ensemble_metrics_cap_rejected_total",
    label_keys=("metric",),
    help=(
        "Observations rejected by the hard cardinality cap, by metric name. "
        "The rejection happens inside the metric's own lock (no TOCTOU)."
    ),
)

# The ``{label}`` value is the LABEL KEY name — always one of the
# observed metric's declared label keys (the observe path raises on an
# unknown key before it can reach here), so the label axis is a closed
# enum of registered label keys, never free text.
_invalid_metric = register_counter(
    "ensemble_metrics_invalid_label_total",
    label_keys=("metric", "label"),
    help=(
        "Label values folded to the reserved 'other' bucket, by metric name "
        "and label key."
    ),
)

_registry_measurements = register_gauge(
    "ensemble_metrics_registry_measurements",
    help=(
        "Number of registered measurement objects in the canonical registry "
        "(NOT the number of series)."
    ),
)

# F0.4 — vocabulary freeze marker.
_vocabulary_version_gauge = register_gauge(
    "ensemble_metrics_vocabulary_version",
    help="Frozen label-vocabulary version of this daemon build.",
)


# ─── Test-only reset ────────────────────────────────────────────────────────


def reset_for_tests() -> None:
    """Reset every registered metric. Test-only."""
    with _METRICS_LOCK:
        metrics = list(_METRICS)
    for metric in metrics:
        metric.reset()


# ─── Text exposition (Prometheus classic format) ───────────────────────────

_DEPRECATION_EMITTED: set[str] = set()


def _format_value(value: float) -> str:
    """Render a sample value (integral values stay integral)."""
    if isinstance(value, int):
        return str(value)
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def _format_le(value: float) -> str:
    if value == float("inf"):
        return "+Inf"
    return f"{value:g}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_line(metric_name: str, labels: Mapping[str, str]) -> str:
    if not labels:
        return metric_name
    parts = ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(labels.items()))
    return f"{metric_name}{{{parts}}}"


def _prom_type(kind: str) -> str:
    return {"counter": "counter", "gauge": "gauge", "histogram": "histogram"}.get(
        kind, "untyped"
    )


def _tier3_allowed(metric: Metric, key: tuple[tuple[str, str], ...]) -> bool:
    """Tier 3 — render-time allowlist predicate (defence in depth)."""
    for label_name, label_value in key:
        if label_name not in metric.label_keys:
            return False
        allowed = metric.allowed_values.get(label_name)
        if allowed is None:
            continue
        if label_value in allowed or label_value == RESERVED_LABEL_VALUE:
            continue
        return False
    return True


def _render_metric(
    name: str,
    metric: Metric,
    snapshot: Optional[dict[tuple[tuple[str, str], ...], dict[str, Any]]],
    contended: bool,
    reason: str = "lock_contended",
) -> list[str]:
    lines: list[str] = []
    if contended:
        lines.append(f"# {name} unavailable: {reason}")
    lines.append(f"# HELP {name} {metric.help}")
    lines.append(f"# TYPE {name} {_prom_type(metric.kind)}")
    data = snapshot or {}
    if metric.kind in ("counter", "gauge"):
        if not data and not metric.label_keys and not contended:
            # A no-label counter with no series still reports its real
            # value (0).  Under contention the value is UNKNOWN — never
            # render a zero for "I have no data" (catalog §0.11).
            lines.append(f"{name} 0")
        for key, series in sorted(data.items()):
            labels = dict(key)
            if not _tier3_allowed(metric, key):
                continue
            lines.append(f"{_format_line(name, labels)} {_format_value(series['value'])}")
    else:  # histogram
        for key, series in sorted(data.items()):
            if not _tier3_allowed(metric, key):
                continue
            labels = dict(key)
            cumulative = 0
            for index, bound in enumerate(metric.buckets or ()):
                cumulative += series["buckets"][index]
                bucket_labels = {**labels, "le": _format_le(bound)}
                lines.append(f"{_format_line(name + '_bucket', bucket_labels)} {cumulative}")
            inf_labels = {**labels, "le": "+Inf"}
            lines.append(f"{_format_line(name + '_bucket', inf_labels)} {series['count']}")
            lines.append(f"{_format_line(name + '_count', labels)} {series['count']}")
            lines.append(
                f"{_format_line(name + '_sum', labels)} {_format_value(series['sum'])}"
            )
    return lines



def _render_frozen_vocabularies() -> list[str]:
    """Render the frozen vocabularies the F0.4 gauge marks.

    Comment lines only (Prometheus text exposition ignores them) — the
    gauge itself is the machine-readable freeze marker.
    """
    tool_categories = sorted(tool_category_vocabulary())
    return [
        "# frozen vocabulary instance_status: "
        + ",".join(sorted(INSTANCE_STATUS_VOCABULARY)),
        "# frozen vocabulary sweep_lane: " + ",".join(SWEEP_LANE_VOCABULARY),
        "# frozen vocabulary mcp_roster: " + ",".join(MCP_ROSTER_VOCABULARY),
        f"# frozen vocabulary tool_category: count={len(tool_categories)} "
        + ",".join(tool_categories),
        f"# frozen vocabulary version: {VOCABULARY_VERSION} ({VERSION_LABEL_POLICY})",
    ]


def render_metrics() -> str:
    """Render the whole registry in Prometheus text-exposition format.

    Never blocks for longer than the per-metric try-acquire budget
    (50 ms).  A family whose lock is contended is served from its last
    published snapshot with a ``# <family> unavailable: lock_contended``
    comment line.
    """
    # Registry bookkeeping gauges (F0.2 / F0.4) are refreshed here.
    with _METRICS_LOCK:
        total = len(_METRICS)
    try:
        _registry_measurements.set(total)
    except Exception:  # pragma: no cover
        pass
    try:
        _vocabulary_version_gauge.set(VOCABULARY_VERSION)
    except Exception:  # pragma: no cover
        pass

    lines: list[str] = []
    with _METRICS_LOCK:
        metrics = list(_METRICS)
    deadline = time.monotonic() + RENDER_TOTAL_BUDGET_SECONDS
    for metric in metrics:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # The render-wide budget is spent — serve the last published
            # snapshot instead of blocking any further.
            lines.extend(
                _render_metric(
                    metric.name,
                    metric,
                    metric._published,
                    True,
                    "render_budget_exhausted",
                )
            )
            continue
        acquired = metric._lock.acquire(timeout=min(RENDER_TRY_ACQUIRE_SECONDS, remaining))
        if acquired:
            try:
                # The lock is held, so the live state is a consistent
                # snapshot — render it directly (no staleness).  The
                # copy-on-publish pointer is refreshed here at most once
                # per publisher-cadence window (R-m5) so a contended
                # render can still serve the last published snapshot.
                live = metric._copy_locked()
            finally:
                metric._lock.release()
            snapshot = live
            contended = False
        else:
            snapshot = metric._published
            contended = True
        lines.extend(_render_metric(metric.name, metric, snapshot, contended))
        for alias in metric.alias_names:
            if alias not in _DEPRECATION_EMITTED:
                _DEPRECATION_EMITTED.add(alias)
                logger.warning(
                    "Deprecated metric name %s is an alias of %s; migrate to the "
                    "canonical name (the alias is removed one release after the "
                    "deprecation window).",
                    alias,
                    metric.name,
                )
            lines.extend(_render_metric(alias, metric, snapshot, False))
    lines.extend(_render_frozen_vocabularies())
    return "\n".join(lines) + ("\n" if lines else "")


__all__ = [
    "CHECKPOINT_OP_BUCKETS",
    "INSTANCE_STATUS_VOCABULARY",
    "MCP_ROSTER",
    "MCP_ROSTER_VOCABULARY",
    "SWEEP_LANES",
    "SWEEP_LANE_VOCABULARY",
    "VERSION_LABEL_POLICY",
    "DEFAULT_CARDINALITY_CAP",
    "DEFAULT_LATENCY_BUCKETS",
    "DEFAULT_PUBLISHER_CADENCE_SECONDS",
    "LEGACY_ALIAS_NAMES",
    "Metric",
    "RESERVED_LABEL_VALUE",
    "VOCABULARY_VERSION",
    "checkpoint_list_total",
    "ensemble_checkpoint_list_total",
    "ensemble_checkpoint_op_seconds",
    "known_label_keys",
    "register_counter",
    "register_gauge",
    "register_histogram",
    "register_summary",
    "registered_metrics",
    "render_metrics",
    "reset_for_tests",
    "saver_op_latency_seconds",
    "set_cardinality_cap_provider",
    "set_publisher_cadence_provider",
    "tool_category_artifact_path",
    "tool_category_vocabulary",
]
