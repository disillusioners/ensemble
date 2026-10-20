"""Build-time registration gate for the ``ask_questions`` tool.

Commission: hide the question tool from NON-ROOT instances. A "user-facing
root" is an instance bound to a real chat surface (Discord / Slack /
Telegram / WhatsApp) that has no parent.

P1 PREDICATE (authoritative — implemented in ``daemon.tools.instance``)::

    USER_FACING_ROOT := (parent_id IS NULL OR parent_id == "")
                         AND (source_type IN {discord, slack, telegram, whatsapp})

The predicate is evaluated from values THREADED INTO the tool build, never
from a DB row read at registration time: at spawn time the instance row is
not INSERTed yet, so a row read would return ``None`` and would strip the
tool from every freshly spawned chat root.

TWO-LAYER TRUTH (do not conflate):

* the GATE is NECESSARY — it HIDES the tool. A non-root never gets the
  tool registered, whatever its agent's allow-list says.
* the agent's allow-list is SUFFICIENT — it GRANTS the capability.

Effective capability = {user-facing root} INTERSECT {agent grants the
"question" category}.

Every test below builds the REAL toolset through
``daemon.tools.instance.create_instance_tools()`` and asserts on the BUILT
toolset only (tool names + resolved tool categories) — never on private
internals, never on line numbers.

ONE EXCEPTION (W3, fix round 2): ``TestGateBeforeFilterOrdering`` does NOT
build a toolset. It reads the SOURCE of ``daemon/tools/instance.py`` and pins
the RELATIVE ORDER of two call sites (the question-tool registration vs the
agent tool-filter application). It asserts an ORDER — never an absolute line
number — and it is the only test in this pack that looks at source instead of
at a built toolset.

RED-pre-change (GATE PROOF — must fail before the implementation):
    T1  non-root child (parent_id set)                      -> no tool
    T3  job root (parent_id None, source_type absent)       -> no tool
    T4b plain HTTP-API root (parent_id None, no source_type)-> no tool
    T5  child that carries a chat source_type               -> no tool
    (T5 covers the fail-closed child-with-chat-source branch)
    plus the capability-E2E child half and the signature pin.

GREEN-pre-change (PRESERVATION GUARDS — must pass before AND after):
    T2  user-facing root (parent_id None, source_type=discord) -> tool
    T4a legacy empty-string parent (parent_id "", source_type=discord) -> tool
    plus the root whose THREADED chat source_type reaches the build -> tool
    plus the capability-E2E root half.

HARNESS NOTE (why the harness is signature-adaptive)
---------------------------------------------------
``create_instance_tools`` gains two NEW keyword-only parameters in this
change. Before the change those parameters do not exist, so a harness that
always passes them raises ``TypeError`` — which would make the
preservation guards fail pre-change for a reason that has nothing to do
with the behaviour they protect.

The harness therefore PASSES the two values ONLY when the signature
already supports them, and otherwise builds the toolset the way the
pre-change code path does. The pre-change build registers the question
tool unconditionally, so the preservation guards still assert exactly what
they are meant to assert ("a legitimate root keeps the tool").

This adaptivity is NOT allowed to hide a missing implementation: the
dedicated signature pin
(``test_ac3_parent_id_and_source_type_are_keyword_only``) asserts that the
two parameters exist AND are keyword-only, and it is RED before the
change. If the implementation forgets the parameters, that pin fails
loudly.
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import daemon.registry as dr
from daemon.registry import AgentRegistry

# Repo root: tests/unit/tools/test_question_tool_root_gate.py -> parents[3].
REPO_ROOT = Path(__file__).resolve().parents[3]

QUESTION_TOOL_NAME = "ask_questions"
QUESTION_CATEGORY = "question"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _stage_synthetic_agent(
    tmp_path: Path, agent_id: str, tools_cfg: dict
) -> Path:
    """Stage a synthetic agent whose meta EXPLICITLY ALLOWS the "question"
    category.

    The explicit grant is the whole point of this pack: tool ABSENCE can
    never be blamed on the allow-list filter, so a failure can only be
    attributed to the gate.
    """
    real_meta = json.loads(
        (REPO_ROOT / "agents" / "watcher" / "meta.json").read_text(encoding="utf-8")
    )
    real_meta.pop("watchover", None)
    real_meta["id"] = agent_id
    real_meta["name"] = agent_id.title()
    real_meta["team_members"] = []
    real_meta["innate_skills"] = []
    real_meta["tools"] = tools_cfg
    agents_dir = tmp_path / "agents"
    agent_dir = agents_dir / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "meta.json").write_text(json.dumps(real_meta), encoding="utf-8")
    return agents_dir


@pytest.fixture
def registry_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Install a registry that discovers only the staged synthetic agent."""

    def _install(agents_dir: Path) -> AgentRegistry:
        registry = AgentRegistry(agents_dir)
        registry.discover()
        monkeypatch.setattr(dr, "_registry", registry)
        return registry

    return _install


