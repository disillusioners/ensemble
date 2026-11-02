"""The closed label vocabularies of the P4 extension subset.

EVERY label key of EVERY family this lane registers declares an
``allowed_values`` set (AM-2).  A key without one is char-checked only
and can reach the per-metric cap with raw values — the fold target is
the reserved ``other`` residual (``checkpoint_metrics.RESERVED_LABEL_VALUE``),
so a disallowed value is counted as an invalid label, never silently
accepted.

DERIVATION (catalog §8 rule A1)
--------------------------------
Every set below is derived FROM CODE, never hand-listed.  The source is
named in each block's comment and the family packs cross-check the
runtime source against the declared set — a producer that grows a new
value must grow the vocabulary with it, or the value folds to ``other``
and the pack fails.

THE ONE EXCEPTION
-----------------
``RELEASE_VERSIONS`` (F21's ``version`` label) is the RELEASE DIRECTORY
set, which is a property of the INSTALL, not of the code.  It is
resolved at import time from the resolvable install dir and always
carries the reserved ``other`` residual, so a release name outside the
registered set folds to ``other`` instead of opening an unbounded label
space.  The daemon re-imports after every promote (a promote restarts
the daemon), so the set is re-derived on every boot.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

# ── the reserved residual (the fold target for every disallowed value) ────
RESERVED = "other"


def _enum_values(enum_cls: object) -> tuple[str, ...]:
    """The string values of a ``str``-Enum, in declaration order."""
    return tuple(str(member.value) for member in enum_cls)  # type: ignore[attr-defined]


# ── F0.3 — the resource spine's four tables ───────────────────────────────
# Source: the four tables the resource monitor's flusher writes
# (`daemon/services/resource_monitor/flusher.py`) — the catalog's
# §8.9 "spine table (4)" set.  ``resource_limit_policy`` accepts ZERO
# rows by design (it is a policy table), so its "accepting rows" gauge
# renders 1 while its row counter stays 0.
SPINE_TABLES: tuple[str, ...] = (
    "resource_daemon_bucket",
    "resource_instance_sample",
    "resource_pid_map",
    "resource_limit_policy",
)

# ── F1 — the loop-lag quantile + probe-state vocabularies ─────────────────
# Source: `daemon/services/wedge_capture/lag_probe.py` — the computed
# quantiles (`LagHistogram.as_dict`: max/p95/p50) and the probe-state
# constants.
LAG_STATS: tuple[str, ...] = ("p50", "p95", "max")
LAG_PROBE_STATES: tuple[str, ...] = ("initializing", "ok", "unknown", "stopped")

# ── F3 — the wedge-capture reason + write-outcome vocabularies ───────────
# Source: `daemon/services/wedge_capture/triggers.py` — CANONICAL_REASONS.
WEDGE_REASONS: tuple[str, ...] = (
    "sustained_cpu",
    "sustained_loop_lag",
    "thread_concentration",
    "manual",
)
WEDGE_WRITE_OUTCOMES: tuple[str, ...] = ("ok", "failed")

# ── F4 / F5 — the pool vocabularies ───────────────────────────────────────
# Source: `daemon/services/wedge_capture/collector.py` (the A2 canonical
# pool counter names) + `daemon/services/readiness.py`
# (`sample_sqlalchemy_pool_state` / `sample_psycopg_pool_state`).
POOL_NAMES: tuple[str, ...] = ("manager", "checkpoint")
POOL_NATIVE_QUANTITIES: tuple[str, ...] = ("size", "max", "available", "overflow")
POOL_REQUEST_ERROR_KINDS: tuple[str, ...] = (
    "waiting_exception",
    "too_many_requests",
    "unclassified",
)

# ── F6 — the PostgreSQL census vocabularies ───────────────────────────────
# Source: PostgreSQL's `pg_stat_activity.state` domain (snake-cased for
# label safety) + the reserved `unknown` for a NULL state; the leak-site
# map is the census's own 6-value fixed fragment→site map
# (`daemon/services/metrics_census.py`).
PG_BACKEND_STATES: tuple[str, ...] = (
    "active",
    "idle",
    "idle_in_transaction",
    "idle_in_transaction_aborted",
    "fastpath_function_call",
    "disabled",
    "unknown",
)
PG_LEAK_SITES: tuple[str, ...] = (
    "instance_read",
    "instance_parent_lookup",
    "job_queue_read",
    "checkpoint_saver",
    "agent_db_tool",
    "other",
)

# ── F8 — the checkpoint store tables ───────────────────────────────────────
# Source: `daemon/services/maintenance.py` — `_VACUUM_FULL_TABLES`.
CHECKPOINT_TABLES: tuple[str, ...] = ("checkpoints", "checkpoint_writes", "checkpoint_blobs")

# ── F9 — the readiness outcome + component vocabularies ───────────────────
# Source: `daemon/services/readiness.py` — the §4.0 failure classes
# (FAILURE_CLASS_*) and READINESS_COMPONENT_NAMES.
READINESS_COMPONENTS: tuple[str, ...] = (
    "database",
    "queue_freshness",
    "services",
    "checkpoint_saver",
)
READINESS_OUTCOMES: tuple[str, ...] = (
    "probe_timeout",
    "pool_busy",
    "queue_stale",
    "read_timeout",
    "boot_incomplete",
    "identity_mismatch",
    "hard_component_red",
    "not_serving",
    "pool_leak",
    "unknown",
    "ok",
)

# ── F10 — the job-queue admission-state vocabulary ────────────────────────
# Source: `daemon/repositories/job_queue/models.py` — the AdmissionState
# enum, the SOLE write authority for queue gating.
def _admission_states() -> tuple[str, ...]:
    from daemon.repositories.job_queue.models import AdmissionState

    return tuple(member.value for member in AdmissionState)


# ── F11 — the worker-pool + event vocabularies ────────────────────────────
# Source: `daemon/services/worker_pool.py` — the `self._stats` dict
# literal (the four event counters) and the pool lanes.
WORKER_POOL_NAMES: tuple[str, ...] = ("worker", "chat")
WORKER_POOL_EVENTS: tuple[str, ...] = (
    "notifications_sent",
    "empty_claim_attempts",
    "workers_woken_by_timeout",
    "claims_skipped_due_to_busy_instance",
)

# ── F12 — the sweep lane + row-outcome vocabularies ──────────────────────
# Source: the seven RDRS lanes (`daemon/services/report_delivery_recovery.py`)
# plus the six standalone sweep services (`watch_reconcile`,
# `eligible_pending`, `stale_pause`, `orphan_watcher`, `job_lock`,
# `upgrade_journal`).
SWEEP_LANES: tuple[str, ...] = (
    "rdrs_deferred",
    "rdrs_no_row_backstop",
    "rdrs_pending_age",
    "rdrs_recovery_retry",
    "rdrs_orphan",
    "rdrs_stuck_wake",
    "rdrs_mission_reconciliation",
    "watch_reconcile",
    "eligible_pending",
    "stale_pause",
    "orphan_watcher",
    "job_lock",
    "upgrade_journal",
)
SWEEP_ROW_OUTCOMES: tuple[str, ...] = (
    "processed",
    "skipped",
    "held",
    "released",
    "retired",
    "error",
)

# ── F13 — the instance status + liveness vocabularies ────────────────────
# Source: `daemon/repositories/instance/models.py` — the InstanceStatus
# enum (10 values); `daemon/services/work_status.py` — the canonical
# liveness vocabulary (6 reachable) + the reserved `other`.
def _instance_statuses() -> tuple[str, ...]:
    from daemon.repositories.instance.models import InstanceStatus

    return _enum_values(InstanceStatus)


INSTANCE_LIVENESS: tuple[str, ...] = (
    "pending",
    "processing",
    "paused",
    "completed",
    "failed",
    "cancelled",
    "other",
)

# ── F17 — the MCP roster + state + failure-kind vocabularies ─────────────
# Source: `daemon/mcp/builtin_servers/` (the three built-ins),
# `daemon/repositories/mcp_server/models.py` (the user-created rows fold
# into `custom`) and `daemon/mcp/init_guard.py` (the state set).
MCP_ROSTER: tuple[str, ...] = ("context7", "plane", "webfetch", "custom", "other")
MCP_STATES: tuple[str, ...] = ("ok", "degraded", "quarantined", "unknown")
MCP_FAILURE_KINDS: tuple[str, ...] = ("connect", "initialize", "tool_call", "quarantine")

# ── F18 — the gateway audit-outcome vocabulary (AMENDED) ──────────────────
# Source: `daemon/services/mcps_gateway/audit.py` — OUTCOME_OK /
# OUTCOME_ERROR / OUTCOME_TIMEOUT.  `denied` is declared+exported but
# NEVER written, so it is NOT in the vocabulary (an over-promise would
# repeat the F7 defect class).  `timeout` is its OWN label — never
# folded into `error` (AD4(a)).
GATEWAY_AUDIT_OUTCOMES: tuple[str, ...] = ("ok", "error", "timeout")


# ── F21 — the release-version vocabulary (the install's property) ─────────
def _candidate_install_dirs() -> tuple[Path, ...]:
    """The install dirs the release set can live in (a LIGHT scan).

    The canonical resolver (``daemon.tools.upgrade_tools._resolve_install_dir``)
    imports the whole tool registry — far too heavy for a vocabulary
    computed at import time.  This scan only NAMES the release
    directories; the ADAPTER itself uses the canonical resolver, so the
    metric's VALUES always come from the daemon's own resolution.
    """
    candidates: list[Path] = []
    try:
        home = Path.home()
    except Exception:  # noqa: BLE001 — no home ⇒ no release set
        home = None
    if home is not None:
        candidates.append(home / "agents-ensemble")
        candidates.append(home / "agents-ensemble-demo")
    if getattr(sys, "frozen", False):
        try:
            candidates.append(Path(sys.executable).resolve().parent.parent.parent)
        except OSError:
            pass
    return tuple(candidates)


def _release_versions() -> tuple[str, ...]:
    """The release directory names at import time (+ the reserved fold)."""
    names: set[str] = {RESERVED}
    for candidate in _candidate_install_dirs():
        try:
            for entry in Path(candidate, "releases").iterdir():
                if entry.is_dir():
                    names.add(entry.name)
        except OSError:
            continue
    try:
        from daemon import __version__ as _daemon_version

        names.add(f"v{_daemon_version}")
    except Exception:  # noqa: BLE001 — the version is optional
        pass
    return tuple(sorted(names))


RELEASE_VERSIONS: tuple[str, ...] = _release_versions()


def allowed(mapping: dict[str, Iterable[str]]) -> dict[str, frozenset[str]]:
    """Freeze a label→values mapping into the registry's shape."""
    return {str(key): frozenset(str(v) for v in values) for key, values in mapping.items()}


