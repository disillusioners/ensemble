"""Engine-independent watchdog (Engine Arc P4 — T4.2 / AMD-2).

A DEDICATED DAEMON THREAD — NEVER A COROUTINE
---------------------------------------------
A starved event loop cannot run a coroutine: that is the entire premise of
the instrument (``daemon/services/wedge_capture/watchdog.py:1-13``).  This
watchdog samples engine liveness from OUTSIDE the loop through the lock-free
atomics stamped by the tick loop (``engine.watchdog_snapshot()``), never via
an engine API call on the sampling path.

STALL PREDICATE (D4.6 / D4.8)
-----------------------------
A stall is ``now - last_tick_monotonic > 3 * tick_resolution_seconds +
boot_max_window_seconds``.  Both the cadence (``watchdog_interval_seconds``)
and the threshold inputs are CONFIG values derived once at ``start()`` —
never literals.  The watchdog is NOT a readiness component (F-8): it adds no
``READINESS_COMPONENT_NAMES`` member and its CRITICAL is its own log surface.

RECOVERY IS ENGINE-INDEPENDENT (AMD-2)
--------------------------------------
On a stall the watchdog logs one CRITICAL per episode, bumps an in-process
counter exposed via the engine snapshot (the L4 lesson: log-only alerting is
not the plan), and invokes an injected ``rearm`` callable.  The rearm path is
STRICTLY engine-independent — a fresh supervisor / direct DB re-arm through
the repository.  It NEVER calls ``engine.register()`` on the wedged engine.

The thread can never raise into the daemon: every tick body is wrapped in
``try/except``; a failure is recorded in ``self.errors`` and the loop continues.
Startup is decoupled — constructing/starting the watchdog does not require the
engine construction to have succeeded.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def make_direct_db_rearm(repository: Any) -> Callable[[dict], int]:
    """Build an ENGINE-INDEPENDENT recovery callable (AMD-2).

    The returned callable re-arms stale ``dead`` engine-job rows DIRECTLY
    through the repository — it never touches the engine, never calls
    ``engine.register()``, and never schedules work on the (possibly wedged)
    loop.  Returns the number of rows re-armed.  Any error is swallowed so a
    recovery attempt can never kill the watchdog thread.

    In the dormant Phase-1 posture there are no dead rows, so this is a
    bounded no-op; the mechanism is what is being built and drilled.
    """
    def _rearm(_sample: dict) -> int:
        rearmed = 0
        try:
            jobs = repository.list_jobs()
        except Exception:  # noqa: BLE001
            return 0
        for job in jobs or ():
            try:
                if getattr(job, "dead", 0) and not getattr(job, "last_outcome", None):
                    repository.stamp_job_outcome(str(job.id), dead=0)
                    rearmed += 1
            except Exception:  # noqa: BLE001
                continue
        return rearmed

    return _rearm


class EngineWatchdog:
    """Off-loop liveness sampler for the engine (AMD-2 / D4.6 / D4.8)."""

    def __init__(
        self,
        engine: Any,
        config: Any = None,
        *,
        interval_s: Optional[float] = None,
        rearm: Optional[Callable[[dict], Any]] = None,
    ) -> None:
        self._engine = engine
        cfg = config if config is not None else getattr(engine, "config", None)

        def _cfg(name: str, default: float) -> float:
            value = getattr(cfg, name, None) if cfg is not None else None
            try:
                return float(value) if value is not None else float(default)
            except (TypeError, ValueError):
                return float(default)

        # D4.8: cadence is a config value, NEVER a literal.
        self._interval = (
            float(interval_s)
            if interval_s is not None
            else _cfg("watchdog_interval_seconds", 5.0)
        )
        if self._interval <= 0:
            self._interval = 5.0
        # D4.6: derive the stall threshold from config at start.
        tick_resolution = _cfg("tick_resolution_seconds", 1.0)
        boot_window = _cfg("boot_max_window_seconds", 120.0)
        self._stall_threshold = 3.0 * tick_resolution + boot_window

        self._rearm = rearm
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._in_stall = False
        self.errors: int = 0
        self.criticals: int = 0
        self.rearms: int = 0

    # ── introspection ────────────────────────────────────────────────
    @property
    def stall_threshold(self) -> float:
        return self._stall_threshold

    @property
    def interval(self) -> float:
        return self._interval

    @property
    def is_alive(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())

    def sample(self) -> dict:
        """Sample the engine's liveness atomics lock-free (no engine API)."""
        snap = self._engine.watchdog_snapshot()
        return dict(snap)

    def is_stalled(self, sample: dict, now: Optional[float] = None) -> bool:
        """Stall predicate — last tick older than the derived threshold.

        A never-ticked engine (``last_tick_monotonic == 0``) is NOT stalled:
        that is the boot grace window, not a wedge.
        """
        last = float(sample.get("last_tick_monotonic", 0.0) or 0.0)
        if last <= 0.0:
            return False
        return (now if now is not None else time.monotonic()) - last > self._stall_threshold

    # ── lifecycle ────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return  # idempotent
        self._stop_event.clear()
        self._in_stall = False
        self._thread = threading.Thread(
            target=self._run,
            name="engine-watchdog",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: Optional[float] = None) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            join_timeout = float(timeout) if timeout is not None else min(
                2.0 * self._interval, 5.0
            )
            thread.join(timeout=join_timeout)
            self._thread = None

    # ── thread body ──────────────────────────────────────────────────
    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                now = time.monotonic()
                sample = self.sample()
                stalled = self.is_stalled(sample, now)
                if stalled and not self._in_stall:
                    # Edge-triggered: exactly ONE CRITICAL per stall episode.
                    self._in_stall = True
                    self._on_stall(sample, now)
                elif not stalled:
                    self._in_stall = False
            except Exception:  # noqa: BLE001 — never raise into the daemon
                self.errors += 1
            self._stop_event.wait(self._interval)

    def _on_stall(self, sample: dict, now: float) -> None:
        """Record one stall episode: CRITICAL + counter + engine-independent rearm."""
        self.criticals += 1
        try:
            engine = self._engine
            engine._watchdog_stall_count = int(
                getattr(engine, "_watchdog_stall_count", 0) or 0
            ) + 1
            engine._watchdog_last_stall_monotonic = now
        except Exception:  # noqa: BLE001
            pass
        age = now - float(sample.get("last_tick_monotonic", 0.0) or 0.0)
        logger.critical(
            "ENGINE WATCHDOG: stall detected — last tick %.3fs ago "
            "(threshold %.3fs); sample=%s",
            age,
            self._stall_threshold,
            sample,
        )
        if self._rearm is not None:
            try:
                self._rearm(sample)
                self.rearms += 1
            except Exception:  # noqa: BLE001 — recovery never kills the thread
                self.errors += 1
                logger.warning("engine watchdog rearm failed (watchdog survives)")


__all__ = ["EngineWatchdog", "make_direct_db_rearm"]
