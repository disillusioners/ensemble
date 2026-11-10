"""The nest-anomaly ACCEPT path — validate → gate → scrub → harvest → cluster → board.

The write-side half of the ``nest-report`` tool category (the
``nest-anomaly-report`` lane, board ``wi-2d691ed2``). Phase 2 (commit
C3) completes the path:

    validate → rate gates (R5b, PRE-harvest) → scrub (R6 + AM-11)
    → harvest (AM-8) → pre-insert capacity (AM-9 + C3)
    → ONE-TRANSACTION cluster assignment (AM-5a, inside the AM-14
    same-key stripe) → critical → BOARD (R3, inside the SAME stripe)
    → envelope with REAL cluster fields

Phase-1's DEGENERATE cluster tail (``cluster_id=NULL``, envelope
``pending-phase-2``) is DELETED — accepted reports are ALWAYS
clustered (AM-5b: ``accepted`` MEANS clustered; a live assignment
failure writes the terminal outcome ``unclustered`` directly).

THE RATE GATES RUN BEFORE ANY HARVEST (R5b + AM-9 + F-1 + N4)
----------------------------------------------------------------
A refused report harvests NOTHING — no transcript read, no ancestor
walk, no task-row lookup. Order (the FIRST refusal wins):

* FENCE 1 (read) — the per-agent refusal-ledger cap decides whether a
  rate-limited refusal may WRITE its ledger row (DECIDE first, WRITE
  later; beyond ``refusal_ledger_max_per_agent`` the SAME envelope
  returns with NO row — the metric is the scalable signal).
* FENCE 2 (read) — the GLOBAL TOTAL-row ceiling (accepted + refused;
  the F-1 correction) — at/over it the envelope is
  ``capacity-exhausted`` with NO row, no ledger, and the
  capacity-exhausted counter increments.
* the R5b per-agent decision — minor/major: COUNT accepted ≥ 5/10min;
  critical: COUNT accepted+critical ≥ 20/hour. Over → the typed
  ``rate-limited`` refusal (ledgered iff FENCE 1 allowed it; ALL
  harvest-derived columns NULL — a refused report harvests NOTHING).
* FENCE 3 (read) — the pre-harvest global accepted/hour ceiling
  (1,500/h) → ``capacity-exhausted``, no row.

ALL cutoffs are computed in PYTHON and BOUND as parameters (AM-4 —
the ``created_at >= now() - interval`` SQL form ERRORS on PG), and
EVERY COUNT executes via ``db_offload.offload`` (N4).

THE AM-14 STRIPE (64 slots, index = ``hash(cluster_key_hash) % 64``)
---------------------------------------------------------------------
All cluster writes funnel through one in-process 64-slot stripe array
— same-key serialization is FULLY preserved (the board double-create
race stays closed because the board write stays INSIDE the same-key
stripe) while cross-key contention is eliminated. The lock wraps ONLY
the assignment unit + the critical board surface — never harvest or
scrub (a slow report cannot stall an unrelated cluster join).

CRITICAL → BOARD, NEVER NOTIFY (R3)
-----------------------------------
On a critical report the service writes the board row SERVER-SIDE via
``BoardService`` (the manager seam) — agents NEVER see a board tool
from this lane, and ``notify_user`` is NEVER called (the doctrine:
board is the ALWAYS surface; notify is the deliberate MAY voice).
ONE item per cluster (note-append on repeat criticals); AM-13
ceilings (3/tree/hour, 20/hour global) route overflow to a NOTE on the
tree's most recent anomaly item + the suppression counter; the title
carries the cluster_id and "critical (peak)" (AM-7); the body's first
line is the FIXED prefix "auto-filed — unverified agent prose"
(AM-12e).

RESOLUTION IS DERIVED (R5)
--------------------------
A cluster is open ⇔ ``last_report_at >= now - resolve_window_seconds``
— there is NO status column and NO reopen write anywhere; read
surfaces compute openness at query time.

THE SPLIT POSTURE (AM-11) — READ THIS BEFORE EDITING
----------------------------------------------------
* The **excerpt** column is FAIL-CLOSED on a scrubber-absent boot:
  it persists NULL and a WARNING is logged. The excerpt is RAW TOOL
  STDOUT (the bash tool returns STDOUT verbatim, 150k cap) — the
  highest-risk field.
* **description** / **suspected_cause** stay FAIL-OPEN on a
  scrubber-absent boot: the pattern masker (step 0) and the shape
  masker (step 1) still apply, and a WARNING is logged. Refusing the
  report would blind the nest exactly when things are broken.

THE SOLE-DEFENCE STATEMENT (C1c)
--------------------------------
For a NON-STORED secret the PATTERN MASKER IS THE ONLY DEFENCE — the
exact-match scrubber knows only STORED secret values, so scrubber
PRESENCE is irrelevant to that class. The excerpt's fail-closed
posture must NOT be read as covering it.

EVERY HARVEST FAILURE DEGRADES THAT FIELD TO NULL
--------------------------------------------------
Evidence harvest NEVER refuses a report. Each seam is isolated: a
failing ancestor walk leaves the chain empty and the tree root NULL;
a failing transcript read leaves the excerpt NULL; a failing registry
snapshot leaves both registry columns NULL. Only the PERSIST step can
fail the report (``persist-failed``).

THE DB SEAMS RUN OFF-LOOP (AM-8 / N4)
-------------------------------------
Every DB-touching seam goes through ``db_offload.offload`` — the
v0.27.14 discipline (a sync DB read on the event loop is the incident
class). ONE exception, pinned by AM-8b: ``metrics_query_provider
.families()`` runs IN-LINE, never inside an offload worker — it takes
the REGISTRY-LEVEL lock, and holding a scarce 25-slot worker behind
that lock is the deadlock the amendment names.

MODULE SHAPE — WHY THIS IS ONE MODULE
-------------------------------------
The accept path, both read projectors, the four harvest seams and the
retention sweep live here because they share two invariants: the fence
ORDER (every refusal precedes any harvest) and the per-call manager
dereference for the seams. Splitting the seams into a collaborator
would move the degradation policy away from the ordering it must
compose with (an off-loop seam failure degrades ONE field — it must
never become a refusal). The repository layer below owns the SQL; this
module owns the ordering, the stripes and the degradation.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from daemon._redact import redact_exc_str, redact_secret_shapes
from daemon.config import NestReportConfig
from daemon.repositories.anomaly.models import (
    OUTCOME_ACCEPTED,
    OUTCOME_RATE_LIMITED,
    OUTCOME_UNCLUSTERED,
    SEVERITY_CRITICAL,
    SEVERITY_MAJOR,
    SEVERITY_MINOR,
    AnomalyReport,
)
from daemon.repositories.anomaly.repository import (
    AnomalyRepository,
    ClusterAssignment,
    SEVERITY_RANK,
    cluster_key_hash,
)
from daemon.services.anomaly_vocab import (
    ANOMALY_CATEGORIES_FALLBACK as _SHARED_ANOMALY_CATEGORIES_FALLBACK,
    CATEGORY_SYNONYMS as _SHARED_CATEGORY_SYNONYMS,
    RESERVED_FALLBACK as _SHARED_RESERVED_FALLBACK,
    canonicalize_category,
    resolve_anomaly_categories,
    resolve_reserved,
)
from daemon.services.db_offload import offload as _db_offload
from daemon.services.job_queue_service import DISPATCH_CHAIN_KEY
from daemon.services.secret_persistence_scrub import (
    get_process_scrubber,
    scrub_enqueued_body,
)

logger = logging.getLogger(__name__)

# ── Closed anomaly vocabulary (deferred import) ───────────────────────────
#
# Module-level imports of ``metrics_query.wiring.vocab`` pull in the wiring
# package whose ``__init__`` registers ~52 metric families in the process-wide
# registry at import time (an import-time side effect). That diverges the
# committed exposition artifact if this module is imported during exposition
# capture.
#
# The import is therefore DEFERRED to the point of use (the canonicalisation
# and filter paths), and exposed via PEP 562 ``__getattr__`` for external
# consumers — so a module that merely imports ``daemon.services.anomaly_service``
# registers nothing. The fallback tuples keep the module usable when the
# metrics package cannot be imported.

#: The frozen fallback vocabularies (used when the metrics package cannot
#: be imported). Mirror ``ANOMALY_CATEGORIES``, ``ANOMALY_SEVERITIES``,
#: and ``RESERVED`` at ``daemon/services/metrics_query/wiring/vocab.py``.
#:
#: M9 — the CATEGORY vocabulary + its canonicaliser now live ONCE, in
#: ``daemon/services/anomaly_vocab.py`` (an IMPORT-PURE leaf). The
#: fallback tuple below is a RE-EXPORT of that module's tuple, so the
#: two copies can never drift; the local name is kept because the
#: M-1b parity pins import it from HERE.
_ANOMALY_CATEGORIES_FALLBACK: tuple[str, ...] = (
    _SHARED_ANOMALY_CATEGORIES_FALLBACK
)
_ANOMALY_SEVERITIES_FALLBACK: tuple[str, ...] = (
    "minor",
    "major",
    "critical",
)
_RESERVED_FALLBACK: str = _SHARED_RESERVED_FALLBACK


def _resolve_anomaly_categories() -> tuple[str, ...]:
    """Resolve the closed anomaly categories vocabulary (at the point of use).

    The canonical list lives in the METRICS lane; importing it (or any
    module of the wiring package) registers the phase's metric families
    in the process-wide registry — an import-time side effect. The
    import is therefore made HERE, on the canonicalisation path only.

    Returns:
        The closed vocabulary tuple (the canonical list when the metrics
        package is importable, the frozen fallback otherwise).
    """
    return resolve_anomaly_categories()


def _resolve_anomaly_severities() -> tuple[str, ...]:
    """Resolve the closed anomaly severities vocabulary (at the point of use)."""
    try:  # the closed vocabulary (imported at the point of use)
        from daemon.services.metrics_query.wiring.vocab import (
            ANOMALY_SEVERITIES,
        )
        return tuple(ANOMALY_SEVERITIES)
    except Exception:  # pragma: no cover — import guard
        return _ANOMALY_SEVERITIES_FALLBACK


def _resolve_reserved() -> str:
    """Resolve the reserved fold target (at the point of use)."""
    return resolve_reserved()


def __getattr__(name: str) -> Any:
    """PEP 562 — expose ``ANOMALY_CATEGORIES``, ``ANOMALY_SEVERITIES``,
    and ``RESERVED`` WITHOUT an eager import.

    The public attributes are kept (the pins and external callers read
    ``anomaly_service.ANOMALY_CATEGORIES``), but they are resolved on
    FIRST ACCESS — so importing this module registers nothing.
    """
    if name == "ANOMALY_CATEGORIES":
        return _resolve_anomaly_categories()
    if name == "ANOMALY_SEVERITIES":
        return _resolve_anomaly_severities()
    if name == "RESERVED":
        return _resolve_reserved()
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}"
    )

#: The scrub actor recorded on the value-substitution audit rows.
SCRUB_ACTOR = "nest_report"

#: The refusal tokens the service raises back at the tool (the C2
#: ``_service_token`` duck-typing contract — the tool maps ``.token``
#: onto the house envelope rungs; anything else is ``persist-failed``).
TOKEN_RATE_LIMITED = "rate-limited"
TOKEN_CAPACITY_EXHAUSTED = "capacity-exhausted"

#: AM-14 — the stripe slot count (index = ``hash(cluster_key_hash) % 64``).
STRIPE_SLOTS = 64

#: The AM-13 suppression-note + AM-7 done-transition author. The
#: maintenance job is daemon-side (there is no reporting instance) —
#: a named SYSTEM identity keeps the board audit trail honest.
MAINTENANCE_AUTHOR_INSTANCE = "ensemble-anomaly-maintenance"

#: The retention per-run cap multiplier (phase2-plan: batch × 10).
MAX_BATCHES_PER_RUN = 10

#: The board body's top-description budget (the body stays ≤ ~1800
#: chars — the plan's "scrubbed, ≤ ~1800 chars body cap").
BOARD_TOP_DESCRIPTION_MAX_CHARS = 1500

#: The raw category cap (R1) — the reporter's verbatim input, capped.
CATEGORY_RAW_MAX_CHARS = 64

#: The transcript SCAN WINDOW — the bounded read cap at the read seam
#: (AM-8a). The house precedent is
#: ``daemon/tools/instance_read.py``'s ``MAX_LAST_MESSAGES = 20``; the
#: window IS the read cap, so a post-read trim would be a RED (the
#: O(N^2) walk family the v0.27.15 incident names).
MAX_TRANSCRIPT_WINDOW = 20

#: The house typed-error markers (R6/C5). A transcript message is
#: ERROR-BEARING when its content carries one of these shapes:
#: ``ERROR:`` (the filesystem tool's error form) and/or the refusal
#: envelope ``Error: {label} REFUSED — reason={token}: {message}``.
_ERROR_MARKERS: tuple[str, ...] = ("ERROR:", "REFUSED —")

#: The synonym map for the write-time category fold (R1). The raw
#: input is lowercased and stripped; an EXACT canonical match wins;
#: otherwise a synonym folds; otherwise the reserved ``other``.
#:
#: M9 — the map now lives ONCE, in ``daemon/services/anomaly_vocab.py``;
#: this is a RE-EXPORT (the name stays in this module's ``__all__`` for
#: its external consumers) so the two copies cannot drift.
CATEGORY_SYNONYMS: dict[str, str] = _SHARED_CATEGORY_SYNONYMS


class AnomalyInputError(ValueError):
    """The authored content failed validation (the ``invalid-input`` token).

    Raised BEFORE anything is persisted — an invalid-input refusal
    writes NO row (the refusal ladder's second rung).
    """

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class AnomalyRefusalError(Exception):
    """A typed service refusal (R5b / AM-9) — carries ``.token``.

    The C2 tool contract: the tool's ``_service_token`` duck-types the
    ``token`` attribute onto the refusal-ladder rungs
    (``rate-limited`` / ``capacity-exhausted``); any other failure is
    ``persist-failed``. The token is asserted against the closed pair
    at construction so an unknown token can never leak into the
    envelope vocabulary.
    """

    __slots__ = ("token", "message", "detail")

    def __init__(
        self, token: str, message: str, *, detail: str = ""
    ) -> None:
        if token not in (TOKEN_RATE_LIMITED, TOKEN_CAPACITY_EXHAUSTED):
            raise ValueError(f"unknown refusal token: {token!r}")
        super().__init__(message)
        self.token = token
        self.message = message
        self.detail = detail


def _write_time_counter(handle_name: str) -> Any | None:
    """Resolve a w4 write-time F23 handle WITHOUT a top-level import.

    A module-top ``from ...wiring.w4 import ...`` would drag the whole
    wiring package (w1/w2/w3 + their imports) into the anomaly
    service's import graph for one counter — the lazy import below
    runs ONLY at the increment/read points.  Returns ``None`` on any
    import-time failure (the counter then degrades to 0 on read and
    the event is only in the log — never a refused report).
    """
    try:
        from daemon.services.metrics_query.wiring import w4

        return getattr(w4, handle_name, None)
    except Exception:  # noqa: BLE001 — the counter must never break the write path
        logger.warning(
            "anomaly: the F23 write-time counter %r is unreachable",
            handle_name,
        )
        return None


def _write_time_inc(handle_name: str) -> None:
    """Increment a write-time F23 counter at its service write point.

    ONE increment point per event: the w4 REGISTRY HANDLE is the
    single counter store (there is deliberately NO parallel local
    int — two stores would drift).
    """
    handle = _write_time_counter(handle_name)
    if handle is None:
        return
    try:
        handle.inc()
    except Exception:  # noqa: BLE001 — the counter must never break the write path
        logger.warning(
            "anomaly: incrementing the F23 write-time counter %r failed",
            handle_name,
        )


def _write_time_read(handle_name: str) -> int:
    """Read a write-time F23 counter (the property view of the handle)."""
    handle = _write_time_counter(handle_name)
    if handle is None:
        return 0
    try:
        return int(handle.get())
    except Exception:  # noqa: BLE001 — a read failure reads as 0, never raises
        return 0


def _now_dt() -> datetime:
    """The service's clock — ONE derivation point.

    Everything (``now`` stamps, every window cutoff) derives from
    ``_utc_now_iso()`` so a single monkeypatch controls the whole
    service deterministically.
    """
    parsed = _parse_iso(_utc_now_iso())
    if parsed is not None:
        return parsed
    return datetime.now(timezone.utc)


def _cutoff(seconds: float) -> str:
    """A Python-computed, FIXED-WIDTH window cutoff (AM-4).

    Bound as a parameter — NEVER a SQL ``now() - interval`` (the TEXT
    vs timestamptz operator does not exist on PG).
    """
    return (
        _now_dt() - timedelta(seconds=float(seconds))
    ).isoformat(timespec="microseconds")


@dataclass
class AnomalyHarvest:
    """The bounded evidence harvest for one report.

    Every field is independently degradable to ``None`` / empty — a
    failing seam NEVER fails the report.
    """

    tree_root_instance_id: str | None = None
    parent_chain: list[str] = field(default_factory=list)
    parent_chain_agents: list[str] = field(default_factory=list)
    job_id: str | None = None
    mission_id: str | None = None
    dispatch_chain: dict[str, Any] | None = None
    boot_epoch: str | None = None
    registry_summary_sha256: str | None = None
    registry_captured_at: str | None = None
    wedge_capture_ids: list[str] | None = None
    excerpt: str | None = None
    excerpt_sha256: str | None = None
    excerpt_length: int | None = None


@dataclass
class AnomalyAcceptResult:
    """What the accept path produced."""

    report_id: str
    category: str
    category_raw: str
    severity: str
    fold_notice: str | None = None
    truncated: bool = False
    cluster_id: str | None = None
    cluster_report_count: int = 1
    cluster_first_report_at: str = ""
    excerpt_sha256: str | None = None
    excerpt_length: int | None = None
    outcome: str = OUTCOME_ACCEPTED


def _utc_now_iso() -> str:
    """The FIXED-WIDTH UTC stamp (AM-4) — 32 chars, always ``+00:00``."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO stamp into an aware UTC datetime, or ``None``.

    Accepts the three shapes in play: the fixed-width ``+00:00`` form,
    the naive form, and the ``...Z`` form. A naive value is ASSUMED to
    be UTC (the boot-epoch convention).
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_boot_epoch(value: str | None) -> str | None:
    """Canonicalize a boot-epoch stamp to the NAIVE-UTC form (AM-8d).

    The wedge subsystem has TWO shapes: the watchdog writes the naive
    ``datetime.isoformat()`` (``wedge_capture/watchdog.py``) and the
    boot-record writes ``isoformat() + "Z"``
    (``wedge_capture/boot_record.py``). Both sides are canonicalized
    to the SAME naive-UTC string before any comparison — a raw-string
    compare would silently fail to match.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.isoformat()


def is_error_bearing(content: str) -> bool:
    """True when the content carries a house typed-error shape.

    The two shapes (R6/C5): ``ERROR:`` (the filesystem tool's error
    form) and the refusal envelope ``Error: {label} REFUSED — reason=
    {token}: {message}``. The comparison is case-insensitive so the
    envelope's ``Error:`` prefix counts too.
    """
    if not content:
        return False
    upper = content.upper()
    return any(marker.upper() in upper for marker in _ERROR_MARKERS)


def select_error_excerpt(
    messages: Sequence[Any],
    *,
    max_messages: int,
    max_chars_per_message: int,
) -> list[str]:
    """The C5 selection rule — the SINGULAR excerpt rule.

    "The excerpt = the last ≤5 ERROR-BEARING tool messages in the turn,
    selected from a bounded scan window of the last ≤20 messages of
    that turn's transcript; each excerpt ≤500 chars, total ≤2000."

    The TOTAL cap is applied by the CALLER after the parts are joined
    (``_harvest_excerpt`` slices the joined text to
    ``excerpt_max_total_chars``) — this function bounds the per-part
    length and the part COUNT only.

    The ERROR-BEARING predicate is the house ASSIGNMENT form, as TWO
    statements — the assignment, then a SEPARATE comparison::

        msg_type = getattr(msg, "type", None) or getattr(msg, "role", None)
        if msg_type == "tool":

    The one-expression rendering (``... or getattr(msg, "role", None)
    == "tool"``) is FORBIDDEN: Python parses it as ``A or (B ==
    "tool")``, so the ``.type`` STRING short-circuits the ``or`` and
    the "is a tool message" half degenerates into a truthiness test —
    which admits the agent's own prose and the system prompt into the
    excerpt (the trust-boundary break M-1i pins).

    Args:
        messages: The bounded scan window (already sliced by the
            caller — the read cap lives at the READ seam).
        max_messages: How many excerpts to keep (the last N).
        max_chars_per_message: The per-excerpt character cap.

    Returns:
        The selected excerpt parts, OLDEST FIRST (chronological).
    """
    selected: list[str] = []
    for message in reversed(list(messages)):
        msg_type = getattr(message, "type", None) or getattr(message, "role", None)
        if msg_type == "tool":
            content = getattr(message, "content", "")
            if not isinstance(content, str):
                content = str(content)
            if is_error_bearing(content):
                piece = content[:max_chars_per_message]
                if piece:
                    selected.append(piece)
                if len(selected) >= max_messages:
                    break
    selected.reverse()
    return selected


def scrub_text(value: object, cap: int, *, actor: str = SCRUB_ACTOR) -> str:
    """The R6 + AM-11 scrub pipeline, in the FIXED order.

    0. ``redact_secret_shapes`` — the secret-VALUE masker. It MUST see
       the FULL text: the cap is applied AFTER the masking inside the
       function, so a value straddling the cap boundary is masked
       whole rather than cut into an unmatchable fragment.
    1. ``redact_exc_str`` — the house shape masker (never raises; the
       limit is applied after ITS redaction).
    2. ``scrub_enqueued_body`` — the exact-match substitution over the
       STORED secret values.
    3. The length cap, post-scrub.

    Args:
        value: The text (any object; a non-string is stringified
            defensively by the maskers).
        cap: The field cap.
        actor: The audit actor for the value-substitution rows.

    Returns:
        The scrubbed, capped text. Never raises.
    """
    masked = redact_secret_shapes(value, limit=cap)
    shaped = redact_exc_str(masked, limit=cap)
    scrubbed = scrub_enqueued_body(shaped, actor=actor)
    return scrubbed[:cap]


@dataclass
class _Seams:
    """The resolved evidence seams."""

    instance_repository: Any = None
    task_repository: Any = None
    job_repository: Any = None
    mission_repository: Any = None
    wedge_repository: Any = None


class AnomalyService:
    """The accept path for the nest-anomaly store.

    Constructed with the daemon engine; the ``manager`` is OPTIONAL
    and supplies the evidence seams (the repositories, the
    checkpointer, the metrics query provider, the board service). A
    missing seam degrades its field to NULL — it never fails the
    report.
    """

    def __init__(
        self,
        engine: Any,
        *,
        config: NestReportConfig | None = None,
        manager: Any = None,
        repository: AnomalyRepository | None = None,
        offload: Callable[..., Any] | None = None,
    ) -> None:
        import asyncio

        self._engine = engine
        self._config = config or NestReportConfig()
        self._manager = manager
        self._repository = repository or AnomalyRepository(engine)
        self._offload = offload or _db_offload
        self._seams: Any = None
        # AM-14 — the 64-slot stripe (index = hash(cluster_key_hash) % 64).
        self._stripe_locks: list[Any] = [asyncio.Lock() for _ in range(STRIPE_SLOTS)]
        # C3(i) + AM-13 — the write-time F23 counters have NO local
        # twin: the w4 REGISTRY HANDLES are the single counter store
        # (incremented via :func:`_write_time_inc` — a lazy import of
        # ``daemon.services.metrics_query.wiring.w4``).  The metric is
        # the PROCESS-WIDE nest signal; the per-SERVICE read below is
        # the delta since THIS service was constructed (one store, two
        # projections — a local int counter would be a second, drifting
        # system).
        self._capacity_exhausted_baseline = _write_time_read("capacity_exhausted")
        self._board_items_suppressed_baseline = _write_time_read("board_items_suppressed")

    # ── configuration access ──────────────────────────────────────────

    @property
    def config(self) -> NestReportConfig:
        return self._config

    @property
    def repository(self) -> AnomalyRepository:
        return self._repository

    @property
    def capacity_exhausted_total(self) -> int:
        """C3(i) — how many reports THIS service refused on capacity.

        The counter STORE is the process-wide F23 handle; this read is
        the per-instance delta since construction (a fresh service
        starts at zero without rewriting the global counter).
        """
        return max(
            0,
            _write_time_read("capacity_exhausted")
            - self._capacity_exhausted_baseline,
        )

    @property
    def board_items_suppressed_total(self) -> int:
        """AM-13 — how many board creates THIS service routed to a note."""
        return max(
            0,
            _write_time_read("board_items_suppressed")
            - self._board_items_suppressed_baseline,
        )

    def _stripe_for(self, key_hash: str) -> Any:
        """The AM-14 same-key stripe slot for a cluster key hash."""
        return self._stripe_locks[hash(key_hash) % STRIPE_SLOTS]

    def _board_service(self) -> Any:
        """The manager's board service (or ``None`` — degraded board)."""
        manager = self._manager
        for attribute in ("_board_service", "board_service"):
            board = getattr(manager, attribute, None) if manager else None
            if board is not None:
                return board
        return None

    # ── normalization (R1 / R3) ───────────────────────────────────────

    def normalize_category(self, raw: str) -> tuple[str, str | None]:
        """Fold the raw category into the CLOSED vocabulary (R1).

        Lowercase → strip → exact canonical match → synonym → the
        reserved ``other``. The RAW input is preserved by the caller
        (``category_raw``, capped at 64 chars) so a vocabulary gap
        stays visible.

        Returns:
            ``(canonical, fold_notice)`` — the notice is ``None`` when
            the input was already canonical.
        """
        return canonicalize_category(raw)

    def normalize_severity(self, raw: object) -> str:
        """Normalize the severity tier (R3).

        Out-of-set ⇒ a typed ``invalid-input`` refusal — never a silent
        fold (``critical`` → ``major`` would HIDE a nest-level event).
        """
        if not isinstance(raw, str):
            raise AnomalyInputError(
                "severity must be a string", detail="non-string severity"
            )
        text = raw.strip().lower()
        severities = _resolve_anomaly_severities()
        if text not in severities:
            raise AnomalyInputError(
                f"severity must be one of {'|'.join(severities)}",
                detail=f"got {raw!r}",
            )
        return text

    # ── the accept path ───────────────────────────────────────────────

    async def accept_report(
        self,
        *,
        agent_id: str,
        instance_id: str,
        category: str,
        description: str,
        severity: str = SEVERITY_MAJOR,
        suspected_cause: str | None = None,
    ) -> AnomalyAcceptResult:
        """Validate → normalize → scrub → harvest → persist.

        Args:
            agent_id: The reporter's agent TYPE id (AM-10 — the budget
                key is the type).
            instance_id: The reporter's instance id.
            category: The raw category (folded per R1).
            description: What is wrong (required).
            severity: The tier (default ``major``).
            suspected_cause: The reporter's hypothesis (optional).

        Returns:
            The accept result (the persisted row + the envelope facts).

        Raises:
            AnomalyInputError: The authored content failed validation
                (``invalid-input``) — nothing was persisted.
        """
        if not isinstance(category, str) or not category.strip():
            raise AnomalyInputError(
                "category must be a non-empty string", detail="empty category"
            )
        if not isinstance(description, str) or not description.strip():
            raise AnomalyInputError(
                "description must be a non-empty string",
                detail="empty description",
            )
        if suspected_cause is not None and not isinstance(suspected_cause, str):
            raise AnomalyInputError(
                "suspected_cause must be a string or omitted",
                detail="non-string suspected_cause",
            )
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise AnomalyInputError(
                "agent_id must be a non-empty string", detail="empty agent_id"
            )
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise AnomalyInputError(
                "instance_id must be a non-empty string", detail="empty instance_id"
            )
        severity_value = self.normalize_severity(severity)

        canonical, fold_notice = self.normalize_category(category)
        raw_category = category[:CATEGORY_RAW_MAX_CHARS]

        # ── 0b. RATE LIMITS (R5b + AM-4 + AM-9 + F-1 + N4) — BEFORE any
        #    harvest. A refused report harvests NOTHING (no transcript
        #    read, no ancestor walk, no task-row lookup).
        await self._enforce_rate_gates(
            agent_id=agent_id,
            instance_id=instance_id,
            severity=severity_value,
            category=category,
            description=description,
            suspected_cause=suspected_cause,
        )

        truncated = False
        description_cap = self._config.description_max_chars
        cause_cap = self._config.suspected_cause_max_chars
        if len(description) > description_cap:
            truncated = True
        if suspected_cause is not None and len(suspected_cause) > cause_cap:
            truncated = True

        scrubbed_description = scrub_text(description, description_cap)
        scrubbed_cause = (
            scrub_text(suspected_cause, cause_cap)
            if suspected_cause is not None
            else None
        )

        harvest = await self.harvest(instance_id=instance_id, severity=severity_value)

        created_at = _utc_now_iso()
        row = AnomalyReport(
            created_at=created_at,
            agent_id=agent_id,
            instance_id=instance_id,
            tree_root_instance_id=harvest.tree_root_instance_id,
            parent_chain=list(harvest.parent_chain),
            parent_chain_agents=list(harvest.parent_chain_agents),
            category=canonical,
            category_raw=raw_category,
            severity=severity_value,
            description=scrubbed_description,
            suspected_cause=scrubbed_cause,
            excerpt=harvest.excerpt,
            excerpt_sha256=harvest.excerpt_sha256,
            excerpt_length=harvest.excerpt_length,
            job_id=harvest.job_id,
            mission_id=harvest.mission_id,
            dispatch_chain=harvest.dispatch_chain,
            boot_epoch=harvest.boot_epoch,
            registry_summary_sha256=harvest.registry_summary_sha256,
            registry_captured_at=harvest.registry_captured_at,
            wedge_capture_ids=harvest.wedge_capture_ids,
            cluster_id=None,
            outcome=OUTCOME_ACCEPTED,
            refusal_reason=None,
        )

        # ── 1b. PRE-INSERT capacity (AM-9 + C3, fold round 5),
        #    post-harvest pre-persist. The GLOBAL TOTAL-row ceiling is
        #    the nest-wide WALL — it REFUSES. The per-tree cap SHEDS
        #    (oldest-first, via the SHARED shed_members helper inside
        #    the same unit-of-work) — the channel NEVER goes dark.
        tree_root = harvest.tree_root_instance_id or instance_id
        total_rows = await self._offload(
            self._repository.count_reports, outcome=None
        )
        if total_rows >= self._config.max_rows_global:
            self._refuse_capacity(
                f"the nest-wide anomaly row ceiling is reached "
                f"({total_rows} rows >= {self._config.max_rows_global})"
            )
        shed_ids: list[str] | None = None
        tree_rows = await self._offload(
            self._repository.count_reports,
            tree_root_instance_id=tree_root,
            outcome=None,
        )
        if tree_rows >= self._config.max_rows_per_tree_root:
            over = tree_rows - (self._config.max_rows_per_tree_root - 1)
            take = min(over, self._config.retention_batch_size)
            shed_ids = await self._offload(
                self._repository.select_oldest_report_ids,
                tree_root_instance_id=tree_root,
                limit=take,
            )

        # ── 2+3. THE ONE-TRANSACTION UNIT-OF-WORK (AM-5a) inside the
        #    AM-14 same-key stripe — report INSERT + cluster
        #    INSERT/UPDATE + binding in ONE repository transaction, ONE
        #    db_offload call.
        key_hash = cluster_key_hash(tree_root, canonical)
        window_cutoff = _cutoff(self._config.cluster_window_seconds)
        async with self._stripe_for(key_hash):
            assignment: ClusterAssignment = await self._offload(
                self._repository.assign_report_unit,
                row,
                tree_root_instance_id=tree_root,
                category=canonical,
                severity=severity_value,
                window_cutoff=window_cutoff,
                now=created_at,
                shed_report_ids=shed_ids,
            )
            # ── 4. CRITICAL → BOARD (R3) — INSIDE the SAME-KEY stripe
            #    (AM-14 prose is authoritative: the board read and the
            #    create/note-append are serialized with the assignment,
            #    so two concurrent criticals on a fresh cluster CANNOT
            #    both create an item).
            if severity_value == SEVERITY_CRITICAL and assignment.assigned:
                await self._ensure_board_surface(
                    cluster_id=assignment.cluster_id or "",
                    tree_root=tree_root,
                    canonical=canonical,
                    count=assignment.report_count,
                    first_report_at=assignment.first_report_at,
                    agent_id=agent_id,
                    reporting_instance_id=instance_id,
                    now=created_at,
                )

        # ── 5. the envelope with REAL cluster fields.
        return AnomalyAcceptResult(
            report_id=assignment.report_id,
            category=canonical,
            category_raw=raw_category,
            severity=severity_value,
            fold_notice=fold_notice,
            truncated=truncated,
            cluster_id=assignment.cluster_id if assignment.assigned else None,
            cluster_report_count=assignment.report_count,
            cluster_first_report_at=assignment.first_report_at,
            excerpt_sha256=harvest.excerpt_sha256,
            excerpt_length=harvest.excerpt_length,
            outcome=OUTCOME_ACCEPTED if assignment.assigned else OUTCOME_UNCLUSTERED,
        )

    # ── the rate gates (R5b / AM-9 / F-1 / N4) ────────────────────────

    def _refuse_capacity(self, detail: str) -> None:
        """Refuse ``capacity-exhausted`` — NO row, NO ledger, COUNTER up.

        The C3(iii) OBSERVABLE CONSEQUENCE: the envelope returns, no row
        is written, the capacity-exhausted counter increments and a
        WARNING surfaces the refusal for morning review (the metric is
        the scalable signal; the ledger is for per-agent trace only).
        """
        _write_time_inc("capacity_exhausted")
        logger.warning(
            "anomaly: capacity-exhausted refusal (surfaced for morning "
            "review): %s",
            detail,
        )
        raise AnomalyRefusalError(
            TOKEN_CAPACITY_EXHAUSTED,
            "the nest-wide anomaly capacity ceiling is reached",
            detail=detail,
        )

    async def _enforce_rate_gates(
        self,
        *,
        agent_id: str,
        instance_id: str,
        severity: str,
        category: str,
        description: str,
        suspected_cause: str | None = None,
    ) -> None:
        """Fences 1-3 + the R5b per-agent decision — all READS first.

        Order (phase2-plan §0b): FENCE 1 (ledger-cap read — DECIDE
        first, WRITE later) → FENCE 2 (the GLOBAL TOTAL-row ceiling,
        BEFORE any refusal-ledger write) → the per-agent rate decision
        (the only WRITE path — the ledgered refusal row) → FENCE 3
        (the pre-harvest global accepted/hour ceiling).
        """
        cfg = self._config
        # FENCE 1 (read) — is this agent's refusal-ledger window full?
        ledger_cutoff = _cutoff(cfg.rate_window_seconds)
        ledger_rows = await self._offload(
            self._repository.count_reports,
            agent_id=agent_id,
            outcome=OUTCOME_RATE_LIMITED,
            since=ledger_cutoff,
        )
        ledger_allowed = ledger_rows < cfg.refusal_ledger_max_per_agent
        # FENCE 2 (read) — the GLOBAL TOTAL-row ceiling (accepted +
        # refused — the F-1 correction: no path persists a refusal row
        # while the ceiling is at/over max_rows_global).
        total_rows = await self._offload(
            self._repository.count_reports, outcome=None
        )
        if total_rows >= cfg.max_rows_global:
            self._refuse_capacity(
                f"the nest-wide anomaly row ceiling is reached "
                f"({total_rows} rows >= {cfg.max_rows_global})"
            )
        # The R5b per-agent decision (read only).
        window_name = "10min"
        observed = 0
        maximum = 0
        if severity != SEVERITY_CRITICAL:
            window_name = f"{cfg.rate_window_seconds}s"
            maximum = cfg.rate_max_per_agent
            observed = await self._offload(
                self._repository.count_reports,
                agent_id=agent_id,
                outcome=OUTCOME_ACCEPTED,
                since=_cutoff(cfg.rate_window_seconds),
            )
        else:
            window_name = f"{cfg.critical_window_seconds}s"
            maximum = cfg.critical_max_per_agent
            observed = await self._offload(
                self._repository.count_reports,
                agent_id=agent_id,
                outcome=OUTCOME_ACCEPTED,
                severity=SEVERITY_CRITICAL,
                since=_cutoff(cfg.critical_window_seconds),
            )
        if observed >= maximum:
            # FENCE 1 decides the WRITE; FENCE 2 already guaranteed the
            # ceiling. Beyond the ledger cap the SAME envelope returns
            # with NO ledger row (the metric is the scalable signal).
            if ledger_allowed:
                await self._ledger_refusal(
                    agent_id=agent_id,
                    instance_id=instance_id,
                    severity=severity,
                    observed=observed,
                    maximum=maximum,
                    window_name=window_name,
                    category=category,
                    description=description,
                    suspected_cause=suspected_cause,
                )
            raise AnomalyRefusalError(
                TOKEN_RATE_LIMITED,
                "this agent type's report budget for the current "
                "window is exhausted",
                detail=f"rate-limited: {window_name} {observed}/{maximum}",
            )
        # FENCE 3 (read) — the pre-harvest global accepted/hour ceiling
        # (the OPERATOR BRAKE, N2/D3: 1,500/h binds in steady state).
        hour_cutoff = _cutoff(3600)
        accepted_hour = await self._offload(
            self._repository.count_reports,
            outcome=OUTCOME_ACCEPTED,
            since=hour_cutoff,
        )
        if accepted_hour >= cfg.max_accepted_per_hour_global:
            self._refuse_capacity(
                f"the global accepted-per-hour brake fired "
                f"({accepted_hour} >= {cfg.max_accepted_per_hour_global})"
            )

    async def _ledger_refusal(
        self,
        *,
        agent_id: str,
        instance_id: str,
        severity: str,
        observed: int,
        maximum: int,
        window_name: str,
        category: str,
        description: str,
        suspected_cause: str | None = None,
    ) -> None:
        """Persist the LEDGERED refusal row (R5b — the spam signal).

        Authored content IS stored (agent, instance, category, severity,
        description — morning review sees WHAT was suppressed); ALL
        harvest-derived columns are NULL (``tree_root_instance_id``,
        chains, excerpt*, job/mission/dispatch, boot_epoch, registry*,
        wedge*) — a refused report harvests NOTHING. The row never
        consumes budget (counting queries filter ``outcome='accepted'``).
        """
        row = AnomalyReport(
            created_at=_utc_now_iso(),
            agent_id=agent_id,
            instance_id=instance_id,
            tree_root_instance_id=None,
            parent_chain=[],
            parent_chain_agents=[],
            category=(self.normalize_category(category))[0],
            category_raw=category[:CATEGORY_RAW_MAX_CHARS],
            severity=severity,
            description=scrub_text(
                description, self._config.description_max_chars
            ),
            suspected_cause=(
                scrub_text(suspected_cause, self._config.suspected_cause_max_chars)
                if suspected_cause
                else None
            ),
            cluster_id=None,
            outcome=OUTCOME_RATE_LIMITED,
            refusal_reason=f"rate-limited: {window_name} {observed}/{maximum}",
        )
        await self._offload(self._repository.insert_report, row)

    # ── the critical → board seam (R3 + AM-13 + AM-12e + AM-7) ────────

    async def _ensure_board_surface(
        self,
        *,
        cluster_id: str,
        tree_root: str,
        canonical: str,
        count: int,
        first_report_at: str,
        agent_id: str,
        reporting_instance_id: str,
        now: str,
    ) -> None:
        """Exactly ONE board item per cluster — create once, note-append.

        Runs INSIDE the same-key stripe (AM-14 prose is authoritative),
        so two concurrent criticals on a fresh cluster cannot both see
        ``board_item_id IS NULL`` and double-create. A board failure is
        DEGRADED (WARNING) — the report is already accepted and stays
        accepted; the board is the surface, not the transaction.
        ``notify_user`` is NEVER called (R3 — pinned).
        """
        board = self._board_service()
        if board is None:
            logger.warning(
                "anomaly: board service unavailable — the critical "
                "board surface for cluster %s was SKIPPED (the report "
                "stays accepted)",
                cluster_id,
            )
            return
        # The append path — the cluster already owns its board row.
        existing = await self._offload(
            self._repository.get_cluster_board_item_id, cluster_id
        )
        if existing:
            note = (
                f"anomaly cluster {cluster_id}: critical report #{count} "
                f"from {agent_id} at {now}; cluster total={count}, "
                f"max_severity=critical (peak)"
            )
            try:
                await self._offload(
                    board.update_item,
                    item_id=existing,
                    note=note,
                    author_instance=reporting_instance_id,
                )
            except Exception as exc:  # noqa: BLE001 — board degrades
                logger.warning(
                    "anomaly: board note-append failed for cluster %s: %s",
                    cluster_id,
                    redact_exc_str(exc, limit=200),
                )
            return
        # AM-13 ceilings — COUNT recent anomaly items (this tree /
        # global, per hour) BEFORE create; beyond a ceiling: append a
        # NOTE to the tree's most recent anomaly item instead of
        # creating + increment the suppression counter + point the
        # cluster at that item.
        hour_cutoff = _cutoff(3600)
        tree_recent = await self._offload(
            self._repository.count_recent_board_items,
            tree_root_instance_id=tree_root,
            since=hour_cutoff,
        )
        global_recent = await self._offload(
            self._repository.count_recent_board_items,
            since=hour_cutoff,
        )
        ceiling_hit = (
            tree_recent >= self._config.max_board_items_per_tree_root_per_hour
            or global_recent >= self._config.max_board_items_per_hour_global
        )
        if ceiling_hit:
            target = await self._offload(
                self._repository.most_recent_board_item_for_tree, tree_root
            )
            if target is None:
                # The tree has no anomaly item yet (the GLOBAL ceiling
                # fired): the note lands on the most recent anomaly
                # item ANYWHERE — a global flood must not mint fresh
                # items just because each tree is on its first.
                target = await self._offload(
                    self._repository.most_recent_board_item_for_tree
                )
            if target is not None:
                _write_time_inc("board_items_suppressed")
                note = (
                    f"anomaly cluster {cluster_id}: critical report "
                    f"#{count} from {agent_id} at {now} SUPPRESSED "
                    f"(board ceiling) — appended to this item; cluster "
                    f"total={count}, max_severity=critical (peak)"
                )
                try:
                    await self._offload(
                        board.update_item,
                        item_id=target,
                        note=note,
                        author_instance=reporting_instance_id,
                    )
                except Exception as exc:  # noqa: BLE001 — board degrades
                    logger.warning(
                        "anomaly: suppressed-board note-append failed "
                        "for cluster %s: %s",
                        cluster_id,
                        redact_exc_str(exc, limit=200),
                    )
                    return
                await self._offload(
                    self._repository.set_cluster_board_item, cluster_id, target
                )
                return
            # No target item exists (nothing to append to) — fall
            # through and create; disclosed.
        # The create path (R3's verbatim shape).
        top_description = await self._offload(
            self._repository.top_description_for_cluster,
            cluster_id,
            max_chars=BOARD_TOP_DESCRIPTION_MAX_CHARS,
        )
        title = f"[anomaly][critical (peak)] {canonical} — cluster {cluster_id}"
        body_md = (
            "auto-filed — unverified agent prose\n"
            f"cluster_id: {cluster_id}\n"
            f"tree_root: {tree_root}\n"
            f"reports: {count} (max_severity: critical, peak)\n"
            f"first_report_at: {first_report_at}\n\n"
            f"top description:\n{top_description or '(none)'}"
        )
        try:
            item = await self._offload(
                board.create_item,
                title=title,
                body_md=body_md,
                tags=["anomaly", "severity:critical", f"category:{canonical}"],
                project_id=None,
                created_by_instance=reporting_instance_id,
            )
        except Exception as exc:  # noqa: BLE001 — board degrades
            logger.warning(
                "anomaly: board create failed for cluster %s: %s",
                cluster_id,
                redact_exc_str(exc, limit=200),
            )
            return
        item_id = str(item.get("id")) if isinstance(item, dict) else str(item)
        await self._offload(
            self._repository.set_cluster_board_item, cluster_id, item_id
        )

    # ── the retention job (R6 + AM-6 + AM-9 + AM-5b + AM-7) ───────────

    async def run_retention_sweep(self) -> None:
        """The 24h maintenance job — bounded, batched, best-effort.

        Registered on the EXISTING ``MaintenanceService`` seam
        (``manager.py``'s registration cluster). An exception logs a
        WARNING and returns — it never crashes the maintenance loop
        (the house sweep convention).

        AM-15 / NR-7 DISCLOSURE (do not fix here): idle-gated
        maintenance starvation — on a continuously busy nest this
        sweep can starve (the checkpoint-cleanup class); the AM-9
        insert gate keeps the flood bounded (the F-1-corrected
        TOTAL-row bound) while the sweep starves. Routed to the
        maintenance lane.
        """
        try:
            await self._run_retention_sweep_inner()
        except Exception as exc:  # noqa: BLE001 — never kill the loop
            logger.warning(
                "anomaly: retention sweep failed (best-effort, will "
                "retry next tick): %s",
                redact_exc_str(exc, limit=300),
            )

    async def _run_retention_sweep_inner(self) -> None:
        cfg = self._config
        now_dt = _now_dt()
        age_cutoff = (
            now_dt - timedelta(days=cfg.retention_days)
        ).isoformat(timespec="microseconds")
        resolve_cutoff = (
            now_dt - timedelta(seconds=cfg.resolve_window_seconds)
        ).isoformat(timespec="microseconds")
        batch = cfg.retention_batch_size
        per_run_cap = batch * MAX_BATCHES_PER_RUN

        # 1. the age sweep — reports, batched, with the AM-6 decrements.
        deleted = 0
        while deleted < per_run_cap:
            taken = await self._offload(
                self._repository.sweep_reports_older_than,
                age_cutoff,
                limit=batch,
            )
            if taken == 0:
                break
            deleted += taken

        # 2. the AM-9 GLOBAL capacity trim (oldest-first beyond
        #    max_rows_global — TOTAL rows, every outcome).
        await self._offload(
            self._repository.trim_to_global_capacity,
            max_rows=cfg.max_rows_global,
            batch=batch,
            per_run_cap=per_run_cap,
        )

        # 3. the C3(ii) per-tree trim (the defense leg — the LIVE shed
        #    runs shed-then-accept at insert).
        await self._offload(
            self._repository.trim_trees_to_capacity,
            max_rows_per_tree=cfg.max_rows_per_tree_root,
            batch=batch,
        )

        # 4. clusters AFTER their member reports (by last_report_at age).
        removed = 1
        swept_clusters = 0
        while removed and swept_clusters < per_run_cap:
            removed = await self._offload(
                self._repository.delete_clusters_older_than,
                age_cutoff,
                limit=batch,
            )
            swept_clusters += removed

        # 5. AM-5b — TERMINALIZE orphan accepted+NULL rows (bounded,
        #    age-gated at the resolve window so a live assignment is
        #    never raced). NEVER reassigned.
        await self._offload(
            self._repository.terminalize_orphan_accepted,
            resolve_cutoff,
            limit=batch,
        )

        # 6. AM-7 — board lifecycle: transition resolved clusters'
        #    board items to ``done`` (a BOARD-row write ONLY — never a
        #    cluster write; R5 forbids cluster status).
        board = self._board_service()
        if board is None:
            return
        pairs = await self._offload(
            self._repository.resolved_clusters_with_open_board_items,
            resolve_cutoff,
            limit=200,
        )
        for _cluster_id, item_id in pairs:
            try:
                await self._offload(
                    board.update_item,
                    item_id=item_id,
                    status="done",
                    author_instance=MAINTENANCE_AUTHOR_INSTANCE,
                )
            except Exception as exc:  # noqa: BLE001 — one item degrades
                logger.warning(
                    "anomaly: board done-transition failed for item %s: %s",
                    item_id,
                    redact_exc_str(exc, limit=200),
                )


    # ── the phase-3 READ half (ONE projector, TWO transports) ────────
    #
    # R8: the row-shaped reads go through THIS service — never a raw
    # engine handle, never direct repository access from a tool/route
    # body, never REST-scraping.  The nest-report-read TOOLS and the
    # /api/nest-reports route BOTH call these two projectors, so the
    # grouped projection cannot drift between the transports (AM-1).

    #: The read-page defaults + caps (the metrics-read house shape).
    DEFAULT_READ_LIMIT = 20
    MAX_READ_LIMIT = 50
    DEFAULT_WINDOW_S = 3600
    MAX_WINDOW_S = 7200  # WINDOW_SECONDS (metrics constants), restated

    def _validate_read_filters(
        self,
        *,
        window_s: int | None,
        severity: str | None,
        category: str | None,
        limit: int | None,
        offset: int,
    ) -> tuple[int, str | None, str | None, int, int]:
        """Clamp/validate the read filters (typed ``invalid-input``)."""
        if window_s is None:
            effective_window = self.DEFAULT_WINDOW_S
        else:
            if not isinstance(window_s, (int, float)) or isinstance(window_s, bool):
                raise AnomalyInputError(
                    "window_s must be a number of seconds",
                    detail="non-numeric window_s",
                )
            effective_window = int(window_s)
        effective_window = max(1, min(self.MAX_WINDOW_S, effective_window))
        if severity is not None:
            severities = _resolve_anomaly_severities()
            if not isinstance(severity, str) or severity not in severities:
                raise AnomalyInputError(
                    "severity must be one of minor | major | critical",
                    detail="out-of-set severity filter",
                )
        if category is not None:
            categories = _resolve_anomaly_categories()
            if not isinstance(category, str) or category not in categories:
                raise AnomalyInputError(
                    "category must be one of the closed canonical vocabulary",
                    detail="out-of-set category filter",
                )
        if limit is None:
            effective_limit = self.DEFAULT_READ_LIMIT
        else:
            if not isinstance(limit, int) or isinstance(limit, bool):
                raise AnomalyInputError(
                    "limit must be an integer", detail="non-integer limit"
                )
            effective_limit = limit
        effective_limit = max(1, min(self.MAX_READ_LIMIT, effective_limit))
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise AnomalyInputError(
                "offset must be a non-negative integer",
                detail="invalid offset",
            )
        return effective_window, severity, category, effective_limit, offset

    def _read_cluster_page(
        self,
        *,
        window_cutoff: str | None,
        severity: str | None,
        category: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        """The SYNC persistence half of the grouped read (runs OFF the
        event loop inside ONE offload — count + page + member fetch)."""
        matched = self._repository.count_cluster_groups(
            window_cutoff=window_cutoff,
            severity=severity,
            category=category,
        )
        page = self._repository.page_cluster_groups(
            window_cutoff=window_cutoff,
            severity=severity,
            category=category,
            limit=limit + 1,
            offset=offset,
        )
        more_available = len(page) > limit
        page = page[:limit]
        members: dict[tuple[str, str], list[tuple[str, str | None]]] = {}
        for tree_root, cat, _occ, _rep, _last, _peak in page:
            members[(tree_root, cat)] = self._repository.cluster_ids_for_group(
                tree_root_instance_id=tree_root,
                category=cat,
                limit=limit,
            )
        return {
            "matched": matched,
            "page": page,
            "members": members,
            "more_available": more_available,
        }

    async def list_clusters(
        self,
        *,
        window_s: int | None = None,
        severity: str | None = None,
        category: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        """THE grouped decision-ready projection (AM-6c) — ONE projector.

        The nest-report-read tools and ``GET /api/nest-reports/clusters``
        BOTH consume this dict; the grouping, the peak-of-peaks, the
        ``open`` derivation and the excerpt exclusion (AM-12a) live HERE
        so the two transports cannot drift.

        Raises:
            AnomalyInputError: an out-of-set filter (the caller maps
                the typed ``invalid-input`` refusal / ``400``).
        """
        effective_window, severity, category, effective_limit, offset = (
            self._validate_read_filters(
                window_s=window_s,
                severity=severity,
                category=category,
                limit=limit,
                offset=offset,
            )
        )
        now = _utc_now_iso()
        window_cutoff = _cutoff(effective_window)
        resolve_cutoff = _cutoff(self._config.resolve_window_seconds)
        data = await self._offload(
            self._read_cluster_page,
            window_cutoff=window_cutoff,
            severity=severity,
            category=category,
            limit=effective_limit,
            offset=offset,
        )

        groups: list[dict[str, Any]] = []
        breakdown: dict[str, dict[str, int]] = {}
        for tree_root, cat, occurrence, report_count, newest_last, peak in data[
            "page"
        ]:
            member_rows = data["members"].get((tree_root, cat), [])
            cluster_ids = [row[0] for row in member_rows]
            board_ids = [row[1] for row in member_rows if row[1] is not None]
            open_ = newest_last >= resolve_cutoff
            entry: dict[str, Any] = {
                "tree_root_instance_id": tree_root,
                "category": cat,
                "severity": peak,
                "severity_label": f"{peak} (peak)",
                "severity_rank": int(SEVERITY_RANK.get(peak, 1)),
                "report_count": report_count,
                "occurrence_count": occurrence,
                "cluster_ids": cluster_ids,
                "last_report_at": newest_last,
                "open": open_,
            }
            if board_ids:
                entry["board_item_ids"] = board_ids
            groups.append(entry)
            bucket = breakdown.setdefault(cat, {"groups": 0, "reports": 0})
            bucket["groups"] += 1
            bucket["reports"] += report_count

        matched = int(data["matched"])
        returned = len(groups)
        more_available = bool(data["more_available"])
        dropped = max(0, matched - returned)
        narrow_hint = None
        if more_available or dropped:
            narrow_hint = (
                "the group page was bounded — narrow the query (a smaller "
                "`window_s=`, a specific `severity=` or `category=`, or a "
                "smaller `limit`) or advance the cursor to read the rest."
            )
        return {
            "groups": groups,
            "bound": {
                "matched_count": matched,
                "returned_count": returned,
                "dropped_count": dropped,
                "more_available": more_available,
                "truncated": more_available or dropped > 0,
                "narrow_hint": narrow_hint,
            },
            "category_breakdown": dict(sorted(breakdown.items())),
            "freshness": {
                "window_s": effective_window,
                "window_cutoff_utc": window_cutoff,
                "queried_at_utc": now,
                "resolve_window_s": int(self._config.resolve_window_seconds),
                "line": (
                    f"clusters with a report in the last {effective_window}s "
                    f"as of {now} (server clock); open means last report "
                    f"within {int(self._config.resolve_window_seconds)}s"
                ),
            },
            "generated_at_utc": now,
        }

    async def list_cluster_reports(
        self,
        *,
        cluster_id: str,
        limit: int | None = None,
        include_excerpt: bool = False,
        offset: int = 0,
    ) -> dict[str, Any] | None:
        """ONE cluster's member reports, FRESHEST-FIRST (the detail read).

        Returns ``None`` when the cluster id matches nothing (the
        transports map that to their own typed miss — the tool's
        ``invalid-input`` refusal, the route's 404
        ``anomaly_cluster_not_found``).

        AM-12(b): the ``excerpt`` key appears on a member ONLY when
        ``include_excerpt=True`` — the default payload carries NO
        excerpt anywhere.
        """
        if not isinstance(cluster_id, str) or not cluster_id.strip():
            raise AnomalyInputError(
                "cluster_id must be a non-empty string",
                detail="empty cluster_id",
            )
        _w, _sev, _cat, effective_limit, offset = self._validate_read_filters(
            window_s=None,
            severity=None,
            category=None,
            limit=limit,
            offset=offset,
        )
        cluster = await self._offload(self._repository.get_cluster, cluster_id)
        if cluster is None:
            return None
        rows = await self._offload(
            self._repository.list_cluster_reports,
            cluster_id,
            limit=effective_limit + 1,
        )
        more_available = len(rows) > effective_limit
        rows = rows[:effective_limit]
        now = _utc_now_iso()
        resolve_cutoff = _cutoff(self._config.resolve_window_seconds)
        members: list[dict[str, Any]] = []
        for row in rows:
            member: dict[str, Any] = {
                "report_id": row["report_id"],
                "agent_id": row["agent_id"],
                "instance_id": row["instance_id"],
                "created_at": row["created_at"],
                "category": row["category"],
                "category_raw": row["category_raw"],
                "severity": row["severity"],
                "description": row["description"],
                "outcome": row["outcome"],
            }
            if include_excerpt:
                member["excerpt"] = row["excerpt"]
            members.append(member)
        total = int(cluster["report_count"])
        returned = len(members)
        dropped = max(0, total - returned)
        narrow_hint = None
        if more_available or dropped:
            narrow_hint = (
                "the member page was bounded — use a smaller `limit` or "
                "advance the cursor to read the rest of the cluster."
            )
        return {
            "cluster_id": cluster["id"],
            "category": cluster["category"],
            "severity": cluster["max_severity"],
            "severity_rank": int(SEVERITY_RANK.get(cluster["max_severity"], 1)),
            "severity_label": f"{cluster['max_severity']} (peak)",
            "report_count": total,
            "tree_root_instance_id": cluster["tree_root_instance_id"],
            "first_report_at": cluster["first_report_at"],
            "last_report_at": cluster["last_report_at"],
            "open": cluster["last_report_at"] >= resolve_cutoff,
            "board_item_id": cluster["board_item_id"],
            "reports": members,
            "bound": {
                "matched_count": total,
                "returned_count": returned,
                "dropped_count": dropped,
                "more_available": more_available,
                "truncated": more_available or dropped > 0,
                "narrow_hint": narrow_hint,
            },
            "freshness": {
                "queried_at_utc": now,
                "resolve_window_s": int(self._config.resolve_window_seconds),
                "line": (
                    f"cluster members as of {now} (server clock); open "
                    f"means last report within "
                    f"{int(self._config.resolve_window_seconds)}s"
                ),
            },
            "generated_at_utc": now,
        }

    # ── the evidence harvest (AM-8) ───────────────────────────────────

    async def harvest(self, *, instance_id: str, severity: str) -> AnomalyHarvest:
        """Collect the bounded evidence for one report.

        Every seam degrades ITS OWN field to NULL on failure; the
        harvest never refuses.
        """
        harvest = AnomalyHarvest()
        await self._harvest_tree(harvest, instance_id)
        await self._harvest_work_chain(harvest, instance_id)
        # AM-8b: ``families()`` runs IN-LINE — never inside an offload
        # worker (it takes the REGISTRY-LEVEL lock, and holding a
        # scarce 25-slot worker behind that lock is the deadlock the
        # amendment names).
        self._harvest_registry(harvest)
        self._harvest_boot_epoch(harvest)
        await self._harvest_wedge(harvest, instance_id)
        await self._harvest_excerpt(harvest, instance_id, severity)
        return harvest

    async def _harvest_tree(self, harvest: AnomalyHarvest, instance_id: str) -> None:
        """The ancestor chain + tree root (AM-8).

        The chain is PARENT → ROOT, SELF EXCLUDED (the helper's actual
        behaviour). The tree root is the LAST chain element, or the
        reporter itself when the chain is empty (+ a WARNING in the
        log — never in the report).
        """
        seams = self._resolve_seams()
        instance_repository = seams.instance_repository
        if instance_repository is None:
            return
        try:
            chain = await self._offload(
                instance_repository.get_ancestor_ids, instance_id
            )
        except Exception as exc:  # noqa: BLE001 — evidence loss, never a refusal
            logger.warning(
                "anomaly: ancestor chain harvest failed for %s: %s",
                instance_id,
                redact_exc_str(exc, limit=200),
            )
            return
        chain = [str(item) for item in (chain or [])]
        harvest.parent_chain = chain
        if chain:
            harvest.tree_root_instance_id = chain[-1]
        else:
            harvest.tree_root_instance_id = instance_id
            logger.warning(
                "anomaly: reporter %s has no ancestor chain; the tree root "
                "is the reporter itself",
                instance_id,
            )
            return
        try:
            labels = await self._offload(
                instance_repository.get_agent_ids_for, list(chain)
            )
        except Exception as exc:  # noqa: BLE001 — labels never block
            logger.warning(
                "anomaly: agent-label harvest failed for %s: %s",
                instance_id,
                redact_exc_str(exc, limit=200),
            )
            return
        harvest.parent_chain_agents = [
            str(labels.get(item) or "") for item in chain
        ]

    async def _harvest_work_chain(
        self, harvest: AnomalyHarvest, instance_id: str
    ) -> None:
        """The job / mission / dispatch chain (C4).

        The REAL three-table chain: ``Task`` → ``JobItem`` →
        ``MissionCommission``, SHORT-CIRCUITED (no running task ⇒ the
        rest is skipped). A partial chain leaves the missing limbs
        NULL.
        """
        seams = self._resolve_seams()
        task_repository = seams.task_repository
        if task_repository is None:
            return
        try:
            task = await self._offload(
                task_repository.find_running_by_instance, instance_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "anomaly: task harvest failed for %s: %s",
                instance_id,
                redact_exc_str(exc, limit=200),
            )
            return
        if task is None:
            return
        work_id = getattr(task, "work_id", None)
        if work_id:
            harvest.job_id = str(work_id)

        job_repository = seams.job_repository
        if job_repository is None:
            return
        try:
            job = await self._offload(
                job_repository.get_active_by_instance, instance_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "anomaly: job-item harvest failed for %s: %s",
                instance_id,
                redact_exc_str(exc, limit=200),
            )
            return
        if job is None:
            return
        metadata = getattr(job, "job_metadata", None) or {}
        try:
            chain = metadata.get(DISPATCH_CHAIN_KEY)
        except Exception:  # noqa: BLE001 — a hostile metadata shape
            chain = None
        if isinstance(chain, dict) and chain:
            harvest.dispatch_chain = chain

        mission_repository = seams.mission_repository
        if mission_repository is None:
            return
        try:
            commission = await self._offload(
                mission_repository.find_outstanding_commission_for_worker,
                instance_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "anomaly: mission harvest failed for %s: %s",
                instance_id,
                redact_exc_str(exc, limit=200),
            )
            return
        if commission is None:
            return
        mission_id = getattr(commission, "mission_id", None)
        if mission_id:
            harvest.mission_id = str(mission_id)

    def _harvest_registry(self, harvest: AnomalyHarvest) -> None:
        """The registry snapshot REFERENCE (AM-8b).

        ``metrics_query_provider.families()`` runs IN-LINE — never
        inside an offload worker (it takes the REGISTRY-LEVEL lock).
        Only the SHA-256 + the capture stamp are kept; the payloads are
        NEVER copied.
        """
        manager = self._manager
        provider = getattr(manager, "metrics_query_provider", None) if manager else None
        if provider is None:
            return
        try:
            summary = provider.families()
            payload = json.dumps(summary, sort_keys=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "anomaly: registry snapshot harvest failed: %s",
                redact_exc_str(exc, limit=200),
            )
            return
        harvest.registry_summary_sha256 = hashlib.sha256(
            payload.encode("utf-8")
        ).hexdigest()
        harvest.registry_captured_at = _utc_now_iso()

    def _harvest_boot_epoch(self, harvest: AnomalyHarvest) -> None:
        """The boot epoch (AM-8d) — naive-UTC, stored as-is."""
        try:
            from daemon.services.boot_epoch import get_boot_epoch

            epoch = get_boot_epoch()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "anomaly: boot-epoch harvest failed: %s",
                redact_exc_str(exc, limit=200),
            )
            return
        if epoch is None:
            return
        if epoch.tzinfo is not None:
            epoch = epoch.astimezone(timezone.utc).replace(tzinfo=None)
        harvest.boot_epoch = epoch.isoformat()

    async def _harvest_wedge(
        self, harvest: AnomalyHarvest, instance_id: str
    ) -> None:
        """The wedge cross-link (AM-8d).

        A capture matches when its ``captured_at`` is within
        +/- ``wedge_match_window_seconds`` of the report AND its
        ``boot_epoch`` equals the report's — both sides canonicalized
        to naive-UTC before the compare (the wedge subsystem writes
        two different shapes). The epoch leg is SKIPPED when the
        report's own epoch is unknown (``own_epoch is None`` — the
        permissive fallback: a time-window match survives a failed
        epoch harvest). Only the capture IDS are stored; the payloads
        are never read.
        """
        seams = self._resolve_seams()
        wedge_repository = seams.wedge_repository
        if wedge_repository is None:
            return
        try:
            captures = await self._offload(wedge_repository.list_recent, 50)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "anomaly: wedge cross-link harvest failed: %s",
                redact_exc_str(exc, limit=200),
            )
            return
        if not captures:
            return
        report_time = _parse_iso(_utc_now_iso())
        window = float(self._config.wedge_match_window_seconds)
        own_epoch = canonical_boot_epoch(harvest.boot_epoch)
        matched: list[str] = []
        for capture in captures:
            captured_at = _parse_iso(getattr(capture, "captured_at", None))
            if captured_at is None or report_time is None:
                continue
            if abs((captured_at - report_time).total_seconds()) > window:
                continue
            capture_epoch = canonical_boot_epoch(
                getattr(capture, "boot_epoch", None)
            )
            if own_epoch is not None and capture_epoch != own_epoch:
                continue
            capture_id = getattr(capture, "capture_id", None)
            if capture_id:
                matched.append(str(capture_id))
        if matched:
            harvest.wedge_capture_ids = matched

    async def _harvest_excerpt(
        self, harvest: AnomalyHarvest, instance_id: str, severity: str
    ) -> None:
        """The transcript excerpt (AM-8a / AM-8e / C5).

        ``minor`` severity SKIPS the transcript read entirely — no
        harvest, no column, no cost.

        The read is BOUNDED AT THE READ SEAM: the checkpoint state is
        materialized and the tail slice (``messages[-20:]``) is the
        only window that is ever examined. A post-read trim would be a
        RED (the O(N^2) walk family).
        """
        if severity == SEVERITY_MINOR:
            return
        manager = self._manager
        checkpointer = getattr(manager, "checkpointer", None) if manager else None
        raw_saver = getattr(checkpointer, "raw_saver", None) if checkpointer else None
        if raw_saver is None:
            return
        try:
            state = await raw_saver.aget(
                {"configurable": {"thread_id": instance_id}}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "anomaly: transcript read failed for %s: %s",
                instance_id,
                redact_exc_str(exc, limit=200),
            )
            return
        messages = (
            (state or {}).get("channel_values", {}).get("messages", []) or []
        )
        window = messages[-MAX_TRANSCRIPT_WINDOW:]
        parts = select_error_excerpt(
            window,
            max_messages=self._config.excerpt_max_messages,
            max_chars_per_message=self._config.excerpt_max_chars_per_message,
        )
        if not parts:
            return
        scrubbed_parts = [
            scrub_text(part, self._config.excerpt_max_chars_per_message)
            for part in parts
        ]
        excerpt = "\n".join(scrubbed_parts)[: self._config.excerpt_max_total_chars]
        if not excerpt:
            return
        if get_process_scrubber() is None:
            # AM-11 SPLIT posture — the excerpt is FAIL-CLOSED: raw
            # tool stdout is the highest-risk field and the exact-match
            # scrubber knows only STORED values.
            logger.warning(
                "anomaly: excerpt withheld — the value scrubber is absent "
                "(fail-closed); the report persists without it"
            )
            return
        harvest.excerpt = excerpt
        harvest.excerpt_sha256 = hashlib.sha256(
            excerpt.encode("utf-8")
        ).hexdigest()
        harvest.excerpt_length = len(excerpt)

    # ── seams ─────────────────────────────────────────────────────────

    def _resolve_seams(self) -> Any:
        """Resolve the evidence seams (cached).

        The manager's own repositories are preferred (one source of
        truth); a missing manager attribute falls back to a repository
        bound to the same engine. A seam that cannot be resolved at all
        stays ``None`` and its field degrades to NULL.
        """
        if self._seams is not None:
            return self._seams
        manager = self._manager
        engine = self._engine

        instance_repository = (
            getattr(manager, "_instance_repository", None) if manager else None
        )
        if instance_repository is None and engine is not None:
            try:
                from daemon.repositories.instance.repository import (
                    SQLModelInstanceRepository,
                )

                instance_repository = SQLModelInstanceRepository(engine)
            except Exception:  # noqa: BLE001 — a missing seam degrades
                instance_repository = None

        task_repository = getattr(manager, "_task_repo", None) if manager else None
        if task_repository is None and engine is not None:
            try:
                from daemon.repositories.task.repository import TaskRepository

                task_repository = TaskRepository(engine)
            except Exception:  # noqa: BLE001
                task_repository = None

        job_repository = None
        if manager is not None:
            job_service = getattr(manager, "_job_queue_service", None)
            job_repository = getattr(job_service, "repository", None)
        if job_repository is None and engine is not None:
            try:
                from daemon.repositories.job_queue.repository import JobRepository

                job_repository = JobRepository(engine)
            except Exception:  # noqa: BLE001
                job_repository = None

        mission_repository = None
        if manager is not None:
            mission_service = getattr(manager, "_mission_record_service", None)
            accessor = getattr(mission_service, "commission_repository", None)
            if callable(accessor):
                try:
                    mission_repository = accessor()
                except Exception:  # noqa: BLE001
                    mission_repository = None
        if mission_repository is None and engine is not None:
            try:
                from daemon.repositories.mission_record.repository import (
                    MissionRecordRepository,
                )

                mission_repository = MissionRecordRepository(engine)
            except Exception:  # noqa: BLE001
                mission_repository = None

        wedge_repository = (
            getattr(manager, "_wedge_capture_repository", None) if manager else None
        )
        if wedge_repository is None and engine is not None:
            try:
                from daemon.repositories.wedge_captures.repository import (
                    WedgeCaptureRepository,
                )

                wedge_repository = WedgeCaptureRepository(engine)
            except Exception:  # noqa: BLE001
                wedge_repository = None

        self._seams = _Seams(
            instance_repository=instance_repository,
            task_repository=task_repository,
            job_repository=job_repository,
            mission_repository=mission_repository,
            wedge_repository=wedge_repository,
        )
        return self._seams


__all__ = [
    "CATEGORY_RAW_MAX_CHARS",
    "CATEGORY_SYNONYMS",
    "MAINTENANCE_AUTHOR_INSTANCE",
    "MAX_TRANSCRIPT_WINDOW",
    "SCRUB_ACTOR",
    "STRIPE_SLOTS",
    "TOKEN_CAPACITY_EXHAUSTED",
    "TOKEN_RATE_LIMITED",
    "AnomalyAcceptResult",
    "AnomalyHarvest",
    "AnomalyInputError",
    "AnomalyRefusalError",
    "AnomalyService",
    "canonical_boot_epoch",
    "scrub_text",
    "select_error_excerpt",
]
