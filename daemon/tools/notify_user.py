"""Agent-facing notify tool — the ``notify-user`` category (1 tool).

The Ari voice (commission 2026-10-24: "with alarm you should have a small
tool, Ari. If you see the next message need user/me -> you can invoke
message to discord, if not do in silence."). ONE deliberate, opt-in
message from a non-user-originated turn (self-alarm wake, scheduled
fire) to the SESSION'S BOUND REGISTERED CHAT SOURCE. The signature takes
the MESSAGE, never a destination — the destination is resolved
SERVER-SIDE from the session's own ``original_source`` binding, so an
arbitrary destination is structurally impossible.

House rules honored here (ADR-005):

* Tools NEVER raise — every failure returns a typed envelope string.
* An UNEXPECTED exception is logged SERVER-SIDE and answered with a
  generic envelope (the PB-F1 leak class: ``str(exc)`` / ``traceback`` /
  ``repr(exc)`` never reach the payload).

Access posture — THREE layers, ALL required
-------------------------------------------
1. The ``notify-user`` category is PRIVILEGED (entry #16 of
   ``PRIVILEGED_TOOL_CATEGORIES``), so it is opt-in-only and never joins
   the default-grant universe (the empty-allow inherit universe — e.g.
   ``watcher`` — must NOT be able to message the user's chat).
2. ``agents/ari/meta.json`` carries the explicit ``"notify-user"`` allow
   entry — and ONLY ari. The category key is EXACTLY ``notify-user``
   (N7): never ``notify``.
3. The tool body rejects any caller whose ``agent_id != "ari"`` — a
   LITERAL tool name in an allow list bypasses the category gate, so
   the category alone cannot spell "ari only".

The board is the ALWAYS surface; notify is the MAY voice
--------------------------------------------------------
Nothing auto-sends. The board remains the guaranteed user-visible
surface for alarm/scheduled work; ``notify_user`` is the DELIBERATE
opt-in for the minority of turns whose content needs the user. Silence
is the default. A session that was never contacted via chat has NO legal
target (its ``original_source`` is absent or colon-less) — the tool
refuses and the board stays the fallback surface.

Steering clause (N11/N12 — double-send semantics)
-------------------------------------------------
If THIS wake shape already delivers the turn's final response to chat
(``internal_report``-family wakes do, when the session carries a chat
binding), your final response will ALSO arrive — prefer silence and let
the final response carry the message. On ``scheduler``-source wakes
(the self-alarm case) there is NO auto final delivery — ``notify_user``
is the only channel. Either way the tool sends at most ONE message per
~minute per instance (the interval proxy below), so it cannot be a
bulk-send primitive.

Never-block + the 10s bound (W4 — ONE bound everywhere)
-------------------------------------------------------
The whole pipeline (binding resolution + ledger gates + adapter send +
the single transient retry) runs under ONE ``asyncio.wait_for(...,
timeout)`` — default 10.0s. Every path returns within the bound; the
retry backoff is computed from the REMAINING budget. On timeout the
delivery status is UNKNOWN (the POST may have landed) — never re-send
blindly. DISCLOSURE (fold-able 5): this 10s tool bound PRE-EMPTS
Discord's own 15s REST pre-flight window — a slow Discord path surfaces
here as ``notify_dispatch_timeout`` (delivery UNKNOWN) before Discord's
own timeout could report definitively. The bound is deliberately tight
(a tool must never hold the turn); it is NOT "compatible" with the 15s
window — it is stricter, and the unknown-tail is the honest cost.

Rate gates (D5 as corrected by C3, serialized by B2)
----------------------------------------------------
* N = 1 per turn, enforced as a **>=60s minimum interval per calling
  instance** (strict turn identity is not tool-visible; turns are
  serialized per instance, so the interval is the enforceable proxy).
  Baseline class (pinned, fold-able 4): the instance's last
  ACCEPTED-class ledger row — refusals and ``rate_limited``-flipped
  attempts never extend the cooldown.
* M = 6 per rolling hour **per RESOLVED DESTINATION**
  (``target_hash = sha256(source_id + ":" + external_user_id)`` — W1,
  source-qualified), counted from the ``notify_audit`` ledger:
  commit-before-count (C3) under a per-destination-key asyncio.Lock
  held across the ENTIRE gate decision (B2). ONLY rows in the ACCEPTED
  CLASS consume budget (N3) — ``accepted`` (still standing) plus the
  settled terminal forms ``dispatched`` / ``delivery_failed`` /
  ``delivery_unknown`` (a row that DID spend a slot stays in the class;
  the authoritative predicate is ``ACCEPTED_CLASS_OUTCOMES`` in
  ``daemon/repositories/notify_audit/models.py``). ``refused`` and
  ``rate_limited`` rows NEVER do. Every refusal class is ledgered and
  NEVER consumes budget; crash-orphaned accepted rows count
  conservatively until the N4 sweep re-marks them ``delivery_unknown``.
* The ledger is the budget source, so the send path is FAIL-CLOSED
  (N2): a ledger-write failure yields the typed refusal
  ``notify_audit_unavailable`` — no ledger row, no send.

Audit (D8): every attempt writes a ``notify_audit`` row; the body is
stored as SHA-256 + length, NEVER cleartext.

Size cap (D6): >2000 chars is rejected BEFORE any resolution or
dispatch (one notify = one chat message). Empty bodies refuse under the
same code (the vocabulary is frozen at 12 codes).

Kill-switch (N9): ``ENSEMBLE_NOTIFY_USER_ENABLED=false`` (or
``notify_user.enabled: false`` in config.yaml) disables the tool with
the typed refusal ``notify_disabled``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import re
import time
import weakref
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ..repositories.notify_audit.models import (
    OUTCOME_ACCEPTED,
    OUTCOME_DELIVERY_FAILED,
    OUTCOME_DELIVERY_UNKNOWN,
    OUTCOME_DISPATCHED,
    OUTCOME_RATE_LIMITED,
)
from ..repositories.notify_audit.repository import NotifyAuditRepository
from ..sources.base import OutgoingMessage, SourceStatus
from .upgrade_journal import USER_ORIGIN_CHAT_SOURCE_TYPES
from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Notify-User"

CATEGORY_DOC = """\
Send ONE deliberate message from THIS session to the user's OWN chat
(the session's registered chat source) — the May voice.

Tool in this category:
- ``notify_user`` — send one opt-in chat message to the bound user

What the tool IS: the small, deliberate voice for non-user-originated
turns (self-alarm wakes, scheduled fires). When the content genuinely
needs the user, invoke it; when it does not, stay silent — the board is
the ALWAYS surface and this is the MAY voice. The destination is the
SESSION'S OWN registered chat source — there is no destination
parameter and arbitrary destinations are structurally impossible.

What the tool is NOT: a bulk channel and not an automatic one. At most
one message per ~minute per instance and 6 per hour per destination;
every attempt is audited (body stored as hash + length, never
cleartext). Nothing auto-sends: you choose, per turn.
"""

#: The category key — EXACTLY ``notify-user`` (N7; research-b's "notify"
#: is wrong and must never be copied anywhere).
NOTIFY_CATEGORY_KEY = "notify-user"

#: The tool name (snake_case — the self_alarm/board_link convention).
NOTIFY_USER_TOOL_NAME = "notify_user"

#: The only agent allowed to notify (the runtime gate — layer 3 of the
#: three-layer posture).
NOTIFY_AUTHORIZED_AGENT = "ari"

# ── D9: the typed error vocabulary — 12 codes, frozen ────────────────
NOTIFY_CODE_NO_BOUND_CHAT_SOURCE = "notify_no_bound_chat_source"
NOTIFY_CODE_SOURCE_NOT_CHAT_TYPE = "notify_source_not_chat_type"
NOTIFY_CODE_SOURCE_NOT_REGISTERED = "notify_source_not_registered"
NOTIFY_CODE_SOURCE_DISABLED = "notify_source_disabled"
NOTIFY_CODE_SOURCE_CIRCUIT_OPEN = "notify_source_circuit_open"
NOTIFY_CODE_RATE_LIMITED = "notify_rate_limited"
NOTIFY_CODE_SIZE_EXCEEDED = "notify_size_exceeded"
NOTIFY_CODE_DISPATCH_TIMEOUT = "notify_dispatch_timeout"
NOTIFY_CODE_DISPATCH_FAILED = "notify_dispatch_failed"
NOTIFY_CODE_INTERNAL_ERROR = "notify_internal_error"
#: Fold-2 N2 — the 11th code: ledger-write failure on the send path is
#: FAIL-CLOSED (no ledger row, no send).
NOTIFY_CODE_AUDIT_UNAVAILABLE = "notify_audit_unavailable"
#: Fold-2 N9 — the 12th code: the kill-switch refusal.
NOTIFY_CODE_DISABLED = "notify_disabled"

#: The frozen vocabulary — validated loudly at import (the secrets
#: precedent: a mistyped code must fail loud, never diff).
NOTIFY_CODES: frozenset[str] = frozenset(
    {
        NOTIFY_CODE_NO_BOUND_CHAT_SOURCE,
        NOTIFY_CODE_SOURCE_NOT_CHAT_TYPE,
        NOTIFY_CODE_SOURCE_NOT_REGISTERED,
        NOTIFY_CODE_SOURCE_DISABLED,
        NOTIFY_CODE_SOURCE_CIRCUIT_OPEN,
        NOTIFY_CODE_RATE_LIMITED,
        NOTIFY_CODE_SIZE_EXCEEDED,
        NOTIFY_CODE_DISPATCH_TIMEOUT,
        NOTIFY_CODE_DISPATCH_FAILED,
        NOTIFY_CODE_INTERNAL_ERROR,
        NOTIFY_CODE_AUDIT_UNAVAILABLE,
        NOTIFY_CODE_DISABLED,
    }
)

for _code in (
    NOTIFY_CODE_NO_BOUND_CHAT_SOURCE,
    NOTIFY_CODE_SOURCE_NOT_CHAT_TYPE,
    NOTIFY_CODE_SOURCE_NOT_REGISTERED,
    NOTIFY_CODE_SOURCE_DISABLED,
    NOTIFY_CODE_SOURCE_CIRCUIT_OPEN,
    NOTIFY_CODE_RATE_LIMITED,
    NOTIFY_CODE_SIZE_EXCEEDED,
    NOTIFY_CODE_DISPATCH_TIMEOUT,
    NOTIFY_CODE_DISPATCH_FAILED,
    NOTIFY_CODE_INTERNAL_ERROR,
    NOTIFY_CODE_AUDIT_UNAVAILABLE,
    NOTIFY_CODE_DISABLED,
):
    if _code not in NOTIFY_CODES:  # pragma: no cover — fails loud at import
        raise RuntimeError(
            f"notify_user: error code { _code!r } missing from NOTIFY_CODES"
        )

#: D6 — the size cap. The leader adjudication pinned 2000 (the
#: Discord-constant reuse) over the ~1900 tighter variant; the
#: adapter's post-format split residual is disclosed, not hidden.
NOTIFY_SIZE_LIMIT = 2000

#: W4 — the ONE never-block bound (default; NotifyUserConfig overrides).
NOTIFY_SEND_TIMEOUT_SECONDS = 10.0

#: D10 — the single transient retry's backoff window (seconds).
NOTIFY_RETRY_BACKOFF_MIN_S = 1.0
NOTIFY_RETRY_BACKOFF_MAX_S = 2.0

#: The source_id grammar — mirrored from the dispatcher's parse
#: (``source.split(":", 1)`` + ``^[a-zA-Z0-9_-]{1,64}$``) so the tool's
#: acceptance and the dispatcher's routing can never disagree.
_SOURCE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_EXTERNAL_USER_ID_MAX = 256

#: Values that are structurally unbound even when colon-less parsing
#: would succeed (the reserved internal sources — never chat targets).
_UNBOUND_SOURCE_IDS: frozenset[str] = frozenset({"api", "scheduler"})

#: N4 — the orphan-sweep threshold FLOOR (seconds): accepted rows
#: older than the sweep threshold are crash orphans. The EFFECTIVE
#: threshold is DERIVED at the point of use as
#: ``max(this floor, 12 * timeout_seconds)`` — see
#: ``_orphan_sweep_seconds`` — so the "a live dispatch can never be
#: swept" invariant stays STRUCTURAL when the (tunable) dispatch bound
#: is raised above its 10s default (120s == 12x the default bound).
NOTIFY_ORPHAN_SWEEP_SECONDS = 120

#: The safety multiplier applied to the dispatch bound when deriving the
#: orphan-sweep threshold (12x the whole pipeline bound — a write-ahead
#: row can only live as long as the ONE ``asyncio.wait_for`` bound).
NOTIFY_ORPHAN_SWEEP_MULTIPLIER = 12

#: B2 — the per-destination-key lock table. Key = resolved target_hash.
#: The daemon is single-process (daemon/__main__.py) — one event loop,
#: one table. The table is kept PER EVENT LOOP (a WeakKeyDictionary
#: keyed by the running loop): an ``asyncio.Lock`` binds itself to the
#: first loop that awaits it, and awaiting it from a SECOND loop
#: raises "is bound to a different event loop". The daemon has one
#: loop, but a tool body may be driven from a worker-thread loop in
#: some deployments, and a bound-to-another-loop lock would turn a
#: legal notify into a typed internal error. Per-loop tables make the
#: primitive loop-correct by construction.
_LOOP_LOCK_TABLES: "weakref.WeakKeyDictionary[Any, tuple[dict[str, asyncio.Lock], asyncio.Lock]]" = (
    weakref.WeakKeyDictionary()
)


def _lock_table() -> tuple[dict[str, asyncio.Lock], asyncio.Lock]:
    """The (locks, guard) pair for the RUNNING loop.

    The creation is safe without an outer lock: between the lookup and
    the insert there is no await, so two coroutines on one loop cannot
    interleave here (the event loop is single-threaded).
    """
    loop = asyncio.get_running_loop()
    entry = _LOOP_LOCK_TABLES.get(loop)
    if entry is None:
        entry = ({}, asyncio.Lock())
        _LOOP_LOCK_TABLES[loop] = entry
    return entry


async def _destination_lock(target_hash: str) -> asyncio.Lock:
    """The B2 primitive — the per-destination-key asyncio.Lock."""
    table, guard = _lock_table()
    async with guard:
        lock = table.get(target_hash)
        if lock is None:
            lock = asyncio.Lock()
            table[target_hash] = lock
        return lock


def _refusal(code: str, message: str) -> str:
    """The house REFUSED envelope (pre-flight refusals)."""
    return f"Error: {NOTIFY_USER_TOOL_NAME} REFUSED — reason={code}: {message}"


def _failure(code: str, message: str) -> str:
    """The house FAILED envelope (dispatch-phase failures)."""
    return f"Error: {NOTIFY_USER_TOOL_NAME} FAILED — reason={code}: {message}"


def _error(msg: str) -> str:
    """House error envelope — tools never raise."""
    return f"ERROR: {msg}"


def _resolve_bounds(config: Any) -> dict[str, Any]:
    """Resolve the NotifyUserConfig knobs: explicit env > config object
    > module defaults. Read at CALL time so tests and operators can
    flip knobs without a restart of the tool module."""
    cfg = getattr(config, "notify_user", None)

    def _pick(env_key: str, attr: str, default: Any) -> Any:
        raw = os.environ.get(env_key)
        if raw is not None and raw != "":
            if isinstance(default, bool):
                return raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}
            if isinstance(default, float):
                try:
                    return float(raw)
                except ValueError:
                    return default
            try:
                return int(raw)
            except ValueError:
                return default
        if cfg is not None:
            return getattr(cfg, attr, default)
        return default

    return {
        "enabled": _pick("ENSEMBLE_NOTIFY_USER_ENABLED", "enabled", True),
        "max_per_hour": int(
            _pick("ENSEMBLE_NOTIFY_USER_MAX_PER_HOUR", "max_per_hour", 6)
        ),
        "min_interval_seconds": int(
            _pick(
                "ENSEMBLE_NOTIFY_USER_MIN_INTERVAL_SECONDS",
                "min_interval_seconds",
                60,
            )
        ),
        "timeout_seconds": float(
            _pick(
                "ENSEMBLE_NOTIFY_USER_TIMEOUT_SECONDS",
                "timeout_seconds",
                NOTIFY_SEND_TIMEOUT_SECONDS,
            )
        ),
        "max_chars": int(
            _pick("ENSEMBLE_NOTIFY_USER_MAX_CHARS", "max_chars", NOTIFY_SIZE_LIMIT)
        ),
    }


def _orphan_sweep_seconds(bounds: dict[str, Any]) -> int:
    """The N4 orphan-sweep threshold — DERIVED from the dispatch bound.

    The invariant is "a live dispatch can never be swept": the
    write-ahead row lives at most ``timeout_seconds`` (the whole
    pipeline — resolution + gates + send + the single retry — runs
    under ONE ``asyncio.wait_for`` bound), so the sweep threshold must
    exceed it. The threshold is therefore
    ``max(NOTIFY_ORPHAN_SWEEP_SECONDS, NOTIFY_ORPHAN_SWEEP_MULTIPLIER *
    timeout_seconds)`` — the 12x multiplier with the 120s floor (120s
    == 12x the DEFAULT 10s bound). Deriving it HERE keeps the
    coupling STRUCTURAL: raising the (tunable) ``timeout_seconds`` knob
    raises the sweep threshold with it, instead of silently making the
    sweep able to catch a live dispatch.
    """
    timeout = float(
        bounds.get("timeout_seconds", NOTIFY_SEND_TIMEOUT_SECONDS)
    )
    derived = NOTIFY_ORPHAN_SWEEP_MULTIPLIER * timeout
    return max(
        NOTIFY_ORPHAN_SWEEP_SECONDS,
        int(math.ceil(derived)),
    )


def _audit_repo_for(manager: Any) -> NotifyAuditRepository | None:
    """The notify_audit ledger for this manager's engine (defensive)."""
    engine = getattr(manager, "engine", None)
    if engine is None:
        return None
    return NotifyAuditRepository(engine)


def _target_hash(source_id: str, external_user_id: str) -> str:
    """W1 — the source-qualified destination hash. The raw destination
    is NEVER stored."""
    return hashlib.sha256(
        f"{source_id}:{external_user_id}".encode("utf-8")
    ).hexdigest()


#: Public alias (tests / ops tooling recompute the W1 hash).
NOTIFY_TARGET_HASH = _target_hash


def _body_digest(message: str) -> tuple[int, str]:
    """The D8 body record — length + sha256, never cleartext."""
    return len(message), hashlib.sha256(message.encode("utf-8")).hexdigest()


def create_notify_user_tools(
    manager: Any,
    *,
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: str | None = None,
) -> list:
    """Build the notify tool for one instance/agent.

    Args:
        manager: The ``InstanceManager`` — the tool resolves the session
            binding through ``_instance_repository``, the registered
            chat source through ``_source_repository`` +
            ``source_registry``, the audit ledger through ``engine``,
            and the knobs through ``config.notify_user``. All five are
            read defensively (a test double without them yields typed
            refusals, never raises).
        current_instance_id: Caller's instance id — the resolution
            anchor (D1) and the interval-gate key.
        agent_id: Caller's agent id — the layer-3 gate reads it.
        agent_tag: Caller's version tag (signature parity; unused).

    Returns:
        A list holding the single ``@tool``-decorated callable.
    """
    caller_agent_id = agent_id or ""
    caller_instance_id = current_instance_id or ""

    class NotifyUserInput(BaseModel):
        """Input schema for notify_user."""

        message: Annotated[
            str,
            Field(
                description=(
                    "The message text, 1-2000 chars (one notify = one "
                    "chat message; over-length is refused BEFORE any "
                    "resolution or dispatch). Plain text — no "
                    "attachments, no markdown tables."
                )
            ),
        ]

    @register_tool_category(NOTIFY_CATEGORY_KEY)
    @tool(args_schema=NotifyUserInput)
    async def notify_user(
        message: Annotated[
            str,
            Field(
                description=(
                    "The message text, 1-2000 chars (one notify = one "
                    "chat message; over-length is refused BEFORE any "
                    "resolution or dispatch). Plain text — no "
                    "attachments, no markdown tables."
                )
            ),
        ],
    ) -> dict | str:
        """Send ONE message to this session's registered chat source. Use tool_help("notify_user") for details."""
        try:
            return await _notify_user_impl(
                manager=manager,
                audit_repo=_audit_repo_for(manager),
                current_instance_id=caller_instance_id,
                agent_id=caller_agent_id,
                message=message,
            )
        except Exception as exc:  # noqa: BLE001 — house pattern: error string, never raise
            logger.exception("notify_user failed")
            # D9 edge 13 — the typed internal-error envelope; the
            # exception detail never reaches the payload (PB-F1 class).
            return _failure(
                NOTIFY_CODE_INTERNAL_ERROR,
                "an unexpected error occurred (logged server-side); "
                "nothing was sent and no delivery is in flight. Retry "
                "later or use the board.",
            )

    notify_user._full_doc_ = _build_full_doc()

    return [notify_user]


def _build_full_doc() -> str:
    """The tool's full documentation (tool_help surface).

    Carries the N11/N12 steering clause and the >=60s interval-proxy
    disclosure in the SAME block (fold-2 requirement), plus the
    fold-able 5 disclosure that the 10s bound pre-empts Discord's own
    15s REST pre-flight.
    """
    return (
        "Send ONE deliberate message to YOUR session's registered chat "
        "source (Discord/Slack/Telegram — whichever chat the user reached "
        "this session through). There is NO destination parameter: the "
        "destination is resolved server-side from this session's "
        "original_source binding, so you cannot notify anyone except the "
        "user who owns this session.\n\n"
        "When to use it (the May voice)\n"
        "-------------------------------\n"
        "You are woken by a self-alarm, a schedule or another "
        "non-user-originated turn. BY DEFAULT you act in silence and "
        "leave evidence on the board. Invoke notify_user ONLY when the "
        "content genuinely needs the user right now (a blocker, a "
        "decision only they can make, a completed thing they asked to be "
        "told about). If the wake shape already delivers your final "
        "response to chat (internal-report-family wakes do, when this "
        "session carries a chat binding), PREFER SILENCE — your final "
        "response will arrive anyway and notify_user would double the "
        "ping. On scheduler-source wakes (the self-alarm case) there is "
        "NO automatic final delivery — notify_user is the only channel.\n\n"
        "Rate limits (enforced against the audit ledger)\n"
        "------------------------------------------------\n"
        "At most ONE message per 60 seconds per session (the enforceable "
        "proxy for one-per-turn — strict turn identity is not visible to "
        "tools), and at most 6 per rolling hour PER DESTINATION (shared "
        "across all ari sessions notifying the same user channel). A "
        "refusal does not extend the interval; only an attempt that "
        "actually dispatched counts. A rate-limit refusal states when "
        "the next send becomes legal.\n\n"
        "Timeout and delivery honesty\n"
        "-----------------------------\n"
        "The whole pipeline (resolution, gates, send, one transient "
        "retry) is bounded by ONE 10-second budget. On timeout the tool "
        "returns reason=notify_dispatch_timeout with delivery status "
        "UNKNOWN — the message may or may not have arrived; do not "
        "re-send blindly. (Disclosure: this 10s bound pre-empts "
        "Discord's own 15s REST pre-flight — it is stricter, not "
        "compatible.) A definite failure after the single retry returns "
        "reason=notify_dispatch_failed.\n\n"
        "No legal target is normal\n"
        "--------------------------\n"
        "Sessions never contacted via chat (API-created, scheduler-only) "
        "have no bound chat source — the tool refuses with "
        "notify_no_bound_chat_source. That is correct behavior, not a "
        "bug: the board remains the fallback surface for those "
        "sessions.\n\n"
        "Audit\n"
        "-----\n"
        "Every attempt (including every refusal) is ledgered in the "
        "notify_audit table; the message body is stored as SHA-256 + "
        "length, never cleartext. If the ledger itself is unavailable "
        "the tool refuses (notify_audit_unavailable) — it will not send "
        "what it cannot audit, because the ledger is also the rate "
        "budget.\n\n"
        "Kill-switch\n"
        "-----------\n"
        "ENSEMBLE_NOTIFY_USER_ENABLED=false disables the tool entirely "
        "(typed refusal notify_disabled).\n\n"
        "Args:\n"
        "    message: The text to send, 1-2000 chars. Over-length is "
        "refused BEFORE any resolution or dispatch (one notify = one "
        "chat message).\n\n"
        "Returns:\n"
        "    dict on success: {ok: true, delivered: true, source_id, "
        "latency_ms}. Refusals come back as 'Error: notify_user REFUSED "
        "— reason=<code>: <hint>' strings; dispatch failures as 'Error: "
        "notify_user FAILED — reason=<code>: ...' strings. Never raises."
    )


# ── the D1 resolution chain ───────────────────────────────────────────


async def _read_original_source(instance_repository: Any, instance_id: str) -> str | None:
    """Read ``instance_metadata["original_source"]`` for one instance
    (sync repo read bridged off the loop)."""
    if instance_repository is None or not instance_id:
        return None
    row = await asyncio.to_thread(instance_repository.get, instance_id)
    if row is None:
        return None
    meta = getattr(row, "instance_metadata", None) or {}
    value = meta.get("original_source")
    return str(value) if value else None


async def _resolve_bound_chat_source(
    instance_repository: Any,
    current_instance_id: str,
) -> tuple[str, str] | None:
    """D1 — instance → (fallback parent walk to root, depth-capped) →
    ``original_source`` → parse ``source_id:external_user_id``.

    Returns the parsed pair, or ``None`` when the session is unbound.
    The mapping-table reverse lookup is deliberately NOT used
    (non-deterministic under multi-mapping — the `.first()` anti-pattern).
    """
    seen_ids: list[str] = [current_instance_id]
    current: str | None = current_instance_id
    raw: str | None = None
    depth = 0
    max_depth = 256  # the _resolve_lineage_root cap (snapshot_executor)
    while current and depth < max_depth:
        raw = await _read_original_source(instance_repository, current)
        if raw and ":" in raw:
            break
        current = None
        # Walk UP via parent_id (the permanent tree).
        if instance_repository is None:
            break
        row = await asyncio.to_thread(instance_repository.get, seen_ids[-1])
        parent = getattr(row, "parent_id", None) if row is not None else None
        if not parent or parent in seen_ids:
            break
        seen_ids.append(parent)
        current = parent
        depth += 1
    if not raw or ":" not in raw:
        return None
    source_id, external_user_id = raw.split(":", 1)
    if not _SOURCE_ID_RE.match(source_id or ""):
        return None
    if not external_user_id or len(external_user_id) > _EXTERNAL_USER_ID_MAX:
        return None
    if source_id in _UNBOUND_SOURCE_IDS or source_id.startswith("internal_"):
        return None
    return source_id, external_user_id


# ── the ordered pipeline (D9 pre-flight order → C3 gates → W4 send) ──


async def _notify_user_impl(
    *,
    manager: Any,
    audit_repo: NotifyAuditRepository | None,
    current_instance_id: str,
    agent_id: str,
    message: str,
) -> dict | str:
    """The STRICTLY ORDERED pipeline. Pure checks first; the ledger-gated
    send path last; every path returns within the W4 budget."""
    bounds = _resolve_bounds(getattr(manager, "config", None))
    body_length, body_sha256 = _body_digest(message)

    # ── Layer 3 of the access posture: the runtime ari-only gate ────
    # (pre-audit, self_alarm precedent — a caller that never had the
    # tool legitimately leaves no ledger noise.)
    if agent_id != NOTIFY_AUTHORIZED_AGENT:
        return _error(
            f"{NOTIFY_USER_TOOL_NAME} is available to the "
            f"{NOTIFY_AUTHORIZED_AGENT} agent only (caller: "
            f"{agent_id or 'unknown'})."
        )

    # ── N9 kill-switch: the outermost gate. Its refusal never depends
    # on ledger health (a safety refusal must not be maskable); the
    # refusal row is attempted best-effort.
    if not bounds["enabled"]:
        await _try_ledger_refusal(
            audit_repo,
            agent_id=agent_id,
            instance_id=current_instance_id,
            source_id=None,
            target_hash=None,
            refusal_reason=NOTIFY_CODE_DISABLED,
            body_length=body_length,
            body_sha256=body_sha256,
            detail="the ENSEMBLE_NOTIFY_USER_ENABLED kill-switch is off",
        )
        return _refusal(
            NOTIFY_CODE_DISABLED,
            "the notify_user tool is disabled by configuration "
            "(ENSEMBLE_NOTIFY_USER_ENABLED=false).",
        )

    # ── D6 size gate — pure, BEFORE any resolution or dispatch
    # (fold-able 6 pin: the ordering test asserts no resolution work
    # happens on this path). Ledgered with source_id NULL (W3); a
    # ledger failure converts the envelope to notify_audit_unavailable
    # (the uniform N2 conversion — no attempt is ever unauditable),
    # with the earlier condition carried in the detail.
    if not isinstance(message, str) or len(message) == 0:
        unavailable = await _ledger_or_audit_unavailable(
            audit_repo,
            agent_id=agent_id,
            instance_id=current_instance_id,
            source_id=None,
            target_hash=None,
            refusal_reason=NOTIFY_CODE_SIZE_EXCEEDED,
            body_length=0,
            body_sha256=body_sha256,
            detail="message must be 1..%d chars" % bounds["max_chars"],
        )
        if unavailable is not None:
            return unavailable
        return _refusal(
            NOTIFY_CODE_SIZE_EXCEEDED,
            "message must be 1..%d chars (one notify = one chat message)."
            % bounds["max_chars"],
        )
    if len(message) > bounds["max_chars"]:
        unavailable = await _ledger_or_audit_unavailable(
            audit_repo,
            agent_id=agent_id,
            instance_id=current_instance_id,
            source_id=None,
            target_hash=None,
            refusal_reason=NOTIFY_CODE_SIZE_EXCEEDED,
            body_length=body_length,
            body_sha256=body_sha256,
            detail="message is %d chars; cap is %d"
            % (len(message), bounds["max_chars"]),
        )
        if unavailable is not None:
            return unavailable
        return _refusal(
            NOTIFY_CODE_SIZE_EXCEEDED,
            "message is %d chars; the cap is %d (one notify = one chat "
            "message; split or shorten before retrying)."
            % (len(message), bounds["max_chars"]),
        )

    # ── the W4 ONE bound starts here: resolution + gates + send + the
    # single retry all run under ONE deadline.
    loop = asyncio.get_running_loop()
    timeout = float(bounds["timeout_seconds"])
    deadline = loop.time() + timeout
    # The row-id holder survives a mid-send cancellation so the timeout
    # path can still settle the write-ahead row.
    row_holder: dict[str, str] = {}

    try:
        return await asyncio.wait_for(
            _resolve_gate_and_send(
                manager=manager,
                audit_repo=audit_repo,
                bounds=bounds,
                deadline=deadline,
                row_holder=row_holder,
                agent_id=agent_id,
                instance_id=current_instance_id,
                message=message,
                body_length=body_length,
                body_sha256=body_sha256,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        # D4/D11 — the POST may have landed: delivery UNKNOWN, never
        # retried. Settle the write-ahead row honestly (best-effort;
        # the N4 sweep is the backstop).
        row_id = row_holder.get("row_id")
        if row_id and audit_repo is not None:
            try:
                await asyncio.to_thread(
                    audit_repo.update_outcome,
                    row_id,
                    OUTCOME_DELIVERY_UNKNOWN,
                    error_code=NOTIFY_CODE_DISPATCH_TIMEOUT,
                    detail="dispatch exceeded the %ss tool bound" % timeout,
                )
            except Exception:  # noqa: BLE001 — the sweep re-marks it
                logger.warning(
                    "notify_user: timeout-path settle failed for row %s "
                    "(the N4 sweep will re-mark it)", row_id
                )
        return _failure(
            NOTIFY_CODE_DISPATCH_TIMEOUT,
            "delivery status UNKNOWN — the send exceeded the %ss tool "
            "bound and may or may not have arrived; do not re-send "
            "blindly." % timeout,
        )


async def _try_ledger_refusal(
    audit_repo: NotifyAuditRepository | None,
    *,
    agent_id: str,
    instance_id: str,
    source_id: str | None,
    target_hash: str | None,
    refusal_reason: str,
    body_length: int,
    body_sha256: str,
    detail: str | None = None,
) -> bool:
    """Best-effort refusal ledger write. Returns True when written.
    Failures are logged loudly (the refusal itself never depends on
    ledger health — no send is at stake on a refusal path)."""
    if audit_repo is None:
        logger.warning(
            "notify_user: refusal %s not ledgered (no audit repository)",
            refusal_reason,
        )
        return False
    try:
        await asyncio.to_thread(
            audit_repo.write_refusal,
            agent_id=agent_id,
            instance_id=instance_id,
            source_id=source_id,
            target_hash=target_hash,
            refusal_reason=refusal_reason,
            body_length=body_length,
            body_sha256=body_sha256,
            detail=detail,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — refusal rows are best-effort
        logger.error(
            "notify_user: refusal ledger write failed (%s): %s: %s",
            refusal_reason,
            type(exc).__name__,
            exc,
        )
        return False


async def _ledger_or_audit_unavailable(
    audit_repo: NotifyAuditRepository | None,
    **kwargs: Any,
) -> str | None:
    """Ledger a refusal and CONVERT to the N2 envelope when the write
    fails (the send path is at stake downstream). Returns the
    notify_audit_unavailable refusal string on failure, else None."""
    written = await _try_ledger_refusal(audit_repo, **kwargs)
    if not written:
        return _refusal(
            NOTIFY_CODE_AUDIT_UNAVAILABLE,
            "the notify_audit ledger is unavailable (the rate budget "
            "could not be recorded) — the notification was NOT sent. "
            "Earlier condition: %s. Retry when the ledger recovers."
            % kwargs.get("refusal_reason", "unknown"),
        )
    return None


async def _resolve_gate_and_send(
    *,
    manager: Any,
    audit_repo: NotifyAuditRepository | None,
    bounds: dict[str, Any],
    deadline: float,
    row_holder: dict[str, str],
    agent_id: str,
    instance_id: str,
    message: str,
    body_length: int,
    body_sha256: str,
) -> dict | str:
    """Resolution (D1) → feasibility pre-flight (D9 order) → the B2-
    serialized ledger gates (C3/N3) → the adapter send (D3) with the
    single transient retry (D10) inside the caller's W4 deadline."""
    loop = asyncio.get_running_loop()

    # ── N4 sweep: cheap, defensive, BEFORE the gates (crash orphans
    # read as unknown, and only rows STILL STANDING as ``accepted`` are
    # swept — the settled terminal forms are already terminal). The
    # threshold is DERIVED from the (tunable) dispatch bound — see
    # ``_orphan_sweep_seconds``.
    if audit_repo is not None:
        try:
            await asyncio.to_thread(
                audit_repo.sweep_stale_accepted,
                older_than_seconds=_orphan_sweep_seconds(bounds),
            )
        except Exception as exc:  # noqa: BLE001 — the sweep is a backstop
            logger.warning("notify_user: orphan sweep failed: %s", exc)

    # ── D1: resolve the session's bound chat source ──────────────────
    instance_repo = getattr(manager, "_instance_repository", None)
    try:
        resolved = await _resolve_bound_chat_source(
            instance_repo, instance_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("notify_user: resolution read failed: %s", exc)
        resolved = None
    if resolved is None:
        unavailable = await _ledger_or_audit_unavailable(
            audit_repo,
            agent_id=agent_id,
            instance_id=instance_id,
            source_id=None,
            target_hash=None,
            refusal_reason=NOTIFY_CODE_NO_BOUND_CHAT_SOURCE,
            body_length=body_length,
            body_sha256=body_sha256,
            detail="the session carries no chat binding (original_source "
            "absent or colon-less) — the board is the fallback surface",
        )
        if unavailable is not None:
            return unavailable
        return _refusal(
            NOTIFY_CODE_NO_BOUND_CHAT_SOURCE,
            "this session has no bound chat source (it was never "
            "contacted via chat), so there is nothing to notify. The "
            "board remains the user-visible surface; leave evidence "
            "there.",
        )
    source_id, external_user_id = resolved
    target_hash = _target_hash(source_id, external_user_id)

    # ── D9 pre-flight order ──────────────────────────────────────────
    source_repo = getattr(manager, "_source_repository", None)
    source_row = None
    if source_repo is not None:
        try:
            source_row = await asyncio.to_thread(
                source_repo.get_source_config, source_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("notify_user: source row read failed: %s", exc)
            source_row = None
    if source_row is None:
        unavailable = await _ledger_or_audit_unavailable(
            audit_repo,
            agent_id=agent_id,
            instance_id=instance_id,
            source_id=source_id,
            target_hash=target_hash,
            refusal_reason=NOTIFY_CODE_SOURCE_NOT_REGISTERED,
            body_length=body_length,
            body_sha256=body_sha256,
            detail="no source_configs row for %r" % source_id,
        )
        if unavailable is not None:
            return unavailable
        return _refusal(
            NOTIFY_CODE_SOURCE_NOT_REGISTERED,
            "the resolved source %r is not registered (deleted or "
            "unknown) — nothing was sent." % source_id,
        )

    source_type = str(getattr(source_row, "source_type", "") or "")
    if source_type not in USER_ORIGIN_CHAT_SOURCE_TYPES:
        unavailable = await _ledger_or_audit_unavailable(
            audit_repo,
            agent_id=agent_id,
            instance_id=instance_id,
            source_id=source_id,
            target_hash=target_hash,
            refusal_reason=NOTIFY_CODE_SOURCE_NOT_CHAT_TYPE,
            body_length=body_length,
            body_sha256=body_sha256,
            detail="source_type=%r is not a human chat channel" % source_type,
        )
        if unavailable is not None:
            return unavailable
        return _refusal(
            NOTIFY_CODE_SOURCE_NOT_CHAT_TYPE,
            "the resolved source %r has type %r — not a human chat "
            "channel (expected one of: %s). Nothing was sent."
            % (
                source_id,
                source_type,
                ", ".join(sorted(USER_ORIGIN_CHAT_SOURCE_TYPES)),
            ),
        )

    if getattr(source_row, "enabled", True) is False:
        unavailable = await _ledger_or_audit_unavailable(
            audit_repo,
            agent_id=agent_id,
            instance_id=instance_id,
            source_id=source_id,
            target_hash=target_hash,
            refusal_reason=NOTIFY_CODE_SOURCE_DISABLED,
            body_length=body_length,
            body_sha256=body_sha256,
            detail="source_configs.enabled=False",
        )
        if unavailable is not None:
            return unavailable
        return _refusal(
            NOTIFY_CODE_SOURCE_DISABLED,
            "the resolved source %r is disabled (enabled=False) — "
            "nothing was sent." % source_id,
        )

    registry = getattr(manager, "source_registry", None)
    adapter = registry.get(source_id) if registry is not None else None
    if adapter is None:
        # The whatsapp class: semantically a chat type, but the registry
        # factory builds no adapter (leader adjudication 3) — the
        # refusal is correct behavior, not a bug.
        unavailable = await _ledger_or_audit_unavailable(
            audit_repo,
            agent_id=agent_id,
            instance_id=instance_id,
            source_id=source_id,
            target_hash=target_hash,
            refusal_reason=NOTIFY_CODE_SOURCE_NOT_REGISTERED,
            body_length=body_length,
            body_sha256=body_sha256,
            detail="no live adapter for %r" % source_id,
        )
        if unavailable is not None:
            return unavailable
        return _refusal(
            NOTIFY_CODE_SOURCE_NOT_REGISTERED,
            "the resolved source %r has no live adapter (not registered "
            "or its type is not buildable) — nothing was sent."
            % source_id,
        )

    adapter_status = getattr(adapter, "status", None)
    if adapter_status != SourceStatus.RUNNING:
        unavailable = await _ledger_or_audit_unavailable(
            audit_repo,
            agent_id=agent_id,
            instance_id=instance_id,
            source_id=source_id,
            target_hash=target_hash,
            refusal_reason=NOTIFY_CODE_SOURCE_DISABLED,
            body_length=body_length,
            body_sha256=body_sha256,
            detail="adapter status=%s (not RUNNING)" % adapter_status,
        )
        if unavailable is not None:
            return unavailable
        return _refusal(
            NOTIFY_CODE_SOURCE_DISABLED,
            "the resolved source %r is not RUNNING (status=%s) — start "
            "the source first; nothing was sent."
            % (source_id, getattr(adapter_status, "value", adapter_status)),
        )

    breaker = getattr(adapter, "_circuit_breaker", None)
    if breaker is not None:
        # A5 — this pre-check is NOT side-effect free: under a HALF_OPEN
        # breaker, ``can_execute()`` CLAIMS the single probe slot (the
        # adapter's own send path then finds the slot already taken). A
        # notify that races the recovery window can therefore surface a
        # TRANSIENT ``notify_dispatch_failed`` instead of
        # ``notify_source_circuit_open``. That is fail-safe and
        # self-healing (the breaker records the failure and recovers on
        # its own) — but it is NOT a circuit-open signal, so the failure
        # must never be read as "the source is down".
        try:
            breaker_ok = await breaker.can_execute()
        except Exception as exc:  # noqa: BLE001 — a broken breaker reads
            logger.warning(  # as open (fail-closed)
                "notify_user: breaker check failed: %s", exc
            )
            breaker_ok = False
        if not breaker_ok:
            unavailable = await _ledger_or_audit_unavailable(
                audit_repo,
                agent_id=agent_id,
                instance_id=instance_id,
                source_id=source_id,
                target_hash=target_hash,
                refusal_reason=NOTIFY_CODE_SOURCE_CIRCUIT_OPEN,
                body_length=body_length,
                body_sha256=body_sha256,
                detail="circuit breaker OPEN (60s recovery window)",
            )
            if unavailable is not None:
                return unavailable
            return _refusal(
                NOTIFY_CODE_SOURCE_CIRCUIT_OPEN,
                "the source %r circuit breaker is OPEN after repeated "
                "send failures (recovers in ~60s) — nothing was sent; "
                "retry later or use the board." % source_id,
            )

    # ── the B2-serialized ledger gates (C3 commit-before-count) ──────
    if audit_repo is None:
        return _refusal(
            NOTIFY_CODE_AUDIT_UNAVAILABLE,
            "the notify_audit ledger is unavailable (no audit "
            "repository) — the notification was NOT sent (fail-closed: "
            "the ledger is the rate budget).",
        )
    lock = await _destination_lock(target_hash)
    async with lock:
        # Interval gate (fold-able 4 pin: last-ACCEPTED-class baseline).
        try:
            last_iso = await asyncio.to_thread(
                audit_repo.last_accepted_class_created_at,
                instance_id=instance_id,
            )
        except Exception as exc:  # noqa: BLE001 — N2 fail-closed
            logger.error("notify_user: interval read failed: %s", exc)
            return _refusal(
                NOTIFY_CODE_AUDIT_UNAVAILABLE,
                "the notify_audit ledger is unavailable (the interval "
                "gate could not be read) — the notification was NOT "
                "sent.",
            )
        now = datetime.now(timezone.utc)
        if last_iso:
            try:
                elapsed = (
                    now - datetime.fromisoformat(last_iso)
                ).total_seconds()
            except (TypeError, ValueError) as exc:
                # A4 — FAIL-CLOSED (the safe direction for outbound-to-
                # user traffic): an UNPARSEABLE baseline stamp means the
                # interval is NOT satisfiable. The old branch fell back
                # to ``elapsed = min_interval_seconds`` (=> wait_s = 0
                # => the send went through on a garbage stamp) — the
                # fail-OPEN direction. Refuse with the rate-limited
                # path instead: typed refusal + a ledgered row (N3
                # discipline — every refusal is ledgered).
                logger.warning(
                    "notify_user: unparseable interval baseline %r: %s",
                    last_iso,
                    exc,
                )
                unavailable = await _ledger_or_audit_unavailable(
                    audit_repo,
                    agent_id=agent_id,
                    instance_id=instance_id,
                    source_id=source_id,
                    target_hash=target_hash,
                    refusal_reason=NOTIFY_CODE_RATE_LIMITED,
                    body_length=body_length,
                    body_sha256=body_sha256,
                    detail="interval gate: the baseline stamp is "
                    "unparseable (%s) — the minimum interval cannot be "
                    "verified, so it is not satisfiable"
                    % (last_iso,),
                )
                if unavailable is not None:
                    return unavailable
                return _refusal(
                    NOTIFY_CODE_RATE_LIMITED,
                    "interval gate: this session's last-notification "
                    "stamp is unparseable, so the minimum interval "
                    "cannot be verified. The notification was NOT sent.",
                )
            wait_s = float(bounds["min_interval_seconds"]) - elapsed
            if wait_s > 0:
                unavailable = await _ledger_or_audit_unavailable(
                    audit_repo,
                    agent_id=agent_id,
                    instance_id=instance_id,
                    source_id=source_id,
                    target_hash=target_hash,
                    refusal_reason=NOTIFY_CODE_RATE_LIMITED,
                    body_length=body_length,
                    body_sha256=body_sha256,
                    detail="interval gate: %.0fs until the next send"
                    % wait_s,
                )
                if unavailable is not None:
                    return unavailable
                return _refusal(
                    NOTIFY_CODE_RATE_LIMITED,
                    "interval gate: this session sent a notification "
                    "%.0fs ago (minimum %ds). The next send becomes "
                    "legal in %.0fs."
                    % (
                        elapsed,
                        bounds["min_interval_seconds"],
                        wait_s,
                    ),
                )

        # C3 commit-before-count: the accepted INSERT commits FIRST,
        # then the COUNT runs in a fresh statement — the durable
        # restart/crash backstop under the B2 lock.
        try:
            row_id = await asyncio.to_thread(
                audit_repo.insert_accepted,
                agent_id=agent_id,
                instance_id=instance_id,
                source_id=source_id,
                target_hash=target_hash,
                body_length=body_length,
                body_sha256=body_sha256,
            )
        except Exception as exc:  # noqa: BLE001 — N2 fail-closed
            logger.error("notify_user: write-ahead insert failed: %s", exc)
            return _refusal(
                NOTIFY_CODE_AUDIT_UNAVAILABLE,
                "the notify_audit ledger is unavailable (the budget row "
                "could not be committed) — the notification was NOT "
                "sent.",
            )
        row_holder["row_id"] = row_id

        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        try:
            accepted = await asyncio.to_thread(
                audit_repo.count_accepted_for_target,
                target_hash=target_hash,
                since_iso=cutoff,
            )
        except Exception as exc:  # noqa: BLE001 — N2 fail-closed
            logger.error("notify_user: budget count failed: %s", exc)
            await _settle_quietly(audit_repo, row_id, OUTCOME_RATE_LIMITED)
            return _refusal(
                NOTIFY_CODE_AUDIT_UNAVAILABLE,
                "the notify_audit ledger is unavailable (the hourly "
                "count could not be read) — the notification was NOT "
                "sent.",
            )
        if accepted > int(bounds["max_per_hour"]):
            # Over limit — flip the own row to rate_limited; it never
            # dispatches and (being no longer 'accepted') never
            # consumes budget (N3).
            await _settle_quietly(
                audit_repo,
                row_id,
                OUTCOME_RATE_LIMITED,
                refusal_reason=NOTIFY_CODE_RATE_LIMITED,
                detail="hourly cap: %d accepted rows in the window "
                "(cap %d)" % (accepted, bounds["max_per_hour"]),
            )
            return _refusal(
                NOTIFY_CODE_RATE_LIMITED,
                "hourly cap: destination already received %d accepted "
                "notifications in the last hour (cap %d, shared across "
                "all sessions). The next send becomes legal as earlier "
                "rows age out of the rolling hour."
                % (accepted, bounds["max_per_hour"]),
            )
    # ── the B2 lock is RELEASED here — the dispatch itself never
    # serializes on the gate lock (the budget slot is already consumed).

    # ── D3: the direct adapter send (bool-returning — the only seam
    # where the caller learns the outcome) under the remaining W4
    # budget, with the D10 single transient retry.
    outgoing = OutgoingMessage(
        external_user_id=external_user_id,
        content=message,
        source_id=source_id,
    )
    started = time.monotonic()
    ok = False
    attempts = 0
    while True:
        attempts += 1
        try:
            ok = bool(
                await asyncio.wait_for(
                    adapter.send(outgoing),
                    timeout=max(0.25, deadline - loop.time()),
                )
            )
        except asyncio.TimeoutError:
            raise  # the outer W4 handler owns the unknown-tail envelope
        except Exception as exc:  # noqa: BLE001 — a raising adapter is a
            logger.warning(  # definite failure for this attempt
                "notify_user: adapter.send raised: %s", exc
            )
            ok = False
        if ok:
            break
        remaining = deadline - loop.time()
        backoff = max(
            NOTIFY_RETRY_BACKOFF_MIN_S,
            min(NOTIFY_RETRY_BACKOFF_MAX_S, remaining / 2),
        )
        if attempts >= 2 or remaining <= backoff + 0.25:
            break
        await asyncio.sleep(backoff)

    latency_ms = int((time.monotonic() - started) * 1000)
    if ok:
        try:
            await asyncio.to_thread(
                audit_repo.update_outcome,
                row_id,
                OUTCOME_DISPATCHED,
                detail="delivered on attempt %d" % attempts,
            )
        except Exception as exc:  # noqa: BLE001 — the row stands accepted
            logger.error(  # (conservative) until the sweep
                "notify_user: dispatched-settle failed for row %s: %s",
                row_id,
                exc,
            )
        return {
            "ok": True,
            "delivered": True,
            "source_id": source_id,
            "latency_ms": latency_ms,
        }

    # Definite failure after the single retry (D10 — refusal-class
    # outcomes are never retried; a timeout never reaches here).
    try:
        await asyncio.to_thread(
            audit_repo.update_outcome,
            row_id,
            OUTCOME_DELIVERY_FAILED,
            error_code=NOTIFY_CODE_DISPATCH_FAILED,
            detail="adapter returned not-ok on %d attempts" % attempts,
        )
    except Exception as exc:  # noqa: BLE001 — the sweep re-marks it
        logger.error(
            "notify_user: failed-settle error for row %s: %s", row_id, exc
        )
    return _failure(
        NOTIFY_CODE_DISPATCH_FAILED,
        "the %r adapter reported the send as failed after %d attempt(s) "
        "(definite failure — nothing ambiguous). Nothing was delivered "
        "by this tool; use the board or retry later."
        % (source_id, attempts),
    )


async def _settle_quietly(
    audit_repo: NotifyAuditRepository,
    row_id: str,
    outcome: str,
    *,
    refusal_reason: str | None = None,
    detail: str | None = None,
) -> None:
    """Best-effort terminal update of the OWN write-ahead row. A failure
    here leaves the row standing as accepted — the conservative
    direction (it counts toward the cap until the N4 sweep)."""
    try:
        await asyncio.to_thread(
            audit_repo.update_outcome,
            row_id,
            outcome,
            error_code=refusal_reason,
            refusal_reason=refusal_reason,
            detail=detail,
        )
    except Exception as exc:  # noqa: BLE001 — logged, never raised
        logger.error(
            "notify_user: settle to %s failed for row %s: %s",
            outcome,
            row_id,
            exc,
        )
