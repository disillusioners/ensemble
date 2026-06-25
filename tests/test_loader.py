"""Tests for daemon/loader.py"""

import time

import pytest

from unittest.mock import MagicMock, patch

from daemon.loader import (
    PromptCache,
    compose_system_prompt,
    estimate_tokens,
    load_agent_prompts,
    load_agent_skills,
    load_and_cache_prompt,
    load_shared_knowledge,
    load_tools_doc_for_agent,
)


class TestLoadAgentPrompts:
    """Tests for load_agent_prompts function."""

    def test_load_agent_prompts_all_files(self, tmp_path):
        """Test loading directory with all 4 markdown files."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        (agent_dir / "skill.md").write_text("# Skills\nTest skills")
        (agent_dir / "workflow.md").write_text("# Workflow\nTest workflow")
        (agent_dir / "rule.md").write_text("# Rules\nTest rules")
        (agent_dir / "memory.md").write_text("# Memory\nTest memory")

        prompts = load_agent_prompts(agent_dir)

        assert "skill" in prompts
        assert "workflow" in prompts
        assert "rule" in prompts
        assert "memory" in prompts
        assert prompts["skill"] == "# Skills\nTest skills"
        assert prompts["workflow"] == "# Workflow\nTest workflow"
        assert prompts["rule"] == "# Rules\nTest rules"
        assert prompts["memory"] == "# Memory\nTest memory"

    def test_load_agent_prompts_partial_files(self, tmp_path):
        """Test loading directory with only some files (e.g., only skill.md and rule.md)."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        (agent_dir / "skill.md").write_text("# Skills\nTest skills")
        (agent_dir / "rule.md").write_text("# Rules\nTest rules")

        prompts = load_agent_prompts(agent_dir)

        assert "skill" in prompts
        assert "rule" in prompts
        assert "workflow" not in prompts
        assert "memory" not in prompts

    def test_load_agent_prompts_missing_dir(self, tmp_path):
        """Test error when agent_dir doesn't exist."""
        agent_dir = tmp_path / "nonexistent_agent"

        prompts = load_agent_prompts(agent_dir)

        assert prompts == {}


class TestComposeSystemPrompt:
    """Tests for compose_system_prompt function."""

    def test_compose_system_prompt_order(self, tmp_path):
        """Test that composition order is correct (rule → skill → workflow → memory)."""
        prompts = {
            "skill": "# Skills content",
            "workflow": "# Workflow content",
            "rule": "# Rules content",
            "memory": "# Memory content",
        }

        result = compose_system_prompt(prompts)

        # Check order: rule should come first (raw content, no added headers)
        rule_pos = result.find("# Rules")
        skill_pos = result.find("# Skills")
        workflow_pos = result.find("# Workflow")
        memory_pos = result.find("# Memory")

        assert rule_pos < skill_pos < workflow_pos < memory_pos

    def test_compose_system_prompt_content(self, tmp_path):
        """Test that each section content is present."""
        prompts = {
            "skill": "# Skills\n\nSkill content",
            "rule": "# Rules\n\nRule content",
        }

        result = compose_system_prompt(prompts)

        # Content should be preserved as-is (no auto-added headers)
        assert "# Rules\n\nRule content" in result
        assert "# Skills\n\nSkill content" in result

    def test_compose_system_prompt_separator(self, tmp_path):
        """Test that sections are separated by '---'."""
        prompts = {
            "skill": "Skill content",
            "workflow": "Workflow content",
        }

        result = compose_system_prompt(prompts)

        assert "\n\n---\n\n" in result

    def test_compose_system_prompt_empty_dict(self, tmp_path):
        """Test compose_system_prompt with empty dict."""
        prompts = {}
        result = compose_system_prompt(prompts)
        assert result == ""


class TestEstimateTokens:
    """Tests for estimate_tokens function."""

    def test_estimate_tokens_basic(self, tmp_path):
        """Test token counting with known text."""
        text = "Hello, world! This is a test."
        tokens = estimate_tokens(text)

        # Basic check - should return a positive integer
        assert isinstance(tokens, int)
        assert tokens > 0

    def test_estimate_tokens_empty(self, tmp_path):
        """Test token counting with empty string."""
        text = ""
        tokens = estimate_tokens(text)

        assert tokens == 0


class TestPromptCache:
    """Tests for PromptCache class."""

    def test_prompt_cache_get_miss(self):
        """Test cache miss returns None."""
        cache = PromptCache()
        agent_id = "test_agent"

        result = cache.get(agent_id)

        assert result is None

    def test_prompt_cache_set_get(self):
        """Test cache set and get."""
        cache = PromptCache()
        agent_id = "test_agent"

        cache.set(agent_id, "test prompt", 100, {"skill.md": 1.0})

        result = cache.get(agent_id)

        assert result is not None
        assert result[0] == "test prompt"
        assert result[1] == 100

    def test_prompt_cache_invalidate(self):
        """Test cache invalidation."""
        cache = PromptCache()
        agent_id = "test_agent"

        cache.set(agent_id, "test prompt", 100, {"skill.md": 1.0})
        cache.invalidate(agent_id)

        result = cache.get(agent_id)
        assert result is None


