"""The P4 WIRING MODULE — the pull adapters over the 16 adjudicated families.

The Phase-2 sampler's stage A calls these adapters once per tick.  Each
adapter READS an existing producer's already-computed value and writes
it into the registry (AD3, strict): no producer module grows a new
observe/inc call at a module site, with ONE exception — the F7 bytes
call site (``daemon/persistence.py``), where an EXISTING observe gains a
REAL value.

THE THREE CLUSTERS
-------------------
* **W1** — F0.3 · F1 · F3 · F4 · F5 · F6
* **W2** — F7 · F8 · F9 · F10 · F11 · F12 · F13
* **W3** — F17 · F18 · F21

F2 is NOT here (the value ruling, AM-25 / LR-2) and neither are the F21
phase families (AD4(b) / D-4).

THE MODULE'S IMPORT IS THE REGISTRATION
----------------------------------------
Importing this package registers the phase's families in the metric
registry — the door discovers them from the registration-time metadata
(AM-3), so no door edit is needed when a family is added.  The sampler
imports it LAZILY (inside its constructor) so the import graph stays
acyclic.
"""

from __future__ import annotations

from daemon.services.metrics_query.wiring.accumulate import DeltaAccumulator
from daemon.services.metrics_query.wiring.registry import (
    FAMILY_SPECS,
    FamilySpec,
    PullAdapter,
    family_metric_names,
    pull_adapters,
    register_family_metric,
    register_pull_adapter,
    registered_families,
    registered_family_metrics,
)
from daemon.services.metrics_query.wiring.sources import (
    WiringSources,
    bind_sources,
    current_sources,
    unbind_sources,
)
from daemon.services.metrics_query.wiring import vocab  # noqa: F401  (the vocab surface)
from daemon.services.metrics_query.wiring.w1 import register_w1_adapters
from daemon.services.metrics_query.wiring.w2 import register_w2_adapters
from daemon.services.metrics_query.wiring.w3 import register_w3_adapters

register_w1_adapters()
register_w2_adapters()
register_w3_adapters()


def default_adapters() -> tuple[PullAdapter, ...]:
    """The pull adapters the sampler's stage A runs each tick."""
    return pull_adapters()


__all__ = [
    "DeltaAccumulator",
    "FAMILY_SPECS",
    "FamilySpec",
    "PullAdapter",
    "WiringSources",
    "bind_sources",
    "current_sources",
    "default_adapters",
    "family_metric_names",
    "pull_adapters",
    "register_family_metric",
    "register_pull_adapter",
    "registered_families",
    "registered_family_metrics",
    "unbind_sources",
]
