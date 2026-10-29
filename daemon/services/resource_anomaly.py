"""``resource_anomaly`` — the explainable anomaly layer (P4).

Turns the resource time series into EXPLAINABLE findings. One
background pass (every ``anomaly_interval_s``, 5-15 min) consumes ONLY
bucketed series — never raw 30 s rows — and emits findings that each
carry the full §2.7 explainability payload:

    {baseline: {method, value, n}, observed, deviation: {metric, value,
     threshold}, contributors: [...]}

Structural decisions (phase4-plan §Structural — BINDING, all honored
here):

1. **NO new table, NO migration.** Findings are derived data held in a
   bounded in-RAM journal (:data:`JOURNAL_MAXLEN` entries, drop-oldest,
   append-only). CONSEQUENCE DOCUMENTED HERE: **finding ids are
   process-local and change across daemon restarts** — consumers (the
   FE timeline, 8 s poll) re-fetch on every poll; this is accepted
   behavior, not a defect.
2. **Consumes ONLY bucketed series.** The store downsamples every
   input to a :data:`STATE_BUCKET_S` (5-min) stride at the store
   boundary (last-wins within a stride bucket — the P1 rollup
   precedent); detectors read ONLY the bucketed state. Inputs may
   arrive at minute cadence (the flusher's minute roll-up rows) or be
   replayed from the DB minute tier on cold boot; raw 30 s rows are
   never scanned by the pass. The DB minute tier warms the pass on
   cold boot (restart-recompute); the in-RAM store is primary after.
   Replay covers the minute tier (``resource_daemon_bucket``,
   ``interval_s=60``) — per-instance series have no minute tier (T2 is
   raw-tier by design) and therefore re-warm live after a restart;
   their pre-restart findings are not regenerated (accepted, bounded
   gap — the daemon-series findings survive via replay).
3. **Thread model unchanged.** The pass piggybacks the FLUSHER thread
   behind :class:`AnomalyScheduler`'s monotonic-interval gate — no new
   thread. A pass measuring over :data:`ANOMALY_PASS_BUDGET_S` causes
   the NEXT due cycle to be skipped (skip-on-overrun, mirroring the
   sampler) and bumps ``counters["missed_anomaly_cycles"]``. Sampling
   and flushing are never delayed by the pass.
4. **Findings feed the P3 traceback contract** — ``build_window_view``
   accepts a ``findings_provider`` and the journal fills the reserved
   ``findings`` key (lean rows; the FE ``TracebackPayload.findings``
   is ``unknown[]``).
5. **No table/migration impact** — the configured worker limit
   (``limits.llm_concurrency``) is a MANAGED SETTING consumed
   read-only via the resolver ladder
   (``daemon.services.settings_service``) at pass time; never written.

**Conditioned baselines (the critical FP-avoidance).** The baseline
stratum is ``(observed_workers, configured_limit)`` — BOTH dimensions.
A worker-count change (observed, sampled live) or a configured-limit
change (the managed setting) moves the series to a DIFFERENT stratum
⇒ the shift re-baselines and is NEVER flagged. The mandated test
``test_worker_count_increase_is_not_anomaly`` pins variants A (workers
4→5 ⇒ no finding), B (identical rise at constant workers+limit ⇒
finding), and C (configured limit 4→6 via the managed-setting shape,
observed count follows ⇒ no finding).

**Severity banding** (fixed, documented, no model, no score):
``ratio = deviation.value / deviation.threshold`` — ``<2 low``,
``<4 medium``, ``>=4 high``.

**Shape classification** (deterministic ordered rules over the
detector outputs, first match wins):

* ``spike`` — single-bucket ``mad_z >= threshold``, returned toward
  baseline within ≤2 buckets;
* ``step``  — change-point: post-window median > pre-window median +
  k·MAD, sustained ≥ W buckets, CUSUM⁺ at threshold;
* ``ramp``  — EWMA rising over ≥ k consecutive buckets WITH partial
  recovery present;
* ``leak``  — monotonic non-recovering upward drift across the state
  window (zero down-steps, CUSUM⁺ persistent) — the RAM-leak
  fingerprint.

A detector firing with NO matching shape rule (e.g. the newest bucket
has no future data to show recovery) falls back conservatively to
``spike`` with a WARN — a fired deviation is never silently dropped.

**One-sided CUSUM (rationale).** ``cusum_m`` is computed and stored on
every bucket (the plan's 6-float state includes both ``cusum⁺`` and
``cusum⁻``) but no detector reads it. Detection is INTENTIONALLY
ONE-SIDED upward:

* the FE-frozen shape taxonomy is ``spike | ramp | leak | step`` —
  every shape names an upward-resource event;
* a downward shift IS a baseline-level change, not an anomaly — it
  re-baselines through the next stratum-conditional cycle (the cold
  stratum's own observations become the new baseline);
* the configured-limit conditioning treats intentional reductions
  (operator-managed ``limits.llm_concurrency`` drops) as stratum
  changes that re-baseline, never as anomalies.

``cusum_m`` is retained on the bucket struct for symmetry / future
extension; it is not consulted by any detector today. Adding new
shapes that read it would be a NEW feature (FE-frozen enum
constraint — see phase4-plan §Structural).

**Honesty rule.** ``contributors`` rank instances by share of the
charged bytes in the finding window; shares sum ≤100 and the
unattributable remainder is reported as an explicit
``{"unattributed": true, "share_pct": ...}`` row. Contributors are
resolved through the P3 join layer (:mod:`daemon.services.resource_traceback`
— ``nearest_sample_at`` / ``in_flight_tasks_at`` / ``children_at_t`` /
``chart_render_aggregate`` / ``resolve_pid_instance``); these joins are
REUSED, never forked. Per P3 adjudication B, a T2 ``status`` of
``"idle"`` is a SAMPLE-TIME label and is NEVER read as aliveness here
— liveness questions go through the task/event joins.

**TZ discipline (R5).** Bucket keys are the P1 naive-UTC digits;
DoW/hour keys derive from the bucket digits read as UTC
(:func:`_parse_bucket_ts` — ``datetime.strptime`` + ``timezone.utc``).
Wallclock usage: ``datetime.now(timezone.utc)`` appears ONCE in
:meth:`AnomalyEngine.list_anomalies` to compute the window-cutoff
epoch (TZ-correct via ``timezone.utc``; no injectable clock is
needed — the cutoff is a pure, non-test-dependent boundary).

**Bounded state (the §2.7 arithmetic — the A1 acceptance bound).**
Per series ≤ ``window_buckets`` buckets × 6 floats × 8 B
(defaults: 48 × 6 × 8 = 2,304 B ≈ 2.3 KB); cardinality cap
:data:`DEFAULT_MAX_SERIES` (≈211); DoW×hour map ≤ 168 floats/series.
Worst case ≈ 769 KB < 1 MB. The findings journal is separately
byte-bounded: findings are MEASURED on append and oldest-first
eviction keeps the journal's measured total STRICTLY under
:data:`JOURNAL_MAX_BYTES` (256 KB). :meth:`SeriesStateStore.
state_bytes` computes the store's accounting from the configured
caps; the bounds test derives every number from those caps (no
literals).
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Contract constants (the §2.7 bounded-state arithmetic) ──────────────────

#: The state window (seconds) — "keep last 4 h" per §2.7.
STATE_WINDOW_S = 4 * 3600

#: Store stride (seconds) — buckets are downsampled to this stride at the
#: store boundary. Default 300 s ⇒ 48 buckets per the §2.7 arithmetic
#: (4 h @ 5 min). Tests may shrink it; the bounds test derives the bucket
#: count from the configured values, never a literal 48.
STATE_BUCKET_S = 300

#: Accounting unit — a float is counted as 8 B (the §2.7 basis).
FLOAT_BYTES = 8

#: Per-bucket state floats: value, baseline, ewma, cusum⁺, cusum⁻, mad.
STATE_FLOATS = 6

#: Series cardinality cap — §2.7 budget: 6 process metrics + MCP pools +
#: ≤100 instances × 2 series ≈ 211. Registration beyond the cap evicts
#: the least-recently-updated series (drop-oldest).
DEFAULT_MAX_SERIES = 211

#: DoW×hour baseline map — 7×24 slots per series (entry cap per series;
#: the stratum is part of the key, the CAP keeps the §2.7 bound honest).
DOW_HOUR_SLOTS = 7 * 24

#: Findings journal bound (entries, drop-oldest, append-only).
JOURNAL_MAXLEN = 256

#: Journal BYTE budget — the A1 second bound: the journal is
#: byte-aware (findings are measured on append; oldest-first eviction
#: keeps the total STRICTLY under this budget). 262_144 = 256 KB.
JOURNAL_MAX_BYTES = 262_144

#: Self-cost budget for ONE pass (seconds). The pass is pure arithmetic
#: over ≤211 series × ≤48 buckets; 5 s is ~1000× the expected cost and
#: stays far under the 60 s flush window so flushing is never delayed.
ANOMALY_PASS_BUDGET_S = 5.0

#: EWMA smoothing factor (documented fixed constant — no tuning knob).
EWMA_ALPHA = 0.3

#: MAD → robust-sigma conversion (consistent-estimate constant).
MAD_TO_SIGMA = 1.4826

#: CUSUM drift allowance (robust sigmas) — documented fixed constant.
CUSUM_K = 0.5

#: CUSUM fire threshold (accumulated robust sigmas).
CUSUM_THRESHOLD = 5.0

#: Effective-sigma fallback: when the same-stratum MAD is 0 (flat
#: baseline), the robust scale would be 0 and NOTHING could ever fire.
#: The documented fallback scale is ``max(MAD·1.4826, 1% of the
#: baseline level, tiny)`` — flat series stay quiet under small noise
#: and still fire on real jumps; deterministic, no extra state float
#: (the 6-float §2.7 state struct is preserved).
MIN_REL_SIGMA = 0.01
ABS_SIGMA_FLOOR = 1e-9

#: Step rule: post-window median must exceed pre-window median by this
#: many robust sigmas, sustained ≥ :data:`STEP_MIN_POST_BUCKETS`.
STEP_MEDIAN_SIGMAS = 3.0

#: Step rule: minimum sustained post-window buckets (the plan's W).
STEP_MIN_POST_BUCKETS = 3

#: Step rule: minimum same-stratum pre-window baseline points.
STEP_MIN_PRE_POINTS = 6

#: DoW×hour baseline minimum slot size — the fallback path engages a
#: periodic-workload baseline when the same-stratum rolling history is
#: thin AND the latest bucket's (dow, hour) slot has at least this many
#: prior observations across days (one per day at bucket_s=3600). The
#: default 3 ⇒ three days of data at the same hour is enough for the
#: fallback to discriminate the periodic morning rise from a real
#: anomaly. Lower ⇒ reachable in ordinary deployments; the rolling
#: branch still wins whenever the same-stratum history is warm
#: (any window with ≥ min_baseline_points same-stratum buckets).
#:
#: Preference adjudication (test-driven, 2026-10-19): with rolling-
#: preferred (rolling first; dow_hour as fallback for cold stratum),
#: the periodic-workload test (``test_dow_hour_baseline_engages_for_
#: periodic_workload``) PASSES via the reachability fix alone — no
#: preference flip to dow_hour-first-when-slot-warm was required.
#: The mandated FP tests (``test_worker_count_increase_is_not_anomaly``
#: A/B/C) stay green; rolling still wins when the stratum is warm.
DOW_HOUR_MIN_N = 3

#: Ramp rule: consecutive EWMA-rise buckets required.
RAMP_MIN_BUCKETS = 4

#: Leak rule: minimum buckets of strictly non-decreasing drift.
LEAK_MIN_BUCKETS = 6

#: Spike rule: a spike must return toward baseline within this many
#: buckets to classify as spike (else the fallback path decides).
SPIKE_RECOVERY_BUCKETS = 2

#: How far back (buckets) a pass looks for spike candidates.
SPIKE_LOOKBACK_BUCKETS = 6

#: Max contributor rows stored per finding (top shares; the honesty
#: remainder row is always preserved). Keeps a finding ≈1 KB.
MAX_CONTRIBUTORS = 8

_NAIVE_FMT = "%Y-%m-%d %H:%M:%S"


def _parse_bucket_ts(ts: str) -> Optional[datetime]:
    """Parse P1 naive-UTC bucket digits into an AWARE-UTC datetime.

    The digits ARE UTC (D9); the aware result makes DoW/hour keys and
    arithmetic explicit. Unparseable ⇒ ``None`` (the caller skips the
    observation — degenerate input never raises).
    """
    if not ts or not isinstance(ts, str):
        return None
    s = ts.replace("T", " ")
    if "." in s:
        s = s.split(".", 1)[0]
    try:
        return datetime.strptime(s, _NAIVE_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _fmt_bucket_ts(dt: datetime) -> str:
    """Format an instant as P1 naive-UTC bucket digits."""
    return dt.replace(tzinfo=None).strftime(_NAIVE_FMT)


def _floor_to_stride(ts: str, bucket_s: int) -> Optional[str]:
    """Floor a bucket timestamp onto the store's stride boundary."""
    dt = _parse_bucket_ts(ts)
    if dt is None:
        return None
    epoch = int(dt.timestamp())
    return _fmt_bucket_ts(
        datetime.fromtimestamp(epoch - (epoch % bucket_s), tz=timezone.utc)
    )