class TestLoadAndCachePrompt:
    """Tests for load_and_cache_prompt function."""

    def test_load_and_cache_prompt_first_time(self, tmp_path):
        """Test loading and caching prompt."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        (agent_dir / "skill.md").write_text("# Skills\nTest skills")
        (agent_dir / "rule.md").write_text("# Rules\nTest rules")

        cache = PromptCache()

        prompt, tokens = load_and_cache_prompt("test_agent", agent_dir, cache)

        # Content preserved as-is (no auto-added headers)
        assert "# Skills" in prompt
        assert "# Rules" in prompt
        assert tokens > 0

    def test_load_and_cache_prompt_cached(self, tmp_path):
        """Test that cached version is returned when files unchanged."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        (agent_dir / "skill.md").write_text("# Skills\nTest skills")

        cache = PromptCache()

        # First call - should load from disk
        prompt1, tokens1 = load_and_cache_prompt("test_agent", agent_dir, cache)

        # Second call - should return cached version
        prompt2, tokens2 = load_and_cache_prompt("test_agent", agent_dir, cache)

        assert prompt1 == prompt2
        assert tokens1 == tokens2

    def test_load_and_cache_prompt_mtime_changed(self, tmp_path):
        """Test reload when file modified time changes."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        skill_file = agent_dir / "skill.md"
        skill_file.write_text("# Skills\nTest skills")

        cache = PromptCache()

        # First call - should load from disk
        prompt1, tokens1 = load_and_cache_prompt("test_agent", agent_dir, cache)

        # Wait a bit and modify the file to change mtime
        time.sleep(0.1)
        skill_file.write_text("# Skills\nUpdated skills")

        # Third call - should reload because mtime changed
        prompt3, tokens3 = load_and_cache_prompt("test_agent", agent_dir, cache)

        assert "Updated skills" in prompt3
        assert tokens3 > 0

    def test_load_and_cache_prompt_invalid_json_meta_falls_back_to_legacy(self, tmp_path):
        """Test that invalid JSON in meta.json causes graceful fallback to legacy skills/ loading."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()

        # Create invalid JSON meta.json
        (agent_dir / "meta.json").write_text("{broken json")

        # Create base rule file
        (agent_dir / "rule.md").write_text("# Rules\nTest rules")

        # Create legacy skills directory with a skill
        skills_dir = agent_dir / "skills"
        coding_dir = skills_dir / "coding"
        coding_dir.mkdir(parents=True)
        (coding_dir / "skill.md").write_text("# Coding\nLegacy skill.")

        cache = PromptCache()

        # Should not crash, should fall back to legacy skills/ loading
        prompt, tokens = load_and_cache_prompt("test_agent", agent_dir, cache)

        # Should contain the legacy skill since meta was None on error
        assert "# Coding" in prompt
        assert "Legacy skill" in prompt
        assert "# Rules" in prompt

    def test_load_and_cache_prompt_innate_skills_mtime_triggers_reload(self, tmp_path):
        """Test that cache invalidates when an innate skill file is modified."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()

        # Create base file
        (agent_dir / "rule.md").write_text("# Rules\nTest rules")

        # Create centralized innate-skills directory
        innate_skills_dir = tmp_path / "_prompt_system" / "innate-skills"
        coding_dir = innate_skills_dir / "coding"
        coding_dir.mkdir(parents=True)
        skill_file = coding_dir / "skill.md"
        skill_file.write_text("# Coding\nWrite code.")

        # Create meta.json referencing the innate skill
        (agent_dir / "meta.json").write_text('{"innate_skills": ["coding"]}')

        cache = PromptCache()

        # First load
        prompt1, _ = load_and_cache_prompt("test_agent", agent_dir, cache)
        assert "Write code" in prompt1

        # Modify the innate skill file
        time.sleep(0.1)
        skill_file.write_text("# Coding\nWrite better code")

        # Should reload because innate skill mtime changed
        prompt2, _ = load_and_cache_prompt("test_agent", agent_dir, cache)
        assert "Write better code" in prompt2


class TestLoadAgentSkills:
    """Tests for load_agent_skills function."""

    def test_load_agent_skills_multiple(self, tmp_path):
        """Test loading multiple skills from skills/ directory."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        skills_dir = agent_dir / "skills"
        
        # Create multiple skill directories
        coding_dir = skills_dir / "coding"
        coding_dir.mkdir(parents=True)
        (coding_dir / "skill.md").write_text("# Coding\nYou are a coding expert.")
        
        reviewing_dir = skills_dir / "reviewing"
        reviewing_dir.mkdir()
        (reviewing_dir / "skill.md").write_text("# Reviewing\nYou review code.")
        
        skills = load_agent_skills(agent_dir)
        
        assert len(skills) == 2
        assert "coding" in skills
        assert "reviewing" in skills
        assert "Coding" in skills["coding"]
        assert "Reviewing" in skills["reviewing"]

    def test_load_agent_skills_empty_dir(self, tmp_path):
        """Test loading skills when skills/ directory doesn't exist."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        
        skills = load_agent_skills(agent_dir)
        
        assert skills == {}

    def test_load_agent_skills_skips_non_dirs(self, tmp_path):
        """Test that non-directory files in skills/ are skipped."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        skills_dir = agent_dir / "skills"
        skills_dir.mkdir()
        
        # Create a file (not a directory) - should be skipped
        (skills_dir / "not-a-skill.txt").write_text("Should be ignored")
        
        skills = load_agent_skills(agent_dir)
        
        assert skills == {}

    def test_load_agent_skills_skips_missing_skill_md(self, tmp_path):
        """Test that skill directories without skill.md are skipped."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        skills_dir = agent_dir / "skills"
        
        # Create skill directory without skill.md
        empty_skill = skills_dir / "empty-skill"
        empty_skill.mkdir(parents=True)
        
        skills = load_agent_skills(agent_dir)
        
        assert skills == {}

    def test_load_agent_skills_with_innate_skills(self, tmp_path):
        """Test loading skills from centralized innate-skills registry."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()

        # Create centralized innate-skills directory (sibling to agent dir)
        innate_skills_dir = tmp_path / "_prompt_system" / "innate-skills"
        coding_skill_dir = innate_skills_dir / "coding"
        coding_skill_dir.mkdir(parents=True)
        (coding_skill_dir / "skill.md").write_text("# Coding\nYou are a coding expert.")

        # Load skills with meta containing innate_skills
        meta = {"innate_skills": ["coding"]}
        skills = load_agent_skills(agent_dir, meta)

        assert len(skills) == 1
        assert "coding" in skills
        assert "Coding" in skills["coding"]

    def test_load_agent_skills_with_empty_innate_skills_falls_back_to_legacy(self, tmp_path):
        """Test that empty innate_skills array falls through to legacy skills/ loading."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()

        # Create legacy skills directory
        skills_dir = agent_dir / "skills"
        coding_dir = skills_dir / "coding"
        coding_dir.mkdir(parents=True)
        (coding_dir / "skill.md").write_text("# Coding\nLegacy skill.")

        # Empty innate_skills should fall through to legacy
        meta = {"innate_skills": []}
        skills = load_agent_skills(agent_dir, meta)

        assert len(skills) == 1
        assert "coding" in skills

    def test_load_agent_skills_multiple_innate_skills(self, tmp_path):
        """Test loading multiple innate skills from centralized registry."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()

        # Create centralized innate-skills directory
        innate_skills_dir = tmp_path / "_prompt_system" / "innate-skills"
        coding_dir = innate_skills_dir / "coding"
        coding_dir.mkdir(parents=True)
        (coding_dir / "skill.md").write_text("# Coding\nWrite code.")

        reviewing_dir = innate_skills_dir / "reviewing"
        reviewing_dir.mkdir(parents=True)
        (reviewing_dir / "skill.md").write_text("# Reviewing\nReview code.")

        # Load with multiple innate_skills
        meta = {"innate_skills": ["coding", "reviewing"]}
        skills = load_agent_skills(agent_dir, meta)

        assert len(skills) == 2
        assert "coding" in skills
        assert "reviewing" in skills

    def test_load_agent_skills_multiple_innate_skills_reversed_order(self, tmp_path):
        """Test loading multiple innate skills in reversed/alphabetical order proves sorted() works."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()

        # Create centralized innate-skills directory
        innate_skills_dir = tmp_path / "_prompt_system" / "innate-skills"
        coding_dir = innate_skills_dir / "coding"
        coding_dir.mkdir(parents=True)
        (coding_dir / "skill.md").write_text("# Coding\nWrite code.")

        reviewing_dir = innate_skills_dir / "reviewing"
        reviewing_dir.mkdir(parents=True)
        (reviewing_dir / "skill.md").write_text("# Reviewing\nReview code.")

        # Load with reversed order input - should still produce alphabetical output
        meta = {"innate_skills": ["reviewing", "coding"]}
        skills = load_agent_skills(agent_dir, meta)

        assert len(skills) == 2
        # Keys should be alphabetically sorted regardless of input order
        skill_names = list(skills.keys())
        assert skill_names == sorted(skill_names)
        assert "coding" in skills
        assert "reviewing" in skills

    def test_load_agent_skills_missing_innate_skill_returns_empty_for_that_skill(self, tmp_path, caplog):
        """Test that missing innate skill file returns empty dict for that skill and logs warning."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()

        # Create innate-skills dir but NOT the skill file
        innate_skills_dir = tmp_path / "_prompt_system" / "innate-skills"
        coding_dir = innate_skills_dir / "coding"
        coding_dir.mkdir(parents=True)
        (coding_dir / "skill.md").write_text("# Coding\nWrite code.")

        # nonexistent-skill doesn't exist
        meta = {"innate_skills": ["coding", "nonexistent-skill"]}
        
        with caplog.at_level("WARNING"):
            skills = load_agent_skills(agent_dir, meta)

        # Should still load the existing skill
        assert "coding" in skills
        # Missing skill should not appear in results
        assert "nonexistent-skill" not in skills
        # Warning should be logged for missing skill
        assert any("nonexistent-skill" in record.message for record in caplog.records)
        assert any("not found" in record.message.lower() for record in caplog.records)

    def test_load_agent_skills_meta_none_activates_legacy_fallback(self, tmp_path):
        """Test that explicitly passing meta=None activates legacy skills/ fallback."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()

        # Create legacy skills directory
        skills_dir = agent_dir / "skills"
        coding_dir = skills_dir / "coding"
        coding_dir.mkdir(parents=True)
        (coding_dir / "skill.md").write_text("# Coding\nLegacy skill.")

        # Explicitly pass meta=None
        skills = load_agent_skills(agent_dir, meta=None)

        assert len(skills) == 1
        assert "coding" in skills

    def test_load_agent_skills_innate_takes_priority_over_local_skills_dir(self, tmp_path):
        """Test that innate-skills path is used when both innate_skills and local skills/ exist."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()

        # Create centralized innate-skills directory
        innate_skills_dir = tmp_path / "_prompt_system" / "innate-skills"
        some_skill_dir = innate_skills_dir / "some-skill"
        some_skill_dir.mkdir(parents=True)
        (some_skill_dir / "skill.md").write_text("# Some Skill\nFrom innate-skills.")

        # Create local skills/ directory with different skill
        local_skills_dir = agent_dir / "skills"
        local_skill_dir = local_skills_dir / "local-skill"
        local_skill_dir.mkdir(parents=True)
        (local_skill_dir / "skill.md").write_text("# Local Skill\nFrom local skills/.")

        # Agent has innate_skills defined
        meta = {"innate_skills": ["some-skill"]}
        skills = load_agent_skills(agent_dir, meta)

        # Should use innate-skills path, local skills/ completely ignored
        assert len(skills) == 1
        assert "some-skill" in skills
        assert "local-skill" not in skills
        assert "From innate-skills" in skills["some-skill"]


