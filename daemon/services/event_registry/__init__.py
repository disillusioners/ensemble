"""Event registry v1 — kind catalog + structured emission + observability.

Phase 5 of the Engine Arc Phase-1 plan. REGISTRATION + OBSERVABILITY ONLY
(Emperor ruling #3: "event register is need. Why: it help on central
loging, debug management, metric") — kinds are declared, emission sites
are BRIDGED (one ``record()`` adjacent to the existing emitter), and a
per-kind metric-shaped projection exists. NOTHING consumes a kind to
trigger work (the engine's Event trigger is specified in P1; Phase 1
registers no such job).

**F-1 RATIONALE (module-level, per the plan's §6 constraint).** The
metrics family census is CLOSED — ``tests/unit/metrics/
test_metrics_family_adjudication.py`` pins 28 verdicts / 18 registered
families, exact key sets, series bands and the wiring-root allowlist.
This package registers **ZERO** families in
``daemon.services.metrics_query.wiring.registry`` — publishing
``ensemble_event_registry_*`` is P6's T6.3 census ceremony (verdict row +
``REGISTERED_FAMILIES`` member + band + allowlist record), never a side
effect. The P5 unit pack asserts ``registered_families()`` is unchanged
(a HARD leg, not a comment).

Submodules:
- :mod:`catalog` — the frozen ``KindSpec`` table (T5.1; import-time data).
- :mod:`emit` — ``record()`` / ``flush()`` / ``set_event_sink()`` (T5.2/T5.8).
- :mod:`metrics` — ``snapshot()`` + consumer-liveness verification (T5.7).

No repository module, no migration (D5.2/D5.3) — the registry's durable
substrate IS the existing ``event`` table + the state tables it observes.
"""

from .catalog import (
    BRIDGE_PENDING,
    CATALOG,
    DEFERRED_V2_KINDS,
    KIND_INDEX,
    SEAM_DEPTH_TRIGGERS,
    ZERO_PRODUCER_KINDS,
    EventClass,
    FailurePosture,
    KindSpec,
    assert_catalog,
    event_bus_kind_id,
    is_event_bus_kind,
    validate_catalog,
)
from .emit import (
    RING_CAP,
    counters,
    flush,
    recent,
    record,
    reset_for_tests,
    set_event_sink,
)
from .metrics import (
    reset_warning_state_for_tests,
    snapshot,
)

__all__ = [
    "BRIDGE_PENDING",
    "CATALOG",
    "DEFERRED_V2_KINDS",
    "KIND_INDEX",
    "RING_CAP",
    "SEAM_DEPTH_TRIGGERS",
    "ZERO_PRODUCER_KINDS",
    "EventClass",
    "FailurePosture",
    "KindSpec",
    "assert_catalog",
    "counters",
    "event_bus_kind_id",
    "flush",
    "is_event_bus_kind",
    "recent",
    "record",
    "reset_for_tests",
    "reset_warning_state_for_tests",
    "set_event_sink",
    "snapshot",
    "validate_catalog",
]