def _dow_hour(ts: str) -> Optional[Tuple[int, int]]:
    """(day-of-week, hour-of-day) of a bucket, read as UTC (R5)."""
    dt = _parse_bucket_ts(ts)
    if dt is None:
        return None
    return (dt.weekday(), dt.hour)


def _finite(v: Any) -> Optional[float]:
    """Coerce to a finite float; NaN/Inf/None ⇒ ``None`` (skip)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _median(values: List[float]) -> float:
    xs = sorted(values)
    n = len(xs)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return xs[mid]
    return (xs[mid - 1] + xs[mid]) / 2.0


# ── Per-bucket state ────────────────────────────────────────────────────────


@dataclass
class BucketState:
    """One 6-float state bucket (§2.7 state struct) + its stratum.

    ``workers`` is the conditioning stratum at observation time — the
    string form ``"<observed_workers>|<configured_limit>"``. Both
    dimensions re-baseline (never flag) when either changes.
    """

    ts: str            # stride-floor bucket ts (naive-UTC digits)
    value: float
    baseline: float
    ewma: float
    cusum_p: float
    cusum_m: float
    mad: float
    workers: str = "?"


@dataclass
class SeriesState:
    """Bounded per-series ring + the DoW×hour baseline map.

    The DoW×hour map is keyed ``(workers, dow, hour)`` — the plan's
    ``(series, n_active_workers, dow, hour)`` key with the series
    implied — and CAPPED at ``dow_hour_slots`` entries per series
    (drop-oldest insertion order), which is what keeps the §2.7 bound
    arithmetic honest under multiple strata.
    """

    key: str
    buckets: Deque[BucketState] = field(default_factory=deque)
    dow_hour: Dict[Tuple[str, int, int], float] = field(default_factory=dict)
    dow_hour_n: Dict[Tuple[str, int, int], int] = field(default_factory=dict)
    last_ts: str = ""

    def __post_init__(self) -> None:
        # Insertion-ordered eviction for the DoW×hour cap (drop-oldest).
        self.dow_hour = dict(self.dow_hour)


# ── The series-state store (task 1) ─────────────────────────────────────────


class SeriesStateStore:
    """Per-series bounded rings + the DoW×hour baseline map.

    * ``window_buckets`` derives from ``(window_s, bucket_s)`` — the
      bounds test reads these attributes; no literal 48 anywhere.
    * Over-cap series registration evicts the LEAST-RECENTLY-UPDATED
      series (drop-oldest; the eviction is logged).
    * Same-stride re-ingest REPLACES the bucket (idempotent — the
      restart replay cannot double-count buckets that also arrive live).
    * ``state_bytes()`` implements the §2.7 accounting over the
      CONFIGURED caps (floats × 8 B; dict overheads excluded — the
      same accounting basis as the architecture's arithmetic).
    """

    def __init__(
        self,
        *,
        max_series: int = DEFAULT_MAX_SERIES,
        window_s: int = STATE_WINDOW_S,
        bucket_s: int = STATE_BUCKET_S,
        dow_hour_slots: int = DOW_HOUR_SLOTS,
    ) -> None:
        self.max_series = max(1, int(max_series))
        self.window_s = max(bucket_s, int(window_s))
        self.bucket_s = max(1, int(bucket_s))
        self.dow_hour_slots = max(1, int(dow_hour_slots))
        # window_buckets = the DERIVED bucket budget (48 at defaults).
        self.window_buckets = max(4, self.window_s // self.bucket_s)
        self._series: Dict[str, SeriesState] = {}
        self._lock = threading.Lock()

    # ── Registration (drop-oldest) ────────────────────────────────────

    def _get_or_register(self, key: str) -> Optional[SeriesState]:
        st = self._series.get(key)
        if st is not None:
            return st
        if len(self._series) >= self.max_series:
            evicted = self._evict_oldest()
            if evicted is None:
                # Cannot happen (max_series ≥ 1), but refuse rather
                # than grow — the bound is absolute.
                return None
            logger.warning(
                "[resource_anomaly] series cap %d reached — evicted "
                "least-recently-updated series %r (drop-oldest)",
                self.max_series,
                evicted,
            )
        st = SeriesState(key=key)
        self._series[key] = st
        return st

    def _evict_oldest(self) -> Optional[str]:
        oldest_key, oldest_ts = None, None
        for k, st in self._series.items():
            if oldest_ts is None or st.last_ts < oldest_ts:
                oldest_key, oldest_ts = k, st.last_ts
        if oldest_key is not None:
            del self._series[oldest_key]
        return oldest_key

    # ── Observation ───────────────────────────────────────────────────

    def observe(
        self,
        key: str,
        ts: str,
        value: Any,
        workers: str = "?",
    ) -> bool:
        """Record one observation; returns ``False`` when skipped.

        The value is downsampled onto the stride boundary (last-wins
        within a stride bucket — a same-stride re-ingest REPLACES the
        bucket and recomputes its state from the remaining history,
        making replay idempotent). NaN/Inf/unparseable-ts inputs are
        skipped silently (degenerate-input discipline).
        """
        v = _finite(value)
        bts = _floor_to_stride(ts, self.bucket_s)
        if v is None or bts is None:
            return False
        with self._lock:
            st = self._get_or_register(key)
            if st is None:
                return False
            # Idempotent same-bucket replacement: pop, then re-observe.
            if st.buckets and st.buckets[-1].ts == bts:
                st.buckets.pop()
            self._append_state(st, bts, v, workers)
            st.last_ts = bts
            self._absorb_dow_hour(st, bts, v, workers)
        return True

    def _append_state(
        self, st: SeriesState, bts: str, v: float, workers: str
    ) -> None:
        """Compute the 6-float state from the SAME-STRATUM trailing
        history and append (bounded by ``window_buckets``)."""
        same = [b.value for b in st.buckets if b.workers == workers]
        n = len(same)
        if n >= 2:
            base = _median(same)
            dev = sorted(abs(x - base) for x in same)
            mid = n // 2
            mad = (
                dev[mid]
                if n % 2
                else (dev[mid - 1] + dev[mid]) / 2.0
            )
        else:
            # Cold stratum: the observation IS its own baseline ⇒
            # z = 0 ⇒ CUSUM cannot fire on a stratum switch (the
            # re-baseline-by-construction property).
            base, mad = v, 0.0
        prev = st.buckets[-1] if st.buckets else None
        if prev is not None:
            ewma = prev.ewma + EWMA_ALPHA * (v - prev.ewma)
        else:
            ewma = v
        z = (v - base) / effective_sigma(mad, base)
        cp = cm = 0.0
        if prev is not None:
            cp = max(0.0, prev.cusum_p + z - CUSUM_K)
            cm = max(0.0, prev.cusum_m + (-z) - CUSUM_K)
        st.buckets.append(
            BucketState(
                ts=bts,
                value=v,
                baseline=base,
                ewma=ewma,
                cusum_p=cp,
                cusum_m=cm,
                mad=mad,
                workers=workers,
            )
        )
        while len(st.buckets) > self.window_buckets:
            st.buckets.popleft()

    def _absorb_dow_hour(
        self, st: SeriesState, bts: str, v: float, workers: str
    ) -> None:
        """Fold the observation into the DoW×hour mean map (capped).

        Keyed ``(workers, dow, hour)`` — the stratum is part of the key
        (the plan's conditioning), the per-series slot cap keeps the
        §2.7 bound honest (drop-oldest eviction).
        """
        dh = _dow_hour(bts)
        if dh is None:
            return
        key3 = (workers, dh[0], dh[1])
        cur = st.dow_hour.get(key3)
        n = st.dow_hour_n.get(key3, 0)
        st.dow_hour[key3] = ((cur or 0.0) * n + v) / (n + 1)
        st.dow_hour_n[key3] = n + 1
        while len(st.dow_hour) > self.dow_hour_slots:
            oldest = next(iter(st.dow_hour))
            st.dow_hour.pop(oldest, None)
            st.dow_hour_n.pop(oldest, None)

    # ── Reads ─────────────────────────────────────────────────────────

    def states(self, key: str) -> List[BucketState]:
        with self._lock:
            st = self._series.get(key)
            return list(st.buckets) if st else []

    def series_keys(self) -> List[str]:
        with self._lock:
            return sorted(self._series.keys())

    def dow_hour_mean(
        self, key: str, workers: str, dh: Tuple[int, int]
    ) -> Tuple[float, int]:
        with self._lock:
            st = self._series.get(key)
            if st is None:
                return (0.0, 0)
            k3 = (workers, dh[0], dh[1])
            return (st.dow_hour.get(k3, 0.0), st.dow_hour_n.get(k3, 0))

    def state_bytes(self) -> int:
        """The §2.7 accounting: series rings + DoW×hour map, floats × 8 B.

        The bounds test derives every number from the configured caps
        via this method + the public attributes — no literals.
        """
        with self._lock:
            ring_bytes = sum(
                len(st.buckets) for st in self._series.values()
            ) * STATE_FLOATS * FLOAT_BYTES
            dow_entries = sum(len(st.dow_hour) for st in self._series.values())
        return ring_bytes + dow_entries * FLOAT_BYTES


# ── Baseline engine (task 2) ────────────────────────────────────────────────


def rolling_baseline(
    states: List[BucketState], workers: str
) -> Tuple[float, int]:
    """Trailing same-stratum median — ``method="rolling_median"``.

    Returns ``(value, n)``; ``n`` counts the same-stratum buckets the
    median was computed over (the cold-start guard reads ``n``).
    """
    same = [b.value for b in states if b.workers == workers]
    if not same:
        return (0.0, 0)
    return (_median(same), len(same))


def dow_hour_baseline(
    store: SeriesStateStore,
    key: str,
    workers: str,
    dh: Tuple[int, int],
) -> Tuple[float, int]:
    """DoW×hour mean — ``method="dow_hour_mean"`` (periodic workloads).

    Keyed ``(series, workers, dow, hour)`` per the plan's conditioning.
    """
    return store.dow_hour_mean(key, workers, dh)


# ── Detectors (task 3 — pure functions over the bucket ring) ────────────────


def effective_sigma(mad: float, baseline_level: float) -> float:
    """The robust scale with the documented zero-MAD fallback.

    ``max(MAD_TO_SIGMA·mad, MIN_REL_SIGMA·|baseline|, ABS_SIGMA_FLOOR)``
    — see the constant's docstring. Every z-score in the module goes
    through this helper (one source of truth).
    """
    return max(
        MAD_TO_SIGMA * max(0.0, mad),
        MIN_REL_SIGMA * abs(baseline_level),
        ABS_SIGMA_FLOOR,
    )


def mad_z(state: BucketState) -> Optional[float]:
    """Robust z of one bucket against its own stored baseline/scale.

    Uses :func:`effective_sigma` (the zero-MAD fallback) so a flat
    baseline with a real jump still fires. Returns ``None`` only on a
    non-finite intermediate (defensive — degenerate inputs never
    raise).
    """
    sigma = effective_sigma(state.mad, state.baseline)
    if sigma <= 0:
        return None
    z = abs(state.value - state.baseline) / sigma
    if math.isnan(z) or math.isinf(z):
        return None
    return z


def detect_spike(
    states: List[BucketState], threshold: float
) -> Optional[int]:
    """Index of the newest spike candidate with visible recovery.

    A spike = ``mad_z >= threshold`` on a single bucket that RETURNS
    toward baseline within ≤2 following buckets. The newest bucket has
    no future data ⇒ ``None`` here (the caller's conservative fallback
    owns that case).
    """
    lo = max(0, len(states) - SPIKE_LOOKBACK_BUCKETS)
    for i in range(len(states) - 2, lo - 1, -1):
        z = mad_z(states[i])
        if z is None or z < threshold:
            continue
        base_i = states[i].baseline
        sigma = effective_sigma(states[i].mad, base_i)
        future = states[i + 1: i + 1 + SPIKE_RECOVERY_BUCKETS]
        if future and min(f.value for f in future) <= base_i + sigma:
            return i
    return None


def detect_step(
    states: List[BucketState], workers: str
) -> Optional[Tuple[int, float, float]]:
    """Change-point: post-window median > pre-window median + k·MAD,
    sustained ≥ W buckets, with CUSUM⁺ at threshold.

    Returns ``(index_of_first_post_bucket, cusum_p, pre_mad)`` or
    ``None``. Evaluated within the CURRENT stratum only — a stratum
    switch (worker count / configured limit change) re-baselines and
    can never present as a step.
    """
    if len(states) < STEP_MIN_POST_BUCKETS + 2:
        return None
    split = len(states) - STEP_MIN_POST_BUCKETS
    post = states[split:]
    if any(b.workers != workers for b in post):
        return None
    pre = [b for b in states[:split] if b.workers == workers]
    if len(pre) < STEP_MIN_PRE_POINTS:
        return None
    pre_med = _median([b.value for b in pre])
    pre_mad = _median([b.mad for b in pre])
    sigma = effective_sigma(pre_mad, pre_med)
    post_med = _median([b.value for b in post])
    last = states[-1]
    post_range = max(b.value for b in post) - min(b.value for b in post)
    shift = abs(post_med - pre_med)
    if (
        post_med > pre_med + STEP_MEDIAN_SIGMAS * sigma
        and last.cusum_p >= CUSUM_THRESHOLD
        # Sustained LEVEL: the post window must sit within ~10% of the
        # shift (or ~1% of the level for tiny shifts) — a still-rising
        # window is NOT a step (the ordered rules then route it to
        # ramp/leak).
        and post_range <= max(0.1 * shift, MIN_REL_SIGMA * abs(post_med))
    ):
        return (split, last.cusum_p, pre_mad)
    return None


def detect_ramp(
    states: List[BucketState],
) -> Optional[Tuple[int, float]]:
    """EWMA rising over ≥ k consecutive buckets WITH partial recovery.

    Scans the WHOLE window for a rise-run of ≥ :data:`RAMP_MIN_BUCKETS`
    consecutive EWMA gains that is later followed by an EWMA decline
    (the partial recovery — what separates a ramp from a leak). Returns
    ``(rise_start_index, cusum_p_at_rise_end)`` or ``None``.
    """
    n = len(states)
    if n < RAMP_MIN_BUCKETS + 2:
        return None
    ew = [b.ewma for b in states]
    i = 0
    while i <= n - RAMP_MIN_BUCKETS - 1:
        # Rise-run [i, i+RAMP_MIN_BUCKETS] (k consecutive gains).
        j = i
        while j + 1 < n and ew[j + 1] > ew[j]:
            j += 1
        rise_len = j - i
        if rise_len >= RAMP_MIN_BUCKETS and j + 1 < n:
            # Partial recovery: any EWMA decline after the rise run.
            if any(ew[m] < ew[m - 1] for m in range(j + 1, n)):
                if states[j].cusum_p >= CUSUM_THRESHOLD:
                    return (i, states[j].cusum_p)
        i = max(j, i + 1)  # skip past this run
    return None


def detect_leak(
    states: List[BucketState],
) -> Optional[Tuple[int, float]]:
    """Monotonic non-recovering upward drift — the leak fingerprint.

    Zero down-steps across ≥ LEAK_MIN_BUCKETS buckets + CUSUM⁺ at
    threshold + the rise DISTRIBUTED across the window (no single
    bucket contributes more than half the total rise — a one-bucket
    jump is a spike-class event, not a drift). Returns
    ``(window_start_index, cusum_p)`` or ``None``.
    """
    tail = states[-LEAK_MIN_BUCKETS:]
    if len(tail) < LEAK_MIN_BUCKETS:
        return None
    increments = [b.value - a.value for a, b in zip(tail, tail[1:])]
    total_rise = tail[-1].value - tail[0].value
    if total_rise <= 0:
        return None
    if max(increments) > 0.5 * total_rise:
        return None  # a single-bucket jump — not a distributed drift
    for a, b in zip(tail, tail[1:]):
        if b.value < a.value:
            return None
    last = states[-1]
    if last.cusum_p >= CUSUM_THRESHOLD and last.value > last.baseline:
        return (len(states) - LEAK_MIN_BUCKETS, last.cusum_p)
    return None


# ── Shape classifier + severity (task 4) ────────────────────────────────────


def classify_shape(
    states: List[BucketState],
    *,
    fired_index: int,
    workers: str,
    mad_threshold: float,
) -> Tuple[str, bool]:
    """Ordered deterministic rules → ``(shape, used_fallback)``.

    First match wins: spike → step → ramp → leak. A fired deviation
    with NO matching rule falls back conservatively to ``spike`` with
    the fallback flag set (the caller WARNs).
    """
    try:
        state = states[fired_index]
    except IndexError:
        return ("spike", True)

    # 1. spike — recovery visible within ≤2 buckets.
    sigma = effective_sigma(state.mad, state.baseline)
    z = mad_z(state)
    future = states[fired_index + 1: fired_index + 1 + SPIKE_RECOVERY_BUCKETS]
    if (
        z is not None
        and z >= mad_threshold
        and future
        and min(f.value for f in future) <= state.baseline + sigma
    ):
        return ("spike", False)

    # 2. step — sustained post-window level shift (current stratum).
    if detect_step(states, workers) is not None:
        return ("step", False)

    # 3. ramp — rising EWMA with partial recovery present.
    if detect_ramp(states) is not None:
        return ("ramp", False)

    # 4. leak — monotonic non-recovering drift.
    if detect_leak(states) is not None:
        return ("leak", False)

    # Unknown combination → conservative fallback (WARN upstream).
    return ("spike", True)


def severity_band(deviation_value: float, threshold: float) -> str:
    """Fixed banding of ``value / threshold`` — <2 low, <4 medium, ≥4 high.

    Documented mapping; no model, no score.
    """
    t = abs(threshold) or 1e-12
    ratio = abs(deviation_value) / t
    if ratio < 2:
        return "low"
    if ratio < 4:
        return "medium"
    return "high"


# ── Finding + journal ───────────────────────────────────────────────────────


def kind_for_series(series_key: str) -> str:
    """The finding ``kind`` — the series' resource domain.

    Deterministic mapping (the FE renders markers per kind):
    memory-series ⇒ ``memory`` / ``instance_memory`` /
    ``instance_child_memory``; cpu ⇒ ``cpu``; threads ⇒ ``threads``;
    fds ⇒ ``fds``; anything else ⇒ the series namespace.
    """
    if series_key.startswith("instance."):
        return "instance_child_memory" if series_key.endswith(
            "child_rss_kb"
        ) else "instance_memory"
    mapped = {
        "daemon.rss_kb": "memory",
        "daemon.anon_kb": "memory",
        "cgroup.anon_kb": "memory",
        "cgroup.current_kb": "memory",
        "daemon.cpu_pct": "cpu",
        "daemon.threads": "threads",
        "daemon.fds": "fds",
    }
    if series_key in mapped:
        return mapped[series_key]
    return series_key.split(".", 1)[0]


def instance_id_for_series(series_key: str) -> Optional[str]:
    """The ``instance_id?`` of instance-scoped series (else ``None``)."""
    if series_key.startswith("instance."):
        parts = series_key.split(".")
        if len(parts) >= 3:
            return parts[1]
    return None


@dataclass
class Finding:
    """One anomaly finding — the FULL §2.7 explainability payload.

    The journal stores this dict shape; the /anomalies endpoint
    projects the FE-frozen LEAN row (``baseline`` flattened to the
    number) via its response model.

    ids are process-local (``anom-<seq>``); they change across
    restarts (documented, accepted).
    """

    id: str
    series_key: str
    time_bucket: str           # the detection bucket (naive-UTC digits)
    window_end: str            # last bucket of the evaluation window
    shape: str
    severity: str
    peak: float
    baseline: Dict[str, Any]   # {method, value, n} — §2.7 verbatim
    observed: float
    deviation: Dict[str, Any]  # {metric, value, threshold}
    contributors: List[Dict[str, Any]]

    def lean_row(self) -> Dict[str, Any]:
        """The FE-frozen ``Anomaly`` row (baseline flattened to a number)."""
        row: Dict[str, Any] = {
            "id": self.id,
            "kind": kind_for_series(self.series_key),
            "t_start": self.time_bucket,
            "t_end": self.window_end,
            "peak": round(float(self.peak), 4),
            "baseline": round(float(self.baseline.get("value") or 0.0), 4),
            "severity": self.severity,
            "series_key": self.series_key,
            "shape": self.shape,
        }
        iid = instance_id_for_series(self.series_key)
        if iid is not None:
            row["instance_id"] = iid
        return row

    def full_dict(self) -> Dict[str, Any]:
        """The §2.7 finding dict (journal / contract-test shape)."""
        d = self.lean_row()
        d.update(
            {
                "time_bucket": self.time_bucket,
                "baseline": dict(self.baseline),
                "observed": float(self.observed),
                "deviation": dict(self.deviation),
                "contributors": list(self.contributors),
            }
        )
        return d


class FindingJournal:
    """Bounded append-only journal — ``deque(maxlen)`` + a BYTE budget.

    Two bounds, both enforced drop-oldest on append:

    * ``maxlen`` (default :data:`JOURNAL_MAXLEN` = 256 entries);
    * ``max_bytes`` (default :data:`JOURNAL_MAX_BYTES` = 256 KB) —
      each finding is MEASURED (``json`` length of its stored dict)
      and the oldest findings are evicted until the total stays
      STRICTLY under the budget.

    Drop-oldest; ids are monotonic within the process. A small lock
    guards append-against-snapshot (the pass runs on the flusher
    thread; the router reads from request threads).
    """

    def __init__(
        self,
        maxlen: int = JOURNAL_MAXLEN,
        max_bytes: int = JOURNAL_MAX_BYTES,
    ) -> None:
        self.maxlen = max(1, int(maxlen))
        self.max_bytes = max(1, int(max_bytes))
        self._buf: Deque[Finding] = deque(maxlen=self.maxlen)
        self._seq = 0
        self._seen: Deque[Tuple[str, str]] = deque(maxlen=self.maxlen)
        self._total_bytes = 0
        self._lock = threading.Lock()

    @staticmethod
    def _measure(finding: Finding) -> int:
        """The finding's stored size — the JSON length of its dict
        (the honest measure; the ~1 KB/finding basis of the §2.7
        arithmetic)."""
        try:
            return len(json.dumps(finding.full_dict(), default=str))
        except (TypeError, ValueError):
            return JOURNAL_MAX_BYTES  # unmeasurable ⇒ evict-first

    def append(self, finding: Finding) -> bool:
        """Append unless the (series, bucket) already has a finding.

        Dedup keeps a sustained deviation from re-journaling on every
        pass. Returns ``True`` when a NEW finding was journaled.
        """
        dedup_key = (finding.series_key, finding.time_bucket)
        with self._lock:
            if dedup_key in self._seen:
                return False
            nb = self._measure(finding)
            # Drop-oldest until BOTH bounds have headroom for the new
            # finding (the total stays STRICTLY < max_bytes).
            while self._buf and (
                len(self._buf) >= self.maxlen
                or self._total_bytes + nb >= self.max_bytes
            ):
                old = self._buf.popleft()
                self._total_bytes -= self._measure(old)
                self._seen_leftpop()
            self._seq += 1
            finding.id = f"anom-{self._seq:06d}"
            self._buf.append(finding)
            self._seen.append(dedup_key)
            self._total_bytes += nb
        return True

    def _seen_leftpop(self) -> None:
        if self._seen:
            self._seen.popleft()

    def snapshot(self) -> List[Finding]:
        with self._lock:
            return list(self._buf)

    def by_id(self, finding_id: str) -> Optional[Finding]:
        with self._lock:
            for f in self._buf:
                if f.id == finding_id:
                    return f
        return None

    def total_bytes(self) -> int:
        """The MEASURED total of the live findings (A1's strict bound:
        ``< max_bytes`` by construction)."""
        with self._lock:
            return self._total_bytes

    def max_bytes_budget(self) -> int:
        """The configured byte budget (262_144 at defaults)."""
        return self.max_bytes


# ── Contributors (task 9 — through the P3 join layer, never forked) ─────────


def _instance_ids_in_window(
    db_engine: Any, t0: str, t1: str, limit: int
) -> List[str]:
    """Distinct instances charged in ``[t0, t1]`` (T2, plain SELECT).

    A minimal DISTINCT read — the per-instance resolution that follows
    goes through the P3 join functions (``nearest_sample_at``),
    which are REUSED, not reimplemented.
    """
    from sqlalchemy import text

    sql = (
        "SELECT DISTINCT instance_id FROM resource_instance_sample "
        "WHERE bucket_ts >= :t0 AND bucket_ts <= :t1 "
        "AND instance_id IS NOT NULL ORDER BY instance_id LIMIT :lim"
    )
    try:
        with db_engine.connect() as conn:
            rows = conn.execute(
                text(sql), {"t0": t0, "t1": t1, "lim": max(1, limit)}
            ).fetchall()
    except Exception as exc:
        logger.warning(
            "[resource_anomaly] instance-id window read degraded: %s", exc
        )
        return []
    return [r[0] for r in rows if r[0]]


def build_contributors(
    db_engine: Any,
    series_key: str,
    t0_aware: datetime,
    t1_aware: datetime,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Rank instances by share of the charged bytes in the window.

    REUSES the P3 join layer: ``nearest_sample_at`` (charged bytes at
    the window edges), ``in_flight_tasks_at`` (each contributor's task
    status — the J2/J3 join). ``share_pct`` sums ≤100; the remainder
    is an explicit ``{"unattributed": true}`` row (honesty rule). Any
    join failure degrades to the honest remainder row — never raises.
    """
    from daemon.services import resource_traceback as rt

    t0_bind = rt._naive_digits(rt.coerce_to_aware_utc(t0_aware))
    t1_bind = rt._naive_digits(rt.coerce_to_aware_utc(t1_aware))
    iids = _instance_ids_in_window(db_engine, t0_bind, t1_bind, limit)
    scoped = instance_id_for_series(series_key)
    if scoped is not None and scoped not in iids:
        iids = [scoped] + [i for i in iids if i != scoped]

    charged: Dict[str, float] = {}
    for iid in iids:
        try:
            s1 = rt.nearest_sample_at(db_engine, iid, t1_aware, tolerance_s=None)
            s0 = rt.nearest_sample_at(db_engine, iid, t0_aware, tolerance_s=None)
        except Exception:
            continue
        b1 = _finite((s1 or {}).get("charged_bytes")) or 0.0
        b0 = _finite((s0 or {}).get("charged_bytes")) or 0.0
        delta = max(0.0, b1 - b0)
        if delta > 0:
            charged[iid] = delta

    contributors: List[Dict[str, Any]] = []
    total = sum(charged.values())
    task_rows: List[Dict[str, Any]] = []
    if charged:
        try:
            task_rows = rt.in_flight_tasks_at(
                db_engine, t1_aware, limit=limit
            )
        except Exception as exc:
            logger.warning(
                "[resource_anomaly] task-status join degraded: %s", exc
            )
            task_rows = []
    status_by_iid = {
        r.get("instance_id"): r for r in task_rows if r.get("instance_id")
    }
    # Shares are computed against the FULL charged total; only the top
    # MAX_CONTRIBUTORS rows are STORED — the truncated tail folds into
    # the explicit unattributed remainder row (the honesty rule's
    # partial-attribution case).
    ranked = sorted(charged.items(), key=lambda kv: kv[1], reverse=True)
    for iid, delta in ranked[:MAX_CONTRIBUTORS]:
        share = (delta / total * 100.0) if total > 0 else 0.0
        tr = status_by_iid.get(iid)
        task_status: Dict[str, Any] = {"status": (tr or {}).get("status") or "unknown"}
        # NOTE (P3 adjudication B): ``status`` here is the task's
        # CURRENT status via the J2 join — a T2 sample-time "idle" is
        # NEVER read as aliveness. ``worker_id`` is omitted (the FE
        # type marks it optional); the P3 join does not expose it and
        # forking the join for it is out of scope.
        agent_id = (tr or {}).get("agent_id")
        row: Dict[str, Any] = {
            "instance_id": iid,
            "share_pct": round(min(100.0, max(0.0, share)), 2),
            "evidence": {"task_status": task_status},
        }
        if agent_id:
            row["agent_id"] = agent_id
        contributors.append(row)

    # Clamp to ≤100 and report the remainder honestly (rounding slack
    # + any truncated-beyond-top-N share + the fully-unattributed case).
    used = sum(c["share_pct"] for c in contributors)
    remainder = max(0.0, 100.0 - min(100.0, used))
    if remainder > 0.01 or not contributors:
        contributors.append(
            {"unattributed": True, "share_pct": round(remainder, 2)}
        )
    return contributors


# ── The anomaly engine (tasks 6-7-9) ────────────────────────────────────────


class AnomalyEngine:
    """State + pass + journal. Built by the facade; the pass runs on
    the FLUSHER thread behind :class:`AnomalyScheduler`'s gate.

    ``workers_provider`` — the OBSERVED worker count (live pool size),
    sampled at ingest/pass time. ``configured_limit_provider`` — the
    CONFIGURED LLM worker limit (the managed setting
    ``limits.llm_concurrency``), read at pass time. Together they form
    the conditioning stratum ``"<observed>|<configured>"``: either
    dimension changing re-baselines the series and can never flag.
    """

    def __init__(
        self,
        *,
        mad_threshold: float = 6.0,
        min_baseline_points: int = 12,
        workers_provider: Optional[Callable[[], Optional[int]]] = None,
        configured_limit_provider: Optional[Callable[[], Any]] = None,
        configured_limit_ttl_s: float = 300.0,
        db_engine: Any = None,
        store: Optional[SeriesStateStore] = None,
        journal: Optional[FindingJournal] = None,
        max_contributor_candidates: int = 50,
    ) -> None:
        self.mad_threshold = float(mad_threshold)
        self.min_baseline_points = max(1, int(min_baseline_points))
        self._workers_provider = workers_provider
        self._limit_provider = configured_limit_provider
        self._limit_ttl_s = max(0.0, float(configured_limit_ttl_s))
        self._limit_cache: Optional[Tuple[float, Any]] = None
        self.db_engine = db_engine
        self.store = store or SeriesStateStore()
        self.journal = journal or FindingJournal()
        self._max_contributor_candidates = max(1, int(max_contributor_candidates))
        self._current_stratum = "?|?"
        self._last_cpu_usec: Optional[Tuple[str, float]] = None
        self._boot_replayed = False
        self.counters: Dict[str, float] = {
            "passes": 0,
            "missed_anomaly_cycles": 0,
            "last_pass_duration_s": 0.0,
            "findings_emitted": 0,
        }

    # ── Conditioning stratum ──────────────────────────────────────────

    def _sample_workers(self) -> Optional[int]:
        if self._workers_provider is None:
            return None
        try:
            w = self._workers_provider()
        except Exception:
            return None
        try:
            return int(w) if w is not None else None
        except (TypeError, ValueError):
            return None

    def _sample_configured_limit(self) -> Any:
        """Cached read of the configured-limit provider.

        The provider is invoked on every ``ingest`` (~1/min on the
        flusher thread) AND on every pass, so a fresh DB query per call
        over 60 s × multiple providers quickly adds up. The cache holds
        the LAST read value for ``configured_limit_ttl_s`` (default
        300 s = anomaly_interval_s / 2 at the default 600 s cadence).
        Mid-window behavior: if the operator flips the limit during
        the TTL window, in-flight buckets carry the OLD value (the
        documented "label mid-window buckets with the pass-time
        value"); the NEXT refresh (after TTL expiry) sees the NEW
        value. The stratum switch then re-baselines on its own
        (the cold-stratum guard). Read-only semantics preserved.
        """
        if self._limit_provider is None:
            return None
        now = time.monotonic()
        cached = self._limit_cache
        if cached is not None and now < cached[0]:
            return cached[1]
        try:
            v = self._limit_provider()
        except Exception:
            return None
        self._limit_cache = (now + self._limit_ttl_s, v)
        return v

    def _refresh_stratum(self) -> str:
        w = self._sample_workers()
        lim = self._sample_configured_limit()
        self._current_stratum = f"{w if w is not None else '?'}|{lim if lim is not None else '?'}"
        return self._current_stratum

    # ── Ingest (bucketed inputs only at the store boundary) ───────────

    def ingest(
        self,
        minute_rows: Optional[List[Dict[str, Any]]] = None,
        instance_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Fold one flush window's bucketed rows into the store.

        ``minute_rows`` are the flusher's minute roll-up rows (daemon
        series); ``instance_rows`` are the T2 rows drained that window
        (downsampled at the store boundary like every other input).
        Never raises — one bad row never kills the ingest.
        """
        stratum = self._refresh_stratum()
        for row in minute_rows or []:
            try:
                self._ingest_minute_row(row, stratum)
            except Exception as exc:
                logger.debug(
                    "[resource_anomaly] minute-row ingest skipped: %s", exc
                )
        for row in instance_rows or []:
            try:
                self._ingest_instance_row(row, stratum)
            except Exception as exc:
                logger.debug(
                    "[resource_anomaly] instance-row ingest skipped: %s", exc
                )

    def _ingest_minute_row(self, row: Dict[str, Any], stratum: str) -> None:
        ts = row.get("bucket_ts")
        if not ts:
            return
        rss = _finite(row.get("rss_kb"))
        if rss is not None:
            self.store.observe("daemon.rss_kb", ts, rss, stratum)
        threads = _finite(row.get("threads"))
        if threads is not None:
            self.store.observe("daemon.threads", ts, threads, stratum)
        fds = _finite(row.get("fds"))
        if fds is not None:
            self.store.observe("daemon.fds", ts, fds, stratum)
        anon = _finite(row.get("anon_kb"))
        if anon is not None:
            # B2 — PROVENANCE SPLIT. The daemon's OWN anonymous pool
            # rides ``daemon.anon_kb``. It used to be observed under
            # ``cgroup.anon_kb``, which made the daemon RSS and the
            # "cgroup" value the SAME number under two names on Darwin
            # (the macOS anonymous pool is not directly measurable, so
            # the daemon reader sources ``anon_kb`` from RSS there).
            # ``cgroup.anon_kb`` is now bound to the cgroup v2
            # ``memory.anon`` column ONLY.
            self.store.observe("daemon.anon_kb", ts, anon, stratum)
        cg_anon = _finite(row.get("cg_anon_kb"))
        if cg_anon is None:
            # The live roll-up path carries the cgroup value in MB
            # (``cg_anon_mb``); the boot-replay path carries the KB
            # column straight from the minute tier.
            cg_anon_mb = _finite(row.get("cg_anon_mb"))
            if cg_anon_mb is not None:
                cg_anon = cg_anon_mb * 1024.0
        if cg_anon is not None:
            self.store.observe("cgroup.anon_kb", ts, cg_anon, stratum)
        # cpu_pct — derived from the CUMULATIVE usec counters between
        # consecutive minute buckets (the /history endpoint's own
        # delta rule). First bucket / counter reset ⇒ skipped.
        user = _finite(row.get("cpu_user_usec"))
        sys_ = _finite(row.get("cpu_sys_usec"))
        if user is not None and sys_ is not None:
            total = user + sys_
            prev = self._last_cpu_usec
            self._last_cpu_usec = (ts, total)
            if prev is not None:
                prev_ts, prev_total = prev
                d0 = _parse_bucket_ts(prev_ts)
                d1 = _parse_bucket_ts(ts)
                if d0 is not None and d1 is not None:
                    dt = (d1 - d0).total_seconds()
                    delta = total - prev_total
                    if dt > 0 and delta >= 0:
                        pct = (delta / (dt * 1_000_000.0)) * 100.0
                        self.store.observe(
                            "daemon.cpu_pct", ts, pct, stratum
                        )

    def _ingest_instance_row(self, row: Dict[str, Any], stratum: str) -> None:
        iid = row.get("instance_id")
        ts = row.get("bucket_ts")
        if not iid or not ts:
            return
        charged = _finite(row.get("charged_bytes"))
        if charged is not None:
            self.store.observe(
                f"instance.{iid}.charged_kb", ts, charged / 1024.0, stratum
            )
        child = _finite(row.get("child_rss_kb"))
        if child is not None:
            self.store.observe(
                f"instance.{iid}.child_rss_kb", ts, child, stratum
            )

    # ── The pass (task 6) ─────────────────────────────────────────────

    def run_pass(self) -> int:
        """One detection pass over all series. Returns findings appended.

        Per-series failure isolation (R4): one bad series never kills
        the pass. Cold-start quiet until ``min_baseline_points``.
        """
        emitted = 0
        stratum = self._refresh_stratum()
        for key in self.store.series_keys():
            try:
                emitted += self._pass_series(key, stratum)
            except Exception as exc:
                logger.warning(
                    "[resource_anomaly] pass failed for %r: %s", key, exc
                )
        self.counters["passes"] += 1
        self.counters["findings_emitted"] += emitted
        return emitted

    def _pass_series(self, key: str, stratum: str) -> int:
        states = self.store.states(key)
        if len(states) < self.min_baseline_points:
            return 0  # cold-start quiet
        last = states[-1]
        # The pass evaluates the series under the stratum of its own
        # recent DATA (the last bucket's conditioning key) — this is
        # what makes a worker-count / configured-limit change RE-
        # BASELINE: new-stratum buckets carry few same-stratum points,
        # so the cold-start guard keeps the pass quiet until the new
        # stratum accumulates its own baseline. The pass-time sampled
        # stratum (``stratum`` arg) labels the engine state; the data's
        # stratum decides detection.
        eval_stratum = last.workers
        # Baseline: same-stratum rolling median first; DoW×hour fallback
        # for periodic workloads when the stratum's history is thin.
        base_val, base_n = rolling_baseline(states, eval_stratum)
        if base_n >= self.min_baseline_points:
            baseline = {
                "method": "rolling_median",
                "value": base_val,
                "n": base_n,
            }
        else:
            dh = _dow_hour(last.ts)
            dh_val, dh_n = (
                dow_hour_baseline(self.store, key, eval_stratum, dh)
                if dh
                else (0.0, 0)
            )
            if dh_n < DOW_HOUR_MIN_N:
                return 0  # quiet — cold start, not an error
            baseline = {"method": "dow_hour_mean", "value": dh_val, "n": dh_n}

        fired_index: Optional[int] = None
        deviation: Optional[Dict[str, Any]] = None

        spike_idx = detect_spike(states, self.mad_threshold)
        if spike_idx is not None:
            fired_index = spike_idx
            z = mad_z(states[spike_idx]) or 0.0
            deviation = {
                "metric": "mad_z",
                "value": round(z, 4),
                "threshold": self.mad_threshold,
            }
        else:
            # Sustained-shift detectors first (they cite cusum_k); the
            # newest-bucket mad_z branch runs LAST — with no future
            # data it cannot show recovery, so it belongs to the
            # conservative fallback path.
            step = detect_step(states, eval_stratum)
            ramp = detect_ramp(states)
            leak = detect_leak(states)
            z_last = mad_z(last)
            if step is not None:
                fired_index = step[0]
                deviation = {
                    "metric": "cusum_k",
                    "value": round(step[1], 4),
                    "threshold": CUSUM_THRESHOLD,
                }
            elif ramp is not None:
                fired_index = ramp[0]
                deviation = {
                    "metric": "cusum_k",
                    "value": round(ramp[1], 4),
                    "threshold": CUSUM_THRESHOLD,
                }
            elif leak is not None:
                fired_index = leak[0]
                deviation = {
                    "metric": "cusum_k",
                    "value": round(leak[1], 4),
                    "threshold": CUSUM_THRESHOLD,
                }
            elif z_last is not None and z_last >= self.mad_threshold:
                fired_index = len(states) - 1
                deviation = {
                    "metric": "mad_z",
                    "value": round(z_last, 4),
                    "threshold": self.mad_threshold,
                }

        if fired_index is None or deviation is None:
            return 0

        shape, used_fallback = classify_shape(
            states,
            fired_index=fired_index,
            workers=eval_stratum,
            mad_threshold=self.mad_threshold,
        )
        if used_fallback:
            logger.warning(
                "[resource_anomaly] %s: detector fired (%s) with no "
                "matching shape rule — conservative spike fallback",
                key,
                deviation["metric"],
            )

        window = states[fired_index:]
        finding = Finding(
            id="",  # assigned by the journal
            series_key=key,
            time_bucket=states[fired_index].ts,
            window_end=last.ts,
            shape=shape,
            severity=severity_band(
                deviation["value"], deviation["threshold"]
            ),
            peak=max(b.value for b in window),
            baseline=baseline,
            observed=float(states[fired_index].value),
            deviation=deviation,
            contributors=self._resolve_contributors(
                key, states[fired_index].ts, last.ts
            ),
        )
        if self.journal.append(finding):
            return 1
        return 0

    def _resolve_contributors(
        self, series_key: str, t0: str, t1: str
    ) -> List[Dict[str, Any]]:
        """Contributors through the P3 join layer; honest remainder on
        any failure (degrade, never raise, never block the pass)."""
        if self.db_engine is None:
            return [{"unattributed": True, "share_pct": 100.0}]
        t0_dt = _parse_bucket_ts(t0)
        t1_dt = _parse_bucket_ts(t1)
        if t0_dt is None or t1_dt is None:
            return [{"unattributed": True, "share_pct": 100.0}]
        try:
            return build_contributors(
                self.db_engine,
                series_key,
                t0_dt,
                t1_dt,
                limit=self._max_contributor_candidates,
            )
        except Exception as exc:
            logger.warning(
                "[resource_anomaly] contributor resolution degraded: %s", exc
            )
            return [{"unattributed": True, "share_pct": 100.0}]

    # ── Restart-recompute (task 7) ────────────────────────────────────

    def replay_minute_rows(self, rows: List[Dict[str, Any]]) -> None:
        """Replay minute-tier rows through the ingest path.

        Deterministic — the same rows through the same store produce
        the same buckets (A5's equality basis). Same-stride re-ingest
        replaces (idempotent), so a replay can never double-count.
        """
        self.ingest(minute_rows=rows, instance_rows=None)

    def replay_from_db(self) -> int:
        """Cold-boot warm: read the retained DB minute tier through the
        detector pipeline. Runs once, lazily, on the first pass (the
        tables exist by then; a first-boot missing table is benign).
        Returns the row count replayed. NEVER raises.
        """
        if self._boot_replayed or self.db_engine is None:
            return 0
        self._boot_replayed = True
        from sqlalchemy import text

        sql = (
            "SELECT bucket_ts, rss_kb, cpu_user_usec, cpu_sys_usec, "
            "threads, fds, anon_kb, cg_anon_kb FROM resource_daemon_bucket "
            "WHERE interval_s = 60 ORDER BY bucket_ts DESC LIMIT :lim"
        )
        try:
            with self.db_engine.connect() as conn:
                rows = conn.execute(
                    text(sql), {"lim": self.store.window_buckets * 4}
                ).fetchall()
        except Exception as exc:
            logger.info(
                "[resource_anomaly] minute-tier replay unavailable "
                "(first boot benign): %s",
                exc,
            )
            return 0
        # DESC was used so the LIMIT selects the MOST-RECENT
        # window_buckets*4 rows (not the OLDEST, which would warm the
        # store on stale ~14-day-old data and FP the live level on the
        # first pass after restart). Reverse to ASC time-order before
        # the replay ingests, so the store's same-stride re-ingest
        # replacement (idempotent pop+re-observe) walks the window in
        # time order.
        rows = list(rows)
        rows.reverse()
        out = [
            {
                "bucket_ts": r[0],
                "rss_kb": r[1],
                "cpu_user_usec": r[2],
                "cpu_sys_usec": r[3],
                "threads": r[4],
                "fds": r[5],
                "anon_kb": r[6],
                "cg_anon_kb": r[7],
            }
            for r in rows
        ]
        self.replay_minute_rows(out)
        logger.info(
            "[resource_anomaly] restart-recompute replayed %d minute-tier "
            "rows (ids regenerate; finding ids are process-local)", len(out)
        )
        return len(out)

    # ── Reads for the router ──────────────────────────────────────────

    def list_anomalies(self, window_s: int, limit: int) -> List[Dict[str, Any]]:
        """Lean rows for ``GET /anomalies`` — newest-window filtered,
        deterministically ordered ``(t_start, series_key)``.

        Ordering + stable ids are the FE's marker-stability contract:
        markers must not reshuffle between 8 s polls.
        """
        cutoff_epoch: Optional[float] = None
        now = _parse_bucket_ts(_fmt_bucket_ts(datetime.now(timezone.utc)))
        if now is not None:
            cutoff_epoch = now.timestamp() - max(0, int(window_s))
        rows: List[Tuple[str, str, Dict[str, Any]]] = []
        for f in self.journal.snapshot():
            if cutoff_epoch is not None:
                ts = _parse_bucket_ts(f.time_bucket)
                if ts is None or ts.timestamp() < cutoff_epoch:
                    continue
            rows.append((f.time_bucket, f.series_key, f.lean_row()))
        rows.sort(key=lambda r: (r[0], r[1]))
        return [r[2] for r in rows[: max(1, int(limit))]]

    def build_evidence(self, finding_id: str) -> Optional[Dict[str, Any]]:
        """The §2.8 evidence payload for one finding — re-derives
        ``samples`` / ``top_pids_per_bucket`` / ``children_delta`` from
        the retained bucket window + T2/T3 reads (P3 joins). Unknown
        id ⇒ ``None`` (the router maps to 404). NEVER raises — an
        internal failure degrades to empty sections."""
        f = self.journal.by_id(finding_id)
        if f is None:
            return None
        anomaly = f.lean_row()
        samples = [
            {"t": b.ts, "value": round(b.value, 4)}
            for b in self.store.states(f.series_key)
        ]
        top_pids: List[Dict[str, Any]] = []
        children_delta: List[Dict[str, Any]] = []
        t0_dt = _parse_bucket_ts(f.time_bucket)
        t1_dt = _parse_bucket_ts(f.window_end)
        if self.db_engine is not None and t0_dt is not None and t1_dt is not None:
            try:
                from daemon.services import resource_traceback as rt

                # chart_render suspected first (the 8.3 GB-class).
                agg = rt.chart_render_aggregate(self.db_engine, t1_dt)
                if agg.get("count"):
                    top_pids.append(agg)
                peak_dt = max(
                    self.store.states(f.series_key),
                    key=lambda b: b.value,
                    default=None,
                )
                at = (peak_dt and _parse_bucket_ts(peak_dt.ts)) or t1_dt
                kids = rt.children_at_t(
                    self.db_engine, at, limit=10
                )
                for k in kids:
                    row = dict(k)
                    for ts_key in ("spawned_at", "exited_at"):
                        if row.get(ts_key) is not None:
                            row[ts_key] = _fmt_bucket_ts(row[ts_key])
                    if row.get("instance_id") is None and row.get("pid"):
                        try:
                            resolved = rt.resolve_pid_instance(
                                self.db_engine, int(row["pid"])
                            )
                            if resolved:
                                row["instance_id"] = resolved.get("instance_id")
                        except Exception:
                            pass
                    top_pids.append(row)
                # Children delta: alive at t1 but NOT at t0 (spawned
                # in-window).
                pids_at_t0 = {
                    k.get("pid")
                    for k in rt.children_at_t(self.db_engine, t0_dt, limit=50)
                }
                children_delta = [
                    {
                        "pid": k.get("pid"),
                        "kind": k.get("kind"),
                        "name": k.get("name"),
                        "instance_id": k.get("instance_id"),
                        "spawned_at": (
                            _fmt_bucket_ts(k["spawned_at"])
                            if k.get("spawned_at") is not None
                            else None
                        ),
                    }
                    for k in rt.children_at_t(self.db_engine, t1_dt, limit=50)
                    if k.get("pid") not in pids_at_t0
                ]
            except Exception as exc:
                logger.warning(
                    "[resource_anomaly] evidence T2/T3 re-derivation "
                    "degraded: %s",
                    exc,
                )
        return {
            "anomaly": anomaly,
            "samples": samples,
            "top_pids_per_bucket": top_pids,
            "children_delta": children_delta,
            "contributors": [dict(c) for c in f.contributors],
        }

    def findings_in_window(
        self, t0_aware: datetime, t1_aware: datetime, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Lean findings whose bucket falls in ``[t0, t1]`` — the P3
        ``/traceback`` ``findings[]`` fill (D5).

        Ordering + stability match :meth:`list_anomalies` — sort by
        ``(time_bucket, series_key)`` ASC, then truncate to keep the
        LAST ``limit`` (= newest). The previous journal-order
        iteration with early break dropped the NEWEST entries under a
        full journal and produced a non-deterministic order across
        8 s polls (markers reshuffled between consecutive reads).
        """
        t0n = _parse_bucket_ts(_fmt_bucket_ts(t0_aware))
        t1n = _parse_bucket_ts(_fmt_bucket_ts(t1_aware))
        if t0n is None or t1n is None:
            return []
        matched: List[Tuple[str, str, Dict[str, Any]]] = []
        for f in self.journal.snapshot():
            ts = _parse_bucket_ts(f.time_bucket)
            if ts is not None and t0n <= ts <= t1n:
                matched.append(
                    (f.time_bucket, f.series_key, f.lean_row())
                )
        # Sort ASC by (t_start, series_key) — matches list_anomalies'
        # ordering. Truncate from the END (= keep the newest ``limit``,
        # not the oldest) so a full journal does NOT drop recent
        # findings.
        matched.sort(key=lambda r: (r[0], r[1]))
        cap = max(1, int(limit))
        if len(matched) > cap:
            matched = matched[-cap:]
        return [r[2] for r in matched]


# ── The configured-limit provider (managed-setting shape, read-only) ────────


def make_configured_limit_provider(db_engine: Any) -> Callable[[], Any]:
    """Read ``limits.llm_concurrency`` over the 4-tier resolution ladder.

    The managed setting (DB row > env > yaml > default) is consumed
    READ-ONLY at pass time — never written (phase4-plan structural
    decision 5). Any failure ⇒ ``None`` (the stratum degrades to the
    observed dimension alone; fail-open, never blocks the pass).
    """

    def _read() -> Any:
        try:
            from daemon.services import settings_service
            from daemon.services.settings_service import SettingsService

            snapshot = settings_service.get_settings_resolution_snapshot()
            overrides = SettingsService(db_engine).get_overrides()
            return settings_service.resolve(
                "limits.llm_concurrency", snapshot, overrides
            )["value"]
        except Exception as exc:
            logger.debug(
                "[resource_anomaly] configured-limit read degraded: %s", exc
            )
            return None

    return _read


# ── The scheduler gate (task 6 — piggybacks the FLUSHER thread) ─────────────


class AnomalyScheduler:
    """Monotonic-interval gate + skip-on-overrun, called by the flusher.

    Contract:

    * ingest runs EVERY window (cheap, bounded — keeps the store warm);
    * the pass fires only when ``interval_s`` elapsed since the last
      pass START (monotonic — stable cadence);
    * a pass measuring over ``budget_s`` marks the NEXT due cycle as
      skipped and bumps ``counters["missed_anomaly_cycles"]`` (one
      increment per skipped cycle — sampling and flushing are never
      delayed);
    * never raises — an engine failure logs and moves on.
    """

    def __init__(
        self,
        engine: AnomalyEngine,
        *,
        interval_s: int,
        budget_s: float = ANOMALY_PASS_BUDGET_S,
        wall_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.engine = engine
        self.interval_s = max(1, int(interval_s))
        self.budget_s = float(budget_s)
        self._wall_clock = wall_clock
        self._last_run: float = wall_clock()
        self._skip_next = False

    def __call__(
        self,
        minute_rows: Optional[List[Dict[str, Any]]] = None,
        instance_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        try:
            self.engine.ingest(minute_rows, instance_rows)
        except Exception as exc:
            logger.warning(
                "[resource_anomaly] ingest failed (pass continues): %s", exc
            )
        now = self._wall_clock()
        if now - self._last_run < self.interval_s:
            return
        if self._skip_next:
            # Overrun consequence: skip THIS due cycle (the overrun
            # itself was already counted when detected).
            self._skip_next = False
            self._last_run = now
            logger.warning(
                "[resource_anomaly] pass skipped this cycle (previous "
                "pass over budget %.2fs); sampling/flush unaffected",
                self.budget_s,
            )
            return
        self._last_run = now
        try:
            self.engine.replay_from_db()  # first-pass cold-boot warm
            t0 = self._wall_clock()
            self.engine.run_pass()
            duration = self._wall_clock() - t0
        except Exception as exc:
            logger.warning("[resource_anomaly] pass failed: %s", exc)
            return
        self.engine.counters["last_pass_duration_s"] = round(duration, 6)
        if duration > self.budget_s:
            self.engine.counters["missed_anomaly_cycles"] += 1
            self._skip_next = True
            logger.warning(
                "[resource_anomaly] pass over self-cost budget: %.3fs > "
                "%.3fs — next cycle will be skipped (missed=%d)",
                duration,
                self.budget_s,
                self.engine.counters["missed_anomaly_cycles"],
            )


__all__ = [
    "ANOMALY_PASS_BUDGET_S",
    "CUSUM_THRESHOLD",
    "DEFAULT_MAX_SERIES",
    "DOW_HOUR_SLOTS",
    "JOURNAL_MAX_BYTES",
    "JOURNAL_MAXLEN",
    "STATE_BUCKET_S",
    "STATE_FLOATS",
    "STATE_WINDOW_S",
    "AnomalyEngine",
    "AnomalyScheduler",
    "BucketState",
    "Finding",
    "FindingJournal",
    "SeriesStateStore",
    "build_contributors",
    "classify_shape",
    "detect_leak",
    "detect_ramp",
    "detect_spike",
    "detect_step",
    "kind_for_series",
    "mad_z",
    "make_configured_limit_provider",
    "severity_band",
]
