"""Registration + allow-list + gate proof for the ``room`` category
(agent group chat Phase 2 / P5, 2026-11-16).

The three-step registration seam (decorator + registry entries +
construction) is proven END-TO-END here, in the guidelines shape:

* (a) the AST discovery finds all five tools in the category module,
  and the constructed tools carry ``_tool_category == "room"``;
* (b) the decorator order is load-bearing and pinned:
  ``@register_tool_category`` ABOVE ``@tool``;
* (c) ``CATEGORY_MODULES["room"]`` resolves to a ``daemon.tools.*``
  path (the AST scanner resolves the value as
  ``daemon/tools/<parts[2:]>.py`` — a ``daemon.services.*`` value is
  never discovered and the drift pin fails at boot);
* (d) ``DYNAMIC_TOOL_NAMES`` + ``KNOWN_TOOL_NAMES`` carry all five
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
import asyncio
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

REPO_ROOT = Path(__file__).resolve().parents[3]

from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
    discover_source_only_tool_names,
)
from daemon.tools.room import (
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
        "close_room",
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
    def test_the_ast_discovery_finds_the_five_tools(self) -> None:
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
        """The layer-2 AUTHORIZATION gate is the FIRST logic in each
        tool body — a literal tool name in an allow list bypasses the
        category gate, so each body re-checks the caller. (The
        MEMBERSHIP-before-write property is pinned separately, per verb,
        below — see
        ``test_the_post_body_resolves_membership_before_the_append``.)

        RE-AIMED (P6 H1, 2026-11-16): the gate now lives in ONE shared
        helper (``_gate``) instead of four copies, so the FIRST logic
        statement of each body DELEGATES to that helper. The pin keeps
        its intent — the gate is still the first thing every body does —
        and it keeps its teeth: an inlined check (a body that does its
        own authorization) REDS here, and a helper that stops checking
        REDS ``test_the_shared_gate_helper_checks_the_caller_first``.
        """
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
            # The gate delegation is the FIRST executable statement of
            # the body. The tool DOCSTRING is the body's first
            # statement — it is skipped (it is not logic).
            statements = list(body.body)
            if (
                statements
                and isinstance(statements[0], ast.Expr)
                and isinstance(statements[0].value, ast.Constant)
                and isinstance(statements[0].value.value, str)
            ):
                statements = statements[1:]
            first = statements[0]
            call = None
            if isinstance(first, (ast.Assign, ast.Expr)):
                call = first.value
            assert isinstance(call, ast.Call), (
                f"{body.name}: the first statement must delegate to the "
                f"shared gate helper, got {type(first).__name__}"
            )
            assert (
                isinstance(call.func, ast.Name) and call.func.id == "_gate"
            ), (
                f"{body.name}: the first statement must call the shared "
                f"_gate helper, got {ast.dump(call.func)}"
            )
            # The helper is told WHICH verb it guards (the refusal
            # envelope names the tool).
            assert call.args, body.name
            label = call.args[0]
            assert (
                isinstance(label, ast.Constant) and label.value == body.name
            ), f"{body.name}: the gate must name its own verb"

    def test_the_shared_gate_helper_checks_the_caller_first(self) -> None:
        """The helper's FIRST logic is the ``_is_authorized`` check — the
        gate is still first, one layer down. A helper that stopped
        checking (or checked later) REDS here."""
        tree = ast.parse(_module_source())
        helpers = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_gate"
        ]
        assert len(helpers) == 1, "exactly ONE shared _gate helper"
        statements = list(helpers[0].body)
        if (
            statements
            and isinstance(statements[0], ast.Expr)
            and isinstance(statements[0].value, ast.Constant)
            and isinstance(statements[0].value.value, str)
        ):
            statements = statements[1:]
        first = statements[0]
        assert isinstance(first, ast.If), (
            "the shared gate helper's first logic must be the "
            f"authorization check, got {type(first).__name__}"
        )
        test = first.test
        # The gate head is ``not _is_authorized(...)`` — the ``not``
        # wraps the call.
        if isinstance(test, ast.UnaryOp):
            test = test.operand
        assert isinstance(test, ast.Call)
        assert (
            isinstance(test.func, ast.Name)
            and test.func.id == "_is_authorized"
        )

    def test_exactly_one_gate_helper_serves_the_four_verbs(self) -> None:
        """The census: the shared helper is called EXACTLY once per
        verb — a body that keeps its own copy (or drops the gate) is
        caught here."""
        tree = ast.parse(_module_source())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_gate"
        ]
        assert len(calls) == len(TOOL_NAMES), (
            f"expected one _gate call per verb ({len(TOOL_NAMES)}), got "
            f"{len(calls)}"
        )

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

    async def test_a_refused_caller_touches_no_room_service(self) -> None:
        """The gate is FIRST — a refused caller reaches NO service seam
        (no DB touch at all). The behavioral bite of the H1 extraction:
        the shared helper runs BEFORE the service handle is resolved."""
        manager, room_service = _stub_manager()
        tools = create_room_tools(
            manager, current_instance_id="iid-1", agent_id=""
        )
        for index, payload in enumerate(
            (
                {"name": "r"},
                {"room_id": "r", "text": "t"},
                {"room_id": "r"},
                {"room_id": "r", "proposal_seq": 1, "decision": "revive"},
                {"room_id": "r"},
            )
        ):
            raw = await tools[index].ainvoke(payload)
            assert raw.startswith("Error:"), (index, raw)
            assert "unknown-agent" in raw, (index, raw)
        for seam in (
            room_service.get_room,
            room_service.get_member,
            room_service.list_members,
            room_service.append_message,
            room_service.read_messages,
            room_service.create_room,
            room_service.close_room,
        ):
            seam.assert_not_called()

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
                {"room_id": "r"},
            )
        ):
            raw = await tools[index].ainvoke(payload)
            assert raw.startswith("Error:"), (index, raw)
            assert "unknown-agent" in raw, (index, raw)


# ── F. the house refusal helpers (the G pins) ──────────────────────────


class TestTheHouseRefusalHelpers:
    """The house refusal machinery — the never-raise backstop and the
    gate's SECOND branch.

    Two pins (the G item):

    * **the gate's SECOND branch** — the H1 extraction left the helper
      with two branches: the authorization check (the first) and the
      caller-instance-id check (the second). Only the FIRST was
      pinned; the SECOND (a blank instance id ⇒ the
      ``not-authorized`` refusal) now has its own pin.
    * **the internal-error census** — the house never-raise backstop
      ``_internal_error_refusal`` is called EXACTLY once per verb:
      a body that dropped it would raise into the agent turn, and a
      body that grew a second copy would drift from the shared
      envelope.
    """

    def test_the_gate_second_branch_refuses_a_blank_instance_id(self) -> None:
        """The SECOND branch of the shared gate: no instance id ⇒
        ``not-authorized``.

        The first branch (the agent identity) passes here — the
        caller IS resolved — so the refusal can only come from the
        second branch.
        """
        manager = MagicMock()
        manager._room_chat_service = MagicMock()
        manager._room_delivery_service = MagicMock()
        tools = create_room_tools(
            manager, current_instance_id="", agent_id="ari"
        )
        payloads = (
            {"name": "r"},
            {"room_id": "r", "text": "t"},
            {"room_id": "r"},
            {"room_id": "r", "proposal_seq": 1, "decision": "revive"},
            {"room_id": "r"},
        )
        for index, payload in enumerate(payloads):
            raw = asyncio.run(tools[index].ainvoke(payload))
            assert raw.startswith("Error:"), (index, raw)
            assert "reason=not-authorized" in raw, (index, raw)

    def test_the_gate_second_branch_is_the_instance_id_check(self) -> None:
        """The structural twin: the second branch tests the INSTANCE id.

        A helper that dropped the second branch (or swapped it for
        something else) REDs here.
        """
        tree = ast.parse(_module_source())
        helpers = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_gate"
        ]
        assert len(helpers) == 1
        statements = [
            node
            for node in helpers[0].body
            if not (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )
        ]
        branches = [node for node in statements if isinstance(node, ast.If)]
        assert len(branches) == 2, branches
        second = branches[1]
        test = second.test
        if isinstance(test, ast.UnaryOp):
            test = test.operand
        assert isinstance(test, ast.Name) and test.id == "caller_instance_id", (
            "the gate's second branch must test the caller instance id"
        )

    def test_every_verb_carries_exactly_one_internal_error_backstop(
        self,
    ) -> None:
        """The census: one ``_internal_error_refusal`` per verb.

        The house never-raise backstop — an unexpected exception is
        logged SERVER-SIDE and answered with the generic envelope (the
        PB-F1 leak class). A body that dropped it raises into the agent
        turn; a body that grew a second copy drifts from the shared
        envelope.
        """
        tree = ast.parse(_module_source())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_internal_error_refusal"
        ]
        assert len(calls) == len(TOOL_NAMES), (
            f"expected one internal-error backstop per verb "
            f"({len(TOOL_NAMES)}), got {len(calls)}"
        )
        # Each call names its OWN verb — the envelope labels the tool.
        labels = []
        for call in calls:
            assert call.args, ast.dump(call)
            label = call.args[0]
            assert isinstance(label, ast.Constant), ast.dump(label)
            labels.append(label.value)
        assert sorted(labels) == sorted(TOOL_NAMES), labels

    async def test_a_faulting_verb_answers_the_house_envelope(self) -> None:
        """The behavioral bite: an unexpected exception NEVER raises and
        NEVER leaks the raw text.

        The PB-F1 leak class: ``str(exc)`` / ``repr(exc)`` must not
        reach the payload.
        """
        manager, room_service = _stub_manager()
        boom = "sensitive-internal-detail"
        room_service.get_room = AsyncMock(side_effect=RuntimeError(boom))
        room_service.create_room = AsyncMock(side_effect=RuntimeError(boom))
        tools = {
            t.name: t
            for t in create_room_tools(
                manager, current_instance_id="iid-1", agent_id="ari"
            )
        }
        payloads = {
            "room_create": {"name": "r"},
            "room_post": {"room_id": "r", "text": "t"},
            "room_read": {"room_id": "r"},
            "room_task_decision": {
                "room_id": "r",
                "proposal_seq": 1,
                "decision": "revive",
            },
            "room_close": {"room_id": "r"},
        }
        for name, payload in payloads.items():
            raw = await tools[name].ainvoke(payload)
            assert raw.startswith("Error:"), (name, raw)
            assert f"{name} REFUSED" in raw, (name, raw)
            assert "reason=internal-error" in raw, (name, raw)
            assert boom not in raw, (name, raw)


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
