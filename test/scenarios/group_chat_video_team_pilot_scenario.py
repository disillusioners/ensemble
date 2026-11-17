#!/usr/bin/env python3
"""The agent group chat VIDEO-TEAM PILOT — the E2E shakedown scenario.

Agent group chat Phase 2 / P6 (2026-11-16). The Emperor's designated
FIRST pilot of the room surface (KV ``gc_pilot_video_team``): *"VIDEO
TEAM = designated FIRST PILOT of group chat … producer / capturer /
editor / verifier coordinate through a chatroom as NAMED MEMBERS instead
of pure parent-routing … parallel scene builds, per-shot tasking THROUGH
THE PARENT (parent-decided tasking exercised), milestone reports
IN-ROOM (digest/nudge paths exercised), tagging across parallel
lanes."*

This scenario is the GATE for the real post-promote pilot: it drives
the REAL room substrate, the REAL tool surface, the REAL delivery
router, the REAL nudge coordinator and the REAL post-budget cap over a
synthetic file-backed SQLite stack — no mocks of the seams under test
(only the OUTER delivery sinks are recorded by spies).

WHAT IT PROVES (the numbered checks)

  C1  the room opens with FOUR NAMED members (the roster carries the
      purpose names; the own-name is what makes an @tag legible);
  C2  PARALLEL lane posts (two lanes post concurrently) — the room log
      stays monotonic and both posts land;
  C3  per-shot tasking THROUGH THE PARENT — the parent records the
      decision on the room (the Ruling-A loop-back);
  C4  the milestone reports ride the digest (the joined-room block:
      own name + the GUIDELINES text + roster + the LAST-N, and the
      history is NOT there);
  C5  a tag ACROSS lanes delivers (the delivery outcome + the DURABLE
      record of that delivery: the member's delivered cursor, Y14 —
      the content-free audit row carries ids + the outcome only);
  C6  the unread → nudge path fires ONCE per turn (the loop-guard) and
      the note names the REAL unread count (the VALUE is pinned, not
      just the word);
  C7  RULING A — a member that COMPLETED mid-run is never revived: the
      tag becomes a durable TASK PROPOSAL routed to the PARENT, and the
      parent's own status is untouched;
  C8  the §9 cost cap — four posts succeed in a turn, the FIFTH is
      typed-refused;
  C9  the §13-P6 exit criteria (the backend half): the room-read API
      answers the conversation with the members ∪ owner-ancestry
      predicate (fail-closed), a PURGED room renders as not-found, the
      SSE room-resolution pre-branch resolves the room to its owner,
      and ``room_purged`` is visible in the audit trail.

ENV SAFETY: the scenario is IN-PROCESS ONLY over a synthetic SQLite
stack — ambient ``POSTGRES_*`` pointing at LIVE ensemble_prod is
explicitly scrubbed at process start, and a guard aborts hard if any
``POSTGRES_*`` var is present.

Dual-layer timeout: ``signal.alarm(180)`` (inner guard) + the caller
wraps this script in ``timeout 300``.

Exit 0 = PASS, 1 = FAIL, 124 = TIMEOUT; the final line prints
``RESULT: PASS|FAIL|TIMEOUT``.
"""
from __future__ import annotations

# ── Standard lib ─────────────────────────────────────────────────────────
import asyncio
import json
import os
import re
import signal
import sys
import tempfile
import time
import traceback
from typing import Any
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# ── ENV-SAFETY GUARD (mandatory pre-import) ──────────────────────────────
_FORBIDDEN_ENV_VARS = (
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_USER",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_URL",
    "DATABASE_URL",
)
_leaked = [v for v in _FORBIDDEN_ENV_VARS if v in os.environ]
if _leaked:
    sys.stderr.write(
        f"\n[FATAL] {len(_leaked)} forbidden POSTGRES_* env var(s) present: "
        f"{_leaked!r}\nThe scenario is in-process only and must not "
        f"inherit any DB env. Aborting BEFORE any daemon import.\n"
    )
    sys.stderr.flush()
    sys.exit(1)

