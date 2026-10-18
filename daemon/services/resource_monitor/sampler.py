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
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .proc_readers import (
    ChildSnapshot,
    CgroupSnapshot,
    DaemonProcSnapshot,
    ProcSnapshot,
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
    # Total ticks run since thread start.
    ticks: int = 0
    # Skipped (overrun) ticks.
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

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()  # counters + ring write guard

        # EMA self-throttle: when the EMA tick cost climbs above the
        # budget, double the interval and disable deep scans.
        self._effective_interval_s = float(sample_seconds)
        self._deep_scan_enabled = True

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
          2. If ``tick_t > interval``: skip + ``missed_samples++`` +
             demote one ladder level. Return ``None``.
          3. Otherwise: read /proc + cgroup (subject to ladder level),
             update EMA tick cost, write the row into the ring,
             re-promote after N clean ticks (hysteresis).
          4. If EMA tick cost > ``self_cpu_budget_pct`` % of
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

        tick_end = self._wall_clock()
        tick_cost_ms = (tick_end - tick_start) * 1000.0

        # Overrun: skip + demote.
        if tick_cost_ms > interval * 1000.0:
            with self._lock:
                self.counters.missed_samples += 1
                self.counters.ticks_skipped += 1
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

        return snap

    def _read_pss_rollup(self) -> Optional[int]:
        """Read ``/proc/self/smaps_rollup`` for PSS in kB.

        The plan gates smaps_rollup on ``detail_level`` (env-gated).
        When the sampler runs in ``deep`` / ``full`` mode every 10th
        tick reads it; the result is plumbed through the daemon row.
        Returns None on any read failure — never crashes the sampler.
        """
        try:
            text = (self.proc_root / "self" / "smaps_rollup").read_text()
        except (FileNotFoundError, PermissionError, OSError):
            return None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("Pss:"):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        return int(parts[1])
                    except (ValueError, TypeError):
                        return None
        return None

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

        P1 boundary (D13): the row carries the daemon + cgroup
        fields plus sampler self-cost counters. Attribution
        counters (charged_bytes / unattributed / coverage_pct)
        are written as 0 by the sampler — P2 fills them.
        """
        d = snap.daemon
        row: dict = {
            # The tick wall-clock (Naive-UTC ISO) is stamped at write
            # time by the flusher (T6); the sampler writes the
            # cost-counter payload, not the timestamp.
            "interval_s": int(self._effective_interval_s),
            "rss_kb": d.rss_kb,
            "pss_kb": d.pss_kb,
            "anon_kb": d.anon_kb,
            "hw_peak_kb": d.hw_peak_kb,
            "threads": d.threads,
            "fds": d.fds,
            "cpu_user_usec": d.cpu_user_usec,
            "cpu_sys_usec": d.cpu_sys_usec,
            "child_rss_total_kb": sum(c.rss_kb for c in snap.children),
            # P1 boundary — D13.
            "attributed_bytes": 0,
            "unattributed_kb": 0,
            "coverage_pct": 0.0,
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


__all__ = [
    "LADDER_LEVELS",
    "LADDER_DEFAULT",
    "RE_PROMOTE_CLEAN_TICKS",
    "PSS_CADENCE",
    "SamplerCounters",
    "Sampler",
]