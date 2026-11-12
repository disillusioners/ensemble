"""The ``prompt_assent`` tool — the conversational approval lane
("The Emperor's Word / Assent") — Phase 2 (tool surface + registration).

One tool, FOUR verbs (AR-2 — ``action ∈ {list, mint, approve, reject}``),
behind the house's THREE-LAYER access posture:

1. the ``prompt-assent`` category is PRIVILEGED (entry #21 of
   ``PRIVILEGED_TOOL_CATEGORIES``) — never default-granted, never in the
   empty-allow inherit universe;
2. ``agents/ari/meta.json`` carries the explicit ``"prompt-assent"``
   allow entry — and ONLY ari (the ari-EXACTLY census);
3. the tool body refuses any caller whose factory-captured
   ``agent_id != "ari"`` with the notify-exact GENERIC envelope — a
   LITERAL tool name in an allow list bypasses the category gate, so the
   category alone cannot spell the audience. That authorization class
   deliberately carries NO refusal code and NO ledger noise.

THE GATE HAS EXACTLY ONE IMPLEMENTATION OWNER: this file (O-E). The
order is the architect's fixed order (§2.2 @ ``df8da01a6``):

    0. ari layer-3 in-body gate      → generic not-ari envelope
    1. kill-switch (call time)       → ``lane-disabled``
    2. action-shape validation       → ``invalid-arguments``
    3. FACTOR GATES (mint AND approve) — ALL failures enumerated in ONE
       refusal, no partial proceed:
       3a. ``user_confirmed == true`` (approve/reject only)
                                       → ``user-confirmation-missing``
       3b. a live user-origin window for THIS instance
                                       → ``no-user-origin-turn``
       3c. the window's source classifies user-origin by the UNFORKED
           ``classify_user_origin``   → ``no-user-origin-turn``
       3d. the window is unexpired     → ``user-origin-window-expired``
       3e. the window is FRESH         → ``user-origin-window-stale``
       3f. the window's message row is readable
                                       → ``assent-verification-unavailable``
       3g. [approve/reject only] the token is echoed verbatim in THAT
           message's content           → ``token-content-mismatch``
    4. the action branch (mint / approve / reject)

THE MINT IS GATED (K-2) — it is NOT a free-standing read: a mint
requires a live, FRESH factor-2 window, and the mint context
``(source_id, channel_id, is_dm, user_id, external_user_id)`` is derived
DAEMON-SIDE from the WINDOW'S MESSAGE ROW (the nested
``metadata["discord"]``) — NEVER from tool arguments. Without that, a
prompt-injected ari could mint for any CORE pending row and supersede
the Emperor's live token (DoS + authority fabrication). The mint branch:

    M-4a. the row exists / is CORE class / is ``pending_approval``
          → ``item-not-found`` / ``item-not-assentable`` / ``item-not-pending``
    M-4b. no live token exists unless explicitly forced
          → ``mint-live-token-exists``
    M-4c. the PRIVATE check on the WINDOW-derived context —
          ``is_dm AND NOT is_group_dm``; an ABSENT discriminator is NOT
          private (fail-closed, K-3)
          → ``mint-requires-private-channel``
    M-5.  the mint context is captured
    M-6.  OWN transaction — the token INSERT (token_hash, render_sha256,
          mint-context, TTL) + the supersede flip of any prior live
          token. NO audit event — the token table IS the mint ledger.

THE APPROVE/REJECT BRANCH adds AR-1's TWO equality legs — the approving
turn's daemon-derived ``(source_id, channel_id)`` must equal the mint's
(→ ``token-channel-mismatch``) AND the approving turn's daemon-derived
``user_id`` must equal the token row's ``mint_user_id`` (→
``token-approver-mismatch``); the approving context must ALSO be a
private 1:1 DM (→ ``token-channel-mismatch``).

RENDER BINDING (J-3 / K-7): the mint renders through the ONE shared
helper (``daemon/services/prompt_version_diff.py``) and the
``render_sha256`` is ``sha256`` of the EXACT canonical-render bytes that
are placed in the mint response — the response carries the render
VERBATIM, so shown == hashed. The AUTHORITATIVE re-check lives inside
the D-4 hook's transaction (O-L) — this module never duplicates it.

THE K-9 HOURLY MISMATCH BUDGET (D-3b) is IN-RAM and P2-OWNED: a counter
keyed by ``(relay_instance_id, hour-bucket)``, read/incremented on every
token-mismatch refusal; exhaustion ⇒ ``mismatch-budget-exceeded``. It is
per-process defence-in-depth, NOT a trust decision — the DURABLE leg is
the PG per-token ``attempts`` + lockout (P1-owned). A successful
approval does NOT reset it; it rolls over with the hour bucket and is
cleared by a daemon restart.

KILL-SWITCH (D-2): ``ENSEMBLE_PROMPT_ASSENT_ENABLED``, read AT CALL
TIME, **default OFF (fail-closed)** through the ONE shared default
constant (:data:`PROMPT_ASSENT_DEFAULT_ENABLED`, declared beside
:class:`~daemon.config.PromptAssentConfig` in ``daemon/config.py`` and
IMPORTED here — this module's resolver and that class are the TWO
consumers of the SAME constant, so they can never disagree). The
resolver is the notify_user ``_resolve_bounds`` shape: the per-call env
value wins over the boot config value, which wins over the shared
default; every value is parsed THROUGH the model's own validators. An
indeterminate/invalid value fails CLOSED (an unreadable knob must never
arm a privileged lane — an invalid kill-switch spelling lands on the
shared OFF default even when the boot config had armed the lane). The
tool STAYS REGISTERED when the switch is off (refusal ``lane-disabled``,
never construction-absence) and the ``mint`` verb is refused too.
Arming is ONE env var per environment — no code change; the knob is
read per call, so a flip needs no restart. The config section is
ENV-ONLY (the ``nest_report`` precedent — no yaml block, no
``load_config`` pass-through, so a yaml tier could never shadow the
env knob).

PHASE 1.5 — DESIGNATED CHANNELS (pinned-approver model)
------------------------------------------------------
The lane's privacy leg grows ONE surface: a proven 1:1 DM **or** a
designated Discord channel (operator-pinned to exactly one human). The
rule and its ordering live in ONE place —
``daemon.repositories.prompt_assent.mint_context.designation_outcome`` —
and BOTH gates call it (the mint gate inside its ``not private`` arm,
the approve-side service predicate inside its own):

    PASS | NOT_DESIGNATED | GROUP_DM_SHAPE | APPROVER_NOT_PINNED |
    ABSENT_DISCRIMINATOR | ABSENT_IDENTITY | CONFIG_INVALID      (D-18)

Those are SIDE-NEUTRAL leg identities, never refusal codes: each gate maps
them through its own pinned table (``_MINT_DESIGNATION_CODES`` here; the
approve-side table beside the service predicate). The burning
``token-approver-mismatch`` keeps ONLY its existing 4e different-human
role — it is never the mapping of ``APPROVER_NOT_PINNED``.

THE MAP IS READ LAZILY, ONCE PER CALL. ``_DesignationCache`` wraps the
call-time resolver (:func:`_resolve_designations` — a DIRECT
``os.environ`` read, never a pydantic dotenv source, so the map stays
restart-bound) and parses on the FIRST non-DM use. A DM turn therefore
never reads the environment variable, never parses the map and never
touches the resolver — that is ``INVARIANT-DM``, the structural proof that
the DM path is unchanged.

House rules honored: tools NEVER raise — every failure is a typed
envelope string (REFUSED for the taxonomy codes, FAILED for the internal
error); the ONE frozen INFO line fires on EVERY terminal path with
counts and flags ONLY (never persona, never diff content, never the raw
or grouped token); a refusal is NEVER auto-retried — it is relayed
verbatim.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field, ValidationError

from ..config import (
    PROMPT_ASSENT_DEFAULT_ENABLED,
    PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET,
    PromptAssentConfig,
)
from ..repositories.prompt_assent.designation import (
    ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS,
    DesignationParseResult,
    designation_digest,
    describe_parse,
    parse_designations,
)
from ..repositories.prompt_assent.mint_context import (
    DesignationResult,
    designation_outcome,
)
from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

# ── Identity ────────────────────────────────────────────────────────────────

#: The category key — KEBAB-case, byte-identical in every place it
#: appears (the module, the registry, ari's meta.json allow entry). The
#: house pattern (``prompt-reload`` → ``prompt_reload``).
PROMPT_ASSENT_CATEGORY_KEY = "prompt-assent"

#: The tool name — snake_case (``prompt-assent`` → ``prompt_assent``).
PROMPT_ASSENT_TOOL_NAME = "prompt_assent"

#: The ONE module constant holding the kill-switch knob string.
KILL_SWITCH_ENV = "ENSEMBLE_PROMPT_ASSENT_ENABLED"

#: The single authorized agent (ari EXACTLY).
PROMPT_ASSENT_AUTHORIZED_AGENT = "ari"

# ── The shared fail-closed defaults (D-2 / K-9) ───────────────────────────
#
# The ONE defaults are IMPORTED from ``daemon/config.py`` (top of this
# file) — declared beside the ``PromptAssentConfig`` class (the M5
# pattern); BOTH consumers — that class and this module's call-time
# resolver — read the SAME constants, and this module re-exports the
# names so existing importers keep working. A structural pin proves no
# second literal is ever declared here. The architect OVERRULED the
# earlier split (P2 "off" / P4 "on"): under EITHER default that split
# is a defect.

#: The factor-2 window FRESHNESS bound (D-6 / J-2) — 600 s on
#: ``stamped_at``, IN ADDITION to the window's ``expires_at``.
PROMPT_ASSENT_WINDOW_FRESHNESS_S: int = 600

#: The list surface's bounds — a bounded enumeration (the repo's own
#: ``list_pending`` bounds at 200; the default mirrors P1's 50).
PROMPT_ASSENT_LIST_LIMIT_DEFAULT: int = 50
PROMPT_ASSENT_LIST_LIMIT_MAX: int = 200

#: The internal-error code — the ONE code that rides the FAILED envelope
#: (the taxonomy codes are all REFUSALS: no state change, no token burn).
PROMPT_ASSENT_INTERNAL_ERROR = "prompt_assent_internal_error"

# ── The action set (AR-2) ──────────────────────────────────────────────────

#: AR-2 — the action set is EXACTLY these FOUR verbs. A 3-verb or a
#: 5-verb set is a pin failure.
PROMPT_ASSENT_ACTIONS: tuple[str, ...] = ("list", "mint", "approve", "reject")

#: The mutating verbs (everything except the read surface).
PROMPT_ASSENT_MUTATING_ACTIONS: frozenset[str] = frozenset(
    {"mint", "approve", "reject"}
)

#: The verbs that consume a token and therefore require the
#: user-confirmation factor.
PROMPT_ASSENT_TOKEN_ACTIONS: frozenset[str] = frozenset({"approve", "reject"})

# ── The refusal taxonomy (architect §3 @ df8da01a6) ───────────────────────

#: The COMPLETE refusal taxonomy — architect §3, verbatim. A pin asserts
#: this set matches the ruling EXACTLY (no missing code, no extra code).
#: ``not-ari`` is the GENERIC envelope marker: it is never emitted as a
#: ``reason=`` code (the authorization class deliberately carries no code).
REFUSAL_TAXONOMY: frozenset[str] = frozenset({
    "lane-disabled",
    "not-ari",
    "invalid-arguments",
    "user-confirmation-missing",
    "no-user-origin-turn",
    "user-origin-window-expired",
    "user-origin-window-stale",
    "assent-verification-unavailable",
    "token-mismatch",
    "token-expired",
    "token-already-used",
    "token-superseded",
    "token-item-mismatch",
    "token-content-mismatch",
    "token-channel-mismatch",
    "token-approver-mismatch",
    "mint-requires-private-channel",
    "mint-live-token-exists",
    "token-locked",
    "mismatch-budget-exceeded",
    "item-not-found",
    "item-not-assentable",
    "item-not-pending",
    "already-decided",
    "self-approval-attempt",
    "extraction-incomplete",
    "audit-unavailable",
    # ── Phase 1.5 (designated channels) — +2 codes, D-8 / D-13 ────────
    "approver-not-pinned",
    "designation-config-invalid",
})

#: The EIGHT codes THIS PHASE PLACES (the amendment's list). Every one
#: of them is EMITTED by a gate in this file.
PHASE2_PLACED_CODES: frozenset[str] = frozenset({
    "invalid-arguments",
    "token-content-mismatch",
    "token-channel-mismatch",
    "mint-requires-private-channel",
    "mint-live-token-exists",
    "extraction-incomplete",
    "token-approver-mismatch",
    "mismatch-budget-exceeded",
})

#: The TWO codes THIS PHASE (Phase 1.5 — designated channels) PLACES.
#: The parent's :data:`PHASE2_PLACED_CODES` keeps its own eight and its own
#: meaning; this is a SEPARATE set so the parent's pin never has to move.
PHASE1_5_PLACED_CODES: frozenset[str] = frozenset({
    "approver-not-pinned",
    "designation-config-invalid",
})

#: The MISMATCH-class codes — the ones that consume the K-9 hourly
#: budget. A mismatch is a presented token that does not belong to this
#: item / this channel / this human — the shapes an attacker produces.
MISMATCH_CODES: frozenset[str] = frozenset({
    "token-mismatch",
    "token-item-mismatch",
    "token-content-mismatch",
    "token-channel-mismatch",
    "token-approver-mismatch",
})

#: The per-token lockout threshold (D-4 / D-3) — the durable leg's
#: default (P1's ``REVOKE_THRESHOLD_DEFAULT``); the counter and the
#: revoke commit in their OWN transaction (the D-4 blocking rule).
PROMPT_ASSENT_REVOKE_THRESHOLD: int = 5

#: The reject reason bound (≤ 500 chars, top-level in the audit payload).
PROMPT_ASSENT_REJECT_REASON_MAX: int = 500

#: The token body — 8 base32 chars (40 bits) behind the house
#: ``CONFIRM-`` prefix (``upgrade_journal.mint_nonce`` — the SSOT).
_TOKEN_BODY_RE = re.compile(r"^CONFIRM-[A-Z2-7]{8}$")
_TOKEN_BODY_LOOSE_RE = re.compile(r"^CONFIRM([A-Z2-7]{8})$")

#: Off-spellings for the per-call kill-switch.
_OFF_SPELLINGS = frozenset({"0", "false", "no", "off", "disabled"})
_ON_SPELLINGS = frozenset({"1", "true", "yes", "on", "enabled"})


# ── The in-RAM hourly mismatch budget (K-9 / D-3b) ───────────────────────

#: The in-process counter — ``(relay_instance_id, hour_bucket) → count``.
#: IN-RAM BY DESIGN (D-3b): per-process defence-in-depth, NOT a trust
#: decision. A daemon restart clears it (an attacker who cannot restart
#: the daemon is bounded by it); the DURABLE leg is the PG per-token
#: ``attempts`` + lockout (P1-owned). It is NEVER persisted.
_MISMATCH_BUDGET: dict[tuple[str, int], int] = {}


def _hour_bucket(now: Optional[float] = None) -> int:
    """The current hour bucket — ``floor(now / 3600)``."""
    return int((time.time() if now is None else now) // 3600)


def _budget_count(instance_id: str, bucket: int) -> int:
    """The current mismatch count for ``(instance_id, bucket)``."""
    return int(_MISMATCH_BUDGET.get((instance_id, bucket), 0))


def _budget_increment(instance_id: str, bucket: int) -> int:
    """Increment the counter and return the NEW count.

    Older buckets are pruned on every increment so the in-RAM map cannot
    grow without bound (the map is per-process and short-lived anyway).
    """
    key = (instance_id, bucket)
    count = _MISMATCH_BUDGET.get(key, 0) + 1
    _MISMATCH_BUDGET[key] = count
    for stale in [k for k in _MISMATCH_BUDGET if k[1] != bucket]:
        _MISMATCH_BUDGET.pop(stale, None)
    return count


def reset_mismatch_budget(instance_id: Optional[str] = None) -> None:
    """Clear the in-RAM budget — for one relay instance, or for all.

    NOT a lane reset (a successful approval never resets the budget —
    only mismatches count); exposed for the tests and for an operator
    facing a legitimate relay that tripped the budget.
    """
    if instance_id is None:
        _MISMATCH_BUDGET.clear()
        return
    for key in [k for k in _MISMATCH_BUDGET if k[0] == instance_id]:
        _MISMATCH_BUDGET.pop(key, None)


def _resolve_hourly_budget(manager: Any = None) -> int:
    """The K-9 budget — the ONE knob resolver, the ONE default constant.

    Phase 4 landed ``PromptAssentConfig.hourly_mismatch_budget``
    (default 10) on the daemon config; this resolver reads it through
    :func:`_resolve_knobs` (the boot config value, the per-call
    ``ENSEMBLE_PROMPT_ASSENT_HOURLY_MISMATCH_BUDGET`` env tier, or
    :data:`PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET` — the SAME
    constant the class consumes, never a second literal). The in-RAM
    MECHANISM below stays P2-owned (D-3b).
    """
    return int(_resolve_knobs(manager).hourly_mismatch_budget)


# ── The kill-switch + knob resolver (D-2 / K-9 — Phase 4) ─────────────────

#: The env prefix the :class:`~daemon.config.PromptAssentConfig` model
#: owns — the tool reads the SAME knob names the model does (one
#: vocabulary, one authority; the notify_user N8 pattern). The
#: kill-switch's own constant (:data:`KILL_SWITCH_ENV`) is the prefix +
#: ``ENABLED``.
PROMPT_ASSENT_ENV_PREFIX = "ENSEMBLE_PROMPT_ASSENT_"


def _coerce_switch(raw: Any) -> Optional[bool]:
    """The kill-switch boolean spellings — the accepted vocabulary.

    Returns ``None`` for an INDETERMINATE value; the caller fails
    CLOSED on ``None`` (D-2: an unreadable knob must never arm a
    privileged lane).
    """
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if value in _ON_SPELLINGS:
        return True
    if value in _OFF_SPELLINGS:
        return False
    return None


def _model_field_defaults() -> dict[str, Any]:
    """The model's OWN field defaults — the shared constants.

    Built WITHOUT constructing a model instance, so an INVALID ambient
    env value can never raise here (bare ``PromptAssentConfig()``
    consults the env tier and would raise on garbage; this reads the
    field defaults directly).
    """
    return {
        name: field.get_default(call_default_factory=True)
        for name, field in PromptAssentConfig.model_fields.items()
    }


def _knob_fallbacks(manager: Any = None) -> dict[str, Any]:
    """The per-knob FALLBACK values (the notify_user shape): the config
    object's boot-resolved value when the daemon carries one, else the
    model's own field defaults (the shared constants)."""
    base = getattr(getattr(manager, "config", None), "prompt_assent", None)
    fallbacks: dict[str, Any] = {}
    for name, field in PromptAssentConfig.model_fields.items():
        value = getattr(base, name, None)
        if value is None:
            value = field.get_default(call_default_factory=True)
        fallbacks[name] = value
    return fallbacks


