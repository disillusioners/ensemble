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
distillation of the conversation; the tool ARMS it, and the daemon then
appends it as a REQUEST that the agent's OWN model executes on its next
invoke (the append method — mid-turn, no seam). The tool NEVER compacts
and NEVER claims it did: ``ARMED is not COMPACTED`` (the honest tool
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
(``armed``) — the prior lane's frozen marker set and its
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
from typing import TYPE_CHECKING, Annotated, Any, Callable

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
# (``armed``) and its OWN content-free INFO line — the prior lane's
# frozen marker set and six-value INFO enum are NOT extended (D25
# discipline).


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


def _configured_model(manager: Any) -> str:
    """The CONFIGURED model the compact-note span is SIZED from, at ARM
    time. CONTENT-FREE (a model id is not conversation content).

    This is the model the SIZING is based on — NOT necessarily the model
    that will SERVE the turn. Which model answers is unwitnessable at
    arm time; the server's audit line names the SERVED model
    (``agent_model=`` in the append audit line).
    """
    llm = getattr(getattr(manager, "config", None), "llm", None)
    return str(getattr(llm, "model", "") or "unknown")


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
    permanently. A FUTURE-dated clock is the same class of corruption
    and fails open the same way (logged at WARNING).

    REAL EXPOSURE (disclosed, not fixed): the clock is stamped ONLY on
    a LANDED compaction (``clear_compact_note_arm``), so a NON-LANDING
    arm can be re-armed each turn — the rate limit does not protect
    against a note that never lands. The malformed-clock fail-open is
    deliberate and bounded by that exposure; it is not a defect.
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
    if elapsed < 0:
        # A FUTURE-dated clock is CORRUPT — the same class as a malformed
        # one. Treating it as "the window is still open" would strand
        # EVERY later attempt (including a replacement note) behind a
        # nonsensical remaining count. Fail OPEN, loudly.
        logger.warning(
            "prompt_reload compact-note: the deliberate-compact clock is "
            "in the FUTURE (elapsed=%.0fs) — treated as corrupt; the rate "
            "limit is not enforced for this attempt",
            elapsed,
        )
        return 0
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
    partial_write: bool = False,
    model_configured: str = "",
) -> str:
    """The ARMED receipt — what the agent actually sees at ARM TIME.

    Content-free: the note's LENGTH and sha256 PREFIX only, never the note
    body. Under v1.5 the note is a REQUEST the agent's own model executes;
    there is NO seam transition and NO "three gates" (the armed-aware
    floor predicate and the threshold bypass are removed). The receipt
    reports what actually happened: the note is armed, awaiting the
    append at the next invoke. The OUTCOME (``compacted`` / ``deferred``
    / ``append_failed`` / ``empty_reply``) is reported after the swap
    lands — never here, and never as a success for work not done.
    """
    from ..services.prompt_refresh import (
        OUTCOME_ARMED,
        OUTCOME_ARMED_PARTIAL_WRITE,
    )

    # A PARTIAL WRITE is still ARMED (the carrier holds the note) — but
    # the write REPORTED a failure, so the receipt says so rather than
    # claiming a clean write.
    outcome = OUTCOME_ARMED_PARTIAL_WRITE if partial_write else OUTCOME_ARMED
    lines: list[str] = [
        f"prompt_reload {outcome} — compact note ARMED; the note is "
        f"a REQUEST your model will execute on its next invoke.",
        f"outcome={outcome}",
        f"note_chars={note_chars}",
        f"note_sha256={note_sha12}",
        f"model_configured={model_configured or 'unknown'}",
        f"cap={cap}",
        f"superseded_previous={str(bool(superseded_previous)).lower()}",
    ]
    if superseded_previous:
        lines.append(f"previous_note_sha256={previous_sha12}")
    lines.append("path=append")
    lines.append("selection=the model made the final selection")
    if status_line:
        lines.append(status_line)
    lines.append(
        "CONTEXT NOT YET COMPACTED — the note is a REQUEST your model will "
        "execute on its next invoke; until then this note remains armed."
    )
    if partial_write:
        lines.append(
            "PARTIAL WRITE — the carrier write reported a failure but the "
            "arm IS present (verified by reading it back); the note is "
            "armed and will be executed as usual."
        )
    return "\n".join(lines)


def _instance_repository(manager: Any) -> Any:
    """The manager's instance repository (the arm carrier's writer)."""
    return getattr(manager, "_instance_repository", None)


