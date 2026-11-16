"""Room delivery router — the P3 delivery machinery.

Implements the TOTAL 10-state lane map (architecture §4.1 / decisions
D-S4), the R1 addressing predicate (§4.4), the Ruling-A proposal
machinery (§7), and the §10 boot delivery wiring.

The router is PURE machinery: it does NOT mint work (no room verb mints
work — D-S6) and it never auto-revives a member (Ruling A). It reads
instance status, applies the lane map, mints durable proposal rows, and
advances cursors. All DB I/O goes through the room repository / service
(engine-passed, off-loop); all instance-lifecycle calls go through the
manager facade.

Lane map (total over the 10-member ``InstanceStatus`` enum):

  terminal {completed, error, terminated, failed}  → (c) parent proposal
  paused                                            → (d) park + audit
  running                                           → (a) injection (DEFECT-A
                                                       guard; race → retry →
                                                       deferred_cursor)
  idle, queued, waiting, waiting_children            → (b) enqueue-family under
                                                       the status CAS

Addressing (§4.4) is ORTHOGONAL and non-escalating: tagged members get
the lanes above; addressed-to (not also tagged) members get the
addressed predicate (terminal → parent notice; running/alive/paused →
digest_only (no delivery action); left → skip+warning).

This module is the service the P5 room tools will call; it is also the
boot-delivery entry point.

Why one module this large: the H1/H2 seam split
(``_terminal_outcome`` / ``_park_paused`` / ``_reroute_after_cas_miss``
/ ``_deliver_after_cas_success``) plus the breadth of the total 10-state
lane map keep it monolithic for now — within the house 1000-3000 band,
so no split is owed yet.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy.engine import Engine

from daemon.repositories.event.models import EventKind
from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.room.models import (
    NON_TERMINAL_INSTANCE_STATUSES_FOR_ROOMS,
    TERMINAL_INSTANCE_STATUSES_FOR_ROOMS,
    RoomMessageKind,
)
from daemon.repositories.room.repository import (
    RoomNotFoundError,
    RoomRepository,
)
from daemon.repositories.room.service import RoomChatService

if TYPE_CHECKING:
    from ..manager import InstanceManager

logger = logging.getLogger(__name__)

__all__ = [
    "LANE_B_CAS_ALLOWED",
    "RESOLUTION_WARNING_UNRESOLVABLE",
    "RoomDeliveryService",
    "resolve_room_targets",
]

# The derived CAS set for lane (b): non-terminal − {running, paused}.
# Derived STRUCTURALLY from the enum (never from ALIVE_INSTANCE_STATUSES,
# which omits ``waiting``). Membership-pinned against future enum
# additions by the totality pin in test_room_models_and_migration.py.
LANE_B_CAS_ALLOWED: frozenset[str] = NON_TERMINAL_INSTANCE_STATUSES_FOR_ROOMS - {
    "running",
    "paused",
}

# Dedup probe cap: rows beyond the cap fall through to the
# UNIQUE(source_message_id, trigger, member) backstop — the probe is
# an optimization, not the guarantee.
_DEDUP_PROBE_LIMIT = 10000
# Bounded race-retry attempts for the mid-turn injection window.
_INJECTION_MAX_ATTEMPTS = 2
# Parent notice cap differs from the member excerpt cap by policy.
_PARENT_NOTICE_MAX_CHARS = 400
# Member excerpt cap (proposal text bodies + notice excerpts).
_EXCERPT_MAX_CHARS = 120
# Trigger values feed the dedup UNIQUE(source_message_id, trigger,
# member) key — never free-text.
_TRIGGER_TAG_TERMINAL = "tag_terminal"
_TRIGGER_ADDRESSED_TERMINAL = "addressed_terminal"
# P5: the parent-decision row's trigger. A decision row points at the
# PROPOSAL row (``source_message_id`` = the proposal's own id), so its
# (source_message_id, trigger, member) triple never collides with the
# proposal's own (post_id, "tag_terminal"|"addressed_terminal",
# member) triple — the UNIQUE backstop therefore allows exactly ONE
# decision per (proposal, member).
_TRIGGER_DECISION = "decision"

#: The resolution-warning reason vocabulary (A-P3-16). A closed set:
#: every warning the resolver can mint names one of these.
RESOLUTION_WARNING_UNRESOLVABLE = "unresolvable"


def _is_uuid_like(name: str) -> bool:
    """Is this name a UUID-shaped fallback identifier?

    §6's fallback: a UUID resolves to an active roster member whose
    ``instance_id`` matches. WIDENED in P5 (P3-approver constraint (a)):
    the original predicate accepted ONLY the 32-hex UNDASHED form, but
    every real instance id is the 36-char DASHED form
    (``8-4-4-4-12``) — so the fallback was INERT in production (a
    dashed id never matched) and only the un-dashed shape could ever
    resolve. BOTH shapes resolve now: the 32-hex form (kept — it is
    the form the §6 text names) and the 36-char dashed form (the real
    id shape).

    Fail-closed: anything else is NOT uuid-like, so it falls through
    to the exact member_name match and (if that misses) becomes a
    resolution warning.
    """
    if not isinstance(name, str) or not name:
        return False
    if len(name) == 32:
        return all(c in "0123456789abcdef" for c in name.lower())
    if len(name) == 36:
        if not (
            name[8] == "-" and name[13] == "-" and name[18] == "-" and name[23] == "-"
        ):
            return False
        stripped = name.replace("-", "")
        return all(c in "0123456789abcdef" for c in stripped.lower())
    return False


def resolve_room_targets(
    members: list[dict[str, Any]],
    names: list[str],
    *,
    kind: Literal["tag", "addressed"],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Resolve per-room target names against the ACTIVE roster.

    THE canonical resolver (architecture §6 / §4.4): exact,
    case-sensitive per-room ``member_name`` match, plus the UUID
    fallback against ``instance_id`` (32-hex OR 36-char dashed — see
    :func:`_is_uuid_like`).

    Fail-soft, never a refusal: an unresolvable name (not on the
    roster, LEFT, or cross-room) is a WARNING and is SKIPPED — the post
    still succeeds (architecture §4.4: "unresolvable / non-member /
    LEFT / cross-room → post SUCCEEDS, routing skips that name").

    Returns ``(resolved_members, warnings)`` where each warning is the
    structured ``{"kind", "name", "reason"}`` shape the P5 row
    persistence carries (A-P3-16).
    """
    by_name = {m["member_name"]: m for m in members}
    by_iid = {m["instance_id"]: m for m in members}
    resolved: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    for name in names:
        member = by_name.get(name)
        if member is None and _is_uuid_like(name):
            member = by_iid.get(name)
        if member is None:
            warnings.append(
                {
                    "kind": kind,
                    "name": name,
                    "reason": RESOLUTION_WARNING_UNRESOLVABLE,
                }
            )
            continue
        resolved.append(member)
    return resolved, warnings


