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
constant (:data:`PROMPT_ASSENT_DEFAULT_ENABLED`) — Phase 4's
``PromptAssentConfig`` class consumes the SAME constant, so the two can
never disagree. An indeterminate/invalid value fails CLOSED. The tool
STAYS REGISTERED when the switch is off (refusal ``lane-disabled``,
never construction-absence) and the ``mint`` verb is refused too.

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
from pydantic import BaseModel, Field

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

#: THE ONE fail-closed OFF default (D-2). Consumed by BOTH this phase's
#: defensive resolver AND Phase 4's ``PromptAssentConfig`` class — the
#: two can never disagree. The architect OVERRULED the earlier split
#: (P2 "off" / P4 "on"): under EITHER default that split is a defect.
PROMPT_ASSENT_DEFAULT_ENABLED: bool = False

#: THE ONE default for the hourly mismatch budget (K-9 / D-3b). Phase 4's
#: config class supplies ``PromptAssentConfig.hourly_mismatch_budget``;
#: until then this constant IS the default (never a second literal).
PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET: int = 10

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
    """The K-9 budget — the ONE default constant, with the Phase-4 seam.

    Phase 4 lands ``PromptAssentConfig.hourly_mismatch_budget`` (default
    10) on the daemon config; when that class exists this resolver
    reads it, and it falls back to
    :data:`PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET` — the SAME
    constant (never a second literal).
    """
    cfg = getattr(getattr(manager, "config", None), "prompt_assent", None)
    value = getattr(cfg, "hourly_mismatch_budget", None)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET


# ── The kill-switch (D-2) ─────────────────────────────────────────────────


def _resolve_enabled() -> bool:
    """Kill-switch resolver — default OFF, fail-closed, read PER CALL.

    D-2: the switch is read at call time; the tool STAYS REGISTERED when
    it is off (refusal-at-call, never construction-absence); the
    ``mint`` verb is refused too; an INDETERMINATE value fails CLOSED
    (an unreadable knob must never arm a privileged lane).
    """
    raw = os.environ.get(KILL_SWITCH_ENV)
    if raw is None:
        return PROMPT_ASSENT_DEFAULT_ENABLED
    value = str(raw).strip().lower()
    if value in _ON_SPELLINGS:
        return True
    if value in _OFF_SPELLINGS:
        return False
    logger.warning(
        "prompt_assent: invalid %s=%r (expected a boolean spelling); "
        "failing CLOSED (the lane stays disabled)",
        KILL_SWITCH_ENV,
        raw,
    )
    return PROMPT_ASSENT_DEFAULT_ENABLED


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
) -> None:
    """The ONE frozen INFO line — counts and flags ONLY.

    Content-free by construction (the "logs content-free v1" doctrine):
    never persona, never diff content, never the raw or grouped token.
    Fired on EVERY terminal path (success and refusal alike).
    """
    flag_text = ",".join(
        f"{k}={'1' if v else '0'}" for k, v in sorted((flags or {}).items())
    )
    logger.info(
        "prompt_assent outcome=%s action=%s code=%s agent=%s instance=%s "
        "count=%d flags=%s",
        outcome,
        action,
        code or "-",
        agent_id or "-",
        (instance_id or "-")[:8],
        int(count),
        flag_text or "-",
    )


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
        "is_group_dm",
        "user_id",
        "external_user_id",
        "guild_id",
        "channel_type",
        "platform_message_id",
        "message_id",
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
       ONLY when the row proves a DM, which is the only shape the
       private check admits.
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
        is_group_dm=discord_meta.get("is_group_dm"),
        user_id=user_id,
        external_user_id=_derive_external_user_id(
            manager, row, discord_meta, user_id, is_dm
        ),
        guild_id=discord_meta.get("guild_id"),
        channel_type=discord_meta.get("channel_type"),
        platform_message_id=discord_meta.get("message_id"),
        message_id=window.get("message_id"),
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


