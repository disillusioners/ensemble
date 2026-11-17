"""The ``prompt_reload`` tool — the ``prompt-reload`` category (1 tool,
ARI-ONLY).

Refreshes THE CALLING INSTANCE'S OWN system prompt from the active
agent definition without a restart or session loss: validate the
agent dir (self-env aware), verify the loader's read set against the
release manifest, recompose through the SAME loader the spawn/restore
path uses, diff old vs new, and evict the cached graph so the NEXT
dispatch cold-restores onto the new persona (the bare-pop seam — the
effect lands on the NEXT turn, never mid-turn).

It ALSO owns the COMPACT-NOTE surface — ``prompt_reload(compact=True,
compact_note="…")`` (architect §D/§G/§H). The note is the agent's own
distillation of the conversation; the tool ARMS it and the compaction
engine lands it at the NEXT QUIESCENT SEAM. The tool NEVER compacts and
NEVER claims it did: ``ARMED is not COMPACTED`` (the honest tool
contract). Arming is refused — never truncated, never swallowed — when
the note is missing, over the cap, supplied without ``compact=true``,
compaction is disabled, or the deliberate-compact rate limit is in
force. The DEFERRED states (``armed — deferred (…), not compacted``)
ride the arm-status surface verbatim; a deferral is never dressed up as
a success receipt.

Access posture — THREE layers, ALL required (notify-user precedent):

1. The ``prompt-reload`` category is PRIVILEGED (entry #20 of
   ``PRIVILEGED_TOOL_CATEGORIES``) — opt-in-only, never in the
   default-grant universe.
2. ``agents/ari/meta.json`` carries the explicit ``"prompt-reload"``
   allow entry — and ONLY ari.
3. The tool body rejects any caller whose factory-captured
   ``agent_id != "ari"`` with the notify-exact generic envelope (a
   LITERAL tool name in an allow list bypasses the category gate, so
   the category alone cannot spell the audience; this authorization
   class deliberately carries NO REFUSED code — AM-6).

Kill-switch (D11/AM-11): ``ENSEMBLE_PROMPT_RELOAD_ENABLED``, default
ON, read PER CALL here — a killed tool REFUSES typed
``prompt_reload_disabled`` and STAYS REGISTERED (the surface never
shrinks with the switch).

House rules honored: tools NEVER raise; every failure is a typed
envelope string (REFUSED for the 14 refused codes, FAILED for the
internal-error code); the ONE frozen INFO line fires on EVERY terminal
path (D24/AM-15) with counts/flags only. The compact-note surface
carries its OWN content-free INFO line + its OWN outcome namespace
(``armed_pending_seam``) — the prior lane's frozen marker set and its
six-value INFO enum are NOT extended (D25 discipline).

Next-turn semantics + provider cache invalidation are DISCLOSED in
every success report (R9) and in the runbook
``docs/runbooks/prompt-reload.md`` — never in ari's prompt artifacts
(D7: this lane adds no agent prompt files).
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, Field
from langchain_core.tools import tool

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    # Annotation-only (from __future__ import annotations keeps these
    # lazy strings) — the runtime prompt_refresh imports stay
    # function-local so call-time monkeypatching keeps working.
    from ..services.prompt_refresh import PromptRefreshResult

logger = logging.getLogger(__name__)

#: The category key — KEBAB-case, byte-identical in every place it
#: appears (the module, the registry, ari's meta.json allow entry, the
#: runbook). House pattern (``notify-user`` → ``notify_user``).
PROMPT_RELOAD_CATEGORY_KEY = "prompt-reload"

#: The ONE module constant holding the exact kill-switch knob string
#: (metrics_read.py:139 pattern).
KILL_SWITCH_ENV = "ENSEMBLE_PROMPT_RELOAD_ENABLED"

#: The single authorized agent (R5: ari EXACTLY).
PROMPT_RELOAD_AUTHORIZED_AGENT = "ari"

#: Off-spellings for the per-call kill-switch (instance_ops.py weight).
_OFF_SPELLINGS = {"0", "false", "no", "off", "disabled"}


def _resolve_enabled() -> bool:
    """Kill-switch resolver — default ON, read PER CALL (D11/AM-11)."""
    raw = os.environ.get(KILL_SWITCH_ENV)
    if raw is None:
        return True
    value = str(raw).strip().lower()
    if value in _OFF_SPELLINGS:
        return False
    if value not in {"1", "true", "yes", "on", "enabled"}:
        logger.warning(
            "prompt_reload: invalid %s=%r (expected a boolean spelling); "
            "defaulting to ENABLED",
            KILL_SWITCH_ENV,
            raw,
        )
    return True


def _refusal(code: str, message: str) -> str:
    """The house REFUSED envelope (notify_user.py:314-316 shape)."""
    return f"Error: prompt_reload REFUSED — reason={code}: {message}"


def _failure(code: str, message: str) -> str:
    """The house FAILED envelope (notify_user.py:319-321 shape)."""
    return f"Error: prompt_reload FAILED — reason={code}: {message}"


def _not_ari(agent_id: str) -> str:
    """The notify-exact generic ERROR envelope (notify_user.py:703-711)."""
    return (
        f"ERROR: prompt_reload is available to the "
        f"{PROMPT_RELOAD_AUTHORIZED_AGENT} agent only "
        f"(caller: {agent_id or 'unknown'})."
    )


# ── Compact-note surface helpers (architect §G / §H) ──────────────────────────
#
# The compact-note surface carries its OWN outcome namespace
# (``armed_pending_seam``) and its OWN content-free INFO line — the prior
# lane's frozen marker set and six-value INFO enum are NOT extended
# (D25 discipline).


def _compaction_config(manager: Any) -> Any:
    """The manager's ``CompactionConfig`` (or ``None`` when unavailable)."""
    return getattr(getattr(manager, "config", None), "compaction", None)