def _supports_threaded_root_values() -> bool:
    """True once ``create_instance_tools`` accepts the two new keyword-only
    parameters (see the HARNESS NOTE in the module docstring)."""
    import inspect

    from daemon.tools.instance import create_instance_tools

    params = inspect.signature(create_instance_tools).parameters
    return "parent_id" in params and "source_type" in params


def _build_instance_tools(
    agent_id: str,
    *,
    parent_id: str | None = None,
    source_type: str | None = None,
) -> dict[str, object]:
    """Build the REAL per-instance toolset.

    Mirrors the established sibling harness
    (``tests/unit/tools/test_service_registration.py``): a MagicMock manager
    with a real-shaped LLM config (the documented default for
    ``allowed_models`` is ``[]``; the compare-tools vision gate fail-CLOSES
    at factory init on any non-list value).
    """
    from daemon.tools.instance import create_instance_tools

    manager = MagicMock(name="InstanceManager")
    manager.config.daemon.port = 0
    manager.config.llm.allowed_models = []
    kwargs: dict[str, object] = {}
    if _supports_threaded_root_values():
        kwargs["parent_id"] = parent_id
        kwargs["source_type"] = source_type
    tools = create_instance_tools(
        manager,
        f"inst-{agent_id}",
        agent_id,
        **kwargs,
    )
    return {getattr(t, "name", "?"): t for t in tools}


def _question_category_tools(tools_by_name: dict[str, object]) -> set[str]:
    """Names of the built tools that resolve to the "question" category."""
    return {
        name
        for name, tool in tools_by_name.items()
        if getattr(tool, "_tool_category", None) == QUESTION_CATEGORY
    }


# ---------------------------------------------------------------------------
# RED-pre-change — GATE PROOF
# ---------------------------------------------------------------------------