def _resolve_knobs(manager: Any = None) -> PromptAssentConfig:
    """The ONE knob resolver — ``PromptAssentConfig`` is the SINGLE
    authority (the notify_user ``_resolve_bounds`` shape), read AT CALL
    TIME so tests and operators can flip knobs without a restart.

    Precedence (per knob): the per-call ENV value wins over the boot
    config value, which wins over the shared field default. The env
    values are parsed THROUGH the model's own validators — never by
    hand.

    D-2 fail-closed rules:

    * an INDETERMINATE/INVALID ``ENSEMBLE_PROMPT_ASSENT_ENABLED`` fails
      CLOSED — to :data:`PROMPT_ASSENT_DEFAULT_ENABLED` (the shared
      constant), NEVER to the boot value and never open;
    * an INVALID budget value repairs per-knob to its validated
      fallback (an invalid knob must not blind the others — the
      notify_user doctrine) and is named in a warning.
    """
    fallbacks = _knob_fallbacks(manager)
    raw: dict[str, Any] = {}

    # The kill-switch: the boolean spellings are coerced explicitly (the
    # lane's accepted vocabulary); an INVALID spelling fails CLOSED.
    enabled_raw = os.environ.get(KILL_SWITCH_ENV)
    if enabled_raw is not None and str(enabled_raw).strip() != "":
        coerced = _coerce_switch(enabled_raw)
        if coerced is None:
            logger.warning(
                "prompt_assent: invalid %s=%r (expected a boolean "
                "spelling); failing CLOSED (the lane stays disabled)",
                KILL_SWITCH_ENV,
                enabled_raw,
            )
            fallbacks["enabled"] = PROMPT_ASSENT_DEFAULT_ENABLED
        else:
            raw["enabled"] = coerced

    budget_raw = os.environ.get(
        f"{PROMPT_ASSENT_ENV_PREFIX}HOURLY_MISMATCH_BUDGET"
    )
    if budget_raw is not None and str(budget_raw).strip() != "":
        raw["hourly_mismatch_budget"] = budget_raw

    try:
        return PromptAssentConfig(**{**fallbacks, **raw})
    except ValidationError as exc:
        # One invalid knob must not blind the others: pydantic reports
        # EVERY failing field, so each is repaired with its own
        # validated fallback (and named in a warning).
        reasons: dict[str, str] = {}
        for err in exc.errors():
            loc = err.get("loc") or ()
            if loc:
                reasons[str(loc[0])] = str(err.get("msg", "invalid value"))
        repaired: dict[str, Any] = {}
        for name, value in raw.items():
            if name in reasons:
                knob_fallback = fallbacks.get(name)
                logger.warning(
                    "prompt_assent: %s%s=%r is INVALID (%s) — using the "
                    "validated fallback %r",
                    PROMPT_ASSENT_ENV_PREFIX,
                    name.upper(),
                    value,
                    reasons[name],
                    knob_fallback,
                )
                repaired[name] = knob_fallback
            else:
                repaired[name] = value
        try:
            return PromptAssentConfig(**{**fallbacks, **repaired})
        except ValidationError as exc2:  # pragma: no cover — defensive
            logger.warning(
                "prompt_assent: the knobs are unresolvable even after "
                "the per-knob fallback (%s) — using the shared defaults",
                exc2,
            )
            return PromptAssentConfig(**_model_field_defaults())