for _v in _FORBIDDEN_ENV_VARS:
    os.environ.pop(_v, None)

# ── Dual-layer watchdog (Layer 2: inner guard, ≤180 s) ───────────────────
WATCHDOG_SECONDS = 180


def _on_alarm(signum, frame):
    sys.stderr.write(
        f"\n[scenario] INTERNAL WATCHDOG FIRED after {WATCHDOG_SECONDS}s "
        f"(signal={signum}); exiting hard with TIMEOUT.\n"
    )
    sys.stderr.flush()
    sys.exit(124)


signal.signal(signal.SIGALRM, _on_alarm)
signal.alarm(WATCHDOG_SECONDS)

# ── Repo path resolution ─────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import daemon.repositories.event.models  # noqa: E402,F401
import daemon.repositories.instance.models  # noqa: E402,F401
from daemon.repositories.event.models import Event, EventKind  # noqa: E402
from daemon.repositories.instance.models import (  # noqa: E402
    Instance,
    InstanceStatus,
)
from daemon.repositories.room.models import ChatRoom, RoomMessageKind  # noqa: E402
from daemon.repositories.room.repository import RoomRepository  # noqa: E402
from daemon.repositories.room.service import RoomChatService  # noqa: E402
from daemon.services.joined_room_context import (  # noqa: E402
    ROOM_DIGEST_LAST_N,
    ROOM_GUIDELINES_TEXT,
    build_joined_room_context_message,
)
from daemon.services.room_delivery import RoomDeliveryService  # noqa: E402
from daemon.services.room_nudge import RoomNudgeCoordinator  # noqa: E402
from daemon.services.room_post_cap import RoomPostTurnCap  # noqa: E402
from daemon.routers.messages import _resolve_sse_target  # noqa: E402
from daemon.tools.room import create_room_tools  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from tests.helpers.room_harness import (  # noqa: E402
    join_member,
    make_room_engine,
    resolve_tree_root_id,
    seed_instance,
)

# ── Constants ────────────────────────────────────────────────────────────
PRODUCER = "pilot-producer-0001"
CAPTURER = "pilot-capturer-0002"
EDITOR = "pilot-editor-0003"
VERIFIER = "pilot-verifier-0004"

NAMES = {
    PRODUCER: "producer",
    CAPTURER: "capturer",
    EDITOR: "editor",
    VERIFIER: "verifier",
}

SCRIPT_START = time.monotonic()
SCENARIO_RESULTS: dict[str, str] = {}


def elapsed() -> float:
    return time.monotonic() - SCRIPT_START


def stamp(msg: str) -> None:
    sys.stdout.write(f"[scenario +{elapsed():6.2f}s] {msg}\n")
    sys.stdout.flush()


def record(check: str, ok: bool, detail: dict[str, Any]) -> None:
    SCENARIO_RESULTS[check] = "PASS" if ok else "FAIL"
    stamp(f"{check}: {'PASS' if ok else 'FAIL'} — {detail}")


class _InstanceRepo:
    """The tree-root resolver (the room scope anchor)."""

    def __init__(self, engine):
        self._engine = engine

    def get_tree_root_id(self, instance_id: str) -> str | None:
        return resolve_tree_root_id(self._engine, instance_id)


def _build_manager(engine: Any, svc: Any):
    """The manager stub — the seams the room surfaces touch.

    The DELIVERY SINKS are spies (the parent injection / enqueue), so
    the scenario can assert what the router did (and did NOT do).
    """
    manager = MagicMock()
    manager._room_chat_service = svc
    manager._room_delivery_service = RoomDeliveryService(manager, svc)
    manager._instance_repository = _InstanceRepo(engine)
    manager._room_post_turn_counter = {}
    manager._room_post_turn_cap = RoomPostTurnCap(manager=manager)
    manager._room_nudge_latch = {}
    manager._room_nudge_coordinator = RoomNudgeCoordinator(manager)
    manager._graph_tasks = {}
    manager.has_live_graph_task = MagicMock(return_value=False)
    manager.set_injection = MagicMock()
    manager.enqueue_message = AsyncMock(return_value=MagicMock(queued=True))
    manager.get_queue_stats = AsyncMock(
        return_value={"pending_count": 0, "processing_count": 0}
    )

    async def _get_instance(instance_id: str):
        """The instance lookup — raises KeyError for a non-instance id
        (the SSE room-resolution pre-branch's instance branch)."""
        with Session(engine) as s:
            row = s.get(Instance, instance_id)
        if row is None:
            raise KeyError(instance_id)
        return {"instance_id": instance_id}

    manager.get_instance = _get_instance
    return manager


