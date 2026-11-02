"""The availability model — WHY a family is dark (plan task 2.4).

Each availability reason is reachable by EXACTLY ONE runtime fact, and a
dark family never reports ``0`` as if it were a value: the honest
signal is the reason, not a fabricated number.

The CLOSED reason set (plan §frozen contract):

``census_gated``
    the census sampler's DB work is gated (the switch is OFF).  Sourced
    from the PUBLIC ``CensusSampler.gated`` property — a read-through of
    the EXISTING private ``_gate()``.  **The query package NEVER reads
    the switch itself**: doing so would add a third behavioural check
    site and break the "the switch is consulted at exactly two sites"
    invariant.
``census_not_running``
    the census sampler is not running (a non-postgres runtime, or the
    thread never started).
``capture_off``
    the wedge capture is not active (the F1/F3 families).
``series_absent``
    the family is registered but has published no observation yet.
``listener_disabled``
    a surface note for the exposition listener only — the query door
    never emits it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The CLOSED availability reason set.
AVAILABILITY_REASONS: tuple[str, ...] = (
    "census_gated",
    "census_not_running",
    "capture_off",
    "series_absent",
    "listener_disabled",
)

#: The source idiom of a family that the census sampler feeds.
SOURCE_IDIOM_POSTGRES_CENSUS = "postgres_census"

#: The source idiom of a family that the wedge capture feeds.
SOURCE_IDIOM_WEDGE_CAPTURE = "wedge_capture"


@dataclass(frozen=True)
class AvailabilityFacts:
    """The runtime facts the availability model reads.

    The facts are INJECTED — the query package never goes looking for
    them itself (no switch read, no DB probe, no thread inspection).
    """

    #: Whether the census sampler's DB work is currently gated.
    census_gated: bool = False
    #: Whether the census sampler thread is running.
    census_running: bool = False
    #: Whether the wedge capture is active.
    capture_active: bool = False


def derive_availability(
    *,
    source_idiom: str,
    live_series_count: int,
    facts: AvailabilityFacts,
) -> dict[str, Any]:
    """Derive the family's ``availability`` block.

    Returns ``{"state": "live"}`` for a live family and
    ``{"state": "dark", "reason": <closed reason>}`` for a dark one.
    """
    if source_idiom == SOURCE_IDIOM_POSTGRES_CENSUS:
        if facts.census_gated:
            return {"state": "dark", "reason": "census_gated"}
        if not facts.census_running:
            return {"state": "dark", "reason": "census_not_running"}
    elif source_idiom == SOURCE_IDIOM_WEDGE_CAPTURE:
        if not facts.capture_active:
            return {"state": "dark", "reason": "capture_off"}
    if live_series_count <= 0:
        return {"state": "dark", "reason": "series_absent"}
    return {"state": "live"}


__all__ = [
    "AVAILABILITY_REASONS",
    "AvailabilityFacts",
    "SOURCE_IDIOM_POSTGRES_CENSUS",
    "SOURCE_IDIOM_WEDGE_CAPTURE",
    "derive_availability",
]
