"""``ram_guard_events`` — the bus-backed emitter's home (OUTSIDE the package).

Contract: architecture-recommendation.md §4.5 (the durable event — the
owner, the emitter and the bridge), §7 (the module decomposition:
this module lives OUTSIDE ``daemon/services/ram_guard/``), §15 B7 (the
emitter interface), §15 B15 (the phase ownership), phase2-plan.md T1.

**PHASE 2 CREATES THIS FILE AS THE IMPLEMENTATION HOME ONLY.**  The
emitter implementation and the loop bridge are PHASE 5's work
(§15 B15: "Phase 5 owns the event EMITTER, the loop bridge, the
lifespan wiring AND the lifespan construction (app.state.ram_guard)"),
so this module intentionally carries NO implementation yet — Phase 2's
deliverable is the HOOK (the call site in ``ram_guard/guard.py``) plus
the payload producer (``ram_guard/errors.py``).

Why the split exists at all: the ``ram_guard`` package is stdlib-only
(arch §7 — "``ram_guard`` imports NOTHING from ``daemon/``").  The
package therefore never imports the event bus, the manager's loop
accessor or ``MainLoopBridge``; it receives an INJECTED callable
(``RamBreachEmitter``) and calls it.  Keeping the implementation here
— outside the package — is what makes that import freeze structural
rather than a convention.

The contract Phase 5 implements against:

* **The interface** (§15 B7, pinned char-for-character)::

      RamBreachEmitter = Callable[[str, Mapping[str, Any]], bool]

  The first argument is the ``instance_id``; the second is the event
  payload — EXACTLY the §15 B3 key set, in order, produced by
  ``ram_guard.errors.build_ram_event_payload``.  ``True`` means the
  event was scheduled for delivery; ``False`` means it was dropped.

* **The call site** is the ``WATCHING → KILLED`` transition on the
  EXECUTION-LANE thread, AFTER the ladder returns and OUTSIDE the
  registry lock.  It is called EXACTLY ONCE per breach and wrapped in
  ``try/except Exception`` so it can never raise into the lane; a
  raising emitter increments ``ram_events_dropped``.  ``instance_id``
  ``None`` ⇒ no call at all (defensive — arch §4.5).

* **The bridge** is NON-BLOCKING and uses ``manager._get_event_loop()``
  (the hardened accessor at ``manager.py:3097-3195``) — NEVER
  ``MainLoopBridge`` (its loop is ``None`` on a no-worker-pool boot —
  E9).  A missing/closed loop or a daemon shutdown DROPS the event; it
  is never raised and never retried (test-plan N9).

* **The event kind** is ``EventKind.RAM_LIMIT_EXCEEDED`` =
  ``"ram_limit_exceeded"`` — the SAME string as the envelope's
  ``code`` (§17.1).  The event is fired only for a KILL: refusals emit
  NO event (they are operational, not forensic — §17.2).
"""

from __future__ import annotations

__all__: list[str] = []
