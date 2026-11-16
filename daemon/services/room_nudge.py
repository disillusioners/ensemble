"""Agent group chat P4 — the nudge layer (rhythm-aware delivery, arm (a)+(a2)).

The Emperor's D-S1 ruling mechanized:

* **(a) TODO-STEP BOUNDARY** — the reminder slot EVERY todo update
  returns (``{"todos": [...], "reminder": str}``) carries a lightweight
  "N unread" note when the room guards allow. The carrier is the
  tool-result reminder — NOT an injection, NOT an enqueue (corrected
  C1 seam; adaptation #3). The carrier is NON-NARROWING: the note is
  decided by the guards (``evaluate_todo_boundary``), never by the carrier
  firing only on done-updates.

* **(a2) COMPLETION BOUNDARY** — the ``room_completion_gate`` graph
  node (wired in ``daemon/graph.py``) evaluates this coordinator at
  the turn's END decision; unread>0 at completion ⇒ a last-chance
  "check unread team messages" nudge and a CONTINUE verdict.

* **THE LOOP-GUARD (binding clause)** — "MUST carry a loop-guard … so
  a busy room cannot block completion forever". THE GUARANTEE IS THE
  SHARED LATCH: one manager-level RAM registry
  (``manager._room_nudge_latch``) keyed ``(instance_id, room_id)``
  shared by BOTH arms. A nudge fires only when

      unread > 0  AND  max(seq) > last_nudged  AND  turn not spent

  — the turn component (the id of the instance's current root graph
  task, ``manager._graph_tasks``) caps the family at ≤1 room nudge per
  turn per room, and the read-watermark (``last_nudged_seq`` — a RAM
  LATCH ENTRY, NOT the durable P1 member cursors
  ``last_read_seq`` / ``last_delivered_seq``, which the repository
  scan reads separately) prevents turn-after-turn re-nagging for the
  same message batch. A busy room therefore delays completion by AT
  MOST one continuation per turn — never blocks it.

* **STRONG FORM** — a TAGGED row pending for the completing member at
  completion (above BOTH cursors) names the @tag in the gate reminder
  AND re-attempts the lane-(a) injection in the same continuation
  (``RoomDeliveryService.redeliver_tag_for_member``). The tag
  re-attempt carries its OWN per-turn spend (``tag_turn``) so a spent
  reminder watermark cannot orphan a pending escalation: the tag
  re-surfaces next turn. Each successful re-attempt advances the
  delivered cursor (Y14, inside the unchanged lane), so the pending
  set shrinks; failures leave it durable and re-derive next turn.

RAM latch payload per entry (N8 — a bounded duplicate per restart is
accepted because the registry is RAM):

    {"reminder_turn": <turn key or None>,   # per-turn reminder spend
     "last_nudged_seq": <int>,              # read-watermark (re-arm; a RAM
                                            #   latch entry — NOT the P1
                                            #   durable member cursors)
     "tag_turn": <turn key or None>}        # per-turn tag re-attempt spend

Every DB touch is off-loop (the room service bridges via
``asyncio.to_thread``); every consumer-facing entry point fails open —
a nudge problem must never break a todo tool result or a graph turn.
"""

from __future__ import annotations

import logging
from typing import Any

from daemon.repositories.room.models import RoomState

logger = logging.getLogger(__name__)

#: The (a2) gate node's name in the instance graph (graph.py wiring).
ROOM_COMPLETION_GATE_NODE_NAME = "room_completion_gate"

#: Session-state channel the gate node writes and its out-edge router
#: reads (same shape as ``attestation_route``). "agent" = CONTINUE
#: (the nudge HumanMessage rides in the same node return); absent/None
#: = pass through to the next chain stage (attestation gate / END).
ROOM_COMPLETION_ROUTE_KEY = "room_completion_route"

#: ``additional_kwargs`` marker on the gate's nudge HumanMessage —
#: identifies server-authored room nudges (the
#: ``language_check_reminder`` stamp precedent). Combined with
#: ``injected_message=True`` the message is excluded from every
#: real-user scan (attestation ladder) and survives compaction
#: verbatim (compaction's injected-message protection).
ROOM_NUDGE_MARKER = "room_nudge_reminder"