class TestGateProofRedPreChange:
    """These tests FAIL before the gate is implemented (the tool is present
    for a non-root) and PASS after it. They are the commission's red-state
    evidence."""

    def test_t1_non_root_child_has_no_question_tool(
        self, tmp_path: Path, registry_for
    ) -> None:
        """RED-pre-change (gate proof) — T1.

        A non-root CHILD (parent_id set) must NOT get the question tool,
        even though its agent explicitly allows the "question" category.
        Fails pre-change: the tool is registered unconditionally.
        """
        agents_dir = _stage_synthetic_agent(
            tmp_path,
            "syn-question-child",
            {"allow": [QUESTION_CATEGORY], "deny": []},
        )
        registry_for(agents_dir)

        tools = _build_instance_tools(
            "syn-question-child", parent_id="parent-abc", source_type=None
        )

        assert QUESTION_TOOL_NAME not in tools, (
            "a non-root child must not be registered with ask_questions "
            "(its agent explicitly allows the 'question' category, so only "
            "the root gate can explain absence)"
        )
        assert _question_category_tools(tools) == set(), (
            "a non-root child must resolve NO tool in the 'question' category"
        )

    def test_t3_job_root_without_source_type_has_no_question_tool(
        self, tmp_path: Path, registry_for
    ) -> None:
        """RED-pre-change (gate proof) — T3.

        A root spawned for a JOB (parent_id None, no chat source) is not a
        user-facing root: nobody is there to answer a question.
        Fails pre-change: the tool is registered unconditionally.
        """
        agents_dir = _stage_synthetic_agent(
            tmp_path,
            "syn-question-job-root",
            {"allow": [QUESTION_CATEGORY], "deny": []},
        )
        registry_for(agents_dir)

        tools = _build_instance_tools(
            "syn-question-job-root", parent_id=None, source_type=None
        )

        assert QUESTION_TOOL_NAME not in tools, (
            "a job root (no chat source) must not be registered with "
            "ask_questions"
        )
        assert _question_category_tools(tools) == set()

    def test_t4b_plain_http_api_root_has_no_question_tool(
        self, tmp_path: Path, registry_for
    ) -> None:
        """RED-pre-change (gate proof) — T4b.

        A root created through the plain HTTP API (parent_id None, no chat
        source_type) is not user-facing. Fails pre-change.
        """
        agents_dir = _stage_synthetic_agent(
            tmp_path,
            "syn-question-http-root",
            {"allow": [QUESTION_CATEGORY], "deny": []},
        )
        registry_for(agents_dir)

        tools = _build_instance_tools(
            "syn-question-http-root", parent_id=None, source_type=None
        )

        assert QUESTION_TOOL_NAME not in tools, (
            "a plain HTTP-API root must not be registered with ask_questions"
        )
        assert _question_category_tools(tools) == set()

    def test_t5_child_with_chat_source_type_has_no_question_tool(
        self, tmp_path: Path, registry_for
    ) -> None:
        """RED-pre-change (gate proof) — T5.

        FAIL-CLOSED: a child that somehow carries a chat source_type (e.g.
        a future inheritance bug) is STILL not a user-facing root — the
        root test is evaluated FIRST and wins. Fails pre-change.
        """
        agents_dir = _stage_synthetic_agent(
            tmp_path,
            "syn-question-chat-child",
            {"allow": [QUESTION_CATEGORY], "deny": []},
        )
        registry_for(agents_dir)

        tools = _build_instance_tools(
            "syn-question-chat-child", parent_id="parent-abc", source_type="discord"
        )

        assert QUESTION_TOOL_NAME not in tools, (
            "a child must not be registered with ask_questions even when it "
            "carries a chat source_type (fail closed)"
        )
        assert _question_category_tools(tools) == set()


# ---------------------------------------------------------------------------
# GREEN-pre-change — PRESERVATION GUARDS
# ---------------------------------------------------------------------------


