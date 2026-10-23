"""Process + per-thread CPU sampler for the wedge-capture instrument (D5 / D6 / T3).

WHAT IT MEASURES
----------------
* the PROCESS CPU, as a percentage of **ONE core** (the value may
  exceed 100 on a multi-core host — the incident metric was "95–107%
  of ONE core", so a "percent of all cores" normalisation would miss
  the shape entirely);
* the PER-THREAD CPU, so a burning thread can be NAMED — the H1 vs
  H2 discriminator.

THE /proc READERS ARE **REUSED READ-ONLY** (A-F7(iii))
--------------------------------------------------------
``daemon/services/resource_monitor/proc_readers.py`` already speaks
this tree's ``/proc`` dialect (``_parse_proc_stat``). The sampler
IMPORTS those readers and does NOT modify them; the readers are NOT
extended with wedge-specific fields. One ``/proc`` dialect in the
tree.

THE OBSERVER-EXCLUSION MECHANISM (MR-6)
----------------------------------------
The sampler CAN filter the observer out of ``per_thread``: the
exclusion applies to thread ids passed via ``exclude_native_ids`` /
``exclude_thread`` — including the observer would inflate the measured
concentration with the observer's own sampling cost. The daemon
wiring does not currently pass the watchdog's tid, so the mechanism
is available but not engaged there.

EVERY THREAD CARRIES BOTH NAMES (D6)
------------------------------------
``native_id`` (the Python/OS thread id) and ``comm`` (the raw
``/proc/self/task/<tid>/comm`` name) are recorded for EVERY thread,
so the attribution stays auditable even if the ``native_id`` mapping
turns out to be wrong. The tree has ZERO precedent for
``threading.get_native_id()`` — hence the redundancy.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from daemon.services.resource_monitor.proc_readers import _parse_proc_stat

def _clock_ticks_per_second() -> int:
    """Resolve the ``USER_HZ`` jiffy rate (sysconf-authoritative; 100 fallback)."""
    try:
        value = int(os.sysconf("SC_CLK_TCK"))
    except (AttributeError, ValueError, OSError):  # pragma: no cover
        return 100
    return value if value > 0 else 100


#: ``USER_HZ`` — the jiffy rate of ``/proc/<pid>/stat``'s utime/stime.
#: ``os.sysconf("SC_CLK_TCK")`` is authoritative; the tree's own readers
#: hard-code 100 with a comment, so 100 is the fallback.
CLOCK_TICKS_PER_SECOND = _clock_ticks_per_second()

#: The default ``/proc`` root (the tree is Linux-only).
PROC_ROOT = Path("/proc")


@dataclass
class ThreadSample:
    """One thread's CPU attribution (the canonical matrix §9 field names).

    Attributes:
        tid: The OS thread id as it appears in ``/proc/self/task``.
        native_id: The Python-visible native thread id (``threading``'s
            ``get_native_id()``), or ``None`` when the mapping failed.
        name: The Python thread name, or ``None``.
        comm: The raw OS thread name from ``/proc/self/task/<tid>/comm``.
        cpu_delta_s: The CPU seconds consumed since the previous sample.
        share_pct: This thread's share of the PROCESS CPU delta.
    """

    tid: int
    native_id: int | None
    name: str | None
    comm: str | None
    cpu_delta_s: float
    share_pct: float

    def as_dict(self) -> dict[str, Any]:
        """The CSV row shape (the canonical per-thread field names)."""
        return {
            "tid": self.tid,
            "native_id": self.native_id,
            "name": self.name,
            "comm": self.comm,
            "cpu_delta_s": round(self.cpu_delta_s, 6),
            "share_pct": round(self.share_pct, 3),
        }


@dataclass
class CpuSample:
    """One sampler tick's CPU payload.

    Attributes:
        t: The monotonic timestamp of the tick.
        process_cpu_pct: The PROCESS CPU since the previous tick, as a
            percentage of ONE core.
        process_cpu_delta_s: The same value in seconds.
        per_thread: The per-thread attribution, EXCLUDING the
            observer's own thread (MR-6).
        loop_thread_native_id: The native id of the thread running the
            event loop, when known.
        tick_delay_s: The watcher's own tick delay — a GIL-contention
            signal, NEVER a trigger input (D4(a)(f)).
        top_thread: The highest-share thread of this tick, or ``None``.
    """

    t: float
    process_cpu_pct: float
    process_cpu_delta_s: float
    per_thread: list[ThreadSample] = field(default_factory=list)
    loop_thread_native_id: int | None = None
    tick_delay_s: float = 0.0

    @property
    def top_thread(self) -> ThreadSample | None:
        """The busiest thread of this tick, or ``None``."""
        if not self.per_thread:
            return None
        return max(self.per_thread, key=lambda t: t.cpu_delta_s)

    @property
    def top_thread_share_pct(self) -> float:
        """The top thread's share of the process CPU (0.0 when idle)."""
        top = self.top_thread
        return top.share_pct if top is not None else 0.0

    @property
    def top_thread_native_id(self) -> int | None:
        """The top thread's native id, when known."""
        top = self.top_thread
        return top.native_id if top is not None else None

    @property
    def top_thread_comm(self) -> str:
        """The top thread's OS comm name ("" when unknown)."""
        top = self.top_thread
        return (top.comm or "") if top is not None else ""


