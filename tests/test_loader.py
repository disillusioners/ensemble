"""Tests for daemon/loader.py"""

import time

import pytest

from daemon.loader import (
    PromptCache,
    compose_system_prompt,
    estimate_tokens,
    load_agent_prompts,
    load_and_cache_prompt,
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

        # Check order: rule should come first
        rule_pos = result.find("## Rules")
        skill_pos = result.find("## Skills")
        workflow_pos = result.find("## Workflow")
        memory_pos = result.find("## Memory")

        assert rule_pos < skill_pos < workflow_pos < memory_pos

    def test_compose_system_prompt_headers(self, tmp_path):
        """Test that each section has proper header."""
        prompts = {
            "skill": "Skill content",
            "rule": "Rule content",
        }

        result = compose_system_prompt(prompts)

        assert "## Rules\n\nRule content" in result
        assert "## Skills\n\nSkill content" in result

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

    def test_prompt_cache_get_miss(self, tmp_path):
        """Test cache miss returns None."""
        cache = PromptCache()
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()

        result = cache.get(agent_dir)

        assert result is None

    def test_prompt_cache_set_get(self, tmp_path):
        """Test cache set and get."""
        cache = PromptCache()
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()

        cache.set(agent_dir, "test prompt", 100, {"skill.md": 1.0})

        result = cache.get(agent_dir)

        assert result is not None
        assert result[0] == "test prompt"
        assert result[1] == 100

    def test_prompt_cache_invalidate(self, tmp_path):
        """Test cache invalidation."""
        cache = PromptCache()
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()

        cache.set(agent_dir, "test prompt", 100, {"skill.md": 1.0})
        cache.invalidate(agent_dir)

        result = cache.get(agent_dir)
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

        prompt, tokens = load_and_cache_prompt(agent_dir, cache)

        assert "## Skills" in prompt
        assert "## Rules" in prompt
        assert tokens > 0

    def test_load_and_cache_prompt_cached(self, tmp_path):
        """Test that cached version is returned when files unchanged."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        (agent_dir / "skill.md").write_text("# Skills\nTest skills")

        cache = PromptCache()

        # First call - should load from disk
        prompt1, tokens1 = load_and_cache_prompt(agent_dir, cache)

        # Second call - should return cached version
        prompt2, tokens2 = load_and_cache_prompt(agent_dir, cache)

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
        prompt1, tokens1 = load_and_cache_prompt(agent_dir, cache)

        # Wait a bit and modify the file to change mtime
        time.sleep(0.1)
        skill_file.write_text("# Skills\nUpdated skills")

        # Third call - should reload because mtime changed
        prompt3, tokens3 = load_and_cache_prompt(agent_dir, cache)

        assert "Updated skills" in prompt3
        assert tokens3 > 0
