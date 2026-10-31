"""Generic windowed log-sumup aggregator (log-noise-hygiene).

Replaces per-event log lines with ONE ``INFO`` sumup line per family per
window. Hot-path callers fold an event via :func:`record` — O(1), a short
per-family dict lock only, NO logging, NO I/O — and a light daemon timer
thread flushes due families on the window cadence (default 180s, env
``ENSEMBLE_LOG_SUMUP_WINDOW_S``).

Design contract:

* **Fail-open, always.** :func:`record` and the flush path wrap every
  step in a broad ``except`` — aggregation must NEVER raise into the
  caller (the same posture as the checkpoint adapter's observability
  seams) and must NEVER crash the flush timer (one bad family cannot
  stall the others).
* **No per-event logging.** :func:`record` only mutates the family's
  accumulators under its lock.
* **Family registry.** Families register via :func:`ensure_family` with
  a ``render`` callable that turns a snapshot dict into the one-line
  string (or ``None`` to decline the emit — e.g. idle windows for
  families that must stay silent when quiet). First registration wins;
  re-registration is a no-op so call sites can invoke ``ensure_family``
  unconditionally.
* **Field convention** (all optional, folded by :func:`record`):
  ``count`` — event count this window (default 1 per call);
  ``sums`` — numeric accumulators ``{key: delta}``;
  ``lasts`` — last-observed values ``{key: value}``;
  ``sets`` — plain overwrites ``{key: value}`` (e.g. the active model).
  The snapshot handed to ``render`` adds ``family``, ``window_s``,
  ``last_event_age_s`` (monotonic seconds since the last record; None
  when the window saw none) alongside ``count``/``sums``/``lasts``/``sets``.
* **Emit outside the lock.** The flush takes the snapshot under the
  family lock, resets the accumulators, releases, THEN renders + logs.
  The lock is never held across I/O.
* **Idle windows.** Families registered with ``emit_idle=True`` render
  even for a zero-record window (the LLM heartbeat's idle variant);
  default families stay silent when a window saw nothing.

Test hooks (mirroring the ``reset_for_tests`` convention of
``daemon/checkpoint_metrics.py`` / ``daemon/services/metrics_gate.py``):
:func:`flush` / :func:`flush_all_for_tests` force a flush regardless of
the window (returning the emitted line(s)); :func:`reset_for_tests`
stops the timer and clears the registry.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Optional

#: The default flush window, in seconds (env ``ENSEMBLE_LOG_SUMUP_WINDOW_S``).
DEFAULT_WINDOW_S = 180

#: How often the flush timer wakes to scan for due windows. Window
#: boundaries are therefore quantized to ~this granularity — deliberate:
#: the timer exists so QUIET windows still flush (idle heartbeats); a
#: 5s scan cadence is negligible drift on 180s windows.
_TIMER_INTERVAL_S = 5.0

#: ``render(snapshot) -> line | None``. Return ``None`` to decline the emit.
RenderFn = Callable[[dict[str, Any]], Optional[str]]


def _default_window_s() -> int:
    """The env-tunable default window (clamped to >= 1s; bad values fall back)."""
    raw = os.environ.get("ENSEMBLE_LOG_SUMUP_WINDOW_S")
    if raw is None or not raw.strip():
        return DEFAULT_WINDOW_S
    try:
        window = int(raw.strip())
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_S
    return max(1, window)


class _Family:
    """One family's accumulators + its own short lock (no registry-wide lock
    is ever held across a fold — one hot family cannot stall another)."""

    __slots__ = (
        "name", "lock", "window_s", "window_started", "count",
        "sums", "lasts", "sets", "last_event", "render", "logger", "emit_idle",
    )

    def __init__(
        self,
        name: str,
        *,
        render: RenderFn,
        logger: logging.Logger,
        window_s: int,
        emit_idle: bool,
    ) -> None:
        self.name = name
        self.lock = threading.Lock()
        self.window_s = window_s
        self.window_started = time.monotonic()
        self.count = 0
        self.sums: dict[str, Any] = {}
        self.lasts: dict[str, Any] = {}
        self.sets: dict[str, Any] = {}
        self.last_event: Optional[float] = None
        self.render = render
        self.logger = logger
        self.emit_idle = emit_idle


#: family name -> state. Registration is rare (once per process per
#: family); the dict is read on every record() without a lock — safe
#: because registration only INSERTS (never mutates existing entries)
#: and dict reads of existing keys are atomic under the GIL.
_registry: dict[str, _Family] = {}
_registry_lock = threading.Lock()

_timer: Optional[threading.Thread] = None
_timer_stop = threading.Event()
_timer_lock = threading.Lock()


def ensure_family(
    name: str,
    *,
    render: RenderFn,
    logger: Optional[logging.Logger] = None,
    window_s: Optional[int] = None,
    emit_idle: bool = False,
) -> None:
    """Idempotent family registration — first registration wins.

    Safe to call on every hot-path site (the repeat call is a dict
    lookup + early return). Starts the flush timer lazily on the FIRST
    registration so a process that never uses sumups pays nothing.
    """
    with _registry_lock:
        if name in _registry:
            return
        if window_s is None or window_s <= 0:
            window_s = _default_window_s()
        _registry[name] = _Family(
            name,
            render=render,
            logger=logger if logger is not None else logging.getLogger(name),
            window_s=window_s,
            emit_idle=emit_idle,
        )
    _ensure_timer()


def record(
    family: str,
    *,
    count: int = 1,
    sums: Optional[dict[str, Any]] = None,
    lasts: Optional[dict[str, Any]] = None,
    sets: Optional[dict[str, Any]] = None,
) -> None:
    """Fold ONE event into ``family`` — O(1), thread-safe, NEVER raises,
    NEVER logs. Unknown family = silent no-op (the family installs on
    its first real event via ``ensure_family``)."""
    try:
        fam = _registry.get(family)
        if fam is None:
            return
        now = time.monotonic()
        with fam.lock:
            try:
                fam.count += int(count)
            except (TypeError, ValueError):
                fam.count += 1
            if sums:
                for key, value in sums.items():
                    try:
                        fam.sums[key] = fam.sums.get(key, 0) + value
                    except (TypeError, ValueError):
                        continue  # fail-open: one bad field skips, not the fold
            if lasts:
                fam.lasts.update(lasts)
            if sets:
                fam.sets.update(sets)
            fam.last_event = now
    except Exception:  # noqa: BLE001 — aggregation must NEVER raise into the caller
        pass


def _snapshot_and_reset(fam: _Family, now: float, *, force: bool = False) -> Optional[dict[str, Any]]:
    """Take the window snapshot under the family lock and reset the
    accumulators. ``None`` when the window is not due (unless forced)."""
    with fam.lock:
        if not force and (now - fam.window_started) < fam.window_s:
            return None
        snapshot = {
            "family": fam.name,
            "window_s": fam.window_s,
            "count": fam.count,
            "sums": dict(fam.sums),
            "lasts": dict(fam.lasts),
            "sets": dict(fam.sets),
            "last_event_age_s": (
                (now - fam.last_event) if fam.last_event is not None else None
            ),
        }
        fam.count = 0
        fam.sums = {}
        fam.lasts = {}
        fam.sets = {}
        fam.last_event = None
        fam.window_started = now
    return snapshot


def _emit_family(fam: _Family, snapshot: Optional[dict[str, Any]]) -> Optional[str]:
    """Render + log OUTSIDE the family lock. Returns the emitted line."""
    if snapshot is None:
        return None
    if not snapshot["count"] and not fam.emit_idle:
        return None  # quiet window, family declines idle emission
    try:
        line = fam.render(snapshot)
    except Exception:  # noqa: BLE001 — a bad renderer must not kill the timer
        return None
    if not line:
        return None
    try:
        fam.logger.info(line)
    except Exception:  # noqa: BLE001 — logging must never break the flush
        return None
    return line


def flush(family: str, *, force: bool = False) -> Optional[str]:
    """Flush one family now (timer seam / test seam).

    ``force=True`` bypasses the window check (snapshot-what-you-have).
    Returns the emitted line, or ``None`` when the family is unknown /
    declined / silent.
    """
    try:
        fam = _registry.get(family)
        if fam is None:
            return None
        return _emit_family(fam, _snapshot_and_reset(fam, time.monotonic(), force=force))
    except Exception:  # noqa: BLE001 — fail-open
        return None


def flush_all_for_tests() -> list[str]:
    """Force-flush EVERY registered family; returns the emitted lines."""
    lines: list[str] = []
    for name in list(_registry.keys()):
        line = flush(name, force=True)
        if line:
            lines.append(line)
    return lines


def _ensure_timer() -> None:
    """Start the daemon flush timer (once per process; restart-safe)."""
    global _timer
    with _timer_lock:
        if _timer is not None and _timer.is_alive():
            return
        _timer_stop.clear()
        _timer = threading.Thread(
            target=_timer_loop, name="log-sumup-timer", daemon=True
        )
        _timer.start()


def _timer_loop() -> None:
    """Wake on a fixed cadence and flush every DUE window. One bad family
    (or one bad tick) must never stop the scan — broad except per family."""
    while not _timer_stop.wait(_TIMER_INTERVAL_S):
        try:
            now = time.monotonic()
            for fam in list(_registry.values()):
                try:
                    snapshot = _snapshot_and_reset(fam, now)
                    if snapshot is not None:
                        _emit_family(fam, snapshot)
                except Exception:  # noqa: BLE001 — per-family isolation
                    continue
        except Exception:  # noqa: BLE001 — the timer loop never dies
            continue


def reset_for_tests() -> None:
    """Stop the timer + drop every family (test-only — mirrors the
    ``reset_for_tests`` convention). A later ``ensure_family`` restarts
    a fresh timer."""
    global _timer
    _timer_stop.set()
    with _registry_lock:
        _registry.clear()
    with _timer_lock:
        _timer = None
