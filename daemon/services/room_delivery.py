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
addressed predicate (terminal → parent notice; running → no action;
alive → digest no-wake; paused → no action; left → skip+warning).

This module is the service the P5 room tools will call; it is also the
boot-delivery entry point.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.room.models import (
    NON_TERMINAL_INSTANCE_STATUSES_FOR_ROOMS,
    TERMINAL_INSTANCE_STATUSES_FOR_ROOMS,
    RoomMessageKind,
)
from daemon.repositories.room.service import RoomChatService

logger = logging.getLogger(__name__)

# The derived CAS set for lane (b): non-terminal − {running, paused}.
# Derived STRUCTURALLY from the enum (never from ALIVE_INSTANCE_STATUSES,
# which omits ``waiting``). Membership-pinned against future enum
# additions by the totality pin in test_room_models_and_migration.py.
LANE_B_CAS_ALLOWED: frozenset[str] = NON_TERMINAL_INSTANCE_STATUSES_FOR_ROOMS - {
    "running",
    "paused",
}


def _is_uuid_like(name: str) -> bool:
    # 32-hex UUID fallback (§6): a bare 32-hex string resolves to an
    # active roster member whose instance_id matches.
    return len(name) == 32 and all(c in "0123456789abcdef" for c in name.lower())


class RoomDeliveryService:
    """The P3 delivery router."""

    def __init__(self, manager: Any, room_service: RoomChatService, logger=None) -> None:
        self._manager = manager
        self._room = room_service
        self._logger = logger or logging.getLogger(__name__)

    # ── public entry ─────────────────────────────────────────────────────

    async def route_post(
        self,
        *,
        room_id: str,
        post: dict[str, Any],
        sender_instance_id: str,
        sender_member_name: str,
    ) -> dict[str, Any]:
        """Route a freshly-appended room post per the lane map + addressing.

        Returns a summary dict:
          {
            "tag_outcomes": [{"member_name", "instance_id", "outcome"}, ...],
            "addressed_outcomes": [{"member_name", "instance_id", "outcome"}, ...],
            "warnings": [str, ...],
          }
        """
        room = await self._room.get_room(room_id)
        if room is None:
            return {"tag_outcomes": [], "addressed_outcomes": [], "warnings": ["room not found"]}
        members = await self._room.list_members(
            room_id, caller_instance_id=sender_instance_id, active_only=True
        )
        # roster lookup: member_name -> member dict; instance_id -> member dict
        by_name = {m["member_name"]: m for m in members}
        by_iid = {m["instance_id"]: m for m in members}

        def resolve(name: str) -> dict[str, Any] | None:
            if name in by_name:
                return by_name[name]
            if _is_uuid_like(name) and name in by_iid:
                return by_iid[name]
            return None

        tagged_raw = list(post.get("tagged_members") or [])
        addressed_raw = list(post.get("addressed_members") or [])

        # Resolve tags (unresolvable → warning, skip).
        tagged_resolved: list[dict[str, Any]] = []
        warnings: list[str] = []
        for name in tagged_raw:
            m = resolve(name)
            if m is None:
                warnings.append(f"tag unresolved: {name!r}")
                continue
            tagged_resolved.append(m)

        # Resolve addressed (unresolvable / non-member / LEFT → warning, skip).
        addressed_resolved: list[dict[str, Any]] = []
        for name in addressed_raw:
            m = resolve(name)
            if m is None:
                warnings.append(f"addressed unresolved: {name!r}")
                continue
            # LEFT members: resolve() only returns active roster, so a LEFT
            # member is already unresolvable here. Cross-room: resolve()
            # reads the room's own roster, so cross-room names never match.
            addressed_resolved.append(m)

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
                await self._room.emit_room_event(
                    o["instance_id"],
                    "room_tag_delivery",
                    {
                        "room_id": room_id,
                        "seq": post["seq"],
                        "member_name": o["member_name"],
                        "outcome": o["outcome"],
                    },
                )
            except Exception as exc:  # noqa: BLE001 — audit must never break delivery
                self._logger.warning("room_tag_delivery emit failed: %r", exc)

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
        post_id = post["id"]

        # Read fresh status (lane assignment FIRST).
        status = await self._room.get_instance_status(member_iid)
        if status is None:
            # Instance row gone — leave durable (deferred_cursor).
            return "deferred_cursor"

        # Terminal → (c) parent proposal (Ruling A).
        if status in TERMINAL_INSTANCE_STATUSES_FOR_ROOMS:
            admitted = await self._mint_proposal(
                room_id=room_id,
                post=post,
                member=member,
                trigger="tag_terminal",
                sender_instance_id=sender_instance_id,
                sender_member_name=sender_member_name,
            )
            if not admitted:
                # F1/A-P3-22: no durable proposal row exists — advancing
                # the cursor would PERMANENTLY hide the undelivered
                # notification from re-derivation. Leave durable.
                return "deferred_cursor"
            # Y14: terminal member's delivered cursor advances at admission.
            await self._room.advance_delivered_cursor(room_id, member_iid, seq)
            if at_boot:
                # Pinned pair #2 (absolute wording, A-P3-21): proposals
                # are NEVER parent-routed at boot — mint-only, durable.
                return "proposal"
            # Route the proposal to the parent per the parent-target matrix.
            await self._route_proposal_to_parent(
                room_id=room_id,
                post=post,
                member=member,
                parent_iid=room["owner_instance_id"],
                trigger="tag_terminal",
                sender_member_name=sender_member_name,
            )
            return "proposal"

        # Paused → (d) park + audit.
        if status == "paused":
            await self._room.emit_room_event(
                member_iid,
                "room_tag_delivery",
                {
                    "room_id": room_id,
                    "seq": seq,
                    "member_name": member_name,
                    "outcome": "parked_paused",
                },
            )
            return "parked_paused"

        # Running → (a) injection, with DEFECT-A guard.
        if status == "running":
            if not self._manager.has_live_graph_task(member_iid):
                # DEFECT-A downgrade → lane (b) shape.
                return await self._lane_b_enqueue(
                    room_id=room_id,
                    post=post,
                    member=member,
                    seq=seq,
                    member_iid=member_iid,
                    graphless=True,
                )
            # Race retry: 1 bounded retry.
            for attempt in range(2):
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
                    self._logger.warning("injection attempt %d failed: %r", attempt, exc)
                    if attempt == 0:
                        await asyncio.sleep(0)
                        continue
            # Race exhausted → deferred_cursor.
            await self._room.emit_room_event(
                member_iid,
                "room_tag_delivery",
                {
                    "room_id": room_id,
                    "seq": seq,
                    "member_name": member_name,
                    "outcome": "deferred_cursor",
                },
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
            )

        # Fallback (shouldn't happen — total map covers all 10).
        return "deferred_cursor"

    async def _lane_b_enqueue(
        self,
        *,
        room_id: str,
        post: dict[str, Any],
        member: dict[str, Any],
        seq: int,
        member_iid: str,
        graphless: bool,
    ) -> str:
        member_name = member["member_name"]
        # Rowcount-0 fallback: re-read fresh status; take the total map's
        # branch for it.
        rowcount = await self._room.cas_instance_status_to_running(
            member_iid, LANE_B_CAS_ALLOWED
        )
        if rowcount == 0:
            fresh = await self._room.get_instance_status(member_iid)
            if fresh is None:
                return "deferred_cursor"
            if fresh in TERMINAL_INSTANCE_STATUSES_FOR_ROOMS:
                # Re-route as terminal (proposal) — but only if we haven't
                # already. Guard: mint proposal + parent route, BOTH gated
                # on admission (F1/A-P3-22).
                admitted = await self._mint_proposal(
                    room_id=room_id,
                    post=post,
                    member=member,
                    trigger="tag_terminal",
                    sender_instance_id=post["author_instance_id"],
                    sender_member_name=post["author_name"],
                )
                if not admitted:
                    return "deferred_cursor"
                await self._room.advance_delivered_cursor(room_id, member_iid, seq)
                await self._route_proposal_to_parent(
                    room_id=room_id,
                    post=post,
                    member=member,
                    parent_iid=(await self._room.get_room(room_id))["owner_instance_id"],
                    trigger="tag_terminal",
                    sender_member_name=post["author_name"],
                )
                return "proposal"
            if fresh == "paused":
                await self._room.emit_room_event(
                    member_iid, "room_tag_delivery",
                    {"room_id": room_id, "seq": seq, "member_name": member_name,
                     "outcome": "parked_paused"},
                )
                return "parked_paused"
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
                await self._room.emit_room_event(
                    member_iid, "room_tag_delivery",
                    {"room_id": room_id, "seq": seq, "member_name": member_name,
                     "outcome": "deferred_cursor"},
                )
                return "deferred_cursor"
            # Unknown/other → deferred.
            await self._room.emit_room_event(
                member_iid, "room_tag_delivery",
                {"room_id": room_id, "seq": seq, "member_name": member_name,
                 "outcome": "deferred_cursor"},
            )
            return "deferred_cursor"

        # CAS succeeded → member is now running. Busy-guard (copied shape).
        try:
            stats = await self._manager.get_queue_stats(member_iid)
            busy = bool(stats.get("pending_count", 0) > 0 or stats.get("processing_count", 0) > 0)
        except Exception:  # noqa: BLE001
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
                except Exception:  # noqa: BLE001
                    pass
            await self._room.emit_room_event(
                member_iid, "room_tag_delivery",
                {"room_id": room_id, "seq": seq, "member_name": member_name,
                 "outcome": "deferred_cursor"},
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
            await self._room.emit_room_event(
                member_iid, "room_tag_delivery",
                {"room_id": room_id, "seq": seq, "member_name": member_name,
                 "outcome": "deferred_cursor"},
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
        post_id = post["id"]

        status = await self._room.get_instance_status(member_iid)
        if status is None:
            return "digest_only"

        # terminal → (c′) parent notice.
        if status in TERMINAL_INSTANCE_STATUSES_FOR_ROOMS:
            admitted = await self._mint_proposal(
                room_id=room_id,
                post=post,
                member=member,
                trigger="addressed_terminal",
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
                excerpt=(post.get("text") or "")[:120],
            )
            if parent_status == "running" and self._manager.has_live_graph_task(parent_iid):
                self._manager.set_injection(
                    parent_iid, notice_text[:400],
                    source=f"room:{room_id}:{sender_member_name}:{seq}:author={sender_instance_id}",
                )
                await self._room.emit_room_event(
                    parent_iid, "room_tag_delivery",
                    {"room_id": room_id, "seq": seq, "member_name": member_name,
                     "outcome": "proposal"},
                )
            elif parent_status == "running" or parent_status in (
                "idle", "queued", "waiting", "waiting_children"
            ):
                # DEFECT-A downgrade: a graphless-running parent (running,
                # no live graph task) takes the lane-(b) shape — enqueue —
                # instead of silently leaving the notice durable-pending.
                try:
                    await self._manager.enqueue_message(
                        parent_iid, notice_text[:400],
                        source=f"internal_agent:{sender_instance_id}",
                        metadata={"room_id": room_id, "seq": seq, "kind": "task_proposal"},
                    )
                except Exception as exc:  # noqa: BLE001
                    self._logger.warning("parent enqueue failed: %r", exc)
            elif parent_status == "paused":
                await self._room.emit_room_event(
                    parent_iid, "room_tag_delivery",
                    {"room_id": room_id, "seq": seq, "member_name": member_name,
                     "outcome": "parked_paused"},
                )
            # parent terminal → one-hop durable pending (no routing).
            await self._room.emit_room_event(
                parent_iid, "room_proposal",
                {"room_id": room_id, "seq": seq, "member_name": member_name,
                 "trigger": "addressed_terminal", "outcome": "pending"},
            )
            return "parent_notice"

        # running → no delivery action.
        if status == "running":
            return "digest_only"

        # alive-not-running → digest, no wake.
        if status in ("idle", "queued", "waiting", "waiting_children"):
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
                    after_seq=0, limit=10000,
                )
            except Exception:  # noqa: BLE001 — probe failure ≠ proof of absence
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
            f"excerpt: \"{(post.get('text') or '')[:120]}\" (seq {post['seq']})\n"
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
            self._logger.warning("proposal mint failed: %r", exc)
            return False
        await self._room.emit_room_event(
            sender_instance_id,
            "room_proposal",
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
                    parent_iid, notice_text[:400],
                    source=f"room:{room_id}:{sender_member_name}:{seq}:author={post['author_instance_id']}",
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("parent injection failed: %r", exc)
        elif parent_status == "running" or parent_status in (
            "idle", "queued", "waiting", "waiting_children"
        ):
            # DEFECT-A downgrade: a graphless-running parent (running, no
            # live graph task) takes the lane-(b) shape — enqueue —
            # instead of silently leaving the proposal durable-pending.
            try:
                await self._manager.enqueue_message(
                    parent_iid, notice_text[:400],
                    source=f"internal_agent:{post['author_instance_id']}",
                    metadata={"room_id": room_id, "seq": seq, "kind": "task_proposal"},
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("parent enqueue failed: %r", exc)
        elif parent_status == "paused":
            await self._room.emit_room_event(
                parent_iid, "room_tag_delivery",
                {"room_id": room_id, "seq": seq, "member_name": member_name,
                 "outcome": "parked_paused"},
            )
        # parent terminal → one-hop durable pending: do nothing (no
        # recursion, no revive). The proposal row stays in the log and is
        # surfaced when the parent next runs through a legitimate lane.

    # ── helpers ───────────────────────────────────────────────────────────

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

    async def boot_deliver(self, engine) -> dict[str, Any]:
        """Boot-time delivery: re-derive tagged undelivered rows and route
        per the lane map. Proposals/notices stay durable pending (no
        routing) per pinned pair #2.

        Returns a counts dict (mirrors run_room_boot_regen).
        """
        from daemon.repositories.room.repository import RoomRepository

        repo = RoomRepository(engine)
        pending = await asyncio.to_thread(repo.derive_undelivered_tagged)
        rooms = sorted({row["room_id"] for row in pending})
        members = sorted({row["instance_id"] for row in pending})
        # For each pending (member, row) pair, route the tagged row.
        # We need the post payload; reconstruct a minimal post dict from
        # the room log.
        delivered = 0
        deferred = 0
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
                    continue
                # Only route if tagged (derive_undelivered_tagged already
                # filtered tagged non-empty).
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
                    admitted = await self._mint_proposal(
                        room_id=row["room_id"],
                        post=post,
                        member=member,
                        trigger="tag_terminal",
                        sender_instance_id=post["author_instance_id"],
                        sender_member_name=post["author_name"],
                    )
                    if not admitted:
                        # F1/A-P3-22: no durable proposal row — the cursor
                        # advance would permanently hide the notification.
                        deferred += 1
                        continue
                    await self._room.advance_delivered_cursor(
                        row["room_id"], row["instance_id"], int(row["seq"])
                    )
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
                self._logger.warning("boot deliver row failed: %r", exc)
        counts = {
            "pending_rows": len(pending),
            "rooms_with_pending": len(rooms),
            "members_with_pending": len(members),
            "delivered": delivered,
            "deferred": deferred,
        }
        self._logger.info(
            "room boot delivery: pending_tagged=%d delivered=%d deferred=%d "
            "rooms=%d members=%d",
            counts["pending_rows"], delivered, deferred, len(rooms), len(members),
        )
        return counts
