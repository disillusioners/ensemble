"""``sampler`` — the resource-monitor sampling thread.

Architecture §2.1 (binding):

  * ONE ``threading.Thread(daemon=True)``. NOT an asyncio task.
  * Tick loop with ``time.monotonic`` budget. ``tick_t > interval``
    ⇒ skip next tick + ``missed_samples++`` + demote one ladder
    level; re-promote after N clean ticks (hysteresis).
  * EMA self-throttle: EMA tick cost > ``self_cpu_budget_pct`` % of
    interval ⇒ double interval + deep-scan off.
  * 1/10-cadence PSS (smaps_rollup), env-gated by ``detail_level``.
  * Captures ``sample_cost_ms``, ``sampler_lag_ms``, ``missed_samples``
    (alarmable; D12).
  * Sampler NEVER touches the DB — writes ONLY the ring.

Self-cost discipline (D12):

  * tick target < 5 ms (median over N synthetic ticks).
  * HARD gate: max tick < 50 ms.
  * EMA tick cost > 1 % of interval ⇒ double interval, deep-scan off.
  * Memory floor < 20 MB (test-level footprint assertion).

Ladder (architecture §2.8):

  * Levels:
    - ``deep``        — full cgroup + per-child cgroup + PSS
    - ``full``        — full cgroup + per-child /proc + PSS every 10th
    - ``reduced``     — daemon-only /proc, no PSS
    - ``minimal``     — daemon-only /proc, no children, no PSS
  * Start at ``full`` (the default; ``detail_level='auto'``).
  * Demote one step on overrun; re-promote after
    ``RE_PROMOTE_CLEAN_TICKS`` consecutive clean ticks (hysteresis).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional

from . import charge as _charge
from .deep_scan import DeepScanResult, DeepScanner
from .pid_registry import PidRegistry, get_pid_registry, read_proc_stat_field
from .proc_readers import (
    ChildSnapshot,
    CgroupSnapshot,
    DaemonProcSnapshot,
    ProcSnapshot,
    read_child_snapshot,
    read_proc_snapshot,
)
from .ring import TierRing

logger = logging.getLogger(__name__)


# ── Ladder levels ───────────────────────────────────────────────────────────


# Ladder ordered from deepest to shallowest.
LADDER_LEVELS = ("deep", "full", "reduced", "minimal")
LADDER_DEFAULT = "full"
RE_PROMOTE_CLEAN_TICKS = 3  # hysteresis: N clean ticks before re-promote

# Detail-level resolution: how often we sample PSS (smaps_rollup).
# ``deep`` / ``full``: 1/10 cadence. ``reduced`` / ``minimal``: never.
PSS_CADENCE = 10


# ── P2 T10 integration constants ────────────────────────────────────────────

#: The single overflow sentinel row's ``instance_id`` (D12). Rows beyond
#: the ≤64/tick cap collapse into this one aggregate row.
OVERFLOW_SENTINEL = "__overflow__"

#: Hard cap on per-child ``/proc`` reads per tick (self-cost tighten,
#: dispatch brief). The registry snapshot can be longer; beyond this cap
#: the remaining entries are skipped this tick (they re-appear next tick
#: — the /live children view then reflects the walked prefix, honestly).
CHILD_READ_CAP = 64

#: Rows kept for the /live children view (§2.8 children[] ≤50).
LIVE_CHILDREN_CAP = 50

#: Rows kept for the /live instances view (§2.8 instances[] ≤50).
LIVE_INSTANCES_CAP = 50

#: Bounded maxlen for the sampler-internal instance-row ring when the
#: facade does not pass one. 8 ticks × (cap + 1 sentinel) covers the
#: flush window handoff many times over; the DB is the durable store.
DEFAULT_INSTANCE_RING_TICKS = 8

#: D14/AC11 — the EXACT key set a T2 ``resource_instance_sample`` row
#: may carry. Ids, counters, durations, scalars ONLY. The row builder
#: below constructs rows exclusively from this set, and the test pack
#: asserts no canary / tool-arg / message-content key ever appears.
ALLOWED_T2_ROW_KEYS = frozenset(
    {
        "bucket_ts",  # stamped by the flusher, not the sampler
        "instance_id",
        "agent_id",
        "status",
        "scan_kind",
        "charged_bytes",
        "deep_bytes",
        "graph_cache_present",
        "in_flight_tasks",
        "all_state_messages_len",
        "child_rss_kb",
        "child_cpu_user_usec",
        "cpu_share_usec_est",
        "estimated",
    }
)


# ── Sampler self-cost counters ─────────────────────────────────────────────


@dataclass
class SamplerCounters:
    """Self-cost counters written into the ring + /self-cost endpoint.

    Updated at the END of every tick (post-read, post-write). The
    counters are RING-FACING — never touched by the flusher; the
    /self-cost endpoint reads a live copy off the sampler (the
    sampler publishes a snapshot on every tick).
    """

    # EMA of tick cost (seconds).
    ema_tick_cost_s: float = 0.0
    # EMA decay weight (0 < alpha < 1). Higher = more responsive to
    # the latest tick; 0.3 matches a 3-tick moving window.
    ema_alpha: float = 0.3
    # Ticks that PRODUCED a sample since thread start. A tick that
    # overran, or whose platform read failed, is NOT counted here —
    # it lands in ``ticks_skipped`` / ``missed_samples`` instead, so
    # this counter (surfaced as the ``samples`` field of
    # ``/api/resources/self-cost``) never over-reports.
    ticks: int = 0
    # Skipped ticks — an overrun OR a structural read failure (any tick
    # that produced no sample).
    ticks_skipped: int = 0
    # ``missed_samples`` counter — bumped every time we skip a tick
    # for overrun or for any structural read failure.
    missed_samples: int = 0
    # Cumulative CPU seconds consumed by the sampler thread.
    cumulative_cpu_s: float = 0.0
    # Per-tick timing samples for the regression test.
    last_tick_cost_ms: float = 0.0
    last_tick_lag_ms: float = 0.0
    # Active ladder level.
    ladder_level: str = LADDER_DEFAULT
    # Whether EMA self-throttle has fired.
    self_throttled: bool = False
    # Monotonic clock timestamp of the LAST overrun (or None).
    last_overrun_monotonic: Optional[float] = None
    # Monotonic clock timestamp of sampler construction (uptime anchor).
    started_at_monotonic: float = 0.0
    # Previous tick's cumulative CPU usec (for cpu_pct delta). None on
    # the very first tick — the delta is meaningless then.
    prev_cpu_usec_total: Optional[int] = None


# ── Sampler thread ─────────────────────────────────────────────────────────


class Sampler:
    """ONE threading.Thread(daemon=True) sampler.

    The sampler reads /proc + cgroup v2 once per tick (architecture
    §2.1) and writes the result into the raw-tier ring. It never
    touches the DB.

    Lifecycle:

      * ``start()`` — spawn the thread (daemon=True; never blocks
        the loop).
      * ``stop()`` — set the stop Event; the thread exits on the
        next tick boundary. Join happens at the lifespan level
        (T7) on a detached thread, NEVER on the loop.

    The sampler is test-friendly: ``run_once()`` advances the
    sampler by a single tick WITHOUT spawning the thread. T9 / T10
    drive ticks via ``run_once()`` to keep the test hermetic
    (no background threads, deterministic timing).
    """

    def __init__(
        self,
        *,
        sample_seconds: int,
        self_cpu_budget_pct: float,
        self_rss_budget_mb: int,
        detail_level: str,
        proc_root: Path | str = "/proc",
        cgroup_root: Path | str = "/sys/fs/cgroup",
        ring: TierRing,
        counters: Optional[SamplerCounters] = None,
        child_pids_provider: Optional[Callable[[], list[int]]] = None,
        wall_clock: Callable[[], float] = time.monotonic,
        # ── P2 T10 integration (attribution) ──
        instance_ring: Optional[TierRing] = None,
        instance_cap: int = 64,
        pid_registry: Optional[PidRegistry] = None,
        deep_scanner: Optional[DeepScanner] = None,
        graph_cache_provider: Optional[
            Callable[[], Mapping[str, tuple]]
        ] = None,
        graph_tasks_provider: Optional[Callable[[], Mapping[str, object]]] = None,
        state_message_counts_provider: Optional[
            Callable[[], Mapping[str, int]]
        ] = None,
    ) -> None:
        self.sample_seconds = sample_seconds
        self.self_cpu_budget_pct = self_cpu_budget_pct
        self.self_rss_budget_mb = self_rss_budget_mb
        self.detail_level = detail_level
        self.proc_root = Path(proc_root)
        self.cgroup_root = Path(cgroup_root)
        self.ring = ring
        self.counters = counters or SamplerCounters()
        self._child_pids_provider = child_pids_provider
        self._wall_clock = wall_clock  # injectable for tests

        # ── P2 T10 — attribution wiring ────────────────────────────────
        # Per-tick T2 rows land here; the flusher drains the SAME ring
        # inside its single engine.begin() window. When no ring is
        # passed (bare-sampler tests), an internal bounded ring keeps
        # the /live latest-tick view working with zero DB coupling.
        # NOTE: explicit None check — TierRing defines __len__, so an
        # empty ring is falsy and ``or`` would silently swap it out.
        self.instance_ring = (
            instance_ring
            if instance_ring is not None
            else TierRing(
                tier="instance",
                maxlen=max(
                    2,
                    DEFAULT_INSTANCE_RING_TICKS
                    * (min(instance_cap, 64) + 1),
                ),
            )
        )
        # D12 — ≤64 instance rows/tick (config ``instance_cap``, env
        # ``RESOURCE_MONITOR_INSTANCE_CAP``; the config layer clamps
        # 1..64 — clamped here too so a bare-constructed sampler stays
        # inside the contract).
        self.instance_cap = max(1, min(int(instance_cap), 64))
        self._pid_registry = pid_registry or get_pid_registry()
        self._deep_scanner = deep_scanner or DeepScanner()
        # Injectable manager/messaging state providers (D5 cheap
        # counts + the deep-scan instances mapping). All optional —
        # absent ⇒ the related columns degrade to None/False and the
        # deep scan is a no-op (the P1 bare-sampler behavior).
        self._graph_cache_provider = graph_cache_provider
        self._graph_tasks_provider = graph_tasks_provider
        self._state_message_counts_provider = state_message_counts_provider

        # Latest-tick views for the /live handler (§2.8). The router
        # NEVER samples on the request path (self-cost discipline);
        # it reads these RAM copies.
        self.last_instance_rows: list[dict] = []
        self.last_instance_view: list[dict] = []
        self.last_children_view: list[dict] = []
        # Per-pid child CPU delta anchor: pid -> (cpu_user_usec, monotonic).
        self._child_cpu_prev: dict[int, tuple[int, float]] = {}
        # This tick's T1 attribution columns (set by _sample_attribution).
        self._attrib: dict = {}

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()  # counters + ring write guard

        # EMA self-throttle: when the EMA tick cost climbs above the
        # budget, double the interval and disable deep scans.
        self._effective_interval_s = float(sample_seconds)
        self._deep_scan_enabled = True

        # Uptime anchor (set at construction; the sampler is built in
        # the lifespan immediately after the manager — so this is a
        # close proxy for daemon uptime).
        self.counters.started_at_monotonic = self._wall_clock()

        # Hysteresis counter for re-promotion.
        self._clean_ticks = 0

        # F3-style one-shot guard: the first structural read failure
        # logs the cause once; later failures stay silent (one clear
        # line ties the degradation to its cause — the flusher's
        # ``_missing_table_warned`` precedent).
        self._no_snapshot_warned = False

    # ── Public lifecycle ───────────────────────────────────────────────

    def start(self) -> None:
        """Start the sampler thread. Idempotent: a no-op if already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="resource-monitor-sampler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the thread to stop. Non-blocking."""
        self._stop_event.set()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── Single-tick driver (used by tests AND the loop) ───────────────

    def run_once(self) -> Optional[ProcSnapshot]:
        """Run a single tick. Returns the snapshot or None on read failure.

        ``run_once()`` is the public seam: the loop calls it once per
        interval, and the tests (T9 / T10) drive it deterministically
        to validate ladder / EMA / overrun semantics.

        Tick semantics (D4 / D12):

          1. Measure ``tick_t`` with ``time.monotonic`` budget.
          2. If the platform read produced NO snapshot: skip +
             ``missed_samples++`` + ``ticks_skipped++`` + return
             ``None``. The tick is NOT counted as a sample.
          3. If ``tick_t > interval``: skip + ``missed_samples++`` +
             demote one ladder level. Return ``None``.
          4. Otherwise: read /proc + cgroup (subject to ladder level),
             update EMA tick cost, write the row into the ring,
             re-promote after N clean ticks (hysteresis).
          5. If EMA tick cost > ``self_cpu_budget_pct`` % of
             ``effective_interval_s``: double interval + disable
             deep scans.
        """
        interval = self._effective_interval_s
        tick_start = self._wall_clock()
        expected_next = tick_start + interval

        # Ladder level — determined at tick start. The level
        # determines PSS cadence + children walk.
        level = self.counters.ladder_level
        if level not in LADDER_LEVELS:
            level = LADDER_DEFAULT

        # Children walk depends on level: deep / full walks children.
        walk_children = level in ("deep", "full")
        # PSS: 1/10 cadence under deep / full.
        pss_kb: Optional[int] = None
        if level in ("deep", "full"):
            if (self.counters.ticks % PSS_CADENCE) == 0:
                pss_kb = self._read_pss_rollup()

        child_pids = (
            list(self._child_pids_provider())
            if (walk_children and self._child_pids_provider is not None)
            else None
        )

        snap = read_proc_snapshot(
            self.proc_root,
            self.cgroup_root,
            pss_kb=pss_kb,
            child_pids=child_pids,
        )

        if snap is None:
            # Structural read failure — THIS TICK PRODUCED NO SAMPLE.
            #
            # The counter contract (``SamplerCounters.missed_samples``:
            # "bumped every time we skip a tick for overrun or for any
            # structural read failure") was previously VIOLATED here:
            # only the overrun path bumped it. A host whose platform
            # reader could not answer therefore reported a
            # HEALTHY-looking tick count (``samples`` climbing) while
            # every single sample was silently dropped — no ring row,
            # so no DB row, so the whole resource-telemetry tier stayed
            # DARK with ``flush.errors == 0`` and ``flush.batches``
            # climbing. The counter now records the drop, and the
            # ``missed_samples`` column it feeds lands in T1 so the
            # database carries the honest count.
            #
            # ``counters.ticks`` is NOT incremented — same semantics as
            # the overrun path: a tick that produced no sample is not a
            # tick. The ``/self-cost`` ``samples`` field therefore
            # counts real samples and ``ticks_skipped`` counts drops.
            with self._lock:
                self.counters.missed_samples += 1
                self.counters.ticks_skipped += 1
            if not self._no_snapshot_warned:
                self._no_snapshot_warned = True
                logger.warning(
                    "resource-monitor sampler: no daemon snapshot "
                    "(platform read failed) — samples are being dropped; "
                    "the resource-telemetry tier stays empty until the "
                    "platform reader recovers",
                )
            return None

        # P2 T10 — attribution sampling (registry children walk +
        # charge snapshot + the 1/tick deep-scan reconcile). Runs
        # BEFORE the overrun check so the tick cost honestly includes
        # the attribution work (self-cost discipline). On overrun the
        # rows are discarded, but the deep-scan correction is a stock
        # update inside the charge registry and legitimately survives.
        attribution = self._sample_attribution(snap, level)
        self._attrib = attribution

        tick_end = self._wall_clock()
        tick_cost_ms = (tick_end - tick_start) * 1000.0

        # Overrun: skip + demote.
        if tick_cost_ms > interval * 1000.0:
            with self._lock:
                self.counters.missed_samples += 1
                self.counters.ticks_skipped += 1
                self.counters.last_overrun_monotonic = tick_end
                # Demote one ladder level.
                idx = LADDER_LEVELS.index(self.counters.ladder_level)
                if idx < len(LADDER_LEVELS) - 1:
                    self.counters.ladder_level = LADDER_LEVELS[idx + 1]
                # Reset the clean-ticks counter (hysteresis).
                self._clean_ticks = 0
            logger.warning(
                "resource-monitor sampler overrun: tick_cost_ms=%.2f "
                "interval_s=%.1f ladder=%s",
                tick_cost_ms,
                interval,
                self.counters.ladder_level,
            )
            return None

        # Clean tick — update EMA + write the ring.
        with self._lock:
            alpha = self.counters.ema_alpha
            self.counters.ema_tick_cost_s = (
                alpha * (tick_cost_ms / 1000.0)
                + (1.0 - alpha) * self.counters.ema_tick_cost_s
            )
            self.counters.ticks += 1
            self.counters.last_tick_cost_ms = tick_cost_ms
            # Lag = wall-clock drift between expected_next and actual tick end.
            self.counters.last_tick_lag_ms = max(
                0.0, (tick_end - expected_next) * 1000.0
            )
            # Re-promote after N clean ticks.
            clean = getattr(self, "_clean_ticks", 0) + 1
            self._clean_ticks = clean
            if (
                clean >= RE_PROMOTE_CLEAN_TICKS
                and self.counters.ladder_level != LADDER_DEFAULT
            ):
                # Promote one step toward LADDER_DEFAULT.
                idx = LADDER_LEVELS.index(self.counters.ladder_level)
                if idx > 0:
                    target = LADDER_LEVELS[idx - 1]
                    # Don't promote beyond the user-requested default.
                    target_default_idx = LADDER_LEVELS.index(
                        self._default_ladder_for_detail()
                    )
                    if (idx - 1) >= target_default_idx:
                        self.counters.ladder_level = target
                        self._clean_ticks = 0

            # EMA self-throttle check.
            budget_s = (self.self_cpu_budget_pct / 100.0) * interval
            if (
                self.counters.ema_tick_cost_s > budget_s
                and not self.counters.self_throttled
            ):
                self.counters.self_throttled = True
                self._effective_interval_s = min(
                    interval * 2.0,
                    float(self.sample_seconds) * 8.0,  # cap at 8x
                )
                self._deep_scan_enabled = False
                logger.warning(
                    "resource-monitor sampler self-throttle: "
                    "ema_tick_cost_s=%.4f budget_s=%.4f "
                    "new_interval_s=%.2f",
                    self.counters.ema_tick_cost_s,
                    budget_s,
                    self._effective_interval_s,
                )

            self.counters.cumulative_cpu_s += tick_cost_ms / 1000.0

            # Write the row into the ring (the only writer).
            if snap is not None:
                self.ring.append(self._snapshot_to_row(snap, tick_cost_ms))
                # P2 T10 — per-instance rows (≤ cap + 1 overflow
                # sentinel) into the instance ring; the flusher drains
                # the SAME ring inside its single engine.begin() window.
                if attribution.get("instance_rows"):
                    self.instance_ring.extend(attribution["instance_rows"])
                    self.last_instance_rows = list(
                        attribution["instance_rows"]
                    )
                self.last_instance_view = list(
                    attribution.get("instances_view") or []
                )
                self.last_children_view = list(
                    attribution.get("children_view") or []
                )
                # Track the cumulative CPU usec for the next tick's
                # cpu_pct delta.
                self.counters.prev_cpu_usec_total = (
                    snap.daemon.cpu_user_usec + snap.daemon.cpu_sys_usec
                )

        return snap

    # ── P2 T10 — attribution sampling (hybrid-C integration) ──────────

    def _sample_attribution(
        self, snap: Optional[ProcSnapshot], level: str
    ) -> dict:
        """One tick's attribution sample — the D1 hybrid-C integration.

        Layers consumed (all fail-soft, never raising into the tick):

          1. ``pid_registry.snapshot()`` — per-child ``/proc`` exact
             reads (starttime-verified; PID-recycle safe), charged to
             the OWNING instance. ``instance_id`` NULL entries (mcp
             pools) stay server-scoped — never guessed onto an
             instance (D11).
          2. ``charge.snapshot_all()`` — the per-instance charged
             stock (message + checkpoint bytes).
          3. ``DeepScanner.scan_next()`` — the 1/tick bounded
             reconcile over the graph cache (D4); gated by the ladder
             level AND the EMA self-throttle (AC10 asserts the OFF
             state, never assumes it).

        **D13 blend rule** (the one implemented — documented per the
        dispatch brief): per instance, ``attributed_i = max(
        charged_i, deep_i)`` — the better of the two estimates, since
        both measure the SAME heap region (charged counters drift
        with compaction; the deep scan is a bounded undercount). The
        two are NEVER summed (double-count) and NEVER blended into
        each other's stored columns (charge keeps both side by
        side). Daemon-wide ``attributed_bytes = Σ attributed_i``;
        ``unattributed_kb = max(0, anon_bytes − attributed) // 1024``
        (charged stock can transiently exceed anon RSS — checkpoint
        reads include since-freed blobs — hence the clamp);
        ``coverage_pct = min(1.0, attributed / anon_bytes)``, with
        ``anon == 0 ⇒ 0.0`` (guard div-by-zero) — capped at 1.0
        because no attribution can honestly exceed the whole anon
        pool.

        Returns a scalar-only dict (D14):
        ``{attributed_bytes, unattributed_kb, coverage_pct,
        instance_rows, children_view}``.
        """
        empty = {
            "attributed_bytes": 0,
            "unattributed_kb": 0,
            "coverage_pct": 0.0,
            "instance_rows": [],
            "instances_view": [],
            "children_view": [],
        }
        if snap is None:
            return empty
        try:
            return self._sample_attribution_inner(snap, level)
        except Exception:
            # R2 — attribution must never break the sampler tick.
            logger.warning(
                "resource-monitor attribution sampling failed; "
                "tick continues with empty attribution",
                exc_info=True,
            )
            return empty

    def _sample_attribution_inner(
        self, snap: ProcSnapshot, level: str
    ) -> dict:
        walk_children = level in ("deep", "full")

        # 1. Registry children — exact per-pid reads.
        children_view, child_by_instance, child_snaps = self._walk_children(
            walk_children
        )

        # Merge registry children into the P1 provider children
        # (dedupe by pid) so T1 child_rss_total_kb covers BOTH paths.
        seen_pids = {c.pid for c in snap.children}
        for child in child_snaps:
            if child.pid not in seen_pids:
                snap.children.append(child)
                seen_pids.add(child.pid)

        # 2. Deep scan — 1/tick reconcile (D4). Gated on ladder level
        # (reduced/minimal skip it, consistent with the children
        # walk) and the EMA self-throttle (AC10).
        instances: Mapping[str, tuple] = {}
        if self._graph_cache_provider is not None:
            try:
                instances = self._graph_cache_provider() or {}
            except Exception:
                instances = {}
        deep_result: Optional[DeepScanResult] = None
        if (
            self._deep_scan_enabled
            and walk_children
            and instances
            and self._deep_scanner is not None
        ):
            deep_result = self._deep_scanner.scan_next(instances)

        # 3. Charge registry snapshot (the per-instance stock).
        snap_all = _charge.snapshot_all()
        graph_tasks: Mapping[str, object] = {}
        if self._graph_tasks_provider is not None:
            try:
                graph_tasks = self._graph_tasks_provider() or {}
            except Exception:
                graph_tasks = {}
        state_counts: Mapping[str, int] = {}
        if self._state_message_counts_provider is not None:
            try:
                state_counts = self._state_message_counts_provider() or {}
            except Exception:
                state_counts = {}

        # ── D13 blend rule → T1 honesty columns ──
        anon_bytes = max(0, snap.daemon.anon_kb or 0) * 1024
        attributed = 0
        charged_total = 0
        for rec in snap_all.values():
            charged = int(rec.get("charged_bytes") or 0)
            deep = int(rec.get("deep_bytes") or 0)
            charged_total += charged
            attributed += charged if charged >= deep else deep
        unattributed_kb = max(0, (anon_bytes - attributed) // 1024)
        if anon_bytes > 0:
            coverage = min(1.0, attributed / anon_bytes)
        else:
            coverage = 0.0  # anon=0 ⇒ coverage 0 (div-by-zero guard)

        # Daemon CPU delta across ticks (for cpu_share_usec_est — an
        # ESTIMATE, charged-share-proportional, labelled `_est`).
        cur_total = snap.daemon.cpu_user_usec + snap.daemon.cpu_sys_usec
        prev_total = self.counters.prev_cpu_usec_total
        daemon_cpu_delta: Optional[int] = None
        if prev_total is not None and cur_total >= prev_total:
            daemon_cpu_delta = cur_total - prev_total

        # ── T2 rows (D12 cap + D14 scalars-only) ──
        universe = set(snap_all) | set(instances) | set(graph_tasks)
        scored = sorted(
            universe,
            key=lambda iid: (
                1 if iid in graph_tasks else 0,
                int((snap_all.get(iid) or {}).get("charged_bytes") or 0),
            ),
            reverse=True,
        )
        kept = scored[: self.instance_cap]
        overflow_ids = scored[self.instance_cap :]
        charge_on = _charge.is_enabled()

        def _agent_of(iid: str) -> Optional[str]:
            entry = instances.get(iid)
            if isinstance(entry, tuple) and len(entry) > 1:
                agent_dir = entry[1]
                if isinstance(agent_dir, str) and agent_dir:
                    return os.path.basename(agent_dir.rstrip("/")) or None
            return None

        def _status_of(iid: str) -> Optional[str]:
            # P3 T2 status seam — the instance's status AT SAMPLE TIME
            # (§2.6 rule: write the observed snapshot, never re-derive
            # aliveness later). The sampler has NO DB access on the
            # tick (D5 self-cost) and the DB registry value is not in
            # any injected provider, so the snapshot is the honest
            # two-state derivation over the maps this tick already
            # holds: "running" = an active graph task exists;
            # "idle" = graph cached without an active task (includes
            # cached paused/completed instances — an acknowledged
            # approximation); None = charge-only presence (status
            # genuinely unknown at sample time — never guessed).
            if iid in graph_tasks:
                return "running"
            if iid in instances:
                return "idle"
            return None

        def _row_for(iid: str) -> dict:
            rec = snap_all.get(iid) or {}
            charged = int(rec.get("charged_bytes") or 0)
            deep = int(rec.get("deep_bytes") or 0)
            deep_scanned = (int(rec.get("deep_scans") or 0)) > 0
            is_deep_target = (
                deep_result is not None
                and deep_result.instance_id == iid
                and charge_on
            )
            scan_kind = "deep" if is_deep_target else "counter"
            partial = (
                bool(deep_result.partial_walk)
                if (is_deep_target and deep_result is not None)
                else False
            )
            # estimated = the row lacks a (complete) fresh deep scan.
            # A partial_walk deep scan undercounts ⇒ still an estimate.
            estimated = not (scan_kind == "deep" and not partial)
            cc = child_by_instance.get(iid)
            share: Optional[int] = None
            if daemon_cpu_delta is not None and charged_total > 0:
                share = int(
                    round(daemon_cpu_delta * (charged / charged_total))
                )
            # T5 all_state_messages_len: the messaging-service map is
            # the primary source; when it lacks the instance, fall
            # back to the charge record's cheap append counter (both
            # are scalar reads — never a deep walk).
            asml: Optional[int] = None
            if self._state_message_counts_provider is not None:
                asml = state_counts.get(iid)
            if asml is None:
                asml = rec.get("all_state_messages_len")
            return {
                "instance_id": iid,
                "agent_id": _agent_of(iid),
                "status": _status_of(iid),  # sample-time snapshot (P3 seam)
                "scan_kind": scan_kind,
                "charged_bytes": charged,
                "deep_bytes": deep if deep_scanned else None,
                "graph_cache_present": (
                    (iid in instances)
                    if self._graph_cache_provider is not None
                    else None
                ),
                "in_flight_tasks": (
                    (1 if iid in graph_tasks else 0)
                    if self._graph_tasks_provider is not None
                    else None
                ),
                "all_state_messages_len": asml,
                "child_rss_kb": int(cc[0]) if cc else 0,
                "child_cpu_user_usec": int(cc[1]) if cc else 0,
                "cpu_share_usec_est": share,
                "estimated": bool(estimated),
            }

        rows = [_row_for(iid) for iid in kept]
        if overflow_ids:
            overflow_charged = sum(
                int((snap_all.get(iid) or {}).get("charged_bytes") or 0)
                for iid in overflow_ids
            )
            overflow_child = child_by_instance.get(OVERFLOW_SENTINEL)
            child_rss_sum = sum(
                child_by_instance.get(iid, (0, 0, 0))[0] for iid in overflow_ids
            )
            child_cpu_sum = sum(
                child_by_instance.get(iid, (0, 0, 0))[1] for iid in overflow_ids
            )
            rows.append(
                {
                    "instance_id": OVERFLOW_SENTINEL,
                    "agent_id": None,
                    # Aggregate pseudo-instance — no single status.
                    "status": None,
                    "scan_kind": "counter",
                    "charged_bytes": overflow_charged,
                    "deep_bytes": None,
                    "graph_cache_present": None,
                    "in_flight_tasks": sum(
                        1 for iid in overflow_ids if iid in graph_tasks
                    ),
                    "all_state_messages_len": None,
                    "child_rss_kb": child_rss_sum,
                    "child_cpu_user_usec": child_cpu_sum,
                    "cpu_share_usec_est": None,
                    "estimated": True,
                }
            )
        # D14 belt-and-braces: a row may ONLY carry allowed scalar keys.
        for r in rows:
            extra = set(r) - ALLOWED_T2_ROW_KEYS
            if extra:  # pragma: no cover — construction bug tripwire
                raise ValueError(
                    f"T2 row carried non-scalar/unknown keys: {sorted(extra)}"
                )

        # ── §2.8 /live views (built here, read by the router — the
        # request path never samples) ──
        live_instances = []
        for r in rows:
            if r["instance_id"] == OVERFLOW_SENTINEL:
                continue
            cc = child_by_instance.get(r["instance_id"])
            live_instances.append(
                {
                    "instance_id": r["instance_id"],
                    "agent": r["agent_id"],
                    "estimated": r["estimated"],
                    "charged_mb": round(r["charged_bytes"] / 1048576.0, 2),
                    "child_rss_mb": round(r["child_rss_kb"] / 1024.0, 2),
                    "children_n": (cc[2] if cc else 0),
                    "in_flight_tasks": r["in_flight_tasks"],
                }
            )
        live_instances = live_instances[:LIVE_INSTANCES_CAP]
        children_view = children_view[:LIVE_CHILDREN_CAP]

        return {
            "attributed_bytes": int(attributed),
            "unattributed_kb": int(unattributed_kb),
            "coverage_pct": float(coverage),
            "instance_rows": rows,
            "instances_view": live_instances,
            "children_view": children_view,
        }

    def _walk_children(
        self, walk: bool
    ) -> tuple[list[dict], dict[str, tuple[int, int, int]], list[ChildSnapshot]]:
        """Exact per-child ``/proc`` reads off the pid registry.

        Returns ``(children_view, by_instance, child_snaps)``:

        * ``children_view`` — §2.8 ``children[]`` entries
          ``{pid, kind, name, cpu_pct, rss_mb}`` (all kinds,
          instance-owned AND server-scoped).
        * ``by_instance`` — instance_id -> (rss_kb, cpu_user_usec, n)
          aggregates for the OWNING instance only (``instance_id``
          NULL entries are excluded — D11 server-scoped).
        * ``child_snaps`` — the raw ChildSnapshots (merged into the
          tick's T1 ``child_rss_total_kb``).

        PID-recycle defense (D8): an entry is trusted only when
        ``/proc/<pid>/stat`` field 22 STILL equals the registered
        ``start_time`` — a recycled pid is never charged. Bounded at
        ``CHILD_READ_CAP`` exact reads per tick (self-cost).
        """
        view: list[dict] = []
        by_instance: dict[str, tuple[int, int, int]] = {}
        child_snaps: list[ChildSnapshot] = []
        if not walk:
            return view, by_instance, child_snaps
        try:
            entries = self._pid_registry.snapshot()
        except Exception:
            entries = []
        now = self._wall_clock()
        for entry in entries[:CHILD_READ_CAP]:
            try:
                start_time_now = read_proc_stat_field(
                    self.proc_root, entry.pid, 22
                )
                if start_time_now is None or start_time_now != entry.start_time:
                    # Gone or recycled — never charge a stolen pid.
                    self._child_cpu_prev.pop(entry.pid, None)
                    continue
                child = read_child_snapshot(self.proc_root, entry.pid)
            except Exception:
                continue
            if child is None:
                continue
            child_snaps.append(child)
            view.append(
                {
                    "pid": entry.pid,
                    "kind": entry.kind,
                    "name": _child_name(entry),
                    "cpu_pct": self._child_cpu_pct(
                        entry.pid, child.cpu_user_usec, now
                    ),
                    "rss_mb": round(child.rss_kb / 1024.0, 2),
                }
            )
            if entry.instance_id:
                agg = by_instance.get(entry.instance_id)
                if agg is None:
                    by_instance[entry.instance_id] = (
                        child.rss_kb,
                        child.cpu_user_usec,
                        1,
                    )
                else:
                    by_instance[entry.instance_id] = (
                        agg[0] + child.rss_kb,
                        agg[1] + child.cpu_user_usec,
                        agg[2] + 1,
                    )
        # Bounded eviction of the per-pid CPU delta anchor.
        if len(self._child_cpu_prev) > 1024:
            live_pids = {e.pid for e in entries}
            self._child_cpu_prev = {
                p: v
                for p, v in self._child_cpu_prev.items()
                if p in live_pids
            }
        return view, by_instance, child_snaps

    def _child_cpu_pct(
        self, pid: int, cpu_user_usec: int, now: float
    ) -> Optional[float]:
        """Per-child cpu_pct from the delta across ticks (§2.8
        children[].cpu_pct). ``None`` on first sight / non-monotonic
        (honest — no estimate invented)."""
        prev = self._child_cpu_prev.get(pid)
        self._child_cpu_prev[pid] = (cpu_user_usec, now)
        if prev is None:
            return None
        prev_cpu, prev_t = prev
        dt = now - prev_t
        if dt <= 0 or cpu_user_usec < prev_cpu:
            return None
        return round(
            ((cpu_user_usec - prev_cpu) / (dt * 1_000_000.0)) * 100.0, 4
        )

    def _read_pss_rollup(self) -> Optional[int]:
        """Read ``/proc/self/smaps_rollup`` for PSS in kB (via the
        proc_readers helper so the fenced tests' escape guard sees
        it).
        """
        from .proc_readers import read_pss_rollup

        return read_pss_rollup(self.proc_root)

    def _default_ladder_for_detail(self) -> str:
        """Translate the configured ``detail_level`` into a starting
        ladder level. ``auto`` is the default (``full``); explicit
        ``full`` / ``reduced`` / ``minimal`` pin the starting level.
        """
        if self.detail_level in LADDER_LEVELS:
            return self.detail_level
        return LADDER_DEFAULT

    def _snapshot_to_row(
        self,
        snap: ProcSnapshot,
        tick_cost_ms: float,
    ) -> dict:
        """Convert a ``ProcSnapshot`` into the ring row shape.

        P2 (D13): the attribution columns carry REAL values computed
        by :meth:`_sample_attribution` this tick — the P1 zero
        placeholders are gone. The blend rule (charged ∪ deep, never
        summed) is documented there.
        """
        d = snap.daemon
        attrib = getattr(self, "_attrib", None) or {}
        row: dict = {
            # The tick wall-clock (Naive-UTC ISO) is stamped at write
            # time by the flusher (T6); the sampler writes the
            # cost-counter payload, not the timestamp.
            "interval_s": int(self._effective_interval_s),
            # Daemon pid rides the ring row so the /live handler can
            # populate the §2.8 daemon.pid field (review F2).
            "pid": d.pid,
            "rss_kb": d.rss_kb,
            "pss_kb": d.pss_kb,
            "anon_kb": d.anon_kb,
            "hw_peak_kb": d.hw_peak_kb,
            "threads": d.threads,
            "fds": d.fds,
            "cpu_user_usec": d.cpu_user_usec,
            "cpu_sys_usec": d.cpu_sys_usec,
            "child_rss_total_kb": sum(c.rss_kb for c in snap.children),
            # P2 T10 (D13) — real honesty columns.
            "attributed_bytes": int(attrib.get("attributed_bytes") or 0),
            "unattributed_kb": int(attrib.get("unattributed_kb") or 0),
            "coverage_pct": float(attrib.get("coverage_pct") or 0.0),
            "sample_cost_ms": tick_cost_ms,
            "sampler_lag_ms": self.counters.last_tick_lag_ms,
            "missed_samples": self.counters.missed_samples,
            "ladder_level": self.counters.ladder_level,
        }
        cg = snap.cgroup
        if cg is not None:
            row["cg_current_mb"] = cg.memory.current_mb
            row["cg_peak_mb"] = cg.memory.peak_mb
            row["cg_anon_mb"] = cg.memory.anon_mb
            row["cg_file_mb"] = cg.memory.file_mb
            row["cg_slab_mb"] = cg.memory.slab_mb
            row["cg_usage_usec"] = cg.cpu.usage_usec
            row["cg_throttled_usec"] = cg.cpu.throttled_usec
            row["cg_nr_throttled"] = cg.cpu.nr_throttled
        else:
            # cgroup v2 absent / v1 / unreadable — sentinel None.
            for k in (
                "cg_current_mb", "cg_peak_mb", "cg_anon_mb",
                "cg_file_mb", "cg_slab_mb", "cg_usage_usec",
                "cg_throttled_usec", "cg_nr_throttled",
            ):
                row[k] = None
        return row

    # ── Loop (background thread) ──────────────────────────────────────

    def _run_loop(self) -> None:
        """The thread body — one tick at a time, sleeping on the
        interval. NEVER touches the DB. NEVER raises.
        """
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:  # sampler NEVER crashes the daemon
                logger.error(
                    "resource-monitor sampler tick failed: %s",
                    exc,
                    exc_info=True,
                )
                self.counters.missed_samples += 1
            # Sleep in short slices so stop() is responsive.
            interval = self._effective_interval_s
            slept = 0.0
            while slept < interval:
                if self._stop_event.wait(timeout=0.1):
                    return
                slept += 0.1


# ── Helpers ─────────────────────────────────────────────────────────────────


def _child_name(entry) -> str:
    """Derive the §2.8 ``children[].name`` from a registry entry.

    Preference: the entry's ``detail`` (the MCP server name for
    ``mcp_child`` stamps — D11 server-scoped identity), else the
    basename of the sanitized ``command``'s argv[0], else ``pid-<n>``.
    The command is ALREADY sanitized argv-shape at the seam (D14) —
    this helper only narrows it further to a display token.
    """
    if entry.detail:
        return str(entry.detail)
    cmd = (entry.command or "").strip()
    if cmd:
        first = cmd.split()[0]
        return os.path.basename(first) or first
    return f"pid-{entry.pid}"


__all__ = [
    "LADDER_LEVELS",
    "LADDER_DEFAULT",
    "RE_PROMOTE_CLEAN_TICKS",
    "PSS_CADENCE",
    "CHILD_READ_CAP",
    "LIVE_CHILDREN_CAP",
    "LIVE_INSTANCES_CAP",
    "OVERFLOW_SENTINEL",
    "ALLOWED_T2_ROW_KEYS",
    "SamplerCounters",
    "Sampler",
]