#: Session-state channel carrying the strong-form tag re-attempt
#: outcomes — a checkpoint-persisted diagnostic channel, read by tests.
ROOM_NUDGE_TAG_REATTEMPT_OUTCOMES_KEY = "room_nudge_tag_reattempt_outcomes"

__all__ = [
    "ROOM_COMPLETION_GATE_NODE_NAME",
    "ROOM_COMPLETION_ROUTE_KEY",
    "ROOM_NUDGE_MARKER",
    "ROOM_NUDGE_TAG_REATTEMPT_OUTCOMES_KEY",
    "RoomNudgeCoordinator",
]

#: Cap sanity bound for the per-scan reminder text (lane-(a)
#: notification discipline: ≤300-char leads; multiple rooms join
#: names, never bodies).
_MAX_ROOMS_NAMED = 3
#: Cap on the tag rows the (a2) reminder names — its own bound (the
#: value today matches the room-name list cap above).
_MAX_TAG_ROWS_NAMED = 3
#: Cap on the arm (a) note text ONLY. The (a2) reminder is bounded BY
#: CONSTRUCTION instead (≤ _MAX_ROOMS_NAMED rooms, ≤ _MAX_TAG_ROWS_NAMED
#: tag rows) — this cap is deliberately NOT applied there (the (a2)
#: reminder text is pin-asserted).
_MAX_NOTE_CHARS = 300


def _short_room_list(names: list[str]) -> str:
    shown = names[:_MAX_ROOMS_NAMED]
    tail = len(names) - len(shown)
    out = ", ".join(repr(n) for n in shown)
    if tail > 0:
        out += f" (+{tail} more)"
    return out


