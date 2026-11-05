"""The memoir tools — the privileged ``memoir`` category (#18, ari EXACTLY).

Phase 4 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase4-plan.md`` — the whole
phase; decisions D4.1-D4.16).

Five callable tools, four families (pin/unpin are one family):

* ``memoir_write(text, title?, importance?, event_at?, stars?, keepsake?,
  reason?, actor?)`` — THE HOT APPEND. One INSERT; the write never blocks
  on the embedding (the background worker owns the semantic half) and the
  result says so (``semantic_status: "pending"`` — AM-23: the model must
  be TOLD the semantic index lags).
* ``memoir_pin(id, reason, actor)`` — keepsake governance. The ``actor``
  is a CLAIM, never derived (R2): the caller names empress/emperor and a
  wrong claim is detectable at write time (the result carries the house
  provenance class beside the claim — D4.10). ``actor`` is REQUIRED.
* ``memoir_unpin(id)`` — the BLESSED signature (no ``actor``: no column
  exists to record an unpin actor). Idempotent on a non-keepsake; the
  episode itself is never deleted (DD-3's sibling rule).
* ``memoir_recall(query, k?)`` — hybrid retrieval (k default 5, hard max
  20; an out-of-range k is a TYPED REFUSAL, never a silent clamp).
* ``memoir_timeline(since?, until?, q?)`` — time-travel over ``event_at``.
  NO ``k`` parameter (the §5.4 signature is binding; the window bounds are
  named ``since``/``until`` because ``from``/``to`` are Python keywords —
  AM-42). The timeline writes NO access stats (DD-12).

THE GATE ORDER IS AUTH-FIRST (D4.2/AM-16, following the exemplar's CODE,
not its comment):

1. ``_is_memoir_authorized(caller_agent_id)`` — the FACTORY-CAPTURED
   ``agent_id`` (DD-13: never a caller-supplied scoping key) ⇒
   ``memoir_agent_unknown`` / ``memoir_not_authorized``;
2. the kill-switch AT CALL TIME (D4.16 — never boot-cached) ⇒
   ``memoir_disabled`` naming the variable;
3. argument validation (the typed field codes);
4. any DB touch.

An unauthorized caller therefore learns NOTHING about the knob's state.

THE REFUSAL VOCABULARY IS FROZEN (D4.8 — 18 codes, asserted at import):
``MEMOIR_CODES`` must match the 18 exactly or the import FAILS LOUD. The
envelope is ``Error: {tool} REFUSED — reason={code}: {message}`` and no
tool ever raises (ADR-005). MAPPING (the tool layer never re-derives a
bound — the repository owns the bounds and refuses with a code; the tool
maps it onto this vocabulary): ``body_required``/``body_too_long`` →
``memoir_body_too_long``; ``title_too_long``/``invalid_title`` →
``memoir_title_too_long``; ``importance_out_of_range`` →
``memoir_importance_out_of_range``; ``event_at_invalid`` →
``memoir_invalid_event_at``; ``invalid_stars``/``too_many_stars``/
``star_too_long`` → ``memoir_stars_out_of_bounds``; ``episode_not_found``
→ ``memoir_not_found``; ``invalid_actor`` → ``memoir_invalid_actor``;
``reason_required`` → ``memoir_reason_required``; the admission errors
(``pin_count`` / ``keepsake_zone_tokens``) → ``memoir_keepsake_cap_reached``;
the recall-service ``k_out_of_range`` → ``memoir_k_out_of_range`` and its
``invalid_window`` → ``memoir_invalid_window``. A service code with NO
frozen counterpart (``query_required``) falls back to
``memoir_internal_error`` with the TRUE message preserved — the
vocabulary is frozen, so the tool never invents a code (the same fallback
covers every unexpected internal failure).

WRITE-PATH PURITY (D4.11/DD-20/AM-12, pinned by
``tests/unit/test_memoir_write_purity_tool.py``): this module contains
ZERO references to the embedder, to ``client.embeddings``, or to
``store_embedding``; the write body performs EXACTLY ONE
``insert_episode`` (the keepsake admission lives INSIDE it — wave 2's
pinned-precede contract); the embedder is reachable only from
``daemon/services/memoir_embedding_worker.py``. A consequence, recorded
honestly: the v1 tool lane constructs
:class:`~daemon.services.memoir_retrieval.MemoirRetrievalService` with NO
query embedder, so the tool-lane recall runs LEXICAL-ONLY
(``semantic=off`` in the verbatim budget footer; the
``missing_embedding`` count still discloses the un-embedded candidates).
The semantic recall lane needs an embedder injection point that v1 does
not build — wiring one would put an embedder symbol inside this module,
which AM-12 forbids. Recorded in ``phase-notes-p4p6.md``.

HOUSE CONVENTIONS HONORED HERE: the repository/service wrap the CALLER'S
engine (``manager._engine``) — this module never builds an engine and
never reads an ambient ``POSTGRES_*`` variable; every DB touch runs off
the event loop under ``asyncio.to_thread`` (the v0.27.14 event-loop
convention); tools never raise.

DISCOVERABILITY (D0.7): the resolved knob state is embedded in the
tools' help from ``daemon.tools.memoir_knob.memoir_tool_help()`` — the
wording has exactly one home (the knob module).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Annotated, Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ..config import MEMOIR_ENABLED_ENV_VAR
from ..repositories.memoir.models import PIN_ACTORS
from ..repositories.memoir.repository import (
    MemoirAdmissionError,
    MemoirRepository,
    MemoirWriteError,
    as_aware_utc,
)
from ..services.memoir_retrieval import (
    MEMOIR_RECALL_DEFAULT_K,
    MEMOIR_RECALL_MAX_K,
    MemoirRecallError,
    MemoirRetrievalService,
)
from ..services.timestamps import now_utc, to_utc_iso
from ._tool_registry import register_tool_category
from .memoir_knob import memoir_enabled, memoir_tool_help

logger = logging.getLogger(__name__)

CATEGORY_DOC = """\
The memoir store — MY durable episodic memory (first-person narrative),
keepsake governance, recall and timeline reads.