def _compact_note_cap(manager: Any) -> int:
    """The compact-note admission cap, in CHARACTERS.

    ``12000`` (the architect's AUTHORITATIVE cap) unless the operator's
    resolved ``CompactionConfig.compact_note_max_chars`` says otherwise.
    A note over the cap is REFUSED — never truncated.
    """
    from ..config import DEFAULT_COMPACT_NOTE_MAX_CHARS

    cap = getattr(_compaction_config(manager), "compact_note_max_chars", None)
    if isinstance(cap, int) and not isinstance(cap, bool) and cap >= 1:
        return cap
    return DEFAULT_COMPACT_NOTE_MAX_CHARS


def _compaction_enabled(manager: Any) -> bool:
    """Whether compaction is enabled at all (``CompactionConfig.enabled``).

    Defaults to ``True`` when the config is unreachable — the refusal
    path exists to catch an operator who DISABLED compaction, not to
    invent a refusal.
    """
    enabled = getattr(_compaction_config(manager), "enabled", None)
    if enabled is None:
        return True
    return bool(enabled)


def _rate_limit_remaining_seconds(
    last_applied_at: str,
    *,
    now: datetime | None = None,
) -> int:
    """Seconds remaining on the deliberate-compact rate limit.

    The clock is ``compact_note_last_applied_at`` — DELIBERATE (note-guided)
    compactions ONLY. Returns ``0`` when the window is free.

    A malformed clock fails OPEN (returns ``0``): the rate limit is an
    anti-abuse bound, and a corrupt clock must not strand an arm
    permanently.
    """
    from ..constants import DELIBERATE_COMPACT_RATE_LIMIT_S

    if not last_applied_at:
        return 0
    try:
        last = datetime.fromisoformat(str(last_applied_at))
    except (TypeError, ValueError):
        return 0
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    elapsed = (now - last).total_seconds()
    remaining = DELIBERATE_COMPACT_RATE_LIMIT_S - elapsed
    if remaining <= 0:
        return 0
    return int(math.ceil(remaining))