class RoomNudgeCoordinator:
    """The shared latch + watermark + unread scan behind both nudge arms.

    ONE instance per manager. The latch registry itself is the
    MANAGER-LEVEL ``manager._room_nudge_latch`` dict — threaded to both
    closures (the todo-tool factory and the gate node) through the
    manager handle, the same threading shape as ``injection_slot`` /
    ``live_hub``. The coordinator never swaps the dict object.

    All methods fail open: any exception degrades to "no nudge" (empty
    note / non-firing decision) — a room-nudge fault must never break
    the todo carrier or the graph turn.
    """

    def __init__(self, manager: Any, logger: logging.Logger | None = None) -> None:
        self._manager = manager
        # The parameter shadows the module logger — the fallback
        # re-resolves it explicitly (the same logger object either way).
        self._log = logger if logger is not None else logging.getLogger(__name__)
        #: RAM interest snapshot — LANE-INTRODUCED in P4 for the
        #: (since-DROPPED, A-P4-8) drain-wrapper backstop probe:
        #: instance_id → True when the last DB scan saw ≥1 nudge-armed
        #: room. Refreshed on every SUCCESSFUL coordinator scan (the
        #: fault path keeps the prior value). NO production consumer
        #: remains (the router probe died with the backstop); the
        #: surface is retained per the O4 leader disposition (dead
        #: surface, docstring-freshness only) — the executed
        #: composition pin 3 arms it solely to prove the armed state
        #: CANNOT bypass the enabled seam's language_check.
        self._ram_interest: dict[str, bool] = {}

    # ── latch primitives ──────────────────────────────────────────────

    @property
    def latch(self) -> dict[tuple[str, str], dict[str, Any]]:
        """THE shared registry (manager-level RAM, keyed (iid, room))."""
        return getattr(self._manager, "_room_nudge_latch", {})

    def _entry(self, instance_id: str, room_id: str) -> dict[str, Any]:
        entries = self.latch
        key = (instance_id, room_id)
        entry = entries.get(key)
        if entry is None:
            entry = {
                "reminder_turn": None,
                "last_nudged_seq": 0,
                "tag_turn": None,
            }
            entries[key] = entry
        return entry

    def resolve_turn_key(self, instance_id: str) -> int | None:
        """The per-turn identity: the current root graph task's id().

        Every node and tool execution of one turn runs inside the
        instance's root graph task (``manager._graph_tasks[instance_id]``),
        so ``id(task)`` is stable within the turn and changes on the
        next dispatch — exactly the once-per-turn scope. When the
        registry has no entry (test embeddings without the manager
        task map), ``None`` is returned and used AS the turn key: two
        evaluations in the same registry-less context then share the
        spend (fail-safe direction — nudges get FEWER, not more).
        """
        try:
            tasks = getattr(self._manager, "_graph_tasks", None) or {}
            task = tasks.get(instance_id)
            return id(task) if task is not None else None
        except Exception:  # noqa: BLE001 — fail open
            return None

    # ── scan ──────────────────────────────────────────────────────────

    async def _snapshot(self, instance_id: str) -> list[dict[str, Any]]:
        """Off-loop per-room pending scan (read-only substrate read)."""
        service = getattr(self._manager, "_room_chat_service", None)
        if service is None:
            return []
        return await service.nudge_snapshot_for_instance(instance_id)

    @staticmethod
    def _reminder_armed(snapshot_room: dict[str, Any], entry: dict[str, Any]) -> bool:
        """The watermark predicate for ONE room: new messages since the
        last nudge AND something actually unread AND the room is
        ACTIVE.

        C4 (2026-11-16 review): the scan restricts to
        ``state='active'`` repository-side; this defensive room-state
        check mirrors the ratified capability row ("closed: no"
        new-activity nudges) should a future caller ever pass a
        closed-room row — a closed room is read-only, its members are
        never nudged (its pending deliveries still ride the P3
        lanes).
        """
        if snapshot_room.get("room_state") != RoomState.ACTIVE.value:
            return False
        return (
            int(snapshot_room.get("unread") or 0) > 0
            and int(snapshot_room.get("max_seq") or 0)
            > int(entry.get("last_nudged_seq") or 0)
        )

    # ── arm (a) — todo-step boundary ──────────────────────────────────

    async def evaluate_todo_boundary(self, instance_id: str) -> str:
        """The (a) arm: the note appended to a todo-update tool result.

        Fires on EVERY successful todo update (the carrier is
        non-narrowing — this filter decides). When a room is armed
        (unread>0 + watermark + turn not spent) the note is a
        lightweight "N unread" line and the room's watermark + turn
        spend are consumed. Otherwise "" — the tool result is
        byte-identical to pre-P4.
        """
        try:
            turn_key = self.resolve_turn_key(instance_id)
            snapshot = await self._snapshot(instance_id)
            # (room, latch entry) pairs — the entry is looked up ONCE
            # per room and reused by the consumption loop below.
            armed: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for room in snapshot:
                entry = self._entry(instance_id, room["room_id"])
                if (
                    self._reminder_armed(room, entry)
                    and entry.get("reminder_turn") != turn_key
                ):
                    armed.append((room, entry))
            if not armed:
                self._remember_interest(instance_id, False)
                return ""
            # Consume the spend for EVERY armed room, then compose the
            # note (counts are read BEFORE consumption).
            total_unread = 0
            for room, entry in armed:
                total_unread += int(room.get("unread") or 0)
                entry["last_nudged_seq"] = int(
                    room.get("max_seq") or 0
                )
                entry["reminder_turn"] = turn_key
            names = [
                str(r.get("room_name") or r["room_id"]) for r, _ in armed
            ]
            note = (
                f"\n\n📬 Nudge: {total_unread} unread message(s) in your "
                f"team chatroom(s) {_short_room_list(names)} — consider "
                f"checking your team chat."
            )
            if len(note) > _MAX_NOTE_CHARS:
                note = note[: _MAX_NOTE_CHARS - 3] + "..."
            self._remember_interest(instance_id, True)
            return note
        except Exception as exc:  # noqa: BLE001 — nudge never breaks the carrier
            self._log.warning(
                "room nudge (a) evaluation failed for %s — no note: %r",
                str(instance_id)[:8],
                exc,
            )
            return ""

    # ── arm (a2) — completion boundary ────────────────────────────────

    async def evaluate_completion(self, instance_id: str) -> dict[str, Any]:
        """The (a2) arm: the completion-gate decision.

        Returns ``{"fire": bool, "reminder": str, "pending_tags": [...]}``.
        ``reminder`` is the full nudge text (SYSTEM CONTEXT header
        discipline) for the gate node to carry as a HumanMessage;
        ``pending_tags`` are the strong-form rows the gate must
        re-attempt (the re-attempt itself routes through
        ``RoomDeliveryService.redeliver_tag_for_member`` — invoked by
        the gate, not here, so a delivery failure cannot corrupt the
        decision).

        Fire predicate (the loop-guard, per room):
            reminder side: unread>0 AND max(seq)>last_nudged AND turn unspent
            tag side:      pending tag rows exist AND tag turn unspent
        Either side firing ⇒ CONTINUE. Spends are consumed ONLY on fire.
        """
        empty: dict[str, Any] = {"fire": False, "reminder": "", "pending_tags": []}
        try:
            turn_key = self.resolve_turn_key(instance_id)
            snapshot = await self._snapshot(instance_id)
            # (room, latch entry) pairs — the entry is looked up ONCE
            # per room and reused by the consumption loop below.
            reminder_rooms: list[tuple[dict[str, Any], dict[str, Any]]] = []
            pending_tags: list[dict[str, Any]] = []
            for room in snapshot:
                entry = self._entry(instance_id, room["room_id"])
                if self._reminder_armed(room, entry) and (
                    entry.get("reminder_turn") != turn_key
                ):
                    reminder_rooms.append((room, entry))
                rows = room.get("pending_tags") or []
                if rows and room.get("room_state") == RoomState.ACTIVE.value:
                    # C4 belt: a closed room's pending tags never fire
                    # the strong form (mirrors _reminder_armed).
                    if entry.get("tag_turn") != turn_key:
                        pending_tags.extend(
                            dict(
                                row,
                                room_id=room["room_id"],
                                room_name=room.get("room_name")
                                or room["room_id"],
                                member_name=room.get("member_name"),
                            )
                            for row in rows
                        )
            if not reminder_rooms and not pending_tags:
                self._remember_interest(instance_id, False)
                return empty

            # Compose the reminder BEFORE consuming spends (counts are
            # pre-consumption).
            total_unread = sum(
                int(r.get("unread") or 0) for r, _ in reminder_rooms
            )
            lines: list[str] = []
            if reminder_rooms:
                names = [
                    str(r.get("room_name") or r["room_id"])
                    for r, _ in reminder_rooms
                ]
                lines.append(
                    f"You have {total_unread} unread message(s) in your "
                    f"team chatroom(s) {_short_room_list(names)}."
                )
            elif pending_tags:
                # Reminder watermark spent but the strong form stands:
                # the escalation still warrants the last-chance check.
                lines.append(
                    "A tagged team message is still waiting for you in "
                    "your team chatroom."
                )
            if pending_tags:
                tag_bits = []
                seen: set[str] = set()
                for row in pending_tags[:_MAX_TAG_ROWS_NAMED]:
                    author = str(row.get("author_name") or "a teammate")
                    tag_names = [
                        t for t in (row.get("tagged_members") or [])
                        if t not in seen
                    ]
                    for t in tag_names:
                        seen.add(t)
                    tag_bits.append(
                        f"@{tag_names[0] if tag_names else '?'} from "
                        f"{author} (room "
                        f"{str(row.get('room_name') or row.get('room_id'))!r}, "
                        f"seq {row.get('seq')})"
                    )
                lines.append(
                    "Tagged message(s) pending: " + "; ".join(tag_bits) + "."
                )
            reminder = (
                "[SYSTEM CONTEXT: Team Room Nudge]\n\n"
                "Before completing: check your unread team chatroom "
                "messages.\n\n"
                + "\n".join(lines)
                + "\n\nThis is the last-chance check before your turn "
                "ends — read the room, act on anything addressed to you, "
                "then finish."
            )

            # Consume the spends (only on fire).
            for room, entry in reminder_rooms:
                entry["last_nudged_seq"] = int(room.get("max_seq") or 0)
                entry["reminder_turn"] = turn_key
            if pending_tags:
                for room in snapshot:
                    if room.get("pending_tags"):
                        entry = self._entry(instance_id, room["room_id"])
                        entry["tag_turn"] = turn_key

            self._remember_interest(instance_id, True)
            return {
                "fire": True,
                "reminder": reminder,
                "pending_tags": pending_tags,
            }
        except Exception as exc:  # noqa: BLE001 — the gate fails open
            self._log.warning(
                "room nudge (a2) evaluation failed for %s — gate passes: %r",
                str(instance_id)[:8],
                exc,
            )
            return empty

    # ── drain-wrapper backstop probe (RAM-only) ───────────────────────

    def _remember_interest(self, instance_id: str, value: bool) -> None:
        try:
            self._ram_interest[instance_id] = value
        except Exception:  # noqa: BLE001 — snapshot is best-effort
            pass

    def ram_interest(self, instance_id: str) -> bool:
        """RAM interest snapshot probe — LANE-INTRODUCED in P4.

        History: created for the drain-wrapper backstop peek, which
        was DROPPED per the A-P4-8 adjudication (the peek-as-
        implemented bypassed language validation on the enabled seam
        and was redundant on the disabled seam). NO production
        consumer remains; the surface is retained per the O4 leader
        disposition (dead surface, docstring-freshness only). Semantics
        unchanged: True when the most recent SUCCESSFUL coordinator
        scan for this instance saw nudge-armed rooms AND no scan has
        since cleared it (the fault path keeps the prior value). This
        was never the fire predicate — the in-chain gate node
        re-evaluates fresh (DB + latch) on every completion.
        """
        try:
            return bool(self._ram_interest.get(instance_id))
        except Exception:  # noqa: BLE001 — fail open
            return False

    # ── strong-form re-attempt ────────────────────────────────────────

    async def reattempt_pending_tags(
        self, instance_id: str, pending_tags: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Re-run the lane-(a) machinery for the completing member's
        pending tagged rows (the strong form's delivery half).

        Each row rides ``RoomDeliveryService.redeliver_tag_for_member``
        — the UNCHANGED P3 lane (fresh status, DEFECT-A downgrade,
        Y14 admission-gated cursor, Ruling-A mint). Outcomes are
        returned per row; failures are contained (warn + leave-durable
        — the row re-derives next turn). Never raises.
        """
        results: list[dict[str, Any]] = []
        delivery = getattr(self._manager, "_room_delivery_service", None)
        if delivery is None:
            return results
        for row in pending_tags:
            outcome = "deferred_cursor"
            try:
                room_id = row["room_id"]
                seq = int(row["seq"])
                # The member's own membership payload (name/role) rides
                # the lane call; the snapshot already carries it.
                member = {
                    "room_id": room_id,
                    "instance_id": instance_id,
                    "member_name": row.get("member_name")
                    or str((row.get("tagged_members") or [""])[0]),
                }
                post = {
                    "id": row.get("message_id"),
                    "room_id": room_id,
                    "seq": seq,
                    "author_instance_id": row.get("author_instance_id"),
                    "author_name": row.get("author_name"),
                    "text": row.get("text"),
                    "tagged_members": row.get("tagged_members") or [],
                    "addressed_members": row.get("addressed_members") or [],
                }
                outcome = await delivery.redeliver_tag_for_member(
                    room_id=room_id, post=post, member=member
                )
            except Exception as exc:  # noqa: BLE001 — leave durable
                self._log.warning(
                    "strong-form tag re-attempt failed (room=%s seq=%s "
                    "member=%s): %r",
                    str(row.get("room_id"))[:8],
                    row.get("seq"),
                    str(instance_id)[:8],
                    exc,
                )
                outcome = "deferred_cursor"
            results.append(
                {
                    "room_id": row.get("room_id"),
                    "seq": row.get("seq"),
                    "outcome": outcome,
                }
            )
        return results
