"""Generalized subprocess registry (T3 ``resource_pid_map`` live side).

Architecture §2.4 T3 / Phase-2 D4-D7: a process-wide live RAM dict
that the sampler reads each tick to enrich its per-instance
attribution with pid-derived metadata, plus an in-memory history
queue that the flusher drains into the ``resource_pid_map`` table
in batched ``executemany`` writes.

Two surfaces:

* **Live RAM dict** keyed by ``pid`` (P1 partial UNIQUE already
  enforces one live row per pid in :class:`ResourcePidMap`). Each
  entry carries ``start_time`` (Linux ``/proc/<pid>/stat`` field
  22 — kernel jiffies), ``pgid`` (field 5), ``kind`` discriminator
  (``bash`` | ``chart_render`` | ``mcp_child`` | ``service`` |
  ``proc``), and the optional identity (``instance_id``,
  ``agent_id``, ``command``, ``detail``, ``parent_pid``,
  ``spawned_at``).
* **History queue** of exited entries waiting for the flusher to
  batch-persist them. ``mark_exit`` removes an entry from the live
  dict and appends to the queue. ``drain_history`` is what the
  flusher calls once per window.

Public API (consumed by the integration coder + the bash/mcp/
chart callers):

* :func:`get_pid_registry` — module singleton accessor.
* :meth:`PidRegistry.register` — idempotent; ``(pid, start_time)``
  tuple is the key.
* :meth:`PidRegistry.mark_exit` — moves entry from live dict to
  history queue. ``exit_kind`` ∈ ``{"success", "timeout",
  "cancelled", "exit", "unknown"}``.
* :meth:`PidRegistry.lookup` — by ``pid``; verifies
  ``/proc/<pid>/stat`` field 22 matches the registered
  ``start_time`` (PID-recycle defense, AC4). Falls back to pgid
  (field 5) when the starttime check fails or /proc is unreadable.
* :meth:`PidRegistry.snapshot` — every live entry (the sampler
  reads this on every tick).
* :meth:`PidRegistry.drain_history` — pops all pending exited
  entries (the flusher reads this once per flush window).
* :meth:`PidRegistry.read_proc_stat_field` — shared helper that
  parses a single ``/proc/<pid>/stat`` integer field; exposed for
  test injection.

D14 alignment (secrets): stamps are scalars/ids only. Callers
sanitize the bash ``command`` string (executable/argv-shape, NEVER
the raw ``env`` overlay) BEFORE handing it here — the registry
itself does not redact.

Concurrency: a single ``threading.Lock`` covers both the live
dict and the history queue. Sampler tick reads + flusher writes
both go through the lock; lock hold time is bounded (no I/O while
held).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

logger = logging.getLogger(__name__)


# /proc/<pid>/stat field positions (1-indexed). After parsing the
# ``(comm)`` field (which may contain spaces and parens), the
# remaining fields live in a list whose element ``i`` corresponds
# to overall field ``i+3``. So field N maps to ``parts[N-3]``.
#
# Field 4  = ppid
# Field 5  = pgrp (PGID)        — pgid fallback for seam escapes
# Field 22 = starttime jiffies  — primary PID-recycle defense
#
# Source: https://man7.org/linux/man-pages/man5/proc.5.html
_STAT_FIELD_PPID = 4
_STAT_FIELD_PGID = 5
_STAT_FIELD_START_TIME = 22
# Offset from 1-indexed field number to 0-based ``parts`` index,
# accounting for the stripped ``(comm)`` field that sits in slot 2.
_STAT_PART_OFFSET = 3


# Default bound on the pending history queue. The flusher drains
# this every window; if the queue runs ahead of the flusher (e.g.,
# during a burst of short-lived bash exits) we drop the OLDEST
# entries and log a single warning. The sampler keeps the
# authoritative live view (per the §2.4 contract); the queue is
# only a buffer for the persistence lane.
HISTORY_QUEUE_CAP = 1024


# Kind discriminator enum (string-valued to keep the column type
# portable; matches the table DDL ``kind TEXT``). The bash lane
# defaults to ``"bash"``; ``chart_tools.py`` pushes
# ``"chart_render"`` into the ContextVar; the MCP lane sets
# ``"mcp_child"``.
KIND_BASH = "bash"
KIND_CHART_RENDER = "chart_render"
KIND_MCP_CHILD = "mcp_child"
KIND_SERVICE = "service"
KIND_PROC = "proc"

VALID_KINDS = frozenset(
    {KIND_BASH, KIND_CHART_RENDER, KIND_MCP_CHILD, KIND_SERVICE, KIND_PROC}
)

# Exit-kind values. Kept narrow so the flusher can index/sweep on
# it without exploding cardinality.
EXIT_KIND_SUCCESS = "success"
EXIT_KIND_TIMEOUT = "timeout"
EXIT_KIND_CANCELLED = "cancelled"
EXIT_KIND_EXIT = "exit"
EXIT_KIND_UNKNOWN = "unknown"

VALID_EXIT_KINDS = frozenset(
    {
        EXIT_KIND_SUCCESS,
        EXIT_KIND_TIMEOUT,
        EXIT_KIND_CANCELLED,
        EXIT_KIND_EXIT,
        EXIT_KIND_UNKNOWN,
    }
)


@dataclass
class PidRegistryEntry:
    """A single live (or just-exited) subprocess row.

    The flusher persists ``exited_at IS NOT NULL`` rows to
    ``resource_pid_map``; live rows (the majority of the snapshot)
    stay RAM-only. The dataclass shape mirrors the table columns
    so the flusher's INSERT can iterate ``entry.<col>``.
    """

    pid: int
    start_time: int  # kernel jiffies (Linux) or epoch seconds (macOS)
    kind: str
    spawned_at: str  # naive-UTC ISO "YYYY-MM-DD HH:MM:SS" (D9)
    pgid: Optional[int] = None
    instance_id: Optional[str] = None  # NULLABLE — D11 (pool warmups)
    agent_id: Optional[str] = None
    command: Optional[str] = None
    detail: Optional[str] = None
    parent_pid: Optional[int] = None
    # Exit-side fields; ``exited_at`` carries the same naive-UTC ISO
    # shape as ``spawned_at`` (D9 consistency).
    exited_at: Optional[str] = None
    exit_code: Optional[int] = None
    exit_kind: Optional[str] = None
    peak_rss_kb: Optional[int] = None
    cpu_user_usec: Optional[int] = None


def _now_naive_utc() -> datetime:
    """Return ``datetime.utcnow()``-equivalent (D9 naive-UTC).

    Mirrors ``flusher._default_now_utc_naive`` — kept inline so the
    registry does not import from the flusher module (avoids a
    package-internal cycle).
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _now_naive_utc_iso() -> str:
    """Naive-UTC ISO string ("YYYY-MM-DD HH:MM:SS") matching D9."""
    return _now_naive_utc().strftime("%Y-%m-%d %H:%M:%S")


