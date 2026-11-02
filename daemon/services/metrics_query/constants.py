"""Frozen constants for the metrics query substrate (central-metrics-v1 P2).

Every number here is a MODULE CONSTANT — the plan's sizing table
(``decisions.md`` D3) fixes them all, and none of them is a managed
key.  The ONE reused managed key (``limits.metrics_sampler_interval_s``)
is the sampler's TICK and is read once at construction, not from here.

The sizing rationale (AM-9/AM-10):

* the ring is ARRAY-backed (``array('d')`` values + ``array('q')``
  timestamps) at **16 B/point** — the ``deque(maxlen)`` form was
  rejected on measured evidence (129-161 B/point ⇒ 92.6-115.0 MiB at
  750 k points, 3.6x over the 32 MiB ceiling);
* the depth is ``clamp(POINT_BUDGET // ring_series, MIN_DEPTH,
  window_s // effective_interval_s)`` where ``ring_series`` is the
  REGISTRY-NATIVE label-combination count (not the catalog count);
* eviction is by COUNT **and** by AGE, so the window stays truthful
  under any cadence change.
"""

from __future__ import annotations

#: The envelope-shape version (a separate axis from the label vocabulary).
#: A BREAKING envelope change bumps it; an additive change does not.
QUERY_SCHEMA_VERSION: int = 1

#: The in-RAM trend buffer's total point budget (750 000 points ⇒
#: 11.4 MiB at 16 B/point — 2.8x under the 32 MiB ceiling).
POINT_BUDGET: int = 750_000

#: The trend window target (2 h).  Deliberately NOT long history — the
#: Prometheus TSDB owns that.
WINDOW_SECONDS: int = 7200

#: The floor of the per-series ring depth.  A ring shall never be
#: shallower than this, whatever the divisor says.
MIN_DEPTH: int = 60

#: The default ``max_points`` of the ``/series`` route (approver B1 +
#: leader ruling): 60 min x 4/min at the 15 s effective spacing = 240
#: points, so the default IS the acceptance floor.
DEFAULT_MAX_POINTS: int = 240

#: The hard ceiling of the ``?names=`` selector (the residual on-loop
#: encode is bounded at 240 x 50 = 12 000 points ≈ 3.6 ms).
MAX_NAMES: int = 50

#: The hard ceiling of the ``?max_points=`` a single request may ask
#: for.  The DEFAULT (:data:`DEFAULT_MAX_POINTS`) and this CEILING are
#: the SAME number by construction: the residual on-loop encode is
#: bounded at ``MAX_POINTS_CEILING x MAX_NAMES`` = 240 x 50 = 12 000
#: points ≈ 3.6 ms (AM-12), so a request ABOVE the ceiling is CLAMPED
#: down to it — never honoured.  (The clamp is the bound's enforcement:
#: a caller may not widen the daemon's own budget.)
MAX_POINTS_CEILING: int = 240

#: The hard ceiling of the families a single ``/families`` response
#: carries.
MAX_FAMILIES: int = 500

#: The byte ceiling of a single response body.  A response that would
#: exceed it is BOUNDED and carries a cursor — never silently truncated.
RESPONSE_BYTE_CEILING: int = 1_048_576

#: A sample is STALE when its age exceeds this multiple of the
#: effective interval (AM-5): a wedged sampler surfaces as staleness,
#: never a silently frozen line.
STALE_INTERVAL_MULTIPLIER: float = 3.0

#: The number of consecutive samplings with no published entry after
#: which a ring series is garbage-collected (AM-13).  The key is "the
#: metric still HAS a published entry" — an idle-but-live family is
#: never dropped.
IDLE_GC_SAMPLINGS: int = 3
