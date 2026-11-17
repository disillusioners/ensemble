"""Agent group chat P6 — the F-3 SPAWN-PATH JOIN BROADCAST (the D item).

A-P6-26 disclosed the gap: the spawn path inserts the membership row
INSIDE the spawn transaction, so it never rode
``RoomChatService.join_room`` — its join event had NO producer, and the
SSE room channel never saw a spawn-time join. The post-spawn read-back
point (the D item's hook) is the producer.

What it pins:

* the SEAM works — the spawn-path broadcast fires the CONTENT-FREE
  ``room_joined`` event through the room chat service's lifecycle
  notifier (ids + role; no names, no content);
* the two producers SHARE ONE payload shape — the spawn-path event and
  the ``join_room`` event carry the SAME keys, so they cannot drift
  (the counter-mutation: a divergence REDS this pin);
* the event passes the SSE room channel's own fail-closed content-free
  guard (the hub's content-bearing key list);
* the SPAWN PATH calls the seam AFTER the commit (the event never
  announces a join that did not commit) — a structural pin over
  ``daemon/services/instance_lifecycle.py``;
* the seam is FAIL-SAFE — a notifier fault never breaks the spawn.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Engine

from daemon.repositories.room.service import RoomChatService
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.services.live_event_hub import LiveEventHub
from daemon.services.main_loop_bridge import MainLoopBridge
from tests.helpers.room_harness import (
    make_room,
    make_room_engine,
    seed_instance,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
LIFECYCLE_PY = REPO_ROOT / "daemon" / "services" / "instance_lifecycle.py"


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    eng = make_room_engine(tmp_path, "spawn_room_join_broadcast.db")
    yield eng
    eng.dispose()


@pytest.fixture
def bridge_loop():
    """Install the running loop on the bridge — and RESTORE it after.

    ``MainLoopBridge`` is a process-wide singleton: leaving a closed
    loop on it would change the behaviour of every later test that
    calls the bridge (the no-loop branch). The fixture puts the
    previous value back.
    """
    previous = MainLoopBridge.get_loop()
    try:
        yield MainLoopBridge
    finally:
        MainLoopBridge._loop = previous


def _recorder() -> tuple[list[dict[str, Any]], Any]:
    """A recording lifecycle notifier + the list it fills."""
    seen: list[dict[str, Any]] = []

    async def _notifier(*, room_id: str, kind: str, data: dict) -> None:
        seen.append({"room_id": room_id, "kind": kind, "data": dict(data)})

    return seen, _notifier


def _lifecycle(service: RoomChatService) -> InstanceLifecycleService:
    """A lifecycle instance carrying ONLY the manager seam the D wiring
    touches (the spawn path itself is exercised elsewhere)."""
    lifecycle = InstanceLifecycleService.__new__(InstanceLifecycleService)
    manager = MagicMock()
    manager._room_chat_service = service
    lifecycle._manager = manager
    return lifecycle


# ---------------------------------------------------------------------------
# the seam
# ---------------------------------------------------------------------------


async def test_the_spawn_join_broadcast_fires_the_content_free_event(
    engine,
    bridge_loop,
):
    """The producer: the spawn-path join announces itself."""
    service = RoomChatService(engine)
    seen, notifier = _recorder()
    service.set_lifecycle_notifier(notifier)
    lifecycle = _lifecycle(service)
    MainLoopBridge.set_loop(asyncio.get_running_loop())

    lifecycle._broadcast_spawn_room_join(
        room_id="room-1", instance_id="child-1", role="child"
    )
    # The bridge schedules on the running loop — yield until it ran.
    for _ in range(5):
        await asyncio.sleep(0)

    assert len(seen) == 1, seen
    event = seen[0]
    assert event["kind"] == "room_joined", event
    assert event["room_id"] == "room-1", event
    assert event["data"] == {
        "room_id": "room-1",
        "instance_id": "child-1",
        "role": "child",
    }, event
    # CONTENT-FREE: the hub's own guard would refuse a content-bearing
    # payload — this one passes.
    assert not LiveEventHub._ROOM_CHANNEL_CONTENT_KEYS.intersection(
        event["data"]
    ), event


async def test_the_two_producers_share_one_payload_shape(engine, bridge_loop):
    """``join_room`` and the spawn-path seam emit the SAME keys.

    The counter-mutation: a divergence between the two producers REDS
    this pin (the drift the shared-shape claim forbids).
    """
    service = RoomChatService(engine)
    seed_instance(engine, "owner-1", None, "idle")
    room_id = make_room(
        service._repo, tree_root="owner-1", owner="owner-1", name="shape-room"
    )
    seen, notifier = _recorder()
    service.set_lifecycle_notifier(notifier)

    await service.join_room(
        room_id=room_id, instance_id="child-1", base_name="worker"
    )
    await service.notify_spawn_join(
        room_id=room_id, instance_id="child-2", role="child"
    )
    assert len(seen) == 2, seen
    join_payload = seen[0]["data"]
    spawn_payload = seen[1]["data"]
    assert seen[0]["kind"] == seen[1]["kind"] == "room_joined", seen
    assert set(join_payload) == set(spawn_payload), (join_payload, spawn_payload)
    assert set(spawn_payload) == {"room_id", "instance_id", "role"}, spawn_payload


async def test_the_seam_is_fail_safe(engine, bridge_loop):
    """A notifier fault NEVER breaks the spawn (the spawn committed)."""
    service = RoomChatService(engine)

    async def _boom(*, room_id: str, kind: str, data: dict) -> None:
        raise RuntimeError("notifier boom")

    service.set_lifecycle_notifier(_boom)
    lifecycle = _lifecycle(service)
    MainLoopBridge.set_loop(asyncio.get_running_loop())
    # No exception escapes — the helper swallows and logs.
    lifecycle._broadcast_spawn_room_join(
        room_id="room-1", instance_id="child-1", role="child"
    )
    for _ in range(5):
        await asyncio.sleep(0)


async def test_a_daemon_without_the_room_service_is_a_no_op(bridge_loop):
    """No room substrate ⇒ no broadcast, no exception."""
    lifecycle = InstanceLifecycleService.__new__(InstanceLifecycleService)
    manager = MagicMock()
    manager._room_chat_service = None
    lifecycle._manager = manager
    MainLoopBridge.set_loop(asyncio.get_running_loop())
    lifecycle._broadcast_spawn_room_join(
        room_id="room-1", instance_id="child-1", role="child"
    )
    for _ in range(3):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# the structural pins — the spawn path calls the seam AFTER the commit
# ---------------------------------------------------------------------------


def _lifecycle_tree() -> ast.Module:
    return ast.parse(LIFECYCLE_PY.read_text(encoding="utf-8"))


def _method(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == name
        ):
            return node
    raise AssertionError(f"{name} not found in daemon/services/instance_lifecycle.py")


def _call_line(node: ast.AST, attr: str) -> int:
    """The line of the FIRST ``<something>.<attr>(...)`` call."""
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute) and func.attr == attr:
            return child.lineno
    raise AssertionError(f"no .{attr}(...) call found")


def test_the_spawn_path_calls_the_broadcast_seam_after_the_commit():
    """The event fires AFTER the spawn commit — never before.

    The commit point is the ``_spawn_instance_db_sync`` call (the
    instance row + the membership row commit together); the broadcast
    must not announce a join that did not commit.
    """
    tree = _lifecycle_tree()
    spawn = _method(tree, "spawn_instance")
    commit_line = _call_line(spawn, "_spawn_instance_db_sync")
    broadcast_line = _call_line(spawn, "_broadcast_spawn_room_join")
    assert commit_line < broadcast_line, (
        f"the join broadcast (line {broadcast_line}) must follow the "
        f"spawn commit (line {commit_line})"
    )


def test_the_broadcast_is_fire_and_forget_from_the_sync_spawn_path():
    """The spawn path is SYNC — the seam routes through the bridge.

    ``MainLoopBridge.run_async_no_wait`` is the house idiom for
    fire-and-forget async from a sync context (the same one the
    spawn path already uses for the SSE status/created events). The
    notifier coroutine is built as the bridge call's ARGUMENT — the
    precise structural fact (not a line-number comparison: the call
    spans two lines).
    """
    tree = _lifecycle_tree()
    helper = _method(tree, "_broadcast_spawn_room_join")
    bridge_calls = [
        node
        for node in ast.walk(helper)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run_async_no_wait"
    ]
    assert len(bridge_calls) == 1, bridge_calls
    arguments = bridge_calls[0].args
    assert len(arguments) == 1, arguments
    argument = arguments[0]
    assert isinstance(argument, ast.Call), ast.dump(argument)
    assert isinstance(argument.func, ast.Attribute), ast.dump(argument)
    assert argument.func.attr == "notify_spawn_join", ast.dump(argument)


def test_the_spawn_path_broadcast_only_when_a_room_join_happened():
    """The broadcast is INSIDE the ``room_join is not None`` branch.

    A spawn with no room join must never announce a join — the
    branch guard is the pin (the legacy zero-change spawn path).
    """
    lines = LIFECYCLE_PY.read_text(encoding="utf-8").splitlines()
    branch_line = next(
        index
        for index, text in enumerate(lines)
        if "if room_join is not None and spawn_result.member_name:" in text
    )
    broadcast_line = next(
        index
        for index, text in enumerate(lines)
        if "self._broadcast_spawn_room_join(" in text
    )
    assert branch_line < broadcast_line, (
        "the broadcast must sit inside the room_join branch"
    )
    # The call is INDENTED one level deeper than the branch guard — it
    # only runs when the branch is taken.
    branch_indent = len(lines[branch_line]) - len(lines[branch_line].lstrip())
    call_indent = len(lines[broadcast_line]) - len(lines[broadcast_line].lstrip())
    assert call_indent > branch_indent, (branch_indent, call_indent)