def _parse_proc_stat_field(stat_text: str, field_1indexed: int) -> Optional[int]:
    """Return the integer at ``field_1indexed`` of a /proc stat line.

    Tolerates spaces/parens inside ``(comm)`` by locating the LAST
    ``)`` and splitting after it. Returns ``None`` on any parse
    failure (caller falls back to other defenses).
    """
    if not stat_text:
        return None
    last_paren = stat_text.rfind(")")
    if last_paren == -1:
        return None
    _, _, tail = stat_text.rpartition(")")
    parts = tail.strip().split()
    try:
        return int(parts[field_1indexed - _STAT_PART_OFFSET])
    except (ValueError, IndexError):
        return None


def read_proc_stat_field(
    proc_root: Union[str, Path], pid: int, field_1indexed: int
) -> Optional[int]:
    """Read a single integer field from ``/proc/<pid>/stat``.

    For ``pid == os.getpid()`` we honour the real ``/proc/self``
    symlink contract (matches ``proc_readers._read_proc_stat``).
    For any other pid we ONLY consult the literal pid directory —
    a child must NOT silently read the daemon's stat under a
    ``/proc/self`` fixture.

    Args:
        proc_root: Synthetic or real /proc root.
        pid: Target PID (or ``os.getpid()`` for the daemon).
        field_1indexed: 1-indexed field number from
            ``man 5 proc`` (e.g. 22 = starttime jiffies,
            5 = pgid, 4 = ppid).

    Returns:
        The integer at that field, or ``None`` on missing-file /
        unparseable / process-gone.
    """
    proc_root = Path(proc_root)
    if pid == os.getpid():
        bases = [proc_root / "self", proc_root / str(pid)]
    else:
        bases = [proc_root / str(pid)]
    for base in bases:
        try:
            raw = base.joinpath("stat").read_text()
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            continue
        first_line = raw.splitlines()[0] if raw else ""
        result = _parse_proc_stat_field(first_line, field_1indexed)
        if result is not None:
            return result
    return None


