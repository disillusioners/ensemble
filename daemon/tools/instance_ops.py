"""Instance ACT tools — the ``instance_ops`` privileged category (6 tools).

Six ACTS over the instance fleet, each one an audited, gated wrapper
over an EXISTING daemon primitive (D-3 — no new daemon subsystem):

``instance_terminate``  — the destructive act (cascade, dry-run default)
``instance_reclaim``    — terminal-only revive-by-documented-message
``instance_nudge``      — inject a message into any instance
``instance_mark``       — park / protect / suspect markers
``instance_answer``     — answer a pending question pack
``instance_dismiss``    — dismiss a pending question pack

EVERY ACT IS AUDITED
--------------------
Every act writes a row to ``instance_ops_audit`` — the preview, the
write-ahead intent and the committed outcome. Refusals from the
protected-target gate, the budget, and the act itself are ledgered;
input-validation and early-gate refusals (reason-required,
self-surgery, not-found, target-too-young) are NOT. A destructive act
that cannot be audited DOES NOT HAPPEN.

The audit boundary is a TWO-TIER property (D-15), stated per tool:

* **Tier 1 — true same-transaction.** ``instance_terminate`` (via the
  additive ``audit_sink`` hook inside the lifecycle service's own
  ``WriteGuardSession``) and ``instance_mark(protect|suspect)`` (via the
  optional ``session=`` on ``repo.set_metadata``). The act and its
  audit row commit TOGETHER or NEITHER commits.
* **Tier 2 — write-ahead.** ``instance_nudge`` / ``instance_answer`` /
  ``instance_dismiss`` / ``instance_reclaim`` / ``instance_mark(park)``
  INSERT the audit row FIRST with ``outcome="pending"``, run the act,
  then UPDATE the row. A crash mid-act leaves a durable ``pending``
  row — the ledger always contains the intent, and the true outcome is
  read from INSTANCE STATE, not from the ledger.

``instance_mark(park)`` is deliberately Tier-2 (leader decision W3):
the deliberate-pause machinery is a live subsystem fixed in v0.26.1,
its writer is fail-open by design, and threading a session into it is
explicitly NOT this category's business.

THE GATES, IN ORDER (terminate)
-------------------------------
reason-required → instance-not-found → self-surgery-refused →
target-too-young → terminate-budget-exceeded →
cascade-false-would-orphan-descendants →
[protected: protected-target-confirmation-required →
confirmed-target-mismatch → no-user-provenance [apply only — provenance]] →
dry-run preview | apply.

F6 / D-8 (the leader-adjudicated MINIMAL reading): the protected gate
SPLITS at the dry-run fork. The CONFIRMATION conditions run on BOTH
paths — a preview still names its target. ONLY the user-provenance
condition is apply-only: a dry-run preview REPORTS what apply requires
(``act_gate`` / ``act_gate_would_pass``) instead of demanding a live
user-origin window the caller cannot self-satisfy. The destructive act
keeps ALL gates exactly as-is.

F6-iv (precedence inversion, disclosed): guards 8-9 (the budget and the
cascade rails) now run BEFORE the protected gate, so an exhausted budget
or a cascade=False refusal on a protected target yields THAT token
instead of ``protected-target-confirmation-required``. Both are refusals
— no authority is granted either way — but the precedence is a
behaviour change outside the provenance-only claim.

THE PROVENANCE GATE IS A SPEED BUMP, NOT A PROOF (D-18 → superseded by DF-4)
-------------------------------------------------------------------------------
The gate keys on LIVENESS, not on class: a protected target needs a live
user-origin provenance window, whatever SHAPE that window has. The class
(``pclass``) is a READ-TIME derivation of the live window — never stored
in the window store (the ledger snapshots it as ``provenance_class``),
never re-verified — so it carried zero trust signal and survives as
OBSERVABILITY only (the audit rows answer "which window shape authorised
this act?").

D-18's "direct refused on purpose" property is RETIRED BY DESIGN. The
old class-keyed rule refused the GENUINE operator-direct path (defect B)
while never blocking the DELEGATED forge: the inherited mint copies the
root WITHOUT inspecting the root's own provenance, so a forged direct
window on a spawner armed an inherited window here anyway. DF-4 closes
that delegated forge at its ROOT — the sweep's no-user-origin fallback
now rides a reserved lane and can no longer mint a forged DIRECT window
— and re-scopes this gate to liveness. The REST-ingress class stays OPEN
(critical note ``257488dc``): a window forged through a NON-sweep surface
is admitted, bounded by the D-16 budget plus the post-hoc audit.

THE KILL-SWITCH (D-7)
---------------------
``ENSEMBLE_INSTANCE_OPS_ENABLED=0`` disables every ACT (byte-identical
no-op refusal). The READ category stays available — you diagnose with
the reads while the acts are off.

Access posture — TWO layers, BOTH required
------------------------------------------
1. The ``instance_ops`` category is PRIVILEGED, so it never joins the
   empty-allow default-grant universe.
2. A privileged category alone is NOT sufficient — a **literal tool
   name** in an allow list bypasses the category gate entirely. So
   every tool body ALSO calls the one shared module-level helper
   :func:`_is_instance_ops_authorized`.

Tools NEVER raise — every failure returns a typed refusal string in
the house envelope:
``Error: {label} REFUSED — reason={token}: {message}``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Annotated, Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from .provenance import ProvenanceVerdict, restart_provenance
from daemon.services.mission_record_service import (
    mint_commission_fail_open,
    mint_revive_epoch_fail_open,
    resolve_or_mint_fail_open,
)
from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Instance Ops"
CATEGORY_DOC = """\
Gated, audited ACTS over the instance fleet — terminate, reclaim,
nudge, mark, answer, dismiss.

Tools in this category:
- ``instance_terminate`` — terminate an instance (cascade by default)
- ``instance_reclaim``   — revive a TERMINAL instance with a message
- ``instance_nudge``     — inject a message into any instance
- ``instance_mark``      — park / protect / suspect markers
- ``instance_answer``    — answer a pending question pack
- ``instance_dismiss``   — dismiss a pending question pack without answering

Every act is AUDITED: it writes a row to the audit ledger, and a
destructive act that cannot be audited does not happen. Every act
requires a `reason` (free text, stored verbatim). Every destructive
act defaults to `dry_run=True` — the preview shows exactly what would
happen, mutates nothing, and reports what apply will require (on a
protected target: `act_gate` and `act_gate_would_pass` — the preview
names the target and reports the apply-path requirements, it does not
grant them).

A PROTECTED target — a user-facing chat session, the leader, or an
instance marked protected — additionally requires a named confirmation
(`confirmed_target_id`, the exact instance id) AND a live user-origin
provenance window. The window's SHAPE (minted on this instance vs
inherited through a delegation chain) is recorded in the audit ledger but
is NOT a gate input — a live window of either shape is admitted.