class TestPreservationGuardsGreenPreChange:
    """These tests PASS before AND after the implementation: they prove the
    gate does not over-hide."""

    def test_t2_user_facing_root_has_question_tool(
        self, tmp_path: Path, registry_for
    ) -> None:
        """GREEN-pre-change (preservation guard) — T2.

        A user-facing root (parent_id None, source_type="discord") whose
        agent allows the "question" category MUST keep the tool.
        """
        agents_dir = _stage_synthetic_agent(
            tmp_path,
            "syn-question-chat-root",
            {"allow": [QUESTION_CATEGORY], "deny": []},
        )
        registry_for(agents_dir)

        tools = _build_instance_tools(
            "syn-question-chat-root", parent_id=None, source_type="discord"
        )

        assert QUESTION_TOOL_NAME in tools, (
            "a user-facing root must be registered with ask_questions"
        )
        assert _question_category_tools(tools) == {QUESTION_TOOL_NAME}

    def test_root_with_threaded_chat_source_type_has_question_tool(
        self, tmp_path: Path, registry_for
    ) -> None:
        """GREEN-pre-change (preservation guard) — threaded chat source.

        HONEST SCOPE (fix round 2, W1 — renamed from the over-claiming
        ``test_t4a_restore_path_root_with_stamped_row_has_question_tool``):

        This test asserts ONE thing: when a ROOT's chat ``source_type``
        reaches the tool build, the ``ask_questions`` tool IS registered.

        It does NOT exercise the RESTORE path. The restore path reads the
        chat source off the instance row's
        ``instance_metadata["source_type"]`` stamp and threads it into the
        build (``daemon/services/instance_lifecycle.py``, restore branch);
        that row-read seam is NOT covered by this pack — this pack builds
        the toolset directly and never touches a repository. The name and
        the docstring previously claimed restore-path coverage that no
        assertion in this file could deliver; the name now states exactly
        what is asserted, and the restore seam stays where it belongs (the
        restore lane's own coverage).

        The value threaded here is the same shape the restore lane reads
        (``"slack"`` — one of the four pinned chat sources), so the
        predicate it exercises is the real one.
        """
        agents_dir = _stage_synthetic_agent(
            tmp_path,
            "syn-question-threaded-source-root",
            {"allow": [QUESTION_CATEGORY], "deny": []},
        )
        registry_for(agents_dir)

        tools = _build_instance_tools(
            "syn-question-threaded-source-root", parent_id=None, source_type="slack"
        )

        assert QUESTION_TOOL_NAME in tools, (
            "a root whose chat source_type reaches the build must keep "
            "ask_questions"
        )
        assert _question_category_tools(tools) == {QUESTION_TOOL_NAME}

    def test_legacy_empty_string_parent_id_is_root_eligible(
        self, tmp_path: Path, registry_for
    ) -> None:
        """GREEN-pre-change (preservation guard).

        Legacy rows carry ``parent_id == ""`` instead of NULL. The root
        test treats the empty string as root-eligible — a legacy chat root
        must keep the tool.
        """
        agents_dir = _stage_synthetic_agent(
            tmp_path,
            "syn-question-legacy-root",
            {"allow": [QUESTION_CATEGORY], "deny": []},
        )
        registry_for(agents_dir)

        tools = _build_instance_tools(
            "syn-question-legacy-root", parent_id="", source_type="discord"
        )

        assert QUESTION_TOOL_NAME in tools, (
            "a legacy root (parent_id == '') must keep ask_questions"
        )


# ---------------------------------------------------------------------------
# Capability E2E — the two-layer model, proven end to end
# ---------------------------------------------------------------------------


class TestCapabilityE2E:
    """BLK-1 (review council): prove the TWO-LAYER model end to end with a
    REAL agent (the real ``leader`` meta, whose ``tools.allow`` contains
    "question").

    * as a chat-bound ROOT  -> the tool IS registered (gate admits AND the
      allow-list admits);
    * the SAME agent as a CHILD -> the tool is NOT registered (the gate
      hides it, regardless of the allow-list).

    T2 alone only proves the gate ADMITS; this pair proves that the gate
    is what hides the tool from a non-root, not the allow-list.
    """

    def test_leader_as_chat_root_registers_question_tool(self) -> None:
        """Capability E2E — root half.

        The real leader agent, bound to a chat source as a ROOT: the tool
        must be registered (gate admits + allow-list admits).
        """
        tools = _build_instance_tools(
            "leader", parent_id=None, source_type="discord"
        )

        assert QUESTION_TOOL_NAME in tools, (
            "a chat-bound root whose agent grants the 'question' category "
            "must be registered with ask_questions"
        )
        assert _question_category_tools(tools) == {QUESTION_TOOL_NAME}

    def test_same_leader_as_child_does_not_register_question_tool(self) -> None:
        """Capability E2E — child half.

        The SAME real leader agent as a CHILD: the tool must NOT be
        registered, even though leader's allow-list grants the category.
        """
        tools = _build_instance_tools(
            "leader", parent_id="parent-abc", source_type=None
        )

        assert QUESTION_TOOL_NAME not in tools, (
            "the same agent as a child must not be registered with "
            "ask_questions — the gate hides it, not the allow-list"
        )
        assert _question_category_tools(tools) == set()


# ---------------------------------------------------------------------------
# Signature pin — the threaded seam itself
# ---------------------------------------------------------------------------


