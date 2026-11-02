"""The ADJUDICATION TABLE — every catalog family carries ONE verdict.

The plan's task 4.10 requires: **every catalog family F0.1–F22 has
exactly ONE verdict** (IN / OUT / PARTIAL / ALREADY-LIVE) **with a seam
anchor**, and every OUT metric is **absent from the registry AND from
the ``/metrics`` body**.

This module is the machine-readable SSOT of that table; the pack
(``metrics_family_adjudication_*``) asserts it, and the plan's docs quote
it.

THE VERDICTS
------------
=========  ===========  =================================================
family     verdict       seam / why
=========  ===========  =================================================
F0.1       ALREADY-LIVE  the exposition listener (metrics-collection P0)
F0.2       ALREADY-LIVE  the canonical registry
F0.3       IN            the resource spine (WS-A conditional)
F0.4       ALREADY-LIVE  the vocabulary freeze marker
F1         IN            the loop-lag quantiles
F2         OUT           the VALUE ruling (AM-25 / LR-2) — not the rule
F3         IN            the wedge capture counters
F4         IN            the pool native quantities
F5         IN            the pool derived quantities
F6         IN            the PostgreSQL census
F7         PARTIAL       the bytes histogram only (the write hook is OUT)
F8         PARTIAL       the byte gauges only (the prune counters are OUT)
F9         IN            the readiness family
F10        IN            the job-queue depth
F11        PARTIAL       the pool gauges (the claim latency is OUT)
F12        IN            the sweep counters
F13        PARTIAL       the census gauges (the wake/lifecycle counters are OUT)
F14        OUT           the commission's OUT (new instrumentation)
F15        OUT           the commission's OUT
F16        OUT           the commission's OUT
F17        IN            the MCP health family
F18        PARTIAL       the sessions / tombstones / audit
F19        OUT           no pull-able snapshot
F20        OUT           the commission's OUT
F21        PARTIAL       the three read gauges (the phases are OUT)
F22        OUT           the commission's OUT (an out-of-process writer)
=========  ===========  =================================================

**10 full + 6 partial = 16 of 26** — the plan's binding subset.
"""

from __future__ import annotations

#: family id → (verdict, seam anchor / rationale).
CATALOG_FAMILY_VERDICTS: dict[str, tuple[str, str]] = {
    "F0.1": ("ALREADY-LIVE", "daemon/services/metrics_listener.py (the exposition listener)"),
    "F0.2": ("ALREADY-LIVE", "daemon/checkpoint_metrics.py (the canonical registry)"),
    "F0.3": ("IN", "daemon/services/resource_monitor/flusher.py:161 + sampler.py"),
    "F0.4": ("ALREADY-LIVE", "daemon/checkpoint_metrics.py (the vocabulary marker)"),
    "F1": ("IN", "daemon/services/wedge_capture/lag_probe.py:107-116, :185"),
    "F2": ("OUT", "the VALUE ruling (AM-25 / LR-2) — prod is Darwin-primary"),
    "F3": ("IN", "daemon/services/wedge_capture/triggers.py:202/:237/:281"),
    "F4": ("IN", "daemon/services/readiness.py:658/:685"),
    "F5": ("IN", "daemon/services/wedge_capture/collector.py:19-23"),
    "F6": ("IN", "daemon/services/metrics_census.py (the frozen CensusSnapshot)"),
    "F7": ("PARTIAL", "daemon/persistence.py:632 (the bytes call site only)"),
    "F8": ("PARTIAL", "daemon/services/maintenance.py:531-536 (the byte gauges only)"),
    "F9": ("IN", "daemon/api.py:5015 (the published composite)"),
    "F10": ("IN", "daemon/repositories/job_queue/models.py:40-43"),
    "F11": ("PARTIAL", "daemon/services/worker_pool.py:1518 (no claim latency)"),
    "F12": ("IN", "the sweep services' counters() readers"),
    "F13": ("PARTIAL", "the instances census + the waiting-children query"),
    "F14": ("OUT", "the commission's OUT — a new increment at the usage site"),
    "F15": ("OUT", "the commission's OUT — a new observe at the request sites"),
    "F16": ("OUT", "the commission's OUT — a new counter at the force-abort site"),
    "F17": ("IN", "daemon/mcp/init_guard.py:145-147 (the published snapshot)"),
    "F18": ("PARTIAL", "the gateway service + the durable audit table"),
    "F19": ("OUT", "no pull-able snapshot — a new observe in the compaction hot path"),
    "F20": ("OUT", "the commission's OUT — a new increment at the tap"),
    "F21": ("PARTIAL", "daemon/tools/upgrade_journal.py:159/:165/:255 (the three read gauges)"),
    "F22": ("OUT", "the commission's OUT — an out-of-process textfile writer"),
}

