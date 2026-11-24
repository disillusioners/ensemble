"""POK tools — project operating knowledge write/read/curate (Phase 1 T4).

Four tools (arch v3.4 §4.2 / §4.3 / §4.6; category ``pok``,
NON-privileged — grants flow through the per-agent ``tools.allow``
entries landed by the B4 sweep; authorization differences are
IN-BODY role gates from the FACTORY-CAPTURED ``agent_id``
(attribution ≠ authorization — the two-layer gate)):

* :func:`pok_upsert` — any agent on a non-default project writes;
  the writer STATES the audience classification at capture
  (default ``operation`` — the channel's genre) and MAY state the
  trust tier (Phase 2, addendum T-1: ``source_tier`` default
  ``agent``; ``curated`` is leader/PM-only). Write-time fence +
  escape-aware budget + exact-dup echo + dispute-laundering guard +
  supersede transaction + admission caps (active-entry
  refuse-never-evict; family >3). Supersede-of-PINNED or
  -of-CURATED is leader/PM-only (the guard predicate widened in
  Phase 2, addendum T-3; an omitted tier on supersede INHERITS the
  target's tier).
* :func:`pok_query` — ONE step, project-scoped, selector-reuse.
  UNIVERSAL READ INVARIANT (ER-1.4, generalized by addendum T-4):
  NO audience filter and NO tier filter — every agent on the
  project reads every entry; audience and source_tier gate ACTIVE
  injection only. Family chains in slug order; pinned bypass;
  typed errors; embedding lag surfaced; ``[CURATED]`` renders in
  the lineage surface only (no in-block marker in v1).
* :func:`pok_curate` — leader/PM only (pinned | disputed | archived
  | audience | audience_agents | source_tier | basis — the tier flip is
  Phase 2, both directions, addendum T-3). Pin cap 2 PER PROJECT;
  disputed suppresses injection but stays readable; archived
  forward-only AND requires a RULED BASIS (pok-write-gate Phase 1,
  T1.6) — consolidation happens only under a ruled basis, never on
  age and never on access counts alone. The archive basis is
  LEDGERED (pok-write-gate Phase 8, T8.17).
* :func:`pok_gate_answer` — the G1 answer path (pok-write-gate Phase
  8, T8.5): a dedicated tool that answers a pending write-gate ASK.
  The answer carries NO body — the pending entry stores the
  adjudicated stripped body + all the upsert inputs, and the answer
  re-runs the write with the gate bypassed so the scope/fence/
  budget/no-op/caps checks re-run at answer time.
* :func:`pok_census` — the census READ surface (pok-write-gate
  Phase 9, T9.1): leader / project-manager only. It returns the
  per-store metrics the POK librarian maintains the store from (the
  shape, the families, the authors, the tiers, the retrieval legs the
  librarian TRUSTS, the never-retrieved / never-verified
  populations) plus the derived soft max and its drift. Read-only.

WRITE GATE (pok-write-gate Wave 2, Phase 7/8): the tool layer is the
EXECUTOR — the gate NEVER writes (design-amendments-v2 §8, authority
split, BINDING). The tool builds the ``gate_fn`` the service calls at
its gate slot, applies the verdict (write / supersede / no-write /
ask), and the audit records BOTH the verdict and the execution.

PROJECT SCOPING (server-side): every tool resolves the CALLING
instance's project from the instance row (the
``self_alarm._resolve_project_id`` precedent) — there is no
``project_id`` tool argument, so an agent cannot scope a call at a
project it is not linked to. The default project is refused with
typed errors BOTH directions (A-5, AC10).

Recording surface: typed echo + ``[POK]`` logger lines (the daemon
log is what ``ens_system_log`` reads).

Engine discipline (memoir): the factory reads ONLY
``manager._engine`` and ``manager._instance_repository``, both
defensively — a test double without them yields typed refusals,
never raises. The service/repository never build an engine.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Mapping, Sequence


from ._schema_registry import tool
from ..repositories.pok.models import POK_ARCHIVE_BASES, POKWriteGateLedger
from ..repositories.pok.repository import POKRepository
from ..services.pok_gate import (
    DEFAULT_MERGE_POLICY,
    ActiveRow,
    GateVerdict,
    PendingGate,
    PendingGateStore,
    POKGateUnavailable,
    VerdictCache,
    classify_answer,
    family_template,
    judge_pok_write,
)
from ..services.boot_subprocess_sweep import is_prod_lane
from ..services.pok_service import (
    POKError,
    POK_ACTIVE_CAP_PER_PROJECT,
    POKService,
    AskUpsertOutcome,
    is_default_project,
    is_curator,
)
from ._tool_registry import register_tool_category

__all__ = ["CATEGORY_DOC", "CATEGORY_NAME", "create_pok_tools"]

logger = logging.getLogger(__name__)

CATEGORY_NAME = "pok"

#: The two SIBLING enforcement switches (design-amendments-v2 §13.3
#: item 5). Both are DB-backed settings keys on the ``settings:write``
#: surface — NOT env (an env flip is a live config change at deploy +
#: restart) — and BOTH default OFF at build.
POK_GATE_SEMANTIC_ENFORCEMENT_KEY = "pok_gate.semantic_enforcement"
POK_GATE_SOFTMAX_TIGHTENING_KEY = "pok_gate.softmax_tightening"
#: The F2 merge dial — the SAME switch family as the two enforcement
#: switches (the architecture places it "on the same switch family as
#: enforcement" so the P1 ceremony moves the dial, not a rebuild).
POK_GATE_MERGE_POLICY_KEY = "pok_gate.merge_policy"

#: The process-wide verdict cache (bounded LRU — T7.8). Process-local
#: and NOT persisted: the ledger is the durable record.
_GATE_CACHE = VerdictCache()

#: The process-wide PENDING-ASK store (T8.6 — in-process, owner-scoped,
#: TTL-bounded; NO live-store writes for the pending state).
_PENDING_GATES = PendingGateStore()

#: The six typed refusals of the answer path (T8.7) — the message each
#: one carries. A refusal NEVER writes and NEVER defaults to accept.
_ANSWER_REFUSAL_MESSAGES: dict[str, str] = {
    "pok_gate_unknown": (
        "no pending gate with that id — it was never minted, it already "
        "completed, or the daemon restarted (the pending state is "
        "in-process only)"
    ),
    "pok_gate_expired": (
        "the pending gate expired — nothing was written; re-issue the "
        "write"
    ),
    "pok_gate_already_answered": (
        "the pending gate was already answered — nothing was written"
    ),
    "pok_gate_not_owner": (
        "the pending gate belongs to another agent — only the agent "
        "that made the pending write may answer it"
    ),
    "pok_gate_answer_required": (
        "an answer is required — pass the answer in plain words"
    ),
    "pok_gate_answer_unrecognized": (
        "the answer could not be classified — answer in plain words"
    ),
}
CATEGORY_DOC = (
    "Project operating knowledge (POK) — ordered operating procedures "
    "per project. pok_upsert writes a fenced single-paragraph fact "
    "(stating its audience: operation | implementation | both; "
    "optionally source_tier: agent | curated — curated is leader / "
    "project-manager only); pok_query reads ALL entries of the "
    "project (universal read — audience and tier never filter a "
    "read); pok_curate (leader / project-manager only) pins, "
    "disputes, archives, reclassifies audience, or flips the trust "
    "tier; pok_gate_answer answers a pending write-gate confirmation."
)


def _resolve_project_id(instance_repository: Any, current_instance_id: str) -> str | None:
    """Resolve the caller's project from the instance row (the
    ``self_alarm._resolve_project_id`` precedent, mirrored)."""
    if instance_repository is None or not current_instance_id:
        return None
    try:
        instance = instance_repository.get(current_instance_id)
    except Exception as exc:  # noqa: BLE001 — degraded read REFUSES, never scopes wrong
        logger.warning(
            "pok: cannot resolve project_id for instance %s: %s",
            current_instance_id,
            exc,
        )
        return None
    if instance is None:
        return None
    project_id = getattr(instance, "project_id", None)
    return str(project_id) if project_id else None


def _project_name(instance_repository: Any, project_id: str | None) -> str | None:
    """The project's name (the is_system_default name fallback needs it)."""
    if instance_repository is None or not project_id:
        return None
    project_repo = getattr(instance_repository, "_project_repository", None)
    if project_repo is None:
        return None
    try:
        project = project_repo.get(project_id)
    except Exception:  # noqa: BLE001 — defensive
        return None
    return getattr(project, "name", None) if project is not None else None


def _err(code: str, message: str, **details: Any) -> dict:
    payload = {"error": message, "code": code}
    payload.update({k: v for k, v in details.items() if v is not None})
    return payload


# ── the write-gate helpers (pok-write-gate Phase 7/8) ─────────────────────


