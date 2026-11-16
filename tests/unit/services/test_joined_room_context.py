"""The JOINED-ROOM context block — content, caps, supersede, hoisting.

Agent group chat Phase 2 / P5 (2026-11-16).

Real file-backed SQLite (the F9-parity room harness) + the REAL
``RoomChatService`` over a manager stub, so the block is rendered from
the REAL room state (roster, messages, unread count).

Coverage map:

* the block carries the member's OWN NAME (Ruling B self-recognition);
* the block carries the ROSTER, including the owner;
* the digest carries the LAST-N messages + the unread count — NEVER
  the full history (the "system message = guidelines + short digest
  ONLY; full history behind the read tool" ruling);
* the caps: digest ≤ 2000 chars, the whole block ≤ 4000 chars, the
  roster ≤ 600 chars;
* the stable id is ``joined_room:{instance_id}`` — one block per
  instance, superseded in place (the digest never accumulates);
* the block is HOISTED through compaction (the truthy-keyed
  ``context_kind`` hoist — zero compaction plumbing);
* the guidelines TEXT is a STATIC CONSTANT in the builder module and
  appears in NO agents/*.md file (the prompt-artifact baseline must
  not move for this feature — RK-8);
* a non-member gets NO block.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session

import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.repositories.room import RoomChatService, RoomRepository
from daemon.services.context_messages import (
    CONTEXT_KIND_JOINED_ROOM,
    ROOM_BLOCK_MAX_CHARS,
    ROOM_DIGEST_LAST_N,
    ROOM_DIGEST_MAX_CHARS,
    ROOM_GUIDELINES_TEXT,
    ROOM_ROSTER_MAX_CHARS,
    build_joined_room_context_message,
)
from tests.helpers.room_harness import join_member, make_room, make_room_engine

REPO_ROOT = Path(__file__).resolve().parents[3]


def _seed_instance(eng: Engine, iid: str, parent: str | None = None) -> None:
    with Session(eng) as s:
        s.add(Instance(instance_id=iid, agent_id="ari", agent_dir="/agents/ari",
                       parent_id=parent, status="idle"))
        s.commit()


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    eng = make_room_engine(tmp_path, "joined_room.db")
    yield eng
    eng.dispose()


@pytest.fixture
def stack(engine):
    repo = RoomRepository(engine)
    svc = RoomChatService(engine)
    owner = "owner-1"
    _seed_instance(engine, owner, None)
    room_id = make_room(repo, tree_root=owner, owner=owner, owner_member_name="lead")
    _seed_instance(engine, "child-1", owner)
    join_member(engine, room_id, "child-1", "worker")
    manager = MagicMock()
    manager._room_chat_service = svc
    return engine, repo, svc, room_id, owner, manager


def _block(manager, instance_id: str):
    return asyncio.run(
        build_joined_room_context_message(instance_id=instance_id, manager=manager)
    )


# ── the content ────────────────────────────────────────────────────────


def test_the_block_carries_the_own_name_the_roster_and_the_digest(stack):
    engine, repo, svc, room_id, owner, manager = stack
    asyncio.run(
        svc.append_message(
            room_id=room_id,
            author_instance_id=owner,
            author_name="lead",
            text_body="scene 3 build done",
        )
    )
    msg = _block(manager, "child-1")
    assert msg is not None
    content = msg.content
    # The member's OWN NAME is present (self-recognition).
    assert "worker" in content, content
    # The ROSTER is present — including the OWNER.
    assert "lead (owner)" in content, content
    assert "ROSTER:" in content
    # The digest carries the recent message + the unread count.
    assert "scene 3 build done" in content, content
    assert "unread" in content, content


def test_the_own_name_comes_first(stack):
    engine, repo, svc, room_id, owner, manager = stack
    content = _block(manager, "child-1").content
    name_idx = content.index("your name in this room: worker")
    guidelines_idx = content.index("How this team works")
    assert name_idx < guidelines_idx, (
        "the own-name banner must come BEFORE the guidelines "
        "(architecture §5: own name FIRST)"
    )


def test_the_digest_is_a_short_slice_not_the_history(stack):
    engine, repo, svc, room_id, owner, manager = stack
    for i in range(10):
        asyncio.run(
            svc.append_message(
                room_id=room_id,
                author_instance_id=owner,
                author_name="lead",
                text_body=f"message-{i}",
            )
        )
    content = _block(manager, "child-1").content
    # The LAST N messages ride the digest; the earlier ones do NOT.
    assert f"message-{9}" in content
    assert f"message-{8}" in content
    assert "message-0" not in content, (
        "the digest must NOT carry the full history — the history "
        "lives behind room_read"
    )
    assert content.count("RECENT (last") == 1


def test_the_guidelines_text_is_a_static_constant_not_an_agents_prompt(stack):
    engine, repo, svc, room_id, owner, manager = stack
    content = _block(manager, "child-1").content
    assert ROOM_GUIDELINES_TEXT in content, (
        "the guidelines constant must be rendered VERBATIM"
    )
    # The constant lives in the builder module — NOT in any agents/*.md
    # file (the prompt-artifact baseline / seal must not move for this
    # feature — RK-8).
    module_source = (
        REPO_ROOT / "daemon" / "services" / "context_messages.py"
    ).read_text(encoding="utf-8")
    assert "ROOM_GUIDELINES_TEXT" in module_source
    for md in sorted((REPO_ROOT / "agents").glob("*/**/*.md")):
        text = md.read_text(encoding="utf-8")
        assert "How this team works: report MILESTONES" not in text, (
            f"{md}: the joined-room guidelines must NOT live in an "
            f"agents/*.md prompt artifact"
        )


# ── the caps ───────────────────────────────────────────────────────────


def test_the_caps_are_the_designed_ones():
    assert ROOM_DIGEST_MAX_CHARS == 2000
    assert ROOM_BLOCK_MAX_CHARS == 4000
    assert ROOM_ROSTER_MAX_CHARS == 600
    assert ROOM_DIGEST_LAST_N == 3


def test_the_block_never_exceeds_the_hard_cap(stack):
    engine, repo, svc, room_id, owner, manager = stack
    # A pathological body: the digest line cap + the block cap must
    # hold.
    asyncio.run(
        svc.append_message(
            room_id=room_id,
            author_instance_id=owner,
            author_name="lead",
            text_body="z" * 5000,
        )
    )
    content = _block(manager, "child-1").content
    assert len(content) <= ROOM_BLOCK_MAX_CHARS, len(content)


def test_a_long_roster_is_capped(stack):
    engine, repo, svc, room_id, owner, manager = stack
    for i in range(40):
        iid = f"member-{i}"
        _seed_instance(engine, iid, owner)
        join_member(engine, room_id, iid, f"worker-{i}")
    content = _block(manager, "child-1").content
    roster_line = next(
        line for line in content.splitlines() if line.startswith("ROSTER:")
    )
    assert len(roster_line) <= ROOM_ROSTER_MAX_CHARS, len(roster_line)


# ── the stable id + the hoist ──────────────────────────────────────────


def test_the_stable_id_supersedes_one_block_per_instance(stack):
    engine, repo, svc, room_id, owner, manager = stack
    first = _block(manager, "child-1")
    assert first is not None
    assert first.id == "joined_room:child-1", first.id
    asyncio.run(
        svc.append_message(
            room_id=room_id,
            author_instance_id=owner,
            author_name="lead",
            text_body="newer",
        )
    )
    second = _block(manager, "child-1")
    assert second.id == first.id, "the stable id must be stable"
    assert "newer" in second.content
    # Two members of the SAME room get SEPARATE blocks (each carries its
    # own name + unread view).
    owner_block = _block(manager, owner)
    assert owner_block.id == "joined_room:owner-1"
    assert owner_block.id != first.id


def test_the_block_is_hoisted_through_compaction(stack):
    engine, repo, svc, room_id, owner, manager = stack
    msg = _block(manager, "child-1")
    kwargs = msg.additional_kwargs or {}
    assert kwargs.get("injected_message") is True
    assert kwargs.get("context_kind") == CONTEXT_KIND_JOINED_ROOM
    # The compaction hoist is TRUTHY-KEYED on the context_kind (the
    # memoir precedent) — zero compaction plumbing is needed.
    from daemon._content_hardening import is_hoisted_injected

    assert is_hoisted_injected(msg, frozenset()) is True


def test_a_non_member_gets_no_block(stack):
    engine, repo, svc, room_id, owner, manager = stack
    assert _block(manager, "outsider-1") is None


def test_no_room_service_means_no_block():
    manager = MagicMock()
    manager._room_chat_service = None
    assert _block(manager, "child-1") is None
