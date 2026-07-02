"""Integration tests for the innate-skills refactoring.

These tests verify the complete end-to-end behavior of the refactored skill system:
- System prompt identity (critical)
- Backward compatibility
- Cache invalidation
- Edge cases
- Registry behavior (including find_skill)

This specifically tests against the REAL agents/ directory to ensure:
- developer, reviewer, tester, planner, tidier, approver all get opencode skill
- leader gets coordination skill
- jober gets job-orchestration skill
- tester gets BOTH opencode AND test-pack
- giter has NO skills section
"""
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from daemon.loader import (
    PromptCache,
    compose_system_prompt,
    load_agent_skills,
    load_and_cache_prompt,
    load_agent_prompts,
)
from daemon.registry import AgentRegistry, get_registry


@pytest.fixture
def real_agents_dir():
    """Return the real agents directory for integration testing."""
    return Path("agents")


@pytest.fixture
def real_registry(real_agents_dir):
    """Create registry with real agents directory."""
    registry = AgentRegistry(real_agents_dir)
    registry.discover()
    return registry


class TestInnateSkillsSystemPromptIdentity:
    """CRITICAL: Verify every agent's loaded system prompt contains the exact same skill content they had before."""

    def test_all_agents_get_correct_innate_skills_in_system_prompt(self, real_agents_dir, real_registry):
        """Test that every agent gets the correct skill content in its final system prompt."""
        cache = PromptCache()
        
        # Test each agent
        # Stale test: skill header uses hyphen (OpenCode-Skill) not underscore
        test_cases = [
            ("developer", ["opencode", "chart"], "OpenCode-Skill"),
            ("reviewer", ["opencode", "chart"], "OpenCode-Skill"),
            ("tester", ["opencode", "chart", "test-pack"], "OpenCode-Skill"),  # tester gets BOTH
            ("tester", ["opencode", "chart", "test-pack"], "Test Pack Skill"),  # tester gets BOTH
            ("planner", ["opencode", "chart"], "OpenCode-Skill"),
            ("tidier", ["opencode", "chart"], "OpenCode-Skill"),
            ("approver", ["opencode", "chart"], "OpenCode-Skill"),
            ("leader", ["coordination"], "Coordination Skill"),
            ("jober", ["job-orchestration"], "Job Orchestration"),
            ("giter", [], None),  # giter has NO innate_skills
        ] 
        
        for agent_id, expected_skills, expected_skill_content in test_cases:
            agent_meta = real_registry.get(agent_id)
            assert agent_meta is not None, f"Agent {agent_id} not found in registry"
            assert agent_meta.innate_skills == expected_skills, f"{agent_id} should have {expected_skills}, got {agent_meta.innate_skills}"
            
            # Load the full prompt (this exercises the complete pipeline)
            prompt, tokens = load_and_cache_prompt(agent_id, agent_meta.path, cache)
            
            if expected_skill_content:
                # Should contain the skill content
                assert expected_skill_content in prompt, f"{agent_id} prompt should contain {expected_skill_content}"
            else:
                # giter should NOT have any skill sections (no innate_skills, no legacy skills/)
                # Stale test: skill header uses hyphen (OpenCode-Skill) not underscore
                assert "OpenCode-Skill" not in prompt, f"giter should NOT have opencode skill"
                assert "Coordination Skill" not in prompt, f"giter should NOT have coordination skill"
                assert "# Skill" not in prompt, f"giter should have no skill sections"
            
            # Verify it contains the soul section (identity content from soul.md)
            # Each agent has different soul.md structure, so just verify non-empty prompt
            assert len(prompt) > 100, f"{agent_id} should have substantial prompt content"
    
    def test_tester_gets_both_skills(self, real_agents_dir, real_registry):
        """Specifically verify tester gets BOTH opencode AND test-pack skills."""
        cache = PromptCache()
        tester_meta = real_registry.get("tester")
        assert tester_meta is not None
        assert tester_meta.innate_skills == ["opencode", "chart", "test-pack"]
        
        prompt, _ = load_and_cache_prompt("tester", tester_meta.path, cache)
        
        # Should contain both skills
        # Stale test: skill header uses hyphen (OpenCode-Skill) not underscore
        assert "OpenCode-Skill" in prompt
        assert "Test Pack Skill" in prompt
        # Should appear in correct order (opencode before test-pack due to sorted())
        opencode_pos = prompt.find("OpenCode-Skill")
        testpack_pos = prompt.find("Test Pack Skill")
        assert opencode_pos < testpack_pos, "opencode should appear before test-pack in prompt"