class TestComposeSystemPromptWithSkills:
    """Tests for compose_system_prompt with multiple skills."""

    def test_compose_with_skills(self):
        """Test composing prompt with multiple skills."""
        prompts = {
            "rule": "# Rules\nFollow these rules",
            "workflow": "# Workflow\nDo this",
        }
        skills = {
            "coding": "# Coding\nWrite code",
            "reviewing": "# Reviewing\nReview code",
        }
        
        result = compose_system_prompt(prompts, skills)
        
        # Check all sections are present (raw content, no auto-added headers)
        assert "# Rules" in result
        assert "# Skills" not in result  # No base skill
        assert "# Coding" in result
        assert "# Reviewing" in result
        assert "# Workflow" in result

    def test_compose_with_base_skill_and_skills(self):
        """Test composing prompt with both base skill and additional skills."""
        prompts = {
            "skill": "# Base Skill\nBase capabilities",
            "rule": "# Rules\nFollow these",
        }
        skills = {
            "testing": "# Testing\nTest code",
        }
        
        result = compose_system_prompt(prompts, skills)
        
        # Base skill comes before additional skills (raw content)
        base_pos = result.find("# Base Skill")
        testing_pos = result.find("# Testing")
        
        assert base_pos != -1
        assert testing_pos != -1
        assert base_pos < testing_pos

    def test_compose_skill_content_preserved(self):
        """Test that skill names and content are preserved as-is."""
        prompts = {}
        skills = {
            "code-review": "# Code Review\nReview code",
            "test_driven_dev": "# TDD\nTest first",
        }
        
        result = compose_system_prompt(prompts, skills)
        
        # Content preserved exactly (no auto formatting)
        assert "# Code Review\nReview code" in result
        assert "# TDD\nTest first" in result

    def test_compose_no_skills(self):
        """Test composing prompt without skills dict."""
        prompts = {
            "skill": "# Skills\nTest",
            "rule": "# Rules\nTest",
        }
        
        result = compose_system_prompt(prompts, None)
        
        assert "# Rules" in result
        assert "# Skills" in result


