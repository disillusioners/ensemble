"""Functional tests for OpenSpace innate skill loading and prompt composition.

Verifies Phase 3 of the OpenSpace MCP integration:
- `load_agent_skills()` discovers the openspace skill from the centralized
  agents/_prompt_system/innate-skills/ directory when an agent's meta declares
  `"innate_skills": ["openspace"]`.
- `compose_system_prompt()` includes the openspace skill content as a section
  in the composed system prompt (so the agent sees the 4 OpenSpace tools).
- When openspace is NOT in `innate_skills`, the composed prompt does NOT
  include the OpenSpace tool names.

OpenSpace is an instructional-only innate skill:
- It is NOT in `INNATE_SKILL_TOOL_CATEGORIES` (daemon/tools/instance.py:52-55),
  so its tools are NOT auto-granted.
- The 4 OpenSpace tools must be granted explicitly via `tools.allow` in the
  agent's meta.json.
- The skill prompt itself is loaded independently of tool access by
  `load_agent_skills()`.

These tests use the real skill file at
agents/_prompt_system/innate-skills/openspace/skill.md (no mocking) to verify
the actual production path resolution.
"""

import json
import re
from pathlib import Path

import pytest


# Path constants
AGENTS_DIR = Path(__file__).parent.parent.parent / "agents"
# Use devops as a stand-in agent dir — its meta.json has empty innate_skills,
# so we can override the meta dict per-test to simulate different configurations.
DEVOPS_AGENT_DIR = AGENTS_DIR / "devops"
OPENSPACE_SKILL_FILE = AGENTS_DIR / "_prompt_system" / "innate-skills" / "openspace" / "skill.md"

# Canonical OpenSpace tool names that MUST appear in the composed prompt when
# the openspace skill is loaded (used as substring assertions).
OPENSPACE_TOOL_NAMES = [
    "mcp_openspace_execute_task",
    "mcp_openspace_search_skills",
    "mcp_openspace_fix_skill",
    "mcp_openspace_upload_skill",
]


def _read_openspace_skill() -> str:
    """Read the real openspace skill file (no mocking)."""
    return OPENSPACE_SKILL_FILE.read_text(encoding="utf-8")


def _basic_prompts() -> dict[str, str]:
    """Minimal prompts dict for compose_system_prompt tests.

    Includes only the fields needed to exercise the skill-injection code path
    (section 4 of compose_system_prompt). Empty soul/rule/workflow content
    keeps assertions focused on skill content.
    """
    return {
        "soul": "# Test Soul\n\nI am a test agent.",
        "rule": "# Rules\n\nTest rules.",
        "workflow": "# Workflow\n\nTest workflow.",
    }


# =============================================================================
# 1. Skill Discovery
# =============================================================================


