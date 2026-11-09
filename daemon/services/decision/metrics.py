"""Decision module — metric families (``controls-and-phasing.md`` §2).

There is NO Prometheus/OTel client in this repository, so a "metric
family" here is a **module counter / structured log row / sink seam**
(IP-4). These families are registered through the canonical registry
(``daemon/checkpoint_metrics.py``: ``register_counter`` /
``register_gauge`` / ``register_histogram``) with the standard
registration-time metadata — ``family`` / ``source_idiom`` /
``durability`` / ``cardinality_weight`` / ``chart_role``.

**REGISTRAR PATH (L3).** The families are registered through the
**WIRING registrar**
(``daemon/services/metrics_query/wiring/registry.py::register_family_metric``),
so AM-2's enforcement is structural — a declared label key without an
``allowed_values`` set REFUSES the registration — and AM-3's
``source_idiom`` / ``durability`` / ``cardinality_weight`` kwargs are
REQUIRED (no defaults). The family is IN the adjudication pack's
census: ``decision_overlay`` carries a verdict in
``CATALOG_FAMILY_VERDICTS``, an entry in ``REGISTERED_FAMILIES`` and a
``FAMILY_SERIES_BANDS`` ceiling.

The package's own test (``tests/unit/decision/test_metrics_discipline.py``)
remains as a belt-and-braces pin on top of the pack's census.

**Naming (L1).** The registrar's canonical name rule is
``^ensemble_[a-z0-9_]+$`` (``daemon/checkpoint_metrics.py:110``), and
the validator RAISES on a violation (``:507-515``). So every series is
named ``ensemble_decision_overlay_*`` and the **family id** is the
single string ``decision_overlay``.

**Label discipline (L2/L3).** Every label key carries an explicit
``allowed_values`` set — the bounded vocabularies:

* ``seam_id`` — the design's four catalog seam ids;
* ``mode`` — ``shadow`` | ``deciding``;
* ``failure_class`` — the 19 distinct labels;
* ``reason`` — the 10-label suppressed vocabulary (9 + ``pricing_unconfigured``);
* ``verdict`` — ``agree`` | ``disagree`` | ``no_overlay``;
* ``outcome`` — ``verified_off`` | ``verified_on`` | ``unreadable``;
* ``state`` — ``ok`` | ``provisioning_missing`` | ``allow_list_excluded``.

**``question_id`` IS NOT A LABEL (L2).** The per-question A/B signal
goes to the SHADOW LOG — the design's own §2.6 substrate, whose row
already carries ``question_id`` as an opaque non-content id. An
unbounded per-question series would blow the cardinality cap and its
excess increments are silently DROPPED.

**R-B identity (stated once).**
``fallbacks_total == failures_total`` scoped to ``mode=deciding`` — the
fail-open invariant made observable (D2). ``floor_rejections_total`` is
a **labeled subset view** of
``failures_total{failure_class="below_floor"}``, NEVER an additive
term (``below_floor`` is counted once, as a failure).

Every helper here is fail-soft: an observability failure never breaks
the caller (the seam path must not depend on observability).
"""

from __future__ import annotations

import logging
from typing import Callable, Iterable, Mapping

from daemon import checkpoint_metrics as cm
from daemon.services.decision.types import FailureClass, SuppressionReason
from daemon.services.metrics_query.wiring import registry as wiring_registry

logger = logging.getLogger(__name__)

__all__ = [
    "COST_TRIP_REASONS",
    "FAILURE_CLASSES",
    "FAMILY_ID",
    "KNOWN_SEAM_IDS",
    "LIVENESS_REASONS",
    "MODES",
    "PROVISIONING_STATES",
    "REVERIFY_OUTCOMES",
    "SUPPRESSION_REASONS",
    "VERDICTS",
    "attempts_total",
    "cost_trip_total",
    "cost_tokens_total",
    "cost_total",
    "decision_metric_handles",
    "ensure_metrics_registered",
    "failures_total",
    "fallbacks_total",
    "floor_rejections_total",
    "liveness_alarm_total",
    "latency_seconds",
    "provisioning_state",
    "record_attempt",
    "record_cost",
    "record_cost_tokens",
    "record_cost_trip",
    "record_failure",
    "record_fallback",
    "record_floor_rejection",
    "record_liveness_alarm",
    "record_shadow_agreement",
    "record_shadow_disagreement",
    "record_shadow_latency",
    "record_shadow_row_dropped",
    "record_shadow_selection_agreement",
    "record_shadow_write_failure",
    "record_suppressed",
    "record_switch_reverify",
    "set_provisioning_state",
    "set_selection_overlap_ratio",
    "shadow_agreement_total",
    "shadow_disagreement_total",
    "shadow_latency_seconds",
    "shadow_rows_dropped_total",
    "shadow_selection_agreement_total",
    "shadow_selection_overlap_ratio",
    "shadow_write_failures_total",
    "suppressed_total",
    "switch_reverify_total",
]


