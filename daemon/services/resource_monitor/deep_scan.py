"""Deep-scan reconcile — P2 T4 (bounded graph-size walk, 1 instance/tick).

Hybrid-C third layer (architecture-recommendation §2.2b): the charge
seams (``charge.py``) drift — messages are removed by compaction
without a charge seam, estimates undercount — so a bounded
``sys.getsizeof`` walk of ONE cached instance graph per sampler tick
corrects the drift. It is a RECONCILE, never the primary source (D4):
``deep_bytes`` is stored NEXT TO ``charged_bytes`` (never blended into
it) and the sampler owns the coverage math (D13).

Contract (phase2-plan T4/D4, R2, AC2, AC10):

* **Bounded** — node cap + depth cap on the walk (``charge.bounded_deep_size``);
  cap trip ⇒ ``partial_walk=True`` propagated on the result AND into the
  instance's charge record.
* **Memoised** — shared substructure is counted exactly once (id-memo).
* **1 instance/tick** — :meth:`DeepScanner.scan_next` advances a
  round-robin cursor over the ``InstanceManager.instances`` mapping
  (``daemon/manager.py:592``, ``dict[str, tuple[CompiledStateGraph,
  str]]`` — the ``str`` is the instance's ``agent_dir``, so in-process
  agent identity rides along for free) and handles EXACTLY ONE
  instance per call. The sampler calls it once per tick.
* **Decoupled** — this module never imports the manager; the instances
  mapping is injected by the caller (sampler lane). Failures never
  propagate into the sampler tick (R2).
* **D14** — outputs are scalars only: byte totals + ids.

Config (env, read once at import — the config-knob plumbing for
``RESOURCE_MONITOR_DEEP_SCAN_*`` lives with the monitor config owner;
these defaults make the module tunable without touching that file):
``RESOURCE_MONITOR_DEEP_SCAN_NODE_CAP`` (default 20000),
``RESOURCE_MONITOR_DEEP_SCAN_DEPTH_CAP`` (default 24).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .charge import (
    DEFAULT_DEEP_SCAN_DEPTH_CAP,
    DEFAULT_DEEP_SCAN_NODE_CAP,
    bounded_deep_size,
    set_deep_bytes,
)

logger = logging.getLogger(__name__)

#: ``scan_kind`` signal the sampler persists on T2 rows (D4/T4).
SCAN_KIND_DEEP = "deep"


@dataclass
class DeepScanResult:
    """Scalar-only outcome of one instance's reconcile walk (D14)."""

    instance_id: str
    deep_bytes: int
    nodes_visited: int
    partial_walk: bool
    agent_dir: Optional[str] = None
    scan_kind: str = SCAN_KIND_DEEP


class DeepScanner:
    """Round-robin bounded walk over the live instance-graph cache.

    Constructed once by the sampler lane; ``instances`` is passed per
    call (live ``manager.instances`` view — no cached reference, so
    instance churn between ticks is always seen fresh).
    """

    def __init__(
        self,
        *,
        node_cap: Optional[int] = None,
        depth_cap: Optional[int] = None,
    ) -> None:
        self.node_cap = (
            DEFAULT_DEEP_SCAN_NODE_CAP if node_cap is None else int(node_cap)
        )
        self.depth_cap = (
            DEFAULT_DEEP_SCAN_DEPTH_CAP if depth_cap is None else int(depth_cap)
        )
        # Round-robin cursor: index into the CURRENT key ordering. The
        # manager's cache is an insertion-order LRU whose entries move
        # on touch, so the cursor is best-effort fairness (every tick
        # makes progress; a touched instance may revisit early) — not
        # a strict permutation guarantee.
        self._cursor = 0
        self.scans_completed = 0

    def scan_next(
        self, instances: Mapping[str, tuple]
    ) -> Optional[DeepScanResult]:
        """Reconcile EXACTLY ONE instance (1/tick pacing — D4/AC2).

        Returns ``None`` (and resets the cursor) when the mapping is
        empty, or when the walk fails — a failed scan is retried on a
        later tick by the natural cursor advance, never raised.
        """
        if not instances:
            self._cursor = 0
            return None
        keys = list(instances.keys())
        start = self._cursor % len(keys)
        self._cursor = (start + 1) % len(keys)
        instance_id = keys[start]
        # N-2 (P2 review): dict access sits BEFORE the walk-try, so a
        # manager-cache eviction racing the sampler raised KeyError
        # and discarded the WHOLE tick's attribution. ``.get`` + skip
        # degrades a single missing instance to a no-op — the cursor
        # has already advanced, so the scan retries on a later tick
        # by the natural round-robin (documented above).
        entry: Any = instances.get(instance_id)
        if entry is None:
            return None
        agent_dir: Optional[str] = None
        graph: Any = entry
        if isinstance(entry, tuple) and entry:
            graph = entry[0]
            agent_dir = (
                entry[1] if len(entry) > 1 and isinstance(entry[1], str) else None
            )
        try:
            deep_bytes, nodes, partial = bounded_deep_size(
                graph, node_cap=self.node_cap, depth_cap=self.depth_cap
            )
        except Exception:
            # Never crash the sampler tick (R2). Scalars-only log (D14).
            logger.warning(
                "resource-deep-scan: walk failed for instance %.8s",
                instance_id,
                exc_info=True,
            )
            return None
        # Correction into the instance's charge record (T4). Enabled-
        # gated inside the registry: with the monitor disabled this is
        # a no-op and the result is still returned for the caller.
        set_deep_bytes(instance_id, deep_bytes, partial_walk=partial)
        self.scans_completed += 1
        return DeepScanResult(
            instance_id=instance_id,
            deep_bytes=deep_bytes,
            nodes_visited=nodes,
            partial_walk=partial,
            agent_dir=agent_dir,
            scan_kind=SCAN_KIND_DEEP,
        )


__all__ = [
    "SCAN_KIND_DEEP",
    "DeepScanResult",
    "DeepScanner",
]