class TestThreadingSignature:
    def test_ac3_parent_id_and_source_type_are_keyword_only(self) -> None:
        """RED-pre-change (gate proof) — AC3 seam pin.

        ``create_instance_tools`` must accept ``parent_id`` and
        ``source_type`` as KEYWORD-ONLY parameters. Keyword-only is what
        keeps every existing caller valid (no positional breakage) while
        letting the two call sites thread the root-predicate values.

        RED before the change: the parameters do not exist.
        """
        import inspect

        from daemon.tools.instance import create_instance_tools

        params = inspect.signature(create_instance_tools).parameters
        for name in ("parent_id", "source_type"):
            assert name in params, (
                f"create_instance_tools must accept {name!r} so the root "
                "predicate can be evaluated from threaded values"
            )
            assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, (
                f"{name!r} must be KEYWORD_ONLY so no existing caller can "
                f"pass it positionally (got {params[name].kind!r})"
            )
            assert params[name].default is None, (
                f"{name!r} must default to None (fail-closed: an unthreaded "
                "call is NOT a user-facing root)"
            )


# ---------------------------------------------------------------------------
# Structural pin — the gate is applied BEFORE the agent's tool filter
# ---------------------------------------------------------------------------


INSTANCE_TOOLS_SOURCE = REPO_ROOT / "daemon" / "tools" / "instance.py"


def _question_registration_and_filter_lines(source: str) -> tuple[int, int]:
    """Locate the two LOAD-BEARING call sites inside ``create_instance_tools``.

    Returns ``(registration_lineno, filter_lineno)`` — the positions of the
    ``create_question_tools(...)`` call and of the ``_apply_tool_filter(...)``
    call, both scoped to the ``create_instance_tools`` body.

    The search is AST-based, never a text grep, for two reasons:

    * COMMENTS mention both symbols (the load-bearing placement note names
      ``_apply_tool_filter``) — a text grep would pick the comment up as a
      "call site" and the pin would compare a comment against a call;
    * the module-level ``def _apply_tool_filter`` lives OUTSIDE the builder
      — scoping to the function body keeps it out of the result.

    Only ABSOLUTE positions are returned. The ORDER between the two is what
    the pin asserts; no line number is hardcoded anywhere.
    """
    tree = ast.parse(source)
    builder = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "create_instance_tools"
        ),
        None,
    )
    assert builder is not None, (
        "daemon/tools/instance.py no longer defines create_instance_tools — "
        "the gate-before-filter ordering pin has lost its anchor"
    )

    registration: list[int] = []
    filter_lines: list[int] = []
    for node in ast.walk(builder):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "create_question_tools":
            registration.append(node.lineno)
        elif name == "_apply_tool_filter":
            filter_lines.append(node.lineno)

    assert registration, (
        "no create_question_tools(...) call site found inside "
        "create_instance_tools — the question-tool registration is gone"
    )
    assert filter_lines, (
        "no _apply_tool_filter(...) call site found inside "
        "create_instance_tools — the agent tool filter is gone"
    )
    return min(registration), min(filter_lines)


def _assert_registration_precedes_filter(source: str) -> tuple[int, int]:
    """Assert the question-tool registration precedes the tool filter."""
    registration_line, filter_line = _question_registration_and_filter_lines(source)
    assert registration_line < filter_line, (
        "the ask_questions GATE must be applied BEFORE the agent's tool "
        "filter: an agent's tools.allow entry can never re-admit a gated "
        "tool for a non-root because the gate runs first "
        f"(registration at line {registration_line}, filter at line "
        f"{filter_line})"
    )
    return registration_line, filter_line


def _mutate_filter_before_registration(source: str) -> str:
    """Return a MUTANT of *source* with the tool-filter application moved
    BEFORE the question-tool registration.

    Used as the pin's own self-check: the mutant is a faithful "someone
    reordered the two call sites" edit, so the pin must FAIL on it. The
    mutant is built in memory — the repository file is never touched.
    """
    tree = ast.parse(source)
    builder = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "create_instance_tools"
        ),
        None,
    )
    assert builder is not None, "create_instance_tools not found"

    gate_block = None
    filter_stmt = None
    for node in ast.walk(builder):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Call):
            if getattr(node.test.func, "id", None) == "_is_user_facing_root":
                gate_block = node
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "id", None) == "_apply_tool_filter"
        ):
            filter_stmt = node
    assert gate_block is not None, "the root-gate block was not found"
    assert filter_stmt is not None, "the tool-filter call was not found"

    lines = source.splitlines(keepends=True)
    gate_start = gate_block.lineno
    filter_start, filter_end = filter_stmt.lineno, filter_stmt.end_lineno
    assert gate_start < filter_start, (
        "the mutant builder expects the gate to come first in the real source"
    )
    filter_lines = lines[filter_start - 1 : filter_end]
    mutant = "".join(
        lines[: gate_start - 1]
        + filter_lines
        + lines[gate_start - 1 : filter_start - 1]
        + lines[filter_end:]
    )
    ast.parse(mutant)  # the mutant must remain syntactically valid
    return mutant


