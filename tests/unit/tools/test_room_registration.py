"""Registration + allow-list + gate proof for the ``room`` category
(agent group chat Phase 2 / P5, 2026-11-16).

The three-step registration seam (decorator + registry entries +
construction) is proven END-TO-END here, in the guidelines shape:

* (a) the AST discovery finds all four tools in the category module,
  and the constructed tools carry ``_tool_category == "room"``;
* (b) the decorator order is load-bearing and pinned:
  ``@register_tool_category`` ABOVE ``@tool``;
* (c) ``CATEGORY_MODULES["room"]`` resolves to a ``daemon.tools.*``
  path (the AST scanner resolves the value as
  ``daemon/tools/<parts[2:]>.py`` — a ``daemon.services.*`` value is
  never discovered and the drift pin fails at boot);
* (d) ``DYNAMIC_TOOL_NAMES`` + ``KNOWN_TOOL_NAMES`` carry all four
  names (factory-built, not import-time registered);
* (e) the ``create_instance_tools`` extend is greppable in source AT
  THE CALL SITE AND precedes ``create_help_tool`` /
  ``scan_tools_for_full_docs`` / ``_apply_tool_filter``
  (decorator-only registration is SILENTLY INVISIBLE — a late extend
  bypasses the privileged strip);
* (f) the category is PRIVILEGED entry #23 — membership + EXACT set +
  count 23 (the SAME-PR rule: the set and its pins never go red
  between two commits);
* (g) the ALLOW CENSUS: ``"room"`` appears in EXACTLY the granted
  agents' ``tools.allow`` arrays and in NO other agent's allow array
  (allow-array semantics — NOT a bare file grep: deny arrays are a
  live pattern);
* (h) the in-body runtime MEMBERSHIP gate is the FIRST logic in each
  tool body (a literal tool name in an allow list bypasses the
  category gate — and a refused caller must never write);
* (i) the loader warm-list entry exists so the cold-boot docs render
  the category (the W1-FIX-NOW class);
* (j) the per-agent construction seam: the tools are constructed for
  a granted agent and the membership gate refuses a NON-MEMBER
  (fail-closed, before any write — the RK-7-before-append shape).

Plus the trailing-order pins (the registry literal's LAST entry is
``room``; ari's allow array's LAST entry is ``room``) and the
mutation-target pin (the count pin lives HERE so the mutation matrix
has a target for the new category — "a category with no mutation
target is not covered by the matrix").
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
    discover_source_only_tool_names,
)
from daemon.tools.room import (
    ROOM_CATEGORY_KEY,
    ROOM_TOOLS,
    create_room_tools,
)

CATEGORY = "room"
MODULE = "daemon.tools.room"
TOOL_NAMES: frozenset[str] = frozenset(ROOM_TOOLS)

#: The agents the grant covers. The room audience is NOT a fixed
#: privileged panel — it is the team lanes: the top orchestrators
#: (ari / leader / sonic-leader), the generic warm worker lane, and
#: the video-team pilot (producer + capturer + editor + verifier —
#: the Emperor's designated FIRST pilot of group chat). The
#: MEMBERSHIP gate in the tool bodies is the real authority: a
#: granted agent that is not a member of the room still gets nothing.
GRANTED_AGENTS: tuple[str, ...] = (
    "ari",
    "leader",
    "sonic-leader",
    "worker",
    "video-producer",
    "video-capturer",
    "video-editor",
    "video-verifier",
)

EXPECTED_PRIVILEGED_SET: frozenset[str] = frozenset({
    "system_upgrade",
    "system-log",
    "ens-db",
    "ens-env",
    "view-views",
    "secrets",
    "settings",
    "board",
    "self-alarm",
    "view-write",
    "board-link",
    "wedge-capture",
    "instance_read",
    "instance_ops",
    "mcp_rpc",
    "notify-user",
    "metrics-read",
    "nest-report-read",
    "memoir",
    "prompt-reload",
    "prompt-assent",
    "guidelines",
    # room (2026-11-16, agent group chat P5): privileged entry #23.
    "room",
})


def _module_source() -> str:
    return (REPO_ROOT / "daemon" / "tools" / "room.py").read_text(encoding="utf-8")


def _instance_source() -> str:
    return (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(encoding="utf-8")


def _allow(agent_id: str) -> list:
    meta = json.loads(
        (REPO_ROOT / "agents" / agent_id / "meta.json").read_text(encoding="utf-8")
    )
    return (meta.get("tools") or {}).get("allow") or []


def _stub_manager():
    """A manager + room-service stub whose seams are ASYNC (the room
    service is an async facade — every method is a coroutine)."""
    manager = MagicMock()
    room_service = MagicMock()
    for name in (
        "get_member",
        "list_members",
        "append_message",
        "read_messages",
        "unread_count",
        "create_room",
        "advance_read_cursor",
        "list_member_rooms",
        "get_room",
    ):
        setattr(room_service, name, AsyncMock())
    manager._room_chat_service = room_service
    manager._room_delivery_service = MagicMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get_tree_root_id = MagicMock(return_value="tree-1")
    return manager, room_service


def _tools(agent_id: str = "ari", instance_id: str = "iid-1"):
    """Construct the category's tools with a MagicMock manager.

    The manager stub carries the room service seams the bodies touch
    AT CALL TIME — construction itself only builds closures.
    """
    manager = MagicMock()
    manager._room_chat_service = MagicMock()
    manager._room_delivery_service = MagicMock()
    tools = create_room_tools(
        manager, current_instance_id=instance_id, agent_id=agent_id
    )
    return {t.name: t for t in tools}


# ── A. the registration seam ───────────────────────────────────────────


class TestRegistration:
    def test_the_ast_discovery_finds_the_four_tools(self) -> None:
        """The category module's ``@tool``-decorated names are
        discoverable by the registry's AST scan."""
        discovered = discover_source_only_tool_names()
        for name in TOOL_NAMES:
            assert name in discovered, name

    def test_the_constructed_tools_carry_the_category(self) -> None:
        tools = _tools()
        assert set(tools) == set(TOOL_NAMES), sorted(tools)
        for name, tool_obj in tools.items():
            assert getattr(tool_obj, "_tool_category", None) == CATEGORY, name

    def test_the_decorator_order_is_register_above_tool(self) -> None:
        """``@register_tool_category("room")`` MUST sit ABOVE
        ``@tool`` — the reverse order silently loses the category."""
        source = _module_source()
        assert source.count('@register_tool_category(ROOM_CATEGORY_KEY)') == len(
            TOOL_NAMES
        )
        for match in re.finditer(
            r"@register_tool_category\(ROOM_CATEGORY_KEY\)", source
        ):
            tool_idx = source.find("@tool(", match.start())
            assert tool_idx != -1
            assert match.start() < tool_idx, (
                "the register decorator must sit ABOVE the @tool decorator"
            )

    def test_the_category_module_resolves_to_a_daemon_tools_path(self) -> None:
        assert CATEGORY_MODULES.get(CATEGORY) == MODULE
        parts = MODULE.split(".")
        assert parts[0] == "daemon" and parts[1] == "tools"
        assert (REPO_ROOT / "daemon" / "tools" / f"{parts[-1]}.py").is_file()

    def test_the_tool_names_are_known(self) -> None:
        for name in TOOL_NAMES:
            assert name in DYNAMIC_TOOL_NAMES, name
            assert name in KNOWN_TOOL_NAMES, name

    def test_the_construction_extend_precedes_the_filter_seams(self) -> None:
        """The extend MUST precede ``create_help_tool`` /
        ``scan_tools_for_full_docs`` / ``_apply_tool_filter`` — a later
        extend is either invisible to the help tool or BYPASSES the
        privileged-category strip."""
        source = _instance_source()
        extend_idx = source.find("create_room_tools_if_available(")
        # The DEF-anchored grep hits the factory definition first — the
        # CALL SITE is the second occurrence.
        call_idx = source.find(
            "create_room_tools_if_available(", extend_idx + 10
        )
        scan_idx = source.find("scan_tools_for_full_docs(tools)")
        filter_idx = source.find("_apply_tool_filter(tools, agent_id")
        help_idx = source.find("create_help_tool(tools, agent_id")
        assert call_idx != -1
        assert scan_idx != -1 and filter_idx != -1 and help_idx != -1
        assert call_idx < scan_idx, (
            "the room extend must precede scan_tools_for_full_docs"
        )
        assert call_idx < filter_idx, (
            "the room extend must precede _apply_tool_filter — a later "
            "extend BYPASSES the privileged-category strip"
        )
        assert call_idx < help_idx, (
            "the room extend must precede create_help_tool — a later "
            "extend is invisible to the help tool's category index"
        )

    def test_the_loader_warm_list_entry_exists(self) -> None:
        """Cold-boot docs render the category ONLY if the warm scan
        imports the module and scans its stub tools."""
        source = (REPO_ROOT / "daemon" / "loader.py").read_text(encoding="utf-8")
        assert "from .tools.room import create_room_tools" in source
        assert "room_tools = create_room_tools(None)" in source
        assert "guidelines_tools, room_tools," in source


