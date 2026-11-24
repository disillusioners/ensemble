"""POK librarian — per-entry metrics, the soft/smart max and the ruled-bases
consolidator (pok-write-gate Wave 2, Phase 10).

The librarian is the **maintenance half** of the write gate. The gate
(``pok_gate.py``) judges ONE candidate write; the librarian reads the
WHOLE store's metrics and maintains it. Emperor ruling (addendum-3
item 7, design-amendments-v2 §9)::

    "POK entry have metrics, so the POK librarian can access for it
     management job... keep a reasonable max (soft/smart max) per
     project. Hard cap is 1024."

Binding picks (design-amendments-v2 §13.2):

* **I1** — the soft max lives in ``project_metadata_records`` under the
  key ``pok.soft_max.v1`` (the table EXISTS ⇒ **ZERO migration**). The
  value is JSON ``{value, inputs, computed_at, drift}``. It is read at
  write time by ONE unique-index SELECT (sub-ms) and NEVER memoised
  in-process.
* **H3** — the periodic pass is ONE global MaintenanceService job
  (UN_GATED, in ``JOB_MAINTENANCE_SECTIONS``) that loops the project
  ids internally; the on-write bound-check is SUBSUMED (the upsert
  already loads ``active_count`` for the 1024 hard cap).
* **J** — the trusted-vs-ignored metric set, CONFIRMED §9 + 3
  amendments (ledger retrieval counts supersede ``access_count``;
  timestamps corroborate, never trigger; per-row vs per-store metrics
  are labelled separately).

What lives here
---------------

* :class:`EntryMetrics` / :class:`StoreMetrics` — the per-entry and
  per-store metric readers (T10.1);
* :func:`derive_soft_max` — the metrics-derived ceiling
  ``derived = max(floor, f(utilization))`` (T10.2) with the
  **cold-start floor** (the floor sits at/above the current
  active-count band, so the FIRST pass never tightens against the rows
  that already exist);
* :func:`read_soft_max` / :func:`write_soft_max` — the I1 placement
  (T10.3);
* :func:`run_librarian_pass` — the H3 pass (T10.4/T10.5/T10.9/
  T10.10): recompute + drift report + the expired-ASK safety net;
* :func:`tightening_decision` — the near-soft-max admission
  tightening (T10.6);
* :func:`classify_metrics` / :func:`consolidation_basis_for` — the J
  metric set (T10.8) and the ruled-bases consolidator (T10.7);
* :func:`select_merge_target` — the J partition wiring (T10.11);
* :func:`detect_r_ret_candidates` — the R-RET detection leg (T10.12);
* :func:`emit_librarian_board_notice` — the Phase 12 board sink (ONE
  bounded notice per pass that HAS proposals; fail-soft).

DOCTRINE (non-negotiable, design-amendments-v2 §9):

* consolidation happens ONLY under a RULED BASIS — ``covered-by-
  surviving-row`` · ``relocated-to-routed-home`` · ``NOW-FALSE``;
  NEVER on age, NEVER on ``access_count`` alone;
* a RAG twin is NEVER redundancy (a fact that also lives in the
  knowledge store still belongs in POK);
* the librarian NEVER writes a knowledge row and NEVER archives one.
  It DETECTS and PROPOSES; the curator's ``pok_curate`` (which
  demands a basis) archives. The pass summary does NOT live in the
  admission ledger (I1's prohibition) — it rides the maintenance
  audit row + the log.

Layer discipline: this module reads the POK repository + the project
metadata table and WRITES ONLY the soft-max metadata key (the
daemon-internal writer the I1 pick sanctions — the authority split
governs AGENT-FACING tool calls, not this) plus, when a pass HAS
consolidation proposals, ONE bounded board NOTICE (the Phase 12 board
sink — fail-soft; the board item's per-proposal lines and the
maintenance-run summary carry the pass detail — the functional gate's
I2 fix).
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from daemon.repositories.pok.models import (
    POK_ARCHIVE_BASES,
    POKWriteGateLedger,
    pok_family_key,
)
from daemon.repositories.pok.repository import POKRepository
from daemon.repositories.project.repository import SQLModelProjectRepository

from .pok_service import POK_ACTIVE_CAP_PER_PROJECT

logger = logging.getLogger(__name__)

__all__ = [
    "CONSOLIDATION_BASES",
    "POK_LIBRARIAN_INTERVAL_HOURS",
    "POK_LIBRARIAN_SECTION",
    "POKLibrarianJob",
    "EntryMetrics",
    "LibrarianPassReport",
    "RRetCandidate",
    "SOFT_MAX_METADATA_KEY",
    "SoftMaxState",
    "StoreMetrics",
    "TighteningDecision",
    "classify_metrics",
    "compute_drift",
    "consolidation_basis_for",
    "derive_soft_max",
    "detect_r_ret_candidates",
    "read_soft_max",
    "read_store_metrics",
    "run_librarian_pass",
    "select_merge_target",
    "surface_expired_asks",
    "tightening_decision",
    "write_soft_max",
]


# ═══════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════

#: The I1 metadata key the soft max lives under (project metadata).
SOFT_MAX_METADATA_KEY = "pok.soft_max.v1"

#: The three RULED archive bases the consolidator may propose under
#: (the ``gate-rejection`` basis is a TOOL-side basis, not a
#: consolidation ground, so it is NOT a consolidation candidate).
CONSOLIDATION_BASES: tuple[str, ...] = tuple(
    b for b in POK_ARCHIVE_BASES if b != "gate-rejection"
)

#: The band size the cold-start floor snaps to. The floor is NOT a
#: hand-set constant: it is DERIVED from the measured active count
#: (the smallest band boundary at/above it), so the ceiling always
#: sits above the rows that already exist.
SOFT_MAX_BAND_SIZE = 128

#: The utilization the derived capacity aims for: the working set
#: (the rows that are actually retrieved) should occupy this share of
#: the store. The derived CEILING is ``working_set / target`` — a
#: measurement-driven value, not a constant.
TARGET_UTILIZATION = 0.5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════════════
# The metric readers (T10.1)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class EntryMetrics:
    """The PER-ROW metrics the librarian reads (T10.1).

    Every field is either a TRUSTED metric (gate-stamped verify count,
    curator tiers, the post-fix family, verdict-derived counts) or a
    legacy/corroborating one (``access_count`` — the J amendment 1
    "legacy corroboration"). The split is the point: a consumer must
    never read ``access_count`` as if it were a use signal.
    """

    id: str
    slug: str
    status: str = "active"
    domain: str = ""
    audience: str = ""
    source_tier: str = "agent"
    pinned: bool = False
    disputed: bool = False
    family: str | None = None
    created_by_agent: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    last_verified: str | None = None
    verify_count: int = 0
    access_count: int = 0
    content_sha256: str = ""
    #: Distinct ledger RETRIEVAL rows for this entry (J-trusted; the
    #: supersede of ``access_count`` — amendment 1).
    ledger_retrievals: int = 0
    #: The subset of the above that came from an INJECTION.
    ledger_injections: int = 0
    #: The subset that came from a QUERY.
    ledger_queries: int = 0
    #: Prior gate MERGE verdicts naming this slug (verdict-derived).
    merge_verdicts: int = 0

    @property
    def is_curated(self) -> bool:
        """Curator tier (the trust signal — NOT the inert ``tier=0``)."""
        return self.source_tier == "curated"

    @property
    def never_retrieved(self) -> bool:
        """Never retrieved by ANY ledger signal and never read.

        Both legs are required: the ledger is the J-trusted surface,
        ``access_count`` the legacy corroboration. A row the ledger
        has never seen AND that was never read is the R-RET
        "never-retrieved" population.
        """
        return self.ledger_retrievals == 0 and self.access_count == 0

    @property
    def no_family(self) -> bool:
        """No family key (the strict post-fix family computation)."""
        return self.family is None


@dataclass(frozen=True)
class StoreMetrics:
    """The PER-STORE metrics (T10.1) — labelled separately from the
    per-row ones (J amendment 3).

    Per-store metrics are pass TELEMETRY (how the store behaves as a
    whole); per-row metrics drive ROW decisions. The two are never
    mixed: a store-wide refusal rate never condemns a row, and a
    single row's verify count never moves the store's ceiling.
    """

    project_id: str
    active_count: int = 0
    entries: tuple[EntryMetrics, ...] = ()
    by_family: Mapping[str, int] = field(default_factory=dict)
    by_author: Mapping[str, int] = field(default_factory=dict)
    by_domain: Mapping[str, int] = field(default_factory=dict)
    #: Ledger retrieval totals (per-store telemetry).
    ledger_retrievals: int = 0
    ledger_injections: int = 0
    ledger_queries: int = 0
    #: Verdict-derived counts (per-store telemetry).
    verdicts: Mapping[str, int] = field(default_factory=dict)
    #: The number of ACTIVE rows that were never retrieved.
    never_retrieved: int = 0
    #: The number of ACTIVE rows with a zero verify count (the cold
    #: population — ``verify_count`` is structurally 0 before the gate
    #: starts stamping).
    zero_verify: int = 0
    #: The number of ACTIVE rows with no family key.
    no_family: int = 0
    measured_at: str = ""

    def entry(self, slug: str) -> EntryMetrics | None:
        for e in self.entries:
            if e.slug == slug:
                return e
        return None

    @property
    def working_set(self) -> int:
        """The rows that are ACTUALLY USED — the utilization basis.

        The ledger's distinct retrieved rows (J-trusted) are the
        primary leg; ``access_count`` is the legacy corroboration
        (amendment 1). The union is the working set.
        """
        ledger_used = sum(1 for e in self.entries if e.ledger_retrievals > 0)
        access_used = sum(1 for e in self.entries if e.access_count > 0)
        return max(ledger_used, access_used)


def _family_of(slug: str) -> str | None:
    """The strict, post-fix family key of a slug (never a prefix)."""
    try:
        return pok_family_key(slug)
    except Exception:  # noqa: BLE001 — defensive (a malformed slug)
        return None


def read_store_metrics(
    repo: POKRepository,
    project_id: str,
    *,
    now: str | None = None,
) -> StoreMetrics:
    """Read the per-entry + per-store metrics for one project (T10.1).

    READ-ONLY. The ledger is read for the retrieval counts (the J
    supersede of ``access_count``) and for the verdict-derived
    counts; the knowledge rows are read for the tier / family /
    verify / access legs.
    """
    rows = repo.list_entries(project_id, statuses=("active",))
    ledger = repo.query_ledger(project_id, limit=None)

    retrievals: dict[str, dict[str, int]] = {}
    verdicts: dict[str, int] = {}
    merge_verdicts: dict[str, int] = {}
    for row in ledger:
        phase = (row.phase or "").upper()
        if phase == "RETRIEVAL" and row.entry_id:
            bucket = retrievals.setdefault(row.entry_id, {"all": 0, "injection": 0, "query": 0})
            bucket["all"] += 1
            source = (row.source or "").lower()
            if source == "injection":
                bucket["injection"] += 1
            elif source == "query":
                bucket["query"] += 1
        elif row.verdict:
            verdicts[row.verdict] = verdicts.get(row.verdict, 0) + 1
            if row.verdict == "MERGE" and row.target_slug:
                merge_verdicts[row.target_slug] = (
                    merge_verdicts.get(row.target_slug, 0) + 1
                )

    entries: list[EntryMetrics] = []
    by_family: dict[str, int] = {}
    by_author: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    for row in rows:
        bucket = retrievals.get(row.id, {"all": 0, "injection": 0, "query": 0})
        entry = EntryMetrics(
            id=row.id,
            slug=row.slug,
            status=row.status,
            domain=row.domain,
            audience=row.audience,
            source_tier=row.source_tier,
            pinned=bool(row.pinned),
            disputed=bool(row.disputed),
            family=_family_of(row.slug),
            created_by_agent=row.created_by_agent or "",
            created_at=row.created_at,
            updated_at=row.updated_at,
            last_verified=row.last_verified,
            verify_count=int(row.verify_count or 0),
            access_count=int(row.access_count or 0),
            content_sha256=row.content_sha256 or "",
            ledger_retrievals=bucket["all"],
            ledger_injections=bucket["injection"],
            ledger_queries=bucket["query"],
            merge_verdicts=merge_verdicts.get(row.slug, 0),
        )
        entries.append(entry)
        if entry.family:
            by_family[entry.family] = by_family.get(entry.family, 0) + 1
        if entry.created_by_agent:
            by_author[entry.created_by_agent] = by_author.get(entry.created_by_agent, 0) + 1
        if entry.domain:
            by_domain[entry.domain] = by_domain.get(entry.domain, 0) + 1

    ledger_injections = sum(e.ledger_injections for e in entries)
    ledger_queries = sum(e.ledger_queries for e in entries)
    return StoreMetrics(
        project_id=project_id,
        active_count=len(entries),
        entries=tuple(entries),
        by_family=by_family,
        by_author=by_author,
        by_domain=by_domain,
        ledger_retrievals=sum(e.ledger_retrievals for e in entries),
        ledger_injections=ledger_injections,
        ledger_queries=ledger_queries,
        verdicts=verdicts,
        never_retrieved=sum(1 for e in entries if e.never_retrieved),
        zero_verify=sum(1 for e in entries if e.verify_count == 0),
        no_family=sum(1 for e in entries if e.no_family),
        measured_at=now or _now_iso(),
    )


# ═══════════════════════════════════════════════════════════════════════════
# The soft/smart max derivation (T10.2) + placement (T10.3)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SoftMaxState:
    """The I1 payload — JSON ``{value, inputs, computed_at, drift}``."""

    value: int
    inputs: Mapping[str, Any] = field(default_factory=dict)
    computed_at: str = ""
    drift: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "value": int(self.value),
            "inputs": dict(self.inputs),
            "computed_at": self.computed_at,
            "drift": dict(self.drift),
        }

    @classmethod
    def from_payload(cls, payload: Any) -> "SoftMaxState | None":
        if not isinstance(payload, Mapping):
            return None
        value = payload.get("value")
        if not isinstance(value, int):
            return None
        inputs = payload.get("inputs") if isinstance(payload.get("inputs"), Mapping) else {}
        drift = payload.get("drift") if isinstance(payload.get("drift"), Mapping) else {}
        return cls(
            value=value,
            inputs=dict(inputs),
            computed_at=str(payload.get("computed_at") or ""),
            drift=dict(drift),
        )


def band_floor(active_count: int, *, band: int = SOFT_MAX_BAND_SIZE) -> int:
    """The cold-start FLOOR — the band boundary at/above the active count.

    The floor is DERIVED from the measurement (the current active
    count), NOT a hand-set constant: the smallest multiple of
    ``band`` that is at or above ``active_count``. With 499 active
    rows the floor is 512 ⇒ the first pass CANNOT tighten against the
    rows that already exist (the "no tightening when
    ``active_count < floor``" rule — the highest-value unit test in
    the derivation task).
    """
    if active_count <= 0:
        return band
    return int(math.ceil(active_count / band) * band)


def derive_soft_max(
    *,
    active_count: int,
    working_set: int = 0,
    floor: int | None = None,
    target_utilization: float = TARGET_UTILIZATION,
    hard_cap: int = POK_ACTIVE_CAP_PER_PROJECT,
) -> tuple[int, dict[str, Any]]:
    """Derive the per-project soft max — ``derived = max(floor, f(util))``.

    ``f(utilization)`` = ``working_set / target_utilization`` — the
    capacity the store needs to keep the USED rows at the target
    utilization share. It is a function of the MEASURED working set,
    never a hand-set constant: as real gate activity lands (the
    ledger's retrieval counts fill in), the working set grows and the
    ceiling LIFTS with it.

    The floor is the cold-start guard: ``max(band_floor(active_count),
    explicit_floor)``. At cold start the working set is 0 (the ledger
    is empty), so the derived value is the floor and tightening stays
    DORMANT.

    Returns ``(value, inputs)`` — the inputs are the provenance of the
    number (they ride the stored payload).
    """
    target = target_utilization if target_utilization > 0 else TARGET_UTILIZATION
    derived_floor = band_floor(active_count)
    effective_floor = derived_floor
    if floor is not None and int(floor) > effective_floor:
        effective_floor = int(floor)
    capacity = int(math.ceil(working_set / target)) if working_set > 0 else 0
    value = max(effective_floor, capacity)
    value = min(value, int(hard_cap))
    inputs = {
        "active_count": int(active_count),
        "working_set": int(working_set),
        "target_utilization": target,
        "band_floor": derived_floor,
        "floor": effective_floor,
        "capacity_from_utilization": capacity,
        "hard_cap": int(hard_cap),
        "utilization_source": "live-measurement" if working_set > 0 else "cold-start",
    }
    return value, inputs


def compute_drift(
    previous: SoftMaxState | None, value: int, inputs: Mapping[str, Any]
) -> dict[str, Any]:
    """The drift report (T10.5) — what moved, and WHY.

    Drift is REPORTED, never silently applied: the payload keeps the
    previous value, the delta and the reason the number moved.
    """
    if previous is None:
        return {
            "previous_value": None,
            "delta": None,
            "direction": "initial",
            "reason": "first pass — no previous value",
            "changed": True,
        }
    delta = int(value) - int(previous.value)
    return {
        "previous_value": int(previous.value),
        "delta": delta,
        "direction": "up" if delta > 0 else ("down" if delta < 0 else "flat"),
        "reason": (
            "utilization-derived capacity moved the ceiling"
            if delta
            else "no movement"
        ),
        "changed": bool(delta),
        "previous_computed_at": previous.computed_at,
        "previous_inputs": dict(previous.inputs),
        "current_inputs": dict(inputs),
    }


def read_soft_max(engine: Any, project_id: str) -> SoftMaxState | None:
    """Read the I1 soft-max value (ONE unique-index SELECT, no memo).

    ``None`` when the key was never written (cold start). The read is
    deliberately NOT memoised in-process: the value is written by the
    pass job and read at write time — a memo would serve a stale
    ceiling to the admission path.
    """
    try:
        payload = SQLModelProjectRepository(engine).get_metadata(
            project_id, SOFT_MAX_METADATA_KEY
        )
    except Exception as exc:  # noqa: BLE001 — a read failure is not fatal
        logger.warning(
            "[POK:Librarian] soft-max read failed for project %s (%s) — "
            "the admission path treats the ceiling as unset",
            project_id,
            type(exc).__name__,
        )
        return None
    return SoftMaxState.from_payload(payload)


def write_soft_max(engine: Any, project_id: str, state: SoftMaxState) -> bool:
    """Write the I1 soft-max payload (the daemon-internal writer).

    The authority split governs AGENT-FACING tool calls; this writer
    is daemon-internal (the pass job) and writes ONLY the soft-max
    key — never a knowledge row, never the admission ledger (I1's
    prohibition: pass summaries do NOT live in the ledger).
    """
    try:
        SQLModelProjectRepository(engine).set_metadata(
            project_id, SOFT_MAX_METADATA_KEY, state.to_payload()
        )
        return True
    except Exception as exc:  # noqa: BLE001 — the pass must not die on it
        logger.warning(
            "[POK:Librarian] soft-max write failed for project %s (%s) — "
            "the ceiling stays at its previous value",
            project_id,
            type(exc).__name__,
        )
        return False


# ═══════════════════════════════════════════════════════════════════════════
# The near-soft-max tightening (T10.6)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TighteningDecision:
    """The admission-side tightening decision (T10.6).

    Near the soft max admission TIGHTENS: more ASK rounds (a new row
    is asked about rather than accepted) and MERGE is preferred over a
    NEW row. The decision is a VALUE the admission path reads — it
    never decides an outcome by itself (the gate still judges).
    """

    tight: bool = False
    reason: str = "cold-start — the soft max is unset or the store is below the floor"
    soft_max: int | None = None
    active_count: int = 0
    headroom: int | None = None

    @property
    def prefer_merge(self) -> bool:
        """Prefer a MERGE over a NEW row while tightening."""
        return self.tight

    @property
    def extra_ask_rounds(self) -> bool:
        """More ASK rounds while tightening."""
        return self.tight

    def to_payload(self) -> dict[str, Any]:
        return {
            "tight": self.tight,
            "reason": self.reason,
            "soft_max": self.soft_max,
            "active_count": self.active_count,
            "headroom": self.headroom,
        }


def tightening_decision(
    *,
    active_count: int,
    soft_max: int | None,
) -> TighteningDecision:
    """Decide whether admission TIGHTENS (T10.6).

    The tightening engages when the store has reached its soft max
    (``active_count >= soft_max``). With NO soft max (cold start) or
    a store below the floor, tightening stays DORMANT — the first
    pass never tightens against the rows that already exist.
    """
    if soft_max is None:
        return TighteningDecision(
            tight=False,
            reason="cold-start — no soft max has been computed yet",
            active_count=active_count,
        )
    if active_count < soft_max:
        return TighteningDecision(
            tight=False,
            reason=(
                "the store is below the soft max (%d active < %d) — "
                "admission is un-tightened" % (active_count, soft_max)
            ),
            soft_max=soft_max,
            active_count=active_count,
            headroom=soft_max - active_count,
        )
    return TighteningDecision(
        tight=True,
        reason=(
            "the store has reached its soft max (%d active >= %d) — "
            "admission tightens: more ASK rounds, MERGE preferred over "
            "a new row" % (active_count, soft_max)
        ),
        soft_max=soft_max,
        active_count=active_count,
        headroom=soft_max - active_count,
    )


# ═══════════════════════════════════════════════════════════════════════════
# The J metric set (T10.8) + the ruled-bases consolidator (T10.7)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class MetricSignal:
    """One metric signal about a row, with its TRUST classification."""

    kind: str
    detail: str
    trusted: bool = True
    #: A corroborating-only signal NEVER triggers a consolidation on
    #: its own (J amendment 2 — timestamps corroborate, never trigger).
    corroborates_only: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "detail": self.detail,
            "trusted": self.trusted,
            "corroborates_only": self.corroborates_only,
        }


#: The TRUSTED metric kinds (J CONFIRMED set). Anything outside this
#: set is either IGNORED or corroborating-only.
TRUSTED_METRIC_KINDS: tuple[str, ...] = (
    "verify_count",  # gate-stamped verification
    "curator_tier",  # pinned / disputed / curated
    "family",  # post-fix strict family
    "verdict",  # verdict-derived counts
    "ledger_retrieval",  # the ledger supersedes access_count
)

#: The IGNORED metric kinds — they NEVER support a consolidation.
IGNORED_METRIC_KINDS: tuple[str, ...] = (
    "access_count",  # retrieval-driven, not use-driven — ALONE never
    "age",  # never a consolidation basis
    "embeddings",  # the vector leg is dead
    "pre_stamp_verify",  # structurally 0 — absence is not evidence
    "tier_zero",  # inert today
)

#: The corroborating-only kinds (J amendment 2).
CORROBORATING_ONLY_KINDS: tuple[str, ...] = ("timestamp",)


def classify_metrics(entry: EntryMetrics) -> list[MetricSignal]:
    """Classify one row's metrics into the J signal set (T10.8).

    The classification is the ENFORCEMENT of the J ruling: a signal
    outside the TRUSTED set is either corroborating-only (it may
    support a decision that another signal already carries) or
    IGNORED (it may never support one at all).
    """
    signals: list[MetricSignal] = []
    if entry.verify_count > 0:
        signals.append(
            MetricSignal(
                kind="verify_count",
                detail="the gate stamped %d verification(s)" % entry.verify_count,
                trusted=True,
            )
        )
    else:
        signals.append(
            MetricSignal(
                kind="pre_stamp_verify",
                detail="verify_count is 0 — the absence is NOT evidence",
                trusted=False,
                corroborates_only=True,
            )
        )
    if entry.pinned or entry.disputed or entry.is_curated:
        signals.append(
            MetricSignal(
                kind="curator_tier",
                detail=(
                    "curator tier: pinned=%s disputed=%s curated=%s"
                    % (entry.pinned, entry.disputed, entry.is_curated)
                ),
                trusted=True,
            )
        )
    if entry.family:
        signals.append(
            MetricSignal(
                kind="family",
                detail="family %s" % entry.family,
                trusted=True,
            )
        )
    if entry.merge_verdicts:
        signals.append(
            MetricSignal(
                kind="verdict",
                detail="%d prior MERGE verdict(s) name this slug" % entry.merge_verdicts,
                trusted=True,
            )
        )
    if entry.ledger_retrievals:
        signals.append(
            MetricSignal(
                kind="ledger_retrieval",
                detail=(
                    "%d ledger retrieval(s) (%d injection / %d query)"
                    % (
                        entry.ledger_retrievals,
                        entry.ledger_injections,
                        entry.ledger_queries,
                    )
                ),
                trusted=True,
            )
        )
    if entry.access_count:
        signals.append(
            MetricSignal(
                kind="access_count",
                detail="%d legacy access(es)" % entry.access_count,
                trusted=False,
                corroborates_only=True,
            )
        )
    if entry.last_verified or entry.updated_at:
        signals.append(
            MetricSignal(
                kind="timestamp",
                detail="last_verified=%s updated_at=%s" % (entry.last_verified, entry.updated_at),
                trusted=True,
                corroborates_only=True,
            )
        )
    if entry.never_retrieved:
        signals.append(
            MetricSignal(
                kind="never_retrieved",
                detail="never retrieved by any signal",
                trusted=True,
            )
        )
    return signals


def metric_triggers_consolidation(signals: Sequence[MetricSignal]) -> bool:
    """MAY these signals alone support a consolidation proposal?

    The mutation leg this pins: ``access_count`` ALONE ⇒ NEVER. The
    answer is True only when a TRUSTED, non-corroborating signal is
    present — every other combination is False (the ignored /
    corroborating-only kinds can ride ALONG, never carry).
    """
    return any(
        s.trusted and not s.corroborates_only and s.kind in TRUSTED_METRIC_KINDS
        for s in signals
    )


def consolidation_basis_for(
    entry: EntryMetrics,
    signals: Sequence[MetricSignal],
    *,
    survivors: Sequence[EntryMetrics] = (),
    boundary_elsewhere: bool = False,
) -> str | None:
    """The RULED BASIS a consolidation proposal may cite (T10.7).

    Returns one of :data:`CONSOLIDATION_BASES` or ``None`` (no
    proposal). The rules, in order:

    * ``covered-by-surviving-row`` — a surviving ACTIVE row already
      carries the fact (the same ``content_sha256``, or a prior MERGE
      verdict names this slug as the merged-away twin);
    * ``NOW-FALSE`` — a curator DISPUTED the row (the curator's
      ruling is the signal; the timestamp only corroborates);
    * ``relocated-to-routed-home`` — the row's class belongs
      elsewhere (the boundary leg) AND it was never retrieved — the
      surface for a curator to re-route it.

    NEVER age. NEVER ``access_count`` alone. A row with NO trusted
    signal yields NO basis.
    """
    if not metric_triggers_consolidation(signals):
        return None

    # covered-by-surviving-row — the exact-content twin.
    for survivor in survivors:
        if survivor.slug == entry.slug:
            continue
        if entry.content_sha256 and survivor.content_sha256 == entry.content_sha256:
            return "covered-by-surviving-row"
    # covered-by-surviving-row — a prior MERGE verdict names this slug.
    if entry.merge_verdicts:
        return "covered-by-surviving-row"

    # NOW-FALSE — a curator disputed the row.
    if entry.disputed:
        return "NOW-FALSE"

    # relocated-to-routed-home — the class belongs elsewhere and the
    # row was never retrieved.
    if boundary_elsewhere and entry.never_retrieved:
        return "relocated-to-routed-home"

    return None


@dataclass(frozen=True)
class ConsolidationProposal:
    """One consolidation PROPOSAL (never an archive — the curator
    archives, with the basis, through ``pok_curate``)."""

    slug: str
    basis: str
    evidence: tuple[str, ...] = ()
    target_slug: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "basis": self.basis,
            "evidence": list(self.evidence),
            "target_slug": self.target_slug,
        }


def propose_consolidations(
    store: StoreMetrics,
    *,
    boundary_elsewhere: Mapping[str, bool] | None = None,
) -> list[ConsolidationProposal]:
    """Propose the consolidations a ruled basis supports (T10.7/T10.9).

    The proposal list is a DETECTION surface: it names the basis and
    the evidence. NOTHING is archived here — the curator's
    ``pok_curate`` demands a basis and holds the authority.
    """
    boundary_elsewhere = boundary_elsewhere or {}
    by_sha: dict[str, list[EntryMetrics]] = {}
    for entry in store.entries:
        if entry.content_sha256:
            by_sha.setdefault(entry.content_sha256, []).append(entry)

    proposals: list[ConsolidationProposal] = []
    for entry in store.entries:
        signals = classify_metrics(entry)
        group = by_sha.get(entry.content_sha256, [])
        survivors = [s for s in group if s.slug != entry.slug]
        # The J-PARTITION TARGET (the functional-gate I3 fix): ONE
        # deterministic merge target per duplicate group — the J
        # selector's pick over the WHOLE group (verify_count, the
        # curator tier, the family match, recency; ``access_count``
        # corroborates only) — never the arbitrary ``survivors[0]``
        # and never a MUTUAL pair (two rows each naming the other as
        # the merge target, the contradiction that corrupts the store
        # the moment consolidation is armed).
        j_target = (
            select_merge_target(group, family=entry.family)
            if len(group) > 1
            else None
        )
        basis = consolidation_basis_for(
            entry,
            signals,
            survivors=survivors,
            boundary_elsewhere=bool(boundary_elsewhere.get(entry.slug)),
        )
        if basis is None:
            continue
        if (
            basis == "covered-by-surviving-row"
            and j_target is not None
            and j_target.slug == entry.slug
        ):
            # THIS row is the group's J-selected survivor — the others
            # merge into IT. Proposing it away under the same basis
            # would contradict them (the mutual-pair defect shape).
            continue
        if j_target is not None and j_target.slug != entry.slug:
            target_slug = j_target.slug
        elif survivors:
            picked = select_merge_target(survivors, family=entry.family)
            target_slug = picked.slug if picked is not None else None
        else:
            target_slug = None
        proposals.append(
            ConsolidationProposal(
                slug=entry.slug,
                basis=basis,
                evidence=tuple(s.detail for s in signals if s.trusted),
                target_slug=target_slug,
            )
        )
    return proposals


# ═══════════════════════════════════════════════════════════════════════════
# The J partition wiring (T10.11) + R-RET detection (T10.12)
# ═══════════════════════════════════════════════════════════════════════════


def select_merge_target(
    candidates: Sequence[EntryMetrics],
    *,
    family: str | None = None,
    now: str | None = None,
) -> EntryMetrics | None:
    """Pick the MERGE TARGET among the candidates (T10.11).

    The J partition: the target is chosen by ``verify_count`` + the
    curator tier + a family match + recency, with high access as a
    CORROBORATING signal only. ``access_count`` alone never decides
    the target — it only breaks a tie between rows that already tie
    on the trusted signals.
    """
    if not candidates:
        return None

    def _sort_key(entry: EntryMetrics) -> tuple:
        tier = 1 if (entry.pinned or entry.is_curated) else 0
        family_match = 1 if (family is not None and entry.family == family) else 0
        recency = entry.last_verified or entry.updated_at or ""
        return (
            entry.verify_count,  # trusted: gate-stamped verification
            tier,  # trusted: curator tier
            family_match,  # trusted: the family match
            recency,  # corroborating: recency (timestamps never trigger alone)
            entry.access_count,  # corroborating: legacy access (tie-break only)
            entry.slug,
        )

    return max(candidates, key=_sort_key)


@dataclass(frozen=True)
class RRetCandidate:
    """One R-RET detection candidate (T10.12).

    R-RET is a DETECTION signal into the ruled-bases consolidator —
    NEVER a standalone archive basis.
    """

    slug: str
    kind: str  # "merge" | "reslug"
    target_slug: str | None = None
    basis: str | None = None
    evidence: tuple[str, ...] = ()


def detect_r_ret_candidates(store: StoreMetrics) -> list[RRetCandidate]:
    """The R-RET detection leg (T10.12).

    Two populations:

    * a MIS-SLUGGED DUPLICATE — a well-slugged active row already
      carries the fact (the same content). The proposal is a MERGE
      (a count-preserving supersede) and the archive of the
      mis-slugged duplicate is a ``covered-by-surviving-row``
      consolidation — never R-RET on its own;
    * a BAD SLUG on UNIQUE content — the content is not duplicated,
      so the proposal is a RE-SLUG (a rename), never an archive.
    """
    candidates: list[RRetCandidate] = []
    by_sha: dict[str, list[EntryMetrics]] = {}
    for entry in store.entries:
        if entry.content_sha256:
            by_sha.setdefault(entry.content_sha256, []).append(entry)

    for sha, group in by_sha.items():
        if len(group) < 2:
            continue
        well_slugged = [e for e in group if not e.no_family]
        mis_slugged = [e for e in group if e.no_family]
        if not well_slugged or not mis_slugged:
            continue
        target = select_merge_target(well_slugged)
        if target is None:
            continue
        for entry in mis_slugged:
            candidates.append(
                RRetCandidate(
                    slug=entry.slug,
                    kind="merge",
                    target_slug=target.slug,
                    basis="covered-by-surviving-row",
                    evidence=(
                        "the same content is carried by the well-slugged "
                        "active row %s" % target.slug,
                        "R-RET detection — never a standalone archive basis",
                    ),
                )
            )

    for entry in store.entries:
        if not entry.no_family:
            continue
        if any(c.slug == entry.slug for c in candidates):
            continue
        candidates.append(
            RRetCandidate(
                slug=entry.slug,
                kind="reslug",
                target_slug=None,
                basis=None,
                evidence=(
                    "the slug carries no family key (NOFAM) — a re-slug "
                    "proposal, never an archive",
                    "the content is unique — nothing covers it",
                ),
            )
        )
    return candidates


# ═══════════════════════════════════════════════════════════════════════════
# The expired-ASK safety net (T10.10)
# ═══════════════════════════════════════════════════════════════════════════


def surface_expired_asks(
    repo: POKRepository,
    project_id: str,
) -> list[POKWriteGateLedger]:
    """Surface the expired / never-answered gates of the ASK chain (T10.10).

    The ASK chain is three rows keyed by ``gate_id`` + ``phase``
    (``ASK_MINTED`` → ``ASK_ANSWERED`` → ``ASK_EXECUTED``). A gate
    whose ``ASK_MINTED`` row has NO terminal row was never answered —
    the caller never came back. Fail-closed on an unanswered ASK means
    the lesson is never written, so the librarian's periodic pass is
    the safety net: it appends the ``ASK_EXPIRED`` audit row so the
    gap is VISIBLE (and the fact can be re-offered).

    Returns the rows that were appended (the NEW ones).
    """
    terminal_phases = {"ASK_ANSWERED", "ASK_EXECUTED", "ASK_EXPIRED"}
    rows = repo.query_ledger(project_id, limit=None)
    minted: dict[str, POKWriteGateLedger] = {}
    terminal: set[str] = set()
    for row in rows:
        phase = (row.phase or "").upper()
        gate_id = row.gate_id or ""
        if phase == "ASK_MINTED" and gate_id:
            minted.setdefault(gate_id, row)
        elif phase in terminal_phases and gate_id:
            terminal.add(gate_id)

    appended: list[POKWriteGateLedger] = []
    for gate_id, minted_row in minted.items():
        if gate_id in terminal:
            continue
        try:
            appended.append(
                repo.append_ledger(
                    POKWriteGateLedger(
                        project_id=project_id,
                        gate_id=gate_id,
                        phase="ASK_EXPIRED",
                        candidate_slug=minted_row.candidate_slug,
                        verdict="ASK",
                        reason=minted_row.reason,
                        judge_explanation=(
                            "the confirmation was never answered — the "
                            "librarian pass surfaced the expired gate "
                            "(fail-closed: the fact was not written)"
                        ),
                        executed=False,
                        agent_id=minted_row.agent_id,
                    )
                )
            )
        except Exception as exc:  # noqa: BLE001 — never break the pass
            logger.warning(
                "[POK:Librarian] ASK_EXPIRED append failed for gate %s "
                "(%s) — the pass continues",
                gate_id,
                type(exc).__name__,
            )
    return appended


# ═══════════════════════════════════════════════════════════════════════════
# The H3 pass (T10.4/T10.5/T10.9)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class LibrarianPassReport:
    """What one project pass did (the pass's own return value)."""

    project_id: str
    soft_max: int = 0
    previous_soft_max: int | None = None
    drift: Mapping[str, Any] = field(default_factory=dict)
    tightening: Mapping[str, Any] = field(default_factory=dict)
    active_count: int = 0
    working_set: int = 0
    expired_asks: int = 0
    proposals: tuple[Mapping[str, Any], ...] = ()
    r_ret: tuple[Mapping[str, Any], ...] = ()
    error: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "soft_max": self.soft_max,
            "previous_soft_max": self.previous_soft_max,
            "drift": dict(self.drift),
            "tightening": dict(self.tightening),
            "active_count": self.active_count,
            "working_set": self.working_set,
            "expired_asks": self.expired_asks,
            "proposals": [dict(p) for p in self.proposals],
            "r_ret": [dict(r) for r in self.r_ret],
            "error": self.error,
        }


# ═══════════════════════════════════════════════════════════════════════════
# The BOARD SINK (pok-write-gate Phase 12) — ONE bounded notice per pass
# ═══════════════════════════════════════════════════════════════════════════

#: The board-author stamp for the librarian's pass notice (the
#: ``daemon:<lane>`` precedent — daemon/repositories/source/
#: maintenance_seed.py:146).
POK_LIBRARIAN_BOARD_AUTHOR = "daemon:pok-librarian"

#: The board item's title — bounded, NEVER a per-proposal subject
#: line (the per-proposal detail rides the BODY, one line each).
POK_LIBRARIAN_BOARD_TITLE = "POK librarian: consolidation proposals to review"

#: The board item's tag — the key a curator filters on.
POK_LIBRARIAN_BOARD_TAG = "pok-librarian"


def librarian_board_body(report: LibrarianPassReport) -> str:
    """The bounded body of the librarian's board notice.

    The board item is a NOTICE, and — since the functional gate's I2
    fix (2026-11-24) — the ONLY PERSISTED artifact that carries the
    PER-PROPOSAL detail: the pass writes no ledger row for a proposal
    and the ``maintenance_runs`` summary is counts-only, so the board
    item itself carries one bounded line per proposal (slug → target
    + basis — NO full bodies), plus the summary counts. One item per
    pass; the per-proposal lines are the detail, not spam.
    """
    by_basis: dict[str, int] = {}
    for proposal in report.proposals:
        basis = str(proposal.get("basis") or "unspecified")
        by_basis[basis] = by_basis.get(basis, 0) + 1
    lines = [
        f"Project: {report.project_id}",
        f"Active rows: {report.active_count} (soft max {report.soft_max})",
        f"Consolidation proposals: {len(report.proposals)}",
    ]
    if by_basis:
        lines.append(
            "By ruled basis: "
            + ", ".join(
                f"{basis}={count}" for basis, count in sorted(by_basis.items())
            )
        )
    # ONE bounded line per proposal (the I2 fix): slug → target + the
    # ruled basis — the curator's actionable detail. NO full bodies
    # (the one-item-per-pass bound and the no-spam rule both hold).
    for proposal in report.proposals:
        slug = str(proposal.get("slug") or "?")
        target = proposal.get("target_slug") or "(no target)"
        basis = str(proposal.get("basis") or "unspecified")
        lines.append(f"- {slug} → {target} (basis: {basis})")
    lines.append(f"R-RET candidates: {len(report.r_ret)}")
    lines.append(f"Expired / never-answered ASK gates: {report.expired_asks}")
    lines.append(
        "Per-proposal detail: the lines above (slug → target + basis). "
        "Counts and the pass summary: the pok-librarian maintenance-run "
        "row. A curator archives via pok_curate with the named basis."
    )
    return "\n".join(lines)


def emit_librarian_board_notice(
    engine: Any, report: LibrarianPassReport
) -> bool:
    """Emit ONE bounded board item for a pass that HAS proposals.

    The board sink (pok-write-gate Phase 12): the librarian's pass
    surfaces its consolidation proposals to the human-facing board —
    ONE item per pass: the summary counts PLUS one bounded
    per-proposal line (slug → target + basis, NO full bodies — the
    functional gate's I2 fix: the pass writes no ledger row for a
    proposal and the maintenance-run summary is counts-only, so this
    item is the only PERSISTED artifact carrying the per-proposal
    detail a curator can act on).

    The call is the SYNCHRONOUS ``BoardService.create_item`` (never
    the async offload path — the ``daemon/repositories/source/
    maintenance_seed.py`` precedent), and it is FAIL-SOFT: a board
    failure (missing table, validation error, DB down) NEVER fails
    the pass — the pass is the job, the notice is the courtesy.

    Returns ``True`` when an item was written, ``False`` when there
    were no proposals (nothing to say) or the write failed.
    """
    if not report.proposals:
        return False
    try:
        from daemon.services.board_service import BoardService

        BoardService(engine=engine).create_item(
            title=POK_LIBRARIAN_BOARD_TITLE,
            body_md=librarian_board_body(report),
            project_id=report.project_id,
            tags=[POK_LIBRARIAN_BOARD_TAG],
            created_by_instance=POK_LIBRARIAN_BOARD_AUTHOR,
        )
        logger.info(
            "[POK:Librarian] board notice written for project %s "
            "(%d proposals)",
            report.project_id,
            len(report.proposals),
        )
        return True
    except Exception as exc:  # noqa: BLE001 — fail-soft by contract
        logger.warning(
            "[POK:Librarian] board notice failed for project %s (%s) — "
            "the pass is unaffected; the proposals remain in the "
            "pass report",
            report.project_id,
            type(exc).__name__,
        )
        return False


# ═══════════════════════════════════════════════════════════════════════════
# The H3 pass JOB (T10.4) — ONE global MaintenanceService job
# ═══════════════════════════════════════════════════════════════════════════


#: The maintenance-run section the pass writes its audit row under
#: (the ``JOB_MAINTENANCE_SECTIONS`` entry — the persisted
#: last-success source).
POK_LIBRARIAN_SECTION = "pok-librarian"

#: The pass cadence (hours between passes). The H3 pick: ONE global
#: job with a persisted clock (``min_interval_hours``), not a
#: per-project timer.
POK_LIBRARIAN_INTERVAL_HOURS = 24.0


class POKLibrarianJob:
    """The H3 periodic pass job (T10.4).

    ONE global job, registered at manager initialize, that loops the
    project ids INTERNALLY (the projects each get their own try/except
    — one project's failure never stops the pass). It is
    ``idle_policy=UN_GATED`` (an IDLE_GATED job starves on a
    continuously busy nest) and it writes a ``maintenance_runs`` row
    with ``section='pok-librarian'``.

    BOOT-SLO (120 s): the registration is a dict-append — the job's
    ``execute`` is NEVER called from the boot path (the maintenance
    loop sleeps 60 s before its first tick). The pass is also
    fail-soft: a project that raises is captured in its report and
    the pass moves on.
    """

    def __init__(
        self,
        *,
        engine: Any,
        runs_repo: Any | None = None,
        run_lock: Any | None = None,
        floor: int | None = None,
    ) -> None:
        self._engine = engine
        self._runs_repo = runs_repo
        self._run_lock = run_lock
        self._floor = floor

    def project_ids(self) -> list[str]:
        """The projects the pass walks (the system default is skipped).

        The POK store is REFUSED on the system-default project (AC10),
        so a pass over it would read a store that cannot be written.
        """
        try:
            from daemon.constants import SYSTEM_DEFAULT_PROJECT_ID

            projects = SQLModelProjectRepository(self._engine).list_projects(
                limit=500
            )
        except Exception as exc:  # noqa: BLE001 — a listing failure is not fatal
            logger.warning(
                "[POK:Librarian] project listing failed (%s) — the pass "
                "walks no project this cycle",
                type(exc).__name__,
            )
            return []
        ids: list[str] = []
        for project in projects:
            pid = getattr(project, "project_id", None)
            if not pid or pid == SYSTEM_DEFAULT_PROJECT_ID:
                continue
            ids.append(pid)
        return ids

    async def execute(self) -> list[LibrarianPassReport]:
        """Run the pass over every project (never raises).

        The pass writes ONE ``maintenance_runs`` row
        (``section='pok-librarian'``) — the audit surface for the
        pass. The row is soft-fail: an audit-table problem NEVER
        stops the pass (the pass is the job; the audit row is the
        record of it).
        """
        import asyncio

        run_id = "pok-librarian-%s" % uuid.uuid4().hex[:12]
        audit_started = self._start_audit_row(run_id)

        reports: list[LibrarianPassReport] = []
        try:
            for project_id in self.project_ids():
                try:
                    report = await asyncio.to_thread(
                        run_librarian_pass,
                        self._engine,
                        project_id,
                        floor=self._floor,
                    )
                except Exception as exc:  # noqa: BLE001 — belt and braces
                    logger.warning(
                        "[POK:Librarian] pass raised for project %s (%s) — "
                        "the pass continues",
                        project_id,
                        type(exc).__name__,
                    )
                    continue
                reports.append(report)
        finally:
            if audit_started:
                self._finalize_audit_row(run_id, reports)
        return reports

    # ── the audit row (soft-fail, never blocks the pass) ────────────

    def _start_audit_row(self, run_id: str) -> bool:
        if self._runs_repo is None:
            return False
        try:
            from daemon.repositories.maintenance_runs.models import (
                MaintenanceRun,
            )

            return bool(
                self._runs_repo.insert(
                    MaintenanceRun(
                        run_id=run_id,
                        section=POK_LIBRARIAN_SECTION,
                        kind="auto",
                        started_at=_now_iso(),
                        status="running",
                        triggered_by="system",
                    )
                )
            )
        except Exception as exc:  # noqa: BLE001 — soft-fail by contract
            logger.warning(
                "[POK:Librarian] audit-row insert failed (%s) — the pass "
                "continues without an audit row",
                type(exc).__name__,
            )
            return False

    def _finalize_audit_row(
        self, run_id: str, reports: Sequence[LibrarianPassReport]
    ) -> None:
        if self._runs_repo is None:
            return
        failed = [r for r in reports if r.error]
        summary = {
            "projects": len(reports),
            "failed": len(failed),
            "soft_max": {r.project_id: r.soft_max for r in reports},
            "expired_asks": sum(r.expired_asks for r in reports),
            "proposals": sum(len(r.proposals) for r in reports),
            "r_ret": sum(len(r.r_ret) for r in reports),
        }
        try:
            self._runs_repo.mark_terminal(
                run_id,
                "failed" if failed else "succeeded",
                _now_iso(),
                summary_json=summary,
            )
        except Exception as exc:  # noqa: BLE001 — soft-fail by contract
            logger.warning(
                "[POK:Librarian] audit-row finalize failed (%s) — the pass "
                "result is unaffected",
                type(exc).__name__,
            )


def run_librarian_pass(
    engine: Any,
    project_id: str,
    *,
    floor: int | None = None,
    write: bool = True,
) -> LibrarianPassReport:
    """Run ONE project's librarian pass (T10.4/T10.5/T10.9/T10.10).

    Per-project, never-raising: a failure is captured in the report
    (``error``) and the pass moves on to the next project (the H3
    requirement: per-project try/except).

    The pass does four things, in order:

    1. read the metrics (T10.1);
    2. derive the soft max and WRITE it to ``pok.soft_max.v1`` with
       the drift report (T10.2/T10.3/T10.5);
    3. surface the expired / never-answered ASK gates (T10.10);
    4. propose the consolidations + R-RET candidates under the RULED
       BASES ONLY (T10.7/T10.9/T10.12) — it never archives.
    """
    try:
        repo = POKRepository(engine)
        store = read_store_metrics(repo, project_id)
        previous = read_soft_max(engine, project_id)
        value, inputs = derive_soft_max(
            active_count=store.active_count,
            working_set=store.working_set,
            floor=floor,
        )
        drift = compute_drift(previous, value, inputs)
        state = SoftMaxState(
            value=value,
            inputs=inputs,
            computed_at=_now_iso(),
            drift=drift,
        )
        if write:
            write_soft_max(engine, project_id, state)

        expired = surface_expired_asks(repo, project_id)

        decision = tightening_decision(
            active_count=store.active_count, soft_max=value
        )
        proposals: list[ConsolidationProposal] = []
        if decision.tight:
            proposals = propose_consolidations(store)
        r_ret = detect_r_ret_candidates(store)

        report = LibrarianPassReport(
            project_id=project_id,
            soft_max=value,
            previous_soft_max=previous.value if previous else None,
            drift=drift,
            tightening=decision.to_payload(),
            active_count=store.active_count,
            working_set=store.working_set,
            expired_asks=len(expired),
            proposals=tuple(p.to_payload() for p in proposals),
            r_ret=tuple(
                {
                    "slug": c.slug,
                    "kind": c.kind,
                    "target_slug": c.target_slug,
                    "basis": c.basis,
                    "evidence": list(c.evidence),
                }
                for c in r_ret
            ),
        )
        logger.info(
            "[POK:Librarian] pass project=%s active=%d soft_max=%d "
            "(previous=%s drift=%s) expired_asks=%d proposals=%d "
            "r_ret=%d",
            project_id,
            store.active_count,
            value,
            report.previous_soft_max,
            drift.get("direction"),
            len(expired),
            len(proposals),
            len(r_ret),
        )
        # The BOARD SINK (pok-write-gate Phase 12): ONE bounded
        # notice per pass that HAS proposals — the pass-internal
        # emission is FAIL-SOFT (a board failure never fails the
        # pass). No proposals ⇒ no board item at all.
        emit_librarian_board_notice(engine, report)
        return report
    except Exception as exc:  # noqa: BLE001 — per-project try/except
        logger.warning(
            "[POK:Librarian] pass failed for project %s (%s) — the pass "
            "continues with the next project",
            project_id,
            type(exc).__name__,
        )
        return LibrarianPassReport(project_id=project_id, error=type(exc).__name__)
