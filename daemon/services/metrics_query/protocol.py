"""The provider protocol — the ONE read substrate of the query door.

The door is swappable: the HTTP routes and the (deferred) agent-tool
door call the SAME provider functions.  The protocol is therefore
**HTTP-agnostic** — no ``Request``, no FastAPI type, no web-framework
import appears in any signature (plan task 2.2).  A duck-typed fake
provider satisfies it, which is what makes the routes testable without
a registry.

The three methods are the contract's three views:

* ``families()`` — the family catalog (the frozen shape);
* ``snapshot()`` — the current published values;
* ``history()`` — the windowed trend points.
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence, runtime_checkable


@runtime_checkable
class MetricsQueryProvider(Protocol):
    """The read substrate the query door serves from.

    Implementations read PUBLISHED registry state only — never a metric
    lock, never the exposition renderer, never a database.
    """

    def families(self) -> list[dict[str, Any]]:
        """The family catalog, one entry per registered family."""
        ...

    def snapshot(
        self,
        *,
        family: str | None = None,
        name_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        """The current published values of the matching series."""
        ...

    def history(
        self,
        *,
        names: Sequence[str] | None = None,
        series: Sequence[str] | None = None,
        window_s: int | None = None,
        max_points: int | None = None,
    ) -> list[dict[str, Any]]:
        """The windowed trend points of the matching series."""
        ...


__all__ = ["MetricsQueryProvider"]