def _resolve_enabled(manager: Any = None) -> bool:
    """Kill-switch resolver — default OFF, fail-closed, read PER CALL.

    D-2: the switch is read at call time; the tool STAYS REGISTERED
    when it is off (refusal-at-call, never construction-absence); the
    ``mint`` verb is refused too; an INDETERMINATE value fails CLOSED
    (an unreadable knob must never arm a privileged lane). The default
    is THE ONE shared constant (:data:`PROMPT_ASSENT_DEFAULT_ENABLED`,
    imported from :mod:`daemon.config` — the same constant the
    ``PromptAssentConfig`` class consumes), so the two consumers can
    never disagree.
    """
    return bool(_resolve_knobs(manager).enabled)


# ── The designation-map resolver (Phase 1.5 — cached-lazy, D-20) ─────────
#
# THE MAP IS READ AT CALL TIME, DIRECTLY FROM ``os.environ`` — never through
# a pydantic-settings dotenv source. ``PromptAssentConfig`` is instantiated
# per call, so a dotenv-backed field would HOT-RELOAD the map without a
# restart and silently break the restart-bound story (D-3 / D-2.1). The
# direct read is therefore a pin, not an accident.
#
# The parse is LAZY: nothing in this module resolves the map at import time
# and the DM path never reaches this function at all (INVARIANT-DM). A
# first-resolution log fires the moment the map is actually read — no boot
# hook, no ``daemon/api.py`` edit (D-20).

#: The process-lifetime LOGGING memo — the last EFFECTIVE map digest this
#: process logged. Module-level so "first successful resolution in a
#: process" and "the digest changed between resolutions" survive across
#: tool calls. It is NEVER a decision input: every call re-parses, so a
#: mid-flight environment change is always visible (the A-9 window).
_DESIGNATION_LOG_STATE: dict[str, Any] = {"resolved": False, "digest": None}


def _reset_designation_log_state() -> None:
    """Clear the resolver's logging memo (tests / operator diagnosis only)."""
    _DESIGNATION_LOG_STATE["resolved"] = False
    _DESIGNATION_LOG_STATE["digest"] = None


def _resolve_designations() -> DesignationParseResult:
    """Resolve the operator's designation map from the environment.

    Returns the typed parse outcome (:func:`parse_designations`), logs the
    mandatory content-free map-parse line, one WARNING per offending entry
    (BY KEY — never by value), and marks the effective-map line on the
    FIRST successful resolution in this process and on every DIGEST CHANGE
    thereafter (D-20). An invalid map is a typed outcome here, never an
    exception (PR-1: the DM path is untouched because it never gets here).
    """
    raw = os.environ.get(ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS)
    result = parse_designations(raw)

    for error in result.errors:
        logger.warning(
            "prompt_assent: designation map entry refused — %s "
            "(the whole map is invalid; the designated-channel path is "
            "unavailable)",
            error,
        )

    resolved = result.valid and result.map is not None
    digest = designation_digest(result.map) if resolved and result.map else None
    first_resolution = resolved and not _DESIGNATION_LOG_STATE["resolved"]
    digest_changed = bool(
        resolved
        and _DESIGNATION_LOG_STATE["resolved"]
        and digest != _DESIGNATION_LOG_STATE["digest"]
    )
    if resolved:
        _DESIGNATION_LOG_STATE["resolved"] = True
        _DESIGNATION_LOG_STATE["digest"] = digest

    logger.info(
        "prompt_assent %s first_resolution=%s digest_changed=%s",
        describe_parse(result),
        "1" if first_resolution else "0",
        "1" if digest_changed else "0",
    )
    return result


class _DesignationCache:
    """The PER-CALL memo of the designation map (D-2.1 / D-2.2).

    Constructed ONCE per tool call beside the knob resolver. The FIRST
    non-DM use resolves and parses; every later use in the SAME call
    returns the memoised result, so the mint gate and the approve leg can
    never disagree within one call. A FRESH call re-resolves — which is
    exactly what makes a mid-flight re-designation observable (A-9).

    The DM path never calls :meth:`get`; that is the structural half of
    ``INVARIANT-DM`` (the runtime half is the resolver-raises pin).
    """

    __slots__ = ("_result",)

    def __init__(self) -> None:
        self._result: Optional[DesignationParseResult] = None

    def get(self) -> DesignationParseResult:
        """The parsed map — resolved on first use, memoised thereafter."""
        if self._result is None:
            self._result = _resolve_designations()
        return self._result

    @property
    def digest(self) -> Optional[str]:
        """The 12-hex map fingerprint, or ``None`` when the map is invalid.

        Reading this RESOLVES the map — callers must therefore be on the
        non-DM path (the DM branch never asks for a digest: a DM mint emits
        NO ``map_digest`` at all — AC13).
        """
        result = self.get()
        if not result.valid or result.map is None:
            return None
        return designation_digest(result.map)

    def outcome(self, discord_meta: Any, user_id: Any) -> DesignationResult:
        """Classify a NON-DM turn (P1's ONE ordering owner)."""
        return designation_outcome(self.get(), discord_meta, user_id)


#: The MINT-side leg mapping (D-18) — every neutral leg maps to EXACTLY one
#: code. ``PASS`` is deliberately absent: it is not a refusal.
_MINT_DESIGNATION_CODES: dict[DesignationResult, str] = {
    DesignationResult.NOT_DESIGNATED: "mint-requires-private-channel",
    DesignationResult.GROUP_DM_SHAPE: "mint-requires-private-channel",
    DesignationResult.ABSENT_DISCRIMINATOR: "mint-requires-private-channel",
    DesignationResult.APPROVER_NOT_PINNED: "approver-not-pinned",
    DesignationResult.ABSENT_IDENTITY: "extraction-incomplete",
    DesignationResult.CONFIG_INVALID: "designation-config-invalid",
}

#: The EXISTING mint refusal detail — BYTE-IDENTICAL to v0.29.18 (architect
#: §2.4 / D-2.3 / T2.8). The designated-channel wording lives on the two NEW
#: codes' messages ONLY, so the DM cells' envelopes stay byte-identical.
_MINT_NOT_PRIVATE_DETAIL = (
    "the mint context is not a proven 1:1 DM (is_dm AND NOT "
    "is_group_dm; an absent discriminator is not private) — the "
    "mint is refused and NO token row is written"
)

#: The MINT-side detail for the codes the DESIGNATED branch can produce.
#: The two NEW codes are mandated by T2.8; ``extraction-incomplete`` is an
#: EXISTING code whose bare detail ("not a proven 1:1 DM") would be a lie on
#: the designated leg (the surface was fine — the human was not identified),
#: so it gets its own truthful, content-free text HERE. The pre-existing
#: ``mint-requires-private-channel`` string is NOT in this map — it stays
#: byte-identical (architect §2.4 / D-2.3).
_MINT_DESIGNATION_DETAILS: dict[str, str] = {
    "extraction-incomplete": (
        "the daemon-derived mint context is incomplete — the designated "
        "channel's turn carries no usable human identity "
        "(metadata['discord']['user_id'] is absent), so no pinned-approver "
        "comparison is possible (fail-closed, D-5); NO token row is written"
    ),
    "approver-not-pinned": (
        "this human is not the pinned approver of this room — the channel is "
        "designated, but the designated human is someone else; NO token row "
        "is written"
    ),
    "designation-config-invalid": (
        "the operator's designated-channel map is malformed or carries an "
        "invalid entry — the designated-channel path is unavailable (the "
        "offending keys are named in the daemon log; NO channel, user or raw "
        "value is ever logged) and NO token row is written"
    ),
}

#: The 4f codes that MUST NOT touch the durable counter or the K-9 budget.
#: The two Phase-1.5 codes are the ruling's list; ``extraction-incomplete``
#: joins them because it is NOT a mismatch-class code anywhere else in the
#: lane (4e refuses the same shape without recording — this keeps that
#: discipline even on a path 4e already guards, i.e. defence in depth
#: against a future reordering).
_APPROVE_NON_BURNING_CODES: frozenset[str] = PHASE1_5_PLACED_CODES | {
    "extraction-incomplete"
}

#: The APPROVE-side detail for the two NEW codes (T2.8 / D-8) — the side
#: ambiguity is resolved by TEXT at zero taxonomy cost (the INFO line already
#: carries ``action=``). Content-free: no persona, no diff, no channel name.
_APPROVE_DESIGNATION_DETAILS: dict[str, str] = {
    "extraction-incomplete": (
        "the approving turn's designated-channel context carries no usable "
        "human identity (metadata['discord']['user_id'] is absent), so no "
        "pinned-approver comparison is possible — fail closed (D-5); the "
        "token stays live and its attempt counter is UNTOUCHED"
    ),
    "approver-not-pinned": (
        "the mint-witnessed human is no longer this room's current pinned "
        "approver — the designation moved between the mint and this decision "
        "(fail-closed: re-mint in a fresh user turn by the current pinned "
        "approver); the token stays live and its attempt counter is UNTOUCHED"
    ),
    "designation-config-invalid": (
        "the operator's designated-channel map is malformed or carries an "
        "invalid entry at decision time — the designated-channel path is "
        "unavailable, so this approval cannot be verified; the token stays "
        "live and its attempt counter is UNTOUCHED"
    ),
}


# ── Envelopes (notify_user.py:314-321 shape) ─────────────────────────────


def _refusal(code: str, message: str) -> str:
    """The house REFUSED envelope (a taxonomy code, no state change)."""
    return f"Error: {PROMPT_ASSENT_TOOL_NAME} REFUSED — reason={code}: {message}"


def _failure(code: str, message: str) -> str:
    """The house FAILED envelope (the internal-error class)."""
    return f"Error: {PROMPT_ASSENT_TOOL_NAME} FAILED — reason={code}: {message}"


def _not_ari(agent_id: str) -> str:
    """The notify-exact GENERIC ERROR envelope (notify_user.py:703-711).

    NO refusal code, NO ledger noise — the authorization class
    deliberately carries neither (AM-6). The caller learns only that the
    tool is not theirs.
    """
    return (
        f"ERROR: {PROMPT_ASSENT_TOOL_NAME} is available to the "
        f"{PROMPT_ASSENT_AUTHORIZED_AGENT} agent only "
        f"(caller: {agent_id or 'unknown'})."
    )


def _emit_info_line(
    *,
    action: str,
    outcome: str,
    code: Optional[str],
    instance_id: str = "",
    agent_id: str = "",
    count: int = 0,
    flags: Optional[dict[str, Any]] = None,
    map_digest: Optional[str] = None,
) -> None:
    """The ONE frozen INFO line — counts and flags ONLY.

    Content-free by construction (the "logs content-free v1" doctrine):
    never persona, never diff content, never the raw or grouped token.
    Fired on EVERY terminal path (success and refusal alike).

    ``map_digest`` is a DEDICATED field, NOT a flag: it BYPASSES the flags
    renderer entirely (the ``'1'/'0'`` boolean coercion below would render a
    hex string as ``0`` — architect blocker #4). It is emitted ONLY by a
    designated-CHANNEL mint; a DM mint passes ``None`` and the field is
    absent from the line (AC13 / INVARIANT-DM).
    """
    flag_text = ",".join(
        f"{k}={'1' if v else '0'}" for k, v in sorted((flags or {}).items())
    )
    line = (
        "prompt_assent outcome=%s action=%s code=%s agent=%s instance=%s "
        "count=%d flags=%s"
    )
    args: list[Any] = [
        outcome,
        action,
        code or "-",
        agent_id or "-",
        (instance_id or "-")[:8],
        int(count),
        flag_text or "-",
    ]
    if map_digest is not None:
        line += " map_digest=%s"
        args.append(map_digest)
    logger.info(line, *args)