# ── B. the privileged category ─────────────────────────────────────────


class TestPrivilegedCategory:
    def test_room_is_in_the_privileged_set(self) -> None:
        assert CATEGORY in PRIVILEGED_TOOL_CATEGORIES

    def test_the_set_is_exactly_the_twenty_three_entries(self) -> None:
        assert PRIVILEGED_TOOL_CATEGORIES == EXPECTED_PRIVILEGED_SET

    def test_the_count_is_twenty_three(self) -> None:
        """The count pin — the SAME-PR rule (the set and its pins ship
        in the SAME commit, so the set never goes red between two
        commits). This literal is ALSO the mutation matrix's find
        string for the room category (MT-R01)."""
        assert len(PRIVILEGED_TOOL_CATEGORIES) == 23

    def test_room_is_the_trailing_entry_of_the_registry_literal(self) -> None:
        """The registry's literal appends the new entry at the END (the
        house convention — the set is a frozen literal, not a set)."""
        source = (REPO_ROOT / "daemon" / "tools" / "_tool_registry.py").read_text(
            encoding="utf-8"
        )
        block = source.split(
            "PRIVILEGED_TOOL_CATEGORIES: frozenset[str] = frozenset({", 1
        )[1]
        literal = block.split("\n})", 1)[0]
        entries = [
            line.strip().strip(",").strip('"')
            for line in literal.splitlines()
            if line.strip().startswith('"')
        ]
        assert entries[-1] == CATEGORY, entries[-3:]