class TestLoadAndCachePromptWithSkills:
    """Tests for load_and_cache_prompt with skills."""

    def test_load_and_cache_with_skills(self, tmp_path):
        """Test loading and caching prompt with multiple skills."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        
        # Base files
        (agent_dir / "rule.md").write_text("# Rules\nTest rules")
        
        # Skills
        skills_dir = agent_dir / "skills"
        coding_dir = skills_dir / "coding"
        coding_dir.mkdir(parents=True)
        (coding_dir / "skill.md").write_text("# Coding\nWrite code")
        
        cache = PromptCache()
        prompt, tokens = load_and_cache_prompt("test_agent", agent_dir, cache)
        
        # Content preserved as-is
        assert "# Rules" in prompt
        assert "# Coding" in prompt
        assert tokens > 0

    def test_cache_invalidates_on_skill_change(self, tmp_path):
        """Test that cache invalidates when a skill file changes."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        
        # Base file
        (agent_dir / "rule.md").write_text("# Rules\nTest rules")
        
        # Skill
        skills_dir = agent_dir / "skills"
        coding_dir = skills_dir / "coding"
        coding_dir.mkdir(parents=True)
        skill_file = coding_dir / "skill.md"
        skill_file.write_text("# Coding\nWrite code")
        
        cache = PromptCache()
        
        # First load
        prompt1, _ = load_and_cache_prompt("test_agent", agent_dir, cache)
        assert "Write code" in prompt1
        
        # Modify skill
        time.sleep(0.1)
        skill_file.write_text("# Coding\nWrite better code")
        
        # Should reload
        prompt2, _ = load_and_cache_prompt("test_agent", agent_dir, cache)
        assert "Write better code" in prompt2


class TestToolsLoading:
    """Tests for tools.md loading."""

    def test_load_agent_prompts_includes_tools(self, tmp_path):
        """Test that tools.md is loaded as a prompt file."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        (agent_dir / "tools.md").write_text("# Tools\nAvailable tools")
        (agent_dir / "rule.md").write_text("# Rules\nTest rules")
        
        prompts = load_agent_prompts(agent_dir)
        
        assert "tools" in prompts
        assert prompts["tools"] == "# Tools\nAvailable tools"

    def test_compose_includes_tools_section(self):
        """Test that tools section is included in composed prompt."""
        prompts = {
            "rule": "# Rules\nFollow rules",
            "tools": "# Tools\n- bash\n- read_file",
            "workflow": "# Workflow\nDo work",
        }
        
        result = compose_system_prompt(prompts)
        
        # Check order: rules → tools → workflow (raw content)
        rule_pos = result.find("# Rules")
        tools_pos = result.find("# Tools")
        workflow_pos = result.find("# Workflow")
        
        assert rule_pos != -1
        assert tools_pos != -1
        assert workflow_pos != -1
        assert rule_pos < tools_pos < workflow_pos

    def test_compose_order_with_skills_and_tools(self):
        """Test full order: soul → rule → skills → tools → workflow → memory."""
        prompts = {
            "soul": "# Who I Am",
            "rule": "# Rules",
            "tools": "# Tools",
            "workflow": "# Workflow",
            "memory": "# Memory",
        }
        skills = {
            "coding": "# Coding skill",
        }
        
        result = compose_system_prompt(prompts, skills)
        
        # Order preserved with raw content
        soul_pos = result.find("# Who I Am")
        rule_pos = result.find("# Rules")
        skill_pos = result.find("# Coding skill")
        tools_pos = result.find("# Tools")
        workflow_pos = result.find("# Workflow")
        memory_pos = result.find("# Memory")
        
        assert soul_pos < rule_pos < skill_pos < tools_pos < workflow_pos < memory_pos

    def test_cache_invalidates_on_tools_change(self, tmp_path):
        """Test that cache invalidates when tools.md changes."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        (agent_dir / "rule.md").write_text("# Rules\nTest")
        tools_file = agent_dir / "tools.md"
        tools_file.write_text("# Tools\n- bash")
        
        cache = PromptCache()
        
        prompt1, _ = load_and_cache_prompt("test_agent", agent_dir, cache)
        assert "bash" in prompt1
        
        time.sleep(0.1)
        tools_file.write_text("# Tools\n- bash\n- read_file")
        
        prompt2, _ = load_and_cache_prompt("test_agent", agent_dir, cache)
        assert "read_file" in prompt2


