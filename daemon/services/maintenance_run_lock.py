"""Single-flight in-process lock for Section 1 Maintenance runs — AM-4.

The lock is step 1 of a per-run sequence (T3 / ``phase1-backend.md`` §5.1);
the DB-side partial-unique-index claim (T4 / AM-5) is the real gate, the
in-process lock is belt-and-braces serialization that gives the auto cycle
its fail-fast skip semantics.

Sequence per run (auto or manual):

    acquire asyncio.Lock → conditional INSERT (partial unique index)
    → run → finalize-in-finally

The lock itself never blocks the caller: ``acquire`` returns ``False``
immediately when held (no ``await``-queueing). The MaintenanceService
auto-cycle loop is strictly sequential (one tick per
``check_interval_minutes``), so the in-process lock is mostly a
serialization guarantee for the auto + manual coexistence on a single
event loop — the multi-daemon case is killed by the partial unique
index on the ``maintenance_runs`` table.

Conflict semantics (AM-5): an INSERT conflict on
``uq_maintenance_runs_running_section`` is the refused-call signal —
the lock alone is insufficient (it serializes in-process callers only).
Every refused acquire produces ONE INFO log line with requester
forensics (AM-6: no refusal rows persisted). The refusal path itself
is owned by the service layer (T5), not the lock.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MaintenanceRunContext:
    """One in-flight run's identity — the lock holder's view.

    Mirrors the ``maintenance_runs`` row that's about to be inserted
    (the row carries the same fields plus the audit trail). The
    ``in_flight`` property exposes the holder's context to callers
    that need to surface ``run_id``/``kind``/``started_at`` on a 409.
    """

    run_id: str
    kind: str                  # 'auto' | 'manual_dry_run' | 'manual_execute'
    started_at: str            # now_utc_iso() — TEXT ISO (INV-7/AM-15)
    triggered_by: str          # 'system' | 'user'


class MaintenanceRunLock:
    """In-process single-flight gate for Section 1 runs.

    Wraps an ``asyncio.Lock`` plus a holder-context slot. ``acquire``
    is FAIL-FAST: when the lock is held, it returns ``False``
    immediately, never ``await``-queues. The race window between
    ``locked()`` and ``await acquire()`` is empty on a single event
    loop (no intervening ``await`` point), so the check-and-acquire
    pair is atomic on one loop.

    The lock is intentionally NOT a context manager: callers own the
    ``release`` to keep the failure semantics explicit (the
    MaintenanceApiService holds the lock across an ``asyncio.create_task``
    that runs the destructive execute in the background — the task's
    own ``finally`` releases; the service's caller does NOT release
    prematurely on the 202 response).
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._current: Optional[MaintenanceRunContext] = None

    @property
    def in_flight(self) -> MaintenanceRunContext | None:
        """The current holder's context, or ``None`` when free.

        Used by callers to populate 409 / skip-log forensics with the
        in-flight ``run_id`` / ``kind`` / ``started_at``.
        """
        return self._current

    def locked(self) -> bool:
        """True when a holder is registered (lock is held).

        Cheap synchronous check; pairs with ``await self.acquire(...)``
        which is a no-op when ``locked()`` is True (no await-queueing).
        """
        return self._current is not None

    async def acquire(self, ctx: MaintenanceRunContext) -> bool:
        """Try to acquire the lock for ``ctx``. Returns True on success.

        Returns ``False`` immediately when the lock is held (no
        ``await``-queueing — the auto cycle must not stall behind a
        multi-minute manual run; the manual caller gets a 409 + run
        adoption instead).

        Atomicity: the ``self._lock.locked()`` pre-check and
        ``await self._lock.acquire()`` are executed with NO suspension
        point between them (``asyncio.Lock.acquire``'s uncontended
        fast path returns without awaiting anything), so on a single
        event loop no other task can interleave — the check-and-acquire
        pair is race-free, exactly as the plan's T3 argument states.
        """
        if self._current is not None or self._lock.locked():
            return False
        await self._lock.acquire()  # uncontended fast path — no queueing
        self._current = ctx
        return True

    def release(self) -> None:
        """Release the lock. Idempotent when already free.

        Called from the caller's ``finally`` block — auto cycle in
        ``CheckpointCleanupJob.execute`` and the manual execute
        task in ``MaintenanceApiService._execute_run``. Never raises.
        """
        self._current = None
        if self._lock.locked():
            self._lock.release()


__all__ = ["MaintenanceRunLock", "MaintenanceRunContext"]