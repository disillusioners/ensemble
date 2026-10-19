"""``ring`` — bounded per-tier deques for the resource monitor.

Architecture §2.1 (binding): the primary store is the ring; the DB
is durability. Bounded memory + drop-oldest semantics protect the
sampler thread from ever allocating unbounded state.

Three tiers, each ``deque(maxlen=...)``:

  * ``raw``        — every tick, sized for the raw-retention window
                     (``retention_raw_minutes`` / 60 / 24 ticks
                     worth, floored to 24 h).
  * ``minute``     — minute-rolled rows.
  * ``hour``       — hour-rolled rows.

The ring is append-only for the sampler thread. The flusher thread
(T6) drains it on a separate session. The /history endpoint reads
straight from these tiers without touching the DB (the DB-backed
read path lives at T6 + the /api/resources/history router handler).

P1 boundary: rings are populated by the sampler; roll-down writes
go to the DB tier tables on the flusher window. Reads of the ring
itself serve the /live and /history endpoints directly.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Generic, TypeVar

# Element type — the resource monitor uses dict rows internally;
# the ring is data-shape agnostic so the sampler and the flusher
# agree on a dict shape.
T = TypeVar("T")


@dataclass
class TierRing(Generic[T]):
    """Bounded ring per tier (raw / minute / hour).

    ``maxlen`` is the row budget. Once full, the oldest row is
    dropped on append (the ``deque(maxlen=...)`` contract).

    Concurrency: the sampler thread is the sole writer; the flusher
    is the sole reader (it pops to its own bounded drop-oldest
    buffer). The /history / /self-cost endpoints read snapshots via
    ``snapshot()`` — never under lock contention with the sampler
    because :class:`collections.deque` operations are atomic in
    CPython.
    """

    tier: str
    maxlen: int
    _buf: Deque[T] = field(init=False)

    def __post_init__(self) -> None:
        self._buf = deque(maxlen=self.maxlen)

    def __len__(self) -> int:
        return len(self._buf)

    def append(self, row: T) -> None:
        """Append a row. Oldest row is dropped when full."""
        self._buf.append(row)

    def extend(self, rows: list[T]) -> None:
        """Extend with multiple rows. Order is preserved; drop-oldest
        happens after the bulk append.
        """
        self._buf.extend(rows)

    def drain(self) -> list[T]:
        """Drain ALL rows into a list and reset the buffer.

        Used by the flusher thread (T6) at flush time. The flusher
        then writes the rows to its own bounded drop-oldest buffer.
        """
        rows = list(self._buf)
        self._buf.clear()
        return rows

    def snapshot(self) -> list[T]:
        """Read-only snapshot of the current contents.

        Cheap — copies the deque's contents into a list. The
        /history / /self-cost endpoints consume this.
        """
        return list(self._buf)

    def capacity(self) -> int:
        """The configured maxlen."""
        return self.maxlen


def default_ring_sizes(
    *,
    sample_seconds: int,
    retention_raw_minutes: int,
    retention_minute_hours: int,
    retention_hour_days: int,
) -> tuple[int, int, int]:
    """Compute (raw, minute, hour) tier ring sizes from retention windows.

    Raw tier holds every tick across the raw-retention window —
    minute tier holds every minute roll-up across its window —
    hour tier holds every hour roll-up across its window. The
    numeric budget is small enough to keep RSS bounded: at
    default sample_seconds=30 and 48 h raw retention ⇒ ~5760 ticks
    in the raw ring.

    Args:
        sample_seconds: tick interval (15-60).
        retention_raw_minutes: raw window in minutes.
        retention_minute_hours: minute window in hours.
        retention_hour_days: hour window in days.

    Returns:
        (raw_maxlen, minute_maxlen, hour_maxlen).
    """
    ticks_per_minute = max(1, 60 // sample_seconds)
    raw_maxlen = max(2, ticks_per_minute * retention_raw_minutes)
    minute_maxlen = max(2, 60 * retention_minute_hours)
    hour_maxlen = max(2, 24 * retention_hour_days)
    return raw_maxlen, minute_maxlen, hour_maxlen


__all__ = [
    "TierRing",
    "default_ring_sizes",
]