#: The ONE family id every decision family carries (L1) — and the id
#: the adjudication pack's census carries for it.
FAMILY_ID = "decision_overlay"

#: The wiring cluster this lane's families belong to (the census's
#: cluster convention — W1..W4 are the census lanes; W5 is the
#: decision module's own).
CLUSTER = "W5"

#: The pack verdict for this family — ``IN`` (registered, observable,
#: no OUT half).
VERDICT = "IN"

#: The seam anchor the pack's verdict row cites.
SEAM_ANCHOR = "daemon/services/decision/metrics.py (the P1 decision overlay)"

#: The bounded ``seam_id`` vocabulary — the design's four catalog seams
#: (``seam-registry.md`` §5). A seam outside this set folds to the
#: registry's reserved ``other`` value (a visible drift signal).
KNOWN_SEAM_IDS: tuple[str, ...] = (
    "snapshot.l3.selection",
    "busy.slow.arbitration",
    "ghost.promise.detection",
    "attestation.rescue",
)

#: The two DISJOINT vocabularies of ``thin-client.md`` §6 — rows 6–30
#: carry 25 failure VALUES over these 19 DISTINCT labels (R-A).
FAILURE_CLASSES: tuple[str, ...] = tuple(fc.value for fc in FailureClass)

#: The closed 10-value suppression vocabulary (``controls-and-phasing.md``
#: §2.2 + L8's ``pricing_unconfigured``).
SUPPRESSION_REASONS: tuple[str, ...] = tuple(sr.value for sr in SuppressionReason)

#: The per-consult mode label.
MODES: tuple[str, ...] = ("shadow", "deciding")

#: The shadow row's verdict vocabulary.
VERDICTS: tuple[str, ...] = ("agree", "disagree", "no_overlay")

#: The liveness alarm reasons (``controls-and-phasing.md`` §2.9).
LIVENESS_REASONS: tuple[str, ...] = (
    "never_consulted",
    "provisioning_missing",
    "allow_list_excluded",
)

#: The provisioning states (``controls-and-phasing.md`` §2.9).
PROVISIONING_STATES: tuple[str, ...] = (
    "ok",
    "provisioning_missing",
    "allow_list_excluded",
)

#: The boot re-verify outcomes (AC-9).
REVERIFY_OUTCOMES: tuple[str, ...] = ("verified_off", "verified_on", "unreadable")

#: The auto-trip reasons (FLAG3/FLAG2).
COST_TRIP_REASONS: tuple[str, ...] = ("global_ceiling", "per_seam_share")


# ── registration ─────────────────────────────────────────────────────────

_registered = False
_handles: dict[str, object] = {}