class TestBackwardCompatibility:
    """Test backward compatibility for agents without innate_skills field."""
    
    def test_no_innate_skills_field_uses_legacy_fallback(self, tmp_path):
        """If an agent has no innate_skills field, it should fall back to legacy skills/ dir."""
        agent_dir = tmp_path / "legacy_agent"
        agent_dir.mkdir()
        
        # Create meta.json WITHOUT innate_skills field
        meta = {
            "id": "legacy_agent",
            "name": "Legacy Agent",
            "description": "Uses legacy skills",
        }
        (agent_dir / "meta.json").write_text(json.dumps(meta))
        
        # Create legacy skills/ directory
        skills_dir = agent_dir / "skills" / "legacy_skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "skill.md").write_text("# Legacy Skill\nThis is legacy behavior.")
        
        # Also create rule.md so we have some content
        (agent_dir / "rule.md").write_text("# Rules\nTest rules")
        
        skills = load_agent_skills(agent_dir)
        assert "legacy_skill" in skills
        assert "Legacy Skill" in skills["legacy_skill"]
    
    def test_empty_innate_skills_array_uses_legacy_fallback(self, tmp_path):
        """If innate_skills is explicitly [], it should fall through to legacy."""
        agent_dir = tmp_path / "empty_innate"
        agent_dir.mkdir()
        
        meta = {
            "id": "empty_innate",
            "name": "Empty Innate",
            "innate_skills": [],  # Empty array should trigger legacy
        }
        (agent_dir / "meta.json").write_text(json.dumps(meta))
        
        # Create legacy skill
        skills_dir = agent_dir / "skills" / "legacy"
        skills_dir.mkdir(parents=True)
        (skills_dir / "skill.md").write_text("# Legacy Skill\nFallback works.")
        
        (agent_dir / "rule.md").write_text("# Rules")
        
        skills = load_agent_skills(agent_dir, meta)
        assert "legacy" in skills
        assert "Fallback works" in skills["legacy"]


class TestCacheInvalidation:
    """Test that modifying innate-skills files triggers prompt reload."""
    
    def test_innate_skill_modification_invalidates_cache(self, real_agents_dir, real_registry):
        """Modifying an innate skill file should trigger cache reload."""
        cache = PromptCache()
        agent_id = "tester"
        agent_meta = real_registry.get(agent_id)
        
        # First load
        prompt1, tokens1 = load_and_cache_prompt(agent_id, agent_meta.path, cache)
        
        # Find the test-pack skill file
        skill_file = real_agents_dir / "_prompt_system" / "innate-skills" / "test-pack" / "skill.md"
        assert skill_file.exists()
        
        original_content = skill_file.read_text()
        try:
            # Modify the skill file (add a marker)
            time.sleep(0.1)  # Ensure mtime changes
            modified_content = original_content + "\n\n# CACHE_INVALIDATION_TEST_MARKER"
            skill_file.write_text(modified_content)
            
            # Should reload from cache miss
            prompt2, tokens2 = load_and_cache_prompt(agent_id, agent_meta.path, cache)
            
            assert "CACHE_INVALIDATION_TEST_MARKER" in prompt2
            assert prompt1 != prompt2, "Prompt should have changed after skill modification"
            
        finally:
            # Restore original content
            skill_file.write_text(original_content)
    
    def test_cache_hit_when_nothing_changed(self, real_agents_dir, real_registry):
        """Cache should return identical object when nothing changed."""
        cache = PromptCache()
        agent_id = "developer"
        agent_meta = real_registry.get(agent_id)
        
        # First call populates cache
        prompt1, tokens1 = load_and_cache_prompt(agent_id, agent_meta.path, cache)
        
        # Second call should be cache hit (same object reference for the tuple)
        prompt2, tokens2 = load_and_cache_prompt(agent_id, agent_meta.path, cache)
        
        assert prompt1 is prompt2, "Should return same prompt object from cache"
        assert tokens1 == tokens2


