"""``ram_guard`` — the RAM-based auto-termination guard (measurement substrate).

Contract: architecture-recommendation.md §4 (the frozen contracts),
§7 (the module decomposition + the import freeze), §15 B7 (the emitter
interface), §15 B15 (the phase ownership), phase2-plan.md T1.

**The import freeze (§7 / §15 B7, verbatim):**

    Import direction: `tools/* → ram_guard` only; `ram_guard → stdlib` only. `ram_guard` imports NOTHING from `daemon/` — no `tools/*`, no `resource_monitor`, no `repositories`, no `event_bus`, no `main_loop_bridge`, no `bash_spawn_reaper`. The event dependency is satisfied by an INJECTED callable (`RamBreachEmitter`), whose implementation lives OUTSIDE the package (`daemon/services/ram_guard_events.py`) and is wired at the composition root (the FastAPI lifespan).

**The module decomposition (§7, frozen):**

    daemon/services/ram_guard/
      __init__.py     facade: get_ram_guard(), register/read_verdict/release
      tree_reader.py  scan_proc / resolve_members / read_tree_rss / read_tree_pss / exceeds  (pure, injectable)
      ceiling.py      detect_effective_total_bytes + the 70% resolution (pure, injectable roots)
      verdict.py      SpawnRecord, PeakRecord, breach predicate, OVERCOUNTED re-arm
      ladder.py       ram_kill_ladder + P0–P7 + the P8 post-kill probe + the grace slice constant
      guard.py        the TWO threads (tick + execution lane) + registry + pending set + GuardCounters + liveness sweep + the emitter call site
      errors.py       format_ram_envelope (one producer, two surfaces) + build_ram_event_payload (the allow-list)
      events.py       RamBreachEmitter type alias ONLY (stdlib typing; no emitter implementation here)
    daemon/services/ram_guard_events.py   OUTSIDE the package: the bus-backed emitter impl + the loop bridge

**Phase ownership (§15 B15, verbatim):**

    Phase 2 owns the event HOOK, the DQ6 consumer-gate artifact (pins-only), the execution-lane thread, and the ceiling detector (ceiling.py).
    Phase 5 owns the event EMITTER, the loop bridge, the lifespan wiring AND the lifespan construction (app.state.ram_guard), and the root-Config wiring of RamGuardConfig.
    Phase 7 owns the operator documentation and the honesty set (fifteen items).
"""

from __future__ import annotations

import threading
from typing import Optional

from .events import RamBreachEmitter
from .guard import GuardCounters, RamGuard
from .verdict import SpawnRecord

# ── The composition-root slot ──────────────────────────────────────────────

#: The current guard.  ``None`` until the composition root (the FastAPI
#: lifespan — PHASE 5) installs one; the facade calls are then
#: complete no-ops, which is the OFF posture (arch §4.6 item 6).
_guard: Optional[RamGuard] = None
_guard_lock = threading.Lock()


def get_ram_guard() -> Optional[RamGuard]:
    """The installed guard (or ``None`` when the guard is OFF/unwired)."""
    return _guard


def set_ram_guard(guard: Optional[RamGuard]) -> None:
    """Install (or clear) the guard — the composition root's call.

    Phase 5's lifespan owns this; the facade exists so the tool lanes
    never import ``guard.py`` directly.
    """
    global _guard
    with _guard_lock:
        _guard = guard


def register(**kwargs) -> Optional[SpawnRecord]:
    """Register a watched spawn (no-op when no guard is installed)."""
    guard = get_ram_guard()
    if guard is None:
        return None
    return guard.register(**kwargs)


def read_verdict(tracking_id: str) -> Optional[SpawnRecord]:
    """The verdict record for ``tracking_id`` (``None`` when unclaimed/unwired)."""
    guard = get_ram_guard()
    if guard is None:
        return None
    return guard.read_verdict(tracking_id)


def release(tracking_id: str, *, force: bool = False) -> bool:
    """Release a watched spawn (no-op when no guard is installed)."""
    guard = get_ram_guard()
    if guard is None:
        return False
    return guard.release(tracking_id, force=force)


__all__ = [
    "GuardCounters",
    "RamBreachEmitter",
    "RamGuard",
    "SpawnRecord",
    "get_ram_guard",
    "read_verdict",
    "register",
    "release",
    "set_ram_guard",
]