# The lazily-resolved vocabularies are exposed through FUNCTIONS (the
# import-time evaluation of the enum-derived sets would drag the
# repository models into every importer of this module).
def admission_states() -> tuple[str, ...]:
    """The AdmissionState enum's values (the SOLE write authority)."""
    return _admission_states()


def instance_statuses() -> tuple[str, ...]:
    """The InstanceStatus enum's values (the 10-value census vocabulary)."""
    return _instance_statuses()


__all__ = [
    "CHECKPOINT_TABLES",
    "GATEWAY_AUDIT_OUTCOMES",
    "INSTANCE_LIVENESS",
    "LAG_PROBE_STATES",
    "LAG_STATS",
    "MCP_FAILURE_KINDS",
    "MCP_ROSTER",
    "MCP_STATES",
    "PG_BACKEND_STATES",
    "PG_LEAK_SITES",
    "POOL_NAMES",
    "POOL_NATIVE_QUANTITIES",
    "POOL_REQUEST_ERROR_KINDS",
    "READINESS_COMPONENTS",
    "READINESS_OUTCOMES",
    "RELEASE_VERSIONS",
    "RESERVED",
    "SPINE_TABLES",
    "SWEEP_LANES",
    "SWEEP_ROW_OUTCOMES",
    "WEDGE_REASONS",
    "WEDGE_WRITE_OUTCOMES",
    "WORKER_POOL_EVENTS",
    "WORKER_POOL_NAMES",
    "admission_states",
    "allowed",
    "instance_statuses",
]
