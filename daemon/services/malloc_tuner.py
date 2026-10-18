"""Idle-time glibc heap trim — resource-cost-control commission, item O1
(optional arm).

``trim_malloc_heap`` wraps glibc's ``malloc_trim(0)`` behind a
never-raises contract so a host loop can call it without any failure mode
escaping: a missing symbol (musl/Alpine, macOS), a library-load failure,
or a call error all collapse to ``False``.

It is registered by ``daemon/manager.py`` on the EXISTING
``MaintenanceService`` loop (hourly, idle-gated) — no new thread, no new
asyncio task, per the O1 brief ("piggybacked on an EXISTING slow loop").
``malloc_trim(0)`` walks the glibc free lists and hands whole free heap
pages back to the OS, which is the step that visibly shrinks daemon anon
RSS after per-turn transients have been freed to the allocator but not
yet returned to the kernel.

glibc-only by nature; on every other platform this is a cheap no-op
returning ``False`` (the companion tuning — the arena cap itself — rides
the pre-exec surfaces: the systemd unit and launcher.sh).
"""

from __future__ import annotations

import ctypes
import logging

logger = logging.getLogger(__name__)


def trim_malloc_heap() -> bool:
    """Return True when glibc returned free heap pages to the OS.

    NEVER raises — any failure (no libc handle, missing ``malloc_trim``
    symbol on non-glibc platforms, call error) is logged at DEBUG and
    collapses to ``False``. Safe to call from any loop at any cadence.
    """
    try:
        libc = ctypes.CDLL(None)
        trim = libc.malloc_trim
        trim.argtypes = [ctypes.c_size_t]
        trim.restype = ctypes.c_int
    except Exception as exc:  # noqa: BLE001 — the contract is never-raise
        logger.debug(f"malloc_trim unavailable on this platform: {exc}")
        return False
    try:
        return bool(trim(0))
    except Exception as exc:  # noqa: BLE001 — the contract is never-raise
        logger.debug(f"malloc_trim call failed: {exc}")
        return False
