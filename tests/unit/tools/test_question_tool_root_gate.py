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

RED-pre-change (GATE PROOF — must fail before the implementation):
    T1  non-root child (parent_id set)                      -> no tool
    T3  job root (parent_id None, source_type absent)       -> no tool
    T4b plain HTTP-API root (parent_id None, no source_type)-> no tool
    T5  child that carries a chat source_type               -> no tool
    (T5 covers the fail-closed child-with-chat-source branch)
    plus the capability-E2E child half and the signature pin.

GREEN-pre-change (PRESERVATION GUARDS — must pass before AND after):
    T2  user-facing root (parent_id None, source_type=discord) -> tool
    T4a restore-path root with a stamped row (source_type=slack) -> tool
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

import json
from pathlib import Path
from unittest.mock import MagicMock

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

    def test_t4a_restore_path_root_with_stamped_row_has_question_tool(
        self, tmp_path: Path, registry_for
    ) -> None:
        """GREEN-pre-change (preservation guard) — T4a.

        The restore path reads the chat source from the instance row's
        ``instance_metadata["source_type"]`` stamp and threads it into the
        tool build. A root restored from a row that carries the stamp
        (source_type="slack") MUST keep the tool — otherwise a daemon
        restart would silently strip a legitimate root's capability.
        """
        agents_dir = _stage_synthetic_agent(
            tmp_path,
            "syn-question-restored-root",
            {"allow": [QUESTION_CATEGORY], "deny": []},
        )
        registry_for(agents_dir)

        tools = _build_instance_tools(
            "syn-question-restored-root", parent_id=None, source_type="slack"
        )

        assert QUESTION_TOOL_NAME in tools, (
            "a root restored from a source_type-stamped row must keep "
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