def _render_armed_receipt(
    *,
    note_chars: int,
    note_sha12: str,
    cap: int,
    superseded_previous: bool = False,
    previous_sha12: str = "",
    status_line: str | None = None,
) -> str:
    """The ARMED receipt — what the agent actually sees.

    Content-free: the note's LENGTH and sha256 PREFIX only, never the note
    body. It NEVER claims the compaction ran — the outcome token is
    ``armed_pending_seam`` and the closing line says so explicitly.
    """
    from ..services.prompt_refresh import (
        ARMED_EXPECTED_SEAM_CLAUSE,
        ARMED_NOT_YET_COMPACTED_LINE,
        ARMED_SEAM_STATEMENT,
        OUTCOME_ARMED_PENDING_SEAM,
    )

    lines: list[str] = [
        f"prompt_reload {OUTCOME_ARMED_PENDING_SEAM} — compact note ARMED; "
        f"the compaction happens at your next turn boundary.",
        f"outcome={OUTCOME_ARMED_PENDING_SEAM}",
        f"note_chars={note_chars}",
        f"note_sha256={note_sha12}",
        f"cap={cap}",
        f"superseded_previous={str(bool(superseded_previous)).lower()}",
    ]
    if superseded_previous:
        lines.append(f"previous_note_sha256={previous_sha12}")
    lines.append(f"seam={ARMED_SEAM_STATEMENT}; {ARMED_EXPECTED_SEAM_CLAUSE}")
    if status_line:
        lines.append(status_line)
    lines.append(ARMED_NOT_YET_COMPACTED_LINE)
    return "\n".join(lines)


def _instance_repository(manager: Any) -> Any:
    """The manager's instance repository (the arm carrier's writer)."""
    return getattr(manager, "_instance_repository", None)


def _compact_note_refusal(
    manager: Any,
    instance_id: str,
    agent_id: str,
    *,
    code: str,
    message: str,
    note: str,
) -> str:
    """One content-free INFO line + the house REFUSED envelope.

    A refusal is a DESIGN OUTCOME, not an error (docs/guidelines/README.md
    item 3) — it rides the REFUSED envelope, never the FAILED one. The
    message NEVER quotes the note body (P4) and NEVER claims a compaction
    was requested, armed, or performed.
    """
    from ..services.prompt_refresh import (
        OUTCOME_REFUSED,
        compact_note_sha256_12,
        format_compact_note_info_line,
    )

    logger.info(
        format_compact_note_info_line(
            instance_id=instance_id,
            agent_id=agent_id,
            outcome=OUTCOME_REFUSED,
            code=code,
            note_chars=len(note),
            note_sha256_12=compact_note_sha256_12(note),
        )
    )
    return _refusal(code, message)