class TestEdgeCases:
    """Test edge cases for the innate skills system."""
    
    def test_missing_innate_skill_file_logs_warning(self, tmp_path, caplog):
        """Declared innate skill that doesn't exist should log warning but not crash."""
        agent_dir = tmp_path / "test_agent"
        agent_dir.mkdir()
        
        # Create meta with non-existent skill
        meta_content = {
            "id": "test_agent",
            "name": "Test",
            "innate_skills": ["opencode", "nonexistent_skill"]
        }
        (agent_dir / "meta.json").write_text(json.dumps(meta_content))
        (agent_dir / "rule.md").write_text("# Rules\nTest")
        
        # Create the innate-skills dir with only opencode
        innate_dir = tmp_path / "_prompt_system" / "innate-skills"
        opencode_dir = innate_dir / "opencode"
        opencode_dir.mkdir(parents=True)
        (opencode_dir / "skill.md").write_text("# OpenCode-Skill\nThis exists.")
        
        with caplog.at_level("WARNING"):
            skills = load_agent_skills(agent_dir, meta_content)
        
        assert "opencode" in skills
        assert "nonexistent_skill" not in skills
        assert any("nonexistent_skill" in record.message for record in caplog.records)
    
    def test_invalid_json_in_meta_json_falls_back_to_legacy(self, tmp_path):
        """Invalid JSON in meta.json should not crash - should fall back to legacy behavior."""
        agent_dir = tmp_path / "bad_json_agent"
        agent_dir.mkdir()
        
        # Invalid JSON
        (agent_dir / "meta.json").write_text("{this is not valid json")
        
        # Create legacy skill
        skills_dir = agent_dir / "skills" / "legacy_skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "skill.md").write_text("# Legacy Skill\nThis should still load.")
        (agent_dir / "rule.md").write_text("# Rules")
        
        cache = PromptCache()
        prompt, tokens = load_and_cache_prompt("bad_json_agent", agent_dir, cache)
        
        assert "Legacy Skill" in prompt
        assert "# Rules" in prompt
    
    def test_innate_skills_takes_priority_over_local_skills_dir(self, tmp_path):
        """When both innate_skills and local skills/ exist, innate should win."""
        agent_dir = tmp_path / "conflict_agent"
        agent_dir.mkdir()
        
        # Create meta with innate_skills
        meta = {"id": "conflict_agent", "innate_skills": ["opencode"]}
        (agent_dir / "meta.json").write_text(json.dumps(meta))
        
        # Create innate skill
        innate_dir = tmp_path / "_prompt_system" / "innate-skills" / "opencode"
        innate_dir.mkdir(parents=True)
        (innate_dir / "skill.md").write_text("# From Innate\nInnate content wins.")
        
        # Create conflicting local skill
        local_dir = agent_dir / "skills" / "opencode"
        local_dir.mkdir(parents=True)
        (local_dir / "skill.md").write_text("# From Local\nThis should be ignored.")
        
        (agent_dir / "rule.md").write_text("# Rules")
        
        skills = load_agent_skills(agent_dir, meta)
        assert "opencode" in skills
        assert "Innate content wins" in skills["opencode"]
        assert "From Local" not in skills["opencode"]


class TestRegistryInnateSkills:
    """Test AgentRegistry behavior with innate_skills."""
    
    def test_find_skill_checks_innate_first(self, real_registry):
        """find_skill() should check innate-skills first, then legacy."""
        # Test opencode skill - should find all agents that declare it via innate_skills
        agents_with_opencode = real_registry.find_skill("opencode")
        expected = ["approver", "developer", "planner", "reviewer", "tester", "tidier"]
        assert sorted(agents_with_opencode) == sorted(expected)
        
        # Test coordination skill
        agents_with_coordination = real_registry.find_skill("coordination")
        assert agents_with_coordination == ["leader"]
        
        # Test job-orchestration
        agents_with_job = real_registry.find_skill("job-orchestration")
        assert agents_with_job == ["jober"]
        
        # Test test-pack
        agents_with_testpack = real_registry.find_skill("test-pack")
        assert agents_with_testpack == ["tester"]
    
    def test_find_skill_respects_innate_skills_in_metadata(self, real_registry):
        """Registry should correctly populate innate_skills in AgentMetadata."""
        for agent_id in ["developer", "leader", "tester", "jober"]:
            meta = real_registry.get(agent_id)
            assert meta is not None
            assert hasattr(meta, "innate_skills")
            assert isinstance(meta.innate_skills, list)
    
    def test_giter_has_no_innate_skills(self, real_registry):
        """giter should have empty innate_skills list."""
        giter = real_registry.get("giter")
        assert giter is not None
        assert giter.innate_skills == []


class TestInnateSkillsIntegration:
    """Full integration test of the loader + registry pipeline."""
    
    def test_complete_pipeline_with_real_agents(self, real_registry):
        """Test the complete flow from registry discovery to prompt composition."""
        cache = PromptCache()
        
        # Test that all agents can be loaded without errors
        for agent_meta in real_registry.list_all():
            agent_id = agent_meta.id
            try:
                prompt, tokens = load_and_cache_prompt(
                    agent_id, agent_meta.path, cache
                )
                assert isinstance(prompt, str)
                assert isinstance(tokens, int)
                assert tokens > 0
                assert len(prompt) > 100  # Should have substantial content
            except Exception as e:
                pytest.fail(f"Failed to load prompt for {agent_id}: {e}")
        
        # Verify specific skill content is present for key agents
        tester_prompt, _ = load_and_cache_prompt("tester", real_registry.get("tester").path, cache)
        # Stale test: skill header uses hyphen (OpenCode-Skill) not underscore
        assert "OpenCode-Skill" in tester_prompt
        assert "Test Pack Skill" in tester_prompt
        
        leader_prompt, _ = load_and_cache_prompt("leader", real_registry.get("leader").path, cache)
        assert "Coordination Skill" in leader_prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])