# ── C. the allow-lists ─────────────────────────────────────────────────


class TestAllowLists:
    def test_every_granted_agent_carries_the_category_key(self) -> None:
        for agent in GRANTED_AGENTS:
            allow = _allow(agent)
            assert CATEGORY in allow, agent
            # The allow array carries the CATEGORY KEY, never a literal
            # tool name — a literal tool-name entry would bypass the
            # category gate (the membership gate is the second layer,
            # not the first).
            for name in TOOL_NAMES:
                assert name not in allow, (agent, name)

    def test_no_other_agent_carries_the_category(self) -> None:
        offenders: list[str] = []
        for meta_path in sorted((REPO_ROOT / "agents").glob("*/meta.json")):
            if meta_path.parent.name in GRANTED_AGENTS:
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            allow = (meta.get("tools") or {}).get("allow") or []
            if CATEGORY in allow:
                offenders.append(str(meta_path.relative_to(REPO_ROOT)))
        assert offenders == [], (
            f"the room category is granted to EXACTLY {GRANTED_AGENTS}; "
            f"found in: {offenders}"
        )

    def test_ari_carries_the_category_as_the_trailing_entry(self) -> None:
        """The trailing-order pin — ari's allow array's LAST entry is
        the newest lane's category."""
        allow = _allow("ari")
        assert allow[-1] == CATEGORY, allow[-3:]


# ── D. the in-body membership gate ─────────────────────────────────────


