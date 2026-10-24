"""The ONE canonical bind-parameter chunking helper.

WHY THIS EXISTS
---------------
Both supported engines cap the number of bind parameters in a single
statement — SQLite's ``SQLITE_MAX_VARIABLE_NUMBER`` is 999 (the
compiled default) and PostgreSQL's protocol limit is 65535. A rendered
``IN (...)`` carrying more parameters than the engine accepts fails at
EXECUTE time, so every bulk id lookup splits its id list into chunks
before rendering.

That split was implemented THREE times, byte-identical apart from the
type annotations:

* the instance repository's metadata lookup and its evidence probe;
* the task repository's answer-handle lookup;
* the read tool's husk JobItem probe.

Three copies drift — and these had already drifted in their CHUNK
SIZES, which is the one number that actually matters at the engine.
This module is the canonical home: ONE implementation, and the sizes
stay at their call sites (each is a named constant beside its own
query).

A LEAF BY CONSTRUCTION
-----------------------
This module imports nothing but the standard library, so both the
repository layer and the tool layer can use it without an import
cycle. (It deliberately does NOT live in ``daemon/utils.py``: that
module imports FastAPI, and pulling the web framework into the
repository layer is the anti-pattern the house flags as the M7 debt.)
"""

from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


def chunked(items: list[T], size: int) -> list[list[T]]:
    """Split ``items`` into consecutive chunks of at most ``size``.

    Args:
        items: the list to split.
        size: the maximum chunk length.

    Returns:
        The chunks, in order. A ``size`` of zero or less yields the
        whole list as ONE chunk — the pre-fix behaviour of all three
        former copies, preserved exactly.
    """
    if size <= 0:
        return [list(items)]
    return [items[i : i + size] for i in range(0, len(items), size)]