class PidRegistry:
    """Process-wide singleton live RAM registry + history queue.

    See module docstring for the public-API surface. The class is
    intentionally small — no I/O, no scheduling. Both the live
    dict and the history queue share ONE lock; all critical
    sections are bounded (no awaits, no logging-with-fmt that
    could block).
    """

    def __init__(
        self,
        *,
        proc_root: Union[str, Path] = "/proc",
        wall_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # The live dict is keyed by ``pid``. Per the AC4 contract
        # the starttime check at lookup time is the canonical
        # PID-recycle defense; the dict itself stays pid-only to
        # keep the hot path O(1).
        self._live: dict[int, PidRegistryEntry] = {}
        # Exited entries waiting for the flusher. Bounded
        # drop-oldest (HISTORY_QUEUE_CAP). Drain happens inside the
        # lock so the flusher sees a consistent snapshot.
        self._history: list[PidRegistryEntry] = []
        self._lock = threading.Lock()
        self._proc_root = Path(proc_root)
        # Injectable for tests; the live ``time.monotonic`` is fine
        # in production (used only as a soft debug anchor today).
        self._wall_clock = wall_clock

    # ── Public API — live side ───────────────────────────────────────

    def register(
        self,
        *,
        pid: int,
        start_time: int,
        kind: str,
        instance_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        command: Optional[str] = None,
        detail: Optional[str] = None,
        parent_pid: Optional[int] = None,
        pgid: Optional[int] = None,
    ) -> PidRegistryEntry:
        """Idempotently register a live subprocess.

        ``(pid, start_time)`` is the key. If a live entry with the
        same ``pid`` already exists AND its ``start_time`` matches,
        this is a NO-OP and the existing entry is returned. If
        the existing entry has a DIFFERENT ``start_time``, the
        caller is racing with a recycled pid — the existing entry
        is force-exited (so the partial UNIQUE ``WHERE
        exited_at IS NULL`` is freed) and the new entry takes its
        place. The seam caller (bash / mcp / chart) is the only
        authoritative source of ``start_time`` — we never derive
        it here.

        Args:
            pid: Kernel PID of the spawned subprocess.
            start_time: Linux jiffies (``/proc/<pid>/stat`` field
                22) or epoch seconds (macOS ``ps -o lstart`` parse).
            kind: Discriminator — must be one of ``VALID_KINDS``.
            instance_id: Owning instance (NULLABLE — D11, server-
                scoped pool warmups).
            agent_id: Owning agent (when known).
            command: Sanitized executable/argv-shape (D14 —
                callers MUST scrub any secret-bearing text
                BEFORE handing it here).
            detail: Free-form auxiliary context (e.g. MCP server
                name; truncated as needed).
            parent_pid: Recorded for tree reconstruction (the
                ``service_tracking`` precedent).
            pgid: Eager-captured PGID (bash.py pattern); pass
                ``None`` when the spawn site does not derive one.

        Returns:
            The live entry (existing or newly inserted).
        """
        if kind not in VALID_KINDS:
            raise ValueError(
                f"pid_registry.register: invalid kind {kind!r}; "
                f"expected one of {sorted(VALID_KINDS)}"
            )
        spawned_at = _now_naive_utc_iso()
        with self._lock:
            existing = self._live.get(pid)
            if existing is not None:
                if existing.start_time == start_time:
                    # Idempotent re-register (e.g. chart tool retries
                    # the same charter invocation). Keep the original
                    # ``spawned_at``; the caller has not changed.
                    return existing
                # PID recycled — force-exit the old entry so the
                # partial UNIQUE constraint frees the slot, then
                # insert the new one. ``mark_exit`` re-enters the
                # lock, so we do the equivalent inline here.
                existing.exited_at = _now_naive_utc_iso()
                existing.exit_kind = EXIT_KIND_UNKNOWN
                existing.exit_code = None
                self._enqueue_history_locked(existing)
                del self._live[pid]
                logger.warning(
                    "pid_registry: pid=%d recycled (old start_time=%d, "
                    "new start_time=%d) — force-exited old entry",
                    pid, existing.start_time, start_time,
                )
            entry = PidRegistryEntry(
                pid=pid,
                start_time=start_time,
                kind=kind,
                spawned_at=spawned_at,
                pgid=pgid,
                instance_id=instance_id,
                agent_id=agent_id,
                command=command,
                detail=detail,
                parent_pid=parent_pid,
            )
            self._live[pid] = entry
            return entry

    def mark_exit(
        self,
        *,
        pid: int,
        start_time: int,
        exit_code: Optional[int] = None,
        exit_kind: str = EXIT_KIND_UNKNOWN,
        peak_rss_kb: Optional[int] = None,
        cpu_user_usec: Optional[int] = None,
    ) -> Optional[PidRegistryEntry]:
        """Mark a live entry as exited; enqueue for history flush.

        Refuses to touch an entry whose ``start_time`` does NOT
        match the caller's value (PID-recycle defense: the caller
        must trust its own captured start_time). If no live entry
        exists for ``pid`` the call is a NO-OP (idempotent — the
        timeout arm + cancellation arm + success arm may all
        race against a fast exit; only one wins).

        Args:
            pid: Kernel PID of the subprocess.
            start_time: Caller-captured start_time jiffies (the
                bash lane captures this at spawn — re-verifying
                against /proc after exit is a TOCTOU race, M3).
            exit_code: Process exit code (``None`` if unknown —
                e.g. signal-killed).
            exit_kind: One of ``VALID_EXIT_KINDS``.
            peak_rss_kb: Best-effort VmHWM (the bash lane reads
                ``/proc/<pid>/status`` BEFORE reap; ``None`` if
                the read raced and missed).
            cpu_user_usec: Best-effort ``utime`` jiffies.

        Returns:
            The now-exited entry, or ``None`` if no live row
            matched.
        """
        if exit_kind not in VALID_EXIT_KINDS:
            raise ValueError(
                f"pid_registry.mark_exit: invalid exit_kind {exit_kind!r}; "
                f"expected one of {sorted(VALID_EXIT_KINDS)}"
            )
        with self._lock:
            entry = self._live.get(pid)
            if entry is None or entry.start_time != start_time:
                return None
            entry.exited_at = _now_naive_utc_iso()
            entry.exit_code = exit_code
            entry.exit_kind = exit_kind
            entry.peak_rss_kb = peak_rss_kb
            entry.cpu_user_usec = cpu_user_usec
            del self._live[pid]
            self._enqueue_history_locked(entry)
            return entry

    def lookup(self, pid: int) -> Optional[PidRegistryEntry]:
        """Return the live entry for ``pid`` with PID-recycle defense.

        Verification ladder (AC4):

        1. If no live entry for ``pid`` ⇒ ``None``.
        2. Read ``/proc/<pid>/stat`` field 22 (``starttime``).
           If it matches the entry's ``start_time`` ⇒ return
           the entry (canonical case).
        3. If the starttime check fails BUT ``/proc/<pid>/stat``
           field 5 (``pgrp``) matches the entry's ``pgid`` ⇒
           return the entry (pgid fallback for seam escapes
           — the new_thread's starttime may be unreadable in
           a tight race window but the pgid is).
        4. Otherwise ⇒ ``None`` (the pid was recycled to a
           different process; the entry's caller must use the
           starttime it captured at spawn, not what is in /proc
           now).

        Set ``proc_root`` via the ``read_proc_stat_field`` injection
        when running under a synthetic /proc fixture (the
        production default is ``/proc``).
        """
        with self._lock:
            entry = self._live.get(pid)
        if entry is None:
            return None
        # No I/O while holding the lock (AC4: cheap lookup path).
        starttime_now = read_proc_stat_field(
            self._proc_root, pid, _STAT_FIELD_START_TIME
        )
        if starttime_now is not None and starttime_now == entry.start_time:
            return entry
        if entry.pgid is not None:
            pgid_now = read_proc_stat_field(
                self._proc_root, pid, _STAT_FIELD_PGID
            )
            if pgid_now is not None and pgid_now == entry.pgid:
                return entry
        return None

    def snapshot(self) -> list[PidRegistryEntry]:
        """Return a shallow copy of every live entry (sampler reads).

        The sampler iterates this list each tick to enrich its
        per-instance attribution with pid-derived metadata. The
        returned list is a fresh copy so the caller can iterate
        without holding the registry lock.
        """
        with self._lock:
            return list(self._live.values())

    def live_count(self) -> int:
        """Return the current size of the live dict (cheap probe)."""
        with self._lock:
            return len(self._live)

    def pending_history(self) -> int:
        """Return the current size of the history queue (cheap probe)."""
        with self._lock:
            return len(self._history)

    # ── Public API — history side ────────────────────────────────────

    def drain_history(self) -> list[PidRegistryEntry]:
        """Pop every pending exited entry (the flusher reads this).

        Returns:
            A fresh list containing every entry that was pending
            at the moment of the drain. The caller is responsible
            for INSERTing them and may batch in a single
            ``executemany`` (the flusher pattern).
        """
        with self._lock:
            entries = self._history
            self._history = []
            return entries

    # ── Test helpers ─────────────────────────────────────────────────

    def _reset(self) -> None:
        """Wipe both the live dict and the history queue.

        Tests call this in their fixture teardown; not for
        production use (the registry is process-lifetime).
        """
        with self._lock:
            self._live.clear()
            self._history.clear()

    def _enqueue_history_locked(self, entry: PidRegistryEntry) -> None:
        """Append ``entry`` to the history queue; bounded drop-oldest.

        Caller MUST hold ``self._lock``. Exposed by the
        leading-underscore name as a deliberate "internal" marker
        (the flusher does not call this; tests do not either).
        """
        if len(self._history) >= HISTORY_QUEUE_CAP:
            dropped = len(self._history) - HISTORY_QUEUE_CAP + 1
            self._history = self._history[-HISTORY_QUEUE_CAP + 1:]
            logger.warning(
                "pid_registry: history queue overflow — dropped %d "
                "oldest exited entries (cap=%d)",
                dropped, HISTORY_QUEUE_CAP,
            )
        self._history.append(entry)


# ── Process-wide singleton ─────────────────────────────────────────────────


_pid_registry: Optional[PidRegistry] = None
_pid_registry_lock = threading.Lock()


def get_pid_registry() -> PidRegistry:
    """Return the process-wide :class:`PidRegistry` singleton.

    Lazily constructed on the first call. The facade and tests
    both consume this accessor; the registry itself never raises
    on construction.
    """
    global _pid_registry
    if _pid_registry is None:
        with _pid_registry_lock:
            if _pid_registry is None:
                _pid_registry = PidRegistry()
    return _pid_registry


def reset_pid_registry_for_tests(
    *,
    proc_root: Union[str, Path] = "/proc",
) -> PidRegistry:
    """Drop the singleton and build a fresh one (tests only).

    Returns the new instance so tests can hold a reference and
    call its methods directly. Production code must use
    :func:`get_pid_registry`.
    """
    global _pid_registry
    with _pid_registry_lock:
        _pid_registry = PidRegistry(proc_root=proc_root)
    return _pid_registry


__all__ = [
    "HISTORY_QUEUE_CAP",
    "KIND_BASH",
    "KIND_CHART_RENDER",
    "KIND_MCP_CHILD",
    "KIND_SERVICE",
    "KIND_PROC",
    "VALID_KINDS",
    "EXIT_KIND_SUCCESS",
    "EXIT_KIND_TIMEOUT",
    "EXIT_KIND_CANCELLED",
    "EXIT_KIND_EXIT",
    "EXIT_KIND_UNKNOWN",
    "VALID_EXIT_KINDS",
    "PidRegistryEntry",
    "PidRegistry",
    "get_pid_registry",
    "reset_pid_registry_for_tests",
    "read_proc_stat_field",
]