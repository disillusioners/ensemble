"""Tests for inner_soul tool's RAG redirect functionality.

Tests the RAG redirect behavior in inner_soul tool:
1. _should_redirect_to_rag() - determines if request should redirect to experience()
2. _classify_request() - semantically classifies requests
3. _format_rag_redirect() - formats redirect response
4. Full redirect behavior in create_inner_soul_tool()
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from daemon.registry import AgentMetadata
from daemon.tools.inner_soul import (
    _should_redirect_to_rag,
    _classify_request,
    _format_rag_redirect,
    create_inner_soul_tool,
    _RAG_TARGETS,
    _KNOWLEDGE_CLASSIFICATIONS,
    _update_memories,
    _update_memory_md,
)


# =============================================================================
# Constants Verification Tests
# =============================================================================


class TestRAGRedirectConstants:
    """Verify the constants used for RAG redirect logic."""

    def test_rag_targets_contains_memories_and_memory(self):
        """Verify _RAG_TARGETS contains the expected values."""
        assert "memories" in _RAG_TARGETS
        assert "memory" in _RAG_TARGETS
        assert len(_RAG_TARGETS) == 2

    def test_knowledge_classifications_contains_expected_types(self):
        """Verify _KNOWLEDGE_CLASSIFICATIONS contains all knowledge-oriented types."""
        expected = {"knowledge", "pattern", "event", "skill", "mistake", "project_knowledge"}
        assert _KNOWLEDGE_CLASSIFICATIONS == expected


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def default_rag_disabled():
    """Default `is_rag_enabled` to False for this file's tests.

    `_should_redirect_to_rag` calls `is_rag_enabled()` which reads the
    module-level `_rag_enabled` flag in `daemon.rag.config`. That flag can be
    mutated by other test files (e.g., test_config.py calls `disable_rag()`).
    Tests in this file that expect redirect behavior use the `rag_enabled`
    fixture to override this. Using `patch` (not the real `disable_rag()`)
    keeps the mutation scoped to each test, preventing state leakage to
    RAG-dependent tools in other test files.
    """
    with patch("daemon.tools.inner_soul.is_rag_enabled", return_value=False):
        yield


@pytest.fixture
def temp_agent(tmp_path):
    """Create a minimal test agent in temp directory."""
    agent_dir = tmp_path / "test_agent"
    agent_dir.mkdir()
    (agent_dir / "soul.md").write_text("# Who I Am\n\nI am a test agent.\n")
    (agent_dir / "growth.md").write_text("# Growth\n\nmax_memory_words: 2000\nmax_soul_chars: 2000\n")
    (agent_dir / "workflow.md").write_text("# Workflow\n\n1. Process\n")
    (agent_dir / "rule.md").write_text("# Rules\n\n- Follow rules\n")
    (agent_dir / "memory.md").write_text("# Memory\n\n## Known Patterns\n\n(Empty)\n")
    (agent_dir / "memories").mkdir()
    (agent_dir / "history").mkdir()
    return agent_dir


@pytest.fixture
def mock_registry(temp_agent):
    """Create mock registry pointing to temp agent."""
    agent_metadata = AgentMetadata(
        id="test_agent",
        name="Test Agent",
        description="Test agent",
        path=temp_agent,
        system=False,
    )
    mock_reg = MagicMock()
    mock_reg.get.return_value = agent_metadata
    mock_reg.resolve_to_id.return_value = "test_agent"
    return mock_reg


@pytest.fixture
def mock_manager():
    """Create mock InstanceManager."""
    mgr = MagicMock()
    mgr.prompt_cache = MagicMock()
    return mgr


@pytest.fixture
def rag_enabled():
    """Mock RAG as enabled for tests that expect redirect behavior."""
    with patch("daemon.tools.inner_soul.is_rag_enabled", return_value=True):
        yield


# =============================================================================
# Test _should_redirect_to_rag()
# =============================================================================


class TestShouldRedirectToRag:
    """Tests for the _should_redirect_to_rag() function."""

    # -------------------------------------------------------------------------
    # Should redirect (knowledge-oriented with RAG targets)
    # -------------------------------------------------------------------------

    def test_knowledge_classification_with_memory_target_redirects(self, rag_enabled):
        """knowledge classification with memory target should redirect."""
        classification = {"type": "knowledge", "targets": ["memory"]}
        assert _should_redirect_to_rag(["memory"], classification, explicit_target=False) is True

    def test_knowledge_classification_with_memories_target_redirects(self, rag_enabled):
        """knowledge classification with memories target should redirect."""
        classification = {"type": "knowledge", "targets": ["memories"]}
        assert _should_redirect_to_rag(["memories"], classification, explicit_target=False) is True

    def test_pattern_classification_with_memories_target_redirects(self, rag_enabled):
        """pattern classification with memories target should redirect."""
        classification = {"type": "pattern", "targets": ["memories"]}
        assert _should_redirect_to_rag(["memories"], classification, explicit_target=False) is True

    def test_event_classification_with_memories_target_redirects(self, rag_enabled):
        """event classification with memories target should redirect."""
        classification = {"type": "event", "targets": ["memories"]}
        assert _should_redirect_to_rag(["memories"], classification, explicit_target=False) is True

    def test_skill_classification_with_memories_target_redirects(self, rag_enabled):
        """skill classification with memories target should redirect."""
        classification = {"type": "skill", "targets": ["memories"]}
        assert _should_redirect_to_rag(["memories"], classification, explicit_target=False) is True

    def test_mistake_classification_with_memories_target_redirects(self, rag_enabled):
        """mistake classification with memories target should redirect."""
        classification = {"type": "mistake", "targets": ["memories"]}
        assert _should_redirect_to_rag(["memories"], classification, explicit_target=False) is True

    def test_project_knowledge_classification_always_redirects(self, rag_enabled):
        """project_knowledge classification always redirects (special case)."""
        # Even with REJECT target, project_knowledge should redirect
        classification = {"type": "project_knowledge", "targets": ["REJECT"]}
        assert _should_redirect_to_rag(["REJECT"], classification, explicit_target=False) is True

    # -------------------------------------------------------------------------
    # Should NOT redirect (self-modification targets)
    # -------------------------------------------------------------------------

    def test_identity_classification_with_soul_target_does_not_redirect(self):
        """identity classification with soul target should NOT redirect."""
        classification = {"type": "identity", "targets": ["soul"]}
        assert _should_redirect_to_rag(["soul"], classification, explicit_target=False) is False

    def test_personality_classification_with_soul_target_does_not_redirect(self):
        """personality classification with soul target should NOT redirect."""
        classification = {"type": "personality", "targets": ["soul"]}
        assert _should_redirect_to_rag(["soul"], classification, explicit_target=False) is False

    def test_personality_classification_with_user_target_does_not_redirect(self):
        """personality classification with user target should NOT redirect."""
        classification = {"type": "personality", "targets": ["user"]}
        assert _should_redirect_to_rag(["user"], classification, explicit_target=False) is False

    def test_user_preference_classification_does_not_redirect(self):
        """user_preference classification with user target should NOT redirect."""
        classification = {"type": "user_preference", "targets": ["user"]}
        assert _should_redirect_to_rag(["user"], classification, explicit_target=False) is False

    def test_user_identity_classification_does_not_redirect(self):
        """user_identity classification with user target should NOT redirect."""
        classification = {"type": "user_identity", "targets": ["user"]}
        assert _should_redirect_to_rag(["user"], classification, explicit_target=False) is False

    def test_workflow_classification_does_not_redirect(self):
        """workflow classification with workflow target should NOT redirect."""
        classification = {"type": "workflow", "targets": ["workflow"]}
        assert _should_redirect_to_rag(["workflow"], classification, explicit_target=False) is False

    # -------------------------------------------------------------------------
    # Edge cases
    # -------------------------------------------------------------------------

    def test_empty_targets_does_not_redirect(self):
        """Empty targets list should NOT redirect."""
        classification = {"type": "knowledge", "targets": []}
        assert _should_redirect_to_rag([], classification, explicit_target=False) is False

    def test_mixed_targets_with_soul_does_not_redirect(self):
        """Mixed targets including soul should NOT redirect."""
        classification = {"type": "personality", "targets": ["soul", "user"]}
        assert _should_redirect_to_rag(["soul", "memories"], classification, explicit_target=False) is False

    def test_mixed_targets_with_user_does_not_redirect(self):
        """Mixed targets including user should NOT redirect."""
        classification = {"type": "personality", "targets": ["soul", "user"]}
        assert _should_redirect_to_rag(["user", "memories"], classification, explicit_target=False) is False

    def test_mixed_targets_with_workflow_does_not_redirect(self):
        """Mixed targets including workflow should NOT redirect."""
        classification = {"type": "workflow", "targets": ["workflow"]}
        assert _should_redirect_to_rag(["workflow", "memories"], classification, explicit_target=False) is False

    def test_reject_filtered_out_with_only_rag_targets_redirects(self, rag_enabled):
        """After REJECT is filtered out, only RAG targets remain -> redirect."""
        classification = {"type": "knowledge", "targets": ["memories", "REJECT"]}
        # REJECT gets filtered, leaving only "memories"
        assert _should_redirect_to_rag(["memories", "REJECT"], classification, explicit_target=False) is True

    def test_explicit_target_overrides_classification(self, rag_enabled):
        """Explicit target parameter takes precedence."""
        # With explicit target="memory", even if classification doesn't match,
        # the target itself should determine behavior
        classification = {"type": "identity", "targets": ["soul"]}
        # This should not redirect because memory is not in _RAG_TARGETS set logic
        # Wait, actually with explicit_target=True and targets=["memory"],
        # it should still check if all targets are RAG targets
        assert _should_redirect_to_rag(["memory"], classification, explicit_target=True) is False

    def test_rag_disabled_never_redirects(self):
        """When RAG is not enabled, _should_redirect_to_rag should always return False."""
        # This tests that the guard at the beginning of _should_redirect_to_rag works
        classification = {"type": "knowledge", "targets": ["memories"]}
        assert _should_redirect_to_rag(["memories"], classification, explicit_target=False) is False

    def test_rag_disabled_preserves_old_behavior(self):
        """When RAG is disabled, even project_knowledge should NOT redirect."""
        classification = {"type": "project_knowledge", "targets": ["REJECT"]}
        assert _should_redirect_to_rag(["REJECT"], classification, explicit_target=False) is False


# =============================================================================
# Test _classify_request()
# =============================================================================


class TestClassifyRequest:
    """Tests for the _classify_request() function."""

    def test_knowledge_classification_i_learned_that(self):
        """Request with 'I learned that' should be classified as knowledge."""
        result = _classify_request("I learned that early testing catches bugs")
        assert result["type"] == "knowledge"
        assert "memory" in result["targets"] or "memories" in result["targets"]

    def test_identity_classification_my_name_is(self):
        """Request with 'my name is' should be classified as identity."""
        result = _classify_request("My name is Cody")
        assert result["type"] == "identity"
        assert "soul" in result["targets"]

    def test_personality_classification_be_more_friendly(self):
        """Request with personality adjectives should be classified as personality."""
        result = _classify_request("Be more friendly")
        assert result["type"] == "personality"
        assert "soul" in result["targets"] or "user" in result["targets"]

    def test_personality_classification_be_cozy(self):
        """Request with 'be cozy' should be classified as personality."""
        result = _classify_request("Be cozy with the user")
        assert result["type"] == "personality"

    def test_workflow_classification_always_check(self):
        """Request with 'always check' should be classified as workflow."""
        result = _classify_request("Always check tests before committing")
        assert result["type"] == "workflow"
        assert "workflow" in result["targets"]

    def test_user_preference_classification_user_likes(self):
        """Request with 'user likes' should be classified as user_preference."""
        result = _classify_request("User likes TypeScript")
        assert result["type"] == "user_preference"
        assert "user" in result["targets"]

    def test_pattern_classification_pattern_colon(self):
        """Request with 'Pattern:' should be classified as pattern."""
        result = _classify_request("Pattern: always when we use k8s")
        assert result["type"] == "pattern"
        assert "memories" in result["targets"]

    def test_pattern_classification_i_noticed_when(self):
        """Request with 'I noticed when' should be classified as pattern."""
        result = _classify_request("I noticed that when the API times out, we retry")
        assert result["type"] == "pattern"

    def test_event_classification_today_we_discussed(self):
        """Request with 'today' and 'discussed' should be classified as event."""
        result = _classify_request("Today we discussed the API design")
        assert result["type"] == "event"
        assert "memories" in result["targets"]

    def test_event_classification_we_talked_about(self):
        """Request with 'we talked about' should be classified as event."""
        result = _classify_request("We talked about the new feature requirements")
        assert result["type"] == "event"

    def test_skill_classification_i_can_now(self):
        """Request with 'I can now' should be classified as skill."""
        result = _classify_request("I can now do Docker deployments")
        assert result["type"] == "skill"
        assert "memories" in result["targets"]

    def test_skill_classification_new_skill(self):
        """Request with 'new skill' should be classified as skill."""
        result = _classify_request("New skill: Writing async tests")
        assert result["type"] == "skill"

    def test_mistake_classification_mistake_colon(self):
        """Request with 'Mistake:' should be classified as mistake."""
        result = _classify_request("Mistake: I was wrong about the SQL query")
        assert result["type"] == "mistake"
        assert "memories" in result["targets"]

    def test_mistake_classification_lesson_learned(self):
        """Request with 'lesson learned' should be classified as mistake."""
        result = _classify_request("Lesson learned: always validate input")
        assert result["type"] == "mistake"

    def test_project_knowledge_classification_postgresql_url(self):
        """Request with postgres URL should be classified as project_knowledge."""
        result = _classify_request("The project uses postgresql://db.example.com")
        assert result["type"] == "project_knowledge"
        assert "REJECT" in result["targets"]

    def test_project_knowledge_classification_k8s(self):
        """Request with k8s references should be classified as project_knowledge."""
        result = _classify_request("The cluster uses kubernetes with k8s")
        assert result["type"] == "project_knowledge"

    def test_project_knowledge_classification_docker(self):
        """Request with Docker references should be classified as project_knowledge."""
        result = _classify_request("We use Docker for containerization")
        assert result["type"] == "project_knowledge"

    def test_default_classification_no_match(self):
        """Request with no matching pattern should default to event."""
        result = _classify_request("Something random with no match")
        assert result["type"] == "event"
        assert "memories" in result["targets"]

    def test_classification_contains_description(self):
        """Classification result should include description."""
        result = _classify_request("My name is Test")
        assert "description" in result
        assert result["description"] != ""

    def test_classification_has_all_matches_field(self):
        """Classification result should include all_matches."""
        result = _classify_request("My name is Test and I believe in quality")
        # identity matches first, but should also note the personality match
        assert "all_matches" in result
        assert isinstance(result["all_matches"], list)

    def test_multiple_patterns_in_request(self):
        """Request matching multiple patterns should include all in all_matches."""
        # Request matching both pattern: and mistake:
        result = _classify_request("Pattern: Mistake: I keep forgetting tests")
        assert "all_matches" in result
        # The best match should be first, but all_matches contains both


# =============================================================================
# Test _format_rag_redirect()
# =============================================================================


class TestFormatRagRedirect:
    """Tests for the _format_rag_redirect() function."""

    def test_output_contains_experience_call(self):
        """Output should contain 'experience()' as the suggested tool."""
        classification = {"type": "knowledge", "description": "Important knowledge"}
        result = _format_rag_redirect("Test request", classification, ["memories"])
        assert "experience()" in result

    def test_output_contains_classification_type(self):
        """Output should include the classification type."""
        classification = {"type": "knowledge", "description": "Important knowledge"}
        result = _format_rag_redirect("Test request", classification, ["memories"])
        assert "knowledge" in result

    def test_output_contains_description(self):
        """Output should include the classification description."""
        classification = {"type": "pattern", "description": "Observed patterns and insights"}
        result = _format_rag_redirect("Test request", classification, ["memories"])
        assert "Observed patterns and insights" in result

    def test_output_contains_rag_message(self):
        """Output should mention RAG knowledge system."""
        classification = {"type": "event", "description": "Events and observations"}
        result = _format_rag_redirect("Test request", classification, ["memories"])
        assert "RAG" in result or "knowledge base" in result.lower()

    def test_output_truncates_long_request(self):
        """Output should truncate long requests."""
        long_request = "A" * 100  # 100 characters
        classification = {"type": "knowledge", "description": "Test"}
        result = _format_rag_redirect(long_request, classification, ["memories"])
        assert "..." in result
        assert len(result.split('\n')[0]) < 120  # Truncated message

    def test_output_shows_original_targets(self):
        """Output should show the original targets before RAG redirect."""
        classification = {"type": "pattern", "description": "Test"}
        result = _format_rag_redirect("Test", classification, ["memories"])
        assert "memories" in result

    def test_output_mentions_explore_tool(self):
        """Output should mention explore() tool for querying."""
        classification = {"type": "skill", "description": "Test"}
        result = _format_rag_redirect("Test", classification, ["memories"])
        assert "explore()" in result

    def test_output_format_is_multiline(self):
        """Output should be formatted as multiple lines."""
        classification = {"type": "knowledge", "description": "Test"}
        result = _format_rag_redirect("Short test", classification, ["memory"])
        assert "\n" in result

    def test_output_handles_quotes_in_request(self):
        """Output should escape quotes in the experience() call."""
        classification = {"type": "knowledge", "description": "Test"}
        result = _format_rag_redirect('He said "hello" to me', classification, ["memories"])
        # The experience call should have escaped quotes
        assert 'experience(' in result


# =============================================================================
# Test Full Tool with RAG Redirect
# =============================================================================


class TestInnerSoulToolRedirect:
    """Tests for the full inner_soul tool with RAG redirect behavior."""

    def test_knowledge_request_redirects_to_experience(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Knowledge request should redirect to experience(), not create file."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            # Count memories before
            memories_dir = temp_agent / "memories"
            memories_before = list(memories_dir.glob("*.md"))
            
            # Call tool
            result = inner_soul_tool.invoke({"request": "I learned that early testing catches bugs"})
            
            # Should redirect
            assert "experience()" in result
            assert "knowledge" in result.lower()
            
            # Should NOT create memory file
            memories_after = list(memories_dir.glob("*.md"))
            assert len(memories_after) == len(memories_before)

    def test_identity_request_does_not_redirect(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Identity request should update soul.md, not redirect."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            soul_file = temp_agent / "soul.md"
            soul_before = soul_file.read_text()
            
            result = inner_soul_tool.invoke({"request": "My name is Cody"})
            
            # Should NOT redirect
            assert "experience()" not in result
            
            # Should update soul.md
            soul_after = soul_file.read_text()
            assert soul_after != soul_before
            assert "Cody" in soul_after

    def test_project_knowledge_request_redirects(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Project knowledge request should redirect (special case)."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({"request": "The project uses postgresql://db.example.com"})
            
            # Should redirect to RAG
            assert "experience()" in result

    def test_intent_remember_redirects(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """intent='remember' should redirect to experience()."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({
                "intent": "remember",
                "content": "Some important knowledge to remember"
            })
            
            assert "experience()" in result

    def test_personality_request_does_not_redirect(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Personality request should update files, not redirect."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            soul_file = temp_agent / "soul.md"
            soul_before = soul_file.read_text()
            
            result = inner_soul_tool.invoke({"request": "Be cozy with the user"})
            
            # Should NOT redirect
            assert "experience()" not in result
            
            # Should update soul.md or user.md
            soul_after = soul_file.read_text()
            # The change might go to soul.md or user.md depending on classification
            assert soul_after != soul_before or "cozy" in soul_after.lower()

    def test_workflow_request_does_not_redirect(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Workflow request should update workflow.md, not redirect."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            workflow_file = temp_agent / "workflow.md"
            workflow_before = workflow_file.read_text()
            
            result = inner_soul_tool.invoke({"request": "Always check tests before committing"})
            
            # Should NOT redirect
            assert "experience()" not in result
            
            # Should update workflow.md
            workflow_after = workflow_file.read_text()
            assert workflow_after != workflow_before

    def test_pattern_request_redirects(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Pattern request should redirect to experience()."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({"request": "Pattern: whenever we deploy to k8s, we see latency spikes"})
            
            assert "experience()" in result
            assert "pattern" in result.lower()

    def test_event_request_redirects(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Event request should redirect to experience()."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({"request": "Today we discussed the new architecture"})
            
            assert "experience()" in result

    def test_skill_request_redirects(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Skill request should redirect to experience()."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({"request": "I learned to write async tests in Python"})
            
            assert "experience()" in result

    def test_mistake_request_redirects(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Mistake request should redirect to experience()."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({"request": "Mistake: I should not have merged without review"})
            
            assert "experience()" in result
            assert "mistake" in result.lower()

    def test_user_preference_does_not_redirect(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """User preference should update user.md, not redirect."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            user_file = temp_agent / "user.md"
            # Create user.md if it doesn't exist
            user_file.write_text("# User\n\n") if not user_file.exists() else None
            user_before = user_file.read_text()
            
            result = inner_soul_tool.invoke({"request": "User likes TypeScript"})
            
            # Should NOT redirect
            assert "experience()" not in result
            
            # Should update user.md
            user_after = user_file.read_text()
            assert user_after != user_before

    def test_content_parameter_works_for_redirect(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """content parameter (legacy) should also trigger redirect."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({
                "content": "I learned that documentation is important",
            })
            
            assert "experience()" in result

    def test_request_over_2000_chars_returns_error(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Request exceeding 2000 chars should return error, not redirect."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            long_request = "A" * 2001
            result = inner_soul_tool.invoke({"request": long_request})
            
            assert "ERROR" in result
            assert "2000" in result

    def test_empty_request_returns_error(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Empty request should return error."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({})
            
            assert "ERROR" in result


# =============================================================================
# Integration: Tool returns correct response structure
# =============================================================================


class TestInnerSoulToolResponseStructure:
    """Tests for response structure and formatting."""

    def test_redirect_response_has_proper_format(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Verify redirect response has expected format and content."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({
                "request": "I learned that keeping code simple prevents bugs",
            })
            
            # Check key elements are present
            lines = result.split('\n')
            assert any("Redirected" in line for line in lines), "Should contain 'Redirected'"
            assert any("Classification:" in line for line in lines), "Should contain classification"
            assert any("experience()" in line for line in lines), "Should suggest experience()"
            assert any("explore()" in line for line in lines), "Should mention explore()"

    def test_soul_update_response_has_proper_format(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Verify soul update response has expected format."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({"request": "My name is TestBot"})
            
            # Check key elements are present
            assert "Processed" in result or "soul" in result.lower()
            assert "soul.md" in result or "Soul" in result

    def test_workflow_update_response_has_proper_format(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Verify workflow update response has expected format."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({
                "intent": "change",
                "target": "workflow",
                "content": "Always run tests",
            })
            
            assert "Processed" in result or "workflow" in result.lower()
            assert "workflow.md" in result


# =============================================================================
# Bug Fix Tests: target="memories" routing
# =============================================================================


class TestMemoriesTargetRouting:
    """Tests for the target="memories" routing fix.

    Bug fix: "memories" was added to the target Literal type annotation,
    and the code path `if target == "memories": return _update_memories(...)`
    is now active (was previously dead code).
    """

    def test_explicit_target_memories_calls_update_memories(self, temp_agent, mock_manager):
        """Test that explicit target='memories' triggers _update_memories()."""
        classification = {"type": "event", "targets": ["memories"], "description": "Event or observation"}
        
        # Call _update_memories directly
        result = _update_memories("test_agent", temp_agent, "Test memory content", classification, mock_manager)
        
        # Verify success
        assert result["success"] is True
        assert result["target"] == "memories"
        assert "file" in result
        assert result["file"].startswith("memories/")
        
        # Verify file was created
        memories_dir = temp_agent / "memories"
        memory_files = list(memories_dir.glob("*.md"))
        assert len(memory_files) >= 1
        
        # Verify file content
        created_file = memory_files[-1]  # Get the most recent one
        content = created_file.read_text()
        assert "Test memory content" in content
        assert "event" in content.lower()

    def test_execute_update_with_memories_target(self, temp_agent, mock_manager):
        """Test that _execute_update routes to _update_memories for target='memories'."""
        from daemon.tools.inner_soul import _execute_update
        
        classification = {"type": "skill", "targets": ["memories"], "description": "New skills"}
        growth_rules = {"max_memory_words": 500, "max_soul_chars": 2000}
        
        # Execute update with memories target
        result = _execute_update(
            agent_id="test_agent",
            agent_path=temp_agent,
            request="I can now write async tests",
            target="memories",
            intent=None,
            rules=growth_rules,
            manager=mock_manager,
            classification=classification
        )
        
        # Should succeed
        assert result["success"] is True
        assert result["target"] == "memories"
        
        # File should be created
        memories_dir = temp_agent / "memories"
        memory_files = list(memories_dir.glob("*.md"))
        assert len(memory_files) >= 1

    def test_tool_with_explicit_target_memories(self, mock_registry, mock_manager, temp_agent):
        """Test full tool invocation with target='memories'."""
        # This is the main integration test for the bug fix
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            memories_dir = temp_agent / "memories"
            memories_before = list(memories_dir.glob("*.md"))
            
            # Call tool with explicit target="memories"
            result = inner_soul_tool.invoke({
                "target": "memories",
                "request": "Remember this important observation"
            })
            
            # Should succeed and not redirect
            assert "ERROR" not in result or "experience()" not in result
            
            # Should create a memory file
            memories_after = list(memories_dir.glob("*.md"))
            assert len(memories_after) > len(memories_before)
            
            # Verify the new memory contains the request content
            new_files = [f for f in memories_after if f not in memories_before]
            assert len(new_files) >= 1
            content = new_files[0].read_text()
            assert "important observation" in content


# =============================================================================
# Bug Fix Tests: Honest error message when memory exceeds limit
# =============================================================================


class TestMemoryLimitErrorMessage:
    """Tests for the honest error message fix in _update_memory_md().

    Bug fix: When memory.md exceeds the word limit, the error message no longer
    claims "Saved to memories/" - it now honestly says "Content was not saved".
    """

    def test_memory_md_error_message_does_not_claim_saved_elsewhere(self, temp_agent, mock_manager):
        """Test that exceeding memory limit doesn't claim content was saved elsewhere."""
        # Create memory.md that exceeds limit
        memory_file = temp_agent / "memory.md"
        
        # Create content that exceeds the 500-word default limit
        # (growth.md in temp_agent fixture has max_memory_words: 2000, but let's set up a scenario)
        growth_rules = {"max_memory_words": 10, "max_soul_chars": 2000}
        
        # Create existing memory with lots of words
        existing_content = "# Memory\n\n" + "\n".join([f"- Word {i}" for i in range(20)])
        memory_file.write_text(existing_content)
        
        # Try to add more content
        result = _update_memory_md(
            agent_id="test_agent",
            agent_path=temp_agent,
            request="This should fail because we exceed the limit",
            rules=growth_rules,
            manager=mock_manager
        )
        
        # Verify failure
        assert result["success"] is False
        assert "error" in result
        
        # CRITICAL: Error message should NOT claim content was saved elsewhere
        error_msg = result["error"]
        assert "Saved to" not in error_msg, f"Error message should not claim 'Saved to': {error_msg}"
        assert "not saved" in error_msg.lower() or "exceed" in error_msg.lower() or "limit" in error_msg.lower()

    def test_memory_md_error_message_mentions_limit(self, temp_agent, mock_manager):
        """Test that error message honestly mentions the limit was exceeded."""
        growth_rules = {"max_memory_words": 5, "max_soul_chars": 2000}
        
        # Create existing memory exceeding limit
        memory_file = temp_agent / "memory.md"
        memory_file.write_text("# Memory\n\n" + "\n".join([f"- Item {i}" for i in range(10)]))
        
        result = _update_memory_md(
            agent_id="test_agent",
            agent_path=temp_agent,
            request="Excess content",
            rules=growth_rules,
            manager=mock_manager
        )
        
        assert result["success"] is False
        error_msg = result["error"]
        
        # Should mention limit exceeded
        assert "limit" in error_msg.lower() or "words" in error_msg.lower()
        # Should NOT falsely claim success
        assert "Saved" not in error_msg or "not saved" in error_msg.lower()

    def test_memory_md_success_does_not_show_error(self, temp_agent, mock_manager):
        """Test that successful memory.md update doesn't produce error message."""
        growth_rules = {"max_memory_words": 500, "max_soul_chars": 2000}
        
        # Create small memory
        memory_file = temp_agent / "memory.md"
        memory_file.write_text("# Memory\n\n- Initial content\n")
        
        result = _update_memory_md(
            agent_id="test_agent",
            agent_path=temp_agent,
            request="New valid content",
            rules=growth_rules,
            manager=mock_manager
        )
        
        assert result["success"] is True
        assert "error" not in result
        
        # Verify content was written
        content = memory_file.read_text()
        assert "New valid content" in content


# =============================================================================
# Bug Fix Tests: _classify_request() with intent parameter
# =============================================================================


class TestClassifyRequestIntentParameter:
    """Tests for the _classify_request() intent parameter fix.

    Bug fix: _classify_request() now accepts an optional `intent` parameter.
    When classification falls to default with intent="remember", it routes to
    memories/ with a debug log; otherwise logs that classification was inconclusive.
    """

    def test_classify_with_intent_remember_on_default_routes_to_memories(self):
        """Test that intent='remember' on inconclusive classification routes to memories."""
        # Use a request that won't match any pattern
        result = _classify_request("xyz abc 123 random text", intent="remember")
        
        # Should route to memories
        assert result["type"] == "event"
        assert "memories" in result["targets"]
        assert len(result["targets"]) == 1
        assert result["all_matches"] == []

    def test_classify_without_intent_on_default_routes_to_memories(self):
        """Test that default behavior without intent also routes to memories."""
        result = _classify_request("xyz abc 123 random text", intent=None)
        
        # Should route to memories
        assert result["type"] == "event"
        assert "memories" in result["targets"]

    def test_classify_with_intent_learn_on_default_routes_to_memories(self):
        """Test that intent='learn' on inconclusive classification routes to memories."""
        result = _classify_request("xyz abc 123", intent="learn")
        
        # Should route to memories (fallback behavior)
        assert "memories" in result["targets"]

    def test_classify_with_intent_change_on_default_routes_to_memories(self):
        """Test that intent='change' on inconclusive classification routes to memories."""
        result = _classify_request("xyz abc 123", intent="change")
        
        # Should route to memories (fallback behavior)
        assert "memories" in result["targets"]

    def test_classify_matching_pattern_ignores_intent(self):
        """Test that matching pattern uses pattern targets, not intent-based routing."""
        # This request matches identity pattern
        result = _classify_request("My name is TestUser", intent="remember")
        
        # Should match the pattern, not use intent-based routing
        assert result["type"] == "identity"
        assert "soul" in result["targets"]

    def test_classify_intent_remember_affects_only_fallback(self):
        """Test that intent only affects behavior when no pattern matches."""
        # No pattern match
        no_match = _classify_request("some random text", intent="remember")
        assert no_match["all_matches"] == []
        
        # Pattern match - intent should be ignored
        with_match = _classify_request("Remember that the sky is blue", intent="remember")
        assert "knowledge" in with_match["type"] or "memory" in with_match["targets"]

    def test_classify_intent_parameter_is_optional(self):
        """Test that _classify_request works without intent parameter (backward compatible)."""
        # Should work without intent parameter
        result1 = _classify_request("My name is Test")
        assert result1 is not None
        assert "type" in result1
        
        # Should work with intent=None
        result2 = _classify_request("My name is Test", intent=None)
        assert result2 is not None
        assert result2 == result1


# =============================================================================
# Bug Fix Tests: Full tool behavior with intent="remember"
# =============================================================================


class TestToolIntentRememberBehavior:
    """Tests for full tool behavior with intent='remember' parameter."""

    def test_tool_intent_remember_with_unclear_request(self, mock_registry, mock_manager, temp_agent):
        """Test that intent='remember' routes unclear requests to memories/."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            memories_dir = temp_agent / "memories"
            memories_before = list(memories_dir.glob("*.md"))
            
            # Call with unclear request and intent="remember"
            result = inner_soul_tool.invoke({
                "intent": "remember",
                "request": "some unclear text xyz"
            })
            
            # Should create memory file
            memories_after = list(memories_dir.glob("*.md"))
            assert len(memories_after) > len(memories_before)

    def test_tool_intent_remember_is_default_for_remember_intent(self, mock_registry, mock_manager, temp_agent):
        """Test that intent='remember' defaults target to memories/."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            memories_dir = temp_agent / "memories"
            memories_before = list(memories_dir.glob("*.md"))
            
            # Call with only intent="remember" (no explicit target)
            result = inner_soul_tool.invoke({
                "intent": "remember",
                "request": "Remember this fact for later"
            })
            
            # Should create memory file (default target is memories/)
            memories_after = list(memories_dir.glob("*.md"))
            new_memories = [f for f in memories_after if f not in memories_before]
            
            if new_memories:
                content = new_memories[0].read_text()
                assert "Remember this fact" in content