class TestTheMembershipGate:
    def test_the_gate_is_the_first_logic_in_each_body(self) -> None:
        """The membership gate runs BEFORE any write — a refused caller
        must never touch the room log (RK-7-before-append)."""
        source = _module_source()
        tree = ast.parse(source)
        bodies = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name in TOOL_NAMES
        ]
        assert len(bodies) == len(TOOL_NAMES)
        for body in bodies:
            # The gate call is the FIRST executable statement of the
            # body (the gate is an ``if not _is_authorized(...):`` head).
            # The tool DOCSTRING is the body's first statement — it is
            # skipped (it is not logic).
            statements = list(body.body)
            if (
                statements
                and isinstance(statements[0], ast.Expr)
                and isinstance(statements[0].value, ast.Constant)
                and isinstance(statements[0].value.value, str)
            ):
                statements = statements[1:]
            first = statements[0]
            assert isinstance(first, ast.If), (
                f"{body.name}: the first statement must be the "
                f"authorization gate, got {type(first).__name__}"
            )
            test = first.test
            # The gate head is ``not _is_authorized(...)`` — the ``not``
            # wraps the call.
            if isinstance(test, ast.UnaryOp):
                test = test.operand
            assert isinstance(test, ast.Call), body.name
            assert (
                isinstance(test.func, ast.Name)
                and test.func.id == "_is_authorized"
            ), body.name

    def test_the_post_body_resolves_membership_before_the_append(self) -> None:
        """The APPEND is the commit point of the room log — the
        membership resolution MUST precede it in the source order."""
        source = _module_source()
        post_idx = source.index("async def room_post(")
        body = source[post_idx : source.index("async def room_read(")]
        gate_idx = body.index("room_service.get_member(room_id, caller_instance_id)")
        append_idx = body.index("room_service.append_message(")
        assert gate_idx < append_idx, (
            "the membership gate must resolve BEFORE the append "
            "(RK-7-before-append, fail-closed)"
        )

    async def test_a_non_member_is_refused_before_any_write(self) -> None:
        """The behavioral bite: a non-member's post is refused and the
        room log stays EMPTY."""
        manager, room_service = _stub_manager()
        room_service.get_member = AsyncMock(return_value=None)
        tools = {
            t.name: t
            for t in create_room_tools(
                manager, current_instance_id="outsider", agent_id="ari"
            )
        }
        raw = await tools["room_post"].ainvoke({"room_id": "r-1", "text": "hello"})
        assert raw.startswith("Error:"), raw
        assert "not-a-member" in raw, raw
        room_service.append_message.assert_not_called()

    async def test_a_left_member_is_refused(self) -> None:
        """A LEFT membership row is NOT an active membership."""
        manager, room_service = _stub_manager()
        room_service.get_member = AsyncMock(
            return_value={"member_name": "ghost", "state": "left"}
        )
        tools = {
            t.name: t
            for t in create_room_tools(
                manager, current_instance_id="iid-1", agent_id="ari"
            )
        }
        raw = await tools["room_post"].ainvoke({"room_id": "r-1", "text": "hello"})
        assert raw.startswith("Error:"), raw
        assert "not-a-member" in raw, raw
        room_service.append_message.assert_not_called()

    async def test_an_unresolved_agent_is_refused(self) -> None:
        """The second gate: a blank agent id never reaches the room."""
        manager = MagicMock()
        manager._room_chat_service = MagicMock()
        manager._room_delivery_service = MagicMock()
        tools = create_room_tools(
            manager, current_instance_id="iid-1", agent_id=""
        )
        for index, payload in enumerate(
            (
                {"name": "r"},
                {"room_id": "r", "text": "t"},
                {"room_id": "r"},
                {"room_id": "r", "proposal_seq": 1, "decision": "revive"},
            )
        ):
            raw = await tools[index].ainvoke(payload)
            assert raw.startswith("Error:"), (index, raw)
            assert "unknown-agent" in raw, (index, raw)


# ── E. the mutation target ────────────────────────────────────────────


class TestTheMutationTarget:
    def test_the_count_pin_lives_in_this_file(self) -> None:
        """The mutation matrix aims at the count pin of the NEW
        category's pin file ("a category with no mutation target is
        not covered by the matrix").

        The expected literal is BUILT from the LIVE count — it is NOT
        spelled out verbatim. Spelling it out would put the count
        literal into this file TWICE, and the matrix's find-string must
        be UNIQUE in its target file: the harness SKIPs a non-unique
        find-string and counts the skip as a leg FAILURE. That is
        exactly what happened — the room leg (MT-R01) was INERT (it
        skipped, and the skip was the one failing leg of the matrix
        run) while this meta-pin spelled the literal out a second time.
        """
        source = (REPO_ROOT / "tests" / "unit" / "tools" / "test_room_registration.py").read_text(
            encoding="utf-8"
        )
        expected = (
            f"assert len(PRIVILEGED_TOOL_CATEGORIES) == "
            f"{len(PRIVILEGED_TOOL_CATEGORIES)}"
        )
        assert expected in source
        # The uniqueness precondition the matrix enforces — pinned here
        # so the room leg can never go inert again unnoticed.
        assert source.count(expected) == 1, source.count(expected)

    def test_the_mutation_matrix_carries_the_room_leg(self) -> None:
        source = (REPO_ROOT / "test" / "packs" / "metrics_read_mutation_matrix_test.py").read_text(
            encoding="utf-8"
        )
        assert "MT-R01-room-count-pin" in source, (
            "the room category must carry a mutation leg in the matrix"
        )
        assert "tests/unit/tools/test_room_registration.py" in source