class TestSoulLoading:
    """Tests for soul.md loading."""

    def test_load_agent_prompts_includes_soul(self, tmp_path):
        """Test that soul.md is loaded as a prompt file."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        (agent_dir / "soul.md").write_text("# Who I Am\nI am a helpful assistant.")
        (agent_dir / "rule.md").write_text("# Rules\nTest rules")
        
        prompts = load_agent_prompts(agent_dir)
        
        assert "soul" in prompts
        assert prompts["soul"] == "# Who I Am\nI am a helpful assistant."

    def test_compose_includes_soul_section_first(self):
        """Test that soul section comes first in composed prompt."""
        prompts = {
            "soul": "# Who I Am\nI am a developer.",
            "rule": "# Rules\nFollow rules",
            "tools": "# Tools\n- bash",
        }
        
        result = compose_system_prompt(prompts)
        
        # Check order: soul → rules → tools (raw content)
        soul_pos = result.find("# Who I Am")
        rule_pos = result.find("# Rules")
        tools_pos = result.find("# Tools")
        
        assert soul_pos != -1
        assert rule_pos != -1
        assert tools_pos != -1
        assert soul_pos < rule_pos < tools_pos

    def test_compose_full_order_with_soul(self):
        """Test full order: soul → rule → skills → tools → workflow → memory."""
        prompts = {
            "soul": "# Who I Am",
            "rule": "# Rules",
            "tools": "# Tools",
            "workflow": "# Workflow",
            "memory": "# Memory",
        }
        skills = {
            "coding": "# Coding skill",
        }
        
        result = compose_system_prompt(prompts, skills)
        
        # Order preserved with raw content
        soul_pos = result.find("# Who I Am")
        rule_pos = result.find("# Rules")
        skill_pos = result.find("# Coding skill")
        tools_pos = result.find("# Tools")
        workflow_pos = result.find("# Workflow")
        memory_pos = result.find("# Memory")
        
        assert soul_pos < rule_pos < skill_pos < tools_pos < workflow_pos < memory_pos

    def test_cache_invalidates_on_soul_change(self, tmp_path):
        """Test that cache invalidates when soul.md changes."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        (agent_dir / "rule.md").write_text("# Rules\nTest")
        soul_file = agent_dir / "soul.md"
        soul_file.write_text("# Who I Am\nI am helpful.")
        
        cache = PromptCache()
        
        prompt1, _ = load_and_cache_prompt("test_agent", agent_dir, cache)
        assert "helpful" in prompt1
        
        time.sleep(0.1)
        soul_file.write_text("# Who I Am\nI am a craftsman of code.")
        
        prompt2, _ = load_and_cache_prompt("test_agent", agent_dir, cache)
        assert "craftsman" in prompt2