# ── Compact-note refusal MESSAGE TEMPLATES (per-code) ──────────────────────
#
# The five code names are the census (pinned at 14) and are UNCHANGED.
# The message LITERALS are hoisted here as templates (the variable parts
# are format placeholders) so the five branches stay readable — but the
# DECISION LADDER below is five EXPLICIT ``if`` branches, NOT a table:
# the code is chosen by CONDITIONS (note present → cap → disabled → rate
# limit), not by lookup. See the H4 ruling (re-briefed 2026-11-17).
_COMPACT_NOTE_MSG_NOTE_REQUIRES_COMPACT = (
    "a compact_note was supplied with compact=false — the note is only "
    "meaningful together with compact=true; re-issue the call with "
    "compact=true. Nothing was armed."
)
_COMPACT_NOTE_MSG_NOTE_REQUIRED = (
    "a compact note is REQUIRED when compact=true — the note is the "
    "point, not an option; supply compact_note with non-empty text. "
    "Nothing was armed."
)
_COMPACT_NOTE_MSG_NOTE_OVER_CAP = (
    "the compact note is {note_chars} characters, over the "
    "{cap}-character cap — the note is NEVER truncated; shorten it and "
    "re-arm. Nothing was armed."
)
_COMPACT_NOTE_MSG_COMPACT_DISABLED = (
    "compaction is DISABLED in this deployment — arming a note that can "
    "never land would strand it; enable compaction (CompactionConfig.enabled) "
    "and re-arm. Nothing was armed."
)
_COMPACT_NOTE_MSG_COMPACT_RATE_LIMITED = (
    "the deliberate-compact rate limit is in force — {remaining}s remaining "
    "before another note-guided compaction may be armed (the limit is "
    "{limit}s). Nothing was armed; try again after the window."
)