def _handle_compact_note(
    manager: Any,
    instance_id: str,
    agent_id: str,
    *,
    compact: bool,
    compact_note: str,
) -> str | None:
    """The compact-note surface — ARM or REFUSE. NEVER compacts.

    Returns the agent-facing string (the ARMED receipt or a REFUSED /
    FAILED envelope), or ``None`` when the call is NOT on the
    compact-note surface at all (``compact=false`` with an absent or
    whitespace-only note) — that is today's shape and stays
    byte-identical.

    The tool body structurally CANNOT observe a compaction: it writes the
    arm and returns.
    """
    from ..services.prompt_refresh import (
        ARMED_PENDING_SEAM,
        CODE_COMPACT_DISABLED,
        CODE_COMPACT_RATE_LIMITED,
        CODE_INTERNAL_ERROR,
        CODE_NOTE_OVER_CAP,
        CODE_NOTE_REQUIRES_COMPACT,
        CODE_NOTE_REQUIRED,
        DEFERRED_STATE_TEXT,
        OUTCOME_ARMED_PENDING_SEAM,
        OUTCOME_REFUSED,
        arm_compact_note,
        compact_note_sha256_12,
        format_compact_note_info_line,
        read_compact_note_state,
    )

    note = compact_note or ""
    if not compact:
        if not note.strip():
            # Today's shape — byte-identical (the still-armed status line
            # rides only the ARMED receipt; the plain receipt is frozen).
            return None
        return _compact_note_refusal(
            manager,
            instance_id,
            agent_id,
            code=CODE_NOTE_REQUIRES_COMPACT,
            message=(
                "a compact_note was supplied with compact=false — the note "
                "is only meaningful together with compact=true; re-issue "
                "the call with compact=true. Nothing was armed."
            ),
            note=note,
        )

    # ── compact=true ─────────────────────────────────────────────────────────
    if not note.strip():
        return _compact_note_refusal(
            manager,
            instance_id,
            agent_id,
            code=CODE_NOTE_REQUIRED,
            message=(
                "a compact note is REQUIRED when compact=true — the note is "
                "the point, not an option; supply compact_note with "
                "non-empty text. Nothing was armed."
            ),
            note=note,
        )

    cap = _compact_note_cap(manager)
    note_chars = len(note)
    if note_chars > cap:
        return _compact_note_refusal(
            manager,
            instance_id,
            agent_id,
            code=CODE_NOTE_OVER_CAP,
            message=(
                f"the compact note is {note_chars} characters, over the "
                f"{cap}-character cap — the note is NEVER truncated; "
                f"shorten it and re-arm. Nothing was armed."
            ),
            note=note,
        )

    if not _compaction_enabled(manager):
        return _compact_note_refusal(
            manager,
            instance_id,
            agent_id,
            code=CODE_COMPACT_DISABLED,
            message=(
                "compaction is DISABLED in this deployment — arming a note "
                "that can never land would strand it; enable compaction "
                "(CompactionConfig.enabled) and re-arm. Nothing was armed."
            ),
            note=note,
        )

    repo = _instance_repository(manager)
    state = read_compact_note_state(repo, instance_id)

    remaining = _rate_limit_remaining_seconds(state.last_applied_at)
    if remaining > 0:
        from ..constants import DELIBERATE_COMPACT_RATE_LIMIT_S

        return _compact_note_refusal(
            manager,
            instance_id,
            agent_id,
            code=CODE_COMPACT_RATE_LIMITED,
            message=(
                f"the deliberate-compact rate limit is in force — "
                f"{remaining}s remaining before another note-guided "
                f"compaction may be armed (the limit is "
                f"{DELIBERATE_COMPACT_RATE_LIMIT_S}s). Nothing was armed; "
                f"try again after the window."
            ),
            note=note,
        )

    # ── ARM ──────────────────────────────────────────────────────────────────
    # A second note while one is armed REPLACES the first WITH
    # DISCLOSURE (``superseded_previous`` + the prior note's sha256
    # prefix) — never a refusal: a refusal would strand a mistyped note
    # with no un-arm path.
    superseded_previous = bool(state.armed)
    previous_sha12 = state.sha256_12 or (
        compact_note_sha256_12(state.note) if state.note else ""
    )
    status_line: str | None = None
    if superseded_previous:
        status_line = DEFERRED_STATE_TEXT.get(state.deferred, ARMED_PENDING_SEAM)

    if repo is None or not arm_compact_note(repo, instance_id, note):
        # The arm could NOT be written — never claim an arm that does not
        # exist. This is an INTERNAL failure (not a design outcome), so it
        # rides the single FAILED code.
        logger.error(
            "prompt_reload compact-note: arm write failed for instance=%s",
            str(instance_id)[:8],
        )
        return _failure(
            CODE_INTERNAL_ERROR,
            "the compact note could not be armed (the instance metadata "
            "carrier rejected the write; logged server-side); nothing was "
            "armed",
        )

    note_sha12 = compact_note_sha256_12(note)
    logger.info(
        format_compact_note_info_line(
            instance_id=instance_id,
            agent_id=agent_id,
            outcome=OUTCOME_ARMED_PENDING_SEAM,
            code=None,
            note_chars=note_chars,
            note_sha256_12=note_sha12,
            superseded_previous=superseded_previous,
            deferred=state.deferred,
        )
    )
    return _render_armed_receipt(
        note_chars=note_chars,
        note_sha12=note_sha12,
        cap=cap,
        superseded_previous=superseded_previous,
        previous_sha12=previous_sha12,
        status_line=status_line,
    )


