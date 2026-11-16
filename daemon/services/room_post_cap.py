"""Agent group chat P6 — the §9 per-turn post budget (A-P6-1).

Architecture §9 (cost discipline): *"post ≤4000 chars · ≤4
posts/member/turn · no daily quota"*. The body cap (4000 chars) is
enforced in the tool body (P5); the PER-TURN COUNT cap is this
module.

**The converged shape (A-P6-1, BINDING).**

* SERVER-SIDE only — the cap lives in the daemon; the FE never sees it
  and never enforces it.
* The counter lives at the MANAGER level, per ``instance_id``, and is
  RESET at the turn boundary — mirroring the ``_room_nudge_latch``
  precedent (the P4 latch): the per-turn identity is the id of the
  instance's current ROOT GRAPH TASK, which is stable within a turn
  and changes on the next dispatch. A new turn key ⇒ a fresh budget.
  NO P4-owned seam is touched: the shape is MIRRORED, not shared.
* The counter is incremented in ``room_post`` at the PRE-APPEND region
  — the append is the commit point of the room log, so the check must
  precede it (the same posture as the membership gate).
* The refusal at the 5th post is TYPED (the house refusal envelope):
  four posts succeed, the fifth is refused.

**Fail-safe direction.** A missing turn key (``None`` — a registry-less
embedding, e.g. a test double) makes consecutive turns share one
budget: the cap then grants FEWER posts, never more. The same posture
the P4 latch takes.

**RAM registry.** The counters are RAM (like the latch): a restart
re-grants the budget. The cost cap is a soft budget, not a durable
quota — the durable substrate is the room log itself.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "RoomPostTurnCap",
    "ROOM_POSTS_PER_TURN",
]

#: §9's budget: at most FOUR posts per member per turn. The 5th post
#: is refused.
ROOM_POSTS_PER_TURN = 4


class RoomPostTurnCap:
    """The manager-level per-``instance_id`` per-turn post budget.

    One instance per manager. The counter REGISTRY is the manager-level
    ``manager._room_post_turn_counter`` dict (the same shape as the
    P4 ``manager._room_nudge_latch``): the manager owns the dict, this
    service owns the policy.
    """

    def __init__(
        self,
        *,
        manager: Any,
        logger: logging.Logger | None = None,
        max_posts_per_turn: int = ROOM_POSTS_PER_TURN,
    ) -> None:
        self._manager = manager
        self._log = logger if logger is not None else logging.getLogger(__name__)
        self._max = int(max_posts_per_turn)

    # ── the manager-level registry ────────────────────────────────────

    @property
    def counters(self) -> dict[str, dict[str, Any]]:
        """THE manager-level registry (``instance_id`` → entry).

        The entry shape mirrors the P4 latch entry: ``{"turn": <turn
        key or None>, "count": <int>}``. The turn key is the RESET
        hook — a changed key ⇒ the count starts over.
        """
        registry = getattr(self._manager, "_room_post_turn_counter", None)
        if registry is None:
            registry = {}
            try:
                self._manager._room_post_turn_counter = registry
            except Exception:  # noqa: BLE001 — a read-only manager degrades to RAM
                pass
        return registry

    # ── the turn identity (the P4 latch shape) ────────────────────────

    def resolve_turn_key(self, instance_id: str) -> int | None:
        """The per-turn identity: the current root graph task's id().

        Mirrors ``RoomNudgeCoordinator.resolve_turn_key``: every node
        and tool execution of one turn runs inside the instance's root
        graph task (``manager._graph_tasks[instance_id]``), so
        ``id(task)`` is stable within the turn and changes on the next
        dispatch. ``None`` (no task map — a registry-less embedding)
        is used AS the turn key: consecutive evaluations then share the
        budget (fail-safe: fewer posts, never more).
        """
        try:
            tasks = getattr(self._manager, "_graph_tasks", None) or {}
            task = tasks.get(instance_id)
            return id(task) if task is not None else None
        except Exception:  # noqa: BLE001 — fail safe
            return None

    # ── the check ─────────────────────────────────────────────────────

    def check(self, instance_id: str) -> tuple[bool, dict[str, Any]]:
        """Consume ONE post slot for ``instance_id``.

        Returns ``(allowed, detail)``. ``detail`` carries the numbers
        the refusal message quotes (the budget, the posts already
        spent in this turn, the turn key). The counter is NOT advanced
        on a refusal — a refused post does not consume budget.
        """
        registry = self.counters
        turn_key = self.resolve_turn_key(instance_id)
        entry = registry.get(instance_id)
        if entry is None or entry.get("turn") != turn_key:
            # A new turn ⇒ a fresh budget (the RESET hook).
            entry = {"turn": turn_key, "count": 0}
            registry[instance_id] = entry
        spent = int(entry.get("count") or 0)
        detail = {
            "max_posts_per_turn": self._max,
            "posts_this_turn": spent,
            "turn_key": turn_key,
        }
        if spent >= self._max:
            return False, detail
        entry["count"] = spent + 1
        detail["posts_this_turn"] = spent + 1
        return True, detail

    def reset(self, instance_id: str) -> None:
        """Drop the instance's budget (TEST/OPERATOR-surface only).

        Production turn boundaries do NOT call this — the budget
        resets inside :meth:`check` when the turn key changes (a new
        turn mints a fresh entry on the spot).
        """
        self.counters.pop(instance_id, None)