# ── Token helpers ─────────────────────────────────────────────────────────


def canonical_token_form(token: str) -> str:
    """Normalize an echoed token to the STORED raw form.

    The Emperor may echo the grouped rendering (``CONFIRM-XXXX-XXXX``)
    or the raw one; the durable row stores ``sha256`` of the RAW form
    (``CONFIRM-XXXXXXXX``). This maps any accepted spelling onto the
    raw form so the hash lookup is stable. Non-token strings are
    returned trimmed and unchanged (they simply never match a row).
    """
    if not isinstance(token, str):
        return ""
    stripped = token.strip()
    if _TOKEN_BODY_RE.match(stripped):
        return stripped
    upper = re.sub(r"[^A-Z0-9]", "", stripped.upper())
    match = _TOKEN_BODY_LOOSE_RE.match(upper)
    if match:
        return f"CONFIRM-{match.group(1)}"
    return stripped


def token_hash(token: str) -> str:
    """``sha256`` of the canonical raw token — the stored form."""
    return hashlib.sha256(canonical_token_form(token).encode("utf-8")).hexdigest()


def token_fingerprint(token: str) -> str:
    """``sha256(token)[:12]`` — the audit payload's fingerprint."""
    return token_hash(token)[:12]


# ── The daemon-derived context (AR-1 / D-5) ──────────────────────────────


class _AssentContext:
    """The daemon-derived context of the factor-2 window's message row.

    EVERY field is derived from DAEMON-OWNED evidence — the window's
    message row (its nested ``metadata["discord"]`` and its own columns).
    NOT ONE field is ever read from a tool argument (the anti-forgery
    analogue of the repo's R3).
    """

    __slots__ = (
        "source_id",
        "channel_id",
        "is_dm",
        "user_id",
        "user_name",
        "external_user_id",
        "guild_id",
        "channel_type",
        "platform_message_id",
        "message_id",
        "instance_id",
        "content",
        "discord_meta",
    )

    def __init__(self, **kwargs: Any) -> None:
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot))

    @property
    def is_private(self) -> bool:
        """``True`` ONLY for a proven 1:1 DM (AR-1 leg 2 / K-3).

        Delegates to P1's fail-closed classifier: an ABSENT or
        UNPARSEABLE ``is_group_dm`` discriminator ⇒ NOT private.
        """
        from ..repositories.prompt_assent.mint_context import (
            is_private_assent_channel,
        )

        return is_private_assent_channel(self.discord_meta)


def _discord_meta_of(row: Any) -> dict[str, Any]:
    """The NESTED ``metadata["discord"]`` dict of a message row.

    D-5: the approving-user key is the NESTED
    ``message_metadata["discord"]["user_id"]`` — REQUIRED, fail-closed.
    A top-level ``metadata.get("user_id")`` returns ``None`` and MUST
    never be used.
    """
    meta = getattr(row, "message_metadata", None) or {}
    if not isinstance(meta, dict):
        return {}
    nested = meta.get("discord")
    return nested if isinstance(nested, dict) else {}


def _derive_external_user_id(
    manager: Any, row: Any, discord_meta: dict[str, Any], user_id: str, is_dm: Any
) -> Optional[str]:
    """The destination identity of the human — daemon-derived.

    Preference order (all daemon-owned):

    1. an explicit ``external_user_id`` in the nested metadata (a future
       adapter may add it);
    2. the instance's SOURCE MAPPING row (the authoritative
       ``(source_id, external_user_id) → instance`` binding);
    3. the Discord 1:1-DM canonical form ``dm:<user_id>`` — reachable
       ONLY when the row proves a DM (byte-behaviour unchanged);
    4. the Discord GUILD-CHANNEL canonical form
       ``channel:<guild_id>:<channel_id>:<user_id>`` — the Phase-1.5
       designated-channel shape (D-19).

    **The guild form is AUDIT-ONLY.** It is a composite that the adapter's
    ``_parse_external_user_id`` is NEVER applied to (there is no in-tree
    consumer, and the pin in the Phase-2 census asserts that): the
    OPERATIONAL identity of a designated-channel mint stays ``mint_user_id``
    (the daemon-side snowflake equality the approve legs 4d/4e use). The
    composite exists so the ledger can say *where* the mint happened without
    collapsing the human into an ambiguous channel key (which is what the
    adapter's lossy ``{guild}:{channel}`` mapping grammar would do).
    """
    explicit = discord_meta.get("external_user_id")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    meta = getattr(row, "message_metadata", None) or {}
    if isinstance(meta, dict):
        top = meta.get("external_user_id")
        if isinstance(top, str) and top.strip():
            return top.strip()
    source_repo = getattr(manager, "_source_repository", None)
    instance_id = getattr(row, "instance_id", "") or ""
    if source_repo is not None and instance_id:
        try:
            mapping = source_repo.get_instance_mapping_by_instance(instance_id)
        except Exception:  # noqa: BLE001 — never break the gate
            mapping = None
        external = getattr(mapping, "external_user_id", None)
        if isinstance(external, str) and external.strip():
            return external.strip()
    if is_dm is True and user_id:
        return f"dm:{user_id}"
    # ── the non-DM branch (D-19, AUDIT-ONLY) ───────────────────────────
    # ``is_dm is False`` proves a GUILD shape; the composite needs the guild
    # and the channel plus the human. An ABSENT nested user id yields NO
    # derivation (the accept path's ``extraction-incomplete`` is the
    # truthful code — M-19), never a partial composite.
    if is_dm is False and user_id:
        guild_id = discord_meta.get("guild_id")
        channel_id = discord_meta.get("channel_id")
        if isinstance(guild_id, str) and guild_id.strip():
            if isinstance(channel_id, str) and channel_id.strip():
                return f"channel:{guild_id.strip()}:{channel_id.strip()}:{user_id}"
    return None


def _derive_context(manager: Any, row: Any, window: dict[str, Any]) -> _AssentContext:
    """Build the daemon-derived context from the window's message row."""
    discord_meta = _discord_meta_of(row)
    user_id = discord_meta.get("user_id")
    user_id = user_id.strip() if isinstance(user_id, str) else ""
    is_dm = discord_meta.get("is_dm")
    source_id = (
        getattr(row, "source", None)
        or window.get("source")
        or ""
    )
    return _AssentContext(
        source_id=str(source_id),
        channel_id=discord_meta.get("channel_id"),
        is_dm=is_dm,
        user_id=user_id,
        user_name=discord_meta.get("user_name"),
        external_user_id=_derive_external_user_id(
            manager, row, discord_meta, user_id, is_dm
        ),
        guild_id=discord_meta.get("guild_id"),
        channel_type=discord_meta.get("channel_type"),
        platform_message_id=discord_meta.get("message_id"),
        message_id=window.get("message_id"),
        instance_id=getattr(row, "instance_id", None),
        content=getattr(row, "content", None),
        discord_meta=discord_meta,
    )


# ── The gate (O-E — the ONE implementation site) ──────────────────────────


class _GateFailure:
    """One enumerated gate failure — a code + a human-readable detail."""

    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail

    def render(self) -> str:
        return f"{self.code}: {self.detail}"


class _GateOutcome:
    """The gate's verdict — the window + the message row, or the failures."""

    __slots__ = ("failures", "window", "row", "context")

    def __init__(self) -> None:
        self.failures: list[_GateFailure] = []
        self.window: Optional[dict[str, Any]] = None
        self.row: Any = None
        self.context: Optional[_AssentContext] = None

    @property
    def ok(self) -> bool:
        return not self.failures


def _read_window(manager: Any, instance_id: str) -> Optional[dict[str, Any]]:
    """The live user-origin window for THIS instance.

    Read from ``manager._user_origin_windows`` — the SAME object the
    promote gate reads (``daemon/tools/upgrade_tools.py:3388-3391``).
    """
    windows = getattr(manager, "_user_origin_windows", None)
    if isinstance(windows, dict):
        window = windows.get(instance_id)
        if isinstance(window, dict):
            return window
    return None


def _classify_window_source(manager: Any, source: Any) -> tuple[bool, str]:
    """Classify the window's source with the UNFORKED classifier.

    ``classify_user_origin`` (``daemon/tools/upgrade_journal.py``) is
    THE single source of truth for user-origin classification — the
    trust anchor is never forked (S-1 (i)).
    """
    from daemon.tools.upgrade_journal import classify_user_origin

    registry = getattr(manager, "source_registry", None)
    registry_get = registry.get if registry is not None else None
    try:
        return classify_user_origin(source, registry_get)
    except Exception as exc:  # noqa: BLE001 — fail closed
        return False, f"classifier-error:{type(exc).__name__}"


async def _run_factor_gates(
    manager: Any,
    *,
    instance_id: str,
    action: str,
    token: str,
    user_confirmed: bool,
) -> _GateOutcome:
    """The FACTOR GATES — steps 3a..3g of the architect's pipeline.

    ALL failures are ENUMERATED in ONE refusal (no partial proceed).
    """
    from daemon.tools.upgrade_journal import parse_iso_utc, user_origin_sources_display

    outcome = _GateOutcome()

    # 3a. the explicit user confirmation (approve/reject only).
    if action in PROMPT_ASSENT_TOKEN_ACTIONS and not user_confirmed:
        outcome.failures.append(
            _GateFailure(
                "user-confirmation-missing",
                "user_confirmed is false — the human's confirmation must be "
                "relayed explicitly; the three factors are always required",
            )
        )

    # 3b. a live window for THIS instance.
    window = _read_window(manager, instance_id)
    if window is None:
        observed = None
        last_stamps = getattr(manager, "_user_origin_last_stamp", None)
        if isinstance(last_stamps, dict):
            observed = last_stamps.get(instance_id)
        if isinstance(observed, dict) and observed.get("source") is not None:
            detail = (
                f"observed source {observed.get('source')!r} classified "
                f"{observed.get('detail', '?')!r} "
                f"(classification: {user_origin_sources_display()})"
            )
        else:
            detail = (
                "no stamp recorded this turn — daemon restart wipes the "
                "in-memory windows "
                f"(classification: {user_origin_sources_display()})"
            )
        outcome.failures.append(
            _GateFailure(
                "no-user-origin-turn",
                f"this turn was not triggered by a user-origin message — {detail}",
            )
        )
        return outcome
    outcome.window = window

    # 3c. the window's source must classify user-origin (UNFORKED).
    source = window.get("source")
    is_user, classifier_detail = _classify_window_source(manager, source)
    if not is_user:
        outcome.failures.append(
            _GateFailure(
                "no-user-origin-turn",
                f"the window's source {source!r} classified "
                f"{classifier_detail!r} (classification: "
                f"{user_origin_sources_display()})",
            )
        )

    # 3d. the window must be unexpired (an unparseable stamp is EXPIRED).
    expires = parse_iso_utc(window.get("expires_at"))
    if expires is None or datetime.now(tz=timezone.utc) > expires:
        outcome.failures.append(
            _GateFailure(
                "user-origin-window-expired",
                f"the user-origin window (source {source!r}) expired at "
                f"{window.get('expires_at')!r} — ask again in a fresh user turn",
            )
        )

    # 3e. the window must be FRESH (stamped_at within the bound).
    stamped = parse_iso_utc(window.get("stamped_at"))
    if stamped is None:
        outcome.failures.append(
            _GateFailure(
                "user-origin-window-stale",
                f"the window's stamped_at {window.get('stamped_at')!r} is "
                f"absent or unparseable — fail closed",
            )
        )
    else:
        age = (datetime.now(tz=timezone.utc) - stamped).total_seconds()
        if age > PROMPT_ASSENT_WINDOW_FRESHNESS_S:
            outcome.failures.append(
                _GateFailure(
                    "user-origin-window-stale",
                    f"the window was stamped {int(age)}s ago — the bound is "
                    f"{PROMPT_ASSENT_WINDOW_FRESHNESS_S}s",
                )
            )

    # 3f. the window's message row must be readable (fail closed).
    message_id = window.get("message_id")
    row = None
    if message_id:
        repo = getattr(manager, "_queue_repository", None)
        if repo is not None:
            try:
                row = await asyncio.to_thread(repo.get, message_id)
            except Exception:  # noqa: BLE001 — an unreadable row fails closed
                row = None
    if row is None or getattr(row, "content", None) is None:
        outcome.failures.append(
            _GateFailure(
                "assent-verification-unavailable",
                "the triggering message row could not be read (daemon "
                "restarted since the turn? the row is wiped at boot)",
            )
        )
        return outcome
    outcome.row = row

    # The daemon-derived context — from the WINDOW'S MESSAGE ROW, never
    # from tool arguments.
    outcome.context = _derive_context(manager, row, window)

    # 3g. the token must be echoed verbatim in THAT message's content.
    if action in PROMPT_ASSENT_TOKEN_ACTIONS:
        from daemon.tools.upgrade_journal import nonce_in_content

        echoed = canonical_token_form(token)
        if not nonce_in_content(echoed, getattr(row, "content", None)):
            outcome.failures.append(
                _GateFailure(
                    "token-content-mismatch",
                    "this turn's human message does not carry the token — "
                    "the token must be echoed by the human in the message "
                    "that triggered this turn",
                )
            )

    return outcome


