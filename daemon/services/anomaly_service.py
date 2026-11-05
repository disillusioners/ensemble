"""The nest-anomaly ACCEPT path — validate → normalize → scrub → harvest → persist.

The write-side half of the ``nest-report`` tool category (the
``nest-anomaly-report`` lane, board ``wi-2d691ed2``). Phase 1 (commit
C1) ships this service with the accept path ONLY:

    validate → normalize (R1/R3) → scrub (R6 + AM-11) → harvest (AM-8)
    → persist

CLUSTERING, RATE LIMITS, THE BOARD SINK and RETENTION land in phase 2
(commit C3) — the phase-1 accept path therefore writes the DEGENERATE
cluster tail: ``cluster_id=NULL``, ``cluster_report_count=1``,
``cluster_first_report_at=created_at``. The exposure window is bounded
to this lane (phases 1-2 stage TOGETHER; no interim promote exists).

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

THE DB SEAMS RUN OFF-LOOP (AM-8)
--------------------------------
Every DB-touching seam goes through ``db_offload.offload`` — the
v0.27.14 discipline (a sync DB read on the event loop is the incident
class). ONE exception, pinned by AM-8b: ``metrics_query_provider
.families()`` runs IN-LINE, never inside an offload worker — it takes
the REGISTRY-LEVEL lock, and holding a scarce 25-slot worker behind
that lock is the deadlock the amendment names.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from daemon._redact import redact_exc_str, redact_secret_shapes
from daemon.config import NestReportConfig
from daemon.repositories.anomaly.models import (
    OUTCOME_ACCEPTED,
    SEVERITY_MAJOR,
    SEVERITY_MINOR,
    AnomalyReport,
)
from daemon.repositories.anomaly.repository import AnomalyRepository
from daemon.services.db_offload import offload as _db_offload
from daemon.services.job_queue_service import DISPATCH_CHAIN_KEY
from daemon.services.metrics_query.wiring.vocab import (
    ANOMALY_CATEGORIES,
    ANOMALY_SEVERITIES,
    RESERVED,
)
from daemon.services.secret_persistence_scrub import (
    get_process_scrubber,
    scrub_enqueued_body,
)

logger = logging.getLogger(__name__)

#: The scrub actor recorded on the value-substitution audit rows.
SCRUB_ACTOR = "nest_report"

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
CATEGORY_SYNONYMS: dict[str, str] = {
    "tool failure": "tool-failure",
    "tool-failures": "tool-failure",
    "tool-error": "tool-failure",
    "tool_error": "tool-failure",
    "toolerror": "tool-failure",
    "perf": "performance",
    "slow": "performance",
    "latency": "performance",
    "resource-exhausted": "resource-exhaustion",
    "resource exhaustion": "resource-exhaustion",
    "oom": "resource-exhaustion",
    "out-of-memory": "resource-exhaustion",
    "loop-stall": "loop-stall",
    "loop": "loop-stall",
    "stuck-loop": "loop-stall",
    "wedge": "wedge-suspected",
    "wedge-suspect": "wedge-suspected",
    "wedged": "wedge-suspected",
    "corruption": "data-corruption",
    "data-corrupt": "data-corruption",
    "corrupt-data": "data-corruption",
    "auth": "auth-permission",
    "permission": "auth-permission",
    "auth-permissions": "auth-permission",
    "unauthorized": "auth-permission",
    "external": "external-dependency",
    "external-dependencies": "external-dependency",
    "dependency-failure": "external-dependency",
    "mcp": "mcp-failure",
    "mcp-failures": "mcp-failure",
    "mcp-error": "mcp-failure",
    "llm": "llm-provider",
    "llm-failures": "llm-provider",
    "provider-error": "llm-provider",
    "queue": "queue-scheduling",
    "scheduling": "queue-scheduling",
    "queue-scheduling-delay": "queue-scheduling",
    "config": "environment-config",
    "environment": "environment-config",
    "misconfiguration": "environment-config",
    "env-config": "environment-config",
}


class AnomalyInputError(ValueError):
    """The authored content failed validation (the ``invalid-input`` token).

    Raised BEFORE anything is persisted — an invalid-input refusal
    writes NO row (the refusal ladder's second rung).
    """

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


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
    max_total_chars: int,
) -> list[str]:
    """The C5 selection rule — the SINGULAR excerpt rule.

    "The excerpt = the last ≤5 ERROR-BEARING tool messages in the turn,
    selected from a bounded scan window of the last ≤20 messages of
    that turn's transcript; each excerpt ≤500 chars, total ≤2000."

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
        max_total_chars: The total character cap across the excerpts.

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
    checkpointer, the metrics query provider). A missing seam degrades
    its field to NULL — it never fails the report.
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
        self._engine = engine
        self._config = config or NestReportConfig()
        self._manager = manager
        self._repository = repository or AnomalyRepository(engine)
        self._offload = offload or _db_offload
        self._seams: Any = None

    # ── configuration access ──────────────────────────────────────────

    @property
    def config(self) -> NestReportConfig:
        return self._config

    @property
    def repository(self) -> AnomalyRepository:
        return self._repository

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
        text = (raw or "").strip().lower()
        canonical = RESERVED
        if text:
            if text in ANOMALY_CATEGORIES:
                canonical = text
            else:
                canonical = CATEGORY_SYNONYMS.get(text, RESERVED)
        if canonical == text:
            return canonical, None
        return canonical, f'category "{text}" folded to "{canonical}"'

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
        if text not in ANOMALY_SEVERITIES:
            raise AnomalyInputError(
                f"severity must be one of {'|'.join(ANOMALY_SEVERITIES)}",
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
        report_id = await self._offload(self._repository.insert_report, row)
        return AnomalyAcceptResult(
            report_id=report_id,
            category=canonical,
            category_raw=raw_category,
            severity=severity_value,
            fold_notice=fold_notice,
            truncated=truncated,
            cluster_id=None,
            cluster_report_count=1,
            cluster_first_report_at=created_at,
            excerpt_sha256=harvest.excerpt_sha256,
            excerpt_length=harvest.excerpt_length,
            outcome=OUTCOME_ACCEPTED,
        )

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
        if not chain:
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
        two different shapes). Only the capture IDS are stored; the
        payloads are never read.
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
            max_total_chars=self._config.excerpt_max_total_chars,
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
    "MAX_TRANSCRIPT_WINDOW",
    "SCRUB_ACTOR",
    "AnomalyAcceptResult",
    "AnomalyHarvest",
    "AnomalyInputError",
    "AnomalyService",
    "canonical_boot_epoch",
    "is_error_bearing",
    "scrub_text",
    "select_error_excerpt",
]