You never act on your own session. There is a kill switch: when the
act surface is switched off, every act here refuses and the read
surface keeps working.
"""

#: The agents allowed to operate the act surface. The privileged
#: category gates VISIBILITY; this set gates AUTHORITY. Both are
#: required (a literal-tool-name allow entry bypasses the category
#: gate). MAINTENANCER-ONLY (D-13 — one owner for both categories).
INSTANCE_OPS_AUTHORIZED_AGENTS: frozenset[str] = frozenset({"maintenancer"})

#: The kill-switch environment variable (D-7). Default ON.
KILL_SWITCH_ENV = "ENSEMBLE_INSTANCE_OPS_ENABLED"

#: The exact byte-identical no-op every ACT returns when the kill
#: switch is OFF (D-7). The shape is the contract — pinned by test.
KILL_SWITCH_REFUSAL = (
    "Error: INSTANCE_OPS DISABLED — reason=instance-ops-disabled: "
    "the instance_ops act tools are disabled by configuration "
    "(ENSEMBLE_INSTANCE_OPS_ENABLED=0). No mutation happened."
)

#: The terminate budget (D-16): N committed terminates per 24h,
#: fleet-wide.
TERMINATE_BUDGET_24H = 3
#: The PROTECTED-target terminate budget (D-16): N per 24h.
PROTECTED_TERMINATE_BUDGET_24H = 1
#: The target min-age rail (D-16): a target younger than this refuses.
TARGET_MIN_AGE_SECONDS = 600

#: The metadata key namespace for the dynamic markers (D-11).
PROTECT_MARKER_KEY = "instance_ops.protect"
SUSPECT_MARKER_KEY = "instance_ops.suspect"

#: Valid marker values for ``instance_mark``.
VALID_MARKERS = ("park", "protect", "suspect")

#: The protected-gate refusal tokens, split by what they mean for the
#: ``dry_run`` fork (F6, D-8).
#:
#: The CONFIRMATION tokens are agent-satisfiable (the caller can name
#: the target) — they gate BOTH paths: an unconfirmed preview on a
#: protected target still refuses.
#:
#: The PROVENANCE token is NOT agent-satisfiable — it needs a live
#: user-origin window on this turn. On the preview path it renders as
#: DATA (``act_gate_would_pass``) instead of a refusal; on the apply
#: path it refuses exactly as before.
GATE_TOKEN_CONFIRMATION_REQUIRED = "protected-target-confirmation-required"
GATE_TOKEN_CONFIRMATION_MISMATCH = "confirmed-target-mismatch"
GATE_TOKEN_NO_USER_PROVENANCE = "no-user-provenance"

#: The confirmation tokens — the ones that gate BOTH paths.
GATE_CONFIRMATION_TOKENS = frozenset({
    GATE_TOKEN_CONFIRMATION_REQUIRED,
    GATE_TOKEN_CONFIRMATION_MISMATCH,
})

#: The house refusal envelope's reason token is NO LONGER recovered by
#: a regex. The gate returns its token STRUCTURALLY (see
#: :func:`_protected_gate`), so the caller never parses a message body
#: to learn which condition fired. The old ANCHORED regex
#: (``^Error: .*? REFUSED — reason=([a-z0-9-]+):``) was a recovery
#: seam, and a recovery seam is a silent-degradation seam: a non-match
#: turned a CONFIRMATION refusal into preview data attributed to a
#: hardcoded provenance token. There is nothing left to recover.


def _is_instance_ops_authorized(agent_id: str) -> bool:
    """Is this agent allowed to operate the act surface?

    ONE shared helper, called by ALL SIX tool bodies — the set lives in
    exactly one place and the six bodies share it (the
    ``daemon/tools/views_auth.py`` / ``daemon/tools/board_tools.py``
    precedent).

    Fail-closed: a blank or non-``str`` ``agent_id`` is NEVER
    authorized.
    """
    if not isinstance(agent_id, str) or not agent_id:
        return False
    return agent_id in INSTANCE_OPS_AUTHORIZED_AGENTS


def _resolve_instance_ops_enabled() -> bool:
    """Resolve the kill-switch. Default ON; explicit off-spellings off.

    Mirrors the ens-db lane's ``_resolve_repair_enabled`` resolution
    order: env → default ON.
    """
    raw = os.environ.get(KILL_SWITCH_ENV)
    if raw is None:
        return True
    return str(raw).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _refusal(label: str, token: str, message: str) -> str:
    """The house refusal envelope (identical shape in both categories)."""
    return f"Error: {label} REFUSED — reason={token}: {message}"


def _error(msg: str) -> str:
    """House error envelope — tools never raise."""
    return f"ERROR: {msg}"


def _reason_missing(reason: Any) -> bool:
    """The reason gate (D5) — required, free text, non-empty."""
    return not isinstance(reason, str) or not reason.strip()


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _age_seconds(created_at: Any) -> int | None:
    parsed = _parse_iso(created_at)
    if parsed is None:
        return None
    return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))


def _is_user_facing_root_target(*, parent_id: Any, source_type: Any) -> bool:
    """The STATIC user-facing-root predicate (D-4, leg 1).

    Delegates to the EXISTING fail-closed predicate in
    ``daemon/tools/instance.py`` — deferred import (that module imports
    this one's factory; a module-level import would be circular).
    """
    try:
        from .instance import _is_user_facing_root

        return bool(
            _is_user_facing_root(
                parent_id=parent_id, source_type=source_type
            )
        )
    except Exception:  # noqa: BLE001 — fail-closed on any resolution failure
        return False


def _provenance_for(manager: Any, instance_id: str) -> ProvenanceVerdict:
    """The canonical provenance verdict — the SINGLE neutral helper.

    AC-10a: this resolves to ``daemon.tools.provenance.restart_provenance``
    (imported at module top). There is NO local re-implementation; a
    source pin asserts the import.
    """
    try:
        return restart_provenance(manager, instance_id)
    except Exception as exc:  # noqa: BLE001 — fail-closed on any failure
        logger.warning(
            "instance_ops: provenance read failed for %s: %s: %s",
            instance_id,
            type(exc).__name__,
            exc,
        )
        return ProvenanceVerdict(ok=False, pclass="", detail=f"provenance read failed: {type(exc).__name__}")


def _user_origin_sources_hint() -> str:
    """The refusal-path disclosure of what counts as user-origin."""
    try:
        from .upgrade_journal import user_origin_sources_display

        return user_origin_sources_display()
    except Exception:  # noqa: BLE001 — disclosure is best-effort
        return ""


def _target_state(manager: Any, instance_id: str) -> dict[str, Any] | None:
    """The target's row as a dict (None when the row is gone)."""
    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        return None
    try:
        row = repo.get(instance_id)
    except Exception:  # noqa: BLE001 — read-only probe
        return None
    if row is None:
        return None
    info = row.to_dict()
    info["children"] = repo.list_child_ids_permanent(instance_id)
    return info


def _protection_reasons(manager: Any, info: dict[str, Any]) -> list[str]:
    """The protection layers that apply to a target (F7 — ONE home).

    Three layers, three CLOSED reason values (D-19 discipline — no open
    vocabulary):

    1. ``leader`` — STATIC: the target is the leader's session.
    2. ``chat-root`` — STATIC: a user-facing chat root
       (``_is_user_facing_root_target``, fail-closed on both legs).
    3. ``marker`` — DYNAMIC: an ``instance_ops.protect`` marker in the
       target's metadata.

    The ORDER is the layer order, so the list is stable for a given
    target. A protect marker can only ADD protection; nothing in this
    category can un-protect a statically protected target.

    ``manager`` is accepted for symmetry with :func:`_is_protected_target`
    (the canonical call shape) — the determination itself reads only the
    target's own row.
    """
    reasons: list[str] = []
    if (info.get("agent_id") or "") == "leader":
        reasons.append("leader")
    metadata = info.get("metadata") or {}
    source_type = (
        metadata.get("source_type")
        if isinstance(metadata, dict)
        else None
    )
    if _is_user_facing_root_target(
        parent_id=info.get("parent_id"), source_type=source_type
    ):
        reasons.append("chat-root")
    if isinstance(metadata, dict):
        marker = metadata.get(PROTECT_MARKER_KEY)
        if isinstance(marker, dict) and marker.get("protected") is True:
            reasons.append("marker")
    return reasons


def _is_protected_target(manager: Any, info: dict[str, Any]) -> bool:
    """The protected determination — an OR of three layers (D-4).

    F7: this is now a CONSEQUENCE of the single-home
    :func:`_protection_reasons` — one implementation, no second
    predicate that can drift.

    1. STATIC: a user-facing chat root (``_is_user_facing_root``,
       fail-closed on both legs) — OR ``agent_id == "leader"``.
    2. DYNAMIC: an ``instance_ops.protect`` marker in the metadata.

    A protect marker can only ADD protection; nothing in this category
    can un-protect a statically protected target.
    """
    return bool(_protection_reasons(manager, info))


def _audit_repo_for(manager: Any):
    """The audit ledger for this manager's engine (defensive)."""
    engine = getattr(manager, "engine", None)
    if engine is None:
        return None
    from ..repositories.instance_ops.repository import InstanceOpsAuditRepository

    return InstanceOpsAuditRepository(engine)


def _protected_gate(
    *,
    target_id: str,
    confirmed_target_id: str | None,
    verdict: ProvenanceVerdict,
    label: str,
) -> tuple[str | None, str | None]:
    """The protected-target gate (D-17 / D-18, re-scoped by DF-4).

    TWO conditions, in order: the confirmed-target check (D-17) and the
    LIVENESS of the user-origin provenance window. The window's CLASS is
    NOT a gate input — ``pclass`` is a read-time derivation that carries
    no trust signal, and it survives as observability in the audit
    ledger (DF-4).

    Returns ``(refusal, token)``:

    * ``(None, None)`` — the gate PASSES.
    * ``(refusal_string, token)`` — the gate REFUSES, and ``token`` is
      the refusal's reason token, RETURNED STRUCTURALLY.

    The token is the CONSTANT that minted the refusal (see
    ``GATE_TOKEN_*``), never a re-parse of the message body. The old
    shape recovered it with an anchored regex and fell back to a
    hardcoded provenance token when the regex did not match — a
    recovery seam that could silently mislabel a CONFIRMATION refusal
    as provenance data. The refusal STRING is byte-identical to the
    pre-fix shape (the constants hold the same spellings).
    """
    if confirmed_target_id is None:
        return (
            _refusal(
                label,
                GATE_TOKEN_CONFIRMATION_REQUIRED,
                (
                    f"{target_id!r} is a protected target. Pass "
                    f"confirmed_target_id='{target_id}' — the confirmation "
                    "NAMES the target it authorizes."
                ),
            ),
            GATE_TOKEN_CONFIRMATION_REQUIRED,
        )
    if confirmed_target_id != target_id:
        return (
            _refusal(
                label,
                GATE_TOKEN_CONFIRMATION_MISMATCH,
                (
                    f"confirmed_target_id={confirmed_target_id!r} does not "
                    f"name the target {target_id!r}. The confirmation must "
                    "name the target the user consented to, verbatim."
                ),
            ),
            GATE_TOKEN_CONFIRMATION_MISMATCH,
        )
    if not verdict.ok:
        hint = _user_origin_sources_hint()
        extra = f" What counts as user-origin: {hint}." if hint else ""
        return (
            _refusal(
                label,
                GATE_TOKEN_NO_USER_PROVENANCE,
                (
                    "no live user-origin provenance window on this turn. "
                    "The windows are in-memory and are wiped on a daemon "
                    f"restart.{extra}"
                ),
            ),
            GATE_TOKEN_NO_USER_PROVENANCE,
        )
    return None, None


def _build_act_gate_payload(
    *,
    protected: bool,
    gate_refusal: str | None,
    gate_token: str | None,
) -> dict[str, Any] | None:
    """The F6 preview's ``act_gate`` block — what APPLY would require.

    The gate verdict is rendered as DATA — TOKENS ONLY, never the raw
    refusal string (it embeds the target id and the caller's own
    confirmation value). A confirmed preview that cannot pass the
    provenance leg reports ``act_gate_would_pass.ok == false`` with the
    reason token: the operator cannot self-satisfy that without a
    genuine user turn.

    ``would_refuse_with`` is DERIVED from the token the gate actually
    minted — never a hardcoded guess (the pre-fix shape hardcoded the
    provenance token, and a token-recovery miss mislabelled a
    confirmation refusal as provenance data).

    Returns:
        The block, or ``None`` when the target is not protected (the
        preview then carries no gate at all).
    """
    if not protected:
        return None
    return {
        "confirmation": "confirmed_target_id must equal target verbatim",
        "provenance": (
            "a LIVE user-origin provenance window on this turn; a "
            "delegated chain (send_message OR job dispatch) is accepted; "
            "apply refuses with no-user-provenance when absent"
        ),
        "would_refuse_with": ([gate_token] if gate_token else []),
    }


def _write_gate_refusal(
    audit: Any,
    *,
    action: str,
    target_id: str,
    reason: str,
    refusal: str,
    caller_instance_id: str,
    caller_agent_id: str,
    confirmed_target_id: str | None,
    verdict: ProvenanceVerdict,
) -> None:
    """Write the protected-gate refusal row to the audit ledger.

    A no-op when there is no audit repository (the ledger is optional;
    the refusal itself is still returned to the caller).

    The provenance fields come from the VERDICT — the same object the
    gate keyed on — so the row and the gate can never disagree about
    what was observed.
    """
    if audit is None:
        return
    audit.write_refusal(
        action=action,
        target=target_id,
        reason=reason,
        error_message=refusal,
        actor_instance_id=caller_instance_id,
        actor_agent_id=caller_agent_id,
        confirmed_target_id=confirmed_target_id,
        provenance_class=verdict.pclass or None,
        provenance_depth=verdict.depth or None,
        root_source=verdict.root_source,
        root_message_id=verdict.root_message_id,
    )


async def _resolve_target(manager: Any, instance_id: str) -> str:
    """Fuzzy-resolve an instance id (deferred import — see module doc)."""
    from .instance import _resolve_instance_id

    return await _resolve_instance_id(manager, instance_id)


# ═════════════════════════════════════════════════════════════════════
# D2(b) — the nudge completion watcher
# ═════════════════════════════════════════════════════════════════════
#
# Defect D2 (premature settle): an ``instance_nudge`` wrote an audit
# row and NOTHING ELSE. The commissioned work was invisible to the
# system's single visibility chokepoint (``dependency_watchers``), so
# the root gate (L3) and the attestation gate (R2) could not see that
# the mission had out-of-tree work in flight — the leader settled
# while the nudged worker was still running (the demonstrated fd302262
# shape: the mission settled 5m06s early).
#
# This helper mints the missing ``dependency_watchers`` row. It is a
# SIBLING of ``daemon/tools/instance.py::_register_child_completion_
# watcher`` — NOT a reuse of it. The existing helper tree-guards its
# target (``target.parent_id != sender`` → ``None``), which is
# correct for the parent→child send path but wrong here: a nudge
# targets ANY instance, INCLUDING out-of-tree workers. The nudge lane
# is exactly the case the tree guard excludes.
#
# The watcher fires through the EXISTING bus machinery:
#   * ``emit_terminal`` (task-keyed, exactly-once) fires it when the
#     watched task reaches a terminal state;
#   * ``emit_terminal_for_child_instance`` (the corrective path)
#     matches ``metadata.child_id`` across EVERY target — it does not
#     switch on ``kind``. The ``kind="nudge_complete"`` marker only
#     improves forensics.
#
# FAIL-OPEN: a failed mint degrades to today's behaviour (no watcher).
# The mint must never fail the nudge itself.


async def _register_nudge_completion_watcher(
    manager: Any,
    *,
    nudger_instance_id: str | None,
    worker_instance_id: str,
    message_id: str | None = None,
    lane: str = "enqueue",
    mission_id: str | None = None,
) -> bool:
    """Mint the ``dependency_watchers`` row for an ``instance_nudge``.

    The watcher wakes the NUDGER when the nudged worker reaches a
    terminal state. Without it the nudger is never told the work
    finished — the commissioned work is invisible to the gate stack.

    The watched SOURCE TASK must be a REAL task id: the bus's orphan
    sweep cancels any PENDING watcher whose ``source_task_id`` is not
    in the active task set (``running`` / ``pending`` / ``paused``),
    after a 30s grace window (worst case ~120s with the 90s sweep
    interval). A watcher minted on a made-up id is therefore cancelled
    and the wake-up is LOST.

    Two lanes, two real task ids:

    * ``enqueue`` — the nudge went through the durable enqueue path.
      The enqueue minted a real PENDING ``Task`` row synchronously in
      the same transaction as the ``MessageQueue`` row, keyed by the
      message id. Resolve it via ``_task_repo.get_by_message``.
    * ``injection`` — the nudge landed mid-turn in a live graph task
      (RAM FIFO — no MessageQueue row, no Task row). Key on the
      worker's live RUNNING task via
      ``_task_repo.find_running_by_instance``.

    Args:
        manager: The ``InstanceManager``.
        nudger_instance_id: The instance that issued the nudge (the
            revival target). ``None`` / empty → no mint (nobody to
            wake).
        worker_instance_id: The nudged instance (the watched child).
        message_id: The enqueued message id (``enqueue`` lane only).
        lane: ``"enqueue"`` or ``"injection"`` (forensics only).

    Returns:
        ``True`` when a watcher row was minted; ``False`` on any
        failure (FAIL-OPEN — the nudge itself still succeeded).
    """
    if not nudger_instance_id:
        logger.debug(
            "nudge completion watcher skipped — no caller instance id "
            "(worker=%s)",
            worker_instance_id[:8],
        )
        return False
    try:
        _task_repo = getattr(manager, "_task_repo", None)
        if _task_repo is None:
            logger.warning(
                "manager._task_repo is missing — cannot mint the nudge "
                "completion watcher (nudger=%s, worker=%s)",
                nudger_instance_id[:8],
                worker_instance_id[:8],
            )
            return False

        task = None
        if lane == "injection":
            # The injection lane has no MessageQueue row — key on the
            # worker's live RUNNING task.
            task = await asyncio.to_thread(
                _task_repo.find_running_by_instance, worker_instance_id
            )
        elif message_id:
            # The enqueue lane minted a real PENDING Task row
            # synchronously, keyed by the message id.
            task = await asyncio.to_thread(
                _task_repo.get_by_message, message_id
            )
        if task is None:
            logger.warning(
                "no %s task for the nudged worker — cannot mint the "
                "nudge completion watcher (nudger=%s, worker=%s)",
                lane,
                nudger_instance_id[:8],
                worker_instance_id[:8],
            )
            return False

        from ..services.dependency_bus import (
            FollowUp,
            get_dependency_bus,
        )

        _bus = get_dependency_bus()
        if _bus is None:
            logger.warning(
                "Bus singleton is None — bus wiring failure (no fallback)"
            )
            return False

        follow_up = FollowUp(
            target_instance_id=nudger_instance_id,
            message=(
                f"[dependency_bus] nudged instance {worker_instance_id} "
                f"completed for message {message_id}"
            ),
            source=f"internal_agent:{nudger_instance_id}",
            metadata={
                "kind": "nudge_complete",
                "child_id": worker_instance_id,
                "parent_id": nudger_instance_id,
                "message_id": message_id,
                # Proposal B PR1: the commission's mission rides the
                # free-form JSONB (NO schema change — proposal-b §2.1).
                # ``None`` absent-when-None keeps pre-PR1 payloads
                # byte-identical... the key IS present with a None value
                # here only when a mission exists upstream; keep the
                # payload literally unchanged when there is no mission.
                **({"mission_id": mission_id} if mission_id else {}),
            },
        )
        await _bus.watch(
            source_task_id=str(task.id),
            follow_up=follow_up,
        )
        logger.debug(
            "bus.watch registered (nudge): task=%s..., nudger=%s..., "
            "worker=%s..., lane=%s",
            str(task.id)[:8],
            nudger_instance_id[:8],
            worker_instance_id[:8],
            lane,
            extra={"completion_delivery_path": "bus"},
        )
        return True
    except Exception as exc:  # noqa: BLE001 — the mint is FAIL-OPEN
        logger.warning(
            "nudge completion watcher mint failed (nudger=%s, worker=%s, "
            "lane=%s): %s",
            (nudger_instance_id or "")[:8],
            worker_instance_id[:8],
            lane,
            exc,
        )
        return False


# ═════════════════════════════════════════════════════════════════════
# The factory — six act tools
# ═════════════════════════════════════════════════════════════════════
def create_instance_ops_tools(
    manager: Any,
    *,
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: str | None = None,
) -> list:
    """Build the six ``instance_ops`` tools for one instance/agent.

    Args:
        manager: The ``InstanceManager`` — every act wraps an existing
            daemon primitive through it.
        current_instance_id: The caller's instance id. The
            self-surgery refusal compares against it.
        agent_id: The caller's agent id — the layer-3 gate's input.
        agent_tag: The caller's version tag (unused; signature parity).

    Returns:
        A list of the six ``@tool``-decorated callables.
    """
    caller_instance_id = current_instance_id or ""
    caller_agent_id = agent_id or ""

    # ── instance_terminate ───────────────────────────────────────────
    class InstanceTerminateInput(BaseModel):
        """Input schema for instance_terminate."""

        id: Annotated[
            str,
            Field(
                description=(
                    "Target instance id (short form accepted). Required. "
                    "Acting on your own session is refused."
                )
            ),
        ]
        cascade: Annotated[
            bool,
            Field(
                default=True,
                description=(
                    "True (default) terminates the target AND its whole "
                    "cascade tree (the underlying service is total). "
                    "False means 'the target only, descendants stay "
                    "running' — that CREATES orphans, so it REFUSES with "
                    "reason=cascade-false-would-orphan-descendants unless "
                    "allow_orphan_descendants=True acknowledges it."
                ),
            ),
        ]
        reason: Annotated[
            str,
            Field(
                description=(
                    "REQUIRED, free text, stored verbatim in the audit "
                    "ledger. The health report's evidence feeds this."
                )
            ),
        ]
        dry_run: Annotated[
            bool,
            Field(
                default=True,
                description=(
                    "True (default) — the preview lists the target, the "
                    "full cascade tree, the protected determination, the "
                    "confirmation requirement, the budget state and the "
                    "orphan list, and mutates NOTHING. False applies the "
                    "terminate (audited, Tier-1 same-transaction)."
                ),
            ),
        ]
        confirmed_target_id: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "The named-target confirmation for a PROTECTED target "
                    "(D-17): must equal `id` VERBATIM. Anything else is "
                    "refused. Attributable and audited — the ledger "
                    "records it next to the provenance fields."
                ),
            ),
        ]
        allow_orphan_descendants: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Explicit acknowledgement for cascade=False on a "
                    "target with live descendants. When True the preview "
                    "AND the audit row NAME the descendants that will be "
                    "orphaned."
                ),
            ),
        ]

    @register_tool_category("instance_ops")
    @tool(args_schema=InstanceTerminateInput)
    async def instance_terminate(
        id: str,
        cascade: bool = True,
        reason: str = "",
        dry_run: bool = True,
        confirmed_target_id: str | None = None,
        allow_orphan_descendants: bool = False,
    ) -> str:
        """Terminate an instance (audited, gated, dry-run default)."""
        label = "INSTANCE_TERMINATE"
        if not _is_instance_ops_authorized(caller_agent_id):
            return _error("instance_ops operation not permitted for this agent")
        if not _resolve_instance_ops_enabled():
            return KILL_SWITCH_REFUSAL
        audit = _audit_repo_for(manager)

        if _reason_missing(reason):
            return _refusal(
                label, "reason-required", "the `reason` argument is required"
            )
        try:
            target_id = await _resolve_target(manager, id)
        except ValueError as exc:
            if audit is not None:
                audit.write_refusal(
                    action="instance_terminate",
                    target=str(id),
                    reason=reason,
                    error_message=_refusal(label, "instance-not-found", str(exc)),
                    actor_instance_id=caller_instance_id,
                    actor_agent_id=caller_agent_id,
                    confirmed_target_id=confirmed_target_id,
                )
            return _refusal(label, "instance-not-found", str(exc))
        if target_id == caller_instance_id:
            return _refusal(
                label,
                "self-surgery-refused",
                "an act tool never operates on its own session",
            )

        info = await asyncio.to_thread(_target_state, manager, target_id)
        if info is None:
            return _refusal(
                label, "instance-not-found", f"no instance row for {target_id!r}"
            )

        # ── the min-age rail (D-16) ─────────────────────────────────
        age = _age_seconds(info.get("created_at"))
        if age is not None and age < TARGET_MIN_AGE_SECONDS:
            return _refusal(
                label,
                "target-too-young",
                (
                    f"the target is {age}s old (min "
                    f"{TARGET_MIN_AGE_SECONDS}s). Terminate-the-thing-that-"
                    "just-started is the footgun this rail exists to kill."
                ),
            )

        # ── the protected determination (the gate CALL moves below) ──
        # F6 (D-8): the DETERMINATION stays here (the budget rail and
        # the preview payload both need it); the gate CALL moves down to
        # guard 10, AFTER the budget/cascade rails and immediately before
        # the ``dry_run`` fork.
        protected = _is_protected_target(manager, info)
        verdict = _provenance_for(manager, caller_instance_id)

        # ── the budget rails (D-16) ─────────────────────────────────
        budget_state = {
            "committed_terminates_24h": 0,
            "protected_terminates_24h": 0,
            "fleet_budget": TERMINATE_BUDGET_24H,
            "protected_budget": PROTECTED_TERMINATE_BUDGET_24H,
            "remaining": TERMINATE_BUDGET_24H,
        }
        budget_refusal = None
        if audit is not None:
            protected_count = audit.count_committed_protected_terminates(hours=24)
            fleet_count = audit.count_committed_terminates(hours=24)
            budget_state.update(
                {
                    "committed_terminates_24h": fleet_count,
                    "protected_terminates_24h": protected_count,
                    "remaining": max(
                        0, TERMINATE_BUDGET_24H - fleet_count
                    ),
                }
            )
            if protected and protected_count >= PROTECTED_TERMINATE_BUDGET_24H:
                budget_refusal = _refusal(
                    label,
                    "terminate-budget-exceeded",
                    (
                        f"the protected-target budget is "
                        f"{PROTECTED_TERMINATE_BUDGET_24H}/24h and "
                        f"{protected_count} protected terminate(s) are "
                        "already committed."
                    ),
                )
            elif fleet_count >= TERMINATE_BUDGET_24H:
                budget_refusal = _refusal(
                    label,
                    "terminate-budget-exceeded",
                    (
                        f"the terminate budget is "
                        f"{TERMINATE_BUDGET_24H}/24h fleet-wide and "
                        f"{fleet_count} are already committed."
                    ),
                )
        if budget_refusal is not None:
            if audit is not None:
                audit.write_refusal(
                    action="instance_terminate",
                    target=target_id,
                    reason=reason,
                    error_message=budget_refusal,
                    actor_instance_id=caller_instance_id,
                    actor_agent_id=caller_agent_id,
                    confirmed_target_id=confirmed_target_id,
                    provenance_class=verdict.pclass or None,
                    provenance_depth=verdict.depth or None,
                    root_source=verdict.root_source,
                    root_message_id=verdict.root_message_id,
                )
            return budget_refusal

        # ── the cascade / orphan determination (D-5) ────────────────
        children = info.get("children") or []
        live_descendants = [
            child_id
            for child_id in children
            if _descendant_is_live(manager, child_id)
        ]
        orphans: list[str] = []
        if not cascade and live_descendants:
            if not allow_orphan_descendants:
                refusal = _refusal(
                    label,
                    "cascade-false-would-orphan-descendants",
                    (
                        f"cascade=False would orphan "
                        f"{len(live_descendants)} live descendant(s): "
                        f"{', '.join(live_descendants[:10])}"
                        + (
                            " …" if len(live_descendants) > 10 else ""
                        )
                        + ". Pass allow_orphan_descendants=True to "
                        "acknowledge, or cascade=True to take the tree."
                    ),
                )
                if audit is not None:
                    audit.write_refusal(
                        action="instance_terminate",
                        target=target_id,
                        reason=reason,
                        error_message=refusal,
                        actor_instance_id=caller_instance_id,
                        actor_agent_id=caller_agent_id,
                        before={"status": info.get("status"), "protected": protected},
                    )
                return refusal
            orphans = list(live_descendants)

        # ── guard 10: the PROTECTED GATE — SPLIT at the dry_run fork ──
        # F6 / D-8 (the leader-adjudicated MINIMAL reading):
        #
        #   * the CONFIRMATION conditions gate BOTH paths — an unconfirmed
        #     preview on a protected target still refuses (the
        #     confirmation is agent-satisfiable: the caller can name the
        #     target);
        #   * ONLY the user-provenance condition is APPLY-ONLY — it
        #     needs a live user-origin window on this turn, which the
        #     caller cannot self-satisfy; on the preview path it renders
        #     as DATA (``act_gate_would_pass``) instead of a refusal.
        #
        # The gate is PURE — it returns a string and writes NOTHING. The
        # refusal ROW is written here, at the caller, only on the real
        # refusal path (compute/write separation).
        gate_refusal = None
        gate_token = None
        if protected:
            # The gate returns ``(refusal, token)`` STRUCTURALLY — the
            # token is the constant that minted the refusal, never a
            # re-parse of the message body.
            gate_refusal, gate_token = _protected_gate(
                target_id=target_id,
                confirmed_target_id=confirmed_target_id,
                verdict=verdict,
                label=label,
            )
            is_confirmation_refusal = gate_token in GATE_CONFIRMATION_TOKENS
            if gate_refusal is not None and (
                not dry_run or is_confirmation_refusal
            ):
                # The real refusal path — a refusal row is written and
                # the refusal is returned to the caller.
                _write_gate_refusal(
                    audit,
                    action="instance_terminate",
                    target_id=target_id,
                    reason=reason,
                    refusal=gate_refusal,
                    caller_instance_id=caller_instance_id,
                    caller_agent_id=caller_agent_id,
                    confirmed_target_id=confirmed_target_id,
                    verdict=verdict,
                )
                return gate_refusal

        before_snapshot = {
            "status": info.get("status"),
            "agent_id": info.get("agent_id"),
            "protected": protected,
            "cascade": cascade,
            "orphans": orphans,
        }
        prov_fields = {
            "confirmed_target_id": confirmed_target_id,
            "provenance_class": verdict.pclass or None,
            "provenance_depth": verdict.depth or None,
            "root_source": verdict.root_source,
            "root_message_id": verdict.root_message_id,
        }

        if dry_run:
            preview_row_id = None
            if audit is not None:
                preview_row_id = audit.write_preview(
                    action="instance_terminate",
                    target=target_id,
                    reason=reason,
                    before=before_snapshot,
                    actor_instance_id=caller_instance_id,
                    actor_agent_id=caller_agent_id,
                    **prov_fields,
                )
            payload = {
                "dry_run": True,
                "action": "instance_terminate",
                "target": target_id,
                "agent_id": info.get("agent_id"),
                "cascade": cascade,
                "cascade_tree": children,
                "protected": protected,
                "protected_confirmation_required": protected,
                "confirmation": (
                    "provided"
                    if protected and confirmed_target_id == target_id
                    else ("required" if protected else "not-required")
                ),
                "provenance": {
                    "ok": verdict.ok,
                    "pclass": verdict.pclass,
                    "depth": verdict.depth,
                    "root_source": verdict.root_source,
                    "root_message_id": verdict.root_message_id,
                    "detail": verdict.detail,
                },
                "budget": budget_state,
                "orphans_would_be_created": orphans,
                "orphan_list": orphans,
                "target_age_seconds": age,
                "audit_preview_row": preview_row_id,
                # F6: what APPLY would require. The gate verdict is
                # rendered as DATA — TOKENS ONLY, never the raw refusal
                # string (it embeds the target id and the caller's own
                # confirmation value). A confirmed preview that cannot
                # pass the provenance leg reports
                # ``act_gate_would_pass.ok == false`` with the reason
                # token — the operator cannot self-satisfy that without
                # a genuine user turn.
                "act_gate": _build_act_gate_payload(
                    protected=protected,
                    gate_refusal=gate_refusal,
                    gate_token=gate_token,
                ),
                "act_gate_would_pass": {
                    "ok": gate_refusal is None,
                    "reason": gate_token,
                },
                "note": "NO mutation happened (dry-run)",
            }
            return json.dumps(payload, default=str) + "\nNO mutation happened (dry-run)"

        # ── apply (Tier-1: act + audit row in ONE transaction) ─────
        sink_error: list[str] = []

        def _audit_sink(session) -> None:
            if audit is None:
                sink_error.append("audit ledger unavailable")
                raise RuntimeError("instance_ops audit ledger unavailable")
            audit.write_audit_and_act(
                session,
                action="instance_terminate",
                target=target_id,
                reason=reason,
                before=before_snapshot,
                outcome="committed",
                actor_instance_id=caller_instance_id,
                actor_agent_id=caller_agent_id,
                **prov_fields,
            )

        try:
            result = await manager.terminate_instance(
                target_id,
                audit_sink=_audit_sink,
            )
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            logger.exception("instance_terminate act failed")
            return _error(
                f"instance_terminate failed and was rolled back with its "
                f"audit row: {type(exc).__name__}"
            )
        if sink_error:
            return _error("instance_terminate refused: " + sink_error[0])
        if not result:
            if audit is not None:
                audit.write_refusal(
                    action="instance_terminate",
                    target=target_id,
                    reason=reason,
                    error_message="service reported not-terminated (missing or already terminal)",
                    actor_instance_id=caller_instance_id,
                    actor_agent_id=caller_agent_id,
                    **prov_fields,
                )
            return _error(
                "instance_terminate: the service reported the target was "
                "not terminated (missing or already terminal)"
            )
        return json.dumps(
            {
                "terminated": True,
                "action": "instance_terminate",
                "target": target_id,
                "cascade": cascade,
                "orphans_created": orphans,
                "audit": "committed (Tier-1, same transaction as the act)",
            },
            default=str,
        )

    instance_terminate._full_doc_ = """Terminate an instance — the DESTRUCTIVE act (audited, gated).

`dry_run` defaults to TRUE: the default call is a preview that mutates
nothing and lists exactly what would happen.

The gates, in order:
1. `reason` is required (free text, stored verbatim in the ledger).
2. The target must exist (short form accepted; fuzzy-matched).
3. Never your own session (`self-surgery-refused`).
4. A target younger than 600s refuses (`target-too-young`).
5. The budget: at most 3 committed terminates per 24h fleet-wide, and
   at most 1 per 24h for protected targets (`terminate-budget-exceeded`).
6. `cascade=False` on a target with live descendants refuses
   (`cascade-false-would-orphan-descendants`) unless
   `allow_orphan_descendants=True` — and then the preview AND the audit
   row both NAME the orphans.
7. A PROTECTED target — a user-facing chat session, the leader, or an
   instance marked protected — additionally requires BOTH:
   `confirmed_target_id == id` (verbatim) (BOTH paths — a preview still
   names its target) AND a live user-origin provenance window (APPLY
   ONLY — a dry-run preview reports `act_gate_would_pass` instead of
   requiring user provenance). The window's shape (minted on this
   instance vs inherited through a delegation chain) is recorded in the
   audit ledger but is NOT a gate input — a live window of either shape
   is admitted.

F6-iv (precedence): the budget and cascade rails run BEFORE the
protected gate, so an exhausted budget or a `cascade=False` refusal on
a protected target yields THAT token instead of
`protected-target-confirmation-required`. Both are refusals — no
authority is granted either way.

F6 x F11 (read this before treating a preview as "one more call from
termination"): a confirmed preview on a protected target reports
`act_gate_would_pass.reason == "no-user-provenance"` when this turn has
no live user-origin window. The operator CANNOT self-satisfy that
condition — it needs a genuine user turn (or a delegated chain from an
instance that holds one). The preview REPORTS the requirement; it does
not grant it.

The audit row is written in the SAME transaction as the terminate
(Tier-1): if the audit write fails, the terminate rolls back. A
destructive act that cannot be audited never happens.

Kill switch: `ENSEMBLE_INSTANCE_OPS_ENABLED=0` disables this act (the
reads stay available).

Args:
    id: Target instance id. Required.
    cascade: True (default) takes the whole cascade tree. False means
        "target only" — and refuses when that would orphan live
        descendants.
    reason: REQUIRED free text; stored verbatim.
    dry_run: True (default) = preview, no mutation.
    confirmed_target_id: The named-target confirmation for a protected
        target — must equal `id` verbatim.
    allow_orphan_descendants: Explicit acknowledgement for
        `cascade=False` with live descendants.

Returns:
    dry_run=True: the verdict block (target, cascade tree, protected
    determination, confirmation requirement, budget state, orphan list)
    ending with `NO mutation happened (dry-run)`.
    dry_run=False: the committed result.

Examples:
    instance_terminate(id="abc123", reason="wedged 6h, health report #4")
    instance_terminate(id="abc123", reason="...", dry_run=False)
    instance_terminate(id="root1", reason="...", cascade=False,
                       allow_orphan_descendants=True)
"""

    def _descendant_is_live(manager_ref: Any, child_id: str) -> bool:
        """A descendant is LIVE when its status is non-terminal."""
        child_info = _target_state(manager_ref, child_id)
        if child_info is None:
            return False
        terminal = {"completed", "terminated", "error", "failed"}
        return child_info.get("status") not in terminal

    # ── instance_reclaim ─────────────────────────────────────────────
    class InstanceReclaimInput(BaseModel):
        """Input schema for instance_reclaim."""

        id: Annotated[str, Field(description="Target instance id (short form accepted). Required.")]
        message: Annotated[
            str,
            Field(
                description=(
                    "REQUIRED — the revival message delivered to the "
                    "revived instance. The message itself IS the revival "
                    "record: it lands in the revived instance's checkpoint "
                    "and is the durable evidence of what was revived and why."
                )
            ),
        ]
        reason: Annotated[str, Field(description="REQUIRED free text; stored verbatim in the ledger.")]
        dry_run: Annotated[
            bool,
            Field(
                default=True,
                description=(
                    "True (default) — the preview shows the target's "
                    "terminal status and the message; no mutation. False "
                    "revives the target by documented message (audited, "
                    "Tier-2 write-ahead)."
                )
            ),
        ]

    @register_tool_category("instance_ops")
    @tool(args_schema=InstanceReclaimInput)
    async def instance_reclaim(
        id: str, message: str = "", reason: str = "", dry_run: bool = True
    ) -> str:
        """Revive a TERMINAL instance with a documented message (audited)."""
        label = "INSTANCE_RECLAIM"
        if not _is_instance_ops_authorized(caller_agent_id):
            return _error("instance_ops operation not permitted for this agent")
        if not _resolve_instance_ops_enabled():
            return KILL_SWITCH_REFUSAL
        audit = _audit_repo_for(manager)
        if _reason_missing(reason):
            return _refusal(label, "reason-required", "the `reason` argument is required")
        if not isinstance(message, str) or not message.strip():
            return _refusal(label, "message-required", "the `message` argument is required")
        try:
            target_id = await _resolve_target(manager, id)
        except ValueError as exc:
            return _refusal(label, "instance-not-found", str(exc))
        if target_id == caller_instance_id:
            return _refusal(label, "self-surgery-refused", "an act tool never operates on its own session")
        info = await asyncio.to_thread(_target_state, manager, target_id)
        if info is None:
            return _refusal(label, "instance-not-found", f"no instance row for {target_id!r}")
        terminal = {"completed", "terminated", "error", "failed"}
        if info.get("status") not in terminal:
            # F9: the CANONICAL corrected copy (ops-side §6.2 — the single
            # source). The old copy recommended ``instance_nudge`` — and a
            # nudge on a graph-less target DISPATCHES a durable,
            # context-free turn (with a source that POPS the target's
            # provenance windows): exactly what an idle, evidence-free husk
            # must never receive. The copy now names the real paths, keeps
            # the D-20 provenance (reclaim is terminal-only BY DESIGN) and
            # states the hazard in one clause.
            return _refusal(
                label,
                "target-not-terminal",
                (
                    f"{target_id!r} is {info.get('status')!r}, not terminal. "
                    "instance_reclaim is TERMINAL-ONLY "
                    "(D-20). For a non-terminal target: run instance_terminate "
                    "with dry_run=True "
                    "first if it is a genuine wedge (then apply with "
                    "confirmed_target_id when the "
                    "target is protected — WITHOUT a live user-origin chain, "
                    "direct or delegated, "
                    "apply refuses no-user-provenance); use instance_health / "
                    "instance_get to read "
                    "its state; use instance_mark(marker='park') only for a "
                    "deliberate hold on a "
                    "pausable target. Do NOT nudge an idle, evidence-free "
                    "instance — a nudge "
                    "DISPATCHES a context-free turn."
                ),
            )
        if dry_run:
            preview_row = None
            if audit is not None:
                preview_row = audit.write_preview(
                    action="instance_reclaim",
                    target=target_id,
                    reason=reason,
                    before={"status": info.get("status")},
                    actor_instance_id=caller_instance_id,
                    actor_agent_id=caller_agent_id,
                )
            return (
                json.dumps(
                    {
                        "dry_run": True,
                        "action": "instance_reclaim",
                        "target": target_id,
                        "status": info.get("status"),
                        "message": message,
                        "audit_preview_row": preview_row,
                        "note": "NO mutation happened (dry-run)",
                    },
                    default=str,
                )
                + "\nNO mutation happened (dry-run)"
            )
        # Tier-2 write-ahead: the intent row FIRST, the act, then settle.
        row_id = None
        if audit is not None:
            row_id = audit.write_audit_ahead(
                action="instance_reclaim",
                target=target_id,
                reason=reason,
                before={"status": info.get("status")},
                actor_instance_id=caller_instance_id,
                actor_agent_id=caller_agent_id,
            )
        try:
            result = await manager.enqueue_message(
                target_id, message, source=f"instance_ops:{caller_instance_id or 'unknown'}"
            )
            ok = bool(getattr(result, "ok", True)) if not isinstance(result, dict) else bool(result.get("ok", True))
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            # AC-9b: the row is deliberately LEFT at "pending" — an
            # unexpected mid-act failure has an UNKNOWN outcome (the act
            # may have partially happened), and "pending" is the honest
            # unknown-outcome state the reconciliation surface surfaces.
            logger.exception("instance_reclaim act failed (audit row left pending)")
            return _error(f"instance_reclaim failed: {type(exc).__name__}")
        if audit is not None and row_id:
            audit.settle_audit(row_id, "committed", after={"revived": bool(ok)})
        return json.dumps(
            {
                "revived": bool(ok),
                "action": "instance_reclaim",
                "target": target_id,
                "message_delivered": message,
                "audit": "committed (Tier-2 write-ahead)",
            },
            default=str,
        )

    instance_reclaim._full_doc_ = """Revive a TERMINAL instance — terminal-only, reason-required,
revive-by-documented-message.

A message to a COMPLETED / TERMINATED / ERROR / FAILED instance flips
it to RUNNING and reuses its checkpoint (the daemon's revive-on-send
primitive). The message IS the revival record: it lands in the revived
instance's checkpoint, which is the durable evidence of what was
revived and why — independent of the audit row.

NON-terminal targets refuse with `target-not-terminal` (that is what
distinguishes reclaim from `instance_nudge`, which delivers to any
target). Your own session refuses (`self-surgery-refused`).

F9: a nudge to an idle, evidence-free target dispatches a context-free
turn — it is NOT a diagnostic. On a graph-less target the nudge falls
through to a durable enqueue: a bare new turn, with no context and no
evidence, whose dispatch source also clears the target's provenance
windows. Use `instance_health` / `instance_get` to READ a target's
state, and `instance_terminate` (dry-run first) to remediate a genuine
wedge. The `target-not-terminal` refusal names those paths and states
the hazard.

Audit tier: 2 (write-ahead) — the intent row is written FIRST with
`outcome="pending"`, the act runs, the row is settled. A crash mid-act
leaves a durable `pending` row.

Args:
    id: Target instance id. Required.
    message: REQUIRED — the revival message delivered to the target.
    reason: REQUIRED free text; stored verbatim.
    dry_run: True (default) = preview, no mutation.

Examples:
    instance_reclaim(id="abc123", message="Resume: the wedge is cleared, report.", reason="recovery after fix")
"""

    # ── instance_nudge ───────────────────────────────────────────────
    class InstanceNudgeInput(BaseModel):
        """Input schema for instance_nudge."""

        id: Annotated[str, Field(description="Target instance id (short form accepted). Required.")]
        message: Annotated[
            str,
            Field(
                description=(
                    "REQUIRED — the message to inject. Delivered into the "
                    "target's live turn when one is running, otherwise "
                    "durably enqueued."
                )
            ),
        ]
        reason: Annotated[str, Field(description="REQUIRED free text; stored verbatim in the ledger.")]
        dry_run: Annotated[
            bool,
            Field(
                default=True,
                description=(
                    "True (default) — preview only. Note: a nudge to a "
                    "TERMINAL instance REVIVES it (revive-on-send); the "
                    "preview names that when the target is terminal."
                )
            ),
        ]

    @register_tool_category("instance_ops")
    @tool(args_schema=InstanceNudgeInput)
    async def instance_nudge(
        id: str, message: str = "", reason: str = "", dry_run: bool = True
    ) -> str:
        """Inject a message into any instance (audited)."""
        label = "INSTANCE_NUDGE"
        if not _is_instance_ops_authorized(caller_agent_id):
            return _error("instance_ops operation not permitted for this agent")
        if not _resolve_instance_ops_enabled():
            return KILL_SWITCH_REFUSAL
        audit = _audit_repo_for(manager)
        if _reason_missing(reason):
            return _refusal(label, "reason-required", "the `reason` argument is required")
        if not isinstance(message, str) or not message.strip():
            return _refusal(label, "message-required", "the `message` argument is required")
        try:
            target_id = await _resolve_target(manager, id)
        except ValueError as exc:
            return _refusal(label, "instance-not-found", str(exc))
        info = await asyncio.to_thread(_target_state, manager, target_id)
        if info is None:
            return _refusal(label, "instance-not-found", f"no instance row for {target_id!r}")
        terminal = {"completed", "terminated", "error", "failed"}
        would_revive = info.get("status") in terminal
        if dry_run:
            preview_row = None
            if audit is not None:
                preview_row = audit.write_preview(
                    action="instance_nudge",
                    target=target_id,
                    reason=reason,
                    before={"status": info.get("status")},
                    actor_instance_id=caller_instance_id,
                    actor_agent_id=caller_agent_id,
                )
            return (
                json.dumps(
                    {
                        "dry_run": True,
                        "action": "instance_nudge",
                        "target": target_id,
                        "status": info.get("status"),
                        "message": message,
                        "would_revive": would_revive,
                        "audit_preview_row": preview_row,
                        "note": "NO mutation happened (dry-run)",
                    },
                    default=str,
                )
                + "\nNO mutation happened (dry-run)"
            )
        row_id = None
        if audit is not None:
            row_id = audit.write_audit_ahead(
                action="instance_nudge",
                target=target_id,
                reason=reason,
                before={"status": info.get("status")},
                actor_instance_id=caller_instance_id,
                actor_agent_id=caller_agent_id,
            )
        try:
            injected = False
            # D2(b) — the enqueue lane's message id, captured for the
            # completion-watcher mint below. It was previously discarded
            # (``enqueue_message``'s result was not read at all).
            enqueued_message_id: str | None = None
            graph_task = getattr(manager, "_graph_tasks", {}).get(target_id) \
                if isinstance(getattr(manager, "_graph_tasks", None), dict) else None
            if graph_task is not None and not graph_task.done():
                manager.set_injection(
                    target_id, message, source=f"instance_ops:{caller_instance_id or 'unknown'}"
                )
                injected = True
            else:
                enqueue_result = await manager.enqueue_message(
                    target_id, message, source=f"instance_ops:{caller_instance_id or 'unknown'}"
                )
                enqueued_message_id = getattr(enqueue_result, "message_id", None)
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            # AC-9b: left at "pending" — unknown outcome (see reclaim).
            logger.exception("instance_nudge act failed (audit row left pending)")
            return _error(f"instance_nudge failed: {type(exc).__name__}")
        if audit is not None and row_id:
            audit.settle_audit(row_id, "committed", after={"lane": "injection" if injected else "enqueue"})

        # ── Proposal B PR1 — instance_nudge mission records (lane 4) ──
        # Minted ADJACENT to the committed audit row (record-side only;
        # FAIL-OPEN — a record failure never fails the nudge).
        # NOTE (documented deviation from proposal-b §2.1's "same
        # transaction as the audit row"): the nudge's audit is Tier-2
        # WRITE-AHEAD — ``write_audit_ahead`` and ``settle_audit`` are
        # TWO transactions today, so "the audit transaction" is not a
        # single unit to ride. The commission is minted here instead:
        # after the act is KNOWN-committed, so the commission never
        # records an act that failed, and dry-run previews mint NOTHING
        # (preview rows are not real acts). Preview/preview-run rows
        # are excluded upstream by the ``dry_run`` early return.
        _mission_record_service = getattr(
            manager, "mission_record_service", None
        )
        _nudge_mission_id: str | None = None
        if caller_instance_id:
            # The nudger's mission (resolve-or-mint ladder — a bare
            # maintenance-context nudger mints its OWN epoch here,
            # ``founding_source='instance_nudge'``: no commissioning act
            # is ever unattributed).
            _nudge_mission_id = await asyncio.to_thread(
                resolve_or_mint_fail_open,
                _mission_record_service,
                caller_instance_id,
                "instance_nudge",
                founding_source="instance_nudge",
            )
            # The COMMISSION row: mission = the nudger's mission,
            # commissioner = the nudger, worker = the target. D4(ii)
            # mint-path validation: a missing worker row RAISES inside
            # the service; the wrapper catches + logs (the nudge still
            # succeeded — a commission that cannot be validated is not
            # recorded, and the miss is LOUD in the logs).
            await asyncio.to_thread(
                mint_commission_fail_open,
                _mission_record_service,
                _nudge_mission_id,
                commissioner_instance_id=caller_instance_id,
                worker_instance_id=target_id,
                lane="instance_nudge",
            )
        if would_revive:
            # The target is TERMINAL: additionally MINT the revive epoch
            # ON THE TARGET (parent chain; the nudge is what commissioned
            # the revived work, so the nudger is the epoch's
            # commissioner).
            await asyncio.to_thread(
                mint_revive_epoch_fail_open,
                _mission_record_service,
                target_id,
                commissioner_instance_id=caller_instance_id,
                context_label="instance_nudge_terminal",
            )

        # D2(b) — mint the completion watcher AFTER the audit settle, so
        # the committed audit row is durable before the watcher exists.
        # FAIL-OPEN: a failed mint degrades to today's behaviour (no
        # watcher) and never fails the nudge. The helper has its own
        # guard; this call-site guard is the belt-and-braces half (a
        # mint failure must NEVER surface as a tool error).
        try:
            await _register_nudge_completion_watcher(
                manager,
                nudger_instance_id=caller_instance_id,
                worker_instance_id=target_id,
                message_id=enqueued_message_id,
                lane="injection" if injected else "enqueue",
                # Proposal B PR1: the commission's mission rides the
                # watcher metadata (free-form JSONB key — no schema
                # change).
                mission_id=_nudge_mission_id,
            )
        except Exception as mint_exc:  # noqa: BLE001 — fail-open producer
            logger.warning(
                "nudge completion watcher mint failed (nudger=%s, "
                "worker=%s): %s",
                (caller_instance_id or "")[:8],
                target_id[:8],
                mint_exc,
            )
        return json.dumps(
            {
                "delivered": True,
                "action": "instance_nudge",
                "target": target_id,
                "lane": "injection" if injected else "enqueue",
                "revived": would_revive,
                "audit": "committed (Tier-2 write-ahead)",
            },
            default=str,
        )

    instance_nudge._full_doc_ = """Inject a message into ANY instance (not just a job root).

Wraps the daemon's existing message-delivery primitives: a LIVE target
(a running turn) receives a RAM injection that lands in its current
turn; anything else gets a durable enqueue. A send to a TERMINAL
instance REVIVES it (revive-on-send) — the preview names that when the
target is terminal.

F9: for an idle, never-dispatched instance a nudge creates a NEW,
context-free turn — no context, no evidence, and a dispatch source that
clears the target's provenance windows. It is a DELIVERY primitive, not a
diagnostic. To
DIAGNOSE a target prefer `instance_health` (or `instance_get`); to
REMEDIATE a genuine wedge prefer `instance_terminate` with
`dry_run=True` first, then apply with `confirmed_target_id`.

Args:
    id: Target instance id. Required.
    message: REQUIRED — the message to inject.
    reason: REQUIRED free text; stored verbatim.
    dry_run: True (default) = preview, no mutation.

Audit tier: 2 (write-ahead).

Examples:
    instance_nudge(id="abc123", message="Status check — report your state.", reason="wedge probe")
"""

    # ── instance_mark ────────────────────────────────────────────────
    class InstanceMarkInput(BaseModel):
        """Input schema for instance_mark."""

        id: Annotated[str, Field(description="Target instance id (short form accepted). Required.")]
        marker: Annotated[
            str,
            Field(
                description=(
                    "One of 'park' | 'protect' | 'suspect'. park writes the "
                    "deliberate-pause tombstone (the release path defers to "
                    "it); protect adds the dynamic protection layer "
                    "(idempotent refusal on a repeat); suspect is a "
                    "non-blocking annotation. Anything else is refused with "
                    "reason=invalid-marker. There is NO unprotect."
                )
            ),
        ]
        note: Annotated[
            str | None,
            Field(
                default=None,
                description="Optional free-text annotation stored with the marker."
            ),
        ]
        reason: Annotated[str, Field(description="REQUIRED free text; stored verbatim in the ledger.")]
        dry_run: Annotated[
            bool,
            Field(
                default=True,
                description=(
                    "True (default) — preview only. False writes the marker "
                    "(audited: protect/suspect are Tier-1 same-transaction; "
                    "park is Tier-2 write-ahead)."
                )
            ),
        ]

    @register_tool_category("instance_ops")
    @tool(args_schema=InstanceMarkInput)
    async def instance_mark(
        id: str,
        marker: str = "",
        note: str | None = None,
        reason: str = "",
        dry_run: bool = True,
    ) -> str:
        """Write a park / protect / suspect marker (audited)."""
        label = "INSTANCE_MARK"
        if not _is_instance_ops_authorized(caller_agent_id):
            return _error("instance_ops operation not permitted for this agent")
        if not _resolve_instance_ops_enabled():
            return KILL_SWITCH_REFUSAL
        audit = _audit_repo_for(manager)
        if _reason_missing(reason):
            return _refusal(label, "reason-required", "the `reason` argument is required")
        if marker not in VALID_MARKERS:
            return _refusal(
                label,
                "invalid-marker",
                f"marker must be one of {', '.join(VALID_MARKERS)} (got {marker!r})",
            )
        try:
            target_id = await _resolve_target(manager, id)
        except ValueError as exc:
            return _refusal(label, "instance-not-found", str(exc))
        if target_id == caller_instance_id:
            return _refusal(label, "self-surgery-refused", "an act tool never operates on its own session")
        info = await asyncio.to_thread(_target_state, manager, target_id)
        if info is None:
            return _refusal(label, "instance-not-found", f"no instance row for {target_id!r}")

        metadata = info.get("metadata") or {}
        # F7: ONE home for the protection determination. ``already_protected``
        # now means PROTECTED (not "marked") — a leader target with no
        # marker previews as protected, and the reasons are echoed so the
        # operator can SEE why.
        protection_reasons = _protection_reasons(manager, info)
        already_protected = bool(protection_reasons)
        has_protect_marker = "marker" in protection_reasons
        if marker == "protect" and has_protect_marker:
            # F7: the refusal is scoped to the DYNAMIC marker layer only.
            # The token, the reason and the text are byte-identical for
            # the dynamic case (the same marker already present — an
            # idempotent no-op). A STATICALLY protected target with no
            # marker is NOT this case: protection is monotone, so the
            # operator is allowed to record the intent (see the
            # succeed-with-disclosure path below).
            return _refusal(
                label,
                "marker-already-set",
                f"{target_id!r} already carries a protect marker",
            )

        before_snapshot = {
            "status": info.get("status"),
            "marker": marker,
            # F7: the disclosure — the protection layers that apply are
            # recorded in the ledger row, not just in the response.
            "protection_reasons": protection_reasons,
        }
        if dry_run:
            preview_row = None
            if audit is not None:
                preview_row = audit.write_preview(
                    action="instance_mark",
                    target=target_id,
                    reason=reason,
                    before=before_snapshot,
                    actor_instance_id=caller_instance_id,
                    actor_agent_id=caller_agent_id,
                )
            return (
                json.dumps(
                    {
                        "dry_run": True,
                        "action": "instance_mark",
                        "target": target_id,
                        "marker": marker,
                        "note": note,
                        "already_protected": already_protected,
                        # F7: WHY the target is protected (the three
                        # closed reasons). Preview-only by design — the
                        # apply response carries the same echo when the
                        # disclosure applies.
                        "protection_reasons": protection_reasons,
                        "audit_preview_row": preview_row,
                        "note_line": "NO mutation happened (dry-run)",
                    },
                    default=str,
                )
                + "\nNO mutation happened (dry-run)"
            )

        if marker == "park":
            # Tier-2 (leader decision W3): the deliberate-pause machinery
            # is a live subsystem fixed in v0.26.1; its writer is
            # fail-open by design and takes NO session. Do NOT thread a
            # session into it.
            from ..services.question_pause_set import mark_deliberate_pause_override

            row_id = None
            if audit is not None:
                row_id = audit.write_audit_ahead(
                    action="instance_mark",
                    target=target_id,
                    reason=reason,
                    before=before_snapshot,
                    actor_instance_id=caller_instance_id,
                    actor_agent_id=caller_agent_id,
                )
            try:
                wrote = await mark_deliberate_pause_override(manager, target_id)
            except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
                # AC-9b: left at "pending" — unknown outcome (see reclaim).
                logger.exception("instance_mark(park) act failed (audit row left pending)")
                return _error(f"instance_mark(park) failed: {type(exc).__name__}")
            if audit is not None and row_id:
                audit.settle_audit(
                    row_id, "committed" if wrote else "error",
                    error_message=None if wrote else "deliberate-pause writer returned False",
                )
            if not wrote:
                return _error(
                    "instance_mark(park): the deliberate-pause writer "
                    "declined (the target may not be pausable in its "
                    "current state)"
                )
            return json.dumps(
                {
                    "marked": True,
                    "action": "instance_mark",
                    "target": target_id,
                    "marker": "park",
                    "audit": "committed (Tier-2 write-ahead)",
                    # F10: the committed ledger row id — the join key
                    # between the preview and the apply (``None`` only
                    # when the ledger was unavailable).
                    "audit_row_id": row_id,
                    "already_protected": already_protected,
                    "protection_reasons": protection_reasons,
                },
                default=str,
            )

        # protect / suspect — Tier-1: the marker write and the audit row
        # commit TOGETHER in ONE transaction.
        key = PROTECT_MARKER_KEY if marker == "protect" else SUSPECT_MARKER_KEY
        value = {
            ("protected" if marker == "protect" else "suspected"): True,
            "note": note,
            "marked_by": caller_instance_id or None,
            "marked_at": datetime.now(timezone.utc).isoformat(),
        }
        repo = getattr(manager, "_instance_repository", None)
        if repo is None:
            return _error("instance_ops: instance repository unavailable")
        from sqlmodel import Session as _Session

        try:
            with _Session(manager.engine) as session:
                if audit is None:
                    raise RuntimeError("instance_ops audit ledger unavailable")

                def _act(s):
                    repo.set_metadata(target_id, key, value, session=s)

                # F10: capture the committed row id. ``write_audit_and_act``
                # returns the row (``repository.py``) — the id is a
                # client-side default, so it is valid before the commit
                # and safe to echo after it.
                row_id = audit.write_audit_and_act(
                    session,
                    action="instance_mark",
                    target=target_id,
                    reason=reason,
                    before=before_snapshot,
                    after={"marker": marker, "key": key},
                    outcome="committed",
                    actor_instance_id=caller_instance_id,
                    actor_agent_id=caller_agent_id,
                    act=_act,
                )
                session.commit()
        except Exception as exc:  # noqa: BLE001 — Tier-1 rolls BOTH back
            logger.exception("instance_mark act failed (rolled back with its audit row)")
            return _error(
                f"instance_mark failed and was rolled back with its audit row: {type(exc).__name__}"
            )
        return json.dumps(
            {
                "marked": True,
                "action": "instance_mark",
                "target": target_id,
                "marker": marker,
                "audit": "committed (Tier-1, same transaction as the act)",
                # F10: the committed ledger row id. ``InstanceOpsAudit.id``
                # is a client-side ``default_factory`` — the value exists
                # before the commit, so it is safe to echo after it.
                "audit_row_id": row_id,
                # F7: the disclosure — a protect on a STATICALLY protected
                # target SUCCEEDS (protection is monotone) and says so.
                "already_protected": already_protected,
                "protection_reasons": protection_reasons,
            },
            default=str,
        )

    instance_mark._full_doc_ = """Write a marker on an instance — park / protect / suspect.

* `park` writes the DELIBERATE-PAUSE tombstone via the existing
  deliberate-pause machinery: the question answer/dismiss release path
  DEFERS to it and does NOT re-claim the instance's suspended turn.
  This is the "operator parked this on purpose" record — the health
  report reads it so a parked instance never reads as wedged.
* `protect` adds the DYNAMIC protection layer: the target becomes
  protected for the terminate gate. A repeat protect on an
  already-protected target refuses with `marker-already-set` (an
  idempotent refusal, not a silent no-op).
* `suspect` is a non-blocking operator annotation.

There is NO `unprotect` marker. A protect marker can only be cleared
by a separately-specified act — deferring it keeps a marker tool from
un-protecting a statically protected target.

Audit tiers: `protect` / `suspect` are TIER-1 (the marker write and
the audit row commit together — a failure rolls both back). `park` is
TIER-2 (write-ahead) by leader decision: the deliberate-pause
machinery is a live subsystem whose writer owns its own thread and is
fail-open by design; threading a session into it is out of scope.

Args:
    id: Target instance id. Required.
    marker: 'park' | 'protect' | 'suspect'. Required.
    note: Optional annotation stored with the marker.
    reason: REQUIRED free text; stored verbatim.
    dry_run: True (default) = preview, no mutation.

Examples:
    instance_mark(id="abc123", marker="park", reason="operator parked until Monday")
    instance_mark(id="root1", marker="protect", reason="user-facing session")
"""

    # ── instance_answer ──────────────────────────────────────────────
    class InstanceAnswerInput(BaseModel):
        """Input schema for instance_answer."""

        id: Annotated[str, Field(description="The ASKER instance id (short form accepted). Required.")]
        answers: Annotated[
            dict,
            Field(
                description=(
                    "The answer payload as a DICT (the route schema is "
                    "answers: dict — NOT a list). Passed verbatim to the "
                    "same implementation the HTTP route uses."
                )
            ),
        ]
        question_pack_id: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Optional pack id echo for correlation; a mismatch "
                    "refuses with reason=stale-question-pack."
                )
            ),
        ]
        resume_message: Annotated[
            str | None,
            Field(default=None, description="Optional extra resume message text.")
        ]
        reason: Annotated[str, Field(description="REQUIRED free text; stored verbatim in the ledger.")]
        dry_run: Annotated[
            bool,
            Field(
                default=True,
                description=(
                    "True (default) — preview only. False submits the "
                    "answer and resumes the cascade (audited, Tier-2 "
                    "write-ahead)."
                )
            ),
        ]

    @register_tool_category("instance_ops")
    @tool(args_schema=InstanceAnswerInput)
    async def instance_answer(
        id: str,
        answers: dict | None = None,
        question_pack_id: str | None = None,
        resume_message: str | None = None,
        reason: str = "",
        dry_run: bool = True,
    ) -> str:
        """Answer a pending question pack and resume the cascade (audited)."""
        label = "INSTANCE_ANSWER"
        # D-6 deliberate exemption: no self-surgery gate — the asker IS the caller (self-answer is the normal flow).
        if not _is_instance_ops_authorized(caller_agent_id):
            return _error("instance_ops operation not permitted for this agent")
        if not _resolve_instance_ops_enabled():
            return KILL_SWITCH_REFUSAL
        audit = _audit_repo_for(manager)
        if _reason_missing(reason):
            return _refusal(label, "reason-required", "the `reason` argument is required")
        if not isinstance(answers, dict):
            return _refusal(
                label,
                "invalid-answers",
                "the `answers` argument must be a dict (the route schema is answers: dict)",
            )
        try:
            target_id = await _resolve_target(manager, id)
        except ValueError as exc:
            return _refusal(label, "instance-not-found", str(exc))

        from ..routers.answer_helper import answer_questions_via_instance

        live_hub = getattr(manager, "_live_hub", None)
        if dry_run:
            preview_row = None
            if audit is not None:
                preview_row = audit.write_preview(
                    action="instance_answer",
                    target=target_id,
                    reason=reason,
                    before={"answers": answers},
                    actor_instance_id=caller_instance_id,
                    actor_agent_id=caller_agent_id,
                )
            return (
                json.dumps(
                    {
                        "dry_run": True,
                        "action": "instance_answer",
                        "target": target_id,
                        "answers": answers,
                        "question_pack_id": question_pack_id,
                        "audit_preview_row": preview_row,
                        "note_line": "NO mutation happened (dry-run)",
                    },
                    default=str,
                )
                + "\nNO mutation happened (dry-run)"
            )
        row_id = None
        if audit is not None:
            row_id = audit.write_audit_ahead(
                action="instance_answer",
                target=target_id,
                reason=reason,
                before={"answers": answers},
                actor_instance_id=caller_instance_id,
                actor_agent_id=caller_agent_id,
            )
        try:
            result = await answer_questions_via_instance(
                manager,
                target_id,
                answers,
                question_pack_id,
                live_hub,
                resume_message=resume_message,
            )
        except Exception as exc:  # noqa: BLE001 — map HTTP parity below
            refusal = _map_answer_error(label, exc)
            if audit is not None:
                if row_id:
                    audit.settle_audit(row_id, "refused", error_message=refusal)
                else:
                    audit.write_refusal(
                        action="instance_answer",
                        target=target_id,
                        reason=reason,
                        error_message=refusal,
                        actor_instance_id=caller_instance_id,
                        actor_agent_id=caller_agent_id,
                    )
            return refusal
        if audit is not None and row_id:
            audit.settle_audit(row_id, "committed", after={"resume_route": result.get("resume_route")})
        return json.dumps(
            {
                "submitted": True,
                "action": "instance_answer",
                "target": target_id,
                "resume_route": result.get("resume_route"),
                "audit": "committed (Tier-2 write-ahead)",
            },
            default=str,
        )

    instance_answer._full_doc_ = """Answer a pending question pack and resume the cascade.

Calls the SAME implementation the HTTP route uses
(`answer_questions_via_instance`), so the two surfaces cannot diverge.

ERROR PARITY with the route (each maps to a typed refusal token):
* 404 NO_PENDING_QUESTION → `no-pending-question`
* 410 QUESTION_PACK_LOST → `question-pack-lost` (the pack lives in
  memory and does not survive a daemon restart — the honest rendering
  is this refusal, never a fabricated answer)
* 400 QUESTION_PACK_MISMATCH → `stale-question-pack`
* 410 ANSWER_TARGET_TERMINAL → `target-not-terminal`
* 200 already_delivered → an idempotent SUCCESS (the duplicate answer
  is a no-op, exactly like the route)
* 503 write-paused → `instance-ops-write-paused`

Args:
    id: The ASKER instance id. Required.
    answers: The answer payload as a DICT (not a list). Required.
    question_pack_id: Optional pack id echo.
    resume_message: Optional extra resume message text.
    reason: REQUIRED free text; stored verbatim.
    dry_run: True (default) = preview, no mutation.

Audit tier: 2 (write-ahead).

Examples:
    instance_answer(id="abc123", answers={"q1": "yes"}, reason="user replied via leader")
"""

    # ── instance_dismiss ─────────────────────────────────────────────
    class InstanceDismissInput(BaseModel):
        """Input schema for instance_dismiss."""

        id: Annotated[str, Field(description="The ASKER instance id (short form accepted). Required.")]
        reason: Annotated[str, Field(description="REQUIRED free text; stored verbatim in the ledger.")]
        dry_run: Annotated[
            bool,
            Field(
                default=True,
                description=(
                    "True (default) — preview only. False dismisses the "
                    "pack and resumes the cascade (audited, Tier-2 "
                    "write-ahead)."
                )
            ),
        ]

    @register_tool_category("instance_ops")
    @tool(args_schema=InstanceDismissInput)
    async def instance_dismiss(
        id: str, reason: str = "", dry_run: bool = True
    ) -> str:
        """Dismiss a pending question pack without answering (audited)."""
        label = "INSTANCE_DISMISS"
        # D-6 deliberate exemption: no self-surgery gate — the asker IS the caller (self-dismiss is the normal flow).
        if not _is_instance_ops_authorized(caller_agent_id):
            return _error("instance_ops operation not permitted for this agent")
        if not _resolve_instance_ops_enabled():
            return KILL_SWITCH_REFUSAL
        audit = _audit_repo_for(manager)
        if _reason_missing(reason):
            return _refusal(label, "reason-required", "the `reason` argument is required")
        try:
            target_id = await _resolve_target(manager, id)
        except ValueError as exc:
            return _refusal(label, "instance-not-found", str(exc))

        try:
            from ..services.instance_ops_service import (
                DismissCascadeFailed,
                DismissInstanceNotFound,
                DismissNoPendingQuestion,
                DismissPackNotPending,
                DismissWritePaused,
                dismiss_question_pack,
            )
        except Exception as exc:  # noqa: BLE001 — the extraction must exist
            logger.exception("instance_dismiss: service import failed")
            return _error(f"instance_dismiss service unavailable: {type(exc).__name__}")

        live_hub = getattr(manager, "_live_hub", None)
        if dry_run:
            preview_row = None
            if audit is not None:
                preview_row = audit.write_preview(
                    action="instance_dismiss",
                    target=target_id,
                    reason=reason,
                    actor_instance_id=caller_instance_id,
                    actor_agent_id=caller_agent_id,
                )
            return (
                json.dumps(
                    {
                        "dry_run": True,
                        "action": "instance_dismiss",
                        "target": target_id,
                        "audit_preview_row": preview_row,
                        "note_line": "NO mutation happened (dry-run)",
                    },
                    default=str,
                )
                + "\nNO mutation happened (dry-run)"
            )
        row_id = None
        if audit is not None:
            row_id = audit.write_audit_ahead(
                action="instance_dismiss",
                target=target_id,
                reason=reason,
                actor_instance_id=caller_instance_id,
                actor_agent_id=caller_agent_id,
            )
        try:
            result = await dismiss_question_pack(manager, target_id, live_hub=live_hub)
        except DismissWritePaused as exc:
            return _settle_or_refuse(
                audit, row_id,
                label=label, token="instance-ops-write-paused", message=str(exc),
                action="instance_dismiss", target=target_id, reason=reason,
                actor_instance_id=caller_instance_id,
                actor_agent_id=caller_agent_id,
            )
        except DismissInstanceNotFound as exc:
            return _settle_or_refuse(
                audit, row_id,
                label=label, token="instance-not-found", message=str(exc),
                action="instance_dismiss", target=target_id, reason=reason,
                actor_instance_id=caller_instance_id,
                actor_agent_id=caller_agent_id,
            )
        except DismissNoPendingQuestion as exc:
            return _settle_or_refuse(
                audit, row_id,
                label=label, token="no-pending-question", message=str(exc),
                action="instance_dismiss", target=target_id, reason=reason,
                actor_instance_id=caller_instance_id,
                actor_agent_id=caller_agent_id,
            )
        except DismissPackNotPending as exc:
            return _settle_or_refuse(
                audit, row_id,
                label=label, token="stale-question-pack", message=str(exc),
                action="instance_dismiss", target=target_id, reason=reason,
                actor_instance_id=caller_instance_id,
                actor_agent_id=caller_agent_id,
            )
        except DismissCascadeFailed as exc:
            # M5: a KNOWN outcome, not an AC-9b unknown — the pack is
            # CONSUMED and the resume cascade failed. Settle the Tier-2
            # row to "error" (never rest at "pending") and refuse with
            # the typed token.
            _msg = (
                "the question pack was consumed (dismissed) but the resume "
                f"cascade failed: {exc}. The pack is GONE — the resume must "
                "be retried (there is no pack left to dismiss again)"
            )
            refusal = _refusal(label, "dismiss-cascade-failed", _msg)
            if audit is not None and row_id:
                audit.settle_audit(row_id, "error", error_message=refusal)
            return refusal
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            # AC-9b: left at "pending" — unknown outcome (see reclaim).
            logger.exception("instance_dismiss act failed (audit row left pending)")
            return _error(f"instance_dismiss failed: {type(exc).__name__}")
        if audit is not None and row_id:
            audit.settle_audit(row_id, "committed", after={"dismissed": True})
        return json.dumps(
            {
                "dismissed": True,
                "action": "instance_dismiss",
                "target": target_id,
                "result": result,
                "audit": "committed (Tier-2 write-ahead)",
            },
            default=str,
        )

    instance_dismiss._full_doc_ = """Dismiss a pending question pack WITHOUT answering; resume the cascade.

Calls the SAME extracted service the HTTP route uses, so the two
surfaces cannot diverge. The SSE `question_pack` event
(status="dismissed") still fires BEFORE the resume cascade (the F3
ordering contract), and the recorded side-effect set is released on
every exit path.

ERROR PARITY with the route: 503 write-paused →
`instance-ops-write-paused`; 404 unknown instance →
`instance-not-found`; 404 no question pack → `no-pending-question`;
409 pack not pending → `stale-question-pack`; cascade failure AFTER
dismissal → `dismiss-cascade-failed` (the pack is consumed and the
resume must be retried — the audit row settles to `error`, it never
rests at `pending`).

Args:
    id: The ASKER instance id. Required.
    reason: REQUIRED free text; stored verbatim.
    dry_run: True (default) = preview, no mutation.

Audit tier: 2 (write-ahead).

Examples:
    instance_dismiss(id="abc123", reason="question is moot — the parent finished")
"""

    return [
        instance_terminate,
        instance_reclaim,
        instance_nudge,
        instance_mark,
        instance_answer,
        instance_dismiss,
    ]