# ── The accept seam (Phase-3 owned — the CALL SITE is wired here) ────────


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

    Raises :class:`_AcceptancePathUnavailable` when the service entrypoint
    is absent (Phase 2's state) — the tool then returns the house
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


def _build_attestation(
    manager: Any,
    *,
    version_id: str,
    context: _AssentContext,
    token_row: Any,
    window: dict[str, Any],
) -> Any:
    """The attestation assembly — PHASE-3 OWNED (O-E).

    Phase 2 wires the CALL SITE only; the attestation is minted by the
    assent service (Phase 3) — never from tool arguments (the
    anti-forgery analogue of the repo's R3).
    """
    raise _AcceptancePathUnavailable(
        "the assent attestation assembly is Phase-3 owned"
    )


# ── The action branches ───────────────────────────────────────────────────


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
    if not _resolve_enabled():
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
    if action == "mint":
        return await _mint_branch(
            manager,
            caller_agent_id=caller_agent_id,
            caller_instance_id=caller_instance_id,
            version_id=version_id,
            context=gate.context,
            force=force,
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

    # ── M-4c. the PRIVATE check on the WINDOW-derived context ─────────
    if context is None or not context.is_private:
        return _mint_refusal(
            "mint-requires-private-channel",
            "the mint context is not a proven 1:1 DM (is_dm AND NOT "
            "is_group_dm; an absent discriminator is not private) — the "
            "mint is refused and NO token row is written",
            flags={"private": False},
        )

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

    from ..services.prompt_version_diff import canonical_render

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
    # (shown == hashed — J-3/K-7). It is byte-identical to
    # ``mint_render_sha256(**render_kwargs)`` (the helper re-renders the
    # same deterministic bytes) — pinned by test.
    render_sha = hashlib.sha256(rendered.encode("utf-8")).hexdigest()

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
            "private": True,
            "forced": bool(force),
            "superseded": prior_live is not None,
        },
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
) -> str:
    """The APPROVE / REJECT branch — the AR-1 equality legs + the service.

    The order (architect §2.2): 4a the item, 4b the state, 4c the token
    row, 4d the channel match, 4e the user match, 4f the private check,
    4g the lockout, 4h the hourly budget.
    """
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
    if getattr(token_row, "version_id", None) != version_id:
        return _refuse(
            "token-item-mismatch",
            f"the token is bound to version_id="
            f"{getattr(token_row, 'version_id', None)!r}, not {version_id!r}",
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

    # ── 4d. the channel match (AR-1) ──────────────────────────────────
    if context is None:
        return _refuse(
            "extraction-incomplete",
            "the approving turn's daemon-derived context is missing — "
            "fail closed (D-5)",
        )
    mint_channel = getattr(token_row, "mint_channel_id", None)
    mint_source = getattr(token_row, "mint_source_id", None)
    channel_mismatch = (
        not context.channel_id
        or not mint_channel
        or str(mint_channel) != str(context.channel_id)
    )
    source_mismatch = (
        mint_source is not None
        and str(mint_source) != str(context.source_id or "")
    )
    if channel_mismatch or source_mismatch:
        return _refuse(
            "token-channel-mismatch",
            "the approving turn's channel does not match the mint's channel "
            "(AR-1 leg 1) — a cross-channel approval is a re-mint",
        )

    # ── 4e. the user match (AR-1 leg 3 / K-8) ─────────────────────────
    if not context.user_id:
        return _refuse(
            "extraction-incomplete",
            "the approving turn's nested identity "
            "(message_metadata['discord']['user_id']) is missing — fail "
            "closed (D-5)",
        )
    mint_user = getattr(token_row, "mint_user_id", None)
    if not mint_user or str(mint_user) != str(context.user_id):
        return _refuse(
            "token-approver-mismatch",
            "the approving human is not the minting human (AR-1 leg 3 / K-8) "
            "— both sides are daemon-derived, neither is tool-supplied",
        )

    # ── 4f. the private check on the APPROVING context ────────────────
    if not context.is_private:
        return _refuse(
            "token-channel-mismatch",
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
