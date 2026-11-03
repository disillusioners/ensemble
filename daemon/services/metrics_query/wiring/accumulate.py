"""The metrics-layer accumulation — the F12 disambiguator.

Several producers expose a **per-tick** value where the registry wants a
**cumulative** counter.  The producers stay untouched: the metrics layer
accumulates (decisions.md D5's F12 row — "the CUMULATIVE accumulation
belongs in the metrics pull layer").

THE RULE
--------
The layer keeps the LAST value it read per key and DIFFS successive
reads:

* ``value >= last`` ⇒ the producer is cumulative ⇒ the delta is
  ``value - last``;
* ``value < last`` ⇒ the producer RESET (it returned a per-tick value)
  ⇒ the delta is the raw ``value``.

This is the disambiguator the plan names ("the metrics layer's own
accumulation — it diffs successive ``counters()`` reads"), and it is
what makes the DEFERRED C17 docstring fix cost no correctness: the
``orphan_released`` counter that the docstring mis-describes is handled
by the same rule as every other key.

THE FIRST READ IS A BASELINE
-----------------------------
The first read of a key establishes the baseline and contributes a
ZERO delta — a cumulative producer's pre-boot history is not ours to
claim, and a per-tick producer's first tick is indistinguishable from a
cumulative total.  Both are honest readings of "we did not observe it".
"""

from __future__ import annotations

import threading
from typing import Hashable


class DeltaAccumulator:
    """The per-key delta of successive producer reads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: dict[Hashable, float] = {}

    def observe(self, key: Hashable, value: float) -> float:
        """Record a read and return its DELTA (0.0 on the first read)."""
        try:
            current = float(value)
        except (TypeError, ValueError):
            return 0.0
        with self._lock:
            last = self._last.get(key)
            self._last[key] = current
        if last is None:
            return 0.0
        if current >= last:
            return current - last
        # The producer reset — it returned a per-tick value.
        return current

    def baseline(self, key: Hashable) -> float | None:
        """The last observed raw value for a key (``None`` when unseen)."""
        with self._lock:
            return self._last.get(key)

    def reset(self) -> None:
        """Forget every baseline (test-only)."""
        with self._lock:
            self._last.clear()


__all__ = ["DeltaAccumulator"]