def _tools(manager: Any, instance_id: str):
    return {
        t.name: t
        for t in create_room_tools(
            manager,
            current_instance_id=instance_id,
            agent_id=NAMES.get(instance_id, "outsider"),
        )
    }


async def _call(tool, payload):
    return await tool.ainvoke(payload)


# ── the scenario ─────────────────────────────────────────────────────────


async def run_scenario() -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="gc-pilot-scenario-"))
    engine = make_room_engine(tmpdir, "pilot.db")
    repo = RoomRepository(engine)
    svc = RoomChatService(engine)

    for iid in (PRODUCER, CAPTURER, EDITOR, VERIFIER):
        seed_instance(engine, iid, None, "idle")
    manager = _build_manager(engine, svc)

    # ── C1: the room opens with FOUR NAMED members ──────────────────────
    producer_tools = _tools(manager, PRODUCER)
    raw = await _call(
        producer_tools["room_create"],
        {"name": "scene-3 build", "member_name": NAMES[PRODUCER]},
    )
    payload = json.loads(raw)
    room_id = payload["room_id"]
    for iid in (CAPTURER, EDITOR, VERIFIER):
        join_member(engine, room_id, iid, NAMES[iid])
    roster = svc._repo.list_members(
        room_id, caller_instance_id=PRODUCER, active_only=True
    )
    roster_names = {m["member_name"] for m in roster}
    record(
        "C1-named-members",
        roster_names == {"producer", "capturer", "editor", "verifier"},
        {"roster": sorted(roster_names), "room_id": room_id},
    )

    # The parent's tree root must resolve for the room scope.
    assert manager._instance_repository.get_tree_root_id(PRODUCER)

    capturer_tools = _tools(manager, CAPTURER)
    editor_tools = _tools(manager, EDITOR)
    verifier_tools = _tools(manager, VERIFIER)

    # ── the PRE-ROLL: two older lines ──────────────────────────────────
    # The digest carries the LAST-N rows (never the history). The C4
    # proof needs a message that falls OUTSIDE that window, so the room
    # log starts with two lines the window will later drop.
    for pre_roll_line in ("history line alpha", "history line beta"):
        await _call(
            verifier_tools["room_post"],
            {"room_id": room_id, "text": pre_roll_line},
        )

    # ── C2: PARALLEL lane posts ─────────────────────────────────────────
    # Two lanes post concurrently (the per-room lock serialises the seq
    # allocation — the log stays monotonic and BOTH posts land).
    results = await asyncio.gather(
        _call(
            capturer_tools["room_post"],
            {"room_id": room_id, "text": "shot 3 plates captured"},
        ),
        _call(
            editor_tools["room_post"],
            {"room_id": room_id, "text": "shot 3 rough cut online"},
        ),
        return_exceptions=True,
    )
    seqs = []
    for item in results:
        if isinstance(item, Exception):
            continue
        body = json.loads(item)
        seqs.append(body.get("seq"))
    failures = [repr(r) for r in results if isinstance(r, Exception)]
    log_rows = repo.list_messages(
        room_id, caller_instance_id=PRODUCER, after_seq=0, limit=50
    )
    seq_list = [r["seq"] for r in log_rows]
    record(
        "C2-parallel-lane-posts",
        len(results) == 2
        and not any(isinstance(r, Exception) for r in results)
        and seq_list == sorted(seq_list)
        and len(set(seq_list)) == len(seq_list)
        and len(seq_list) >= 2,
        {"seqs": seq_list, "returned": [s for s in seqs], "failures": failures},
    )

    # ── C4: the milestone reports ride the DIGEST ───────────────────────
    # The joined-room block for the capturer: own name + guidelines +
    # roster + the LAST-N messages (never the history).
    block = await build_joined_room_context_message(
        instance_id=CAPTURER, manager=manager
    )
    content = block.content if block is not None else ""
    digest_has_capture = "shot 3 plates captured" in content
    # The guidelines are the STATIC constant the block carries (the
    # "system message = guidelines + short digest" ruling) — the WHOLE
    # text, not a loose fragment.
    has_guidelines = ROOM_GUIDELINES_TEXT in content
    # "never the history": the OLDEST line (the pre-roll, outside the
    # last-N window) must NOT be in the block, and the block carries at
    # most the window's worth of the room's lines.
    room_lines = (
        "history line alpha",
        "history line beta",
        "shot 3 plates captured",
        "shot 3 rough cut online",
    )
    carried = [line for line in room_lines if line in content]
    history_absent = "history line alpha" not in content
    record(
        "C4-digest-carries-the-milestones",
        block is not None
        and "your name in this room: capturer" in content
        and "ROSTER:" in content
        and has_guidelines
        and history_absent
        and len(carried) <= ROOM_DIGEST_LAST_N
        and digest_has_capture,
        {
            "has_own_name": "your name in this room: capturer" in content,
            "has_roster": "ROSTER:" in content,
            "has_guidelines": has_guidelines,
            "has_milestone": digest_has_capture,
            "history_absent": history_absent,
            "carried_lines": carried,
            "carried_count": len(carried),
            "digest_window": ROOM_DIGEST_LAST_N,
        },
    )

    # ── C5: a tag ACROSS lanes delivers ────────────────────────────────
    # The capturer is IDLE-but-live ⇒ the lane-(b) SUCCESS path (the
    # delivery rides the queue). The DURABLE record of that delivery is
    # the member's DELIVERED CURSOR — Y14: the cursor advances at
    # ADMISSION — NOT an event. The content-free audit row the lane
    # emits carries ids + the outcome ONLY (never the message text).
    raw = await _call(
        producer_tools["room_post"],
        {
            "room_id": room_id,
            "text": "capturer: the plates need a colour pass",
            "tagged": ["capturer"],
        },
    )
    tag_payload = json.loads(raw)
    tag_outcomes = tag_payload.get("tag_outcomes") or []
    tagged_seq = int(tag_payload.get("seq") or 0)
    capturer_member = next(
        (
            m
            for m in svc._repo.list_members(
                room_id, caller_instance_id=PRODUCER, active_only=True
            )
            if m["instance_id"] == CAPTURER
        ),
        None,
    )
    delivered_cursor = (
        int(capturer_member["last_delivered_seq"])
        if capturer_member is not None
        else -1
    )
    with Session(engine) as s:
        audit_rows = list(s.exec(select(Event)).all())
    tag_events = [
        row
        for row in audit_rows
        if row.kind == EventKind.ROOM_TAG_DELIVERY.value
    ]
    audit_content_free = all(
        "the plates need a colour pass" not in (row.data or "")
        for row in tag_events
    )
    record(
        "C5-tag-across-lanes",
        tag_payload.get("routing_error") is False
        and len(tag_outcomes) == 1
        and tag_outcomes[0]["member_name"] == "capturer"
        and tag_outcomes[0]["outcome"] == "enqueued"
        and delivered_cursor == tagged_seq
        and audit_content_free,
        {
            "tag_outcomes": tag_outcomes,
            "routing_error": tag_payload.get("routing_error"),
            "tagged_seq": tagged_seq,
            "delivered_cursor": delivered_cursor,
            "audit_rows": len(tag_events),
            "audit_content_free": audit_content_free,
        },
    )

    # ── C6: the unread → nudge path (and its loop-guard) ───────────────
    # The capturer has unread messages (the producer's tag landed).
    manager._graph_tasks[CAPTURER] = object()  # a fresh TURN
    note = await manager._room_nudge_coordinator.evaluate_todo_boundary(
        CAPTURER
    )
    note_again = await manager._room_nudge_coordinator.evaluate_todo_boundary(
        CAPTURER
    )
    # The note NAMES the unread count — and the VALUE must be the
    # room's REAL unread count (a substring check alone would let a
    # "0 unread" note pass).
    real_unread = int(
        await svc.unread_count(room_id, caller_instance_id=CAPTURER) or 0
    )
    count_match = re.search(r"Nudge:\s*(\d+)\s+unread", note)
    named_unread = int(count_match.group(1)) if count_match else -1
    record(
        "C6-unread-nudge-loop-guard",
        count_match is not None
        and named_unread == real_unread
        and real_unread > 0
        and note_again == "",
        {
            "note": note[:120],
            "second_note": note_again,
            "named_unread": named_unread,
            "real_unread": real_unread,
        },
    )

    # ── C7: RULING A — a COMPLETED member is never revived ─────────────
    # The verifier finishes its turn mid-run; a tag to it must become a
    # durable TASK PROPOSAL routed to the PARENT (never a silent
    # revive, never a message to the completed member).
    with Session(engine) as s:
        row = s.get(Instance, VERIFIER)
        row.status = InstanceStatus.COMPLETED.value
        s.commit()
    manager.set_injection.reset_mock()
    manager.enqueue_message.reset_mock()
    raw = await _call(
        producer_tools["room_post"],
        {
            "room_id": room_id,
            "text": "verifier: please confirm the grade",
            "tagged": ["verifier"],
        },
    )
    proposal_payload = json.loads(raw)
    proposals = [
        m
        for m in repo.list_messages(
            room_id, caller_instance_id=PRODUCER, after_seq=0, limit=100
        )
        if m.get("kind") == RoomMessageKind.TASK_PROPOSAL.value
    ]
    with Session(engine) as s:
        verifier_row = s.get(Instance, VERIFIER)
        producer_row = s.get(Instance, PRODUCER)
    # The parent (producer) is IDLE — the notice rides the lane-(b)
    # enqueue; the completed member is NEVER revived (its status is
    # untouched and it received no message).
    verifier_messages = [
        m
        for m in manager.enqueue_message.await_args_list
        if m.args and m.args[0] == VERIFIER
    ]
    record(
        "C7-ruling-a-no-silent-revive",
        len(proposals) >= 1
        and verifier_row.status == InstanceStatus.COMPLETED.value
        and producer_row.status == "idle"
        and not verifier_messages
        and not any(
            call.args and call.args[0] == VERIFIER
            for call in manager.set_injection.call_args_list
        ),
        {
            "proposals": len(proposals),
            "verifier_status": verifier_row.status,
            "producer_status": producer_row.status,
            "verifier_enqueues": len(verifier_messages),
            "parent_enqueues": len(manager.enqueue_message.await_args_list),
            "routing_error": proposal_payload.get("routing_error"),
        },
    )

    # ── C3: per-shot tasking THROUGH THE PARENT ────────────────────────
    # The parent (the room owner) records the decision ON the room.
    proposal_seq = proposals[0]["seq"]
    raw = await _call(
        producer_tools["room_task_decision"],
        {
            "room_id": room_id,
            "proposal_seq": proposal_seq,
            "decision": "reroute",
            "note": "the editor picks it up next",
        },
    )
    decision_payload = json.loads(raw)
    decisions = [
        m
        for m in repo.list_messages(
            room_id, caller_instance_id=PRODUCER, after_seq=0, limit=100
        )
        if m.get("kind") == RoomMessageKind.TASK_DECISION.value
    ]
    record(
        "C3-parent-decides-on-the-room",
        decision_payload.get("recorded") is True and len(decisions) == 1,
        {"decisions": len(decisions), "payload": decision_payload},
    )

    # ── C8: the §9 cost cap (four posts, the fifth refused) ───────────
    manager._graph_tasks[EDITOR] = object()  # a fresh TURN for the editor
    accepted = 0
    refusal = ""
    for i in range(5):
        raw = await _call(
            editor_tools["room_post"],
            {"room_id": room_id, "text": f"editor beat {i}"},
        )
        if raw.startswith("Error:"):
            refusal = raw
            break
        accepted += 1
    record(
        "C8-per-turn-post-cap",
        accepted == 4 and "post-cap" in refusal,
        {"accepted": accepted, "refusal": refusal[:120]},
    )

    # ── C9: the §13-P6 exit criteria (the backend half) ───────────────
    # (a) the room-read API answers the conversation (the members ∪
    #     owner-ancestry predicate, fail-closed);
    raw = await _call(
        capturer_tools["room_read"], {"room_id": room_id, "limit": 100}
    )
    read_payload = json.loads(raw)
    conversation = read_payload.get("messages") or []
    # (b) a NON-MEMBER is refused (the fail-closed predicate).
    seed_instance(engine, "pilot-outsider-0099", None, "idle")
    outsider_tools = _tools(manager, "pilot-outsider-0099")
    outsider_raw = await _call(
        outsider_tools["room_read"], {"room_id": room_id}
    )
    outsider_refused = outsider_raw.startswith("Error:") and (
        "room-not-found" in outsider_raw
    )
    # (c) the SSE room-resolution pre-branch resolves the room to its
    #     owner (the §8 pre-branch);
    resolved_instance, resolved_room = await _resolve_sse_target(
        manager, room_id
    )
    # (d) ``room_purged`` is visible in the audit trail — the purge
    #     emits the content-free event row and deletes the room.
    purge_result = svc._repo.purge_room(room_id, reason="pilot-scenario")
    with Session(engine) as s:
        audit_rows = list(s.exec(select(Event)).all())
        purged = s.get(ChatRoom, room_id)
    purge_events = [
        row
        for row in audit_rows
        if row.kind == EventKind.ROOM_PURGED.value
    ]
    # (e) a purged room renders as NOT-FOUND everywhere.
    purged_read = await _call(capturer_tools["room_read"], {"room_id": room_id})
    purged_refused = purged_read.startswith("Error:") and (
        "room-not-found" in purged_read
    )
    try:
        await _resolve_sse_target(manager, room_id)
        purged_sse = False
    except Exception as exc:  # noqa: BLE001 — the typed 404 is the point
        purged_sse = getattr(exc, "status_code", None) == 404
    record(
        "C9-p6-exit-criteria",
        len(conversation) >= 5
        and outsider_refused
        and resolved_instance == PRODUCER
        and resolved_room == room_id
        and len(purge_events) == 1
        and purged is None
        and purged_refused
        and purged_sse,
        {
            "conversation_rows": len(conversation),
            "outsider_refused": outsider_refused,
            "sse_resolved": resolved_instance,
            "room_purged_events": len(purge_events),
            "purged_row": purged is not None,
            "purged_read_refused": purged_refused,
            "purged_sse_404": purged_sse,
            "purge_result": purge_result,
        },
    )

    engine.dispose()
    return 0


def main() -> int:
    try:
        asyncio.run(run_scenario())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 — the scenario reports, never raises out
        traceback.print_exc()
        print("RESULT: FAIL (the scenario raised)")
        return 1
    failed = [k for k, v in SCENARIO_RESULTS.items() if v != "PASS"]
    for name in sorted(SCENARIO_RESULTS):
        stamp(f"{name}: {SCENARIO_RESULTS[name]}")
    if len(SCENARIO_RESULTS) < 9:
        print(f"RESULT: FAIL (only {len(SCENARIO_RESULTS)} checks ran)")
        return 1
    if failed:
        print(f"RESULT: FAIL ({len(failed)} check(s) failed: {failed})")
        return 1
    print(f"RESULT: PASS ({len(SCENARIO_RESULTS)} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