def _map_answer_error(label: str, exc: Exception) -> str:
    """Map an ``answer_questions_via_instance`` failure onto a refusal.

    Error parity with the route's documented contract — the HTTP status
    code and the ErrorResponse code travel in the message so the
    parity is auditable.
    """
    status = getattr(exc, "status_code", None)
    detail = getattr(exc, "detail", None)

    def _code() -> str:
        if isinstance(detail, dict):
            return str(detail.get("code") or "")
        return ""

    if status == 503:
        return _refusal(label, "instance-ops-write-paused", "writes are paused (migration)")
    if status == 404 and _code() == "NO_PENDING_QUESTION":
        return _refusal(
            label,
            "no-pending-question",
            "instance exists but no question pack is pending",
        )
    if status == 410 and _code() == "QUESTION_PACK_LOST":
        return _refusal(
            label,
            "question-pack-lost",
            (
                "the pack does not survive a daemon restart (in-memory "
                "store); the question must be re-asked"
            ),
        )
    if status == 400 and _code() == "QUESTION_PACK_MISMATCH":
        return _refusal(
            label,
            "stale-question-pack",
            f"question_pack_id mismatch ({str(exc)[:200]})",
        )
    if status == 410 and _code() == "ANSWER_TARGET_TERMINAL":
        return _refusal(
            label,
            "target-not-terminal",
            "the asker is terminal (ANSWER_TARGET_TERMINAL)",
        )
    if status == 404:
        return _refusal(label, "instance-not-found", str(exc)[:300])
    return _error(f"instance_answer failed: {type(exc).__name__}: {str(exc)[:200]}")


def _settle_or_refuse(
    audit,
    row_id: str | None,
    *,
    label: str,
    token: str,
    message: str,
    action: str,
    target: str,
    reason: str,
    actor_instance_id: str | None,
    actor_agent_id: str | None,
) -> str:
    """Settle a Tier-2 row as REFUSED and return the refusal string."""
    refusal = _refusal(label, token, message)
    if audit is not None:
        if row_id:
            audit.settle_audit(row_id, "refused", error_message=refusal)
        else:
            audit.write_refusal(
                action=action,
                target=target,
                reason=reason,
                error_message=refusal,
                actor_instance_id=actor_instance_id,
                actor_agent_id=actor_agent_id,
            )
    return refusal
