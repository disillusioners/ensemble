"""Resource monitor subsystem — observability spine for the
resource-cost-control commission, Phase 1.

Sub-modules:

  * ``proc_readers`` — cgroup v2 + /proc readers (T4).
  * ``ring``        — bounded per-tier deques (T5).
  * ``sampler``     — one threading.Thread(daemon=True) tick loop (T5).
  * ``flusher``     — batched executemany writes + roll-down (T6).
  * ``rollup``      — raw → minute → hour arithmetic + retention sweep
                     (T6).

The ``ResourceMonitor`` facade (see ``daemon/api.py`` lifespan
wiring, T7) composes the sampler + flusher + ring into a single
lifespan-managed subsystem.
"""

from .flusher import FLUSH_BUFFER_CAP, Flusher, FlusherCounters
from .proc_readers import (
    ChildSnapshot,
    CgroupCpu,
    CgroupMemory,
    CgroupSnapshot,
    DaemonProcSnapshot,
    ProcSnapshot,
    read_cgroup_snapshot,
    read_child_snapshot,
    read_daemon_snapshot,
    read_proc_snapshot,
)
from .ring import TierRing, default_ring_sizes
from .rollup import (
    RetentionPolicy,
    cutoff_iso,
    retention_cutoffs,
    rollup_to_hour,
    rollup_to_minute,
    sweep_query,
)
from .sampler import (
    LADDER_DEFAULT,
    LADDER_LEVELS,
    PSS_CADENCE,
    RE_PROMOTE_CLEAN_TICKS,
    Sampler,
    SamplerCounters,
)

__all__ = [
    # proc_readers
    "ChildSnapshot",
    "CgroupCpu",
    "CgroupMemory",
    "CgroupSnapshot",
    "DaemonProcSnapshot",
    "ProcSnapshot",
    "read_cgroup_snapshot",
    "read_child_snapshot",
    "read_daemon_snapshot",
    "read_proc_snapshot",
    # ring
    "TierRing",
    "default_ring_sizes",
    # sampler
    "LADDER_DEFAULT",
    "LADDER_LEVELS",
    "PSS_CADENCE",
    "RE_PROMOTE_CLEAN_TICKS",
    "Sampler",
    "SamplerCounters",
    # flusher
    "FLUSH_BUFFER_CAP",
    "Flusher",
    "FlusherCounters",
    # rollup
    "RetentionPolicy",
    "cutoff_iso",
    "retention_cutoffs",
    "rollup_to_hour",
    "rollup_to_minute",
    "sweep_query",
]