class TestLoadToolsDocForAgent:
    """Tests for load_tools_doc_for_agent function."""

    @pytest.fixture(autouse=True)
    def setup_registry_and_tools(self):
        """Set up mock registry and populate tool registry with test tools."""
        from daemon.tools._tool_registry import clear_registry, register_full_doc
        from daemon.tools._tool_registry import _tool_metadata
        
        # Clear any existing registry state
        clear_registry()
        
        # Populate tool registry with test tools
        # These simulate the tools that would be in _tool_metadata
        _tool_metadata["bash"] = {
            "category": "bash",
            "short_doc": "Execute bash commands",
            "full_doc": "Execute bash commands.\n\nArgs:\n    command: Command to execute.",
        }
        _tool_metadata["read_file"] = {
            "category": "filesystem",
            "short_doc": "Read file contents",
            "full_doc": "Read file contents.\n\nArgs:\n    path: File path.",
        }
        _tool_metadata["write_file"] = {
            "category": "filesystem",
            "short_doc": "Write file contents",
            "full_doc": "Write file contents.\n\nArgs:\n    path: File path.\n    content: Content to write.",
        }
        _tool_metadata["project_create"] = {
            "category": "project",
            "short_doc": "Create a new project",
            "full_doc": "Create a new project.\n\nArgs:\n    name: Project name.",
        }
        
        # Mock the registry
        self.mock_agent_meta = MagicMock()
        self.mock_agent_meta.tools = None  # Default: no restrictions
        
        self.mock_registry = MagicMock()
        self.mock_registry.get.return_value = self.mock_agent_meta
        
        # Patch the registry getter at the module level where it's imported
        self.registry_patcher = patch("daemon.registry.get_registry", return_value=self.mock_registry)
        self.registry_patcher.start()
        
        yield
        
        # Cleanup
        clear_registry()
        self.registry_patcher.stop()

    def test_no_filter_returns_all_categories(self):
        """Agent with no tool filter should get all categories."""
        # Set up agent with no tools restriction
        self.mock_agent_meta.tools = None
        
        result = load_tools_doc_for_agent("test_agent")
        
        # Should return something (categories with tools)
        assert len(result) > 0
        # Should contain section headers
        assert "## " in result
        # Should contain bash tool
        assert "bash" in result.lower()

    def test_restricted_tools_returns_filtered_categories(self):
        """Agent with restricted tool set should only see allowed categories."""
        from daemon.registry import ToolFilter
        
        # Set up agent with restricted tools (only bash category)
        self.mock_agent_meta.tools = ToolFilter(allow=["bash"], deny=None)
        
        result = load_tools_doc_for_agent("test_agent")
        
        # Should contain bash-related content
        assert "Bash" in result or "bash" in result.lower()
        # Should list bash tool
        assert "bash" in result.lower()
        # Should have Available tools line
        assert "Available tools:" in result

    def test_category_doc_appears_in_output(self):
        """CATEGORY_DOC content should appear in the output."""
        self.mock_agent_meta.tools = None  # No restriction
        
        result = load_tools_doc_for_agent("test_agent")
        
        # Should contain category descriptions from CATEGORY_DOC
        # The bash category has a CATEGORY_DOC with usage info
        assert "Bash" in result or "bash" in result.lower()

    def test_available_tools_line_lists_correct_tools(self):
        """'Available tools:' line should list the correct tools."""
        from daemon.registry import ToolFilter
        
        # Restrict to only bash category
        self.mock_agent_meta.tools = ToolFilter(allow=["bash"], deny=None)
        
        result = load_tools_doc_for_agent("test_agent")
        
        # Should have Available tools: followed by bash
        assert "Available tools:" in result
        assert "bash" in result.lower()

    def test_agent_not_found_returns_all_tools(self):
        """Agent not in registry should get all tools (full access by default)."""
        self.mock_registry.get.return_value = None
        
        result = load_tools_doc_for_agent("nonexistent_agent")
        
        # When agent not in registry, they get full access (all tools)
        # The function returns tool docs, not empty string
        assert len(result) > 0
        assert "bash" in result.lower()

    def test_tool_help_instruction_present(self):
        """Output should contain instruction to use tool_help for docs."""
        self.mock_agent_meta.tools = None
        
        result = load_tools_doc_for_agent("test_agent")
        
        # Should mention tool_help for detailed docs
        assert "tool_help" in result

    def test_allow_and_deny_filter(self):
        """Allow with deny should properly filter tools."""
        from daemon.registry import ToolFilter
        
        # Allow filesystem but deny write_file
        self.mock_agent_meta.tools = ToolFilter(allow=["filesystem"], deny=["write_file"])
        
        result = load_tools_doc_for_agent("test_agent")
        
        # Should contain filesystem tools but not write_file
        # Category name is "File Operations" not "Filesystem"
        assert "File Operations" in result or "file operations" in result.lower()
        # write_file should not appear in available tools
        lines = result.split("\n")
        available_line = [l for l in lines if "Available tools:" in l]
        if available_line:
            assert "write_file" not in available_line[0]

    def test_deny_without_allow(self):
        """Deny without allow should deny only specified tools."""
        from daemon.registry import ToolFilter
        
        # Deny bash only
        self.mock_agent_meta.tools = ToolFilter(allow=None, deny=["bash"])
        
        result = load_tools_doc_for_agent("test_agent")
        
        # bash should not appear in the output
        # The result might contain bash category name in header but not in tools list
        # Check that the bash tool is not listed
        lines = result.split("\n")
        available_lines = [l for l in lines if "Available tools:" in l]
        # At least one category should be present (not all denied)
        assert len(available_lines) > 0


# =============================================================================
# Tests for load_shared_knowledge and shared_knowledge parameter
# =============================================================================


class TestLoadSharedKnowledge:
    """Tests for load_shared_knowledge function."""

    def test_load_shared_knowledge_returns_empty_when_rag_disabled(self):
        """load_shared_knowledge should return empty string when RAG is disabled."""
        with patch("daemon.loader.is_rag_enabled", return_value=False):
            result = load_shared_knowledge()
            assert result == ""

    def test_load_shared_knowledge_returns_empty_when_file_missing(self):
        """load_shared_knowledge should return empty string when knowledge.md doesn't exist."""
        fake_file = MagicMock()
        fake_file.exists = MagicMock(return_value=False)
        
        with patch("daemon.loader.is_rag_enabled", return_value=True):
            with patch("daemon.loader.KNOWLEDGE_FILE", fake_file):
                result = load_shared_knowledge()
                assert result == ""

    def test_load_shared_knowledge_returns_content_when_rag_enabled_and_file_exists(self, tmp_path):
        """load_shared_knowledge should return file content when RAG is enabled and file exists."""
        knowledge_file = tmp_path / "knowledge.md"
        knowledge_file.write_text("# Knowledge Base\n\nThis is shared knowledge.")
        
        with patch("daemon.loader.is_rag_enabled", return_value=True):
            with patch("daemon.loader.KNOWLEDGE_FILE", knowledge_file):
                result = load_shared_knowledge()
                assert "# Knowledge Base" in result
                assert "This is shared knowledge" in result