def ensure_metrics_registered() -> None:
    """Register the decision-overlay families exactly once.

    Idempotent: the module-level import registers them, and this
    function makes the guarantee explicit for callers that want the
    families live BEFORE the first overlay call (the P1 exit criteria
    — "the global and shadow families must be live and populated
    before the first overlay call").

    Every registration goes through the WIRING registrar (L3), so the
    label vocabularies are enforced (AM-2) and the family metadata is
    mandatory (AM-3).
    """
    global _registered
    if _registered:
        return
    _registered = True

    seam = ("seam_id",)
    seam_values = {"seam_id": KNOWN_SEAM_IDS}

    def _counter(
        name: str,
        label_keys: Iterable[str],
        allowed_values: Mapping[str, Iterable[str]],
        help_text: str,
        weight: int,
    ) -> cm.Metric:
        return wiring_registry.register_family_metric(
            family=FAMILY_ID,
            cluster=CLUSTER,
            name=name,
            kind="counter",
            verdict=VERDICT,
            seam=SEAM_ANCHOR,
            label_keys=label_keys,
            allowed_values=allowed_values,
            help=help_text,
            source_idiom="registry",
            durability="process",
            cardinality_weight=weight,
        )

    def _gauge(
        name: str,
        label_keys: Iterable[str],
        allowed_values: Mapping[str, Iterable[str]],
        help_text: str,
        weight: int,
    ) -> cm.Metric:
        return wiring_registry.register_family_metric(
            family=FAMILY_ID,
            cluster=CLUSTER,
            name=name,
            kind="gauge",
            verdict=VERDICT,
            seam=SEAM_ANCHOR,
            label_keys=label_keys,
            allowed_values=allowed_values,
            help=help_text,
            source_idiom="registry",
            durability="process",
            cardinality_weight=weight,
        )

    def _histogram(
        name: str,
        label_keys: Iterable[str],
        allowed_values: Mapping[str, Iterable[str]],
        help_text: str,
        weight: int,
    ) -> cm.Metric:
        return wiring_registry.register_family_metric(
            family=FAMILY_ID,
            cluster=CLUSTER,
            name=name,
            kind="histogram",
            verdict=VERDICT,
            seam=SEAM_ANCHOR,
            label_keys=label_keys,
            allowed_values=allowed_values,
            help=help_text,
            source_idiom="registry",
            durability="process",
            cardinality_weight=weight,
        )

    handles = {
        "attempts_total": _counter(
            "ensemble_decision_overlay_attempts_total",
            ("seam_id", "mode"),
            {"seam_id": KNOWN_SEAM_IDS, "mode": MODES},
            "Overlay calls that reached the socket (one per attempt).",
            2,
        ),
        "failures_total": _counter(
            "ensemble_decision_overlay_failures_total",
            ("seam_id", "failure_class", "mode"),
            {
                "seam_id": KNOWN_SEAM_IDS,
                "failure_class": FAILURE_CLASSES,
                "mode": MODES,
            },
            "Overlay failures by class and mode (19 distinct classes).",
            5,
        ),
        "fallbacks_total": _counter(
            "ensemble_decision_overlay_fallbacks_total",
            ("seam_id", "mode"),
            {"seam_id": KNOWN_SEAM_IDS, "mode": MODES},
            "Times the heuristic absorbed a failure (== failures, mode=deciding).",
            2,
        ),
        "latency_seconds": _histogram(
            "ensemble_decision_overlay_latency_seconds",
            seam,
            seam_values,
            "Overlay call latency in seconds.",
            2,
        ),
        "cost_total": _counter(
            "ensemble_decision_overlay_cost_total",
            seam,
            seam_values,
            "Billed overlay cost in USD — only attempts that reached the socket.",
            2,
        ),
        "cost_tokens_total": _counter(
            "ensemble_decision_overlay_cost_tokens_total",
            seam,
            seam_values,
            "Metered overlay tokens — the fail-safe meter when no USD rate exists.",
            2,
        ),
        "floor_rejections_total": _counter(
            "ensemble_decision_overlay_floor_rejections_total",
            ("seam_id", "mode"),
            {"seam_id": KNOWN_SEAM_IDS, "mode": MODES},
            "Subset view of failures{failure_class=below_floor} — never additive.",
            2,
        ),
        "suppressed_total": _counter(
            "ensemble_decision_overlay_suppressed_total",
            ("seam_id", "reason"),
            {"seam_id": KNOWN_SEAM_IDS, "reason": SUPPRESSION_REASONS},
            "Consults that made NO call (closed 10-value reason vocabulary).",
            4,
        ),
        # L2 — NO question_id label: the per-question A/B signal lives in
        # the SHADOW LOG (the §2.6 substrate), which already carries it.
        "shadow_agreement_total": _counter(
            "ensemble_decision_overlay_shadow_agreement_total",
            ("seam_id", "verdict"),
            {"seam_id": KNOWN_SEAM_IDS, "verdict": VERDICTS},
            "Shadow A/B verdicts (aggregated per seam).",
            3,
        ),
        "shadow_disagreement_total": _counter(
            "ensemble_decision_overlay_shadow_disagreement_total",
            seam,
            seam_values,
            "Where the overlay disagreed with the heuristic (aggregated per seam).",
            2,
        ),
        "shadow_latency_seconds": _histogram(
            "ensemble_decision_overlay_shadow_latency_seconds",
            seam,
            seam_values,
            "Overlay-only latency, distinct from the end-to-end latency.",
            2,
        ),
        "liveness_alarm_total": _counter(
            "ensemble_decision_overlay_liveness_alarm_total",
            ("seam_id", "reason"),
            {"seam_id": KNOWN_SEAM_IDS, "reason": LIVENESS_REASONS},
            "'Enabled but never consults' liveness alarms.",
            4,
        ),
        "provisioning_state": _gauge(
            "ensemble_decision_overlay_provisioning_state",
            ("seam_id", "state"),
            {"seam_id": KNOWN_SEAM_IDS, "state": PROVISIONING_STATES},
            "Per-seam provisioning state (ok | provisioning_missing | allow_list_excluded).",
            3,
        ),
        "shadow_write_failures_total": _counter(
            "ensemble_decision_overlay_shadow_write_failures_total",
            (),
            None,
            "Shadow-writer failures (counted, swallowed — the writer never fails a caller).",
            1,
        ),
        "shadow_rows_dropped_total": _counter(
            "ensemble_decision_overlay_shadow_rows_dropped_total",
            seam,
            seam_values,
            "Shadow rows dropped by the per-seam row-count cap.",
            2,
        ),
        "switch_reverify_total": _counter(
            "ensemble_decision_overlay_switch_reverify_total",
            ("seam_id", "outcome"),
            {"seam_id": KNOWN_SEAM_IDS, "outcome": REVERIFY_OUTCOMES},
            "Boot re-verification of every kill (a kill is never undone by a restart).",
            4,
        ),
        "cost_trip_total": _counter(
            "ensemble_decision_overlay_cost_trip_total",
            ("reason",),
            {"reason": COST_TRIP_REASONS},
            "Auto-trips fired by the cost ceiling (global) or the per-seam share guard.",
            1,
        ),
        # The S1 pair (§2.4) — set-level comparison of the overlay's
        # ranked selection against the heuristic's.
        "shadow_selection_agreement_total": _counter(
            "ensemble_decision_overlay_shadow_selection_agreement_total",
            seam,
            seam_values,
            "S1: shadow selections compared set-wise against the heuristic's.",
            2,
        ),
        "shadow_selection_overlap_ratio": _gauge(
            "ensemble_decision_overlay_shadow_selection_overlap_ratio",
            seam,
            seam_values,
            "S1: Jaccard overlap of the overlay's selection and the heuristic's.",
            2,
        ),
    }
    _handles.update(handles)
    globals().update(handles)


