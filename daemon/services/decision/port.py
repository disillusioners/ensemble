"""Decision module — the narrow OUTWARD port (``port.py``).

This is the seam the SNAPSHOT side calls. It is deliberately the
SMALLEST possible surface: a Protocol plus its result type.

**THE IMPORT-GRAPH CONSTRAINT (AC-15).**
``daemon/services/snapshot_metrics_service.py:31-34`` structurally
forbids the snapshot RANKING modules (``snapshot_search`` /
``snapshot_embedding``) from importing the metrics service — pinned by
``TestMonitoringOnlyPin`` (``tests/unit/tools/test_snapshot_v3.py:1559``),
so a violation breaks a unit test rather than merely a convention. The
overlay's counters must therefore NOT be registered from inside the
ranking module.

This module upholds that constraint structurally: it imports NOTHING
from the decision package — never ``metrics``, never ``overlay``, never
``registry``; every type it needs is stdlib. A ranking module may
import ``daemon.services.decision.port`` and stay clean of the metrics
registry; the overlay (which DOES register the families) is injected
into it by the wiring round.

The ranking module therefore calls a PASSED-IN port, never the
overlay's implementation.

**THE S1 ID HELPERS.** The snapshot service is DUCK-TYPED (its rows
expose ``.id``; test doubles expose nothing else), and
``Snapshot.to_dict()`` returns EVERY field (title, task_summary, domain
tags, the full digest) — a generic dump LEAKS. So the id extraction
below reads the id and NOTHING else, and the selection intersection
validates every id against the candidate set before anything is
written.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

__all__ = [
    "DecisionOverlayPort",
    "OverlayConsultResult",
    "extract_snapshot_ids",
    "intersect_selection",
]


@dataclass(frozen=True)
class OverlayConsultResult:
    """The result of one consult, as the OUTWARD seam sees it.

    Attributes:
        outcome: The effective outcome — the heuristic's, unless the
            overlay acted.
        state: The consult's terminal state (an ``OverlayOutcome``
            value: ``applied`` / ``logged`` / ``suppressed`` /
            ``failed`` / ``refused``).
        applied: True when the overlay's answer moved the outcome.
        failure_class: The failure label (a ``FailureClass`` value) or
            ``None``.
        suppression: The suppression reason (a ``SuppressionReason``
            value) or ``None``.
        verdict: The shadow A/B verdict.
        latency_s: The overlay call's wall clock.
        cost_usd: The billed cost of the attempt (0 when no call was
            made).
    """

    outcome: Any = None
    state: str = "suppressed"
    applied: bool = False
    failure_class: str | None = None
    suppression: str | None = None
    verdict: str = "no_overlay"
    latency_s: float = 0.0
    cost_usd: float = 0.0


@runtime_checkable
class DecisionOverlayPort(Protocol):
    """The narrow outward seam the snapshot side calls.

    One method: consult the overlay for one seam and get back the
    EFFECTIVE outcome. The implementation owns the gates, the call, the
    metrics, the shadow row and the cost accounting; the caller owns
    its own heuristic answer.

    The caller NEVER sees an exception: a consult always yields an
    :class:`OverlayConsultResult` whose ``outcome`` is the heuristic's
    unless the overlay acted (D2).
    """

    async def consult(
        self,
        seam_id: str,
        *,
        heuristic_outcome: Any,
        context: Mapping[str, Any],
    ) -> OverlayConsultResult:
        """Consult the overlay for ``seam_id``.

        Args:
            seam_id: The seam to consult.
            heuristic_outcome: The heuristic's answer — the permanent
                default and the fallback (D1).
            context: The seam's own context (the seam's
                ``build_input`` reads it).

        Returns:
            The effective outcome plus the consult's telemetry. Never
            raises.
        """
        ...

    def consult_detached(
        self,
        seam_id: str,
        *,
        heuristic_outcome: Any,
        context: Mapping[str, Any],
    ) -> None:
        """Dispatch the consult as a bounded BACKGROUND task — the (c)
        shadow entry. Returns immediately; the caller's path is
        untouched.

        The implementation owns the strong-reference set (a bare
        ``create_task`` is GC-eligible mid-flight), the per-task budget
        (the configured timeout), the containment (exceptions logged
        TYPE-ONLY) and the structural never-apply guarantee (shadow ⇒
        detached; deciding ⇒ the awaited :meth:`consult`). The caller
        owns the capture (ids + query) and nothing else.

        Never raises.
        """
        ...


# ── the S1 id helpers (duck-typed, never a generic dump) ────────────────


def extract_snapshot_ids(candidates: Sequence[Any]) -> tuple[str, ...]:
    """Extract the candidate ids — the id and NOTHING else.

    The snapshot service is duck-typed: a row exposes ``.id`` (the
    production shape) and a test double may expose nothing else. A
    generic dump (``to_dict`` / ``model_dump`` / ``vars``) would leak
    the title, the task summary, the domain tags and the full digest —
    so the extraction reads the id attribute only.

    Args:
        candidates: The candidate rows (or plain id strings).

    Returns:
        The ids, in order, without duplicates. An object without a
        usable id contributes nothing.
    """
    ids: list[str] = []
    seen: set[str] = set()
    for candidate in candidates or ():
        raw: Any = None
        if isinstance(candidate, str):
            raw = candidate
        else:
            raw = getattr(candidate, "id", None)
            if raw is None and isinstance(candidate, Mapping):
                raw = candidate.get("id")
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ids.append(value)
    return tuple(ids)


def intersect_selection(
    parsed_ids: Sequence[str], candidate_ids: Sequence[str]
) -> tuple[str, ...]:
    """Keep only the ids that ARE candidates — the validated selection.

    ``_parse_selection`` accepts ANY non-empty string, so a parsed
    selection is NOT a set of candidate ids. Every id is validated
    against the candidate set BEFORE it reaches a row: an unknown id is
    DROPPED, never written.

    Args:
        parsed_ids: The parsed selection (untrusted).
        candidate_ids: The validated candidate ids.

    Returns:
        The validated ids, in the parsed order, without duplicates.
    """
    allowed = set(candidate_ids or ())
    validated: list[str] = []
    seen: set[str] = set()
    for raw in parsed_ids or ():
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if not value or value not in allowed or value in seen:
            continue
        seen.add(value)
        validated.append(value)
    return tuple(validated)
