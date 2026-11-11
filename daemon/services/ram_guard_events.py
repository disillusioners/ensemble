"""``ram_guard_events`` — the bus-backed emitter's home (OUTSIDE the package).

Contract: architecture-recommendation.md §4.5 (the durable event — the
owner, the emitter and the bridge), §7 (the module decomposition:
this module lives OUTSIDE ``daemon/services/ram_guard/``), §15 B7 (the
emitter interface), §15 B15 (the phase ownership), phase5-plan.md T7.

**Phase ownership (§15 B15):** Phase 2 created this file as the
implementation home and owns the HOOK (the call site in
``ram_guard/guard.py``) plus the payload producer
(``ram_guard/errors.build_ram_event_payload``); **Phase 5 owns this
module** — the event EMITTER, the non-blocking loop bridge and the
lifespan wiring.

Why the split exists at all: the ``ram_guard`` package is stdlib-only
(arch §7 — "``ram_guard`` imports NOTHING from ``daemon/``").  The
package therefore never imports the event bus, the manager's loop
accessor or ``MainLoopBridge``; it receives an INJECTED callable
(``RamBreachEmitter``) and calls it.  Keeping the implementation here
— outside the package — is what makes that import freeze structural
rather than a convention.

The contract this module implements (§15 B7, pinned char-for-character):

    RamBreachEmitter = Callable[[str, Mapping[str, Any]], bool]

The first argument is the ``instance_id``; the second is the event
payload — EXACTLY the §15 B3 key set, in order, produced by
``ram_guard.errors.build_ram_event_payload``.  ``True`` means the event
was scheduled for delivery; ``False`` means it was DROPPED.

**The bridge (§4.5 / phase5-plan T7) — NON-BLOCKING, never silent:**

* ``loop = manager._get_event_loop()`` — the HARDENED accessor
  (``manager.py:3097-3195``): the manager's stored loop when live, else
  the thread's live loop, else it RAISES ``RuntimeError`` — it NEVER
  creates one.
* ``coro = event_bus.create_event(instance_id=..., kind=EventKind.
  RAM_LIMIT_EXCEEDED, data=dict(payload))`` then
  ``fut = asyncio.run_coroutine_threadsafe(coro, loop)`` — scheduling
  is NON-BLOCKING; the emitter NEVER waits on the future (never
  ``.result(timeout=…)`` — the blocking precedent blocks a tick 8 s,
  40× the 200 ms poll interval, the 🔴 "the guard's own death is
  silent and fatal" failure mode).
* ``fut.add_done_callback(_log_ram_event_future_exception)`` — the
  callback LOGS a failed delivery; it never re-raises into the tick or
  the lane.
* **NOT ``MainLoopBridge``** — its loop is ``None`` on a
  no-worker-pool boot (``PoolOrchestrator.setup()`` returns early when
  ``USE_WORKER_POOL`` is false ⇒ every RAM event would be silently
  dropped forever).
* **Drop semantics are VISIBLE, never silent:** no live loop / loop
  closed / bus absent / daemon shutdown ⇒ the emitter returns
  ``False`` ⇒ the guard's hook counts ``ram_events_dropped++``
  (surfaced on ``GET /api/resources/ram-guard``) beside the
  ``WATCHING→KILLED`` transition log line (which the hook writes on
  EVERY kill, dropped or not). A drop is never raised, never retried.

The event kind is ``EventKind.RAM_LIMIT_EXCEEDED`` =
``"ram_limit_exceeded"`` — the SAME string as the envelope's ``code``
(§17.1).  The event is fired only for a KILL: refusals emit NO event
(§17.2).  ``instance_id=None`` ⇒ no call at all (the hook's defensive
guard — arch §4.5).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Mapping

from daemon.repositories.event.models import EventKind
from daemon.services.ram_guard.events import RamBreachEmitter

logger = logging.getLogger(__name__)

__all__: list[str] = [
    "make_ram_breach_emitter",
]


def _log_ram_event_future_exception(fut: "asyncio.Future") -> None:
    """The done-callback: LOG a failed delivery; NEVER re-raise.

    Runs on the loop thread once the bus coroutine finishes.  A lost
    delivery here (loop closed mid-flight, bus failure) is logged
    loudly — the loss stays VISIBLE even though it can no longer be
    counted deterministically by the guard hook (which has already
    returned by the time the coroutine runs).
    """
    if fut.cancelled():
        return
    exc = fut.exception()
    if exc is not None:
        logger.warning(
            "ram-guard: RAM_LIMIT_EXCEEDED event delivery FAILED "
            "(the event was dropped): %s: %s",
            type(exc).__name__,
            exc,
        )


def make_ram_breach_emitter(
    *,
    manager: Any,
    event_bus: Any,
) -> RamBreachEmitter:
    """Build the bus-backed breach emitter (the composition root's seam).

    ``manager`` supplies the canonical loop via the hardened accessor
    ``manager._get_event_loop()`` (NEVER ``MainLoopBridge`` — its loop
    is ``None`` on a no-worker-pool boot); ``event_bus`` supplies the
    ``create_event`` coroutine (the manager's own ``EventBus`` at
    ``manager._event_bus``).  A ``None`` bus degrades to PERMANENT
    DROP-AT-EMIT (``False``) — visible on the endpoint's
    ``ram_events_dropped`` counter — never a raise into the lane.

    The returned callable is TOTAL: it returns ``True`` only when the
    bus coroutine was successfully SCHEDULED on a live loop; every
    failure mode returns ``False`` (the drop) and never raises.
    """

    def _emit(instance_id: str, payload: Mapping[str, Any]) -> bool:
        if not instance_id:
            return False  # defensive parity with the hook (arch §4.5)
        if event_bus is None:
            return False  # no bus ⇒ drop (visible via the counter)
        try:
            loop = manager._get_event_loop()
        except Exception as loop_exc:
            # No live loop / loop closed / shutdown — DROPPED, never
            # raised, never retried (test-plan N9). The hook counts it.
            logger.warning(
                "ram-guard: no live event loop — RAM_LIMIT_EXCEEDED "
                "event DROPPED (%s: %s)",
                type(loop_exc).__name__,
                loop_exc,
            )
            return False
        try:
            coro = event_bus.create_event(
                instance_id=instance_id,
                kind=EventKind.RAM_LIMIT_EXCEEDED,
                data=dict(payload),
            )
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
        except Exception as sched_exc:
            logger.warning(
                "ram-guard: RAM_LIMIT_EXCEEDED event scheduling FAILED "
                "— the event was DROPPED (%s: %s)",
                type(sched_exc).__name__,
                sched_exc,
            )
            return False
        # NON-BLOCKING: schedule-and-forget. The callback logs a failed
        # delivery; nothing ever blocks the execution-lane thread here.
        fut.add_done_callback(_log_ram_event_future_exception)
        return True

    return _emit