ensure_metrics_registered()


def decision_metric_handles() -> dict[str, object]:
    """The registered handles — the package's own census surface."""
    return dict(_handles)


# ── handles (bound after registration) ───────────────────────────────────

attempts_total = _handles["attempts_total"]
failures_total = _handles["failures_total"]
fallbacks_total = _handles["fallbacks_total"]
latency_seconds = _handles["latency_seconds"]
cost_total = _handles["cost_total"]
cost_tokens_total = _handles["cost_tokens_total"]
floor_rejections_total = _handles["floor_rejections_total"]
suppressed_total = _handles["suppressed_total"]
shadow_agreement_total = _handles["shadow_agreement_total"]
shadow_disagreement_total = _handles["shadow_disagreement_total"]
shadow_latency_seconds = _handles["shadow_latency_seconds"]
liveness_alarm_total = _handles["liveness_alarm_total"]
provisioning_state = _handles["provisioning_state"]
shadow_write_failures_total = _handles["shadow_write_failures_total"]
shadow_rows_dropped_total = _handles["shadow_rows_dropped_total"]
switch_reverify_total = _handles["switch_reverify_total"]
cost_trip_total = _handles["cost_trip_total"]
shadow_selection_agreement_total = _handles["shadow_selection_agreement_total"]
shadow_selection_overlap_ratio = _handles["shadow_selection_overlap_ratio"]


# ── recording helpers (fail-soft — observability never breaks a caller) ──


def _safe(fn: Callable[[], None], *, what: str) -> None:
    """Run a metric update; swallow and log any failure."""
    try:
        fn()
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug(
            "decision metric %s failed (non-fatal): %s: %s",
            what,
            type(exc).__name__,
            exc,
        )


def record_attempt(seam_id: str, mode: str) -> None:
    """Count one attempt that reached the socket."""
    _safe(
        lambda: attempts_total.inc(1, {"seam_id": seam_id, "mode": mode}),
        what="attempts_total",
    )


def record_failure(seam_id: str, failure_class: str, mode: str) -> None:
    """Count one failure by class and mode."""
    _safe(
        lambda: failures_total.inc(
            1,
            {"seam_id": seam_id, "failure_class": failure_class, "mode": mode},
        ),
        what="failures_total",
    )