class TestOpenspaceSkillDiscovery:
    """Verify `load_agent_skills()` discovers the openspace skill file."""

    def test_openspace_skill_file_exists(self) -> None:
        """The openspace skill.md file must exist at the centralized location."""
        assert OPENSPACE_SKILL_FILE.exists(), (
            f"openspace skill.md not found at {OPENSPACE_SKILL_FILE}"
        )
        assert OPENSPACE_SKILL_FILE.is_file(), (
            f"openspace skill.md is not a regular file: {OPENSPACE_SKILL_FILE}"
        )

    def test_openspace_skill_file_contains_tool_names(self) -> None:
        """The real skill.md must mention all 4 OpenSpace tool names."""
        content = _read_openspace_skill()
        for tool_name in OPENSPACE_TOOL_NAMES:
            assert tool_name in content, (
                f"Expected {tool_name!r} in openspace skill.md, but it was not found. "
                f"First 200 chars: {content[:200]!r}"
            )

    def test_load_agent_skills_discovers_openspace(self) -> None:
        """When meta declares innate_skills=['openspace'], the skill is loaded."""
        from daemon.loader import load_agent_skills

        meta = {"innate_skills": ["openspace"]}
        skills = load_agent_skills(DEVOPS_AGENT_DIR, meta)

        assert "openspace" in skills, (
            f"Expected 'openspace' key in skills dict, got keys: {list(skills.keys())}"
        )
        assert isinstance(skills["openspace"], str), (
            f"openspace skill content should be str, got {type(skills['openspace'])}"
        )
        assert len(skills["openspace"]) > 0, "openspace skill content should be non-empty"

    def test_load_agent_skills_openspace_matches_real_file(self) -> None:
        """The loaded openspace content must equal the real skill.md file content."""
        from daemon.loader import load_agent_skills

        skills = load_agent_skills(DEVOPS_AGENT_DIR, {"innate_skills": ["openspace"]})
        real_content = _read_openspace_skill()

        assert skills["openspace"] == real_content, (
            "Loaded openspace content should be byte-identical to the real skill.md file. "
            f"Loaded length: {len(skills['openspace'])}, "
            f"Real length: {len(real_content)}"
        )

    def test_load_agent_skills_missing_skill_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A declared but missing skill should log a warning, not raise.

        Verifies the loader is permissive: unknown/missing innate skills
        degrade gracefully (warn + skip) rather than blocking prompt composition.
        This matters for OpenSpace because agents might declare it before the
        skill file is installed.
        """
        import logging

        from daemon.loader import load_agent_skills

        meta = {"innate_skills": ["openspace", "definitely-not-a-real-skill-xyz"]}
        with caplog.at_level(logging.WARNING, logger="daemon.loader"):
            skills = load_agent_skills(DEVOPS_AGENT_DIR, meta)

        # Real skill is still loaded despite the bogus entry
        assert "openspace" in skills, "Real openspace skill should still be loaded"
        # Bogus entry is silently dropped (not in result)
        assert "definitely-not-a-real-skill-xyz" not in skills, (
            "Bogus skill should not appear in result"
        )
        # A warning was logged for the missing skill
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("definitely-not-a-real-skill-xyz" in m for m in warning_msgs), (
            f"Expected a warning mentioning the missing skill, got: {warning_msgs}"
        )

    def test_load_agent_skills_empty_list_falls_through(self) -> None:
        """Empty innate_skills list should NOT trigger the centralized loader path.

        Verifies the truthy-check semantics: [] is treated as absent so legacy
        agents with empty arrays continue to work.
        """
        from daemon.loader import load_agent_skills

        # Load the actual devops meta (which has innate_skills: [])
        meta_path = DEVOPS_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        assert meta.get("innate_skills") == [], (
            f"Precondition: devops meta should have empty innate_skills, got {meta.get('innate_skills')}"
        )

        skills = load_agent_skills(DEVOPS_AGENT_DIR, meta)
        # No skills should be loaded via the centralized path
        assert "openspace" not in skills, (
            f"openspace should not be loaded with empty innate_skills, got: {list(skills.keys())}"
        )


# =============================================================================
# 2. Prompt Composition Inclusion
# =============================================================================


class TestOpenspaceSkillInPromptComposition:
    """Verify the openspace skill content appears in the composed system prompt."""

    def test_compose_includes_openspace_section(self) -> None:
        """When openspace is in the skills dict, compose_system_prompt includes it."""
        from daemon.loader import compose_system_prompt

        skills = {"openspace": _read_openspace_skill()}
        prompts = _basic_prompts()

        system_prompt = compose_system_prompt(prompts, skills=skills)

        # All 4 OpenSpace tool names must appear in the composed prompt
        for tool_name in OPENSPACE_TOOL_NAMES:
            assert tool_name in system_prompt, (
                f"Expected {tool_name!r} in composed system prompt, but it was missing."
            )

    def test_compose_includes_openspace_via_load_then_compose(self) -> None:
        """End-to-end: load_agent_skills + compose_system_prompt produces a prompt
        that contains OpenSpace tool names."""
        from daemon.loader import compose_system_prompt, load_agent_skills

        # Use the actual loader (not a hand-built dict) to verify the real path
        skills = load_agent_skills(DEVOPS_AGENT_DIR, {"innate_skills": ["openspace"]})
        prompts = _basic_prompts()

        system_prompt = compose_system_prompt(prompts, skills=skills)

        for tool_name in OPENSPACE_TOOL_NAMES:
            assert tool_name in system_prompt, (
                f"End-to-end check failed: {tool_name!r} not in composed prompt "
                f"(load_agent_skills → compose_system_prompt pipeline)"
            )

    def test_compose_openspace_section_is_distinct(self) -> None:
        """The openspace section should be added as a distinct, identifiable block.

        We check that "OpenSpace-Skill" (the H1 heading) appears in the prompt
        — this is the unique marker the skill.md uses to title itself.
        """
        from daemon.loader import compose_system_prompt

        skills = {"openspace": _read_openspace_skill()}
        prompts = _basic_prompts()
        system_prompt = compose_system_prompt(prompts, skills=skills)

        # H1 heading from the skill file
        assert "OpenSpace-Skill" in system_prompt or "# OpenSpace-Skill" in system_prompt, (
            "Composed prompt should include the openspace skill's H1 heading 'OpenSpace-Skill'"
        )

    def test_compose_section_separator_present(self) -> None:
        """The compose_system_prompt should join sections with the documented separator."""
        from daemon.loader import compose_system_prompt

        skills = {"openspace": _read_openspace_skill()}
        prompts = _basic_prompts()
        system_prompt = compose_system_prompt(prompts, skills=skills)

        # `---` is the section separator used by compose_system_prompt
        assert "\n---\n" in system_prompt, (
            "Composed prompt should use '---' as section separator"
        )


# =============================================================================
# 3. Prompt Composition Exclusion
# =============================================================================


class TestOpenspaceSkillExclusionFromComposition:
    """Verify the openspace content is NOT included when not declared in innate_skills."""

    def test_compose_without_skills_omits_openspace(self) -> None:
        """No skills dict → no openspace tool names in the composed prompt."""
        from daemon.loader import compose_system_prompt

        prompts = _basic_prompts()
        system_prompt = compose_system_prompt(prompts, skills=None)

        for tool_name in OPENSPACE_TOOL_NAMES:
            assert tool_name not in system_prompt, (
                f"{tool_name!r} should NOT appear in a prompt with no skills, "
                f"but it was found."
            )

    def test_compose_with_empty_skills_omits_openspace(self) -> None:
        """Empty skills dict → no openspace tool names."""
        from daemon.loader import compose_system_prompt

        prompts = _basic_prompts()
        system_prompt = compose_system_prompt(prompts, skills={})

        for tool_name in OPENSPACE_TOOL_NAMES:
            assert tool_name not in system_prompt, (
                f"{tool_name!r} should NOT appear in a prompt with empty skills dict."
            )

    def test_compose_with_unrelated_skill_omits_openspace(self) -> None:
        """Loading a different innate skill (e.g. 'chart') should NOT inject openspace content."""
        from daemon.loader import compose_system_prompt, load_agent_skills

        # Use a different real skill to verify openspace content is skill-specific
        chart_skill_path = AGENTS_DIR / "_prompt_system" / "innate-skills" / "chart" / "skill.md"
        if not chart_skill_path.exists():
            pytest.skip("chart skill not present in this checkout")

        # Load only the chart skill (no openspace in meta)
        skills = load_agent_skills(DEVOPS_AGENT_DIR, {"innate_skills": ["chart"]})
        assert "openspace" not in skills, (
            f"openspace should not be loaded when only chart is declared, got: {list(skills.keys())}"
        )

        prompts = _basic_prompts()
        system_prompt = compose_system_prompt(prompts, skills=skills)

        for tool_name in OPENSPACE_TOOL_NAMES:
            assert tool_name not in system_prompt, (
                f"{tool_name!r} should NOT appear in a prompt composed from chart-only skills."
            )

    def test_full_pipeline_empty_innate_skills_omits_openspace(self) -> None:
        """End-to-end: devops agent (empty innate_skills) prompt must not mention OpenSpace tools."""
        from daemon.loader import compose_system_prompt, load_agent_prompts, load_agent_skills

        # Use real devops meta (empty innate_skills)
        meta_path = DEVOPS_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        prompts = load_agent_prompts(DEVOPS_AGENT_DIR)
        skills = load_agent_skills(DEVOPS_AGENT_DIR, meta)
        system_prompt = compose_system_prompt(prompts, skills=skills)

        for tool_name in OPENSPACE_TOOL_NAMES:
            assert tool_name not in system_prompt, (
                f"DevOps system prompt (empty innate_skills) should not contain {tool_name!r}. "
                f"This indicates openspace content is being injected when it shouldn't be."
            )


# =============================================================================
# 4. Tool Category Independence (Sanity)
# =============================================================================


class TestInnateSkillToolCategories:
    """Verify OpenSpace is NOT in INNATE_SKILL_TOOL_CATEGORIES.

    OpenSpace is instructional-only: the skill prompt is loaded by
    load_agent_skills(), but the 4 OpenSpace tools must be granted explicitly
    in the agent's tools.allow. This is a deliberate design decision (per
    the skill's own Agent Configuration Note section).
    """

    def test_openspace_not_in_innate_skill_tool_categories(self) -> None:
        """INNATE_SKILL_TOOL_CATEGORIES must NOT contain 'openspace'."""
        from daemon.tools.instance import INNATE_SKILL_TOOL_CATEGORIES

        assert "openspace" not in INNATE_SKILL_TOOL_CATEGORIES, (
            f"INNATE_SKILL_TOOL_CATEGORIES should not auto-grant openspace tools. "
            f"Current map: {INNATE_SKILL_TOOL_CATEGORIES}"
        )

    def test_expand_allow_for_innate_skills_openspace_is_noop(self) -> None:
        """expand_allow_for_innate_skills with openspace in innate_skills should
        leave the allow list unchanged (since openspace has no tool categories)."""
        from daemon.tools.instance import expand_allow_for_innate_skills

        allow = ["bash", "filesystem", "mcp_openspace_search_skills"]
        result = expand_allow_for_innate_skills(allow, ["openspace"])

        assert result == allow, (
            f"expand_allow_for_innate_skills should not modify allow list when "
            f"openspace is in innate_skills (no categories mapped). "
            f"Got: {result}, expected: {allow}"
        )

    def test_innate_skill_loader_does_not_consult_tool_categories(self) -> None:
        """The skill loader must not require an entry in INNATE_SKILL_TOOL_CATEGORIES.

        The two systems (skill prompt loading vs tool category expansion) are
        decoupled. An innate skill with no tool category entry should still
        have its prompt loaded.
        """
        from daemon.loader import load_agent_skills
        from daemon.tools.instance import INNATE_SKILL_TOOL_CATEGORIES

        # Precondition: openspace is not in tool categories map
        assert "openspace" not in INNATE_SKILL_TOOL_CATEGORIES

        # Despite the absence in the categories map, the skill prompt loads fine
        skills = load_agent_skills(DEVOPS_AGENT_DIR, {"innate_skills": ["openspace"]})
        assert "openspace" in skills, (
            "load_agent_skills should not depend on INNATE_SKILL_TOOL_CATEGORIES. "
            "Even without a categories entry, the openspace skill prompt should load."
        )
        assert len(skills["openspace"]) > 0