def _as_bool(value: Any) -> bool:
    """A tolerant truthiness read for a settings override."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def read_gate_switches(engine: Any) -> tuple[bool, bool]:
    """Read the two SIBLING gate switches (T8.11).

    Returns ``(semantic_enforcement, softmax_tightening)``. Both are
    DB-backed settings keys (the ``settings:write`` surface — NOT env:
    an env flip is a live config change at deploy + restart) and BOTH
    default OFF at build.

    A degraded read (no engine, a missing table, a settings service
    that cannot start) reads as OFF — the fail-open direction is the
    SHADOW direction, and shadow never refuses a write.
    """
    if engine is None:
        return (False, False)
    try:
        from ..services.settings_service import SettingsService  # noqa: PLC0415

        overrides = SettingsService(engine=engine).get_overrides()
    except Exception as exc:  # noqa: BLE001 — degraded read ⇒ OFF
        logger.warning(
            "[POK] gate switch read failed (%s) — the gate stays in SHADOW",
            type(exc).__name__,
        )
        return (False, False)
    return (
        _as_bool(overrides.get(POK_GATE_SEMANTIC_ENFORCEMENT_KEY)),
        _as_bool(overrides.get(POK_GATE_SOFTMAX_TIGHTENING_KEY)),
    )


# ── the refusal ROUTE TABLE (pok-write-gate Phase 9, T9.2/T9.3) ───────────
#
# The per-lane refusal TRANSITION MAP: what class of content a refusal
# names, WHERE that content belongs instead, and the tool CATEGORY
# that provides the destination (the grant-check inputs). Every
# rejection carries its route, so the caller is never left with a
# refusal and no home (R5: "a rejection with no home is itself a
# defect").
#
# The map is the guideline's per-class routing table (its §4 table)
# expressed in the gate's own vocabulary: knowledge → RAG, operational
# → POK, governance → the curated note surface / the board, episode →
# the board / the KV.
CONTENT_CLASS_ROUTES: dict[str, dict[str, Any]] = {
    "knowledge": {
        "route": "rag",
        "destination": "the knowledge base (RAG)",
        "categories": ("rag",),
        "instruction": (
            "capture it with the knowledge-base tool (rag_insert_text) — "
            "POK holds this project's operating how-to, not general "
            "knowledge"
        ),
    },
    "operational": {
        "route": "pok",
        "destination": "POK (this project)",
        "categories": ("pok",),
        "instruction": (
            "state the operational invariant the fact protects, then write "
            "it again"
        ),
    },
    "governance": {
        "route": "critical_notes_or_board",
        "destination": "the critical notes (a curated, pinned surface) or the board",
        "categories": ("critical_notes", "board"),
        "instruction": (
            "a curated project contract belongs in the critical notes; a "
            "human-facing project state belongs on the board"
        ),
    },
    "episode": {
        "route": "board_or_kv",
        "destination": "the board (a human-facing ledger) or the shared KV",
        "categories": ("board", "shared_meta_kv"),
        "instruction": (
            "a snapshot or an episode does not belong in a durable store — "
            "put it on the board (or the shared KV) and keep only the "
            "invariant here"
        ),
    },
}

#: The per-FAMILY map: which content class the family's rejection
#: names. The route + the destination + the grant categories come from
#: :data:`CONTENT_CLASS_ROUTES`.
REFUSAL_FAMILY_CLASSES: dict[str, str] = {
    "boundary": "knowledge",
    "reason": "operational",
    "durability": "episode",
    "redundancy": "operational",
    "scope": "operational",
}

#: The family-specific instruction (the route's own instruction is the
#: class-level default).
_REFUSAL_FAMILY_INSTRUCTIONS: dict[str, str] = {
    "reason": (
        "rewrite the reason so it names the operational invariant the fact "
        "protects, then write it again"
    ),
    "redundancy": (
        "supersede the named ACTIVE row with the better body instead of "
        "adding a second row for one fact"
    ),
    "scope": "write it to the project it belongs to, not to this one",
}


def route_for_family(family: str | None) -> dict[str, Any]:
    """The route a refusal family names (T9.2/T9.3).

    The route is the CONTENT CLASS's route (the transition map) plus
    the family's own instruction. A family outside the closed set
    falls back to the operational route — the envelope is never empty.
    """
    content_class = REFUSAL_FAMILY_CLASSES.get(family or "", "operational")
    base = CONTENT_CLASS_ROUTES[content_class]
    return {
        "content_class": content_class,
        "route": base["route"],
        "destination": base["destination"],
        "categories": base["categories"],
        "instruction": _REFUSAL_FAMILY_INSTRUCTIONS.get(
            family or "", base["instruction"]
        ),
    }


def _agent_tool_grants(agent_id: str) -> list[str] | None:
    """The agent's ``tools.allow`` list — ``None`` means UNRESTRICTED.

    The registry pattern (ALL meta lookups use ``get_version`` with a
    ``get_resolved`` fallback). An empty/absent allow list means the
    agent inherits the whole universe (the F1b semantics), so it
    resolves to ``None`` here — "everything is reachable".
    """
    if not agent_id:
        return None
    try:
        from ..registry import get_registry  # noqa: PLC0415 — cycle-neutral

        registry = get_registry()
        meta = registry.get_version(agent_id) or registry.get_resolved(agent_id)
        if meta is None:
            return None
        tools = getattr(meta, "tools", None)
        allow = getattr(tools, "allow", None) if tools is not None else None
        if not allow:
            return None
        return [str(entry) for entry in allow]
    except Exception as exc:  # noqa: BLE001 — a degraded read ⇒ unrestricted
        logger.warning(
            "[POK] grant read failed for %r (%s) — the destination check "
            "falls back to 'available'",
            agent_id,
            type(exc).__name__,
        )
        return None


def destination_available(agent_id: str, categories: Any) -> bool:
    """Is the named destination REACHABLE by this agent? (T9.3)

    The grant-check behind the rejection envelope: a refusal that
    names a home the caller cannot reach is a SILENT DROP unless the
    content is parked (T9.4). The check reads the agent's
    ``tools.allow`` grant list — an unrestricted agent (no allow
    list) reaches everything.
    """
    if not categories:
        return True
    wanted = [str(c) for c in categories]
    allow = _agent_tool_grants(agent_id)
    if allow is None:
        return True
    return any(category in allow for category in wanted)


# ── the near-soft-max TIGHTENING read (pok-write-gate Phase 10, T10.6) ────


def _tightening_payload(
    engine: Any, project_id: str, *, enabled: bool
) -> dict[str, Any] | None:
    """Read the tightening decision for ONE write (T10.6).

    The soft max is read at WRITE TIME by one unique-index SELECT
    (the I1 placement) — NEVER memoised in-process (a memo would
    serve a stale ceiling to the admission path). The active count
    comes from the same store the upsert already reads.

    ``enabled=False`` (the ``pok_gate.softmax_tightening`` switch is
    OFF — the build default) short-circuits: no read, no tightening.
    """
    if not enabled or engine is None:
        return None
    try:
        from ..services.pok_librarian import (  # noqa: PLC0415 — cycle-neutral
            read_soft_max,
            tightening_decision,
        )

        state = read_soft_max(engine, project_id)
        if state is None:
            return None
        active_count = POKRepository(engine).count_active(project_id)
        return tightening_decision(
            active_count=active_count, soft_max=state.value
        ).to_payload()
    except Exception as exc:  # noqa: BLE001 — a degraded read ⇒ no tightening
        logger.warning(
            "[POK] tightening read failed for project %s (%s) — the write "
            "is not tightened",
            project_id,
            type(exc).__name__,
        )
        return None


# ── the PARKING seam (pok-write-gate Phase 9, T9.4) ───────────────────────#
# ZERO SILENT DROPS: at enforcement a refused write whose destination
# the caller cannot reach would vanish. The parking seam RETAINS it:
# a ledger receipt with the ``park:`` gate-id prefix carries the
# content + the reason, so the fact is never lost and the curator can
# route it later. The seam is P1-READY (the surface exists and is
# exercised); the ENFORCEMENT flip is the Emperor's ceremony.
PARKING_GATE_ID_PREFIX = "park:"
PARKING_BODY_LIMIT = 900


def parking_id(project_id: str, slug: str, body: str, reason: str) -> str:
    """The stable id of a parked write (idempotent per content)."""
    import hashlib

    digest = hashlib.sha256(
        f"{project_id}\x00{slug}\x00{body}\x00{reason}".encode("utf-8")
    ).hexdigest()
    return f"{PARKING_GATE_ID_PREFIX}{digest[:24]}"


def park_rejected_write(
    engine: Any,
    *,
    project_id: str,
    slug: str,
    body: str,
    reason: str,
    family: str | None,
    route: Mapping[str, Any],
    agent_id: str,
) -> str | None:
    """PARK a refused write whose destination is unreachable (T9.4).

    Appends the parking receipt to the ledger (the only mutator on
    that surface). Idempotent: the ``park:`` id is derived from the
    content, and the DB-level gate-chain unique index makes a replay
    a no-op (the duplicate error is swallowed — the receipt is
    already there).

    Returns the parking id, or ``None`` when the receipt could not be
    written (the envelope then says the write was NOT retained — the
    caller must be told the truth).
    """
    if engine is None:
        return None
    pid = parking_id(project_id, slug, body, reason)
    excerpt = (body or "")[:PARKING_BODY_LIMIT]
    explanation = (
        "PARKED — the write gate refused this fact (family=%s) and its "
        "destination (%s) is not available to this agent, so the content "
        "is retained here instead of being dropped: %s"
        % (family or "unknown", route.get("destination", "unknown"), excerpt)
    )
    try:
        POKRepository(engine).append_ledger(
            POKWriteGateLedger(
                project_id=project_id,
                gate_id=pid,
                phase="ADMISSION",
                candidate_slug=slug,
                verdict="REJECT",
                reason=(reason or "")[:200] or None,
                judge_explanation=explanation,
                basis="gate-rejection",
                executed=False,
                agent_id=agent_id or "unknown",
            )
        )
        return pid
    except Exception as exc:  # noqa: BLE001 — best-effort, never raises
        if "UQ_POK_LEDGER_GATE_CHAIN" in str(exc).upper() or "GATE_ID" in str(exc).upper():
            # Already parked (the content is retained) — idempotent.
            return pid
        logger.warning(
            "[POK] parking receipt failed for %r (%s) — the refused write "
            "is NOT retained",
            slug,
            type(exc).__name__,
        )
        return None


def active_rows_for_digest(engine: Any, project_id: str) -> list[ActiveRow]:
    """The project's ACTIVE rows, projected for the judge digest.

    The judge NEVER sees a live store — it sees this bounded
    projection (T7.9).
    """
    if engine is None:
        return []
    try:
        rows = POKRepository(engine).list_entries(project_id, statuses=("active",))
    except Exception as exc:  # noqa: BLE001 — a degraded digest ⇒ empty
        logger.warning(
            "[POK] digest read failed (%s) — the judge sees an empty digest",
            type(exc).__name__,
        )
        return []
    return [
        ActiveRow(
            slug=row.slug,
            body=row.body,
            family=_slug_family_of(row.slug),
            pinned=bool(row.pinned),
            source_tier=row.source_tier or "agent",
            verify_count=int(row.verify_count or 0),
            last_verified=row.last_verified,
        )
        for row in rows
    ]


def merge_policy_override() -> str | None:
    """The F2 merge-policy dial (``None`` = the default).

    The dial lives on the same DB-backed switch family as the
    enforcement switches (``pok_gate.merge_policy``); an absent row
    reads as the DEFAULT (``ASK_WITH_PROPOSAL``).
    """
    from ..services.settings_service import (  # noqa: PLC0415
        MANAGED_KEYS,
        SettingsService,
    )

    spec = MANAGED_KEYS.get(POK_GATE_MERGE_POLICY_KEY)
    default = spec.default if spec is not None else "ASK_WITH_PROPOSAL"
    return default


def _resolve_active_row(engine: Any, project_id: str, slug: str) -> Any:
    """The ACTIVE row that owns a slug (``None`` when there is none)."""
    if engine is None or not slug:
        return None
    try:
        return POKRepository(engine).resolve_slug_row(
            project_id, slug, scope="active"
        )
    except Exception:  # noqa: BLE001 — defensive
        return None


def _stamp_target_verification(
    engine: Any, project_id: str, slug: str
) -> bool:
    """Stamp the merge TARGET's verification (the no-delta merge).

    This is the FIRST REAL WRITER of ``verify_count`` (design-
    amendments-v2 §6 / §9 "close the loop"): the gate's own activity
    stamps the metric the librarian's next pass reads. The target row
    is the one that carries the fact — the stamp says "this row was
    re-verified by the gate".
    """
    row = _resolve_active_row(engine, project_id, slug)
    if row is None:
        return False
    try:
        return POKRepository(engine).record_verification(row.id)
    except Exception as exc:  # noqa: BLE001 — best-effort stamp
        logger.warning(
            "[POK] verify stamp on %r failed (%s) — the merge verdict "
            "is unaffected",
            slug,
            type(exc).__name__,
        )
        return False


def _gate_envelope(verdict: Any) -> dict:
    """The tool-result envelope's ``gate`` block (technical-analysis
    "Audit Trail": verdict + family + explanation + alternative +
    model_alias + latency_ms + cache_hit)."""
    if verdict is None:
        return {}
    return {
        "verdict": getattr(verdict, "verdict", None),
        "family": getattr(verdict, "family", None),
        "explanation": getattr(verdict, "explanation", None),
        "alternative": getattr(verdict, "alternative", None),
        "model_alias": getattr(verdict, "model_alias", None),
        "latency_ms": getattr(verdict, "latency_ms", None),
        "cache_hit": getattr(verdict, "cache_hit", None),
        "gate_id": getattr(verdict, "gate_id", None),
    }


def _llm_config(manager: Any) -> dict[str, Any]:
    """The raw LLM connection config for the judge call.

    The gate sends a PURPOSE-BOUND ALIAS (never ``config.llm.model``
    and never ``config.llm.model_quick`` — the latter is silently
    rewritten to the global model by ``set_title_model_fallback``);
    the proxy resolves the alias.
    """
    config = getattr(manager, "config", None)
    llm = getattr(config, "llm", None)
    if llm is None:
        return {}
    return {
        "base_url": getattr(llm, "base_url", None),
        "base_url_backup": getattr(llm, "base_url_backup", None),
        "api_key": getattr(llm, "api_key", None),
    }


def _slug_family_of(slug: str) -> str | None:
    """The family of a slug (the store's own rule, mirrored locally)."""
    from ..repositories.pok.models import pok_family_key  # noqa: PLC0415

    try:
        return pok_family_key(slug)
    except Exception:  # noqa: BLE001 — defensive
        return None


def _record_standing_defects_best_effort(engine: Any, project_id: str) -> bool:
    """Record the standing-defect RESIDUAL rows on the gate path.

    The Q-2 ruling: NO boot-time seed, no live-write path without
    sanction — the residual rows (the dispositions of the two
    standing defects of the write-gate subsystem) are written by the
    GATE PATH, the first time the gate judges for a project. The
    write is idempotent per defect (the ledger rows are keyed by
    their defect) and it NEVER changes the write verdict.
    """
    if engine is None:
        return False
    try:
        POKRepository(engine).record_standing_defects(project_id)
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort by contract
        logger.warning(
            "[POK] standing-defect residual write failed for project %s "
            "(%s) — the write verdict is unchanged",
            project_id,
            type(exc).__name__,
        )
        return False


def append_ledger_row_best_effort(engine: Any, row: POKWriteGateLedger) -> bool:
    """Append a ledger row, NEVER raising (best-effort audit).

    A VERDICT row is advisory telemetry: an append failure must NEVER
    change the write verdict (design-amendments-v2 §13.10 — "VERDICT
    rows are post-commit best-effort never-raise"). The failure is
    logged at WARNING with the exception TYPE only.
    """
    if engine is None:
        return False
    try:
        POKRepository(engine).append_ledger(row)
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort by contract
        logger.warning(
            "[POK] ledger append failed (%s) — the audit row was lost, "
            "the write verdict is unchanged",
            type(exc).__name__,
        )
        return False


def create_pok_tools(
    manager: Any,
    *,
    current_instance_id: str = "",
    agent_id: str = "",
) -> list[Any]:
    """Build the three POK tools for one instance/agent.

    ``agent_id`` is the FACTORY-CAPTURED attribution + the in-body
    role gate's input (``create_instance_tools(..., agent_id=...)``
    parameter — never a caller-supplied key at call time).
    """
    engine = getattr(manager, "_engine", None)
    instance_repository = getattr(manager, "_instance_repository", None)
    # Captured agent id (factory param first — the proven in-body
    # pattern; the instance row's agent_id is the fallback so a
    # test/spawn path that omitted the param still attributes
    # honestly).
    captured_agent_id = agent_id or ""
    if not captured_agent_id and instance_repository is not None and current_instance_id:
        try:
            row = instance_repository.get(current_instance_id)
            captured_agent_id = getattr(row, "agent_id", "") or ""
        except Exception:  # noqa: BLE001 — defensive
            captured_agent_id = ""

    def _service() -> POKService | None:
        if engine is None:
            return None
        return POKService(engine)

    def _scope() -> tuple[str | None, str | None, dict | None]:
        """(project_id, project_name, refusal-dict-or-None)."""
        project_id = _resolve_project_id(instance_repository, current_instance_id)
        if not project_id:
            return None, None, _err(
                "pok_no_project",
                "this instance is not linked to a project — "
                "project_operating_knowledge is project-scoped",
            )
        name = _project_name(instance_repository, project_id)
        if is_default_project(project_id, name):
            return None, name, _err(
                "pok_default_project",
                "project_operating_knowledge is scoped to non-default "
                "projects — the system-default project accepts no POK "
                "calls (AC10)",
            )
        return project_id, name, None

    # ── the write gate closures (Phase 7/8) ───────────────────────────────

    def _build_gate_fn(
        *,
        manager: Any,
        engine: Any,
        project_id: str,
        project_name: str | None,
        enforcement_on: bool,
        gate_state: dict[str, Any],
        tightening_on: bool = False,
    ) -> Any:
        """Build the gate callable the SERVICE calls at its gate slot.

        The gate NEVER writes — it returns a typed verdict and the TOOL
        executes (design-amendments-v2 §8, authority split, BINDING).

        SHADOW is the default (both sibling switches are OFF at build):
        the verdict is ADVISORY — it is logged and it refuses no write.
        In ENFORCE mode an unavailable judge is D2 fail-closed: the
        write does NOT happen and the caller is told the content was
        not judged.

        THE TIGHTENING (T10.6): when the ``pok_gate.softmax_tightening``
        switch is ON and the store has reached its soft max, the gate
        is told to tighten (more ASK rounds, MERGE preferred over a
        new row). The soft max is read at WRITE TIME by one
        unique-index SELECT (the I1 placement) — NEVER memoised.
        """

        def _gate_fn(**kwargs: Any) -> GateVerdict | None:
            try:
                verdict = judge_pok_write(
                    project_id=kwargs.get("project_id") or project_id,
                    project_name=project_name,
                    slug=kwargs.get("slug") or "",
                    body=kwargs.get("body") or "",
                    reason=kwargs.get("reason") or "",
                    rows=active_rows_for_digest(engine, project_id),
                    llm_config=_llm_config(manager),
                    merge_policy=DEFAULT_MERGE_POLICY,
                    cache=_GATE_CACHE,
                    is_production=is_prod_lane(),
                    shadow=not enforcement_on,
                    tightening=_tightening_payload(
                        engine, project_id, enabled=tightening_on
                    ),
                )
            except POKGateUnavailable as exc:
                if enforcement_on:
                    # D2 fail-closed — the write is NOT committed and the
                    # content was NOT judged.
                    raise
                # SHADOW: an unavailable judge is advisory — the write
                # proceeds and the gap is recorded.
                gate_state["unavailable"] = exc.reason
                append_ledger_row_best_effort(
                    engine,
                    POKWriteGateLedger(
                        project_id=project_id,
                        gate_id=f"gate-shadow-{uuid.uuid4()}",
                        phase="ADMISSION",
                        verdict=None,
                        reason=(kwargs.get("reason") or "")[:200] or None,
                        judge_explanation=(
                            "the judge was unavailable (reason=%s) — the "
                            "write proceeded in SHADOW" % exc.reason
                        ),
                        agent_id=captured_agent_id or "unknown",
                    ),
                )
                return None
            gate_state["verdict"] = verdict
            # The STANDING-DEFECT RESIDUAL rows (pok-write-gate Phase 5,
            # T5.6 — the FIRST REAL WRITER of the dispositions). The
            # Q-2 ruling: NO boot-time seed and no unwired write path
            # — the residual rows are recorded ON THE GATE PATH, the
            # first time the gate judges for this project. The write
            # is idempotent per defect (the ledger is append-only and
            # the residual rows are keyed by their defect) and it is
            # best-effort: a failure NEVER changes the write verdict.
            _record_standing_defects_best_effort(engine, project_id)
            return verdict

        return _gate_fn

    def _verdict_phase(verdict: Any) -> str:
        """The audit phase for a verdict row.

        A plain ACCEPT/MERGE/REJECT is an ``ADMISSION`` row; an ASK is
        ``ASK_MINTED`` (enforce) or ``ASK_SHADOW`` (the advisory-only
        shadow ASK — no pending mint, no "confirmation required"
        payload).
        """
        if verdict is None:
            return "ADMISSION"
        token = getattr(verdict, "verdict", None)
        if token == "ASK":
            return "ASK_SHADOW" if getattr(verdict, "advisory", False) else "ASK_MINTED"
        return "ADMISSION"

    def _append_verdict_row(
        engine: Any,
        *,
        project_id: str,
        verdict: Any,
        reason: str | None,
        slug: str | None = None,
        phase: str | None = None,
    ) -> bool:
        """Append the gate's VERDICT row (post-commit, best-effort).

        The verdict row is ADVISORY telemetry (design-amendments-v2
        §13.10): it never shares the write's transaction and its
        failure NEVER changes the write verdict.
        """
        if verdict is None and phase is None:
            return False
        gate_id = getattr(verdict, "gate_id", None) or f"gate-{uuid.uuid4()}"
        row = POKWriteGateLedger(
            project_id=project_id,
            gate_id=gate_id,
            phase=phase or _verdict_phase(verdict),
            candidate_slug=slug,
            verdict=getattr(verdict, "verdict", None),
            reason=(reason or "")[:200] or None,
            judge_explanation=getattr(verdict, "explanation", None),
            confidence=getattr(verdict, "confidence", None),
            executed=False,
            target_slug=getattr(verdict, "target_slug", None),
            merge_kind=getattr(verdict, "merge_kind", None),
            proposed_body_sha256=getattr(verdict, "proposed_body_sha256", None),
            target_body_sha256=getattr(verdict, "target_body_sha256", None),
            agent_id=captured_agent_id or "unknown",
        )
        return append_ledger_row_best_effort(engine, row)

    def _mint_pending(
        *,
        project_id: str,
        verdict: Any,
        slug: str,
        body: str,
        reason: str,
        domain: str,
        provenance: str,
        audience: str,
        source_tier: str | None,
        question: str,
        merge_target_slug: str | None = None,
        merge_target_body: str | None = None,
        merge_kind: str | None = None,
        proposed_body: str | None = None,
        effective_slug: str | None = None,
        routed_to_curator: bool = False,
    ) -> PendingGate:
        """Mint the IN-PROCESS pending ASK state (T8.6).

        The entry stores the ADJUDICATED STRIPPED body + every upsert
        input, so the answer path can re-run the write with the gate
        bypassed (the scope/fence/budget/no-op/caps checks re-run at
        answer time). NO live-store writes: the pending state is
        process memory only.
        """
        ttl = _PENDING_GATES.ttl_s
        now = time.monotonic()
        pending = PendingGate(
            gate_id=str(getattr(verdict, "gate_id", "") or uuid.uuid4()),
            project_id=project_id,
            owner_agent_id=captured_agent_id,
            owner_instance_id=current_instance_id,
            slug=effective_slug or slug,
            body=body.strip(),
            reason=reason,
            domain=domain,
            provenance=provenance,
            audience=audience,
            source_tier=source_tier,
            question=question,
            verdict=verdict,
            created_at=now,
            expires_at=now + ttl,
            merge_target_slug=merge_target_slug,
            merge_target_body=merge_target_body,
            merge_kind=merge_kind,
            proposed_body=proposed_body,
        )
        pending.routed_to_curator = routed_to_curator
        _PENDING_GATES.mint(pending)
        return pending

    def _handle_gate_ask(
        *,
        engine: Any,
        project_id: str,
        project_name: str | None,
        outcome: AskUpsertOutcome,
        reason: str,
        domain: str,
        provenance: str,
        audience: str,
        source_tier: str | None,
        agent_id: str,
        instance_id: str,
        gate_state: dict[str, Any],
    ) -> dict:
        """Apply a gate ASK / MERGE verdict (T8.2, T8.4, T8.12, T8.15).

        * a plain ASK → the pending state is minted and the structured
          "confirmation required" payload is returned (nothing was
          written);
        * a MERGE with ``merge_kind="no_delta"`` → NO WRITE: the
          verdict row + a ``record_verification`` stamp on the target
          (the fact is already carried — a second row would be
          duplication);
        * a MERGE with ``merge_kind="delta"`` → the merge POLICY
          decides: ``AUTO`` executes the count-preserving supersede,
          ``ASK_WITH_PROPOSAL`` / ``ASK_NO_PROPOSAL`` mint the pending
          state and ask (F2 — the caller confirms; the payload carries
          the FULL target body + the candidate body + the proposal);
        * an R-RET rename proposal → the effective slug is the
          PROPOSED one (the body is preserved — the remedy is the
          slug, never the content).
        """
        verdict = outcome.verdict
        token = getattr(verdict, "verdict", None)
        if token == "MERGE":
            return _handle_merge_verdict(
                engine=engine,
                project_id=project_id,
                project_name=project_name,
                outcome=outcome,
                reason=reason,
                domain=domain,
                provenance=provenance,
                audience=audience,
                source_tier=source_tier,
                agent_id=agent_id,
                instance_id=instance_id,
            )
        # ── A plain ASK ──
        question = outcome.question or "the write gate needs your confirmation"
        proposed_slug = getattr(verdict, "proposed_slug", None)
        pending = _mint_pending(
            project_id=project_id,
            verdict=verdict,
            slug=outcome.candidate_slug or "",
            body=outcome.candidate_body or "",
            reason=reason,
            domain=domain,
            provenance=provenance,
            audience=audience,
            source_tier=source_tier,
            question=question,
            effective_slug=proposed_slug,
        )
        _append_verdict_row(
            engine,
            project_id=project_id,
            verdict=verdict,
            reason=reason,
            phase="ASK_MINTED",
        )
        logger.info(
            "[POK] gate ASK gate_id=%s slug=%s by=%s (confirmation required)",
            pending.gate_id,
            pending.slug,
            agent_id or "unknown",
        )
        return {
            "action": "confirmation_required",
            "gate_id": pending.gate_id,
            "question": question,
            "expires_in_s": int(_PENDING_GATES.ttl_s),
            "family": getattr(verdict, "family", None),
            "alternative": getattr(verdict, "alternative", None),
            "proposed_slug": proposed_slug,
            "answer_with": "pok_gate_answer(gate_id, answer)",
        }

    def _handle_merge_verdict(
        *,
        engine: Any,
        project_id: str,
        project_name: str | None,
        outcome: AskUpsertOutcome,
        reason: str,
        domain: str,
        provenance: str,
        audience: str,
        source_tier: str | None,
        agent_id: str,
        instance_id: str,
    ) -> dict:
        """Execute (or ask about) a MERGE verdict (T8.2 / T8.12 / T8.15).

        The merge is COUNT-PRESERVING: the target row leaves ``active``
        and the successor enters it in ONE transaction (the store's own
        supersede machinery), so the project never holds two rows for
        one fact.
        """
        verdict = outcome.verdict
        target_slug = outcome.merge_target_slug or getattr(verdict, "target_slug", None)
        merge_kind = outcome.merge_kind or getattr(verdict, "merge_kind", None) or "no_delta"
        target_body = outcome.merge_target_body
        candidate_body = outcome.candidate_body or ""
        proposal = outcome.proposed_body or getattr(verdict, "proposed_body", None)

        if not target_slug:
            # A merge without a target is not executable — ASK.
            question = "the judge named no merge target — which ACTIVE row carries this fact?"
            pending = _mint_pending(
                project_id=project_id,
                verdict=verdict,
                slug="",
                body=candidate_body,
                reason=reason,
                domain=domain,
                provenance=provenance,
                audience=audience,
                source_tier=source_tier,
                question=question,
            )
            _append_verdict_row(
                engine, project_id=project_id, verdict=verdict, reason=reason, phase="ASK_MINTED"
            )
            return {
                "action": "confirmation_required",
                "gate_id": pending.gate_id,
                "question": question,
                "expires_in_s": int(_PENDING_GATES.ttl_s),
                "answer_with": "pok_gate_answer(gate_id, answer)",
            }

        # ── NO-DELTA MERGE = NO WRITE (F2) ──
        if merge_kind == "no_delta":
            # The fact is already fully carried by the target: the
            # verdict row + a verification stamp on the target, and NO
            # new row (a second row would be the duplication the gate
            # exists to prevent).
            stamped = _stamp_target_verification(engine, project_id, target_slug)
            _append_verdict_row(
                engine, project_id=project_id, verdict=verdict, reason=reason, phase="ADMISSION"
            )
            logger.info(
                "[POK] gate MERGE (no-delta) target=%s — no write, verify stamp=%s",
                target_slug,
                stamped,
            )
            return {
                "action": "merged",
                "executed": False,
                "merge_kind": "no_delta",
                "target_slug": target_slug,
                "slug": target_slug,
                "note": (
                    "the active row already carries this fact — no new "
                    "row was written and the target's verify_count was "
                    "stamped"
                ),
                "gate": _gate_envelope(verdict),
            }

        # ── TIER PRE-CHECK (T8.12) ──
        # A pinned / curated target is CURATOR-ONLY: the judge cannot
        # see the tier from the digest, so the TOOL pre-checks it. An
        # untouchable target is NEVER auto-merged — the ASK is routed
        # to the curator (leader / project-manager).
        target_row = _resolve_active_row(engine, project_id, target_slug)
        untouchable = bool(
            target_row is not None
            and (target_row.pinned or (target_row.source_tier or "agent") == "curated")
        )
        if untouchable and not is_curator(agent_id):
            question = (
                "the merge target %r is PINNED/CURATED — only a curator "
                "(leader / project-manager) may merge into it; this "
                "confirmation is routed to the curator" % target_slug
            )
            pending = _mint_pending(
                project_id=project_id,
                verdict=verdict,
                slug=target_slug,
                body=candidate_body,
                reason=reason,
                domain=domain,
                provenance=provenance,
                audience=audience,
                source_tier=source_tier,
                question=question,
                merge_target_slug=target_slug,
                merge_target_body=target_body,
                merge_kind=merge_kind,
                proposed_body=proposal,
                routed_to_curator=True,
            )
            _append_verdict_row(
                engine, project_id=project_id, verdict=verdict, reason=reason, phase="ASK_MINTED"
            )
            return {
                "action": "confirmation_required",
                "gate_id": pending.gate_id,
                "question": question,
                "expires_in_s": int(_PENDING_GATES.ttl_s),
                "routed_to": "curator",
                "curator_agents": ["leader", "project-manager"],
                "target_slug": target_slug,
                "answer_with": "pok_gate_answer(gate_id, answer)",
            }

        # ── THE DELTA MERGE — the policy decides (F2) ──
        policy = DEFAULT_MERGE_POLICY
        if merge_policy_override() is not None:
            policy = merge_policy_override()
        if policy == "AUTO":
            merged_body = proposal or candidate_body
            svc = POKService(engine)
            try:
                merged = svc.upsert(
                    project_id=project_id,
                    slug=target_slug,
                    body=merged_body,
                    reason=reason,
                    domain=domain,
                    provenance=provenance,
                    audience=audience,
                    source_tier=source_tier,
                    agent_id=agent_id,
                    project_name=project_name,
                    gate_fn=None,
                )
            except POKError as exc:
                return _err(exc.code, exc.message, **exc.details)
            _append_verdict_row(
                engine, project_id=project_id, verdict=verdict, reason=reason, phase="ADMISSION"
            )
            entry = merged.entry
            return {
                "action": "merged",
                "executed": True,
                "merge_kind": merge_kind,
                "target_slug": target_slug,
                "slug": entry.slug if entry is not None else target_slug,
                "id": entry.id if entry is not None else None,
                "gate": _gate_envelope(verdict),
            }

        # ASK_WITH_PROPOSAL (the DEFAULT) / ASK_NO_PROPOSAL — the
        # caller confirms; the payload carries the FULL target body
        # (not the 80-char digest slice) + the candidate body so the
        # caller can check coverage (F2).
        question = (
            "the active row %r already carries this fact — confirm the "
            "merge (the merged body below is the judge's proposal; the "
            "answer cannot carry a body, so a different body means a "
            "plain pok_upsert instead)" % target_slug
        )
        pending = _mint_pending(
            project_id=project_id,
            verdict=verdict,
            slug=target_slug,
            body=candidate_body,
            reason=reason,
            domain=domain,
            provenance=provenance,
            audience=audience,
            source_tier=source_tier,
            question=question,
            merge_target_slug=target_slug,
            merge_target_body=target_body,
            merge_kind=merge_kind,
            proposed_body=proposal,
        )
        _append_verdict_row(
            engine, project_id=project_id, verdict=verdict, reason=reason, phase="ASK_MINTED"
        )
        return {
            "action": "confirmation_required",
            "gate_id": pending.gate_id,
            "question": question,
            "expires_in_s": int(_PENDING_GATES.ttl_s),
            "merge_kind": merge_kind,
            "target_slug": target_slug,
            "target_body": target_body,
            "candidate_body": candidate_body,
            "proposed_body": proposal if policy == "ASK_WITH_PROPOSAL" else None,
            "answer_with": "pok_gate_answer(gate_id, answer)",
        }

    def _substitute_active_cap(fn: Any) -> Any:
        """Substitute the LIVE admission cap into the docstring BEFORE
        ``@tool`` bakes it into the tool description (and any
        ``args_schema`` doc).

        This runs on the PLAIN function object — a pure string op on
        our own ``__doc__``, never a post-construction attribute write
        on the tool object (an upstream ``frozen=True`` on ``BaseTool``
        would otherwise break every instance spawn, since
        ``create_pok_tools`` runs bare in the spawn path).

        Load-bearing: the constant is read from the MODULE GLOBAL
        inside the body. It must NOT be captured as a default argument
        — a default binds at ``def`` time and would freeze the value,
        defeating the discriminating test that patches
        ``daemon.tools.pok.POK_ACTIVE_CAP_PER_PROJECT``.
        """
        if fn.__doc__:
            fn.__doc__ = fn.__doc__.replace(
                "<active_cap>", str(POK_ACTIVE_CAP_PER_PROJECT)
            )
        return fn

    @register_tool_category(CATEGORY_NAME)
    @tool
    @_substitute_active_cap
    def pok_upsert(
        slug: str,
        body: str,
        reason: str,
        domain: str = "other",
        provenance: str = "observed",
        audience: str = "operation",
        source_tier: str | None = None,
    ) -> dict:
        """Write one operating-knowledge fact for THIS project.

        Use tool_help() for details. Behavior:
        - ``slug``: kebab-case [a-z0-9-], <=44 chars. Family-ordered
          slugs follow ``<family>-<NN>-<tail>`` (e.g.
          ``pipeline-01-merge-master`` or ``prompt-assent-01-x``); the
          family head may be multi-token (e.g. ``prompt-assent``); members
          of a family render adjacently.
        - ``body``: ONE paragraph, <=240 chars measured AFTER
          escaping. Newlines, leading '#', '[SYSTEM CONTEXT:' and
          triple-backticks are REFUSED at write (the content fence, R0).
        - ``reason``: REQUIRED field (the tool schema enforces presence).
          The OPERATIONAL CLAIM — why this fact belongs in POK (the
          gate's primary input). Empty/whitespace is refused
          (``pok_reason_required``). Be specific: state the operational
          invariant, not "because" or boilerplate.
        - ``audience`` (stated at capture, default 'operation'):
          operation → {ari, leader, devops, sonic-leader,
          sonic-developer}; implementation → {leader, developer,
          sonic-developer}; both → every agent on the project.
          Curator-narrow override is pok_curate's ``audience_agents``.
        - ``source_tier`` (default 'agent'): omit for agent-captured
          facts. ``'curated'`` marks a dev-curated immutable
          reference (a guideline) and is LEADER / PROJECT-MANAGER
          ONLY — one call captures a curated guideline. Non-curators
          passing 'curated' are refused (pok_tier_requires_curator).
          On a supersede, an omitted tier INHERITS the target's tier
          (content refresh never reclassifies authority); tier flips
          on a live row are pok_curate's ``source_tier``.
        - Same slug + same content = no-op echo. Same slug + changed
          content = SUPERSEDE (the prior row flips superseded with
          its lineage wire; the successor starts unpinned).
          Superseding a PINNED or CURATED entry requires leader /
          project-manager.
        - The WRITE GATE may ask: a write that needs a confirmation
          returns {"action": "confirmation_required", "gate_id": ...,
          "question": ...} and NOTHING is written. Answer it with
          pok_gate_answer(gate_id, answer); a missing or unclassifiable
          answer never defaults to accept.
        - Admission is refuse-never-evict: at <active_cap> active
          entries the call refuses naming the archive cure; a family's
          4th active member refuses (pok_family_full).
        """
        svc = _service()
        if svc is None:
            return _err(
                "pok_store_unavailable",
                "the POK store is unavailable on this boot — the tool "
                "refuses rather than writing blind",
            )
        project_id, project_name, refusal = _scope()
        if refusal is not None:
            return refusal
        # Reason (the OPERATIONAL CLAIM) is the gate's primary input.
        # Precedence is load-bearing: store → scope → REASON →
        # fence/budget/family. This check sits AFTER the store and
        # scope guards above and BEFORE svc.upsert (where the fence,
        # budget and family checks live), so a degraded store or a
        # bad scope always wins over a missing reason.
        if not reason or not reason.strip():
            return _err(
                "pok_reason_required",
                "a reason (the operational claim) is required — state "
                "why this fact belongs in POK",
            )
        try:
            from ..services.context_messages import (  # noqa: PLC0415
                escape_for_context_block,
            )

            # ── THE WRITE GATE (Phase 7/8) ─────────────────────────────
            # The gate NEVER writes; the TOOL executes (the authority
            # split). The tool builds the ``gate_fn`` the SERVICE calls
            # at its gate slot (strictly between the no-op echo and the
            # supersede) and applies the verdict here.
            #
            # SHADOW by default: the two sibling switches are DB-backed
            # settings keys, both OFF at build, so the judge is
            # ADVISORY — it logs its verdict and refuses no write.
            enforcement_on, _softmax_tightening = read_gate_switches(engine)
            gate_state: dict[str, Any] = {}
            gate_fn = _build_gate_fn(
                manager=manager,
                engine=engine,
                project_id=project_id,
                project_name=project_name,
                enforcement_on=enforcement_on,
                gate_state=gate_state,
                tightening_on=_softmax_tightening,
            )

            outcome = svc.upsert(
                project_id=project_id,
                slug=slug,
                body=body,
                reason=reason,
                domain=domain,
                provenance=provenance,
                audience=audience,
                source_tier=source_tier,
                agent_id=captured_agent_id,
                project_name=project_name,
                escape_fn=escape_for_context_block,
                gate_fn=gate_fn,
            )
        except POKError as exc:
            if exc.code == "pok_gate_reject":
                # The REJECT verdict row is ADVISORY telemetry: written
                # after the (never-committed) decision, best-effort,
                # never raising (design-amendments-v2 §13.10).
                _append_verdict_row(
                    engine,
                    project_id=project_id,
                    verdict=gate_state.get("verdict"),
                    reason=reason,
                )
                # ── the ROUTE + the zero-silent-drop parking seam ──────
                # A refusal must NAME a home (R5) and the caller must
                # be able to REACH it (T9.3). When the home is out of
                # the caller's reach, the content would vanish — so it
                # is PARKED (T9.4) and the envelope says so.
                family = exc.details.get("family")
                route = route_for_family(family)
                reachable = destination_available(
                    captured_agent_id, route.get("categories")
                )
                parked_id: str | None = None
                if not reachable:
                    parked_id = park_rejected_write(
                        engine,
                        project_id=project_id,
                        slug=slug,
                        body=body,
                        reason=reason,
                        family=family,
                        route=route,
                        agent_id=captured_agent_id,
                    )
                return _err(
                    exc.code,
                    exc.message,
                    family=family,
                    alternative=exc.details.get("alternative"),
                    gate_id=exc.details.get("gate_id"),
                    route=route.get("route"),
                    destination=route.get("destination"),
                    content_class=route.get("content_class"),
                    instruction=route.get("instruction"),
                    destination_available=reachable,
                    parked=bool(parked_id),
                    parking_id=parked_id,
                )
            return _err(exc.code, exc.message, **exc.details)
        except POKGateUnavailable as exc:
            # D2 fail-closed: the write was NOT committed and the
            # content was NOT judged (the message says so).
            return _err(exc.code, exc.message, reason=exc.reason)
        except Exception as exc:  # noqa: BLE001 — M13
            logger.exception("pok_upsert store error")
            return _err("pok_store_error", f"POK store error: {type(exc).__name__}")
        if isinstance(outcome, AskUpsertOutcome):
            # The gate ASKED (or returned a MERGE the tool must
            # execute): nothing was written. The tool mints the pending
            # state + the ASK_MINTED audit row and returns the
            # structured "confirmation required" payload.
            return _handle_gate_ask(
                engine=engine,
                project_id=project_id,
                project_name=project_name,
                outcome=outcome,
                reason=reason,
                domain=domain,
                provenance=provenance,
                audience=audience,
                source_tier=source_tier,
                agent_id=captured_agent_id,
                instance_id=current_instance_id,
                gate_state=gate_state,
            )
        # The write HAPPENED. The VERDICT row is post-commit
        # best-effort (design-amendments-v2 §13.10): it is advisory
        # telemetry and its failure NEVER changes the write. The PHASE
        # is derived from the verdict (an ASK — shadow or enforce — is
        # an ASK row, not an ADMISSION row).
        _append_verdict_row(
            engine,
            project_id=project_id,
            verdict=gate_state.get("verdict"),
            reason=reason,
        )
        entry = outcome.entry
        logger.info(
            "[POK] upsert action=%s slug=%s project=%s by=%s audience=%s "
            "tier=%s",
            outcome.action,
            entry.slug,
            project_id,
            captured_agent_id or "unknown",
            entry.audience,
            entry.source_tier,
        )
        return {
            "action": outcome.action,
            "id": entry.id,
            "slug": entry.slug,
            "domain": entry.domain,
            "audience": entry.audience,
            "source_tier": entry.source_tier,
            "status": entry.status,
            "created_by_agent": entry.created_by_agent,
            **(
                {"superseded_id": outcome.superseded_id}
                if outcome.superseded_id
                else {}
            ),
            **(
                {
                    "note": "exact duplicate of the active entry — no "
                    "new row written"
                }
                if outcome.action == "no-op"
                else {}
            ),
        }

    @register_tool_category(CATEGORY_NAME)
    @tool
    def pok_query(query: str = "", domain: str | None = None) -> dict:
        """Read THIS project's operating knowledge (one step).

        Use tool_help() for details. UNIVERSAL READ: returns entries
        of ALL audiences to any agent on the project — the audience
        tags gate ACTIVE injection only, never a read (injection is
        curated; reading is free).

        Behavior:
        - ``query``: natural-language ranking (BM25→vector fusion on
          the shared selector; pinned rows bypass the threshold and
          always load). Empty query = the ranked baseline (pinned +
          floor).
        - ``domain``: optional filter (artifact | deploy | verify |
          environment | catalog | other).
        - Superseded lineages ride along per entry (``lineage``);
          ``embedding_lag`` names entries ranked BM25-only this call
          (their vector is still minting — never fatal).
        """
        svc = _service()
        if svc is None:
            return _err(
                "pok_store_unavailable",
                "the POK store is unavailable on this boot — the tool "
                "refuses rather than reading blind",
            )
        project_id, project_name, refusal = _scope()
        if refusal is not None:
            return refusal
        try:
            outcome = svc.query(
                project_id=project_id,
                query=query,
                domain=domain,
                agent_id=captured_agent_id,
                project_name=project_name,
            )
        except POKError as exc:
            return _err(exc.code, exc.message, **exc.details)
        except Exception as exc:  # noqa: BLE001
            logger.exception("pok_query store error")
            return _err("pok_store_error", f"POK store error: {type(exc).__name__}")
        entries = []
        for e in outcome.entries:
            item = {
                "id": e.id,
                "slug": e.slug,
                "domain": e.domain,
                "audience": e.audience,
                "body": e.body,
                "status": e.status,
                "disputed": e.disputed,
                "pinned": e.pinned,
                "source_tier": e.source_tier,
                "provenance": e.provenance,
                "created_by_agent": e.created_by_agent,
                "verify_count": e.verify_count,
                "access_count": e.access_count,
                "last_verified": e.last_verified,
            }
            chain = outcome.lineage.get(e.slug)
            if chain:
                # The lineage surface is where [CURATED] renders
                # (trust-tier addendum T-4 — query lineage only; the
                # ACTIVE-injection block carries NO in-block marker in
                # v1, the band position signals status). Universal read
                # across tiers: source_tier is visible to every agent,
                # it never filters a read.
                item["lineage"] = [
                    {
                        "id": r.id,
                        "status": r.status,
                        "superseded_by_id": r.superseded_by_id,
                        "body": r.body,
                        "source_tier": r.source_tier,
                        **(
                            {"marker": "[CURATED]"}
                            if r.source_tier == "curated"
                            else {}
                        ),
                    }
                    for r in chain
                ]
            entries.append(item)
        if outcome.bm25_only:
            logger.info(
                "[POK] query bm25_only=true (no query embedding) "
                "project=%s results=%d",
                project_id,
                len(entries),
            )
        result: dict[str, Any] = {
            "entries": entries,
            "count": len(entries),
            "bm25_only": outcome.bm25_only,
        }
        if outcome.embedding_lag_entries:
            result["embedding_lag"] = outcome.embedding_lag_entries
        return result

    @register_tool_category(CATEGORY_NAME)
    @tool
    def pok_gate_answer(gate_id: str, answer: str) -> dict:
        """Answer a pending POK write-gate confirmation.

        Use tool_help() for details. A write the gate could not settle
        on its own returns a "confirmation required" payload carrying a
        ``gate_id``; pass that id here with your answer.

        Behavior:
        - ``gate_id``: the id from the confirmation-required payload.
        - ``answer``: your answer IN WORDS (the answer carries NO body
          — the pending entry already stores the adjudicated body and
          every write input, and the write is re-checked at answer
          time: scope, fence, budget, no-op and the admission caps).
        - A CONFIRMING answer executes the pending write; a DECLINING
          answer writes nothing.
        - MISSING or UNCLASSIFIABLE answers NEVER default to accept:
          they refuse with a typed code (``pok_gate_answer_required`` /
          ``pok_gate_answer_unrecognized``) and NOTHING is written.
        - The gate is OWNER-SCOPED (the agent that made the pending
          write answers it) and expires (``pok_gate_expired``); a
          second answer is an audited no-op
          (``pok_gate_already_answered``).
        - A merge whose target is PINNED or CURATED is routed to a
          curator (leader / project-manager).
        """
        svc = _service()
        if svc is None:
            return _err(
                "pok_store_unavailable",
                "the POK store is unavailable on this boot — the tool "
                "refuses rather than writing blind",
            )
        project_id, project_name, refusal = _scope()
        if refusal is not None:
            return refusal
        pending, refusal_code = _PENDING_GATES.resolve(
            gate_id,
            agent_id=captured_agent_id,
            instance_id=current_instance_id,
            answer=answer,
        )
        if refusal_code is not None:
            if refusal_code in ("pok_gate_not_owner", "pok_gate_already_answered"):
                # AUDITED refusals (T8.7): the refusal itself is a
                # ledger event. The audit row carries a DISTINCT
                # gate_id (the original + a refusal suffix) so the
                # gate-chain's (project_id, gate_id, phase) uniqueness
                # is never violated by the audit of a refusal.
                append_ledger_row_best_effort(
                    engine,
                    POKWriteGateLedger(
                        project_id=project_id,
                        gate_id=f"{gate_id}#{refusal_code}",
                        phase="ASK_ANSWERED",
                        verdict="ASK",
                        reason=(answer or "")[:200] or None,
                        judge_explanation=(
                            "the answer was refused (%s) — nothing was "
                            "written" % refusal_code
                        ),
                        answer_excerpt=(answer or "")[:200] or None,
                        answered_by=captured_agent_id or "unknown",
                        executed=False,
                        agent_id=captured_agent_id or "unknown",
                    ),
                )
            return _err(
                refusal_code,
                _ANSWER_REFUSAL_MESSAGES.get(
                    refusal_code, "the pending gate could not be answered"
                ),
                gate_id=gate_id,
            )
        classification = classify_answer(answer)
        if classification is None:
            # UNCLASSIFIABLE — the Emperor's catch: a garbage answer
            # must never launder into a write.
            append_ledger_row_best_effort(
                engine,
                POKWriteGateLedger(
                    project_id=project_id,
                    gate_id=f"{gate_id}#pok_gate_answer_unrecognized",
                    phase="ASK_ANSWERED",
                    verdict="ASK",
                    reason=(answer or "")[:200] or None,
                    judge_explanation=(
                        "the answer could not be classified — nothing was "
                        "written"
                    ),
                    answer_excerpt=(answer or "")[:200] or None,
                    answered_by=captured_agent_id or "unknown",
                    executed=False,
                    agent_id=captured_agent_id or "unknown",
                ),
            )
            return _err(
                "pok_gate_answer_unrecognized",
                "the answer could not be classified as a confirmation or a "
                "decline — answer in plain words ('yes, confirmed' / "
                "'no, declined'); nothing was written",
                gate_id=gate_id,
            )
        # The ASK_ANSWERED row (post-answer adjudication) — written
        # BEFORE the execution so the chain reads mint → answer →
        # execution.
        append_ledger_row_best_effort(
            engine,
            POKWriteGateLedger(
                project_id=project_id,
                gate_id=pending.gate_id,
                phase="ASK_ANSWERED",
                candidate_slug=pending.slug,
                verdict="ASK",
                reason=(pending.reason or "")[:200] or None,
                judge_explanation=(
                    "the caller answered the gate (%s)" % classification
                ),
                answer_excerpt=(answer or "")[:200] or None,
                answered_by=captured_agent_id or "unknown",
                executed=False,
                target_slug=pending.merge_target_slug,
                merge_kind=pending.merge_kind,
                agent_id=captured_agent_id or "unknown",
            ),
        )
        _PENDING_GATES.consume(pending.gate_id)
        if classification == "decline":
            logger.info(
                "[POK] gate DECLINED gate_id=%s slug=%s by=%s — no write",
                pending.gate_id,
                pending.slug,
                captured_agent_id or "unknown",
            )
            return {
                "action": "declined",
                "gate_id": pending.gate_id,
                "slug": pending.slug,
                "written": False,
                "note": "the pending write was declined — nothing was written",
            }
        # ── CONFIRMED — execute the pending write ──
        execution_audit = {
            "phase": "ASK_EXECUTED",
            "gate_id": pending.gate_id,
            "verdict": "ASK",
            "judge_explanation": getattr(pending.verdict, "explanation", None),
            "confidence": getattr(pending.verdict, "confidence", None),
            "target_slug": pending.merge_target_slug,
            "merge_kind": pending.merge_kind,
        }
        target_slug = pending.merge_target_slug
        body = pending.body
        if pending.merge_target_slug:
            # A confirmed MERGE writes the TARGET slug (the count-
            # preserving supersede): the merged body is the judge's
            # proposal when there is one, else the candidate body.
            target_slug = pending.merge_target_slug
            body = pending.proposed_body or pending.body
        try:
            outcome = svc.upsert(
                project_id=project_id,
                slug=target_slug or pending.slug,
                body=body,
                reason=pending.reason,
                domain=pending.domain,
                provenance=pending.provenance,
                audience=pending.audience,
                source_tier=pending.source_tier,
                agent_id=captured_agent_id,
                project_name=project_name,
                gate_fn=None,  # the answer path does NOT re-enter the gate
                execution_audit=execution_audit,
            )
        except POKError as exc:
            return _err(exc.code, exc.message, **exc.details)
        except Exception as exc:  # noqa: BLE001 — M13
            logger.exception("pok_gate_answer store error")
            return _err(
                "pok_store_error", f"POK store error: {type(exc).__name__}"
            )
        entry = getattr(outcome, "entry", None)
        logger.info(
            "[POK] gate ANSWERED gate_id=%s slug=%s by=%s action=%s",
            pending.gate_id,
            target_slug or pending.slug,
            captured_agent_id or "unknown",
            getattr(outcome, "action", None),
        )
        return {
            "action": getattr(outcome, "action", "written"),
            "gate_id": pending.gate_id,
            "written": True,
            "id": entry.id if entry is not None else None,
            "slug": entry.slug if entry is not None else (target_slug or pending.slug),
            "domain": entry.domain if entry is not None else None,
            "audience": entry.audience if entry is not None else None,
            "source_tier": entry.source_tier if entry is not None else None,
            "status": entry.status if entry is not None else None,
            "created_by_agent": entry.created_by_agent if entry is not None else None,
            "superseded_id": getattr(outcome, "superseded_id", None),
        }

    @register_tool_category(CATEGORY_NAME)
    @tool
    def pok_curate(
        slug: str,
        pinned: bool | None = None,
        disputed: bool | None = None,
        archived: bool | None = None,
        audience: str | None = None,
        audience_agents: list[str] | None = None,
        source_tier: str | None = None,
        basis: str | None = None,
    ) -> dict:
        """Curate THIS project's operating knowledge (leader /
        project-manager ONLY).

        The slug resolves to THE ROW THAT OWNS IT — the active row if
        present, else the most recent archived row, else the most
        recent superseded row (a dead ancestor the service layer then
        refuses); a successor-generation slug still carries its
        superseded ancestors under the same slug, but the ancestors
        are never the curation target and are never mutated.

        Use tool_help() for details. Flips (all optional, combinable):
        - ``pinned``: core tier (ALWAYS injected first). Cap 2 per
          project — the 3rd pin refuses naming the current pins.
        - ``disputed``: TRUE suppresses ACTIVE injection and marks
          the entry; it stays READABLE via pok_query. Re-adding the
          same content under a new slug while disputed is refused
          (laundering guard).
        - ``archived``: TRUE retires the entry. FORWARD-ONLY — an
          archived entry cannot return (write a successor instead).
          **Archiving REQUIRES a ``basis``** — the ruled ground on
          which the row was retired (see below).
        - ``audience`` / ``audience_agents``: reclassification /
          curator narrowing. ``audience_agents`` REPLACES the
          audience's default set when non-empty; pass [] to clear
          back to the default set.
        - ``source_tier``: trust-tier reclassification, both
          directions ('agent' ↔ 'curated'). 'curated' marks a
          dev-curated immutable reference — it renders in the
          dedicated curated band (cap 2 per view) and requires
          curator authority to supersede.
        - ``basis``: THE RULED ARCHIVE BASIS. REQUIRED whenever
          ``archived`` is TRUE, and it MUST be one of:
          ``covered-by-surviving-row`` (a surviving row already
          carries the fact), ``relocated-to-routed-home`` (the fact
          moved to another home), ``NOW-FALSE`` (the rule no longer
          holds), ``gate-rejection`` (the write gate rejected the
          row and the curator cites that rejection). An archive with
          a missing or unknown basis is REFUSED: consolidation
          happens ONLY under a ruled basis — NEVER on age, NEVER on
          access counts alone.
        """
        svc = _service()
        if svc is None:
            return _err(
                "pok_store_unavailable",
                "the POK store is unavailable on this boot — the tool "
                "refuses rather than curating blind",
            )
        project_id, project_name, refusal = _scope()
        if refusal is not None:
            return refusal
        # The in-body role gate is ALSO stated here (the service
        # enforces it from the same captured id; the explicit check
        # gives the tool surface its own typed echo).
        if not is_curator(captured_agent_id):
            return _err(
                "pok_curator_required",
                "pok_curate is leader / project-manager only — "
                f"attribution {captured_agent_id!r} is not a curator "
                "(attribution ≠ authorization)",
            )
        # RULED ARCHIVE BASIS (pok-write-gate Phase 1, T1.6): an
        # archive is permitted ONLY under a ruled basis. Validated
        # BEFORE the service call so a missing/unknown basis can never
        # archive a row. Consolidation happens ONLY under a ruled basis
        # — NEVER on age, NEVER on access counts alone
        # (design-amendments-v2 §9).
        if archived is True:
            if not basis or not str(basis).strip():
                return _err(
                    "pok_curate_basis_required",
                    "archiving requires a ruled basis — pass one of "
                    f"{', '.join(POK_ARCHIVE_BASES)}; an archive without "
                    "a basis is refused (consolidation happens only "
                    "under a ruled basis — never on age, never on "
                    "access counts alone)",
                    allowed=list(POK_ARCHIVE_BASES),
                )
            if basis not in POK_ARCHIVE_BASES:
                return _err(
                    "pok_curate_basis_invalid",
                    f"unknown archive basis {basis!r} — allowed bases "
                    f"are {', '.join(POK_ARCHIVE_BASES)}",
                    allowed=list(POK_ARCHIVE_BASES),
                )
        try:
            row = svc.curate(
                project_id=project_id,
                slug=slug,
                agent_id=captured_agent_id,
                pinned=pinned,
                disputed=disputed,
                archived=archived,
                audience=audience,
                audience_agents=audience_agents,
                source_tier=source_tier,
                project_name=project_name,
            )
        except POKError as exc:
            return _err(exc.code, exc.message, **exc.details)
        except Exception as exc:  # noqa: BLE001 — M13
            logger.exception("pok_curate store error")
            return _err("pok_store_error", f"POK store error: {type(exc).__name__}")
        # M12: curate() can return None — guard the dereference.
        if row is None:
            return _err("pok_not_found", f"entry {slug!r} vanished during curate")
        # ── THE RULED ARCHIVE BASIS IS LEDGERED (T8.17) ────────────────
        # The basis used to be validated + echoed but persisted ONLY in
        # the INFO log. It now lands as a LEDGER ROW on the SAME audit
        # surface as the verdict / execution rows, so the ledger alone
        # answers "on what ground was this row retired?" (SC#30).
        # D6 is UNCHANGED: ``pok_curate`` is still not gated — this is
        # a record, not a permission.
        if archived is True and basis:
            append_ledger_row_best_effort(
                engine,
                POKWriteGateLedger(
                    project_id=project_id,
                    gate_id=f"curate:{uuid.uuid4()}",
                    phase="ADMISSION",
                    candidate_slug=row.slug,
                    verdict=None,
                    reason=(
                        f"curator archived {row.slug!r} under the ruled "
                        f"basis {basis!r}"
                    ),
                    judge_explanation=(
                        "the curator's ruled archive basis — consolidation "
                        "happens ONLY under a ruled basis (never on age, "
                        "never on access counts alone)"
                    ),
                    basis=basis,
                    executed=True,
                    executed_by=captured_agent_id or "unknown",
                    agent_id=captured_agent_id or "unknown",
                ),
            )
        logger.info(
            "[POK] curate slug=%s project=%s by=%s "
            "pinned=%s disputed=%s archived=%s audience=%s audience_agents=%s "
            "tier=%s basis=%s",
            row.slug,
            project_id,
            captured_agent_id,
            row.pinned,
            row.disputed,
            row.status,
            row.audience,
            row.audience_agents,
            row.source_tier,
            basis if archived is True else None,
        )
        return {
            "action": "curated",
            "id": row.id,
            "slug": row.slug,
            "pinned": row.pinned,
            "disputed": row.disputed,
            "status": row.status,
            "audience": row.audience,
            "audience_agents": row.audience_agents,
            "source_tier": row.source_tier,
            "basis": basis if archived is True else None,
        }

    @register_tool_category(CATEGORY_NAME)
    @tool
    def pok_census() -> dict:
        """Census THIS project's operating knowledge (leader /
        project-manager ONLY).

        Use tool_help() for details. The census is the READ surface
        the POK librarian maintains the store from: the per-store
        metrics (shape, families, authors, tiers), the retrieval legs
        the librarian TRUSTS (the ledger's retrieval counts, split
        query-vs-injection), the never-retrieved / never-verified
        populations, the soft max the librarian derives and its drift.

        Behavior:
        - Read-only: the census NEVER writes a row.
        - ``snapshot_at``: the measurement time — every count is tied
          to it (cite the snapshot, never a bare count).
        - ``soft_max``: the derived ceiling (or null when the
          librarian has not run yet) + ``tightening``: whether
          admission is currently tightened.
        - The metric legs are labelled per-store (telemetry); the
          per-row metrics live in the ledger, not here.
        """
        svc = _service()
        if svc is None:
            return _err(
                "pok_store_unavailable",
                "the POK store is unavailable on this boot — the tool "
                "refuses rather than reading blind",
            )
        project_id, project_name, refusal = _scope()
        if refusal is not None:
            return refusal
        if not is_curator(captured_agent_id):
            return _err(
                "pok_curator_required",
                "pok_census is leader / project-manager only — "
                f"attribution {captured_agent_id!r} is not a curator "
                "(attribution ≠ authorization)",
            )
        try:
            from ..services.pok_librarian import (  # noqa: PLC0415 — cycle-neutral
                read_soft_max,
                read_store_metrics,
                tightening_decision,
            )

            repo = POKRepository(engine)
            store = read_store_metrics(repo, project_id)
            state = read_soft_max(engine, project_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("pok_census store error")
            return _err(
                "pok_census_error", f"POK census error: {type(exc).__name__}"
            )
        decision = tightening_decision(
            active_count=store.active_count,
            soft_max=state.value if state else None,
        )
        census = {
            "snapshot_at": store.measured_at,
            "project_id": project_id,
            "project_name": project_name,
            "active_count": store.active_count,
            "by_family": dict(sorted(store.by_family.items())),
            "by_author": dict(sorted(store.by_author.items())),
            "by_domain": dict(sorted(store.by_domain.items())),
            "tiers": {
                "pinned": sum(1 for e in store.entries if e.pinned),
                "disputed": sum(1 for e in store.entries if e.disputed),
                "curated": sum(1 for e in store.entries if e.is_curated),
            },
            "populations": {
                "never_retrieved": store.never_retrieved,
                "zero_verify": store.zero_verify,
                "no_family": store.no_family,
            },
            "retrieval": {
                "ledger_total": store.ledger_retrievals,
                "ledger_injection": store.ledger_injections,
                "ledger_query": store.ledger_queries,
                "working_set": store.working_set,
            },
            "verdicts": dict(sorted(store.verdicts.items())),
            "soft_max": state.value if state else None,
            "soft_max_inputs": dict(state.inputs) if state else None,
            "soft_max_drift": dict(state.drift) if state else None,
            "soft_max_computed_at": state.computed_at if state else None,
            "tightening": decision.to_payload(),
        }
        logger.info(
            "[POK] census project=%s active=%d snapshot=%s",
            project_id,
            store.active_count,
            store.measured_at,
        )
        return census

    return [
        pok_upsert,
        pok_query,
        pok_curate,
        pok_gate_answer,
        pok_census,
    ]