def record_fallback(seam_id: str, mode: str) -> None:
    """Count one absorbed failure (the heuristic carried the outcome)."""
    _safe(
        lambda: fallbacks_total.inc(1, {"seam_id": seam_id, "mode": mode}),
        what="fallbacks_total",
    )


def record_latency(seam_id: str, seconds: float) -> None:
    """Observe one attempt's latency."""
    _safe(
        lambda: latency_seconds.observe(float(seconds), {"seam_id": seam_id}),
        what="latency_seconds",
    )


def record_cost(seam_id: str, usd: float) -> None:
    """Add one attempt's billed USD cost."""
    _safe(
        lambda: cost_total.inc(float(usd), {"seam_id": seam_id}),
        what="cost_total",
    )


def record_cost_tokens(seam_id: str, tokens: int) -> None:
    """Add one attempt's metered tokens (the fail-safe meter)."""
    _safe(
        lambda: cost_tokens_total.inc(float(tokens), {"seam_id": seam_id}),
        what="cost_tokens_total",
    )


def record_floor_rejection(seam_id: str, mode: str) -> None:
    """Count one below-floor answer (the subset view of failures)."""
    _safe(
        lambda: floor_rejections_total.inc(1, {"seam_id": seam_id, "mode": mode}),
        what="floor_rejections_total",
    )


def record_suppressed(seam_id: str, reason: str) -> None:
    """Count one consult that made NO call (or a visible degradation)."""
    _safe(
        lambda: suppressed_total.inc(1, {"seam_id": seam_id, "reason": reason}),
        what="suppressed_total",
    )


def record_shadow_agreement(seam_id: str, verdict: str) -> None:
    """Count one shadow A/B verdict (aggregated per seam — L2)."""
    _safe(
        lambda: shadow_agreement_total.inc(
            1, {"seam_id": seam_id, "verdict": verdict}
        ),
        what="shadow_agreement_total",
    )


def record_shadow_disagreement(seam_id: str) -> None:
    """Count one shadow disagreement (aggregated per seam — L2)."""
    _safe(
        lambda: shadow_disagreement_total.inc(1, {"seam_id": seam_id}),
        what="shadow_disagreement_total",
    )


def record_shadow_latency(seam_id: str, seconds: float) -> None:
    """Observe one overlay-only latency."""
    _safe(
        lambda: shadow_latency_seconds.observe(float(seconds), {"seam_id": seam_id}),
        what="shadow_latency_seconds",
    )


def record_liveness_alarm(seam_id: str, reason: str) -> None:
    """Count one liveness alarm."""
    _safe(
        lambda: liveness_alarm_total.inc(1, {"seam_id": seam_id, "reason": reason}),
        what="liveness_alarm_total",
    )


def set_provisioning_state(seam_id: str, state: str) -> None:
    """Set the per-seam provisioning state gauge."""
    _safe(
        lambda: provisioning_state.set(1, {"seam_id": seam_id, "state": state}),
        what="provisioning_state",
    )


def record_shadow_write_failure() -> None:
    """Count one shadow-writer failure (the writer never fails a caller)."""
    _safe(
        lambda: shadow_write_failures_total.inc(1),
        what="shadow_write_failures_total",
    )


def record_shadow_row_dropped(seam_id: str) -> None:
    """Count one shadow row dropped by the row-count cap."""
    _safe(
        lambda: shadow_rows_dropped_total.inc(1, {"seam_id": seam_id}),
        what="shadow_rows_dropped_total",
    )


def record_switch_reverify(seam_id: str, outcome: str) -> None:
    """Count one boot re-verification of a kill."""
    _safe(
        lambda: switch_reverify_total.inc(
            1, {"seam_id": seam_id, "outcome": outcome}
        ),
        what="switch_reverify_total",
    )


def record_cost_trip(reason: str) -> None:
    """Count one cost-ceiling auto-trip."""
    _safe(
        lambda: cost_trip_total.inc(1, {"reason": reason}),
        what="cost_trip_total",
    )


def record_shadow_selection_agreement(seam_id: str) -> None:
    """Count one S1 set-level selection comparison."""
    _safe(
        lambda: shadow_selection_agreement_total.inc(1, {"seam_id": seam_id}),
        what="shadow_selection_agreement_total",
    )


def set_selection_overlap_ratio(seam_id: str, ratio: float) -> None:
    """Set the S1 selection overlap ratio gauge."""
    _safe(
        lambda: shadow_selection_overlap_ratio.set(
            float(ratio), {"seam_id": seam_id}
        ),
        what="shadow_selection_overlap_ratio",
    )
