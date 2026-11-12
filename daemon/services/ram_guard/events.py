"""``events`` — the event emitter's TYPE ALIAS ONLY.

Contract: architecture-recommendation.md §4.5 (the durable event —
owner, emitter, bridge), §15 B7 (the emitter interface), §7 (the
module decomposition), phase2-plan.md T1 / T23.

**This module carries the type alias and NOTHING ELSE** — no emitter
implementation, no bus import, no loop bridge.  The import freeze
(arch §7) requires ``ram_guard`` to import NOTHING from ``daemon/``:
no ``tools/*``, no ``resource_monitor``, no ``repositories``, no
``event_bus``, no ``main_loop_bridge``, no ``bash_spawn_reaper``.  The
event dependency is satisfied by an INJECTED callable
(``RamBreachEmitter``), whose implementation lives OUTSIDE the package
(``daemon/services/ram_guard_events.py``) and is wired at the
composition root (the FastAPI lifespan — Phase 5).

**Phase ownership (§15 B15):** Phase 2 owns the event HOOK (the call
site in ``guard.py``); Phase 5 owns the event EMITTER, the loop bridge,
the lifespan wiring AND the lifespan construction.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

#: The emitter interface (§15 B7, pinned char-for-character).
#:
#: ``True``  — the event was SCHEDULED for delivery.
#: ``False`` — the event was DROPPED.
#:
#: The first argument is the ``instance_id``; the second is the event
#: payload built by ``errors.build_ram_event_payload`` (the §15 B3 key
#: set, in order).
RamBreachEmitter = Callable[[str, Mapping[str, Any]], bool]

__all__ = ["RamBreachEmitter"]