# ── The accept seam (Phase-3 owned — the attestation assembly) ──────────


class _AcceptancePathUnavailable(RuntimeError):
    """The Phase-3 acceptance path is not wired yet.

    Phase 2 wires the CALL SITE behind the Phase-3 signature
    (``svc.approve_assent`` / ``svc.reject_assent``); Phase 3 owns the
    attestation assembly and the service entrypoint. The tool NEVER
    falls back to ``repo.atomic_transition`` — that bypass is the F2
    lesson (``daemon/routers/prompts.py:747-753``).
    """


async def _accept_via_service(
    manager: Any,
    *,
    action: str,
    version_id: str,
    context: _AssentContext,
    token_row: Any,
    window: dict[str, Any],
    reject_reason: str,
) -> str:
    """Call the Phase-3 acceptance service — the WIRED call site.

    Assembles the attestation from DAEMON-OWNED evidence only
    (:func:`_build_attestation`) and hands it to
    ``svc.approve_assent(version_id, attestation)`` /
    ``svc.reject_assent(version_id, attestation, reason)`` — the
    service is the ONLY route to a relayed decision. Raises
    :class:`_AcceptancePathUnavailable` when the service entrypoint
    is absent (the mid-build state) — the tool then returns the house
    FAILED envelope. No bypass, no raw ``atomic_transition``.
    """
    service = getattr(manager, "_prompt_version_service", None)
    method = getattr(
        service,
        "approve_assent" if action == "approve" else "reject_assent",
        None,
    )
    if method is None:
        raise _AcceptancePathUnavailable(
            "the assent acceptance path (svc."
            f"{'approve_assent' if action == 'approve' else 'reject_assent'}) "
            "is not wired yet"
        )
    attestation = _build_attestation(
        manager,
        version_id=version_id,
        context=context,
        token_row=token_row,
        window=window,
    )
    if action == "approve":
        return await method(version_id, attestation)
    return await method(version_id, attestation, reject_reason)


def _attestation_refusal(what: str) -> Any:
    """The fail-closed D-5 extraction refusal — a typed carrier.

    ``extraction-incomplete`` rides the tool's taxonomy handler (any
    exception carrying a taxonomy ``code`` is surfaced verbatim as a
    REFUSED envelope — no state change, no token burn).
    """
    from ..services.prompt_version_service import AssentRefusalError

    return AssentRefusalError(
        "extraction-incomplete",
        f"the daemon-derived attestation assembly is incomplete — {what} "
        "(the nested metadata['discord'] extraction is REQUIRED and "
        "fail-closed, D-5); NO decision is recorded",
    )


def _require_str(value: Any, what: str) -> str:
    """A REQUIRED non-empty string from daemon-owned evidence."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise _attestation_refusal(what)


def _build_attestation(
    manager: Any,
    *,
    version_id: str,
    context: _AssentContext,
    token_row: Any,
    window: dict[str, Any],
) -> Any:
    """The attestation assembly — Phase-3 owned (tasks 3.3 / 3.5).

    Assembles the typed ``AssentAttestation`` (architect §2.3 field
    set) from DAEMON-OWNED evidence ONLY — **the tool arguments never
    contribute** (the anti-forgery analogue of the repo's R3; this
    function's signature accepts none):

    * the window — ``source``, ``message_id`` (the daemon-minted id
      of the approving turn), ``stamped_at``, ``expires_at``;
    * the extracted human identity — the NESTED
      ``metadata["discord"]`` of the window's message row (D-5:
      ``user_id`` REQUIRED fail-closed → ``extraction-incomplete``;
      ``guild_id`` / ``channel_id`` / ``channel_type`` / ``is_dm`` /
      the PLATFORM-side message id from the same dict;
      ``user_name`` cosmetic only, never authority;
      ``external_user_id`` the destination identity, NEVER the
      approving-user key);
    * the token row — the item binding (the mint's denormalized
      item key), ``token_id``, ``token_fingerprint`` and
      **``render_sha256`` CARRIED from the row** (task 3.5/O-L: the
      hash was computed at mint from the EXACT canonical-render
      bytes; it is NEVER recomputed here — the only render-hash
      re-check site is P1's D-4 hook — and NEVER copied from
      ``prompt_versions.content_hash``);
    * the relay identity — the authorized agent constant (the gate
      proved the caller IS that agent) and the message row's
      instance id (the relay instance).

    ``approver_subject = f"{source_id}:{user_id}"`` (D-5); the type's
    own ``__post_init__`` re-validates the composition and the
    completeness of every field.
    """
    from daemon.tools.upgrade_journal import now_iso
    from ..repositories.prompt_version.assent_attestation import (
        AssentAttestation,
        AssentHumanIdentity,
        AssentItemBinding,
    )

    if context is None or token_row is None:
        raise _attestation_refusal(
            "the daemon-derived context or the token row is missing"
        )

    # The item binding — from the TOKEN ROW (the mint's denormalized
    # item key), never from a tool argument. The gate has already
    # proven token_row.version_id == version_id (step 4c); the re-check
    # here is fail-closed belt-and-braces.
    row_version_id = getattr(token_row, "version_id", None)
    if row_version_id != version_id:
        from ..services.prompt_version_service import AssentRefusalError

        raise AssentRefusalError(
            "token-item-mismatch",
            f"the token is bound to version_id={row_version_id!r}, not "
            f"{version_id!r}",
        )
    item_binding = AssentItemBinding(
        version_id=str(row_version_id),
        agent_id=_require_str(
            getattr(token_row, "agent_id", None), "the item's agent_id"
        ),
        version_tag=str(getattr(token_row, "version_tag", "") or ""),
        artifact=_require_str(
            getattr(token_row, "artifact", None), "the item's artifact"
        ),
        version=int(getattr(token_row, "version", 0) or 0),
    )

    # ── the D-5 extraction contract (fail closed) ─────────────────────
    source_id = _require_str(context.source_id, "the window's source_id")
    user_id = _require_str(
        context.user_id,
        "message_metadata['discord']['user_id'] (a top-level "
        "metadata.get('user_id') returns None and is never used)",
    )
    channel_id = _require_str(
        context.channel_id, "metadata['discord']['channel_id']"
    )
    channel_type = _require_str(
        context.channel_type, "metadata['discord']['channel_type']"
    )
    if not isinstance(context.is_dm, bool):
        # The attestation type demands a REAL boolean; anything else
        # (absent / 1 / "yes") is unparseable under the ruling and
        # fails closed — the same discipline as the private check.
        raise _attestation_refusal(
            "metadata['discord']['is_dm'] (a real boolean is required)"
        )
    external_user_id = _require_str(
        context.external_user_id,
        "the destination identity (external_user_id — daemon-derived; "
        "NEVER the approving-user key)",
    )
    relay_source_message_id = _require_str(
        context.platform_message_id,
        "the PLATFORM-side message id (metadata['discord']['message_id'] "
        "— the daemon queue id is ephemeral/wiped at boot; without the "
        "platform id the forensic chain breaks)",
    )
    relay_instance_id = _require_str(
        context.instance_id,
        "the relay instance id (the message row's instance_id)",
    )

    # ── the window stamps (the gate evaluated them; carried verbatim) ─
    stamped_at = _require_str(
        window.get("stamped_at"), "the factor-2 window stamped_at"
    )
    expires_at = _require_str(
        window.get("expires_at"), "the factor-2 window expires_at"
    )
    human_message_id = _require_str(
        window.get("message_id"),
        "the daemon-minted message id of the approving turn",
    )

    human_identity = AssentHumanIdentity(
        source_id=source_id,
        user_id=user_id,
        channel_id=channel_id,
        channel_type=channel_type,
        is_dm=context.is_dm,
        external_user_id=external_user_id,
        user_name=(
            str(context.user_name).strip()
            if isinstance(context.user_name, str) and context.user_name.strip()
            else None
        ),
        guild_id=(
            str(context.guild_id)
            if context.guild_id is not None and str(context.guild_id).strip()
            else None
        ),
    )

    render_sha = getattr(token_row, "render_sha256", None)
    if not isinstance(render_sha, str) or len(render_sha) != 64:
        raise _attestation_refusal(
            "the token row's render_sha256 (the canonical-render binding "
            "is REQUIRED on the attestation — J-3/K-7)"
        )
    token_hash_value = getattr(token_row, "token_hash", None)
    if not isinstance(token_hash_value, str) or len(token_hash_value) < 12:
        raise _attestation_refusal("the token row's token_hash")

    return AssentAttestation(
        item_binding=item_binding,
        # CARRIED (task 3.5 / O-L) — computed at mint from the EXACT
        # canonical-render bytes; NEVER recomputed here (the only
        # render-hash re-check site is P1's D-4 hook) and NEVER a
        # prompt_versions.content_hash copy.
        render_sha256=render_sha,
        human_message_id=human_message_id,
        relay_source_message_id=relay_source_message_id,
        human_identity=human_identity,
        approver_subject=f"{source_id}:{user_id}",
        token_id=str(getattr(token_row, "token_id")),
        token_fingerprint=token_hash_value[:12],
        window_stamped_at=stamped_at,
        window_expires_at=expires_at,
        # The gate proved the caller IS the authorized agent; the relay
        # instance is the message row's own instance_id — both
        # daemon-derived.
        relay_agent=PROMPT_ASSENT_AUTHORIZED_AGENT,
        relay_instance_id=relay_instance_id,
        attested_at=now_iso(),
    )


# ── The action branches ───────────────────────────────────────────────────


async def _record_mismatch(token_repo: Any, token_row: Any) -> None:
    """The DURABLE per-token attempt counter — task 3.6 / D-4.

    Called on the MISMATCH-class refusal paths that carry a known
    token row (``token-item-mismatch`` / ``token-channel-mismatch``
    / ``token-approver-mismatch``). ``record_failed_attempt`` commits
    in its OWN transaction — BEFORE any refusal envelope and BEFORE
    any guarded transition opens (the D-4 BLOCKING rule: a counter
    sharing the guarded transaction would be reset by every refusal's
    rollback and the lockout would silently never fire). At the
    threshold the live token is revoked IN THAT SAME OWN TRANSACTION
    (the lockout ⇒ a fresh mint ⇒ a fresh human turn is required to
    clear it).

    A counter fault NEVER blocks the refusal: the refusal is the
    verdict; the counter is forensics. An UNKNOWN token (no row)
    cannot be counted durably — the K-9 in-RAM hourly budget is the
    counter for that shape (P2-owned, consumed by the envelope
    helper below).
    """
    token_id = getattr(token_row, "token_id", None)
    if not token_id:
        return
    try:
        await asyncio.to_thread(
            token_repo.record_failed_attempt,
            token_id,
            PROMPT_ASSENT_REVOKE_THRESHOLD,
        )
    except Exception:  # noqa: BLE001 — never break the refusal
        logger.warning(
            "prompt_assent: the attempt-counter increment failed for "
            "token %s (the refusal stands)",
            str(token_id)[:8],
            exc_info=True,
        )


def _render_pending_list(rows: list[Any]) -> str:
    """The ``list`` body — CORE pending rows, bounded, NOT agent-filtered."""
    if not rows:
        return "prompt_assent list — 0 pending CORE item(s)"
    lines = [f"prompt_assent list — {len(rows)} pending CORE item(s)"]
    for row in rows:
        lines.append(
            "- version_id={vid} agent={agent} artifact={artifact} "
            "version={version} submitted_at={submitted}".format(
                vid=getattr(row, "version_id", "?"),
                agent=getattr(row, "agent_id", "?"),
                artifact=getattr(row, "artifact", "?"),
                version=getattr(row, "version", "?"),
                submitted=getattr(row, "created_at", "?"),
            )
        )
    return "\n".join(lines)


def _render_mint_response(
    *,
    row: Any,
    rendered: str,
    render_sha: str,
    grouped_token: str,
    expires_at: str,
) -> str:
    """The mint response — item identity + diff + token + expiry + hash.

    The canonical render is carried VERBATIM: the ``render_sha256`` is
    the sha256 of EXACTLY these bytes (J-3/K-7 — shown == hashed).
    """
    return "\n".join([
        "prompt_assent mint — item: "
        f"{getattr(row, 'agent_id', '?')}/{getattr(row, 'version_tag', '') or ''}"
        f"/{getattr(row, 'artifact', '?')} version {getattr(row, 'version', '?')}",
        f"version_id: {getattr(row, 'version_id', '?')}",
        f"token: {grouped_token}",
        f"expires_at: {expires_at}",
        f"render_sha256: {render_sha}",
        "--- BEGIN CANONICAL RENDER ---",
        rendered,
        "--- END CANONICAL RENDER ---",
    ])


# ── The factory ───────────────────────────────────────────────────────────


def create_prompt_assent_tools(
    manager: Any,
    *,
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: str | None = None,
) -> list:
    """Build the ``prompt_assent`` tool for one instance/agent.

    The factory ALWAYS constructs (the kill-switch is a per-call
    refusal, never a construction-absence — D-2). Per-agent visibility
    is scoped by ``_apply_tool_filter`` via the ``prompt-assent``
    PRIVILEGED category + ari's meta.json allow entry; the body carries
    the THIRD, independent agent_id gate.
    """
    caller_agent_id = agent_id or ""
    caller_instance_id = current_instance_id or ""

    class PromptAssentInput(BaseModel):
        """Input schema for ``prompt_assent`` (AR-2's four verbs)."""

        action: Annotated[
            str,
            Field(
                description=(
                    "One of exactly four verbs: list | mint | approve | reject. "
                    "list — the pending CORE prompt versions (read "
                    "only); mint — mint an assent token for one pending item "
                    "(gated on a live user turn); approve / reject — relay "
                    "the Emperor's decision for a minted token. Any other "
                    "verb is refused with invalid-arguments."
                ),
            ),
        ]
        version_id: Annotated[
            str,
            Field(
                default="",
                description=(
                    "The pending prompt-version row (mint / approve / "
                    "reject). Ignored by list."
                ),
            ),
        ] = ""
        token: Annotated[
            str,
            Field(
                default="",
                description=(
                    "The grouped assent token the Emperor received at mint "
                    "time (approve / reject only). It must also appear in "
                    "the human message that triggered this turn."
                ),
            ),
        ] = ""
        user_confirmed: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "The relay agent's explicit confirmation that the human "
                    "confirmed the decision (approve / reject only). The "
                    "three factors are always required."
                ),
            ),
        ] = False
        reject_reason: Annotated[
            str,
            Field(
                default="",
                description=(
                    "The rejection reason (reject only; required, <= 500 "
                    "chars)."
                ),
            ),
        ] = ""
        limit: Annotated[
            int,
            Field(
                default=PROMPT_ASSENT_LIST_LIMIT_DEFAULT,
                description=(
                    "The list bound (list only; default 50, max 200)."
                ),
            ),
        ] = PROMPT_ASSENT_LIST_LIMIT_DEFAULT
        force: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Mint only: explicitly supersede an existing live token "
                    "for this item (M-4b). A re-mint re-shows the diff."
                ),
            ),
        ] = False

    @register_tool_category(PROMPT_ASSENT_CATEGORY_KEY)
    @tool(args_schema=PromptAssentInput)
    async def prompt_assent(
        action: str,
        version_id: str = "",
        token: str = "",
        user_confirmed: bool = False,
        reject_reason: str = "",
        limit: int = PROMPT_ASSENT_LIST_LIMIT_DEFAULT,
        force: bool = False,
    ) -> str:
        """The conversational approval lane: list / mint / approve / reject a pending prompt version. Use tool_help("prompt_assent") for details."""
        try:
            return await _dispatch(
                manager,
                caller_agent_id=caller_agent_id,
                caller_instance_id=caller_instance_id,
                action=action,
                version_id=version_id,
                token=token,
                user_confirmed=user_confirmed,
                reject_reason=reject_reason,
                limit=limit,
                force=force,
            )
        except Exception as exc:  # noqa: BLE001 — tools NEVER raise
            logger.warning(
                "prompt_assent: unexpected failure action=%s: %s",
                action,
                exc,
            )
            _emit_info_line(
                action=action,
                outcome="failed",
                code=PROMPT_ASSENT_INTERNAL_ERROR,
                instance_id=caller_instance_id,
                agent_id=caller_agent_id,
            )
            return _failure(
                PROMPT_ASSENT_INTERNAL_ERROR,
                f"{type(exc).__name__}: {exc}",
            )

    return [prompt_assent]