class TestGateBeforeFilterOrdering:
    """W3 — the root gate is applied BEFORE the agent's tool filter."""

    def test_gate_registration_precedes_tool_filter_in_build_path(self) -> None:
        """W3 structural pin — gate BEFORE filter in the build path.

        WHAT structural property it pins: inside
        ``daemon/tools/instance.py::create_instance_tools``, the
        ``create_question_tools(...)`` REGISTRATION happens BEFORE the
        ``_apply_tool_filter(...)`` APPLICATION.

        WHY it matters: the two mechanisms have OPPOSITE jobs. The gate is
        NECESSARY — it HIDES ``ask_questions`` from a non-root; the agent's
        ``tools.allow`` list is SUFFICIENT — it GRANTS the capability to an
        agent. If the filter ran FIRST, an agent whose ``tools.allow``
        contains the ``question`` category could re-admit the tool into a
        non-root's toolset (the filter would keep it because the agent
        grants the category) — the gate would then only ever remove the
        tool, never guarantee its absence. Running the gate FIRST makes the
        allow-list unable to re-admit a gated tool: a non-root's toolset can
        never contain a question-category tool, whatever the agent allows.

        HOW it fails if the property breaks: the pin reads the SOURCE of
        ``daemon/tools/instance.py``, resolves BOTH call sites with the AST
        (never a text grep, never a hardcoded line number — comments that
        name the symbols and the module-level ``def _apply_tool_filter``
        are both out of scope), and asserts the registration position is
        strictly smaller than the filter position. Reordering the two call
        sites makes the assertion fail with both positions in the message.

        SELF-CHECK: the pin also builds an in-memory MUTANT of the real
        source in which the filter call is moved BEFORE the gate, and
        asserts the same check FAILS on it — so the pin cannot silently
        degrade into a no-op that passes on an inverted file.
        """
        source = INSTANCE_TOOLS_SOURCE.read_text(encoding="utf-8")

        registration_line, filter_line = _assert_registration_precedes_filter(source)

        mutant = _mutate_filter_before_registration(source)
        with pytest.raises(AssertionError):
            _assert_registration_precedes_filter(mutant)

        assert registration_line < filter_line, (
            "gate registration must precede the tool filter "
            f"(got {registration_line} vs {filter_line})"
        )


# ---------------------------------------------------------------------------
# Mappings lane — the chat-bound root must keep the tool (AC6)
# ---------------------------------------------------------------------------