Tools in this category:
- ``memoir_write`` — append one episodic memory (the hot append; never
  blocks on the embedding)
- ``memoir_pin`` / ``memoir_unpin`` — keepsake governance (cap-refused,
  never evicted)
- ``memoir_recall`` — hybrid recall over my own episodes (budgeted)
- ``memoir_timeline`` — time-travel over my episodes by event time

The wake briefing shows me the keepsake zone + recent episodes each
session; these tools are the write/read path behind it.
"""

#: The category key — EXACTLY ``memoir`` (D4.1: never "memoir-store" /
#: "ari-memoir"; the notify N7 exact-key discipline).
MEMOIR_CATEGORY_KEY = "memoir"

#: The tool names (snake_case — the self_alarm/board_link convention).
MEMOIR_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "memoir_write",
        "memoir_pin",
        "memoir_unpin",
        "memoir_recall",
        "memoir_timeline",
    }
)

#: The only agent allowed to call ANY memoir tool (the runtime gate —
#: layer 2 of the two-layer posture; the category allow entry is layer 1).
MEMOIR_AUTHORIZED_AGENT = "ari"

# ── D4.8: the typed error vocabulary — 18 codes, FROZEN ────────────────
MEMOIR_CODE_DISABLED = "memoir_disabled"
MEMOIR_CODE_AGENT_UNKNOWN = "memoir_agent_unknown"
MEMOIR_CODE_NOT_AUTHORIZED = "memoir_not_authorized"
MEMOIR_CODE_REASON_REQUIRED = "memoir_reason_required"
MEMOIR_CODE_ACTOR_REQUIRED = "memoir_actor_required"
MEMOIR_CODE_INVALID_ACTOR = "memoir_invalid_actor"
MEMOIR_CODE_KEEPSAKE_CAP_REACHED = "memoir_keepsake_cap_reached"
MEMOIR_CODE_NOT_FOUND = "memoir_not_found"
MEMOIR_CODE_NOT_A_KEEPSAKE = "memoir_not_a_keepsake"
MEMOIR_CODE_IMPORTANCE_OUT_OF_RANGE = "memoir_importance_out_of_range"
MEMOIR_CODE_BODY_TOO_LONG = "memoir_body_too_long"
MEMOIR_CODE_BUDGET_EXCEEDED = "memoir_budget_exceeded"
MEMOIR_CODE_INTERNAL_ERROR = "memoir_internal_error"
#: The five W10 field codes completing the set.
MEMOIR_CODE_K_OUT_OF_RANGE = "memoir_k_out_of_range"
MEMOIR_CODE_INVALID_WINDOW = "memoir_invalid_window"
MEMOIR_CODE_TITLE_TOO_LONG = "memoir_title_too_long"
MEMOIR_CODE_STARS_OUT_OF_BOUNDS = "memoir_stars_out_of_bounds"
MEMOIR_CODE_INVALID_EVENT_AT = "memoir_invalid_event_at"

#: The frozen vocabulary — validated loudly at import (the notify
#: precedent: a mistyped code must fail loud, never diff).
MEMOIR_CODES: frozenset[str] = frozenset(
    {
        MEMOIR_CODE_DISABLED,
        MEMOIR_CODE_AGENT_UNKNOWN,
        MEMOIR_CODE_NOT_AUTHORIZED,
        MEMOIR_CODE_REASON_REQUIRED,
        MEMOIR_CODE_ACTOR_REQUIRED,
        MEMOIR_CODE_INVALID_ACTOR,
        MEMOIR_CODE_KEEPSAKE_CAP_REACHED,
        MEMOIR_CODE_NOT_FOUND,
        MEMOIR_CODE_NOT_A_KEEPSAKE,
        MEMOIR_CODE_IMPORTANCE_OUT_OF_RANGE,
        MEMOIR_CODE_BODY_TOO_LONG,
        MEMOIR_CODE_BUDGET_EXCEEDED,
        MEMOIR_CODE_INTERNAL_ERROR,
        MEMOIR_CODE_K_OUT_OF_RANGE,
        MEMOIR_CODE_INVALID_WINDOW,
        MEMOIR_CODE_TITLE_TOO_LONG,
        MEMOIR_CODE_STARS_OUT_OF_BOUNDS,
        MEMOIR_CODE_INVALID_EVENT_AT,
    }
)

assert len(MEMOIR_CODES) == 18, (
    "memoir: the frozen refusal vocabulary must hold EXACTLY 18 codes; "
    f"got {len(MEMOIR_CODES)}: {sorted(MEMOIR_CODES)}"
)
for _code in (
    MEMOIR_CODE_DISABLED,
    MEMOIR_CODE_AGENT_UNKNOWN,
    MEMOIR_CODE_NOT_AUTHORIZED,
    MEMOIR_CODE_REASON_REQUIRED,
    MEMOIR_CODE_ACTOR_REQUIRED,
    MEMOIR_CODE_INVALID_ACTOR,
    MEMOIR_CODE_KEEPSAKE_CAP_REACHED,
    MEMOIR_CODE_NOT_FOUND,
    MEMOIR_CODE_NOT_A_KEEPSAKE,
    MEMOIR_CODE_IMPORTANCE_OUT_OF_RANGE,
    MEMOIR_CODE_BODY_TOO_LONG,
    MEMOIR_CODE_BUDGET_EXCEEDED,
    MEMOIR_CODE_INTERNAL_ERROR,
    MEMOIR_CODE_K_OUT_OF_RANGE,
    MEMOIR_CODE_INVALID_WINDOW,
    MEMOIR_CODE_TITLE_TOO_LONG,
    MEMOIR_CODE_STARS_OUT_OF_BOUNDS,
    MEMOIR_CODE_INVALID_EVENT_AT,
):
    if _code not in MEMOIR_CODES:  # pragma: no cover — fails loud at import
        raise RuntimeError(
            f"memoir: error code {_code!r} missing from MEMOIR_CODES"
        )

#: The knob variable, named in the ``memoir_disabled`` refusal (D4.16).
#: One import — the spelling has exactly one home (daemon.config).

# ── the repository/service code → frozen-vocabulary mapping ────────────
_WRITE_CODE_MAP: dict[str, str] = {
    "body_required": MEMOIR_CODE_BODY_TOO_LONG,
    "body_too_long": MEMOIR_CODE_BODY_TOO_LONG,
    "title_too_long": MEMOIR_CODE_TITLE_TOO_LONG,
    "invalid_title": MEMOIR_CODE_TITLE_TOO_LONG,
    "importance_out_of_range": MEMOIR_CODE_IMPORTANCE_OUT_OF_RANGE,
    "event_at_invalid": MEMOIR_CODE_INVALID_EVENT_AT,
    "invalid_stars": MEMOIR_CODE_STARS_OUT_OF_BOUNDS,
    "too_many_stars": MEMOIR_CODE_STARS_OUT_OF_BOUNDS,
    "star_too_long": MEMOIR_CODE_STARS_OUT_OF_BOUNDS,
    "episode_not_found": MEMOIR_CODE_NOT_FOUND,
    "invalid_actor": MEMOIR_CODE_INVALID_ACTOR,
    "reason_required": MEMOIR_CODE_REASON_REQUIRED,
}
_RECALL_CODE_MAP: dict[str, str] = {
    "k_out_of_range": MEMOIR_CODE_K_OUT_OF_RANGE,
    "invalid_window": MEMOIR_CODE_INVALID_WINDOW,
}


def _is_memoir_authorized(agent_id: str) -> bool:
    """Is this agent allowed to call the memoir tools?

    ONE shared helper, called by ALL FIVE tool bodies — the set lives in
    exactly one place (the ``_is_metrics_authorized`` shape,
    ``daemon/tools/metrics_read.py:403-414``).

    Fail-closed: a blank or non-``str`` ``agent_id`` is NEVER authorized.
    The identity is the FACTORY-CAPTURED ``agent_id`` (DD-13) — never a
    caller-supplied parameter.
    """
    if not isinstance(agent_id, str) or not agent_id:
        return False
    return agent_id == MEMOIR_AUTHORIZED_AGENT


def _refuse(label: str, code: str, message: str) -> str:
    """The house REFUSED envelope (D4.8)."""
    return f"Error: {label} REFUSED — reason={code}: {message}"


def _memoir_repository_for(manager: Any) -> MemoirRepository | None:
    """Wrap the CALLER'S engine (``manager._engine``) — never build one.

    This module never constructs an engine and never reads an ambient
    ``POSTGRES_*`` variable (the wave-1 incident's fence). ``None`` means
    the manager carries no engine (a degraded/test boot) — the tool then
    refuses ``memoir_internal_error`` naming the gap, loudly, but NEVER
    raises (D4.6: a degraded boot leaves the tools PRESENT and refusing).
    """
    engine = getattr(manager, "_engine", None)
    if engine is None:
        return None
    return MemoirRepository(engine)


def _memoir_service_for(manager: Any) -> MemoirRetrievalService | None:
    """Build the retrieval service over the caller's engine.

    NO query embedder is passed (see the module docstring's purity
    consequence): the v1 tool-lane recall is lexical-only and the verbatim
    budget footer discloses it (``semantic=off``).
    """
    repo = _memoir_repository_for(manager)
    if repo is None:
        return None
    return MemoirRetrievalService(repo)


def _map_write_error(exc: MemoirWriteError, label: str) -> str:
    """Map a repository write refusal onto the frozen vocabulary."""
    code = _WRITE_CODE_MAP.get(str(getattr(exc, "code", "")))
    if code is None:
        # An unmapped repository code falls back to the catch-all with
        # the true message preserved — never a code invention.
        code = MEMOIR_CODE_INTERNAL_ERROR
    return _refuse(label, code, str(getattr(exc, "message", exc)))


#: The cap on the pin inventory quoted inside one admission refusal (the
#: admission itself caps at 12; the quote is bounded defensively).
MEMOIR_INVENTORY_LIMIT = 12


def _map_admission_error(exc: MemoirAdmissionError, label: str) -> str:
    """Map a pin-admission refusal (D2.12) onto ``memoir_keepsake_cap_reached``.

    The refusal NAMES the binding constraint and the current pins (the
    admission's own message already does; the pin inventory rides along so
    the agent can see exactly what is in the way — nothing is ever evicted,
    DD-3).
    """
    pins = list(getattr(exc, "pins", ()) or [])
    message = str(getattr(exc, "message", exc))
    if pins:
        inventory = "; ".join(
            f"{pin.get('id')}/{pin.get('title') or '(untitled)'}"
            f" by {pin.get('pinned_by')}"
            for pin in pins[: MEMOIR_INVENTORY_LIMIT]
        )
        message = f"{message} — current pins: {inventory}"
    return _refuse(label, MEMOIR_CODE_KEEPSAKE_CAP_REACHED, message)


def _map_recall_error(exc: MemoirRecallError, label: str) -> str:
    """Map a retrieval-service refusal onto the frozen vocabulary."""
    code = _RECALL_CODE_MAP.get(str(getattr(exc, "code", "")))
    if code is None:
        # ``query_required`` (and any future service code with no frozen
        # counterpart) falls back to the catch-all with the TRUE message
        # preserved — the vocabulary is frozen; no code invention.
        code = MEMOIR_CODE_INTERNAL_ERROR
    return _refuse(label, code, str(getattr(exc, "message", exc)))


def _provenance_class_for(manager: Any, instance_id: str) -> str:
    """The house provenance class, surfaced beside the pin CLAIM (D4.10).

    ``capture_provenance_class`` is defensive; one more swallow keeps the
    claim's corroboration from ever killing the tool.
    """
    try:
        from .provenance import capture_provenance_class

        capture = capture_provenance_class(manager, instance_id)
        return str(capture.get("provenance_class") or "")
    except Exception:  # noqa: BLE001 — defensive (D4.10)
        return ""


def _parse_event_at(value: Any) -> datetime | None:
    """Parse the tool's ``event_at`` (ISO-8601) — refused, never clamped.

    Naive digits are interpreted as UTC wall-clock (the house
    assume-UTC policy, ``as_aware_utc``); the house timezone is
    Asia/Ho_Chi_Minh (+07:00) — a local wall-clock string must carry its
    offset or be pre-converted to UTC. Raises :class:`MemoirWriteError`
    with the repository's own ``event_at_invalid`` code so the mapping
    stays single-sourced.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return as_aware_utc(value)
    if not isinstance(value, str) or not value.strip():
        raise MemoirWriteError(
            "event_at_invalid", "the event time must be an ISO-8601 string"
        )
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise MemoirWriteError(
            "event_at_invalid",
            f"the event time {value!r} is not an ISO-8601 timestamp",
        ) from exc
    return as_aware_utc(parsed)


def _render_episode_block(projection: dict[str, Any]) -> str:
    """One shipped episode, rendered for the tool result (D4.9's field set).

    The projection is the SERVICE'S shipped dict (the clamped body, the
    measured bytes) — the fields are surfaced, never re-derived.
    """
    stars = projection.get("stars")
    stars_text = ", ".join(stars) if stars else "(none)"
    lines = [
        f"- id: {projection.get('id')}",
        f"  title: {projection.get('title') or '(untitled)'}",
        f"  body: {projection.get('body') or ''}",
        f"  created_at: {projection.get('created_at')}",
        f"  event_at: {projection.get('event_at')}",
        f"  importance: {projection.get('importance')}",
        f"  stars: {stars_text}",
        f"  is_keepsake: {str(bool(projection.get('is_keepsake'))).lower()}",
        f"  pinned_by: {projection.get('pinned_by') or '(none)'}",
        f"  last_accessed_at: {projection.get('last_accessed_at') or '(never)'}",
    ]
    return "\n".join(lines)


def create_memoir_tools(
    manager: Any,
    *,
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: str | None = None,
) -> list:
    """Build the five memoir tools for one instance/agent.

    Args:
        manager: The ``InstanceManager`` — the tools read ONLY its
            ``_engine`` handle (the repository wraps it) and its
            ``config`` (the knob's config tier). Both are read
            defensively: a test double without them yields typed
            refusals, never raises.
        current_instance_id: The caller's instance id — the provenance
            capture anchor (D4.10).
        agent_id: The caller's agent id. Captured at FACTORY time and
            checked by :func:`_is_memoir_authorized` in every tool body
            (DD-13: the factory capture is the identity; a caller-supplied
            scoping key is the L3 anti-pattern).
        agent_tag: The caller's version tag (signature parity; unused).

    Returns:
        A list of the five ``@tool``-decorated callables. NO
        ``*_if_available`` wrapper (D4.6): a degraded boot leaves the
        tools PRESENT and loudly refusing.
    """
    caller_agent_id = agent_id or ""
    caller_instance_id = current_instance_id or ""

    class MemoirWriteInput(BaseModel):
        """Input schema for memoir_write."""

        text: Annotated[
            str,
            Field(
                description=(
                    "The memory itself — FIRST-PERSON narrative about me "
                    "and the user (what happened, what I learned, what "
                    "matters). Not project state, task progress or code."
                )
            ),
        ]
        title: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Optional short title (<=200 chars) — the first line "
                    "of the keepsake zone rendering."
                ),
            ),
        ]
        importance: Annotated[
            int,
            Field(
                default=5,
                description=(
                    "1-10 (default 5). 9-10 = shaped who we are; 1-2 = "
                    "noise. Affects recall ranking."
                ),
            ),
        ]
        event_at: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Optional ISO-8601 timestamp of WHEN the event "
                    "happened (defaults to now). Naive digits are read "
                    "as UTC; the house timezone is Asia/Ho_Chi_Minh "
                    "(+07:00) — carry the offset or pre-convert."
                ),
            ),
        ]
        stars: Annotated[
            list[str] | None,
            Field(
                default=None,
                description=(
                    "Optional flat list of mention strings (@people, "
                    "#projects) — self-declared, verbatim, <=16 entries."
                ),
            ),
        ]
        keepsake: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "True = pin as a keepsake (the always-injected zone). "
                    "Requires BOTH reason and actor; refused at the cap "
                    "(nothing is ever evicted)."
                ),
            ),
        ]
        reason: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Required when keepsake=true — why this memory must "
                    "always be in context."
                ),
            ),
        ]
        actor: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Required when keepsake=true — WHO decided: exactly "
                    "'empress' or 'emperor'. A CLAIM, never derived; a "
                    "wrong claim is detectable (the result carries the "
                    "turn's provenance class beside it)."
                ),
            ),
        ]

    class MemoirPinInput(BaseModel):
        """Input schema for memoir_pin."""

        id: Annotated[
            str,
            Field(description="The episode id to pin as a keepsake."),
        ]
        reason: Annotated[
            str,
            Field(
                description=(
                    "Why this memory must always be in context — part of "
                    "the pin's meaning, never optional."
                ),
            ),
        ]
        actor: Annotated[
            str,
            Field(
                description=(
                    "WHO decided: exactly 'empress' or 'emperor'. A CLAIM, "
                    "never derived."
                ),
            ),
        ]

    class MemoirUnpinInput(BaseModel):
        """Input schema for memoir_unpin."""

        id: Annotated[
            str,
            Field(
                description=(
                    "The episode id to unpin. The episode itself is kept; "
                    "only the pin (and its claim) is cleared. Idempotent."
                ),
            ),
        ]

    class MemoirRecallInput(BaseModel):
        """Input schema for memoir_recall."""

        query: Annotated[
            str,
            Field(
                description=(
                    "What to remember — free text matched against my own "
                    "episodes (lexical always; semantic when indexed)."
                ),
            ),
        ]
        k: Annotated[
            int,
            Field(
                default=5,
                description=(
                    "How many episodes (default 5, hard max 20). An "
                    "out-of-range k is REFUSED, never silently clamped."
                ),
            ),
        ]

    class MemoirTimelineInput(BaseModel):
        """Input schema for memoir_timeline."""

        since: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Inclusive lower bound on event_at (ISO-8601; naive "
                    "digits read as UTC)."
                ),
            ),
        ]
        until: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Inclusive upper bound on event_at (ISO-8601; naive "
                    "digits read as UTC)."
                ),
            ),
        ]
        q: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Optional lexical narrowing term inside the window."
                ),
            ),
        ]

    @register_tool_category(MEMOIR_CATEGORY_KEY)
    @tool(args_schema=MemoirWriteInput)
    async def memoir_write(
        text: str,
        title: str | None = None,
        importance: int = 5,
        event_at: str | None = None,
        stars: list[str] | None = None,
        keepsake: bool = False,
        reason: str | None = None,
        actor: str | None = None,
    ) -> dict | str:
        """Append one episodic memory to my memoir store. Use tool_help("memoir_write") for details."""
        label = "memoir_write"
        try:
            # ── D4.2 gate 1: the factory-captured identity, FIRST ────
            if not _is_memoir_authorized(caller_agent_id):
                if not caller_agent_id:
                    return _refuse(
                        label,
                        MEMOIR_CODE_AGENT_UNKNOWN,
                        "the caller's agent identity is unknown; the memoir "
                        f"store is {MEMOIR_AUTHORIZED_AGENT}-only",
                    )
                return _refuse(
                    label,
                    MEMOIR_CODE_NOT_AUTHORIZED,
                    f"the memoir store is {MEMOIR_AUTHORIZED_AGENT}-only "
                    f"(caller: {caller_agent_id})",
                )
            # ── D4.2 gate 2: the kill-switch, AT CALL TIME ───────────
            if not memoir_enabled(getattr(manager, "config", None)):
                return _refuse(
                    label,
                    MEMOIR_CODE_DISABLED,
                    f"the memoir surface is disabled ({MEMOIR_ENABLED_ENV_VAR}"
                    "=false); nothing was recorded",
                )
            # ── D4.2 gate 3: argument validation (pre-DB) ────────────
            if not isinstance(text, str) or not text.strip():
                return _refuse(
                    label,
                    MEMOIR_CODE_BODY_TOO_LONG,
                    "the episode body is required (a memoir_write without "
                    "content records nothing)",
                )
            parsed_event = _parse_event_at(event_at)
            if keepsake:
                if not isinstance(reason, str) or not reason.strip():
                    return _refuse(
                        label,
                        MEMOIR_CODE_REASON_REQUIRED,
                        "a keepsake requires a reason — why this memory must "
                        "always be in context",
                    )
                if not isinstance(actor, str) or not actor.strip():
                    return _refuse(
                        label,
                        MEMOIR_CODE_ACTOR_REQUIRED,
                        "a keepsake requires an actor claim: exactly "
                        "'empress' or 'emperor' (required iff keepsake=true)",
                    )
                if actor not in PIN_ACTORS:
                    return _refuse(
                        label,
                        MEMOIR_CODE_INVALID_ACTOR,
                        f"the pin actor must be one of {sorted(PIN_ACTORS)}; "
                        f"got {actor!r} — the claim is never derived",
                    )
            # ── D4.2 gate 4: the DB touch (off-loop, ONE insert) ─────
            repo = _memoir_repository_for(manager)
            if repo is None:
                return _refuse(
                    label,
                    MEMOIR_CODE_INTERNAL_ERROR,
                    "the memoir store is not available on this manager "
                    "(no engine); nothing was recorded",
                )
            created = now_utc()
            new_id = await asyncio.to_thread(
                repo.insert_episode,
                agent_id=caller_agent_id,
                body=text,
                title=title,
                importance=importance,
                stars=stars,
                event_at=parsed_event,
                created_at=created,
                is_keepsake=bool(keepsake),
                pinned_by=actor if keepsake else None,
                pinned_reason=reason if keepsake else None,
            )
            result: dict[str, Any] = {
                "episode_id": new_id,
                "created_at": to_utc_iso(created),
                "event_at": to_utc_iso(parsed_event or created),
                "is_keepsake": bool(keepsake),
                # AM-23: the embedding-pending note IS the contract — the
                # write never blocks on the semantic half.
                "semantic_status": "pending",
            }
            if keepsake:
                result["pinned_by"] = actor
                result["provenance_class"] = _provenance_class_for(
                    manager, caller_instance_id
                )
            return result
        except MemoirAdmissionError as exc:
            return _map_admission_error(exc, label)
        except MemoirWriteError as exc:
            return _map_write_error(exc, label)
        except Exception:  # noqa: BLE001 — house pattern: error string, never raise
            logger.exception("memoir_write failed")
            return _refuse(
                label,
                MEMOIR_CODE_INTERNAL_ERROR,
                "an unexpected error occurred (logged server-side); nothing "
                "was recorded",
            )

    @register_tool_category(MEMOIR_CATEGORY_KEY)
    @tool(args_schema=MemoirPinInput)
    async def memoir_pin(
        id: str,
        reason: str,
        actor: str,
    ) -> dict | str:
        """Pin an existing episode as a keepsake (cap-refused, never evicted). Use tool_help("memoir_pin") for details."""
        label = "memoir_pin"
        try:
            if not _is_memoir_authorized(caller_agent_id):
                if not caller_agent_id:
                    return _refuse(
                        label,
                        MEMOIR_CODE_AGENT_UNKNOWN,
                        "the caller's agent identity is unknown; the memoir "
                        f"store is {MEMOIR_AUTHORIZED_AGENT}-only",
                    )
                return _refuse(
                    label,
                    MEMOIR_CODE_NOT_AUTHORIZED,
                    f"the memoir store is {MEMOIR_AUTHORIZED_AGENT}-only "
                    f"(caller: {caller_agent_id})",
                )
            if not memoir_enabled(getattr(manager, "config", None)):
                return _refuse(
                    label,
                    MEMOIR_CODE_DISABLED,
                    f"the memoir surface is disabled ({MEMOIR_ENABLED_ENV_VAR}"
                    "=false); nothing was changed",
                )
            if not isinstance(id, str) or not id.strip():
                return _refuse(
                    label,
                    MEMOIR_CODE_NOT_FOUND,
                    "an episode id is required",
                )
            if not isinstance(reason, str) or not reason.strip():
                return _refuse(
                    label,
                    MEMOIR_CODE_REASON_REQUIRED,
                    "a pin requires a reason — why this memory must always "
                    "be in context",
                )
            if not isinstance(actor, str) or not actor.strip():
                return _refuse(
                    label,
                    MEMOIR_CODE_ACTOR_REQUIRED,
                    "a pin requires an actor claim: exactly 'empress' or "
                    "'emperor'",
                )
            if actor not in PIN_ACTORS:
                return _refuse(
                    label,
                    MEMOIR_CODE_INVALID_ACTOR,
                    f"the pin actor must be one of {sorted(PIN_ACTORS)}; "
                    f"got {actor!r} — the claim is never derived",
                )
            repo = _memoir_repository_for(manager)
            if repo is None:
                return _refuse(
                    label,
                    MEMOIR_CODE_INTERNAL_ERROR,
                    "the memoir store is not available on this manager "
                    "(no engine); nothing was changed",
                )
            # Re-pin awareness (D4.9): pinning an already-pinned episode
            # OVERWRITES the claim — the result says so.
            keepsakes = await asyncio.to_thread(
                repo.fetch_keepsakes, caller_agent_id
            )
            re_pin = any(row.id == id for row in (keepsakes or []))
            await asyncio.to_thread(
                repo.set_pin, id, actor=actor, reason=reason
            )
            kept = await asyncio.to_thread(repo.fetch_keepsakes, caller_agent_id)
            pinned_at = None
            for row in kept or []:
                if row.id == id:
                    pinned_at = to_utc_iso(row.pinned_at)
                    break
            result = {
                "episode_id": id,
                "is_keepsake": True,
                "pinned_by": actor,
                "pinned_at": pinned_at,
                "re_pinned": bool(re_pin),
                "provenance_class": _provenance_class_for(
                    manager, caller_instance_id
                ),
            }
            if re_pin:
                result["note"] = (
                    "re-pinned: the episode was already a keepsake and the "
                    "claim (actor + reason) was overwritten"
                )
            return result
        except MemoirAdmissionError as exc:
            return _map_admission_error(exc, label)
        except MemoirWriteError as exc:
            return _map_write_error(exc, label)
        except Exception:  # noqa: BLE001 — house pattern: error string, never raise
            logger.exception("memoir_pin failed")
            return _refuse(
                label,
                MEMOIR_CODE_INTERNAL_ERROR,
                "an unexpected error occurred (logged server-side); nothing "
                "was changed",
            )

    @register_tool_category(MEMOIR_CATEGORY_KEY)
    @tool(args_schema=MemoirUnpinInput)
    async def memoir_unpin(id: str) -> dict | str:
        """Unpin a keepsake — the episode itself is kept, only the pin is cleared. Use tool_help("memoir_unpin") for details."""
        label = "memoir_unpin"
        try:
            if not _is_memoir_authorized(caller_agent_id):
                if not caller_agent_id:
                    return _refuse(
                        label,
                        MEMOIR_CODE_AGENT_UNKNOWN,
                        "the caller's agent identity is unknown; the memoir "
                        f"store is {MEMOIR_AUTHORIZED_AGENT}-only",
                    )
                return _refuse(
                    label,
                    MEMOIR_CODE_NOT_AUTHORIZED,
                    f"the memoir store is {MEMOIR_AUTHORIZED_AGENT}-only "
                    f"(caller: {caller_agent_id})",
                )
            if not memoir_enabled(getattr(manager, "config", None)):
                return _refuse(
                    label,
                    MEMOIR_CODE_DISABLED,
                    f"the memoir surface is disabled ({MEMOIR_ENABLED_ENV_VAR}"
                    "=false); nothing was changed",
                )
            if not isinstance(id, str) or not id.strip():
                return _refuse(
                    label,
                    MEMOIR_CODE_NOT_FOUND,
                    "an episode id is required",
                )
            repo = _memoir_repository_for(manager)
            if repo is None:
                return _refuse(
                    label,
                    MEMOIR_CODE_INTERNAL_ERROR,
                    "the memoir store is not available on this manager "
                    "(no engine); nothing was changed",
                )
            episodes = await asyncio.to_thread(
                repo.fetch_latest, caller_agent_id, None
            )
            episode = None
            for row in episodes or []:
                if row.id == id:
                    episode = row
                    break
            if episode is None:
                return _refuse(
                    label,
                    MEMOIR_CODE_NOT_FOUND,
                    f"no episode with id {id!r}",
                )
            if not bool(episode.is_keepsake):
                # D4.9/AC5: idempotent no-op — nothing to clear, no error.
                return {
                    "episode_id": id,
                    "unpinned": False,
                    "note": (
                        "idempotent no-op: the episode is not a keepsake "
                        "(nothing to clear; the episode is kept)"
                    ),
                }
            await asyncio.to_thread(repo.clear_pin, id)
            return {
                "episode_id": id,
                "unpinned": True,
                "note": (
                    "the pin (and its whole claim — actor, reason, stamp) is "
                    "cleared; the episode itself is kept"
                ),
            }
        except MemoirWriteError as exc:
            return _map_write_error(exc, label)
        except Exception:  # noqa: BLE001 — house pattern: error string, never raise
            logger.exception("memoir_unpin failed")
            return _refuse(
                label,
                MEMOIR_CODE_INTERNAL_ERROR,
                "an unexpected error occurred (logged server-side); nothing "
                "was changed",
            )

    @register_tool_category(MEMOIR_CATEGORY_KEY)
    @tool(args_schema=MemoirRecallInput)
    async def memoir_recall(query: str, k: int = MEMOIR_RECALL_DEFAULT_K) -> str:
        """Recall my own episodes by relevance (budgeted hybrid retrieval). Use tool_help("memoir_recall") for details."""
        label = "memoir_recall"
        try:
            if not _is_memoir_authorized(caller_agent_id):
                if not caller_agent_id:
                    return _refuse(
                        label,
                        MEMOIR_CODE_AGENT_UNKNOWN,
                        "the caller's agent identity is unknown; the memoir "
                        f"store is {MEMOIR_AUTHORIZED_AGENT}-only",
                    )
                return _refuse(
                    label,
                    MEMOIR_CODE_NOT_AUTHORIZED,
                    f"the memoir store is {MEMOIR_AUTHORIZED_AGENT}-only "
                    f"(caller: {caller_agent_id})",
                )
            if not memoir_enabled(getattr(manager, "config", None)):
                return _refuse(
                    label,
                    MEMOIR_CODE_DISABLED,
                    f"the memoir surface is disabled ({MEMOIR_ENABLED_ENV_VAR}"
                    "=false); the memory is unreadable while disabled",
                )
            # D4.2 gate 3 — the k bounds are REFUSED pre-DB, never clamped.
            try:
                k_value = int(k)
            except (TypeError, ValueError):
                return _refuse(
                    label,
                    MEMOIR_CODE_K_OUT_OF_RANGE,
                    f"k must be an integer; got {k!r}",
                )
            if k_value < 1 or k_value > MEMOIR_RECALL_MAX_K:
                return _refuse(
                    label,
                    MEMOIR_CODE_K_OUT_OF_RANGE,
                    f"k must be between 1 and {MEMOIR_RECALL_MAX_K}; got "
                    f"{k_value} (an out-of-range k is refused, never "
                    "silently clamped)",
                )
            service = _memoir_service_for(manager)
            if service is None:
                return _refuse(
                    label,
                    MEMOIR_CODE_INTERNAL_ERROR,
                    "the memoir store is not available on this manager "
                    "(no engine)",
                )
            result = await asyncio.to_thread(
                service.recall, caller_agent_id, query, k_value
            )
            # The FOOTER ships VERBATIM (its bytes are pinned by wave 2's
            # budget pack — the tool never reformats it).
            blocks = "\n".join(
                _render_episode_block(episode)
                for episode in (result.episodes or [])
            )
            header = (
                f"memoir_recall for {caller_agent_id}: "
                f"{len(result.episodes or [])} episode(s), "
                f"{result.dropped} dropped for budget, "
                f"rank={result.rank_basis}"
            )
            return f"{header}\n{blocks}\n{result.footer}".strip()
        except MemoirRecallError as exc:
            return _map_recall_error(exc, label)
        except Exception:  # noqa: BLE001 — house pattern: error string, never raise
            logger.exception("memoir_recall failed")
            return _refuse(
                label,
                MEMOIR_CODE_INTERNAL_ERROR,
                "an unexpected error occurred (logged server-side)",
            )

    @register_tool_category(MEMOIR_CATEGORY_KEY)
    @tool(args_schema=MemoirTimelineInput)
    async def memoir_timeline(
        since: str | None = None,
        until: str | None = None,
        q: str | None = None,
    ) -> str:
        """Time-travel over my episodes by event time (writes no stats). Use tool_help("memoir_timeline") for details."""
        label = "memoir_timeline"
        try:
            if not _is_memoir_authorized(caller_agent_id):
                if not caller_agent_id:
                    return _refuse(
                        label,
                        MEMOIR_CODE_AGENT_UNKNOWN,
                        "the caller's agent identity is unknown; the memoir "
                        f"store is {MEMOIR_AUTHORIZED_AGENT}-only",
                    )
                return _refuse(
                    label,
                    MEMOIR_CODE_NOT_AUTHORIZED,
                    f"the memoir store is {MEMOIR_AUTHORIZED_AGENT}-only "
                    f"(caller: {caller_agent_id})",
                )
            if not memoir_enabled(getattr(manager, "config", None)):
                return _refuse(
                    label,
                    MEMOIR_CODE_DISABLED,
                    f"the memoir surface is disabled ({MEMOIR_ENABLED_ENV_VAR}"
                    "=false); the memory is unreadable while disabled",
                )
            # D4.2 gate 3 — the window bounds are parsed pre-DB. An
            # unparseable bound is an event-time validation failure
            # (memoir_invalid_event_at, naming the parameter); an
            # inverted window is memoir_invalid_window (AM-42).
            try:
                since_dt = _parse_event_at(since)
            except MemoirWriteError as exc:
                return _refuse(
                    label,
                    MEMOIR_CODE_INVALID_EVENT_AT,
                    f"since: {exc.message}",
                )
            try:
                until_dt = _parse_event_at(until)
            except MemoirWriteError as exc:
                return _refuse(
                    label,
                    MEMOIR_CODE_INVALID_EVENT_AT,
                    f"until: {exc.message}",
                )
            if (
                since_dt is not None
                and until_dt is not None
                and since_dt > until_dt
            ):
                return _refuse(
                    label,
                    MEMOIR_CODE_INVALID_WINDOW,
                    "the timeline window is inverted: since is after until",
                )
            service = _memoir_service_for(manager)
            if service is None:
                return _refuse(
                    label,
                    MEMOIR_CODE_INTERNAL_ERROR,
                    "the memoir store is not available on this manager "
                    "(no engine)",
                )
            result = await asyncio.to_thread(
                service.timeline,
                caller_agent_id,
                since_dt,
                until_dt,
                q,
            )
            # The FOOTER ships VERBATIM; the timeline marks NO stats
            # (DD-12) — the footer's stats_updated=false says so.
            blocks = "\n".join(
                _render_episode_block(episode)
                for episode in (result.episodes or [])
            )
            header = (
                f"memoir_timeline for {caller_agent_id}: "
                f"{len(result.episodes or [])} episode(s), "
                f"{result.dropped} dropped for budget, "
                f"rank={result.rank_basis}"
            )
            return f"{header}\n{blocks}\n{result.footer}".strip()
        except MemoirRecallError as exc:
            return _map_recall_error(exc, label)
        except Exception:  # noqa: BLE001 — house pattern: error string, never raise
            logger.exception("memoir_timeline failed")
            return _refuse(
                label,
                MEMOIR_CODE_INTERNAL_ERROR,
                "an unexpected error occurred (logged server-side)",
            )

    # ── the tool_help surface (the ``_full_doc_`` contract) ─────────────
    # D0.7: the resolved knob state is embedded from the knob module's
    # own text — the wording has exactly one home.
    memoir_help = memoir_tool_help(getattr(manager, "config", None))
    shared_tail = (
        "\n\nKill-switch state right now:\n" + memoir_help
    )
    memoir_write._full_doc_ = (
        "Append ONE episodic memory to your memoir store (first-person "
        "narrative about you and the user — what happened, what you "
        "learned, what matters). The write NEVER blocks on the semantic "
        "index: the embedding is applied by a background worker and the "
        "result says so (semantic_status=pending) — the episode is "
        "lexically searchable immediately, semantically a moment later.\n\n"
        "keepsake=true pins the memory into the ALWAYS-injected zone: it "
        "requires BOTH reason (why it must always be in context) and "
        "actor (exactly 'empress' or 'emperor' — a CLAIM, never derived; "
        "the result carries the turn's provenance class beside the claim "
        "so a wrong claim is detectable). Keepsakes are capped (12 pins / "
        "a 4,000-token zone) and NOTHING is ever evicted — a refused "
        "keepsake names what is in the way.\n\n"
        "event_at is ISO-8601; naive digits are read as UTC (house "
        "timezone Asia/Ho_Chi_Minh, +07:00 — carry the offset or "
        "pre-convert); results are UTC ISO-8601." + shared_tail
    )
    memoir_pin._full_doc_ = (
        "Pin an EXISTING episode (by id) as a keepsake. The actor is "
        "REQUIRED and is a CLAIM — exactly 'empress' or 'emperor', never "
        "derived; the result carries the turn's provenance class beside "
        "the claim (a wrong claim is detectable at write time). Pinning "
        "an already-pinned episode re-pins (the claim is overwritten) and "
        "the result says so. The cap refuses with the current pin "
        "inventory; nothing is ever evicted." + shared_tail
    )
    memoir_unpin._full_doc_ = (
        "Unpin a keepsake by id. The episode itself is NEVER deleted — "
        "only the pin and its whole claim (actor, reason, stamp) are "
        "cleared. Idempotent: unpinning a non-keepsake is a no-op with a "
        "note, not an error." + shared_tail
    )
    memoir_recall._full_doc_ = (
        "Recall YOUR OWN episodes by relevance. k defaults to 5, hard max "
        "20 — an out-of-range k is REFUSED, never silently clamped. The "
        "budgeted result carries a fixed footer (counts, rank basis, "
        "embedding disclosure, stats flag) — the episodes returned are "
        "exactly the episodes marked as accessed; anything dropped for "
        "budget is named in the footer, never silently lost. The timeline "
        "tool (not this one) is the right read when you know WHEN." + shared_tail
    )
    memoir_timeline._full_doc_ = (
        "Time-travel over YOUR OWN episodes by event time: since/until "
        "are INCLUSIVE ISO-8601 bounds on event_at (naive digits read as "
        "UTC), q optionally narrows lexically inside the window. There is "
        "NO k parameter and NO stats write — reading the timeline is not "
        "an act of attention and never reorders future recalls. An "
        "inverted window (since after until) is refused." + shared_tail
    )

    return [
        memoir_write,
        memoir_pin,
        memoir_unpin,
        memoir_recall,
        memoir_timeline,
    ]


__all__ = [
    "CATEGORY_DOC",
    "MEMOIR_AUTHORIZED_AGENT",
    "MEMOIR_CATEGORY_KEY",
    "MEMOIR_CODES",
    "MEMOIR_TOOL_NAMES",
    "create_memoir_tools",
]