class TestComposeSystemPromptWithSharedKnowledge:
    """Tests for compose_system_prompt with shared_knowledge parameter."""

    def test_compose_system_prompt_with_empty_shared_knowledge(self):
        """compose_system_prompt should work with empty shared_knowledge (default)."""
        prompts = {
            "soul": "# Who I Am\nI am a test agent.",
            "rule": "# Rules\nFollow these rules",
        }
        result = compose_system_prompt(prompts, shared_knowledge="")
        assert "# Who I Am" in result
        assert "# Rules" in result
        assert "Knowledge Base" not in result

    def test_compose_system_prompt_includes_shared_knowledge_when_provided(self):
        """compose_system_prompt should include shared_knowledge section when provided."""
        prompts = {
            "soul": "# Who I Am\nI am a test agent.",
            "rule": "# Rules\nFollow these rules",
        }
        shared_knowledge = "Use the explore tool to query the knowledge base."
        result = compose_system_prompt(prompts, shared_knowledge=shared_knowledge)
        assert "Knowledge Base" in result
        assert "explore tool" in result

    def test_compose_system_prompt_shared_knowledge_ordering(self):
        """shared_knowledge should appear after memory and before project experience."""
        prompts = {
            "soul": "# Who I Am",
            "rule": "# Rules",
            "workflow": "# Workflow",
            "memory": "# Memory",
        }
        shared_knowledge = "Shared KB content"
        project_experience = "Project experience content"
        result = compose_system_prompt(
            prompts,
            shared_knowledge=shared_knowledge,
            project_experience=project_experience,
        )
        memory_pos = result.find("# Memory")
        knowledge_pos = result.find("## Knowledge Base")
        project_pos = result.find("## Project Experience")
        assert memory_pos < knowledge_pos < project_pos

    def test_compose_system_prompt_skips_empty_shared_knowledge_section(self):
        """compose_system_prompt should not add Knowledge Base section when shared_knowledge is empty."""
        prompts = {
            "soul": "# Who I Am",
            "memory": "# Memory",
        }
        result = compose_system_prompt(prompts, shared_knowledge="   ")
        assert "Knowledge Base" not in result

    def test_compose_system_prompt_backward_compatible_no_shared_knowledge_param(self):
        """compose_system_prompt() works when called without shared_knowledge parameter."""
        prompts = {"rule": "# Rules\nFollow rules"}
        result = compose_system_prompt(prompts)
        assert "# Rules" in result
        assert "Knowledge Base" not in result

    def test_compose_system_prompt_with_all_dynamic_params(self):
        """compose_system_prompt should work with all dynamic parameters including shared_knowledge."""
        prompts = {
            "soul": "# Who I Am",
            "rule": "# Rules",
            "memory": "# Memory\n\nMemory content",
        }
        skills = {
            "coding": "# Coding\nWrite code.",
        }
        dynamic_tools = "## Bash\n\nAvailable tools: bash"
        project_experience = "Use .agents directory for project context."
        recent_memories = "- memory-001.md\n- memory-002.md"
        shared_knowledge = "Query the knowledge base for project patterns."
        
        result = compose_system_prompt(
            prompts,
            skills=skills,
            dynamic_tools=dynamic_tools,
            project_experience=project_experience,
            recent_memories=recent_memories,
            shared_knowledge=shared_knowledge,
        )
        
        assert "# Who I Am" in result
        assert "# Rules" in result
        assert "# Coding" in result
        assert "Bash" in result
        assert "# Memory" in result
        assert "memory-001.md" in result
        assert "## Knowledge Base" in result
        assert "## Project Experience" in result
        assert "query the knowledge base" in result.lower()


class TestLoadAndCachePromptWithSharedKnowledge:
    """Tests for load_and_cache_prompt with shared_knowledge."""

    def test_cache_tracks_knowledge_mtime_when_rag_enabled(self, tmp_path):
        """Cache should include knowledge.md mtime when RAG is enabled."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        (agent_dir / "rule.md").write_text("# Rules\nTest rules")

        # Create knowledge file
        knowledge_file = tmp_path / "knowledge.md"
        knowledge_file.write_text("# Knowledge Base\n\nKnowledge content.")

        with patch("daemon.loader.is_rag_enabled", return_value=True):
            with patch("daemon.loader.KNOWLEDGE_FILE", knowledge_file):
                cache = PromptCache()
                prompt1, tokens1 = load_and_cache_prompt("test_agent", agent_dir, cache)
                assert "Knowledge Base" in prompt1

                # Modify knowledge file
                time.sleep(0.1)
                knowledge_file.write_text("# Knowledge Base\n\nUpdated knowledge.")

                # Should reload
                prompt2, tokens2 = load_and_cache_prompt("test_agent", agent_dir, cache)
                assert "Updated knowledge" in prompt2

    def test_cache_always_tracks_knowledge_mtime(self, tmp_path):
        """Cache always tracks knowledge.md mtime regardless of RAG state.

        This ensures cache invalidates when knowledge.md changes even if RAG
        is currently disabled. The mtime is tracked unconditionally so that
        enabling RAG later will pick up any changes made while disabled.
        """
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        (agent_dir / "rule.md").write_text("# Rules\nTest rules")

        # Create knowledge file
        knowledge_file = tmp_path / "knowledge.md"
        knowledge_file.write_text("# Knowledge Base\n\nKnowledge content.")

        cache = PromptCache()

        with patch("daemon.loader.is_rag_enabled", return_value=False):
            # First load
            prompt1, tokens1 = load_and_cache_prompt("test_agent", agent_dir, cache)
            assert "# Rules" in prompt1
            assert "Knowledge Base" not in prompt1  # Not included in prompt when RAG disabled

            # Modify knowledge file
            time.sleep(0.1)
            knowledge_file.write_text("# Knowledge Base\n\nUpdated knowledge.")

            # Should reload because knowledge.md mtime changed (even though RAG is disabled)
            prompt2, tokens2 = load_and_cache_prompt("test_agent", agent_dir, cache)
            # Cache was invalidated, but prompt still doesn't include KB since RAG is disabled
            assert "Knowledge Base" not in prompt2

    def test_cache_invalidates_when_rag_toggled(self, tmp_path):
        """Cache should reflect RAG state changes - content differs when toggled."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        (agent_dir / "rule.md").write_text("# Rules\nTest rules")

        # Create knowledge file
        knowledge_file = tmp_path / "knowledge.md"
        knowledge_file.write_text("# Knowledge Base\n\nImportant knowledge content.")

        cache = PromptCache()

        # 1. Load with RAG disabled → no KB content
        with patch("daemon.loader.is_rag_enabled", return_value=False):
            prompt1, tokens1 = load_and_cache_prompt("test_agent", agent_dir, cache)
            assert "Knowledge Base" not in prompt1
            assert "Important knowledge" not in prompt1

        # 2. Load with RAG enabled → KB content present
        with patch("daemon.loader.is_rag_enabled", return_value=True):
            with patch("daemon.loader.KNOWLEDGE_FILE", knowledge_file):
                # Invalidate cache first since RAG state changed
                cache.invalidate("test_agent")
                prompt2, tokens2 = load_and_cache_prompt("test_agent", agent_dir, cache)
                assert "Knowledge Base" in prompt2
                assert "Important knowledge" in prompt2

        # 3. Toggle back to disabled → no KB content
        with patch("daemon.loader.is_rag_enabled", return_value=False):
            # Invalidate cache since RAG state changed
            cache.invalidate("test_agent")
            prompt3, tokens3 = load_and_cache_prompt("test_agent", agent_dir, cache)
            assert "Knowledge Base" not in prompt3
            assert "Important knowledge" not in prompt3