# ── The dispatch (the gate + the branches) ────────────────────────────────


async def _dispatch(
    manager: Any,
    *,
    caller_agent_id: str,
    caller_instance_id: str,
    action: str,
    version_id: str,
    token: str,
    user_confirmed: bool,
    reject_reason: str,
    limit: int,
    force: bool,
) -> str:
    """The whole tool body — the gate order is FIXED."""
    action = action if isinstance(action, str) else ""

    # ── 0. the ari layer-3 gate (auth-first) ───────────────────────────
    # An unauthorized caller touches NOTHING else: no switch read, no
    # window read, no ledger noise. The envelope carries NO refusal code.
    if caller_agent_id != PROMPT_ASSENT_AUTHORIZED_AGENT:
        _emit_info_line(
            action=action or "-",
            outcome="refused",
            code=None,
            instance_id=caller_instance_id,
            agent_id=caller_agent_id,
        )
        return _not_ari(caller_agent_id)

    # ── 1. the kill-switch (call time, fail-closed) ────────────────────
    if not _resolve_enabled(manager):
        _emit_info_line(
            action=action,
            outcome="refused",
            code="lane-disabled",
            instance_id=caller_instance_id,
            agent_id=caller_agent_id,
        )
        return _refusal(
            "lane-disabled",
            f"the prompt-assent lane is disabled ({KILL_SWITCH_ENV}) — "
            "the tool stays registered; no action is taken",
        )

    # ── 2. the action-shape validation ────────────────────────────────
    if action not in PROMPT_ASSENT_ACTIONS:
        _emit_info_line(
            action=action or "-",
            outcome="refused",
            code="invalid-arguments",
            instance_id=caller_instance_id,
            agent_id=caller_agent_id,
        )
        return _refusal(
            "invalid-arguments",
            f"action must be one of {list(PROMPT_ASSENT_ACTIONS)} — got "
            f"{action!r}",
        )

    if action in PROMPT_ASSENT_MUTATING_ACTIONS and not (
        isinstance(version_id, str) and version_id.strip()
    ):
        _emit_info_line(
            action=action,
            outcome="refused",
            code="invalid-arguments",
            instance_id=caller_instance_id,
            agent_id=caller_agent_id,
        )
        return _refusal(
            "invalid-arguments",
            f"version_id is REQUIRED for action={action!r}",
        )

    if action == "reject" and not (
        isinstance(reject_reason, str) and reject_reason.strip()
    ):
        _emit_info_line(
            action=action,
            outcome="refused",
            code="invalid-arguments",
            instance_id=caller_instance_id,
            agent_id=caller_agent_id,
        )
        return _refusal(
            "invalid-arguments",
            "reject_reason is REQUIRED for action='reject' (a rejection "
            "without a reason is not auditable)",
        )

    if action == "reject" and len(reject_reason or "") > (
        PROMPT_ASSENT_REJECT_REASON_MAX
    ):
        _emit_info_line(
            action=action,
            outcome="refused",
            code="invalid-arguments",
            instance_id=caller_instance_id,
            agent_id=caller_agent_id,
        )
        return _refusal(
            "invalid-arguments",
            f"reject_reason must be <= {PROMPT_ASSENT_REJECT_REASON_MAX} chars",
        )

    if action in PROMPT_ASSENT_TOKEN_ACTIONS and not (
        isinstance(token, str) and token.strip()
    ):
        _emit_info_line(
            action=action,
            outcome="refused",
            code="invalid-arguments",
            instance_id=caller_instance_id,
            agent_id=caller_agent_id,
        )
        return _refusal(
            "invalid-arguments",
            "the token is REQUIRED for approve/reject — it is the grouped "
            "token the Emperor received at mint time",
        )

    # ── the READ surface (side-effect free) ────────────────────────────
    if action == "list":
        return await _list_branch(
            manager,
            caller_agent_id=caller_agent_id,
            caller_instance_id=caller_instance_id,
            limit=limit,
        )

    # ── 3. the FACTOR GATES (mint AND approve/reject) ──────────────────
    gate = await _run_factor_gates(
        manager,
        instance_id=caller_instance_id,
        action=action,
        token=token,
        user_confirmed=bool(user_confirmed),
    )
    if not gate.ok:
        code = gate.failures[0].code
        return _mismatch_aware_refusal(
            manager,
            action=action,
            instance_id=caller_instance_id,
            agent_id=caller_agent_id,
            code=code,
            message="; ".join(f.render() for f in gate.failures),
        )

    # ── 4. the action branch ───────────────────────────────────────────
    # The designation map is resolved LAZILY, ONCE PER CALL (D-2.1/D-2.2):
    # the cache is constructed here but nothing is read until a branch
    # actually asks for it — and a DM context never asks (INVARIANT-DM).
    designations = _DesignationCache()
    if action == "mint":
        return await _mint_branch(
            manager,
            caller_agent_id=caller_agent_id,
            caller_instance_id=caller_instance_id,
            version_id=version_id,
            context=gate.context,
            force=force,
            designations=designations,
        )
    return await _decide_branch(
        manager,
        caller_agent_id=caller_agent_id,
        caller_instance_id=caller_instance_id,
        action=action,
        version_id=version_id,
        token=token,
        context=gate.context,
        window=gate.window,
        reject_reason=reject_reason,
        designations=designations,
    )