def _emit_info_line(
    result: PromptRefreshResult, instance_id: str, agent_id: str
) -> None:
    """The ONE frozen INFO line (D24/AM-15) — counts/flags only, never
    persona or diff content. Fired on EVERY terminal path."""
    from ..services.prompt_refresh import format_info_line

    provenance = getattr(result, "provenance", {}) or {}
    logger.info(
        format_info_line(
            instance_id=instance_id,
            agent_id=provenance.get("agent_id", agent_id),
            outcome=getattr(result, "outcome", "refused"),
            code=getattr(result, "code", None),
            provenance=provenance,
            fingerprint_changed=bool(provenance.get("fingerprint_changed", False)),
            hash_divergence=bool(provenance.get("hash_divergence", False)),
            cache_busted=bool(provenance.get("cache_busted", False)),
            persona_diff_kind=(getattr(result, "diff", {}) or {}).get("persona_diff", "-"),
            was_cached=bool(provenance.get("was_cached", False)),
            manager_instances=provenance.get("manager_instances", "?->?"),
        )
    )


def _render_report(result: PromptRefreshResult) -> str:
    """The D14 frozen report shape — abbreviated diff + provenance."""
    from ..services.prompt_refresh import (
        PROMPT_RELOADED_MARKER,
        S10_C_DISPLAY_DIVERGENCE,
        S10_D_NEXT_TURN,
        S10_E_PROVIDER_CACHE,
        S10_J_NO_CHANGE_WORDING,
        S10_N_COLD_START_DELIBERATE,
        OUTCOME_NO_CHANGE,
        OUTCOME_UNPROVEN,
    )

    p = result.provenance
    d = result.diff or {}
    lines: list[str] = []
    lines.append(f"prompt_reload {result.outcome} — {result.message}")
    lines.append(
        f"release: {p.get('running_release') or '?'} "
        f"(current_matches_running={p.get('current_matches_running', 'unknown')}, "
        f"daemon_version={p.get('daemon_version', '?')})"
    )
    if p.get("divergence_line"):
        lines.append(p["divergence_line"])
    lines.append(f"manifest: {p.get('manifest', '-')}")
    lines.append(
        f"db_fingerprint: {p.get('db_fingerprint', '-')} "
        f"(mcp_tool_names_source={p.get('mcp_tool_names_source', '-')}, "
        f"was_cached={str(bool(p.get('was_cached'))).lower()})"
    )
    lines.append(f"manager_instances: {p.get('manager_instances', '-')}")

    if d.get("persona_diff") == "exact":
        lines.append(
            f"persona diff: +{d.get('lines_added', 0)}/-{d.get('lines_removed', 0)} lines, "
            f"{d.get('bytes_before', 0)}→{d.get('bytes_after', 0)} bytes"
        )
        if d.get("headings_added"):
            lines.append("headings added: " + "; ".join(d["headings_added"]))
        if d.get("headings_removed"):
            lines.append("headings removed: " + "; ".join(d["headings_removed"]))
    else:
        lines.append("persona_diff=unavailable")

    files = d.get("files", [])
    changed = [f for f in files if f.get("changed")]
    lines.append(f"files: {len(changed)} changed of {len(files)} in the loader read set")
    for row in changed:
        lines.append(f"  {row['path']}: sha256 {row['sha_before']} → {row['sha_after']}")

    for row in p.get("db_rows", []) or []:
        line = (
            f"  db row: {row['artifact']} ({row.get('class', 'CORE')}, "
            f"selected={row.get('selected', '-')})"
        )
        if row.get("honesty"):
            line += f" — {row['honesty']}"
        lines.append(line)

    lines.append(S10_C_DISPLAY_DIVERGENCE)
    lines.append(S10_D_NEXT_TURN)
    lines.append(S10_E_PROVIDER_CACHE)
    if result.outcome == OUTCOME_NO_CHANGE:
        lines.append(S10_J_NO_CHANGE_WORDING)
    if result.outcome == OUTCOME_UNPROVEN:
        lines.append(S10_N_COLD_START_DELIBERATE)
    lines.append(PROMPT_RELOADED_MARKER.format(outcome=result.outcome))
    return "\n".join(lines)