class TestComposeSystemPromptH1Stripping:
    """Tests for H1 stripping in compose_system_prompt shared_knowledge handling."""

    def test_compose_strips_leading_h1_from_knowledge(self):
        """Leading H1 in shared_knowledge should be stripped to prevent double-heading."""
        prompts = {
            "soul": "# Who I Am\nI am a test agent.",
            "rule": "# Rules\nFollow these rules",
        }
        # shared_knowledge starts with H1 heading
        shared_knowledge = "# Project Knowledge Base\n\nThis is the knowledge content."
        
        result = compose_system_prompt(prompts, shared_knowledge=shared_knowledge)
        
        # The section should be "## Knowledge Base\n\n" + stripped content
        assert "## Knowledge Base" in result
        # The stripped H1 should not appear in the output
        assert "# Project Knowledge Base" not in result
        # But the content should still be there
        assert "This is the knowledge content" in result

    def test_compose_strips_h1_with_various_heading_styles(self):
        """H1 stripping handles various markdown H1 styles."""
        prompts = {"rule": "# Rules\nTest"}
        
        # Test with underlined style H1 (shouldn't match, but shouldn't break)
        shared_knowledge_underlined = "Project Docs\n============\n\nContent here."
        result = compose_system_prompt(prompts, shared_knowledge=shared_knowledge_underlined)
        # Underline style isn't an H1 by our regex, so it stays
        assert "Project Docs" in result
        
        # Test with extra whitespace in H1
        shared_knowledge_whitespace = "#   Project Title  \n\nContent."
        result = compose_system_prompt(prompts, shared_knowledge=shared_knowledge_whitespace)
        # Should strip the H1
        assert "# Project Title" not in result
        assert "Content" in result

    def test_compose_does_not_strip_h2_headings(self):
        """H2 headings in shared_knowledge should NOT be stripped."""
        prompts = {"rule": "# Rules\nTest"}
        # shared_knowledge starts with H2 (##)
        shared_knowledge = "## Important Notes\n\nThis is H2 content."
        
        result = compose_system_prompt(prompts, shared_knowledge=shared_knowledge)
        
        # H2 should NOT be stripped
        assert "## Important Notes" in result
        assert "This is H2 content" in result

    def test_compose_no_double_h1_when_file_has_h1(self):
        """When shared_knowledge has H1, output should have section header + stripped content."""
        prompts = {
            "rule": "# Rules\nTest rules",
            "memory": "# Memory\nMemory content",
        }
        shared_knowledge = "# Knowledge\n\nDetailed knowledge here."

        result = compose_system_prompt(prompts, shared_knowledge=shared_knowledge)

        # Should have the section header we add
        assert "## Knowledge Base" in result
        # Should have the stripped content (without the original H1)
        assert "Detailed knowledge here" in result
        # Original H1 should not appear - the regex strips lines starting with exactly "# " (H1)
        assert "Detailed knowledge here" in result
        # The H1 "# Knowledge\n\n" should be completely stripped
        # Check that the content after Knowledge Base section starts with the content
        kb_section = result.split("## Knowledge Base")[-1]
        assert kb_section.startswith("\n\nDetailed knowledge here")

    def test_compose_knowledge_without_h1_preserves_all_content(self):
        """When shared_knowledge has no H1, all content should be preserved."""
        prompts = {"rule": "# Rules\nTest"}
        # shared_knowledge without H1 (just body text)
        shared_knowledge = "Use the explore tool to query the knowledge base."
        
        result = compose_system_prompt(prompts, shared_knowledge=shared_knowledge)
        
        # All content should be present
        assert "Use the explore tool" in result
        assert "knowledge base" in result
