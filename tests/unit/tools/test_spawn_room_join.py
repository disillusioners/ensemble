"""Spawn-tool integration matrix for the P2 room-join surface.

Harness: the test_spawn_instance_default_version.py spy pattern — a
REAL ``create_instance_tools`` closure (the actual tool body runs)
over a MagicMock manager whose room seams are REAL: the
``RoomChatService``/``RoomRepository`` stack runs on real file-backed
SQLite, and the ``manager.spawn_instance`` stub REPLICATES the
lifecycle's in-transaction join semantics
(``membership.join_room_in_session`` + the Instance row with its
JSONB keys — ONE commit), so the tool's read-back of the FINAL name
exercises the real registry mechanics.

Coverage (commission P2 scope items 1-3 + 6):

* the exact spawn param names (``join_room`` / ``member_name``) exist
  on the tool schema;
* join-at-spawn happy path: membership row + instance JSONB keys in
  one commit; the FINAL name in the toolcall result at EVERY tier
  (1 parent-provided / 2 quick-model / 3 agent-fallback / 4 suffix);
* the typed refusal matrix via the TOOL (5 distinct conjunct texts,
  NO instance row written, spawn never invoked);
* the commit-time UNIQUE(room_id, member_name) backstop
  (``MemberNameCollisionError``, dual-family RoomError+RoomJoinError)
  surfaces via the TOOL as the DISTINCT collision text — never the
  generic spawn-failure wrapper, never an admission-refusal text —
  with the whole spawn rolled back (no instance row);
* naming FAILURE never blocks spawn (instant tier-3 fallback);
* the legacy path (no join_room) is byte-shape unchanged (no room
  line, no room_join kwarg).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session

import daemon.repositories.event.models  # noqa: F401 — register event
import daemon.repositories.instance.models  # noqa: F401 — register instances
from daemon.repositories.instance.models import Instance
from daemon.repositories.room import RoomChatService, RoomRepository
from daemon.repositories.room import membership as room_membership
from daemon.services.room_naming import RoomNamingService
from tests.helpers.room_harness import (
    _make_spawn_sim,
    make_room,
    make_room_engine,
)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    eng = make_room_engine(tmp_path, "spawn_room_join.db")
    yield eng
    eng.dispose()


CALLER = "11111111-1111-1111-1111-111111111111"
TREE_ROOT = "22222222-2222-2222-2222-222222222222"
FOREIGN_ROOT = "33333333-3333-3333-3333-333333333333"


@pytest.fixture
def room_stack(engine: Engine) -> tuple[RoomRepository, RoomChatService, str]:
    repo = RoomRepository(engine)
    svc = RoomChatService(engine)
    room_id = make_room(repo, tree_root=TREE_ROOT, owner=CALLER)
    return repo, svc, room_id


def _build_manager(
    engine: Engine,
    svc: RoomChatService,
    spawn_sim,
    tree_root: str | None = TREE_ROOT,
) -> MagicMock:
    manager = MagicMock()
    manager.ensure_mcp_preloaded = AsyncMock(return_value=None)
    manager.config.llm.allowed_models = []
    manager.config.llm.model_quick = "quick-model"
    manager.config.llm.model = "fallback-model"
    manager._project_repository = MagicMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get.return_value = None
    manager._instance_repository.get_tree_root_id.return_value = tree_root
    manager._instance_repository.count_children.return_value = 0
    manager._room_chat_service = svc
    manager._room_naming_service = RoomNamingService(manager)
    manager.spawn_instance = MagicMock(side_effect=spawn_sim)
    manager._lifecycle_service._format_model_fallback_notice = MagicMock(
        return_value=""
    )
    return manager


def _build_tool_and_registry(manager: MagicMock):
    """Build the real tool closure + its fake registry OUTSIDE any
    patch (construction reads growth rules through the REAL registry;
    a concurrent test driving two factories under overlapping patches
    would leak the fake into construction and mock the file reads)."""
    from daemon.tools.instance import create_instance_tools

    tools = create_instance_tools(
        manager, current_instance_id=CALLER, agent_id="tester"
    )
    spawn_tool = next(
        t for t in tools if getattr(t, "name", "") == "spawn_instance"
    )
    fake_registry = MagicMock()
    fake_registry.get_version.return_value = MagicMock(
        snapshot_enabled=False
    )
    fake_registry.get_resolved.return_value = MagicMock(
        snapshot_enabled=False
    )
    return spawn_tool, fake_registry


async def _run_spawn_tool_async(manager: MagicMock, **params) -> str:
    """Drive the REAL spawn_instance tool body through its auth +
    registry seams (the sibling harness pattern). Awaitable form for
    tests already inside an event loop."""
    spawn_tool, fake_registry = _build_tool_and_registry(manager)
    with patch(
        "daemon.tools.instance._check_team_membership",
        return_value=None,
    ):
        with patch(
            "daemon.registry.get_registry", return_value=fake_registry
        ):
            result = await spawn_tool.coroutine(**params)
    if not isinstance(result, str):  # coroutine safety
        result = str(result)
    return result


def _run_spawn_tool(manager: MagicMock, **params) -> str:
    """Sync wrapper (asyncio.run) for the sync test bodies."""
    import asyncio as _asyncio

    return _asyncio.run(_run_spawn_tool_async(manager, **params))


# ---------------------------------------------------------------------------
# (a) the exact param surface
# ---------------------------------------------------------------------------


class TestParamSurface:
    def test_exact_param_names_on_schema(self):
        from daemon.tools.instance import SpawnInstanceInput

        fields = SpawnInstanceInput.model_fields
        assert "join_room" in fields
        assert "member_name" in fields

    def test_params_optional_by_default(self):
        from daemon.tools.instance import SpawnInstanceInput

        parsed = SpawnInstanceInput(agent_id="capture")
        assert parsed.join_room is None
        assert parsed.member_name is None


# ---------------------------------------------------------------------------
# (b) happy path — the naming cascade's final name at EVERY tier
# ---------------------------------------------------------------------------


class TestJoinHappyPath:
    def test_tier1_parent_provided_name_in_result(
        self, engine, room_stack
    ):
        repo, svc, room_id = room_stack
        calls: list[dict] = []
        manager = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        result = _run_spawn_tool(
            manager, agent_id="capture", join_room=room_id, member_name="Scout Lead"
        )
        assert "room join: room=" in result
        assert "member_name='scout-lead'" in result
        assert "tier 1 parent-provided" in result
        # One tx: BOTH the member row and the instance JSONB exist.
        spawned_id = calls[0]["instance_id"]
        member = repo.get_member(room_id, spawned_id)
        assert member is not None and member["member_name"] == "scout-lead"
        with Session(engine) as s:
            inst = s.get(Instance, spawned_id)
            assert inst.instance_metadata["join_room"] == room_id
            assert inst.instance_metadata["member_name"] == "scout-lead"

    def test_tier2_quick_model_name_in_result(self, engine, room_stack):
        repo, svc, room_id = room_stack
        calls: list[dict] = []
        manager = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        manager._room_naming_service.generate_member_name = AsyncMock(
            return_value="Sampler Pro"
        )
        result = _run_spawn_tool(
            manager, agent_id="capture", join_room=room_id, member_name=None
        )
        assert "member_name='sampler-pro'" in result
        assert "tier 2 quick-model" in result
        spawned_id = calls[0]["instance_id"]
        assert (
            repo.get_member(room_id, spawned_id)["member_name"]
            == "sampler-pro"
        )

    async def test_tier2_naming_wait_runs_outside_room_lock(
        self, engine, room_stack
    ):
        """Behavioral pin: the tier-2 quick-model wait (the ONE
        sanctioned spawn-blocking naming wait) happens BEFORE the
        per-room lock is taken — a slow model must NOT serialize
        unrelated same-room membership writes. Proved: while
        generate_member_name is in-flight, the room's lock is NOT
        held (not even acquired) by this spawn."""
        repo, svc, room_id = room_stack
        # Materialize the lock up-front so the assertion is concrete
        # (get_room_lock creates it lazily on first call).
        lock = await svc.get_room_lock(room_id)
        assert not lock.locked()

        calls: list[dict] = []
        manager = _build_manager(engine, svc, _make_spawn_sim(engine, calls))

        async def naming_side_effect(*, agent_id, instance_name):
            # While naming is in-flight, this spawn must NOT hold the
            # room lock (tier-2 wait runs outside the lock).
            assert not lock.locked(), (
                "tier-2 naming wait ran INSIDE the room lock — "
                "would serialize same-room writes on a slow model"
            )
            await asyncio.sleep(0.05)  # simulate the model latency
            return "sampler"

        manager._room_naming_service.generate_member_name = (
            AsyncMock(side_effect=naming_side_effect)
        )

        tool, reg = _build_tool_and_registry(manager)
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            with patch(
                "daemon.registry.get_registry", return_value=reg
            ):
                result = await tool.coroutine(
                    agent_id="capture", join_room=room_id
                )
        assert "member_name='sampler'" in result

    def test_tier3_agent_fallback_in_result_and_instant(
        self, engine, room_stack
    ):
        repo, svc, room_id = room_stack
        calls: list[dict] = []
        manager = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        manager._room_naming_service.generate_member_name = AsyncMock(
            return_value=None
        )
        started = time.monotonic()
        result = _run_spawn_tool(
            manager, agent_id="capture", join_room=room_id, member_name=None
        )
        elapsed = time.monotonic() - started
        assert "member_name='capture'" in result
        assert "tier 3 agent-fallback" in result
        assert elapsed < 2.0  # instant — naming failure never blocks
        spawned_id = calls[0]["instance_id"]
        assert repo.get_member(room_id, spawned_id)["member_name"] == "capture"

    def test_tier4_suffix_visible_in_result(self, engine, room_stack):
        """Collision in-room: a pre-registered 'scout' ⇒ the spawn
        lands 'scout-1' and the result SURFACES the suffix (no silent
        renaming)."""
        repo, svc, room_id = room_stack
        with Session(engine) as s:
            s.add(
                Instance(
                    instance_id="taken-1",
                    agent_id="capture",
                    agent_dir="/agents/capture",
                    parent_id=CALLER,
                    status="idle",
                )
            )
            s.commit()
        repo.join_room(
            room_id=room_id, instance_id="taken-1", base_name="scout"
        )
        calls: list[dict] = []
        manager = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        result = _run_spawn_tool(
            manager, agent_id="capture", join_room=room_id, member_name="scout"
        )
        assert "member_name='scout-1'" in result
        assert "tier 4 suffix" in result
        spawned_id = calls[0]["instance_id"]
        member = repo.get_member(room_id, spawned_id)
        assert member["member_name"] == "scout-1"
        with Session(engine) as s:
            inst = s.get(Instance, spawned_id)
            # The FINAL name rides the JSONB — the instance knows its
            # own name (Ruling B).
            assert inst.instance_metadata["member_name"] == "scout-1"

    async def test_concurrent_same_room_spawns_distinct_names(
        self, engine, room_stack
    ):
        """Two concurrent same-room spawns (same base name) under the
        room lock → distinct final names, both instances joined.

        Tools are constructed SEQUENTIALLY before the concurrent
        coroutine run so inner_soul's construction-time registry reads
        cannot see an in-flight patch (the established harness pattern
        from test_spawn_instance_default_version.py:340).
        """
        repo, svc, room_id = room_stack
        calls: list[dict] = []

        m1 = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        m1._room_naming_service.generate_member_name = AsyncMock(
            return_value=None
        )
        tool1, reg1 = _build_tool_and_registry(m1)

        m2 = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        m2._room_naming_service.generate_member_name = AsyncMock(
            return_value=None
        )
        tool2, reg2 = _build_tool_and_registry(m2)

        async def run_one(tool):
            return await tool.coroutine(
                agent_id="capture", join_room=room_id
            )

        # Now gather the actual tool COROUTINES concurrently — the
        # per-room lock serializes the join_room_in_session writes.
        # ONE patch set around the WHOLE gather: overlapping
        # enter/exit of the same patch target across concurrent
        # coroutines corrupts patch's save/restore stack (the final
        # exit would restore a MOCK into the global — leaking into
        # later constructions).
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            with patch(
                "daemon.registry.get_registry", return_value=reg1
            ):
                results = await asyncio.gather(
                    run_one(tool1), run_one(tool2)
                )
        assert all("room join: room=" in r for r in results)
        names = []
        for call in calls:
            member = repo.get_member(room_id, call["instance_id"])
            names.append(member["member_name"])
        assert len(set(names)) == 2  # capture + capture-1
        assert "capture" in names


# ---------------------------------------------------------------------------
# (c) the typed refusal matrix via the TOOL
# ---------------------------------------------------------------------------


class TestJoinRefusals:
    def _assert_refused(
        self,
        engine: Engine,
        manager: MagicMock,
        result: str,
        marker: str,
    ) -> None:
        assert result.startswith(
            "ERROR: spawn refused — room join rejected"
        ), result
        assert marker in result
        assert manager.spawn_instance.called is False
        with Session(engine) as s:
            rows = s.exec(
                text("SELECT COUNT(*) FROM instances WHERE agent_id = 'capture'")
            ).scalar()
            assert int(rows) == 0  # NO instance row written

    def test_unknown_room_refusal(self, engine, room_stack):
        _, svc, _ = room_stack
        calls: list[dict] = []
        manager = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        result = _run_spawn_tool(
            manager, agent_id="capture", join_room=str(uuid.uuid4())
        )
        self._assert_refused(engine, manager, result, "unknown room")

    def test_closed_room_refusal(self, engine, room_stack):
        repo, svc, room_id = room_stack
        with Session(engine) as s:
            from daemon.repositories.room.models import ChatRoom

            room = s.get(ChatRoom, room_id)
            room.state = "closed"
            s.add(room)
            s.commit()
        calls: list[dict] = []
        manager = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        result = _run_spawn_tool(
            manager, agent_id="capture", join_room=room_id
        )
        self._assert_refused(engine, manager, result, "not active")

    def test_cross_tree_refusal(self, engine, room_stack):
        _, svc, room_id = room_stack
        calls: list[dict] = []
        manager = _build_manager(
            engine, svc, _make_spawn_sim(engine, calls), tree_root=FOREIGN_ROOT
        )
        result = _run_spawn_tool(
            manager, agent_id="capture", join_room=room_id
        )
        self._assert_refused(engine, manager, result, "cross-tree")

    def test_unresolvable_tree_root_fails_closed_via_tool(
        self, engine, room_stack
    ):
        _, svc, room_id = room_stack
        calls: list[dict] = []
        manager = _build_manager(
            engine, svc, _make_spawn_sim(engine, calls), tree_root=None
        )
        result = _run_spawn_tool(
            manager, agent_id="capture", join_room=room_id
        )
        self._assert_refused(engine, manager, result, "cross-tree")

    def test_member_cap_refusal(self, engine, room_stack):
        repo, svc, room_id = room_stack
        # Fill to the 16-active cap (owner seeded + 15 direct joins).
        with Session(engine) as s:
            for i in range(15):
                s.add(
                    Instance(
                        instance_id=f"fill-{i}",
                        agent_id="filler",
                        agent_dir="/agents/capture",
                        parent_id=CALLER,
                        status="idle",
                    )
                )
            s.commit()
        for i in range(15):
            with Session(engine) as s:
                s.add(
                    room_membership.join_room_in_session(
                        s,
                        room_id=room_id,
                        instance_id=f"fill-{i}",
                        base_name=f"worker-{i}",
                    )
                )
                s.commit()
        calls: list[dict] = []
        manager = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        result = _run_spawn_tool(
            manager, agent_id="capture", join_room=room_id
        )
        self._assert_refused(engine, manager, result, "member cap")

    def test_room_cap_refusal(self, engine, room_stack):
        repo, svc, room_id = room_stack
        # The over-budget anomaly: 8 OTHER active rooms in the tree.
        for i in range(8):
            repo.create_room(
                name=f"crowd-{i}",
                tree_root_id=TREE_ROOT,
                owner_instance_id=CALLER,
                owner_member_name=f"owner-{i}",
            )
        calls: list[dict] = []
        manager = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        result = _run_spawn_tool(
            manager, agent_id="capture", join_room=room_id
        )
        self._assert_refused(engine, manager, result, "room budget")

    def test_refusal_texts_all_distinct(self, engine, room_stack):
        """The typed-error matrix end-to-end: five conjuncts → five
        DISTINCT tool refusal texts."""
        repo, svc, room_id = room_stack
        texts: list[str] = []

        calls: list[dict] = []
        m = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        texts.append(_run_spawn_tool(
            m, agent_id="capture", join_room=str(uuid.uuid4())
        ))

        with Session(engine) as s:
            from daemon.repositories.room.models import ChatRoom

            room = s.get(ChatRoom, room_id)
            room.state = "closed"
            s.add(room)
            s.commit()
        m = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        texts.append(_run_spawn_tool(
            m, agent_id="capture", join_room=room_id
        ))
        with Session(engine) as s:
            from daemon.repositories.room.models import ChatRoom

            room = s.get(ChatRoom, room_id)
            room.state = "active"
            s.add(room)
            s.commit()

        m = _build_manager(
            engine, svc, _make_spawn_sim(engine, calls), tree_root=FOREIGN_ROOT
        )
        texts.append(_run_spawn_tool(
            m, agent_id="capture", join_room=room_id
        ))

        # member cap — fill via the tool-sim path.
        with Session(engine) as s:
            for i in range(15):
                s.add(
                    Instance(
                        instance_id=f"fill-{i}",
                        agent_id="filler",
                        agent_dir="/agents/capture",
                        parent_id=CALLER,
                        status="idle",
                    )
                )
            s.commit()
        for i in range(15):
            with Session(engine) as s:
                s.add(
                    room_membership.join_room_in_session(
                        s,
                        room_id=room_id,
                        instance_id=f"fill-{i}",
                        base_name=f"worker-{i}",
                    )
                )
                s.commit()
        m = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        texts.append(_run_spawn_tool(
            m, agent_id="capture", join_room=room_id
        ))
        # Free 3 members so only the room-cap conjunct fires next.
        repo.leave_room(room_id=room_id, instance_id="fill-0")
        repo.leave_room(room_id=room_id, instance_id="fill-1")
        repo.leave_room(room_id=room_id, instance_id="fill-2")
        for i in range(8):
            repo.create_room(
                name=f"crowd-{i}",
                tree_root_id=TREE_ROOT,
                owner_instance_id=CALLER,
                owner_member_name=f"owner-{i}",
            )
        m = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        texts.append(_run_spawn_tool(
            m, agent_id="capture", join_room=room_id
        ))

        assert len(texts) == 5
        assert len(set(texts)) == 5


# ---------------------------------------------------------------------------
# (d) legacy path unchanged
# ---------------------------------------------------------------------------


class TestBlankJoinRoomRefusal:
    """Item-3 pin: an EMPTY/blank join_room is a MALFORMED join
    request — typed fail-closed refusal naming the problem, never a
    silent spawn-without-join (the truthy `if join_room:` conflated
    None and "")."""

    def _assert_blank_refused(self, engine, manager, result, value):
        assert result.startswith(
            "ERROR: spawn refused — room join rejected"
        ), result
        assert "malformed join_room" in result
        assert repr(value) in result
        assert manager.spawn_instance.called is False
        with Session(engine) as s:
            from sqlalchemy import text

            rows = s.exec(
                text("SELECT COUNT(*) FROM instances")
            ).scalar()
            assert int(rows) == 0  # NO instance row written

    def test_empty_string_join_room_refused(self, engine, room_stack):
        _, svc, _ = room_stack
        calls: list[dict] = []
        manager = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        result = _run_spawn_tool(
            manager, agent_id="capture", join_room=""
        )
        self._assert_blank_refused(engine, manager, result, "")

    def test_whitespace_join_room_refused(self, engine, room_stack):
        _, svc, _ = room_stack
        calls: list[dict] = []
        manager = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        result = _run_spawn_tool(
            manager, agent_id="capture", join_room="   "
        )
        self._assert_blank_refused(engine, manager, result, "   ")

    def test_none_join_room_still_inert(self, engine, room_stack):
        """The gate must NOT fire for the LEGACY None path — no
        join_room means no room intent, byte-shape unchanged."""
        _, svc, _ = room_stack
        calls: list[dict] = []
        manager = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        result = _run_spawn_tool(manager, agent_id="capture")
        assert "room join:" not in result
        assert manager.spawn_instance.called is True


class TestLegacyPath:
    def test_no_join_room_no_room_line_no_kwarg(self, engine, room_stack):
        _, svc, _ = room_stack
        calls: list[dict] = []
        manager = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        result = _run_spawn_tool(manager, agent_id="capture")
        assert "room join:" not in result
        assert manager.spawn_instance.called is True
        assert "room_join" not in manager.spawn_instance.call_args.kwargs
        assert "Successfully spawned instance:" in result

    def test_member_name_without_join_room_is_inert(self, engine, room_stack):
        """member_name without join_room: no room context ⇒ the param
        is ignored (no refusal, no room line, no room_join kwarg)."""
        _, svc, _ = room_stack
        calls: list[dict] = []
        manager = _build_manager(engine, svc, _make_spawn_sim(engine, calls))
        result = _run_spawn_tool(
            manager, agent_id="capture", member_name="lone-name"
        )
        assert "room join:" not in result
        assert manager.spawn_instance.called is True
        assert "room_join" not in manager.spawn_instance.call_args.kwargs


# ---------------------------------------------------------------------------
# (e) commit-time name collision — the typed backstop text
# ---------------------------------------------------------------------------


class TestCommitTimeNameCollision:
    """The UNIQUE(room_id, member_name) commit-time backstop.

    The tier-4 registry runs inside the tx, but a name that looks free
    at registry-read time can be TAKEN at insert time (the lock cannot
    see an out-of-band writer) — the DB backstop fires, the failed
    commit rolls the whole spawn back, and ``MemberNameCollisionError``
    (dual-family: RoomError + RoomJoinError) surfaces through the tool
    as the DISTINCT collision text.
    """

    def _seed_taken_name(self, engine: Engine, room_id: str) -> None:
        """Background row: 'solo' is already registered (prior
        member). Direct insert — deliberate: the collision source is
        out-of-band state the suffix registry cannot see."""
        with Session(engine) as s:
            s.add(
                room_membership.join_room_in_session(
                    s,
                    room_id=room_id,
                    instance_id="prior-member",
                    base_name="solo",
                )
            )
            s.commit()

    def test_typed_collision_text_distinct_at_tool(
        self, engine, room_stack, monkeypatch
    ):
        from daemon.repositories.room import (
            MemberNameCollisionError,
            RoomError,
        )
        from daemon.repositories.room.membership import RoomJoinError

        # Dual-family contract (the option-a design decision): the
        # collision stays a RoomError AND rides the room-join family
        # so the spawn tool's room-join handler catches it.
        assert issubclass(MemberNameCollisionError, RoomError)
        assert issubclass(MemberNameCollisionError, RoomJoinError)

        _, svc, room_id = room_stack
        self._seed_taken_name(engine, room_id)
        # Simulate the lock-invisible race: the registry read saw
        # 'solo' as free, so resolve returns the base UNCHANGED and
        # the INSERT collides with the seeded row at commit.
        monkeypatch.setattr(
            room_membership, "resolve_member_name", lambda taken, base: base
        )
        calls: list[dict] = []
        manager = _build_manager(
            engine, svc, _make_spawn_sim(engine, calls, commit_backstop=True)
        )
        result = _run_spawn_tool(
            manager, agent_id="capture", join_room=room_id,
            member_name="solo",
        )

        # TYPED: the distinct collision head, naming room + colliding
        # name (the classifier message carries member_name='solo' and
        # room=<room_id>).
        assert result.startswith(
            "ERROR: spawn failed — room member name already taken"
        ), result
        assert room_id in result
        assert "member_name='solo'" in result
        # NOT the generic spawn-failure wrapper.
        assert not result.startswith("ERROR: Failed to spawn instance")
        # NOT one of the 5 admission-refusal texts.
        assert "spawn refused — room join rejected" not in result
        assert "unknown room" not in result
        assert "member cap" not in result
        assert "room budget" not in result

        # One-tx contract: the failed commit rolled the WHOLE spawn
        # back — no instance row, and 'solo' stays registered exactly
        # once (owned by the prior member).
        assert manager.spawn_instance.called is True
        with Session(engine) as s:
            instances = s.exec(
                text("SELECT COUNT(*) FROM instances WHERE agent_id = 'capture'")
            ).scalar()
            assert int(instances) == 0
            solos = s.exec(
                text(
                    "SELECT COUNT(*) FROM chatroom_members "
                    "WHERE member_name = 'solo'"
                )
            ).scalar()
            assert int(solos) == 1
            prior = s.exec(
                text(
                    "SELECT instance_id FROM chatroom_members "
                    "WHERE member_name = 'solo'"
                )
            ).scalar()
            assert prior == "prior-member"

    def test_collision_text_differs_from_every_admission_refusal(
        self, engine, room_stack, monkeypatch
    ):
        """Distinguishability, mechanically: the tool-level collision
        text equals the dedicated formatter's output and differs from
        EVERY admission-refusal text (each real refusal type driven
        through the shared formatter)."""
        from daemon.repositories import room as room_pkg
        from daemon.repositories.room.membership import (
            RoomJoinCrossTreeError,
            RoomJoinMemberCapError,
            RoomJoinRoomCapError,
            RoomJoinRoomStateError,
            RoomJoinUnknownRoomError,
        )
        from daemon.tools.instance import (
            _format_member_name_collision_failure,
            _format_room_join_refusal,
        )

        _, svc, room_id = room_stack
        self._seed_taken_name(engine, room_id)
        monkeypatch.setattr(
            room_membership, "resolve_member_name", lambda taken, base: base
        )
        calls: list[dict] = []
        manager = _build_manager(
            engine, svc, _make_spawn_sim(engine, calls, commit_backstop=True)
        )
        result = _run_spawn_tool(
            manager, agent_id="capture", join_room=room_id,
            member_name="solo",
        )
        typed = room_pkg.MemberNameCollisionError(
            f"member_name='solo' already registered in room={room_id} "
            f"(UNIQUE(room_id, member_name) backstop)"
        )
        collision_text = _format_member_name_collision_failure(typed)
        # The tool returned EXACTLY the dedicated collision text.
        assert result == collision_text
        # …and it differs from every admission-refusal text.
        refusals = [
            _format_room_join_refusal(RoomJoinUnknownRoomError("probe")),
            _format_room_join_refusal(RoomJoinRoomStateError("probe")),
            _format_room_join_refusal(RoomJoinCrossTreeError("probe")),
            _format_room_join_refusal(RoomJoinMemberCapError("probe")),
            _format_room_join_refusal(RoomJoinRoomCapError("probe")),
        ]
        assert len(set(refusals)) == 5
        for refusal in refusals:
            assert refusal != collision_text
        assert collision_text not in " ".join(refusals)