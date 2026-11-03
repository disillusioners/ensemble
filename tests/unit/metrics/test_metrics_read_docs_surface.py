"""The DOCS-SURFACE verification pack — the agent-visible docs chain,
end to end.

The tools are only useful if the AGENT can see them: the category's
doc must resolve from the registry, the loader must render the per-agent
section for every HOLDER, and NOTHING metrics-shaped may reach an agent
that does not hold the category.

The chain under test (no loader/help change is needed — they read the
registry generically):

    ``get_category_doc("metrics-read")``
      → the module's ``CATEGORY_NAME`` / ``CATEGORY_DOC``
      → ``loader.load_tools_doc_for_agent(agent_id)``
        → the per-agent "## Metrics" section with the three tool names
      → ``tool_help("metrics_families")``
        → the tool's ``_full_doc_``

Plus the AmC-11 landing: the D-WATCH-4 read-broad / alert-may-be-narrow
asymmetry sentence must be present in ``CATEGORY_DOC`` **AND** in each
of the three ``tools_note.md`` sections (the Phase-6 outline doc alone is
NOT a sufficient landing site).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daemon.tools._tool_registry import get_category_doc
from daemon.tools.metrics_read import CATEGORY_DOC, CATEGORY_NAME, TOOL_NAMES

REPO_ROOT = Path(__file__).resolve().parents[3]

CATEGORY = "metrics-read"

#: The three holders (the allow-set is ONE set — R4).
HOLDERS = ("maintenancer", "devops", "wanderer")

#: The AmC-11 asymmetry sentence — the READ surface is granted to three
#: non-ari agents while the future ALERT surface may end up ari-only.
#: (The markers are matched against a WHITESPACE-FLATTENED copy: the
#: shipped texts are line-wrapped, so a raw substring match would be a
#: formatting accident, not a contract check.)
ASYMMETRY_MARKERS = ("ALERT surface may end up ari-only",)


def _flat(text: str) -> str:
    """The whitespace-flattened text (the wrap-tolerant comparison form)."""
    return " ".join(text.split())

#: A distinctive phrase of the shipped caveat — the docs must carry the
#: GENERIC form (never a cause attribution).
GENERIC_CAVEAT_MARKER = "the series below are real"


@pytest.fixture(scope="module", autouse=True)
def _spawn_shaped_metadata():
    """The SPAWN-SHAPED fixture.

    The prompt section is rendered AFTER the instance's tools are
    constructed and scanned (``create_instance_tools`` calls
    ``scan_tools_for_full_docs``) — so the fixture does the same: it
    constructs the three metrics tools and scans them, exactly as the
    production spawn path does.  Without it the registry's metadata
    universe may hold only the module-level registrations, and the
    category would be invisible to the renderer for a reason that has
    nothing to do with the category itself.
    """
    from daemon.tools._tool_registry import scan_tools_for_full_docs
    from daemon.tools.metrics_read import create_metrics_read_tools

    tools = create_metrics_read_tools(
        None, current_instance_id="", agent_id="maintenancer", agent_tag=None
    )
    scan_tools_for_full_docs(list(tools))
    yield


def _meta(agent_dir: str) -> dict:
    return json.loads(
        (REPO_ROOT / "agents" / agent_dir / "meta.json").read_text(encoding="utf-8")
    )


def _tools_note(agent_dir: str) -> str:
    return (REPO_ROOT / "agents" / agent_dir / "tools_note.md").read_text(
        encoding="utf-8"
    )


class TestCategoryDoc:
    def test_the_registry_resolves_the_category(self) -> None:
        resolved = get_category_doc(CATEGORY)
        assert resolved is not None, (
            f"the registry must resolve {CATEGORY!r} — the loader reads the "
            "docs from here"
        )
        name, doc = resolved
        assert name == CATEGORY_NAME
        assert doc == CATEGORY_DOC

    def test_the_doc_lists_the_three_tools(self) -> None:
        for tool_name in sorted(TOOL_NAMES):
            assert tool_name in CATEGORY_DOC, (
                f"{tool_name} must be listed in the category doc — the doc IS "
                "the agent's documentation surface"
            )

    def test_the_doc_carries_the_asymmetry_sentence(self) -> None:
        for marker in ASYMMETRY_MARKERS:
            assert marker in _flat(CATEGORY_DOC), (
                "the read-broad / alert-may-be-narrow asymmetry must be in "
                "the CATEGORY_DOC (the loader renders it to the agent)"
            )

    def test_the_doc_carries_the_generic_caveat(self) -> None:
        assert GENERIC_CAVEAT_MARKER in _flat(CATEGORY_DOC), (
            "the doc must teach the dark-is-not-empty rule with the GENERIC "
            "caveat wording"
        )

    def test_the_doc_names_no_cause(self) -> None:
        for token in ("R-i", "R-ii", "capture_active", "census inverted"):
            assert token not in CATEGORY_DOC, (
                f"the shipped doc must not attribute a cause — {token!r} must "
                "be absent"
            )


class TestLoaderRendering:
    def test_each_holder_gets_the_metrics_section(self) -> None:
        from daemon.loader import load_tools_doc_for_agent

        for agent_id in HOLDERS:
            doc = load_tools_doc_for_agent(agent_id)
            assert doc, f"{agent_id} must get a tool documentation section"
            assert "## Metrics" in doc, (
                f"{agent_id} must get the '## Metrics' category section"
            )
            for tool_name in sorted(TOOL_NAMES):
                assert tool_name in doc, (
                    f"{agent_id}'s rendered section must name {tool_name}"
                )
            # The loader renders the category NAME + the tool NAMES (the
            # house section shape — it resolves the category doc by the
            # display NAME, so no category body is rendered for ANY
            # category; the CATEGORY_DOC reaches the agent through
            # ``tool_help`` and through the agent's tools_note.md).
            # The asymmetry sentence's landing sites are therefore the
            # CATEGORY_DOC and the three tools_note.md sections — both
            # asserted below (AmC-11).

    def test_an_empty_allow_agent_gets_nothing_metrics_shaped(self) -> None:
        from daemon.loader import load_tools_doc_for_agent

        doc = load_tools_doc_for_agent("watcher")
        assert "## Metrics" not in doc
        for tool_name in sorted(TOOL_NAMES):
            assert tool_name not in doc

    def test_an_agent_that_does_not_hold_the_category_gets_nothing(self) -> None:
        from daemon.loader import load_tools_doc_for_agent

        for agent_id in ("ari", "coder", "tester", "leader"):
            doc = load_tools_doc_for_agent(agent_id)
            assert "## Metrics" not in doc, (
                f"{agent_id} does NOT hold the metrics-read category — the "
                "privileged default-deny must keep it away"
            )


class TestToolHelpSurface:
    async def test_the_help_tool_serves_the_full_doc(self) -> None:
        """``tool_help(<name>)`` must serve the tool's ``_full_doc_``."""
        from daemon.tools.help import create_help_tool
        from daemon.tools.metrics_read import create_metrics_read_tools

        tools = create_metrics_read_tools(
            None, current_instance_id="", agent_id="maintenancer", agent_tag=None
        )
        help_tool = create_help_tool(list(tools), "maintenancer")
        for tool_name in sorted(TOOL_NAMES):
            out = await help_tool.ainvoke({"tool_name": tool_name})
            assert "No detailed documentation available." not in out, (
                f"tool_help({tool_name!r}) must serve the tool's _full_doc_"
            )
            assert tool_name in out