def create_prompt_reload_tools(
    manager: Any,
    *,
    current_instance_id: str = "",
    agent_id: str = "",
) -> list:
    """Build the prompt_reload tool for one instance/agent.

    The factory ALWAYS constructs (kill-switch OFF refuses per call,
    never hides the tool — AM-11). Per-agent visibility is scoped by
    ``_apply_tool_filter`` via the ``prompt-reload`` PRIVILEGED
    category + ari's meta.json allow entry; the body carries the
    third, independent agent_id gate.
    """
    caller_agent_id = agent_id or ""
    caller_instance_id = current_instance_id or ""

    class PromptReloadInput(BaseModel):
        """Input schema for prompt_reload."""

        force: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Pop the cached graph even when nothing changed "
                    "(the escape hatch for the shared-cache edge). "
                    "Optional; default false."
                ),
            ),
        ]
        compact: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Arm a compact note for the NEXT quiet seam. The note "
                    "is REQUIRED when compact=true (compact=false with a "
                    "note is refused — ignoring it would be dishonest). "
                    "The tool ARMS the note; the compaction happens at "
                    "your next turn boundary, never mid-turn. Optional; "
                    "default false."
                ),
            ),
        ]
        compact_note: Annotated[
            str,
            Field(
                default="",
                description=(
                    "Your own distillation of this conversation — it rides "
                    "verbatim into the compacted context, at the GLOBAL "
                    "OVERVIEW slot. Max 12000 characters (over the cap is "
                    "refused, never truncated). Optional; default empty."
                ),
            ),
        ]

    @register_tool_category(PROMPT_RELOAD_CATEGORY_KEY)
    @tool(args_schema=PromptReloadInput)
    async def prompt_reload(
        force: bool = False,
        compact: bool = False,
        compact_note: str = "",
    ) -> str:
        """Refresh MY system prompt from the active agent definition; the new persona takes effect on my NEXT turn. Use tool_help("prompt_reload") for details."""
        try:
            # Layer 3 — the in-body agent gate (auth-first: an
            # unauthorized caller touches nothing else). The denial is
            # still a terminal path → the D24 INFO line fires (code=-:
            # the authorization class deliberately carries no code).
            if caller_agent_id != PROMPT_RELOAD_AUTHORIZED_AGENT:
                from ..services.prompt_refresh import (
                    OUTCOME_REFUSED,
                    PromptRefreshResult,
                )

                denial = PromptRefreshResult(
                    outcome=OUTCOME_REFUSED,
                    code=None,
                    message="non-ari caller",
                    provenance={"agent_id": caller_agent_id},
                )
                _emit_info_line(denial, caller_instance_id, caller_agent_id)
                return _not_ari(caller_agent_id)

            # Kill-switch — per call, in-body (AM-11).
            if not _resolve_enabled():
                from ..services.prompt_refresh import (
                    CODE_DISABLED,
                    PromptRefreshResult,
                    OUTCOME_REFUSED,
                )

                result = PromptRefreshResult(
                    outcome=OUTCOME_REFUSED,
                    code=CODE_DISABLED,
                    message=f"kill-switch {KILL_SWITCH_ENV} is OFF",
                    provenance={"agent_id": caller_agent_id},
                )
                _emit_info_line(result, caller_instance_id, caller_agent_id)
                return _refusal(CODE_DISABLED, f"kill-switch {KILL_SWITCH_ENV} is OFF")

            # ── Compact-note surface ────────────────────────────────────────
            # ``compact=false`` + no note → today's shape (byte-identical,
            # returned as ``None``). ``compact=true`` → ARM or REFUSE. The
            # tool NEVER compacts and NEVER claims it did.
            compact_reply = _handle_compact_note(
                manager,
                caller_instance_id,
                caller_agent_id,
                compact=compact,
                compact_note=compact_note,
            )
            if compact_reply is not None:
                return compact_reply

            result = await manager.refresh_instance_prompt(
                caller_instance_id, force=force
            )
            _emit_info_line(result, caller_instance_id, caller_agent_id)
            if result.code is not None:
                from ..services.prompt_refresh import FAILURE_CODES

                if result.code in FAILURE_CODES:
                    return _failure(result.code, result.message)
                return _refusal(result.code, result.message)
            return _render_report(result)
        except Exception:  # noqa: BLE001 — house pattern: typed envelope, never raise
            logger.exception("prompt_reload failed")
            from ..services.prompt_refresh import (
                CODE_INTERNAL_ERROR,
                PromptRefreshResult,
                OUTCOME_REFUSED,
            )

            result = PromptRefreshResult(
                outcome=OUTCOME_REFUSED,
                code=CODE_INTERNAL_ERROR,
                message="an unexpected error occurred (logged server-side)",
                provenance={"agent_id": caller_agent_id},
            )
            _emit_info_line(result, caller_instance_id, caller_agent_id)
            return _failure(
                CODE_INTERNAL_ERROR,
                "an unexpected error occurred (logged server-side); nothing "
                "was popped",
            )

    prompt_reload._full_doc_ = _build_full_doc()

    return [prompt_reload]