class RoomDeliveryService:
    """The P3 delivery router."""

    def __init__(
        self,
        manager: "InstanceManager",
        room_service: RoomChatService,
        logger: logging.Logger | None = None,
    ) -> None:
        self._manager = manager
        self._room = room_service
        self._logger = logger or logging.getLogger(__name__)

    # ── public entry ─────────────────────────────────────────────────────

    async def route_post(
        self,
        *,
        room_id: str,
        post: dict[str, Any],
    ) -> dict[str, Any]:
        """Route a freshly-appended room post per the lane map + addressing.

        THE SENDER IDENTITY COMES FROM THE POST — ONE SOURCE (P5
        constraint (c), P3-approver). The pre-P5 shape took the sender
        as a SEPARATE argument AND read the same identity off the post
        payload: two sources for one fact, and the two could DISAGREE —
        the roster read is behind the fail-closed access predicate
        (``list_members(caller_instance_id=...)``), so a caller-supplied
        ``sender_instance_id`` that differed from the post's author
        would have had the READ evaluated against a DIFFERENT identity
        than the routing. The post IS the committed record of who
        wrote (the append is the commit point, and the P5 tool enforces
        author membership BEFORE the append), so the post is the ONE
        source now.

        Returns a summary dict:
          {
            "tag_outcomes": [{"member_name", "instance_id", "outcome"}, ...],
            "addressed_outcomes": [{"member_name", "instance_id", "outcome"}, ...],
            "warnings": [{"kind", "name", "reason"}, ...],
          }
        """
        sender_instance_id = str(post.get("author_instance_id") or "")
        sender_member_name = str(post.get("author_name") or "")
        if not sender_instance_id:
            # No author on the post — nothing to route as, and the
            # access predicate has no caller. Fail-closed.
            return {
                "tag_outcomes": [],
                "addressed_outcomes": [],
                "warnings": [
                    {
                        "kind": "post",
                        "name": "",
                        "reason": "no-author",
                    }
                ],
            }
        room = await self._room.get_room(room_id)
        if room is None:
            return {"tag_outcomes": [], "addressed_outcomes": [], "warnings": ["room not found"]}
        members = await self._room.list_members(
            room_id, caller_instance_id=sender_instance_id, active_only=True
        )

        tagged_raw = list(post.get("tagged_members") or [])
        addressed_raw = list(post.get("addressed_members") or [])

        # Resolve tags (unresolvable → warning, skip).
        tagged_resolved, tag_warnings = resolve_room_targets(
            members, tagged_raw, kind="tag"
        )
        warnings: list[Any] = list(tag_warnings)

        # Resolve addressed (unresolvable / non-member / LEFT → warning, skip).
        addressed_resolved, addr_warnings = resolve_room_targets(
            members, addressed_raw, kind="addressed"
        )
        warnings.extend(addr_warnings)

        tagged_names = {m["member_name"] for m in tagged_resolved}
        tagged_iids = {m["instance_id"] for m in tagged_resolved}

        tag_outcomes: list[dict[str, Any]] = []
        # Tagged lane: per resolved tagged member.
        for m in tagged_resolved:
            outcome = await self._tagged_lane(
                room_id=room_id,
                post=post,
                member=m,
                sender_instance_id=sender_instance_id,
                sender_member_name=sender_member_name,
                room=room,
            )
            tag_outcomes.append(
                {"member_name": m["member_name"], "instance_id": m["instance_id"], "outcome": outcome}
            )

        # Addressed lane: per resolved addressee NOT also tagged.
        addressed_outcomes: list[dict[str, Any]] = []
        for m in addressed_resolved:
            if m["member_name"] in tagged_names or m["instance_id"] in tagged_iids:
                # Co-presence: tag dominates; addressing adds nothing.
                addressed_outcomes.append(
                    {
                        "member_name": m["member_name"],
                        "instance_id": m["instance_id"],
                        "outcome": "tag_dominates",
                    }
                )
                continue
            outcome = await self._addressed_lane(
                room_id=room_id,
                post=post,
                member=m,
                sender_instance_id=sender_instance_id,
                sender_member_name=sender_member_name,
                room=room,
            )
            addressed_outcomes.append(
                {"member_name": m["member_name"], "instance_id": m["instance_id"], "outcome": outcome}
            )

        # Emit content-free audit for tag-delivery outcomes.
        for o in tag_outcomes:
            try:
                await self._emit_delivery_event(
                    o["instance_id"],
                    room_id,
                    post["seq"],
                    o["member_name"],
                    o["outcome"],
                )
            except Exception as exc:  # noqa: BLE001 — audit must never break delivery
                self._logger.warning(
                    "room_tag_delivery emit failed (room=%s seq=%s member=%s "
                    "outcome=%s): %r",
                    room_id, post["seq"], o["member_name"], o["outcome"], exc,
                )

        return {
            "tag_outcomes": tag_outcomes,
            "addressed_outcomes": addressed_outcomes,
            "warnings": warnings,
        }

    # ── tagged lane (10-state map) ────────────────────────────────────────

    async def _tagged_lane(
        self,
        *,
        room_id: str,
        post: dict[str, Any],
        member: dict[str, Any],
        sender_instance_id: str,
        sender_member_name: str,
        room: dict[str, Any],
        at_boot: bool = False,
    ) -> str:
        member_iid = member["instance_id"]
        member_name = member["member_name"]
        seq = int(post["seq"])

        # Read fresh status (lane assignment FIRST).
        status = await self._room.get_instance_status(member_iid)
        if status is None:
            # Instance row gone — leave durable (deferred_cursor).
            self._logger.debug(
                "instance row gone mid-route (room=%s seq=%s member=%s)",
                room_id, seq, member_iid,
            )
            return "deferred_cursor"

        # Terminal → (c) parent proposal (Ruling A).
        if status in TERMINAL_INSTANCE_STATUSES_FOR_ROOMS:
            return await self._terminal_outcome(
                room_id=room_id,
                post=post,
                member=member,
                trigger=_TRIGGER_TAG_TERMINAL,
                parent_iid=room["owner_instance_id"],
                at_boot=at_boot,
            )

        # Paused → (d) park + audit.
        if status == "paused":
            return await self._park_paused(
                member_iid=member_iid,
                room_id=room_id,
                seq=seq,
                member_name=member_name,
            )

        # Running → (a) injection, with DEFECT-A guard.
        if status == "running":
            if not self._manager.has_live_graph_task(member_iid):
                # DEFECT-A downgrade → lane (b) shape. at_boot threads
                # through (A-P3-24): for a running-graphless member the
                # CAS-miss is UNCONDITIONAL (running ∉ allowed set), so
                # the fresh-terminal re-read transit is guaranteed —
                # boot must stay mint-only two hops deep too.
                return await self._lane_b_enqueue(
                    room_id=room_id,
                    post=post,
                    member=member,
                    seq=seq,
                    member_iid=member_iid,
                    graphless=True,
                    at_boot=at_boot,
                    room=room,
                )
            # Race retry: bounded retry.
            for attempt in range(_INJECTION_MAX_ATTEMPTS):
                try:
                    notif = self._notification_text(room_id, sender_member_name, seq)
                    source = (
                        f"room:{room_id}:{sender_member_name}:{seq}:"
                        f"author={sender_instance_id}"
                    )
                    self._manager.set_injection(member_iid, notif, source=source)
                    await self._room.advance_delivered_cursor(room_id, member_iid, seq)
                    return "injected"
                except Exception as exc:  # noqa: BLE001
                    self._logger.warning(
                        "injection attempt %d failed (room=%s seq=%s member=%s): %r",
                        attempt, room_id, seq, member_iid, exc,
                    )
                    if attempt == 0:
                        # yield-only; no backoff by design (race window is in-process)
                        await asyncio.sleep(0)
                        continue
            # Race exhausted → deferred_cursor.
            await self._emit_delivery_event(
                member_iid, room_id, seq, member_name, "deferred_cursor"
            )
            return "deferred_cursor"

        # Lane (b): idle/queued/waiting/waiting_children.
        if status in LANE_B_CAS_ALLOWED:
            return await self._lane_b_enqueue(
                room_id=room_id,
                post=post,
                member=member,
                seq=seq,
                member_iid=member_iid,
                graphless=False,
                at_boot=at_boot,
                room=room,
            )

        # Fallback (shouldn't happen — total map covers all 10).
        return "deferred_cursor"

    # ── the extracted lane helpers (P5 tidier H1/H2) ─────────────────────
    #
    # H2 — the TERMINAL branch (lane c / c′) lives in ONE implementation.
    # Pre-P5 the same six-step sequence (mint → admission gate → cursor
    # advance → at_boot gate → parent route) was written out at THREE
    # sites (the tagged lane, the lane-(b) CAS-miss re-route, and the
    # boot pass) — the pinned pair #2 invariant ("never parent-route at
    # boot") was therefore enforced by an ``if at_boot:`` guard repeated
    # three times, and A-P3-21/A-P3-24 already had to repair TWO of the
    # three copies separately. ONE implementation makes the invariant
    # structural.
    async def _terminal_outcome(
        self,
        *,
        room_id: str,
        post: dict[str, Any],
        member: dict[str, Any],
        trigger: str,
        parent_iid: str,
        at_boot: bool = False,
    ) -> str:
        """The TERMINAL branch — mint the durable proposal, then route it.

        1. Mint the durable ``task_proposal`` row (admission-gated:
           ``_mint_proposal`` returns True iff a durable row EXISTS —
           A-P3-22). A mint failure returns ``deferred_cursor`` with the
           cursor UNTOUCHED, so boot re-derivation retries the row; the
           cursor must NEVER advance without an admission.
        2. Y14: the terminal member's delivered cursor advances AT
           ADMISSION (the proposal row is the durable carrier).
        3. PINNED PAIR #2: at boot the branch is MINT-ONLY — the
           proposal stays durable pending and is NEVER parent-routed
           (A-P3-21 one-hop, A-P3-24 two-hop). Outside boot, the
           proposal is routed to the parent per the parent-target
           matrix.
        """
        admitted = await self._mint_proposal(
            room_id=room_id,
            post=post,
            member=member,
            trigger=trigger,
            sender_instance_id=post["author_instance_id"],
            sender_member_name=post["author_name"],
        )
        if not admitted:
            # F1/A-P3-22: no durable proposal row exists — advancing the
            # cursor would PERMANENTLY hide the undelivered notification
            # from re-derivation. Leave durable.
            return "deferred_cursor"
        await self._room.advance_delivered_cursor(
            room_id, member["instance_id"], int(post["seq"])
        )
        if at_boot:
            # Pinned pair #2 (absolute wording, A-P3-21/24): proposals
            # are NEVER parent-routed at boot — mint-only, durable.
            return "proposal"
        # Route the proposal to the parent per the parent-target matrix.
        await self._route_proposal_to_parent(
            room_id=room_id,
            post=post,
            member=member,
            parent_iid=parent_iid,
            trigger=trigger,
            sender_member_name=post["author_name"],
        )
        return "proposal"

    async def _park_paused(
        self, *, member_iid: str, room_id: str, seq: int, member_name: str
    ) -> str:
        """The PAUSED branch (lane d) — park the row + audit it.

        One implementation for the two callers (the tagged lane and the
        lane-(b) CAS-miss re-route); the ADDRESSED lane's paused branch
        is deliberately NOT this one — addressing is non-escalating
        (digest at resume, no lane action, §4.4).
        """
        await self._emit_delivery_event(
            member_iid, room_id, seq, member_name, "parked_paused"
        )
        return "parked_paused"

    async def _lane_b_enqueue(
        self,
        *,
        room_id: str,
        post: dict[str, Any],
        member: dict[str, Any],
        seq: int,
        member_iid: str,
        graphless: bool,
        at_boot: bool = False,
        room: dict[str, Any] | None = None,
    ) -> str:
        """Lane (b) — the enqueue family, under the status CAS.

        P5 tidier H1: the body was ~160 lines at five levels of
        nesting; it is now a TWO-branch dispatcher over the two named
        seams — ``_reroute_after_cas_miss`` (the CAS missed: re-read the
        fresh status and take the total map's branch for it) and
        ``_deliver_after_cas_success`` (the CAS succeeded: busy probe →
        busy fallback → enqueue). The behaviour is unchanged; only the
        shape moved.
        """
        # Rowcount-0 fallback: re-read fresh status; take the total map's
        # branch for it.
        rowcount = await self._room.cas_instance_status_to_running(
            member_iid, LANE_B_CAS_ALLOWED
        )
        if rowcount == 0:
            return await self._reroute_after_cas_miss(
                room_id=room_id,
                post=post,
                member=member,
                seq=seq,
                member_iid=member_iid,
                at_boot=at_boot,
                room=room,
            )
        return await self._deliver_after_cas_success(
            room_id=room_id,
            post=post,
            member=member,
            seq=seq,
            member_iid=member_iid,
        )

    async def _reroute_after_cas_miss(
        self,
        *,
        room_id: str,
        post: dict[str, Any],
        member: dict[str, Any],
        seq: int,
        member_iid: str,
        at_boot: bool,
        room: dict[str, Any] | None,
    ) -> str:
        """The CAS-MISS seam — the member's status changed under us.

        The lane-(b) CAS only succeeds from the idle family; a
        concurrent flip (or a member that was never in the allowed set
        — e.g. a RUNNING member on the graphless downgrade) misses it.
        The fresh re-read then takes the TOTAL MAP's branch for the
        member's actual status. The at_boot flag rides down (A-P3-24:
        the two-hop boot race reaches this branch — for a running-graphless
        member the CAS-miss is UNCONDITIONAL, so the fresh-terminal
        transit is GUARANTEED, not ms-luck).
        """
        member_name = member["member_name"]
        fresh = await self._room.get_instance_status(member_iid)
        if fresh is None:
            self._logger.debug(
                "instance row gone mid-route (room=%s seq=%s member=%s)",
                room_id, seq, member_iid,
            )
            return "deferred_cursor"
        if fresh in TERMINAL_INSTANCE_STATUSES_FOR_ROOMS:
            # Re-route as terminal (proposal) — mint + parent route, BOTH
            # gated on admission (F1/A-P3-22), and NEVER parent-routed at
            # boot (A-P3-24 two-hop closure).
            parent_iid = (room or {}).get("owner_instance_id") or ""
            return await self._terminal_outcome(
                room_id=room_id,
                post=post,
                member=member,
                trigger=_TRIGGER_TAG_TERMINAL,
                parent_iid=parent_iid,
                at_boot=at_boot,
            )
        if fresh == "paused":
            return await self._park_paused(
                member_iid=member_iid,
                room_id=room_id,
                seq=seq,
                member_name=member_name,
            )
        if fresh == "running":
            # CAS missed on a concurrent running flip — the lane map
            # still assigns running → (a). Attempt delivery NOW. The
            # pre-fix shape advanced the cursor and returned
            # "enqueued" without injecting or enqueueing: a silent
            # drop of a tagged notification that also defeated boot
            # re-derivation (the pre-advanced cursor hid the row).
            if self._manager.has_live_graph_task(member_iid):
                notif = self._notification_text(room_id, post["author_name"], seq)
                source = (
                    f"room:{room_id}:{post['author_name']}:{seq}:"
                    f"author={post['author_instance_id']}"
                )
                try:
                    self._manager.set_injection(member_iid, notif, source=source)
                    await self._room.advance_delivered_cursor(room_id, member_iid, seq)
                    return "injected"
                except Exception as exc:  # noqa: BLE001
                    self._logger.warning(
                        "post-CAS-miss injection failed for %s: %r", member_iid, exc
                    )
            else:
                # DEFECT-A downgrade → lane (b) shape: running with
                # no live graph task never sees a mid-turn injection.
                try:
                    await self._manager.enqueue_message(
                        member_iid,
                        self._notification_text(room_id, post["author_name"], seq),
                        source=f"internal_agent:{post['author_instance_id']}",
                        metadata={"room_id": room_id, "seq": seq},
                    )
                    await self._room.advance_delivered_cursor(room_id, member_iid, seq)
                    return "enqueued"
                except Exception as exc:  # noqa: BLE001
                    self._logger.warning(
                        "post-CAS-miss enqueue failed for %s: %r", member_iid, exc
                    )
            # Delivery not completed → deferred_cursor with the cursor
            # UNTOUCHED, so boot re-derivation retries the row.
            await self._emit_delivery_event(
                member_iid, room_id, seq, member_name, "deferred_cursor"
            )
            return "deferred_cursor"
        # Unknown/other → deferred.
        await self._emit_delivery_event(
            member_iid, room_id, seq, member_name, "deferred_cursor"
        )
        return "deferred_cursor"

    async def _deliver_after_cas_success(
        self,
        *,
        room_id: str,
        post: dict[str, Any],
        member: dict[str, Any],
        seq: int,
        member_iid: str,
    ) -> str:
        """The CAS-SUCCESS seam — the member is now ``running``.

        Busy-guard: mirrors the enqueue path's ``get_queue_stats``
        pending/processing probe. A busy member falls back to an
        injection when it is eligible, and otherwise leaves the row
        durable (``deferred_cursor``, cursor UNTOUCHED) so boot
        re-derivation retries it.
        """
        member_name = member["member_name"]
        try:
            stats = await self._manager.get_queue_stats(member_iid)
            busy = bool(stats.get("pending_count", 0) > 0 or stats.get("processing_count", 0) > 0)
        except Exception as exc:  # noqa: BLE001
            self._logger.debug(
                "queue stats probe failed (room=%s seq=%s member=%s): %r",
                room_id, seq, member_iid, exc,
            )
            busy = False
        if busy:
            # Busy fallback: inject if eligible, else deferred.
            fresh = await self._room.get_instance_status(member_iid)
            if fresh == "running" and self._manager.has_live_graph_task(member_iid):
                notif = self._notification_text(room_id, post["author_name"], seq)
                source = (
                    f"room:{room_id}:{post['author_name']}:{seq}:"
                    f"author={post['author_instance_id']}"
                )
                try:
                    self._manager.set_injection(member_iid, notif, source=source)
                    await self._room.advance_delivered_cursor(room_id, member_iid, seq)
                    return "injected"
                except Exception as exc:  # noqa: BLE001
                    self._logger.warning(
                        "busy-fallback injection failed for %s (room=%s seq=%s): %r",
                        member_iid, room_id, seq, exc,
                    )
            await self._emit_delivery_event(
                member_iid, room_id, seq, member_name, "deferred_cursor"
            )
            return "deferred_cursor"

        # Enqueue (terminal-refusing: member is now running, never terminal).
        try:
            result = await self._manager.enqueue_message(
                member_iid,
                self._notification_text(room_id, post["author_name"], seq),
                source=f"internal_agent:{post['author_instance_id']}",
                metadata={"room_id": room_id, "seq": seq},
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("enqueue failed for %s: %r", member_iid, exc)
            await self._emit_delivery_event(
                member_iid, room_id, seq, member_name, "deferred_cursor"
            )
            return "deferred_cursor"

        # Advance delivered cursor on success.
        await self._room.advance_delivered_cursor(room_id, member_iid, seq)
        return "enqueued"

    # ── addressed lane (§4.4) ─────────────────────────────────────────────

    async def _addressed_lane(
        self,
        *,
        room_id: str,
        post: dict[str, Any],
        member: dict[str, Any],
        sender_instance_id: str,
        sender_member_name: str,
        room: dict[str, Any],
    ) -> str:
        member_iid = member["instance_id"]
        member_name = member["member_name"]
        seq = int(post["seq"])

        status = await self._room.get_instance_status(member_iid)
        if status is None:
            self._logger.debug(
                "instance row gone mid-route (room=%s seq=%s member=%s)",
                room_id, seq, member_iid,
            )
            return "digest_only"

        # terminal → (c′) parent notice.
        if status in TERMINAL_INSTANCE_STATUSES_FOR_ROOMS:
            admitted = await self._mint_proposal(
                room_id=room_id,
                post=post,
                member=member,
                trigger=_TRIGGER_ADDRESSED_TERMINAL,
                sender_instance_id=sender_instance_id,
                sender_member_name=sender_member_name,
            )
            if not admitted:
                # F1/A-P3-22: no durable proposal row — leave durable.
                return "deferred_cursor"
            await self._room.advance_delivered_cursor(room_id, member_iid, seq)
            parent_iid = room["owner_instance_id"]
            # parent-target matrix for the notice delivery.
            parent_status = await self._room.get_instance_status(parent_iid)
            notice_text = self._notice_text(
                room_id=room_id,
                sender_name=sender_member_name,
                member_name=member_name,
                member_iid=member_iid,
                prior_status=status,
                seq=seq,
                excerpt=(post.get("text") or "")[:_EXCERPT_MAX_CHARS],
            )
            if parent_status == "running" and self._manager.has_live_graph_task(parent_iid):
                self._manager.set_injection(
                    parent_iid, notice_text[:_PARENT_NOTICE_MAX_CHARS],
                    source=f"room:{room_id}:{sender_member_name}:{seq}:author={sender_instance_id}",
                )
                await self._room.emit_room_event(
                    parent_iid, EventKind.ROOM_TAG_DELIVERY.value,
                    {"room_id": room_id, "seq": seq, "member_name": member_name,
                     "outcome": "proposal"},
                )
            # LANE_B_CAS_ALLOWED (non-terminal − {running, paused}) matches
            # this lane-(b) tuple today.
            elif parent_status == "running" or parent_status in LANE_B_CAS_ALLOWED:
                # DEFECT-A downgrade: a graphless-running parent (running,
                # no live graph task) takes the lane-(b) shape — enqueue —
                # instead of silently leaving the notice durable-pending.
                try:
                    await self._manager.enqueue_message(
                        parent_iid, notice_text[:_PARENT_NOTICE_MAX_CHARS],
                        source=f"internal_agent:{sender_instance_id}",
                        metadata={"room_id": room_id, "seq": seq, "kind": "task_proposal"},
                    )
                except Exception as exc:  # noqa: BLE001
                    self._logger.warning(
                        "parent enqueue failed (parent=%s room=%s seq=%s): %r",
                        parent_iid, room_id, seq, exc,
                    )
            elif parent_status == "paused":
                await self._room.emit_room_event(
                    parent_iid, EventKind.ROOM_TAG_DELIVERY.value,
                    {"room_id": room_id, "seq": seq, "member_name": member_name,
                     "outcome": "parked_paused"},
                )
            # parent terminal → one-hop durable pending (no routing).
            await self._room.emit_room_event(
                parent_iid, EventKind.ROOM_PROPOSAL.value,
                {"room_id": room_id, "seq": seq, "member_name": member_name,
                 "trigger": "addressed_terminal", "outcome": "pending"},
            )
            return "parent_notice"

        # running → no delivery action.
        if status == "running":
            return "digest_only"

        # alive-not-running → digest, no wake. LANE_B_CAS_ALLOWED
        # (non-terminal − {running, paused}) matches this tuple today.
        if status in LANE_B_CAS_ALLOWED:
            return "digest_only"

        # paused → digest at resume, no lane action.
        if status == "paused":
            return "digest_only"

        return "digest_only"

    # ── proposal minting ──────────────────────────────────────────────────

    async def _mint_proposal(
        self,
        *,
        room_id: str,
        post: dict[str, Any],
        member: dict[str, Any],
        trigger: str,
        sender_instance_id: str,
        sender_member_name: str,
    ) -> bool:
        """ADMISSION RESULT (F1, A-P3-22): True iff a durable
        task_proposal row EXISTS after this call — (a) minted here,
        (b) dedup-present (the row was already there — that IS
        admission; treating it as failure would re-deliver proposals
        forever), or (c) concurrent-winner (the UNIQUE
        (source_message_id, trigger, member) backstop raced us; the
        winner's row is verified before admitting). Only a
        NO-durable-row outcome returns False: the caller MUST gate the
        delivered-cursor advance (and parent routing) on admission —
        advancing the cursor on a mint failure would PERMANENTLY hide
        the undelivered notification from re-derivation."""
        post_id = int(post["id"])
        member_name = member["member_name"]

        async def _durable_row_exists() -> bool:
            try:
                msgs = await self._room.read_messages(
                    room_id, caller_instance_id=sender_instance_id,
                    after_seq=0, limit=_DEDUP_PROBE_LIMIT,
                )
            except Exception as exc:  # noqa: BLE001 — probe failure ≠ proof of absence
                self._logger.debug(
                    "dedup probe failed (room=%s post=%s trigger=%s member=%s): %r",
                    room_id, post_id, trigger, member_name, exc,
                )
                return False
            for m in msgs:
                if (
                    m.get("kind") == "task_proposal"
                    and m.get("source_message_id") == post_id
                    and m.get("trigger") == trigger
                    and m.get("member") == member_name
                ):
                    return True
            return False

        # (b) Dedup-present: the row already exists — that IS admission.
        if await _durable_row_exists():
            return True
        text_body = (
            f"[ROOM NOTICE — addressed to a completed member] room={room_id} · "
            f"{sender_member_name}'s post is addressed to {member_name} "
            f"({member['instance_id']}, prior_status=terminal) · "
            f"excerpt: \"{(post.get('text') or '')[:_EXCERPT_MAX_CHARS]}\" (seq {post['seq']})\n"
            f"options: revive {member_name} | reroute | answer in-room · "
            f"full context: room_read(room={room_id}, after_seq={post['seq']})"
        )
        try:
            await self._room.append_message(
                room_id=room_id,
                author_instance_id=sender_instance_id,
                author_name=sender_member_name,
                text_body=text_body,
                kind=RoomMessageKind.TASK_PROPOSAL.value,
                source_message_id=post_id,
                trigger=trigger,
                member=member_name,
                tagged_members=[],
                addressed_members=[],
            )
        except Exception as exc:  # noqa: BLE001
            # (c) Concurrent-winner: the dedup backstop (or any other
            # integrity shape) fired — verify before admitting.
            if await _durable_row_exists():
                return True
            self._logger.warning(
                "proposal mint failed (room=%s seq=%s member=%s trigger=%s): %r",
                room_id, post["seq"], member_name, trigger, exc,
            )
            return False
        await self._room.emit_room_event(
            sender_instance_id,
            EventKind.ROOM_PROPOSAL.value,
            {
                "room_id": room_id,
                "seq": post["seq"],
                "member_name": member_name,
                "trigger": trigger,
                "outcome": "minted",
            },
        )
        return True

    async def _route_proposal_to_parent(
        self,
        *,
        room_id: str,
        post: dict[str, Any],
        member: dict[str, Any],
        parent_iid: str,
        trigger: str,
        sender_member_name: str,
    ) -> None:
        """Route a minted proposal to the parent per the parent-target matrix.
        One-hop rule: a terminal parent leaves the proposal durable pending
        (no recursion, no revive)."""
        parent_status = await self._room.get_instance_status(parent_iid)
        if parent_status is None:
            # Deliberate debug (zero control-flow): a gone parent row falls
            # through the matrix below as the one-hop pending posture.
            self._logger.debug(
                "instance row gone mid-route (room=%s seq=%s parent=%s)",
                room_id, post["seq"], parent_iid,
            )
        seq = int(post["seq"])
        member_name = member["member_name"]
        member_iid = member["instance_id"]
        notice_text = (
            f"[ROOM NOTICE] room={room_id} · {sender_member_name}'s post "
            f"tagged {member_name} ({member_iid}, terminal) · seq {seq}\n"
            f"options: revive {member_name} | reroute | answer in-room"
        )
        if parent_status == "running" and self._manager.has_live_graph_task(parent_iid):
            try:
                self._manager.set_injection(
                    parent_iid, notice_text[:_PARENT_NOTICE_MAX_CHARS],
                    source=f"room:{room_id}:{sender_member_name}:{seq}:author={post['author_instance_id']}",
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "parent injection failed (parent=%s room=%s seq=%s): %r",
                    parent_iid, room_id, seq, exc,
                )
        # LANE_B_CAS_ALLOWED (non-terminal − {running, paused}) matches
        # this lane-(b) tuple today.
        elif parent_status == "running" or parent_status in LANE_B_CAS_ALLOWED:
            # DEFECT-A downgrade: a graphless-running parent (running, no
            # live graph task) takes the lane-(b) shape — enqueue —
            # instead of silently leaving the proposal durable-pending.
            try:
                await self._manager.enqueue_message(
                    parent_iid, notice_text[:_PARENT_NOTICE_MAX_CHARS],
                    source=f"internal_agent:{post['author_instance_id']}",
                    metadata={"room_id": room_id, "seq": seq, "kind": "task_proposal"},
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "parent enqueue failed (parent=%s room=%s seq=%s): %r",
                    parent_iid, room_id, seq, exc,
                )
        elif parent_status == "paused":
            await self._room.emit_room_event(
                parent_iid, EventKind.ROOM_TAG_DELIVERY.value,
                {"room_id": room_id, "seq": seq, "member_name": member_name,
                 "outcome": "parked_paused"},
            )
        # parent terminal → one-hop durable pending: do nothing (no
        # recursion, no revive). The proposal row stays in the log and is
        # surfaced when the parent next runs through a legitimate lane.

    # ── the parent-decision record surface (P5) ──────────────────────────

    async def record_task_decision(
        self,
        *,
        room_id: str,
        proposal_seq: int,
        decision: str,
        note: str = "",
        decider_instance_id: str,
        decider_member_name: str,
    ) -> dict[str, Any]:
        """Record the PARENT's decision on a task proposal (P5, (e)).

        The Ruling-A loop-back: a terminal member's tagged post mints a
        durable ``task_proposal`` row and the parent is notified; the
        parent's ANSWER has to land ON THE ROOM, or the proposal stays
        an unanswered row forever (the parent-decided tasking rule:
        siblings PROPOSE, the parent DECIDES).

        The decision row is a ``kind="task_decision"`` message whose
        ``source_message_id`` points at the PROPOSAL row's own id and
        whose ``trigger`` is ``"decision"`` — so the dedup UNIQUE
        (source_message_id, trigger, member) backstop allows exactly ONE
        decision per (proposal, member) and never collides with the
        proposal row itself (a different source_message_id AND a
        different trigger).

        Fail-closed: an unknown proposal (no ``task_proposal`` row at
        that seq) raises :class:`RoomNotFoundError`; a second decision
        for the same proposal raises the append path's
        :class:`RoomError` (the UNIQUE backstop).
        """
        # Locate the proposal row — the read rides the fail-closed read
        # predicate with the DECIDER as the caller (the decider is the
        # room owner, hence a member).
        messages = await self._room.read_messages(
            room_id,
            caller_instance_id=decider_instance_id,
            after_seq=max(int(proposal_seq) - 1, 0),
            limit=1,
        )
        proposal = next(
            (m for m in messages if int(m["seq"]) == int(proposal_seq)), None
        )
        if proposal is None:
            raise RoomNotFoundError(
                f"room={room_id} has no message at seq={proposal_seq} "
                f"(proposal not found — fail-closed)"
            )
        if proposal.get("kind") != RoomMessageKind.TASK_PROPOSAL.value:
            raise RoomNotFoundError(
                f"room={room_id} seq={proposal_seq} is not a task_proposal "
                f"(kind={proposal.get('kind')!r}) — nothing to decide"
            )
        member_name = str(proposal.get("member") or "")
        body = (
            f"[ROOM DECISION] {decider_member_name} decided on "
            f"{member_name or 'the proposal'}: {decision}"
        )
        if note:
            body += f" — {note}"
        body += (
            f" (proposal seq={proposal_seq}, room={room_id}; "
            f"options were revive | reroute | answer in-room)"
        )
        row = await self._room.append_message(
            room_id=room_id,
            author_instance_id=decider_instance_id,
            author_name=decider_member_name,
            text_body=body,
            kind=RoomMessageKind.TASK_DECISION.value,
            source_message_id=int(proposal["id"]),
            trigger=_TRIGGER_DECISION,
            member=member_name or None,
            tagged_members=[],
            addressed_members=[],
        )
        await self._room.emit_room_event(
            decider_instance_id,
            EventKind.ROOM_DECISION.value,
            {
                "room_id": room_id,
                "seq": row["seq"],
                "member_name": member_name,
                "proposal_seq": int(proposal_seq),
                "outcome": "recorded",
            },
        )
        return {
            "decision": decision,
            "note": note,
            "member_name": member_name,
            "proposal_seq": int(proposal_seq),
            "decision_seq": int(row["seq"]),
            "decision_id": row["id"],
        }

    # ── helpers ───────────────────────────────────────────────────────────

    async def _emit_delivery_event(
        self,
        instance_id: str,
        room_id: str,
        seq: int,
        member_name: str,
        outcome: str,
    ) -> None:
        """Emit ONE content-free ``room_tag_delivery`` audit row (the
        shared 4-field payload: room_id / seq / member_name / outcome)."""
        await self._room.emit_room_event(
            instance_id,
            EventKind.ROOM_TAG_DELIVERY.value,
            {
                "room_id": room_id,
                "seq": seq,
                "member_name": member_name,
                "outcome": outcome,
            },
        )

    def _notification_text(self, room_id: str, sender_name: str, seq: int) -> str:
        text = f"Room {room_id} · new tagged message from {sender_name} (seq {seq})"
        if len(text) > 300:
            text = text[:297] + "..."
        return text

    def _notice_text(
        self,
        *,
        room_id: str,
        sender_name: str,
        member_name: str,
        member_iid: str,
        prior_status: str,
        seq: int,
        excerpt: str,
    ) -> str:
        return (
            f"[ROOM NOTICE — addressed to a completed member] room={room_id} · "
            f"{sender_name}'s untagged post is addressed to {member_name} "
            f"({member_iid}, prior_status={prior_status}) · "
            f"excerpt: \"{excerpt}\" (seq {seq})\n"
            f"options: revive {member_name} | reroute | answer in-room · "
            f"full context: room_read(room={room_id}, after_seq={seq})"
        )

    # ── boot delivery ─────────────────────────────────────────────────────

    async def boot_deliver(self, engine: Engine) -> dict[str, Any]:
        """Boot-time delivery: re-derive tagged undelivered rows and route
        per the lane map. Proposals/notices stay durable pending (no
        routing) per pinned pair #2.

        F-1 gate: only RESOLVED TAGGED targets are routed. The substrate
        deliberately over-includes — one (member, row) pair per member
        whose cursors sit behind ANY tagged row (repository.py SEMANTICS
        NOTE, P1 resolution #1) — so each pair's target resolution runs
        HERE (route_post's resolver semantics: active roster, name or
        32-hex-UUID identity) BEFORE any lane assignment, CAS flip, or
        cursor advance. Non-target pairs (untagged members and
        addressed-only addressees — the addressed dimension is
        digest/no-wake alive and boot never re-routes notices, D4 pair)
        skip as neither delivered nor deferred (A-P3-20 skip
        discipline).

        Returns a counts dict (mirrors run_room_boot_regen, plus
        ``filtered_not_targets``).
        """
        repo = RoomRepository(engine)
        pending = await asyncio.to_thread(repo.derive_undelivered_tagged)
        rooms = sorted({row["room_id"] for row in pending})
        members = sorted({row["instance_id"] for row in pending})
        # For each pending (member, row) pair, route the tagged row.
        # We need the post payload; reconstruct a minimal post dict from
        # the room log.
        delivered = 0
        deferred = 0
        filtered = 0
        # F-1: resolved target identities per (room_id, seq) — the same
        # post can carry many over-included member pairs; resolve its
        # tag set once per post, not once per pair.
        target_identities: dict[tuple[str, int], tuple[set[str], set[str]]] = {}
        for row in pending:
            try:
                # Fetch the message row to get full post payload.
                msgs = await self._room.read_messages(
                    row["room_id"],
                    caller_instance_id=row["instance_id"],
                    after_seq=row["seq"] - 1,
                    limit=1,
                )
                post = None
                for m in msgs:
                    if int(m["seq"]) == int(row["seq"]):
                        post = m
                        break
                if post is None:
                    # Defensive: the window must contain exactly the pending seq.
                    continue
                # F-1 gate: route only RESOLVED TAGGED targets. The old
                # comment here claimed derive_undelivered_tagged "already
                # filtered tagged non-empty" — it filters ROWS with a
                # non-empty tag LIST, never members INTO the tag set: the
                # substrate deliberately over-includes (repository.py
                # SEMANTICS NOTE, P1 resolution #1), and pre-fix boot
                # trusted the over-included pairs, so a post tagged only
                # to X woke, CAS-flipped, and cursor-advanced every
                # untagged member (including the owner) at boot.
                # Resolution is route_post's resolver semantics IDENTICAL
                # (§6 / A-P3-6): active roster, member_name match or
                # 32-hex UUID→instance_id match; unresolvable names are
                # not targets. The filter gates BEFORE any lane
                # assignment, CAS flip, or cursor advance.
                cache_key = (row["room_id"], int(row["seq"]))
                if cache_key not in target_identities:
                    target_identities[cache_key] = (
                        await self._resolve_tagged_target_identities(
                            room_id=row["room_id"], post=post
                        )
                    )
                t_names, t_iids = target_identities[cache_key]
                if (
                    row["member_name"] not in t_names
                    and row["instance_id"] not in t_iids
                ):
                    # Not a resolved target. Untagged members receive
                    # NOTHING at boot. Addressed-only addressees skip
                    # too: addressing is digest/no-wake for alive
                    # members (§4.4, F3 pin) and boot never re-routes
                    # addressed-to-terminal notices (D4 pair, pinned
                    # pair #2) — the row stays durable above the cursor
                    # and re-derives until the member's read path
                    # consumes it. Counts as neither (A-P3-20).
                    filtered += 1
                    continue
                member = await self._room.get_member(row["room_id"], row["instance_id"])
                if member is None:
                    continue
                room = await self._room.get_room(row["room_id"])
                if room is None:
                    continue
                # Tagged lane only at boot (proposals stay pending).
                # We invoke the tagged lane for the member; if it mints a
                # proposal, that's allowed (tagged terminal → proposal is
                # the lane-(c) outcome, and the proposal row is durable).
                # Pinned pair #2 (absolute wording, A-P3-21): proposals
                # are NEVER parent-routed at boot — enforced by the
                # at_boot flag (mint-only terminal branch), which also
                # covers the ms-scale nick where the member flips
                # terminal between the status read above and the lane's
                # own re-read.
                status = await self._room.get_instance_status(row["instance_id"])
                if status in TERMINAL_INSTANCE_STATUSES_FOR_ROOMS:
                    # ONE implementation of the terminal branch (H2) —
                    # at_boot=True ⇒ MINT-ONLY (pinned pair #2), so the
                    # parent is NEVER routed at boot.
                    outcome = await self._terminal_outcome(
                        room_id=row["room_id"],
                        post=post,
                        member=member,
                        trigger=_TRIGGER_TAG_TERMINAL,
                        parent_iid=room["owner_instance_id"],
                        at_boot=True,
                    )
                    if outcome == "deferred_cursor":
                        # F1/A-P3-22: no durable proposal row — the cursor
                        # advance would permanently hide the notification.
                        deferred += 1
                        continue
                    delivered += 1
                    continue
                # Otherwise, route normally (injection/enqueue/park).
                # at_boot=True (A-P3-21): a member flipping terminal
                # between the status read above and the lane's re-read
                # must still take the mint-only boot branch — NEVER the
                # parent-routing branch (pinned pair #2, absolute).
                outcome = await self._tagged_lane(
                    room_id=row["room_id"],
                    post=post,
                    member=member,
                    sender_instance_id=post["author_instance_id"],
                    sender_member_name=post["author_name"],
                    room=room,
                    at_boot=True,
                )
                # Outcome-faithful counting: delivered = the cursor
                # advanced this pass (injected / enqueued / proposal);
                # deferred = the row stays above the cursor and will be
                # re-derived next boot (deferred_cursor / parked_paused).
                if outcome in ("deferred_cursor", "parked_paused"):
                    deferred += 1
                else:
                    delivered += 1
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "boot deliver row failed (room=%s seq=%s member=%s): %r",
                    row["room_id"], row["seq"], row["instance_id"], exc,
                )
        counts = {
            "pending_rows": len(pending),
            "rooms_with_pending": len(rooms),
            "members_with_pending": len(members),
            "delivered": delivered,
            "deferred": deferred,
            "filtered_not_targets": filtered,
        }
        self._logger.info(
            "room boot delivery: pending_tagged=%d delivered=%d deferred=%d "
            "filtered_not_targets=%d rooms=%d members=%d",
            counts["pending_rows"], delivered, deferred, filtered,
            len(rooms), len(members),
        )
        return counts

    async def _resolve_tagged_target_identities(
        self, *, room_id: str, post: dict[str, Any]
    ) -> tuple[set[str], set[str]]:
        """Resolve a post's tagged names against the room's ACTIVE roster —
        route_post's resolver semantics IDENTICAL (§6 / A-P3-6): a tag
        matches an active roster member_name, or a UUID-shaped tag
        resolves to an active roster instance_id (32-hex OR the 36-char
        dashed form — P5 constraint (a)); unresolvable names are NOT
        targets (route_post warns + skips them). LEFT members are not on
        the active roster, so a tag to a LEFT member resolves to nothing.
        Returns the resolved targets as (member_name set, instance_id
        set). Reads ride the room's fail-closed read predicate with the
        post AUTHOR as caller — the same predicate route_post's roster
        read uses (sender = author)."""
        roster = await self._room.list_members(
            room_id,
            caller_instance_id=post["author_instance_id"],
            active_only=True,
        )
        resolved, _warnings = resolve_room_targets(
            roster, list(post.get("tagged_members") or []), kind="tag"
        )
        names = {m["member_name"] for m in resolved}
        iids = {m["instance_id"] for m in resolved}
        return names, iids