def _compact_note_refusal(
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
        ARMED_PENDING_APPEND,
        CODE_COMPACT_DISABLED,
        CODE_COMPACT_RATE_LIMITED,
        CODE_INTERNAL_ERROR,
        CODE_NOTE_OVER_CAP,
        CODE_NOTE_REQUIRES_COMPACT,
        CODE_NOTE_REQUIRED,
        DEFERRED_STATE_TEXT,
        OUTCOME_ARMED,
        OUTCOME_ARMED_PARTIAL_WRITE,
        OUTCOME_REFUSED,
        arm_compact_note,
        compact_note_sha256_12,
        format_compact_note_info_line,
        read_compact_note_state,
    )

    note = compact_note or ""
    if not compact and not note.strip():
        # Today's shape — byte-identical (the still-armed status line
        # rides only the ARMED receipt; the plain receipt is frozen).
        return None

    cap = _compact_note_cap(manager)
    note_chars = len(note)

    # ── The refusal ladder — FIVE EXPLICIT ``if`` BRANCHES ──────────────────
    # The FIRST branch whose guard fires IS the refusal; the evaluation
    # order is the branch order (top to bottom). The five code names are
    # the census (pinned at 14) and are UNCHANGED.
    #
    # ORDER / MASKING (documented, not reordered — the census re-pin rides
    # a later lane): ``note_over_cap`` is evaluated BEFORE
    # ``compact_disabled`` and ``compact_rate_limited``. A note that is
    # BOTH over-cap AND (disabled / rate-limited) is reported as
    # ``note_over_cap`` — the remediation advice names the cap, not the
    # disabled/rate-limit state. Every refusal is still TRUE and TYPED;
    # only the surfaced cause is the one that fires first. Reordering is
    # deferred: the census and the refusal tests ride the later lane.
    # The carrier state is read LAZILY (only the rate-limit branch needs
    # it), so an early refusal never touches the DB.
    repo = _instance_repository(manager)

    if not compact and note.strip():
        return _compact_note_refusal(
            instance_id,
            agent_id,
            code=CODE_NOTE_REQUIRES_COMPACT,
            message=_COMPACT_NOTE_MSG_NOTE_REQUIRES_COMPACT,
            note=note,
        )

    if compact and not note.strip():
        return _compact_note_refusal(
            instance_id,
            agent_id,
            code=CODE_NOTE_REQUIRED,
            message=_COMPACT_NOTE_MSG_NOTE_REQUIRED,
            note=note,
        )

    if note_chars > cap:
        return _compact_note_refusal(
            instance_id,
            agent_id,
            code=CODE_NOTE_OVER_CAP,
            message=_COMPACT_NOTE_MSG_NOTE_OVER_CAP.format(
                note_chars=note_chars, cap=cap
            ),
            note=note,
        )

    if not _compaction_enabled(manager):
        return _compact_note_refusal(
            instance_id,
            agent_id,
            code=CODE_COMPACT_DISABLED,
            message=_COMPACT_NOTE_MSG_COMPACT_DISABLED,
            note=note,
        )

    # The rate-limit branch is the ONLY one that touches the carrier
    # (lazily). The state is read here and only here.
    _state_box: dict[str, Any] = {}
    def _state() -> Any:
        if "value" not in _state_box:
            _state_box["value"] = read_compact_note_state(repo, instance_id)
        return _state_box["value"]

    _remaining_box: dict[str, int] = {}
    def _remaining() -> int:
        if "value" not in _remaining_box:
            _remaining_box["value"] = _rate_limit_remaining_seconds(
                _state().last_applied_at
            )
        return _remaining_box["value"]

    if _remaining() > 0:
        from ..constants import DELIBERATE_COMPACT_RATE_LIMIT_S

        return _compact_note_refusal(
            instance_id,
            agent_id,
            code=CODE_COMPACT_RATE_LIMITED,
            message=_COMPACT_NOTE_MSG_COMPACT_RATE_LIMITED.format(
                remaining=_remaining(),
                limit=DELIBERATE_COMPACT_RATE_LIMIT_S,
            ),
            note=note,
        )

    state = _state()

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
        status_line = DEFERRED_STATE_TEXT.get(state.deferred, ARMED_PENDING_APPEND)

    armed_ok = repo is not None and arm_compact_note(
        repo, instance_id, note, max_chars=cap
    )
    partial_write = False
    new_sha12 = compact_note_sha256_12(note)
    if not armed_ok:
        # The write REPORTED a failure. Did the arm actually land? A
        # carrier can apply the key and still report failure (the
        # per-key fallback is NOT atomic) — so the state is READ BACK.
        #
        # THREE DISTINCT OUTCOMES (never collapsed):
        #   (1) the read-back could NOT be performed (read_ok=False) —
        #       the state is UNVERIFIED: never "nothing was armed" (we do
        #       not know) and never "verified by reading it back" (no
        #       verification happened);
        #   (2) an OLDER note survives — NAME it (the new digest must NOT
        #       be advertised: the swap would execute the OLD note);
        #   (3) the arm is genuinely absent — the read-back CONFIRMS it.
        read_state = None
        read_ok = False
        if repo is not None:
            try:
                read_state = read_compact_note_state(repo, instance_id)
                read_ok = bool(getattr(read_state, "read_ok", False))
            except Exception:  # pragma: no cover — defensive
                read_state = None
                read_ok = False
        if not read_ok:
            logger.warning(
                "prompt_reload compact-note: the arm write reported a "
                "failure and the READ-BACK could not be performed for "
                "instance=%s — the arm state is UNVERIFIED",
                str(instance_id)[:8],
            )
            return _failure(
                CODE_INTERNAL_ERROR,
                "the compact note could not be CONFIRMED as armed (the "
                "carrier reported a write failure and the read-back could "
                "not be performed); the arm state is UNVERIFIED — re-check "
                "before re-arming.",
            )
        if read_state is not None and read_state.armed:
            # The NEW note is PRESENT if its digest matches, OR if no
            # digest was recorded but the BODY matches (a torn bundle can
            # land the body and lose the digest).
            new_is_present = (read_state.sha256_12 or "") == new_sha12 or (
                not (read_state.sha256_12 or "")
                and (read_state.note or "") == note
            )
            if new_is_present:
                # (2a) the NEW note landed despite the reported failure.
                partial_write = True
                logger.warning(
                    "prompt_reload compact-note: PARTIAL WRITE for "
                    "instance=%s — the carrier write reported a failure but "
                    "the carrier HOLDS the NEW arm (verified by reading it "
                    "back)",
                    str(instance_id)[:8],
                )
            else:
                # (2b) an OLDER note survives — the NEW note did NOT land.
                logger.warning(
                    "prompt_reload compact-note: the arm write reported a "
                    "failure and the read-back found an OLDER arm "
                    "(sha256=%s) for instance=%s — the NEW note did NOT "
                    "land; the OLD note remains armed",
                    read_state.sha256_12 or "-",
                    str(instance_id)[:8],
                )
                return _failure(
                    CODE_INTERNAL_ERROR,
                    "the NEW compact note did NOT land — the carrier still "
                    "holds an OLDER armed note (sha256 prefix "
                    f"{read_state.sha256_12 or '-'}); un-arm or replace it "
                    "before re-arming.",
                )
        else:
            # (3) the arm is genuinely NOT there — the read-back CONFIRMS it.
            logger.error(
                "prompt_reload compact-note: arm write failed for "
                "instance=%s — the arm is NOT present (verified by "
                "reading it back)",
                str(instance_id)[:8],
            )
            return _failure(
                CODE_INTERNAL_ERROR,
                "the compact note could not be armed (the instance metadata "
                "carrier rejected the write; logged server-side); nothing "
                "was armed",
            )

    note_sha12 = new_sha12
    logger.info(
        format_compact_note_info_line(
            instance_id=instance_id,
            agent_id=agent_id,
            outcome=(
                OUTCOME_ARMED_PARTIAL_WRITE if partial_write else OUTCOME_ARMED
            ),
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
        partial_write=partial_write,
        status_line=status_line,
        model_configured=_configured_model(manager),
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
                    "Arm a compact note — your own distillation of this "
                    "conversation. The note is REQUIRED when compact=true "
                    "(compact=false with a note is refused — ignoring it "
                    "would be dishonest). The tool ARMS the note; the "
                    "daemon then appends it as a REQUEST and your own "
                    "model writes the compaction on its next invoke "
                    "(mid-turn — there is no quiet seam and no gate). "
                    "Optional; default false."
                ),
            ),
        ]
        compact_note: Annotated[
            str,
            Field(
                default="",
                description=(
                    "Your own distillation of this conversation — it is the "
                    "REQUEST your own model executes on its next invoke "
                    "(it is NOT rendered into the compaction doc). Max the "
                    "configured cap (default 12000 characters; over the cap "
                    "is refused, never truncated). Optional; default empty."
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
  of this conversation. The note is the REQUEST your own model executes
  on its next invoke: the daemon appends it as a user message to your
  message list and your own model writes the compaction (same model =>
  same provider). Nothing is re-rendered into the doc; the note is
  never a layer. The append is mid-turn and explicitly permitted;
  compaction happens on that invoke, not at a quiescent gate.
- The note is REQUIRED when compact=true, and a note supplied with
  compact=false is refused (ignoring it would be dishonest).
- You can ALSO be refused for reasons that are not about the note: if
  compaction is DISABLED in this deployment (arming a note that can
  never land would strand it), or if the 900-second deliberate-compact
  rate limit is still in force from an earlier note-guided compaction
  (the refusal echoes the seconds remaining). Both are typed refusals,
  and neither arms anything.
- The note is capped at the CONFIGURED cap (default 12000 characters;
  the operator may raise or lower it — see the cap knob in the refusal
  list below). An over-cap note is REFUSED with the length and the cap —
  it is NEVER silently truncated.
- ARMED is not COMPACTED. The receipt reports what actually happened:
  path=append, model_configured=<the model the span is sized from>, and
  outcome=compacted ONLY after the swap landed and persisted; otherwise
  empty_reply, tool_calls_reply, refused_reply, append_failed, or
  armed_partial_write (the carrier write reported a failure but the arm
  IS present — treat the note as armed) — never a success receipt for
  work not done. There is no "armed pending seam" and no three gates
  (the floor predicate and the threshold bypass are removed). A second
  note while one is armed REPLACES the first, with a disclosure of the
  prior note's digest.
- The note is never echoed in a log — only its length and digest are
  observable.
- The span the request announces is sized from the CONFIGURED model at
  append time (the vision switch applies only if images are already in
  the channel). The announced span is an ESTIMATE: it equals the
  compaction that replaces it in the normal case, but under failover (a
  different model answers) it MAY DIFFER — it is a best-effort
  announcement, not a promise. (The model that ANSWERS is named in the
  server-side audit line, not here.)
- KNOWN LIMITS, disclosed (the daemon classifies your model's reply with
  a 120-character head scan for 12 refusal markers, so it cannot ask
  twice): a genuine refusal OUTSIDE those 12 markers is misread as a
  compaction, and a compaction whose OPENING reads like a refusal is
  misread as a refusal (the safe side — nothing is compacted and your
  note stays armed). FAIL-OPEN DIRECTION, plainly: a refusal outside
  the 12 markers lets the swap fire, the arm is CONSUMED, and the audit
  row says outcome=compacted.
- The compaction exchange is consumed as a WHOLE group: when your
  prompt_reload call was batched with other tools, the parallel sibling
  tool's result is REMOVED with the group — it is not summarized in.

Accepted consequences (disclosed, by design)
--------------------------------------------
- The effect lands on the SAME turn via the append (mid-turn is
  permitted for this path); the agent's own model produces the
  compaction and the history is swapped in place.
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