def _read_thread_comm(task_dir: Path) -> str | None:
    """Read ``/proc/self/task/<tid>/comm`` — the raw OS thread name."""
    try:
        raw = (task_dir / "comm").read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    return raw.strip() or None


def _read_thread_jiffies(task_dir: Path) -> tuple[int, int] | None:
    """Read one thread's ``(utime, stime)`` jiffies, or ``None``."""
    try:
        raw = (task_dir / "stat").read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    if not raw:
        return None
    first_line = raw.splitlines()[0] if raw else ""
    parsed = _parse_proc_stat(first_line)
    if parsed is None:
        return None
    return parsed["utime"], parsed["stime"]


def _read_process_jiffies(proc_root: Path) -> tuple[int, int] | None:
    """Read the PROCESS ``(utime, stime)`` jiffies, or ``None``."""
    for base in (proc_root / "self", proc_root / str(os.getpid())):
        try:
            raw = (base / "stat").read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        if not raw:
            continue
        first_line = raw.splitlines()[0] if raw else ""
        parsed = _parse_proc_stat(first_line)
        if parsed is None:
            continue
        return parsed["utime"], parsed["stime"]
    return None


def _python_thread_names() -> dict[int, tuple[int | None, str | None]]:
    """Map ``native_id`` → ``(native_id, python thread name)``.

    The mapping is best-effort: a thread that exits between the
    ``/proc`` sweep and this call simply has no Python name — and the
    ``comm`` column still carries its identity (D6).
    """
    out: dict[int, tuple[int | None, str | None]] = {}
    try:
        threads = threading.enumerate()
    except RuntimeError:  # pragma: no cover — defensive
        return out
    for thread in threads:
        try:
            native_id = int(thread.native_id)
        except (AttributeError, TypeError, ValueError):  # pragma: no cover
            continue
        out[native_id] = (native_id, thread.name)
    return out