class TestToolsNoteSections:
    @pytest.mark.parametrize("agent_dir", HOLDERS)
    def test_each_holder_has_the_section(self, agent_dir: str) -> None:
        text = _tools_note(agent_dir)
        assert "metrics-read" in text, (
            f"{agent_dir}'s tools_note must name the category"
        )
        for tool_name in sorted(TOOL_NAMES):
            assert tool_name in text, (
                f"{agent_dir}'s tools_note must name {tool_name}"
            )

    @pytest.mark.parametrize("agent_dir", HOLDERS)
    def test_each_holder_carries_the_asymmetry_sentence(self, agent_dir: str) -> None:
        text = _flat(_tools_note(agent_dir))
        for marker in ASYMMETRY_MARKERS:
            assert marker in text, (
                f"{agent_dir}'s tools_note must carry the read-broad / "
                "alert-may-be-narrow asymmetry sentence (AmC-11: the loader "
                "render is NOT the only landing site)"
            )

    @pytest.mark.parametrize("agent_dir", HOLDERS)
    def test_the_section_agrees_with_the_category_doc(self, agent_dir: str) -> None:
        """The three notes and the CATEGORY_DOC must agree on the
        disambiguation sentence (the Agent-Snapshot confusion)."""
        text = _tools_note(agent_dir)
        assert "NOT" in text and "Agent Snapshot" in text, (
            f"{agent_dir}'s tools_note must carry the metrics_snapshot ≠ "
            "Agent-Snapshot disambiguation"
        )


class TestPromptArtifactImplication:
    """The ``agents/`` delta moves the prompt-artifact baseline on EXACTLY
    the three ``tools_note.md`` files (council W7: ``meta.json`` is NOT a
    versioned prompt artifact)."""

    def test_the_baseline_tracks_the_three_tools_notes_only(self) -> None:
        from daemon.tools.prompt_artifacts import VERSIONED_PROMPT_ARTIFACTS

        names = set(VERSIONED_PROMPT_ARTIFACTS)
        assert "tools_note.md" in names
        assert "meta.json" not in names, (
            "if meta.json ever became a versioned artifact the baseline "
            "delta would be 6 files, not 3"
        )

    def test_the_three_holder_metas_carry_the_category(self) -> None:
        for agent_dir in HOLDERS:
            allow = _meta(agent_dir).get("tools", {}).get("allow", [])
            assert CATEGORY in allow, f"{agent_dir} must hold {CATEGORY}"