def _build_full_doc() -> str:
    from ..services.prompt_refresh import REFUSAL_CODES, FAILURE_CODES

    codes = "\n".join(f"- {code} (REFUSED envelope)" for code in sorted(REFUSAL_CODES))
    failed = "\n".join(f"- {code} (FAILED envelope)" for code in sorted(FAILURE_CODES))
    return f"""\
Refresh this instance's OWN system prompt from the active agent
definition — no restart, no session loss. The recomposed persona takes
effect on the NEXT dispatch (the current turn completes on the old
persona); this tool's return is the only in-turn signal.

What it does
------------
- Resolves this agent's directory the same way the spawn path does
  (registry), validates it against this daemon's environment (dev vs
  live/sandbox release), and verifies every file the prompt loader
  reads against the release manifest (tamper detection).
- Recomposes the prompt through the SAME loader the spawn/restore path
  uses — DB-approved prompt versions are honored automatically.
- Compares old vs new (per-file sha256 + the prompt-versions DB
  fingerprint) and evicts the cached graph ONLY when something changed
  (or force=true). Idempotent: unchanged inputs leave the cache in
  place and say so.
- Returns an abbreviated diff + provenance. When the old persona
  cannot be proven, the report says persona_diff=unavailable rather
  than guessing.

Compact notes (compact + compact_note)
--------------------------------------
- compact=true ARMS a note you wrote yourself — your own distillation
  of this conversation. The note rides VERBATIM into the compacted
  context at the GLOBAL OVERVIEW slot; the compaction happens at your
  NEXT turn boundary (the quiescent pre-dispatch gate), NEVER
  mid-turn.
- The note is REQUIRED when compact=true, and a note supplied with
  compact=false is refused (ignoring it would be dishonest).
- The note is capped (12000 characters). An over-cap note is REFUSED
  with the length and the cap — it is NEVER silently truncated.
- ARMED is not COMPACTED. The receipt says outcome=armed_pending_seam
  and names the three gates that can defer the seam (the
  minimum-compaction floor, the 60s anti-refire window, and the
  deliberate-compact rate limit). A second note while one is armed
  REPLACES the first, with a disclosure of the prior note's digest.
- The note is never echoed in a log — only its length and digest are
  observable.

Accepted consequences (disclosed, by design)
--------------------------------------------
- The effect lands on the NEXT turn (never mid-turn); an in-flight
  turn completes on the old persona unharmed.
- Reloading the persona invalidates provider prompt caches — accepted.
- The FE-visible system message may lag until the next display
  rebuild; no display-parity promise.
- A first call after any daemon restart reports outcome=unproven and
  recomposes+evicts as a precaution — deliberate, not a defect.

Refusal codes (typed envelopes; tools never raise)
--------------------------------------------------
{codes}
{failed}
Kill-switch: {KILL_SWITCH_ENV}=false disables the tool per call (it
stays registered and refuses typed prompt_reload_disabled).
"""
