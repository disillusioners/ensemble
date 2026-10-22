"""Event-loop lag probe (D4 / D4b / A1 / T4).

THE PROBLEM THIS SOLVES
------------------------
A naive "skip the sample when the loop callback did not run"
implementation SILENTLY DISABLES the lag trigger on a fully starved
loop — which is EXACTLY the H2 shape the instrument exists to catch
(RK-16). A1 therefore makes three promises:

* **A1(a) — the stamp slot starts UNSET.** The first tick emits NO
  numeric sample and sets ``probe_state="initializing"``. An unset
  slot is never rendered as a number, never as healthy, and NEVER
  evaluated by the trigger.
* **A1(b) — at most ONE outstanding post.** The probe posts
  ``loop.call_soon_threadsafe(probe_cb)`` only when no probe is
  outstanding. One post per tick is REJECTED: during a wedge it
  grows the loop's ready queue without bound, bursts on unblock, and
  makes the peak racy (RK-21).
* **A1(c) — "never fired" is a SAMPLE, not a skip.** A tick with no
  loop execution yields a sample valued at the elapsed time since the
  last observed execution — never skipped, never ``0``. A fully
  starved loop therefore yields a monotonically growing series, and
  the sustain window accumulates across it.

THE SUSTAIN TEST IS WALL-TIME (D4b)
-----------------------------------
The elapsed wall time from the first to the last consecutive
at/above-threshold sample must reach ``sustain_s``. The verdict is
then IDENTICAL at a 1 s and a 5 s tick cadence, and the fully starved
case fires DETERMINISTICALLY (the series grows monotonically, so the
elapsed time between the first and last over-threshold sample is
well-defined even when the loop never executes).

THE MEASURED FORM (B1)
----------------------
Under A1(b) the first blocked tick RE-POSTS rather than accumulating,
so a block of *T* seconds measures ``lag_s ≈ T − one tick``. A 6 s
block at the 1 Hz cadence therefore measures ``lag_s >= 5.0`` and
sustains a 2 s window. The earlier ``_LAG_S=1`` sat on the
healthy-tick noise floor and could not fire at all.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any

#: Probe states (D4(a) / D4(e)).
PROBE_INITIALIZING = "initializing"
PROBE_OK = "ok"
PROBE_UNKNOWN = "unknown"
PROBE_STOPPED = "stopped"

#: The rolling histogram's bound (T4) — 300 samples ≈ 5 min at 1 Hz.
MAX_LAG_SAMPLES = 300

#: The histogram's overflow bucket ceiling, in seconds. Values above
#: this are CLAMPED into the top bucket, never dropped (A1(d)).
LAG_OVERFLOW_BUCKET_S = 3600.0


@dataclass
class LagHistogram:
    """The bounded rolling lag histogram (D4(d) / T4).

    Attributes:
        samples: The retained samples, oldest first. Bounded at
            ``MAX_LAG_SAMPLES`` — the OLDEST are evicted first.
        overflow_count: How many samples were clamped into the top
            bucket (never dropped).
        loop_unresponsive_s: The current outstanding duration at the
            time of the snapshot — how long the loop has been
            unresponsive RIGHT NOW.
    """

    samples: list[float] = field(default_factory=list)
    overflow_count: int = 0
    loop_unresponsive_s: float | None = None

    def add(self, value: float) -> None:
        """Append a sample, evicting the oldest past the bound."""
        self.samples.append(float(value))
        if len(self.samples) > MAX_LAG_SAMPLES:
            del self.samples[: len(self.samples) - MAX_LAG_SAMPLES]

    def count_overflow(self, value: float) -> None:
        """Count a clamped sample (the value itself is NOT stored)."""
        if value > LAG_OVERFLOW_BUCKET_S:
            self.overflow_count += 1

    def percentile(self, pct: float) -> float | None:
        """The *pct* percentile of the retained samples, or ``None``."""
        if not self.samples:
            return None
        ordered = sorted(self.samples)
        if len(ordered) == 1:
            return ordered[0]
        # Nearest-rank percentile — no interpolation, no invented value.
        # ``ceil`` (not ``round``): the earlier ``round(x + 0.5)`` form was
        # subject to banker's rounding, so p95 of 1..100 reported 96 while
        # p50 reported 50 — an inconsistent headline number in an evidence
        # artifact. Nearest-rank is ``ceil(pct/100 * n)``, clamped to 1.
        rank = max(1, min(len(ordered), math.ceil(pct / 100.0 * len(ordered))))
        return ordered[rank - 1]

    def as_dict(self) -> dict[str, Any]:
        """The ``lag.json`` payload (the canonical matrix §9 fields)."""
        return {
            "samples": len(self.samples),
            "max": max(self.samples) if self.samples else None,
            "p95": self.percentile(95),
            "p50": self.percentile(50),
            "overflow_count": self.overflow_count,
            "loop_unresponsive_s": self.loop_unresponsive_s,
            "bucket_ceiling_s": LAG_OVERFLOW_BUCKET_S,
            "values": [round(v, 6) for v in self.samples],
        }


class LoopLagProbe:
    """The thread-safe loop-lag probe.

    The probe is driven from the WATCHDOG thread and posts ONE
    callback per tick to the event loop. The callback stamps
    ``time.monotonic()`` into the shared slot.

    Args:
        loop: The event loop to probe. ``None`` ⇒ the probe reports
            ``unknown`` and never posts (D4(e)).
    """

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop
        self._lock = threading.Lock()
        self._outstanding_posted_at: float | None = None
        self._last_exec_at: float | None = None
        self._probe_state: str = PROBE_INITIALIZING
        self._loop_thread_native_id: int | None = None
        self._last_probe_age_s: float | None = None
        self._histogram = LagHistogram()

    # ── wiring ─────────────────────────────────────────────────────────
    def set_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        """Bind (or rebind) the loop under test."""
        with self._lock:
            self._loop = loop
            if loop is None:
                self._probe_state = PROBE_UNKNOWN
            elif self._probe_state in (PROBE_INITIALIZING, PROBE_UNKNOWN):
                self._probe_state = PROBE_INITIALIZING

    @property
    def probe_state(self) -> str:
        """The probe's state — ``initializing`` / ``ok`` / ``unknown`` / ``stopped``."""
        with self._lock:
            return self._probe_state

    @property
    def loop_thread_native_id(self) -> int | None:
        """The native id of the thread running the loop.

        The probe callback executes ON the loop thread, so it records
        ``threading.get_native_id()`` there — the mapping is measured,
        not assumed.
        """
        with self._lock:
            return self._loop_thread_native_id

    @property
    def last_probe_age_s(self) -> float | None:
        """Seconds since the loop last executed the probe callback.

        ``None`` when the probe has never fired (RK-23: lag telemetry
        goes quiet exactly when it matters, so the AGE is reported).
        """
        with self._lock:
            if self._last_exec_at is None:
                return None
            return max(0.0, time.monotonic() - self._last_exec_at)

    @property
    def never_fired(self) -> bool:
        """True when the loop has NEVER executed the probe callback.

        The explicit ``never_fired`` marker (RK-23) — the analyst must
        be able to tell "the lag was 0" from "the loop never answered".
        """
        with self._lock:
            return self._last_exec_at is None

    @property
    def histogram(self) -> LagHistogram:
        """The rolling histogram (the watchdog copies it at capture)."""
        return self._histogram

    def snapshot_histogram(self) -> dict[str, Any]:
        """A JSON-safe snapshot of the histogram."""
        with self._lock:
            self._histogram.loop_unresponsive_s = (
                None
                if self._outstanding_posted_at is None
                else max(0.0, time.monotonic() - self._outstanding_posted_at)
            )
            return self._histogram.as_dict()

    # ── the tick ───────────────────────────────────────────────────────
    def tick(self, now: float | None = None) -> float | None:
        """Advance the probe by one tick and return the measured lag.

        Returns:
            The measured lag in seconds, or ``None`` when the probe is
            still initializing (A1(a)) or has no usable loop (D4(e)).
        """
        moment = time.monotonic() if now is None else now

        with self._lock:
            loop = self._loop
            if loop is None:
                self._probe_state = PROBE_UNKNOWN
                return None
            try:
                if loop.is_closed():
                    self._probe_state = PROBE_STOPPED
                    return None
            except Exception:  # pragma: no cover — defensive
                self._probe_state = PROBE_UNKNOWN
                return None

            if self._probe_state == PROBE_STOPPED:
                return None

            # A1(b): at most ONE outstanding post.
            if self._outstanding_posted_at is not None:
                lag_s = max(0.0, moment - self._outstanding_posted_at)
                self._histogram.count_overflow(lag_s)
                self._histogram.add(lag_s)
                # The PROBE is functioning (it produced a numeric
                # sample); the LOOP is late. The state's meaning is
                # "the probe works", not "the loop is healthy" — the
                # loop's health IS the measured lag.
                self._probe_state = PROBE_OK
                return lag_s

            # No outstanding probe — post one and measure the time since
            # the LAST observed execution (A1(c): a sample, never a skip).
            try:
                loop.call_soon_threadsafe(self._probe_callback)
            except RuntimeError as exc:
                # D4(e): a closed loop is a STATE, not a crash — and it
                # must NOT inflate ``missed_samples``.
                if "closed" in str(exc).lower():
                    self._probe_state = PROBE_STOPPED
                else:
                    self._probe_state = PROBE_UNKNOWN
                return None
            except Exception:  # pragma: no cover — defensive
                self._probe_state = PROBE_UNKNOWN
                return None

            self._outstanding_posted_at = moment
            if self._last_exec_at is None:
                # A1(a): the very first tick has NO sample to report.
                self._probe_state = PROBE_INITIALIZING
                return None
            lag_s = max(0.0, moment - self._last_exec_at)
            self._histogram.count_overflow(lag_s)
            self._histogram.add(lag_s)
            return lag_s

    def _probe_callback(self) -> None:
        """The callback that runs ON the event loop."""
        with self._lock:
            self._outstanding_posted_at = None
            self._last_exec_at = time.monotonic()
            self._probe_state = PROBE_OK
            try:
                self._loop_thread_native_id = threading.get_native_id()
            except Exception:  # pragma: no cover — defensive
                self._loop_thread_native_id = None

    def stop(self) -> None:
        """Stop posting — the probe goes to ``stopped`` and stays there."""
        with self._lock:
            self._probe_state = PROBE_STOPPED


__all__ = [
    "LAG_OVERFLOW_BUCKET_S",
    "MAX_LAG_SAMPLES",
    "PROBE_INITIALIZING",
    "PROBE_OK",
    "PROBE_STOPPED",
    "PROBE_UNKNOWN",
    "LagHistogram",
    "LoopLagProbe",
]