async def _list_branch(
    manager: Any,
    *,
    caller_agent_id: str,
    caller_instance_id: str,
    limit: int,
) -> str:
    """The READ surface — the CORE pending rows. Side-effect free.

    NOT agent-filtered: the relay lane must be able to approve any
    agent's row. The body goes to the Emperor's chat, NEVER into the
    daemon log (the INFO line stays content-free).
    """
    pv_repo = getattr(manager, "_prompt_version_repo", None)
    if pv_repo is None:
        _emit_info_line(
            action="list",
            outcome="failed",
            code=PROMPT_ASSENT_INTERNAL_ERROR,
            instance_id=caller_instance_id,
            agent_id=caller_agent_id,
        )
        return _failure(
            PROMPT_ASSENT_INTERNAL_ERROR,
            "the prompt-version repository is not available on the manager",
        )
    try:
        bounded = max(1, min(int(limit), PROMPT_ASSENT_LIST_LIMIT_MAX))
        rows = await asyncio.to_thread(pv_repo.list_pending, bounded)
    except Exception as exc:  # noqa: BLE001
        _emit_info_line(
            action="list",
            outcome="failed",
            code=PROMPT_ASSENT_INTERNAL_ERROR,
            instance_id=caller_instance_id,
            agent_id=caller_agent_id,
        )
        return _failure(PROMPT_ASSENT_INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
    _emit_info_line(
        action="list",
        outcome="ok",
        code=None,
        instance_id=caller_instance_id,
        agent_id=caller_agent_id,
        count=len(rows),
    )
    return _render_pending_list(list(rows))


async def _mint_branch(
    manager: Any,
    *,
    caller_agent_id: str,
    caller_instance_id: str,
    version_id: str,
    context: _AssentContext,
    force: bool,
    designations: "_DesignationCache",
) -> str:
    """The MINT branch — M-4a..M-6.

    Gated (K-2): the factor gates have already proven a live, fresh
    user-origin window; the mint context comes from THAT window's message
    row (never from tool arguments).
    """
    pv_repo = getattr(manager, "_prompt_version_repo", None)
    token_repo = _token_repo(manager)
    if pv_repo is None or token_repo is None:
        _emit_info_line(
            action="mint",
            outcome="failed",
            code=PROMPT_ASSENT_INTERNAL_ERROR,
            instance_id=caller_instance_id,
            agent_id=caller_agent_id,
        )
        return _failure(
            PROMPT_ASSENT_INTERNAL_ERROR,
            "the prompt-assent repositories are not available on the manager",
        )

    def _mint_refusal(code: str, message: str, flags: Optional[dict] = None) -> str:
        _emit_info_line(
            action="mint",
            outcome="refused",
            code=code,
            instance_id=caller_instance_id,
            agent_id=caller_agent_id,
            flags=flags,
        )
        return _refusal(code, message)

    # ── M-4a. the item ─────────────────────────────────────────────────
    try:
        row = await asyncio.to_thread(pv_repo.get_version, version_id)
    except Exception as exc:  # noqa: BLE001
        return _mint_refusal(
            PROMPT_ASSENT_INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"
        )
    if row is None:
        return _mint_refusal(
            "item-not-found", f"no prompt version row for version_id={version_id!r}"
        )
    from ..repositories.prompt_version.repository import ARTIFACT_CLASS_CORE

    if getattr(row, "artifact_class", None) != ARTIFACT_CLASS_CORE:
        return _mint_refusal(
            "item-not-assentable",
            f"the row's class is {getattr(row, 'artifact_class', None)!r} — "
            "only CORE rows ride the approval lane",
        )
    if getattr(row, "state", None) != "pending_approval":
        return _mint_refusal(
            "item-not-pending",
            f"the row's state is {getattr(row, 'state', None)!r} — only a "
            "pending_approval row can be minted",
        )

    # ── M-4b. no live token unless explicitly forced ───────────────────
    try:
        prior_live = await asyncio.to_thread(token_repo.find_live, version_id)
    except Exception as exc:  # noqa: BLE001
        return _mint_refusal(
            PROMPT_ASSENT_INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"
        )
    if prior_live is not None and not force:
        return _mint_refusal(
            "mint-live-token-exists",
            f"a live token already exists for version_id={version_id!r} — a "
            "re-mint must be explicitly forced (and re-shows the diff)",
        )

    # ── M-4c. the PRIVATE (or DESIGNATED) check on the WINDOW context ──
    # The DM short-circuit stays FIRST and it never touches the resolver:
    # a proven 1:1 DM mints without reading the designation map at all
    # (INVARIANT-DM). Every other shape goes through P1's ONE
    # classification owner and is mapped through the mint-side table.
    designated = False
    mint_digest: Optional[str] = None
    if context is None:
        return _mint_refusal(
            "mint-requires-private-channel",
            _MINT_NOT_PRIVATE_DETAIL,
            flags={"private": False},
        )
    if not context.is_private:
        leg = designations.outcome(context.discord_meta, context.user_id)
        if leg is not DesignationResult.PASS:
            code = _MINT_DESIGNATION_CODES[leg]
            if code in PHASE1_5_PLACED_CODES:
                # T2.17 (blocker #6): the mint path bypasses
                # ``_mismatch_aware_refusal`` (the only other site that
                # sets this flag), so the NEW codes' envelopes state
                # ``mismatch=0`` directly — the probe's pass criterion is
                # then producible without routing through
                # ``_record_mismatch``.
                return _mint_refusal(
                    code,
                    _MINT_DESIGNATION_DETAILS[code],
                    flags={"private": False, "mismatch": False},
                )
            # Every PRE-EXISTING code keeps its EXACT v0.29.18 envelope
            # (byte-identical detail text and byte-identical flags dict) —
            # except ``extraction-incomplete``, which gets the designated-
            # branch text above (the same CODE, a truthful detail for a
            # different leg; the DM path never reaches this arm).
            return _mint_refusal(
                code,
                _MINT_DESIGNATION_DETAILS.get(code, _MINT_NOT_PRIVATE_DETAIL),
                flags={"private": False},
            )
        # A designated channel, turned by its pinned approver.
        designated = True
        mint_digest = designations.digest

    # ── the nested-identity extraction (D-5) — fail closed ────────────
    # The mint context must carry the channel AND the human: a token
    # bound to an absent channel would approve from ANY channel.
    missing = [
        name
        for name, value in (
            ("source_id", context.source_id),
            ("channel_id", context.channel_id),
            ("user_id", context.user_id),
        )
        if not (isinstance(value, str) and value.strip())
    ]
    if missing:
        return _mint_refusal(
            "extraction-incomplete",
            "the daemon-derived mint context is incomplete — missing "
            f"{missing} (the nested metadata['discord'] extraction is "
            "fail-closed, D-5); NO token row is written",
        )

    # ── M-5 / M-6. the render, the mint context, the INSERT ───────────
    try:
        effective = await asyncio.to_thread(
            pv_repo.effective_for_render,
            row.agent_id,
            row.version_tag,
            row.artifact,
        )
    except Exception as exc:  # noqa: BLE001
        return _mint_refusal(
            PROMPT_ASSENT_INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"
        )

    from ..services.prompt_version_diff import canonical_render, render_sha256

    render_kwargs = {
        "agent_id": row.agent_id,
        "version_tag": row.version_tag,
        "artifact": row.artifact,
        "version": int(row.version),
        "version_id": row.version_id,
        "pending_content": row.content or "",
        "effective_content": effective.content or "",
        "effective_version_id": effective.version_id,
    }
    rendered = canonical_render(**render_kwargs)
    # The hash is over the EXACT bytes placed in the response below
    # (shown == hashed — J-3/K-7). The ONE shared wrapper
    # (``render_sha256`` — the same rule the D-4 hook re-checks with)
    # re-renders the same deterministic bytes, so hashed == shown ==
    # re-checked-at-approve BY CONSTRUCTION; it is byte-identical to
    # hashing ``rendered`` directly (pinned by test).
    render_sha = render_sha256(**render_kwargs)

    from daemon.tools.upgrade_journal import mint_nonce, nonce_grouped

    raw_token = mint_nonce()
    grouped = nonce_grouped(raw_token)

    try:
        token_row = await asyncio.to_thread(
            token_repo.mint,
            version_id=row.version_id,
            agent_id=row.agent_id,
            version_tag=row.version_tag,
            artifact=row.artifact,
            version=int(row.version),
            token_hash=token_hash(raw_token),
            render_sha256=render_sha,
            mint_source_id=context.source_id,
            mint_channel_id=context.channel_id,
            mint_is_dm=bool(context.is_dm),
            mint_user_id=context.user_id,
            minted_external_user_id=context.external_user_id,
            force=bool(force),
        )
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", None)
        if code == "mint-live-token-exists":
            return _mint_refusal(
                "mint-live-token-exists",
                "a live token already exists for this item (the store "
                "refused the INSERT)",
            )
        _emit_info_line(
            action="mint",
            outcome="failed",
            code=PROMPT_ASSENT_INTERNAL_ERROR,
            instance_id=caller_instance_id,
            agent_id=caller_agent_id,
        )
        return _failure(PROMPT_ASSENT_INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")

    _emit_info_line(
        action="mint",
        outcome="ok",
        code=None,
        instance_id=caller_instance_id,
        agent_id=caller_agent_id,
        count=1,
        flags={
            # P2-AC15: a DESIGNATED-channel mint is NOT a private DM — it is
            # private-EQUIVALENT (the operator's designation + the pinned
            # human). A DM mint is both. The flag set is documented, never
            # silently wrong.
            "private": not designated,
            "private_equivalent": True,
            "forced": bool(force),
            "superseded": prior_live is not None,
        },
        # AC13 / D-15(b): a CHANNEL mint records the map digest; a DM mint
        # passes None (it never read the map — INVARIANT-DM).
        map_digest=mint_digest,
    )
    return _render_mint_response(
        row=row,
        rendered=rendered,
        render_sha=render_sha,
        grouped_token=grouped,
        expires_at=getattr(token_row, "ttl_expires_at", "") or "",
    )


async def _decide_branch(
    manager: Any,
    *,
    caller_agent_id: str,
    caller_instance_id: str,
    action: str,
    version_id: str,
    token: str,
    context: _AssentContext,
    window: Optional[dict[str, Any]],
    reject_reason: str,
    designations: "_DesignationCache",
) -> str:
    """The APPROVE / REJECT branch — the AR-1 equality legs + the service.

    The order (architect §2.2): 4a the item, 4b the state, 4c the token
    row (status → TTL → item binding), 4d the channel match, 4e the
    user match, 4f the private check, 4g the lockout, then the service
    acceptance path (counters / attestation / guarded transition).
    """
    from ..services.prompt_version_service import (
        assent_approver_mismatch_code,
        assent_channel_mismatch_code,
        assent_channel_not_private_code,
    )

    pv_repo = getattr(manager, "_prompt_version_repo", None)
    token_repo = _token_repo(manager)
    if pv_repo is None or token_repo is None:
        _emit_info_line(
            action=action,
            outcome="failed",
            code=PROMPT_ASSENT_INTERNAL_ERROR,
            instance_id=caller_instance_id,
            agent_id=caller_agent_id,
        )
        return _failure(
            PROMPT_ASSENT_INTERNAL_ERROR,
            "the prompt-assent repositories are not available on the manager",
        )

    def _refuse(code: str, message: str) -> str:
        """The refusal envelope — the K-9 budget is consulted FIRST for
        the MISMATCH-class codes (see :func:`_mismatch_aware_refusal`)."""
        return _mismatch_aware_refusal(
            manager,
            action=action,
            instance_id=caller_instance_id,
            agent_id=caller_agent_id,
            code=code,
            message=message,
        )

    # ── 4a. the item ───────────────────────────────────────────────────
    try:
        row = await asyncio.to_thread(pv_repo.get_version, version_id)
    except Exception as exc:  # noqa: BLE001
        return _refuse(PROMPT_ASSENT_INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
    if row is None:
        return _refuse(
            "item-not-found", f"no prompt version row for version_id={version_id!r}"
        )
    from ..repositories.prompt_version.repository import ARTIFACT_CLASS_CORE

    if getattr(row, "artifact_class", None) != ARTIFACT_CLASS_CORE:
        return _refuse(
            "item-not-assentable",
            f"the row's class is {getattr(row, 'artifact_class', None)!r} — "
            "only CORE rows ride the approval lane",
        )

    # ── 4b. the state ──────────────────────────────────────────────────
    state = getattr(row, "state", None)
    if state != "pending_approval":
        return _refuse(
            "already-decided" if state in ("approved", "rejected") else "item-not-pending",
            f"the row's state is {state!r} — only a pending_approval row can "
            "be decided",
        )

    # ── 4c. the token row ──────────────────────────────────────────────
    try:
        token_row = await asyncio.to_thread(
            token_repo.find_by_hash, token_hash(token)
        )
    except Exception as exc:  # noqa: BLE001
        return _refuse(PROMPT_ASSENT_INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
    if token_row is None:
        return _refuse(
            "token-mismatch",
            "the supplied token matches no issued token — relay it verbatim "
            "from the mint response",
        )
    token_status = getattr(token_row, "status", None)
    if token_status == "consumed":
        return _refuse(
            "token-already-used",
            "the token was already consumed — a token is single-use (S-2)",
        )
    if token_status == "superseded":
        return _refuse(
            "token-superseded",
            "the token was superseded by a re-mint — mint again and use the "
            "new token (deliberately distinct from token-already-used)",
        )
    if token_status == "revoked":
        return _refuse(
            "token-locked",
            "the token was revoked by the attempt lockout — a fresh mint "
            "(⇒ a fresh human turn) is required",
        )
    if token_status != "live":
        return _refuse(
            "token-mismatch",
            f"the token's status is {token_status!r} — only a live token can "
            "be decided",
        )

    # ── 4c-ttl. the token's own TTL (task 3.6) ────────────────────────
    # Distinct from the WINDOW clocks (3d/3e): a minted token lives
    # NONCE_TTL_S (60 min); fail-closed on an unparseable stamp
    # (is_expired). NOT a mismatch — an expired token is a lifecycle
    # fact, so it never consumes the K-9 budget nor the durable
    # counter.
    try:
        token_expired = await asyncio.to_thread(token_repo.is_expired, token_row)
    except Exception as exc:  # noqa: BLE001
        return _refuse(PROMPT_ASSENT_INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
    if token_expired:
        return _refuse(
            "token-expired",
            f"the token's TTL expired at "
            f"{getattr(token_row, 'ttl_expires_at', None)!r} — mint again "
            "(a fresh mint ⇒ a fresh human turn)",
        )

    # ── 4c-item. the item binding (with the D-4 counter) ─────────────
    if getattr(token_row, "version_id", None) != version_id:
        await _record_mismatch(token_repo, token_row)
        return _refuse(
            "token-item-mismatch",
            f"the token is bound to version_id="
            f"{getattr(token_row, 'version_id', None)!r}, not {version_id!r}",
        )

    # ── 4d. the channel match (AR-1 leg 1 — the service predicate) ────
    if context is None:
        return _refuse(
            "extraction-incomplete",
            "the approving turn's daemon-derived context is missing — "
            "fail closed (D-5)",
        )
    channel_code = assent_channel_mismatch_code(
        getattr(token_row, "mint_source_id", None),
        getattr(token_row, "mint_channel_id", None),
        context.source_id,
        context.channel_id,
    )
    if channel_code is not None:
        await _record_mismatch(token_repo, token_row)
        return _refuse(
            channel_code,
            "the approving turn's channel does not match the mint's channel "
            "(AR-1 leg 1) — a cross-channel approval is a re-mint",
        )

    # ── 4e. the user match (AR-1 leg 3 / K-8 — the service predicate) ─
    if not context.user_id:
        return _refuse(
            "extraction-incomplete",
            "the approving turn's nested identity "
            "(message_metadata['discord']['user_id']) is missing — fail "
            "closed (D-5)",
        )
    user_code = assent_approver_mismatch_code(
        getattr(token_row, "mint_user_id", None),
        context.user_id,
    )
    if user_code is not None:
        await _record_mismatch(token_repo, token_row)
        return _refuse(
            user_code,
            "the approving human is not the minting human (AR-1 leg 3 / K-8) "
            "— both sides are daemon-derived, neither is tool-supplied",
        )

    # ── 4f. the private (or DESIGNATED) check on the APPROVING context ─
    # THE DM SHORT-CIRCUIT IS PRESERVED: a proven 1:1 DM approves without
    # the designation map ever being resolved (INVARIANT-DM / PR-1). Only a
    # non-DM context reaches the resolver, and only a non-DM context can be
    # designation-eligible.
    #
    # THE 4f SPLIT (T2.5 / refusal-cell-matrix §1.3 — BLOCKING). This site
    # used to call ``_record_mismatch`` UNCONDITIONALLY, before the code's
    # class was examined — so five Phase-1.5 refusals would have REVOKED the
    # Emperor's live token (threshold 5) and burned the K-9 budget. Now:
    #   * the two NEW codes (operator fault / re-designation) refuse BEFORE
    #     the recording call — no counter motion, no revoke, no budget burn
    #     (A-9, A-10 / AC7);
    #   * every pre-existing 4f code keeps ``_record_mismatch`` EXACTLY as
    #     before — including A-12 (a withdrawn designation maps to
    #     ``token-channel-mismatch``, a user-side mismatch, and BURNS);
    #   * the three genuine mismatch sites (4c-item / 4d / 4e) are untouched.
    if context.is_private:
        private_code = None
    else:
        private_code = assent_channel_not_private_code(
            context.discord_meta, designations.get(), context.user_id
        )
    if private_code is not None:
        if private_code in _APPROVE_NON_BURNING_CODES:
            return _refuse(
                private_code,
                _APPROVE_DESIGNATION_DETAILS[private_code],
            )
        await _record_mismatch(token_repo, token_row)
        return _refuse(
            private_code,
            "the approving turn is not a proven 1:1 DM (is_dm AND NOT "
            "is_group_dm; an absent discriminator is not private)",
        )

    # ── 4g. the per-token lockout ──────────────────────────────────────
    attempts = int(getattr(token_row, "attempts", 0) or 0)
    if attempts >= PROMPT_ASSENT_REVOKE_THRESHOLD:
        return _refuse(
            "token-locked",
            f"the token recorded {attempts} mismatches (the lockout bound is "
            f"{PROMPT_ASSENT_REVOKE_THRESHOLD}) — a fresh mint (⇒ a fresh "
            "human turn) is required",
        )

    # ── the acceptance path (Phase-3 owned — the call site is wired) ──
    try:
        result = await _accept_via_service(
            manager,
            action=action,
            version_id=version_id,
            context=context,
            token_row=token_row,
            window=window or {},
            reject_reason=reject_reason,
        )
    except _AcceptancePathUnavailable as exc:
        _emit_info_line(
            action=action,
            outcome="failed",
            code=PROMPT_ASSENT_INTERNAL_ERROR,
            instance_id=caller_instance_id,
            agent_id=caller_agent_id,
        )
        return _failure(PROMPT_ASSENT_INTERNAL_ERROR, str(exc))
    except Exception as exc:  # noqa: BLE001 — the service's refusals
        code = getattr(exc, "code", None)
        if isinstance(code, str) and code in REFUSAL_TAXONOMY:
            return _mismatch_aware_refusal(
                manager,
                action=action,
                instance_id=caller_instance_id,
                agent_id=caller_agent_id,
                code=code,
                message=str(exc),
            )
        _emit_info_line(
            action=action,
            outcome="failed",
            code=PROMPT_ASSENT_INTERNAL_ERROR,
            instance_id=caller_instance_id,
            agent_id=caller_agent_id,
        )
        return _failure(PROMPT_ASSENT_INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")

    _emit_info_line(
        action=action,
        outcome="ok",
        code=None,
        instance_id=caller_instance_id,
        agent_id=caller_agent_id,
        count=1,
    )
    return str(result)


def _mismatch_aware_refusal(
    manager: Any,
    *,
    action: str,
    instance_id: str,
    agent_id: str,
    code: str,
    message: str,
) -> str:
    """A refusal envelope — with the K-9 budget consulted FIRST.

    A MISMATCH-class refusal reads the in-RAM hourly budget: when the
    budget is exhausted the refusal becomes ``mismatch-budget-exceeded``
    (the mismatch never counts twice); otherwise the counter is
    incremented and the mismatch is reported. A NON-mismatch refusal
    never touches the budget (only mismatches count — a successful
    approval does NOT reset it).
    """
    if code in MISMATCH_CODES:
        budget = _resolve_hourly_budget(manager)
        bucket = _hour_bucket()
        count = _budget_count(instance_id, bucket)
        if count >= budget:
            _emit_info_line(
                action=action,
                outcome="refused",
                code="mismatch-budget-exceeded",
                instance_id=instance_id,
                agent_id=agent_id,
                count=count,
            )
            return _refusal(
                "mismatch-budget-exceeded",
                f"this relay instance recorded {count} token mismatches in "
                f"the current hour — the bound is {budget} (K-9/D-3b; the "
                "counter is in-RAM and clears with the hour)",
            )
        _budget_increment(instance_id, bucket)
    _emit_info_line(
        action=action,
        outcome="refused",
        code=code,
        instance_id=instance_id,
        agent_id=agent_id,
        flags={"mismatch": code in MISMATCH_CODES},
    )
    return _refusal(code, message)


def _token_repo(manager: Any) -> Any:
    """The assent token repository — built on the manager's engine.

    The token store is P1-owned; Phase 2 constructs the repository per
    call (the manager holds the engine, not the repository).
    """
    engine = getattr(manager, "_engine", None)
    if engine is None:
        return None
    from ..repositories.prompt_assent.repository import (
        PromptAssentTokenRepository,
    )

    return PromptAssentTokenRepository(engine)