class TestMappingsLaneThreading:
    """AC6 — ``daemon/routers/mappings.py`` spawns a CHAT-BOUND ROOT.

    Before the fix that lane spawned with NO ``source_type``, so the
    USER-FACING ROOT predicate would false-negative and a genuine chat
    root would silently lose the ``ask_questions`` tool.

    The lane is exercised with a mocked manager (no DB, no daemon) and
    the assertion is that the chat platform type of the SOURCE row
    reaches the spawn — the exact value the tool build then evaluates.
    The final link to the BUILT TOOLSET is asserted too: that value, fed
    into the tool build as a root, registers the tool.
    """

    async def test_mappings_lane_threads_the_sources_chat_type(self, monkeypatch) -> None:
        """The mappings lane hands the source's chat platform type to the
        spawn that feeds the tool build."""
        import daemon.routers.mappings as mappings_router
        from daemon.models import InstanceMappingCreate

        source = SimpleNamespace(source_type="discord")
        saved = SimpleNamespace(
            mapping_id="src-1:user-1",
            source_id="src-1",
            external_user_id="user-1",
            agent_instance_id="inst-123",
            agent_id="leader",
            agent_dir="/agents/leader",
            mapping_metadata=None,
            last_message_at=None,
            created_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
        )

        manager = MagicMock()
        manager.is_write_paused = False
        manager.ensure_mcp_preloaded = AsyncMock()
        manager.spawn_instance = MagicMock(return_value=("inst-123", None))
        manager._source_repository = MagicMock()
        manager._source_repository.get_source_config = MagicMock(return_value=source)
        # First call = "does a mapping already exist?" (None), second = fetch saved.
        manager._source_repository.get_instance_mapping = MagicMock(
            side_effect=[None, saved]
        )
        manager._source_repository.create_instance_mapping = MagicMock()

        monkeypatch.setattr(
            mappings_router, "_get_manager", lambda request: manager
        )
        monkeypatch.setattr(
            mappings_router,
            "validate_agent_id",
            lambda agent_id: ("leader", Path("/agents/leader")),
        )

        await mappings_router.create_mapping(
            "src-1",
            InstanceMappingCreate(external_user_id="user-1", agent_id="leader"),
            MagicMock(),
        )

        assert manager.spawn_instance.call_count == 1
        spawn_kwargs = manager.spawn_instance.call_args.kwargs
        assert spawn_kwargs.get("source_type") == "discord", (
            "the mappings lane must hand the source's chat platform type to "
            "the spawn — otherwise a chat-bound root false-negatives the "
            "user-facing-root predicate and loses ask_questions"
        )

        # Link to the BUILT TOOLSET: the threaded value, fed into the tool
        # build as a ROOT, registers the question tool.
        tools = _build_instance_tools(
            "leader",
            parent_id=None,
            source_type=spawn_kwargs["source_type"],
        )
        assert QUESTION_TOOL_NAME in tools, (
            "a chat-bound root spawned through the mappings lane must be "
            "registered with ask_questions"
        )


# ---------------------------------------------------------------------------
# Taxonomy pins — the chat-source set must not silently drift
# ---------------------------------------------------------------------------


class TestChatSourceTaxonomyPins:
    def test_three_way_equality_of_chat_source_taxonomies(self) -> None:
        """N7 pin — the gate's chat-source set must equal BOTH existing
        authorities, so the taxonomy cannot silently drift into a third
        copy:

        * ``daemon.repositories.instance.CHAT_SOURCE_TYPES``
          (the Projects-tab chat-source filter set), and
        * ``daemon.tools.upgrade_journal.USER_ORIGIN_CHAT_SOURCE_TYPES``
          (the registry-backed live-upgrade user-origin gate set).
        """
        from daemon.repositories.instance import CHAT_SOURCE_TYPES
        from daemon.repositories.instance.repository import (
            SQLModelInstanceRepository,
        )
        from daemon.tools.upgrade_journal import USER_ORIGIN_CHAT_SOURCE_TYPES

        from daemon.tools.instance import CHAT_SOURCE_TYPES_FOR_ROOT

        assert set(CHAT_SOURCE_TYPES_FOR_ROOT) == set(CHAT_SOURCE_TYPES), (
            "the gate's chat-source set drifted from the repository's "
            "CHAT_SOURCE_TYPES"
        )
        assert set(CHAT_SOURCE_TYPES_FOR_ROOT) == set(
            USER_ORIGIN_CHAT_SOURCE_TYPES
        ), (
            "the gate's chat-source set drifted from the upgrade-journal's "
            "USER_ORIGIN_CHAT_SOURCE_TYPES"
        )
        assert set(CHAT_SOURCE_TYPES) == set(SQLModelInstanceRepository.CHAT_SOURCE_TYPES)
        assert set(CHAT_SOURCE_TYPES_FOR_ROOT) == {
            "discord",
            "slack",
            "telegram",
            "whatsapp",
        }, (
            "the pinned chat-source set is the FOUR-member registry set; a "
            "change here is a deliberate decision, not drift"
        )
