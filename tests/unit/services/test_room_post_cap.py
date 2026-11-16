"""Agent group chat P6 — the §9 per-turn post budget (A-P6-1).

WHAT IT PROVES (the budget's own layer):

* **FOUR posts succeed, the FIFTH is refused** — the spec-faithful
  "≤4" (the refusal is typed; the budget is not consumed by the
  refused post).
* **The counter is MANAGER-LEVEL and per-instance** — two instances
  have independent budgets.
* **The reset hook is the TURN BOUNDARY** — a new turn key (the id of
  the instance's root graph task — the P4 latch's shape) starts a
  fresh budget; the SAME turn key does not.
* **A missing turn key shares the budget** — the fail-safe direction
  (fewer posts, never more), the P4 latch's posture.

The tool-level chokepoint (the refusal the AGENT sees) is pinned in
``tests/unit/tools/test_room_post_cap.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from daemon.services.room_post_cap import (
    ROOM_POSTS_PER_TURN,
    RoomPostTurnCap,
)


class _FakeTask:
    """A stand-in for the root graph task (its id() is the turn key)."""


@pytest.fixture
def manager():
    """A manager stub carrying the two seams the cap touches."""
    return SimpleNamespace(_graph_tasks={}, _room_post_turn_counter={})


@pytest.fixture
def cap(manager):
    return RoomPostTurnCap(manager=manager)


def _post(cap: RoomPostTurnCap, instance_id: str) -> bool:
    allowed, _detail = cap.check(instance_id)
    return allowed


def test_four_posts_succeed_and_the_fifth_is_refused(cap):
    for _ in range(ROOM_POSTS_PER_TURN):
        assert _post(cap, "inst-1") is True
    assert _post(cap, "inst-1") is False


def test_the_refusal_does_not_consume_the_budget(cap):
    for _ in range(ROOM_POSTS_PER_TURN):
        _post(cap, "inst-1")
    assert _post(cap, "inst-1") is False
    # A refused post does not advance the counter — the budget stays
    # spent at exactly four.
    entry = cap.counters["inst-1"]
    assert entry["count"] == ROOM_POSTS_PER_TURN


def test_the_budget_is_per_instance(cap):
    for _ in range(ROOM_POSTS_PER_TURN):
        _post(cap, "inst-1")
    assert _post(cap, "inst-1") is False
    # A DIFFERENT instance has its own budget.
    assert _post(cap, "inst-2") is True


def test_the_counter_is_manager_level(manager, cap):
    """The registry lives on the MANAGER (the P4 latch's shape) — the
    service never swaps the dict object."""
    assert cap.counters is manager._room_post_turn_counter
    _post(cap, "inst-1")
    assert manager._room_post_turn_counter["inst-1"]["count"] == 1


def test_a_new_turn_resets_the_budget(manager, cap):
    for _ in range(ROOM_POSTS_PER_TURN):
        _post(cap, "inst-1")
    assert _post(cap, "inst-1") is False
    # The next dispatch ⇒ a new root graph task ⇒ a new turn key ⇒ a
    # fresh budget.
    manager._graph_tasks["inst-1"] = _FakeTask()
    assert _post(cap, "inst-1") is True
    assert manager._room_post_turn_counter["inst-1"]["count"] == 1


def test_the_same_turn_does_not_reset_the_budget(manager, cap):
    task = _FakeTask()
    manager._graph_tasks["inst-1"] = task
    for _ in range(ROOM_POSTS_PER_TURN):
        assert _post(cap, "inst-1") is True
    assert _post(cap, "inst-1") is False


def test_a_missing_turn_key_shares_the_budget(cap):
    """The fail-safe direction: with no task map, consecutive turns
    share one budget (fewer posts, never more)."""
    for _ in range(ROOM_POSTS_PER_TURN):
        assert _post(cap, "inst-1") is True
    assert _post(cap, "inst-1") is False


def test_the_refusal_detail_quotes_the_budget(cap):
    for _ in range(ROOM_POSTS_PER_TURN):
        cap.check("inst-1")
    allowed, detail = cap.check("inst-1")
    assert allowed is False
    assert detail["max_posts_per_turn"] == ROOM_POSTS_PER_TURN
    assert detail["posts_this_turn"] == ROOM_POSTS_PER_TURN


def test_an_explicit_reset_clears_the_budget(cap):
    for _ in range(ROOM_POSTS_PER_TURN):
        _post(cap, "inst-1")
    assert _post(cap, "inst-1") is False
    cap.reset("inst-1")
    assert _post(cap, "inst-1") is True