#: The verdicts the table may carry.
VALID_VERDICTS = ("IN", "OUT", "PARTIAL", "ALREADY-LIVE")

#: The families this lane registered (the IN + PARTIAL set).
REGISTERED_FAMILIES: tuple[str, ...] = (
    "F0.3",
    "F1",
    "F3",
    "F4",
    "F5",
    "F6",
    "F7",
    "F8",
    "F9",
    "F10",
    "F11",
    "F12",
    "F13",
    "F17",
    "F18",
    "F21",
)

#: The canonical names of every metric that must be ABSENT from the
#: registry and from the ``/metrics`` body.
OUT_METRIC_NAMES: tuple[str, ...] = (
    # F2 — the value ruling.
    "ensemble_thread_cpu_seconds",
    "ensemble_thread_count",
    # F14 / F15 / F16 — the commission's OUT families.
    "ensemble_llm_tokens_total",
    "ensemble_llm_token_usage_missing_total",
    "ensemble_llm_request_total",
    "ensemble_llm_request_seconds",
    "ensemble_llm_stream_stalls_total",
    "ensemble_llm_stream_watchdog_sweep_total",
    # F19 / F20 / F22 — the commission's OUT families.
    "ensemble_compaction_fired_total",
    "ensemble_compaction_occupancy_ratio",
    "ensemble_compaction_precall_estimate_tokens",
    "ensemble_turn_events_total",
    "ensemble_turn_duration_seconds",
    "ensemble_tool_calls_total",
    "ensemble_process_cpu_seconds",
    "ensemble_textfile_last_write_timestamp_seconds",
    # F8 — the OUT half of the PARTIAL family.
    "ensemble_checkpoint_versions_per_thread",
    "ensemble_checkpoint_prune_runs_total",
    # F11 — the OUT half of the PARTIAL family.
    "ensemble_job_claim_latency_seconds",
    # F13 — the OUT half of the PARTIAL family.
    "ensemble_wake_turns_total",
    "ensemble_instance_lifecycle_total",
    # F18 — the OUT half of the PARTIAL family.
    "ensemble_mcps_gateway_session_total",
    # F21 — the OUT half of the PARTIAL family (the phase families).
    "ensemble_journal_op_seconds",
    "ensemble_journal_ops_total",
    "ensemble_upgrade_phase_seconds",
    "ensemble_upgrade_phase_total",
    "ensemble_upgrade_journal_events_total",
)

#: The per-family CLOSED label-space ceiling (the band the plan pins).
#:
#: The values are the catalog's own §1 / D5 label vocabularies
#: MULTIPLIED OUT (a histogram counts its buckets + count + sum per
#: label combination) — the catalog's live ESTIMATE is the subset of
#: combinations that actually fire, so the closed space is the honest
#: upper bound the pack can measure.  The band is a ceiling: the pack
#: asserts the measured closed space never exceeds it.
FAMILY_SERIES_BANDS: dict[str, int] = {
    "F0.3": 8,
    "F1": 9,
    "F3": 6,
    "F4": 14,
    "F5": 4,
    "F6": 15,
    "F7": 20,
    "F8": 4,
    "F9": 53,
    "F10": 6,
    "F11": 16,
    "F12": 117,
    "F13": 19,
    "F17": 40,
    "F18": 5,
    "F21": 32,
}

#: The registered baseline (the catalog convention, F7c-inclusive).
BASELINE_SERIES = 89

#: The plan's pinned post-lane budget (the catalog convention).
BUDGET_SERIES = 363

#: The global cap (the ≤2000 planning convention).
GLOBAL_SERIES_CAP = 2000


__all__ = [
    "BASELINE_SERIES",
    "BUDGET_SERIES",
    "CATALOG_FAMILY_VERDICTS",
    "FAMILY_SERIES_BANDS",
    "GLOBAL_SERIES_CAP",
    "OUT_METRIC_NAMES",
    "REGISTERED_FAMILIES",
    "VALID_VERDICTS",
]
