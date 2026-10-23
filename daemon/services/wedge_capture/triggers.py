"""The three wedge triggers (D2 / D3 / D4b / D7 / D13 / T5).

THREE INDEPENDENT TRIGGERS, ALL SHIPPED
--------------------------------------
``sustained_cpu``       — the PROCESS is burning CPU.
``sustained_loop_lag``  — the event loop is NOT being serviced.
``thread_concentration``— ONE thread holds the process's CPU.

They are complementary, not redundant: the incident's H2 shape (three
~181 s stalls with NO CPU peg) is invisible to a CPU-only trigger, and
the H1 shape (a loop thread holding ~100% of one core) is invisible
to a lag-only trigger. The instrument must be able to FALSIFY a
hypothesis, so the signals must stay SEPARABLE (D2).

THE SUSTAIN TEST IS WALL-TIME (D4b)
-----------------------------------
Elapsed wall time from the first to the last consecutive
at/above-threshold sample. Identical verdict at a 1 s and a 5 s tick
cadence; deterministic in the fully starved case.

PURE FUNCTIONS OVER A SERIES
----------------------------
:func:`evaluate` is a pure function over a sample series — no I/O, no
threads, no clock reads. The unit packs drive it directly. The
:class:`TriggerEngine` wraps it with the cooldown bookkeeping.

THE CONCENTRATION FLOOR (D7 / F-06)
------------------------------------
The ratio ``thread_cpu_delta / process_cpu_delta`` is evaluated ONLY
when the process actually burned CPU over the window — an ABSOLUTE
floor of 5% of one core. Below that floor the ratio is trivially
≈1.0 for a near-idle process (a single short-lived helper thread),
so an unfloored ratio manufactures false positives on a quiet daemon.

THE PROBE STATES THE TRIGGER NEVER CONSUMES (A1)
-------------------------------------------------
An unset probe (the very first tick) and a ``stopped`` / ``unknown``
probe are NEVER evaluated — neither can produce a number, and a
number that does not exist must not become a threshold breach.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Sequence

from .config import WedgeCaptureConfig
from .lag_probe import PROBE_INITIALIZING, PROBE_STOPPED, PROBE_UNKNOWN

# ── the canonical trigger reasons (D3) ──────────────────────────────────────
REASON_SUSTAINED_CPU = "sustained_cpu"
REASON_SUSTAINED_LOOP_LAG = "sustained_loop_lag"
REASON_THREAD_CONCENTRATION = "thread_concentration"
REASON_MANUAL = "manual"

#: Every canonical reason, in the sibling matrix §9's order.
CANONICAL_REASONS: tuple[str, ...] = (
    REASON_SUSTAINED_CPU,
    REASON_SUSTAINED_LOOP_LAG,
    REASON_THREAD_CONCENTRATION,
    REASON_MANUAL,
)

#: The ABSOLUTE CPU floor for the concentration ratio, as a percentage
#: of ONE core over the window (D7 / F-06).
CONCENTRATION_ABSOLUTE_FLOOR_PCT = 5.0


@dataclass(frozen=True)
class TriggerSample:
    """One tick's sample, as the trigger engine sees it.

    Attributes:
        t: The monotonic timestamp of the tick.
        process_cpu_pct: The process CPU, as a percentage of ONE core.
        process_cpu_delta_s: The process CPU consumed in this tick, in
            seconds.
        top_thread_delta_s: The busiest thread's CPU in this tick, in
            seconds.
        top_thread_share_pct: The busiest thread's share of the
            process CPU in this tick.
        top_thread_native_id: The busiest thread's native id.
        top_thread_comm: The busiest thread's OS comm name.
        lag_s: The measured event-loop lag, or ``None`` when the probe
            produced no numeric sample (A1(a)).
        lag_state: The probe state — ``ok`` / ``initializing`` /
            ``unknown`` / ``stopped``.
    """

    t: float
    process_cpu_pct: float
    process_cpu_delta_s: float
    top_thread_delta_s: float = 0.0
    top_thread_share_pct: float = 0.0
    top_thread_native_id: int | None = None
    top_thread_comm: str = ""
    lag_s: float | None = None
    lag_state: str = "unknown"


@dataclass(frozen=True)
class TriggerDecision:
    """A firing trigger.

    Attributes:
        reason: One of the canonical reason tokens.
        detail: A human-readable explanation of WHAT was observed.
        since: The monotonic timestamp of the FIRST sample of the
            over-threshold run.
        observed: The observed values that produced the decision.
    """

    reason: str
    detail: str
    since: float
    observed: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """A JSON-safe view of the decision."""
        return {
            "reason": self.reason,
            "detail": self.detail,
            "since": self.since,
            "observed": dict(self.observed),
        }


def _trailing_run(
    samples: Sequence[TriggerSample],
    predicate: Callable[[TriggerSample], bool],
) -> list[TriggerSample]:
    """The trailing run of samples satisfying ``predicate``.

    The run BREAKS at the first sample that fails the predicate — a
    series that drops below the threshold RESETS the sustain window.
    """
    run: list[TriggerSample] = []
    for sample in reversed(samples):
        if not predicate(sample):
            break
        run.append(sample)
    run.reverse()
    return run


def _sustained(
    run: Sequence[TriggerSample],
    sustain_s: float,
) -> tuple[bool, float]:
    """Is the run sustained for ``sustain_s`` of wall time?

    Returns:
        ``(sustained, elapsed_s)``. A sustain window of ``0`` fires on
        the FIRST over-threshold sample (the boot-smoke's tiny-threshold
        legs rely on this).
    """
    if not run:
        return False, 0.0
    elapsed = run[-1].t - run[0].t
    if sustain_s <= 0:
        return True, elapsed
    return elapsed >= sustain_s, elapsed


def evaluate(
    samples: Sequence[TriggerSample],
    config: WedgeCaptureConfig,
) -> list[TriggerDecision]:
    """Evaluate the three triggers over a sample series.

    Pure: no I/O, no clock reads, no state. The caller owns the
    cooldown.

    Args:
        samples: The series, OLDEST FIRST. Only the trailing run of
            each predicate matters (a drop below the threshold resets
            the sustain window).
        config: The effective configuration (thresholds + sustain
            windows).

    Returns:
        One decision per trigger whose sustain window is satisfied.
        Two DIFFERENT reasons can fire on the same series — they are
        different shapes and the cooldown is per-reason (D13).
    """
    if not samples:
        return []

    decisions: list[TriggerDecision] = []

    # ── sustained_cpu ────────────────────────────────────────────────
    cpu_run = _trailing_run(
        samples, lambda s: s.process_cpu_pct >= config.cpu_pct
    )
    cpu_ok, cpu_elapsed = _sustained(cpu_run, config.cpu_sustain_s)
    if cpu_ok:
        decisions.append(
            TriggerDecision(
                reason=REASON_SUSTAINED_CPU,
                detail=(
                    f"process CPU {cpu_run[-1].process_cpu_pct:.1f}% of one "
                    f"core sustained for {cpu_elapsed:.1f}s "
                    f"(threshold {config.cpu_pct:.1f}% / "
                    f"{config.cpu_sustain_s:.1f}s)"
                ),
                since=cpu_run[0].t,
                observed={
                    "process_cpu_pct": round(cpu_run[-1].process_cpu_pct, 3),
                    "process_cpu_delta_s": round(
                        sum(s.process_cpu_delta_s for s in cpu_run), 6
                    ),
                    "sustained_s": round(cpu_elapsed, 3),
                    "threshold_pct": config.cpu_pct,
                    "sustain_s": config.cpu_sustain_s,
                },
            )
        )

    # ── sustained_loop_lag ───────────────────────────────────────────
    # A1: an unset probe and a stopped/unknown probe are NEVER consumed.
    lag_run = _trailing_run(
        samples,
        lambda s: (
            s.lag_s is not None
            and s.lag_state
            not in (PROBE_INITIALIZING, PROBE_STOPPED, PROBE_UNKNOWN)
            and s.lag_s >= config.lag_s
        ),
    )
    lag_ok, lag_elapsed = _sustained(lag_run, config.lag_sustain_s)
    if lag_ok:
        decisions.append(
            TriggerDecision(
                reason=REASON_SUSTAINED_LOOP_LAG,
                detail=(
                    f"event-loop lag {lag_run[-1].lag_s:.2f}s sustained for "
                    f"{lag_elapsed:.1f}s (threshold {config.lag_s:.2f}s / "
                    f"{config.lag_sustain_s:.1f}s)"
                ),
                since=lag_run[0].t,
                observed={
                    "lag_s": round(float(lag_run[-1].lag_s or 0.0), 6),
                    "sustained_s": round(lag_elapsed, 3),
                    "threshold_s": config.lag_s,
                    "sustain_s": config.lag_sustain_s,
                },
            )
        )

    # ── thread_concentration (with the ABSOLUTE floor, D7 / F-06) ────
    conc_run = _trailing_run(
        samples,
        lambda s: (
            s.top_thread_share_pct >= config.thread_concentration_pct
            and s.process_cpu_delta_s > 0
        ),
    )
    if conc_run:
        window_process_s = sum(s.process_cpu_delta_s for s in conc_run)
        window_thread_s = sum(s.top_thread_delta_s for s in conc_run)
        # The window's elapsed span. A single-sample window has NO span:
        # fall back to that tick's own percentage. Dividing by an epsilon
        # would fabricate an astronomical number and make the absolute
        # floor trivially true — exactly the degenerate case F-06 exists
        # to prevent.
        window_elapsed_s = conc_run[-1].t - conc_run[0].t
        if window_elapsed_s > 0:
            window_pct = (window_process_s / window_elapsed_s) * 100.0
        else:
            window_pct = conc_run[-1].process_cpu_pct
        ratio = (window_thread_s / window_process_s) if window_process_s > 0 else 0.0
        above_floor = window_pct >= CONCENTRATION_ABSOLUTE_FLOOR_PCT
        conc_ok, conc_elapsed = _sustained(conc_run, config.cpu_sustain_s)
        if conc_ok and above_floor:
            top = conc_run[-1]
            decisions.append(
                TriggerDecision(
                    reason=REASON_THREAD_CONCENTRATION,
                    detail=(
                        f"thread {top.top_thread_native_id} "
                        f"(comm={top.top_thread_comm or '?'}) holds "
                        f"{ratio * 100:.1f}% of process CPU over "
                        f"{conc_elapsed:.1f}s (process CPU "
                        f"{window_pct:.1f}% of one core; ratio threshold "
                        f"{config.thread_concentration_pct:.1f}%, absolute "
                        f"floor {CONCENTRATION_ABSOLUTE_FLOOR_PCT:.1f}%)"
                    ),
                    since=conc_run[0].t,
                    observed={
                        "native_id": top.top_thread_native_id,
                        "comm": top.top_thread_comm,
                        "share_pct": round(ratio * 100.0, 3),
                        "window_process_cpu_pct": round(window_pct, 3),
                        "absolute_floor_pct": CONCENTRATION_ABSOLUTE_FLOOR_PCT,
                        "sustained_s": round(conc_elapsed, 3),
                        "threshold_pct": config.thread_concentration_pct,
                    },
                )
            )

    return decisions


class TriggerEngine:
    """The trigger engine with cooldown + episode de-dup (D13 / T5).

    The engine keeps a bounded rolling window of samples (long enough
    to cover the longest sustain window) and a per-reason cooldown
    timestamp. The cooldown is measured from the START of the previous
    capture for that reason and is STAMPED BY THE CAPTURE PATH (W1):
    :meth:`observe` gates but never stamps, so a decision the watchdog
    could not service (one capture per tick) stays armed.

    Args:
        config: The effective configuration.
        window_seconds: The rolling window's span. Defaults to a value
            derived from the longest sustain window.
    """

    def __init__(
        self,
        config: WedgeCaptureConfig,
        *,
        window_seconds: float | None = None,
    ) -> None:
        self._config = config
        longest = max(
            config.cpu_sustain_s,
            config.lag_sustain_s,
            0.0,
        )
        span = window_seconds if window_seconds is not None else longest + 10.0
        capacity = max(16, int(span / max(config.sample_interval_s, 0.05)) + 8)
        self._window: deque[TriggerSample] = deque(maxlen=capacity)
        self._last_fire: dict[str, float] = {}
        self._last_capture_started: float | None = None

    def observe(self, sample: TriggerSample) -> list[TriggerDecision]:
        """Feed one sample and return the decisions that are allowed to fire.

        The cooldown is ENFORCED here (per reason fingerprint) but never
        STAMPED here (W1): a decision this method returns may never be
        serviced — the watchdog captures at most one decision per tick.
        Stamping on the ALLOWED decision would suppress an unserviced
        reason for the whole cooldown without it ever being captured.
        The stamp belongs to the CAPTURE PATH, via
        :meth:`note_capture_started`.

        The decision's ``since`` is the START of the over-threshold run.
        """
        self._window.append(sample)
        decisions = evaluate(list(self._window), self._config)
        allowed: list[TriggerDecision] = []
        now = sample.t
        for decision in decisions:
            last = self._last_fire.get(decision.reason)
            if last is not None and (now - last) < self._config.cooldown_s:
                continue
            allowed.append(decision)
        return allowed

    def note_capture_started(
        self,
        when: float | None = None,
        reason: str | None = None,
    ) -> None:
        """Record that a capture STARTED — the cooldown's reference point.

        This is the caller-ack from the capture path (W1): the per-reason
        cooldown is stamped ONLY on an ACTUAL capture. Pass the captured
        ``reason``; an unserviced decision stays armed for the next tick.
        """
        moment = time.monotonic() if when is None else when
        self._last_capture_started = moment
        if reason is not None:
            self._last_fire[reason] = moment

    @property
    def last_capture_started(self) -> float | None:
        """When the last capture started (``None`` if never)."""
        return self._last_capture_started

    def window(self) -> list[TriggerSample]:
        """The current rolling window (oldest first) — for tests."""
        return list(self._window)

    def cooldown_state(self) -> dict[str, float]:
        """The per-reason last-fire timestamps — for the status surface."""
        return dict(self._last_fire)


def samples_from(
    cpu_sample: Any,
    *,
    lag_s: float | None = None,
    lag_state: str = "unknown",
) -> TriggerSample:
    """Build a :class:`TriggerSample` from a :class:`CpuSample`."""
    return TriggerSample(
        t=float(getattr(cpu_sample, "t", 0.0)),
        process_cpu_pct=float(getattr(cpu_sample, "process_cpu_pct", 0.0)),
        process_cpu_delta_s=float(getattr(cpu_sample, "process_cpu_delta_s", 0.0)),
        top_thread_delta_s=float(
            getattr(getattr(cpu_sample, "top_thread", None), "cpu_delta_s", 0.0)
            if getattr(cpu_sample, "top_thread", None) is not None
            else 0.0
        ),
        top_thread_share_pct=float(getattr(cpu_sample, "top_thread_share_pct", 0.0)),
        top_thread_native_id=getattr(cpu_sample, "top_thread_native_id", None),
        top_thread_comm=str(getattr(cpu_sample, "top_thread_comm", "") or ""),
        lag_s=lag_s,
        lag_state=lag_state,
    )


__all__ = [
    "CANONICAL_REASONS",
    "CONCENTRATION_ABSOLUTE_FLOOR_PCT",
    "REASON_MANUAL",
    "REASON_SUSTAINED_CPU",
    "REASON_SUSTAINED_LOOP_LAG",
    "REASON_THREAD_CONCENTRATION",
    "TriggerDecision",
    "TriggerEngine",
    "TriggerSample",
    "evaluate",
    "samples_from",
]