class CpuSampler:
    """The 1 Hz CPU sampler.

    The sampler keeps the PREVIOUS ``/proc`` reading so every tick
    reports a DELTA. The first tick has no predecessor: it reports
    zero CPU (never a fabricated number) and establishes the baseline.

    Args:
        proc_root: The ``/proc`` root (tests point this at a fixture).
        exclude_native_ids: Thread ids to EXCLUDE from the attribution
            (the watchdog's own thread — MR-6).
    """

    def __init__(
        self,
        *,
        proc_root: Path = PROC_ROOT,
        exclude_native_ids: frozenset[int] | set[int] | None = None,
    ) -> None:
        self._proc_root = Path(proc_root)
        self._exclude = set(exclude_native_ids or ())
        self._prev_process: tuple[int, int] | None = None
        self._prev_threads: dict[int, tuple[int, int]] = {}
        self._last_tick_started: float | None = None
        self._last_threads: list[ThreadSample] = []

    @property
    def last_threads(self) -> list[ThreadSample]:
        """The most recent tick's per-thread attribution."""
        return list(self._last_threads)

    # ── the previous-tick bookkeeping ──────────────────────────────────
    def reset(self) -> None:
        """Test seam: forget the previous reading — the next tick
        re-baselines. No production caller."""
        self._prev_process = None
        self._prev_threads = {}
        self._last_tick_started = None

    def exclude_thread(self, native_id: int | None) -> None:
        """Exclude a thread (the observer) from the attribution."""
        if native_id is not None:
            self._exclude.add(int(native_id))

    # ── the sample ─────────────────────────────────────────────────────
    def sample(
        self,
        *,
        loop_thread_native_id: int | None = None,
    ) -> CpuSample:
        """Take ONE sample and return the CPU payload.

        Never raises — a ``/proc`` read failure degrades the sample
        (zero CPU, an empty thread list), never the caller.
        """
        now = time.monotonic()
        tick_delay = 0.0
        if self._last_tick_started is not None:
            tick_delay = max(0.0, now - self._last_tick_started)
        self._last_tick_started = now

        process = _read_process_jiffies(self._proc_root)
        threads = self._read_threads()

        process_delta_s = 0.0
        if process is not None and self._prev_process is not None:
            delta_jiffies = (
                (process[0] - self._prev_process[0])
                + (process[1] - self._prev_process[1])
            )
            # A negative delta means the counter wrapped or the process
            # was replaced — report zero, never a negative.
            if delta_jiffies > 0:
                process_delta_s = delta_jiffies / CLOCK_TICKS_PER_SECOND

        per_thread: list[ThreadSample] = []
        total_thread_delta_s = 0.0
        python_names = _python_thread_names()
        for tid, jiffies in threads.items():
            previous = self._prev_threads.get(tid)
            delta_jiffies = 0
            if previous is not None:
                delta_jiffies = (jiffies[0] - previous[0]) + (jiffies[1] - previous[1])
            if delta_jiffies < 0:
                delta_jiffies = 0
            delta_s = delta_jiffies / CLOCK_TICKS_PER_SECOND
            total_thread_delta_s += delta_s
            if tid in self._exclude:
                # MR-6 — the observer is NOT part of the observed.
                continue
            comm = _read_thread_comm(self._proc_root / "self" / "task" / str(tid))
            entry = python_names.get(tid)
            per_thread.append(
                ThreadSample(
                    tid=tid,
                    native_id=tid,
                    name=entry[1] if entry is not None else None,
                    comm=comm,
                    cpu_delta_s=delta_s,
                    share_pct=0.0,
                )
            )

        # The share is computed against the PROCESS delta when the
        # process reading is available (the canonical denominator), and
        # against the summed thread deltas otherwise.
        denominator = process_delta_s if process_delta_s > 0 else total_thread_delta_s
        if denominator > 0:
            for item in per_thread:
                item.share_pct = (item.cpu_delta_s / denominator) * 100.0

        per_thread.sort(key=lambda t: t.cpu_delta_s, reverse=True)
        self._last_threads = list(per_thread)

        # Baseline bookkeeping happens AFTER the deltas are computed.
        if process is not None:
            self._prev_process = process
        self._prev_threads = threads

        process_cpu_pct = (process_delta_s / max(tick_delay, 1e-9)) * 100.0
        return CpuSample(
            t=now,
            process_cpu_pct=process_cpu_pct,
            process_cpu_delta_s=process_delta_s,
            per_thread=per_thread,
            loop_thread_native_id=loop_thread_native_id,
            tick_delay_s=tick_delay,
        )

    def _read_threads(self) -> dict[int, tuple[int, int]]:
        """Sweep ``/proc/self/task/*/stat`` — every live thread's jiffies."""
        out: dict[int, tuple[int, int]] = {}
        task_root = self._proc_root / "self" / "task"
        try:
            entries = list(task_root.iterdir())
        except (OSError, ValueError):
            return out
        for entry in entries:
            try:
                tid = int(entry.name)
            except (TypeError, ValueError):
                continue
            jiffies = _read_thread_jiffies(entry)
            if jiffies is None:
                continue
            out[tid] = jiffies
        return out


__all__ = [
    "CLOCK_TICKS_PER_SECOND",
    "CpuSample",
    "CpuSampler",
    "PROC_ROOT",
    "ThreadSample",
]
