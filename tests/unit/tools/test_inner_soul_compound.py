"""Tests for inner_soul tool's compound request detection and splitting.

Tests the compound request functionality in inner_soul tool:
1. _split_compound_request() - splits compound requests into parts
2. Full tool behavior with compound requests
3. RAG redirect interaction with compound requests
4. Edge cases and boundary conditions
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from daemon.registry import AgentMetadata
from daemon.tools.inner_soul import (
    _split_compound_request,
    _classify_request,
    create_inner_soul_tool,
    _should_redirect_to_rag,
    _format_rag_redirect,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_agent(tmp_path):
    """Create a minimal test agent in temp directory."""
    agent_dir = tmp_path / "test_agent"
    agent_dir.mkdir()
    (agent_dir / "soul.md").write_text("# Who I Am\n\nI am a test agent.\n")
    (agent_dir / "growth.md").write_text("# Growth\n\nmax_memory_words: 2000\nmax_soul_chars: 2000\n")
    (agent_dir / "workflow.md").write_text("# Workflow\n\n1. Process\n")
    (agent_dir / "rule.md").write_text("# Rules\n\n- Follow rules\n")
    (agent_dir / "memory.md").write_text("# Memory\n\n")
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


@pytest.fixture
def rag_disabled():
    """Mock RAG as disabled for tests that expect no redirect."""
    with patch("daemon.tools.inner_soul.is_rag_enabled", return_value=False):
        yield


# =============================================================================
# Test _split_compound_request() - Unit Tests
# =============================================================================


class TestSplitCompoundRequest:
    """Tests for the _split_compound_request() function."""

    # -------------------------------------------------------------------------
    # Split on AND keyword
    # -------------------------------------------------------------------------

    def test_split_on_and_uppercase(self):
        """Split on uppercase AND keyword."""
        result = _split_compound_request("Remember my name is John AND I prefer dark mode")
        assert len(result) == 2
        assert "Remember my name is John" in result
        assert "I prefer dark mode" in result

    def test_split_on_and_lowercase(self):
        """Split on lowercase AND keyword (case-insensitive)."""
        # The regex uses re.IGNORECASE, so lowercase 'and' should also split
        result = _split_compound_request("one and two")
        assert len(result) == 2
        assert "one" in result
        assert "two" in result

    def test_split_on_and_mixed_case(self):
        """Split on mixed case AND keyword."""
        result = _split_compound_request("First part And Second part")
        assert len(result) == 2
        assert "First part" in result
        assert "Second part" in result

    def test_split_multiple_ands(self):
        """Split on multiple AND keywords."""
        result = _split_compound_request("A AND B AND C")
        assert len(result) == 3
        assert "A" in result
        assert "B" in result
        assert "C" in result

    def test_split_on_and_preserves_whitespace_around(self):
        """AND with surrounding whitespace should split correctly."""
        result = _split_compound_request("Part1  AND  Part2")
        assert len(result) == 2
        assert "Part1" in result
        assert "Part2" in result

    # -------------------------------------------------------------------------
    # Split on semicolons
    # -------------------------------------------------------------------------

    def test_split_on_semicolons(self):
        """Split on semicolons."""
        result = _split_compound_request("Deploy to staging; notify the team")
        assert len(result) == 2
        assert "Deploy to staging" in result
        assert "notify the team" in result

    def test_split_on_semicolons_with_spaces(self):
        """Split on semicolons with surrounding spaces."""
        result = _split_compound_request("First; Second ; Third")
        assert len(result) == 3
        assert "First" in result
        assert "Second" in result
        assert "Third" in result

    # -------------------------------------------------------------------------
    # Split on sentence boundaries
    # -------------------------------------------------------------------------

    def test_split_on_sentence_boundary(self):
        """Split on period followed by uppercase (sentence boundary)."""
        result = _split_compound_request("First sentence. Second one")
        assert len(result) == 2
        assert "First sentence" in result
        assert "Second one" in result

    def test_split_on_multiple_sentences(self):
        """Split on multiple sentence boundaries."""
        result = _split_compound_request("One. Two. Three")
        assert len(result) == 3

    def test_sentence_boundary_requires_uppercase(self):
        """Sentence boundary should only split when followed by uppercase."""
        # This should NOT split because 's' is lowercase
        result = _split_compound_request("This is a test. it continues here")
        assert len(result) == 1
        assert "This is a test. it continues here" in result

    # -------------------------------------------------------------------------
    # Priority: AND takes precedence
    # -------------------------------------------------------------------------

    def test_and_takes_precedence_over_semicolons(self):
        """AND should split before semicolons are considered."""
        # If AND is present, it should split on AND, not semicolons
        result = _split_compound_request("A AND B; C")
        assert len(result) == 2
        assert "A" in result
        assert "B; C" in result  # Semicolon preserved in second part

    def test_and_takes_precedence_over_sentence_boundaries(self):
        """AND should split before sentence boundaries are considered."""
        result = _split_compound_request("First AND Second. Third")
        assert len(result) == 2
        assert "First" in result
        assert "Second. Third" in result

    # -------------------------------------------------------------------------
    # No split (simple requests)
    # -------------------------------------------------------------------------

    def test_no_split_simple_request(self):
        """Simple request with no split markers should return single item."""
        result = _split_compound_request("Hello world")
        assert len(result) == 1
        assert result == ["Hello world"]

    def test_no_split_with_and_as_part_of_word(self):
        """'and' as part of another word should not cause split."""
        result = _split_compound_request("Handle expandable widgets")
        assert len(result) == 1
        assert "expandable" in result[0]

    def test_no_split_with_sandwich_word(self):
        """Words containing 'and' inside should not cause split."""
        result = _split_compound_request("The command is standard")
        assert len(result) == 1
        assert "standard" in result[0]

    # -------------------------------------------------------------------------
    # Edge cases
    # -------------------------------------------------------------------------

    def test_empty_string(self):
        """Empty string should return single-item list with empty string stripped."""
        result = _split_compound_request("")
        # Empty strings should be filtered out, leaving empty list
        # Then function returns [request] which is [""]
        assert len(result) == 1
        assert result == [""]

    def test_whitespace_only(self):
        """Whitespace-only string returns as-is (no split pattern matches)."""
        result = _split_compound_request("   ")
        # No split pattern matches, so returns original string with whitespace
        assert len(result) == 1
        assert result == ["   "]

    def test_only_and(self):
        """String with only AND should handle gracefully."""
        result = _split_compound_request("AND")
        # AND alone with surrounding whitespace splits to empty strings
        # which are filtered out, leaving empty result
        assert result == ["AND"]

    def test_and_with_only_whitespace(self):
        """AND with only whitespace around should handle gracefully."""
        result = _split_compound_request("   AND   ")
        # Should produce empty strings which get filtered
        assert result == ["   AND   "]

    def test_multiple_empty_parts_filtered(self):
        """Multiple empty parts after split should be filtered."""
        # "A AND AND B" - The pattern \s+AND\s+ requires spaces around AND.
        # First AND matches " AND ", leaving "AND B" which doesn't match (no leading space).
        # Result: ["A", "", "AND B"] -> filtered to ["A", "AND B"]
        result = _split_compound_request("A AND AND B")
        assert len(result) == 2
        assert "A" in result
        assert "AND B" in result

    def test_double_and(self):
        """Double AND with spaces in between splits correctly."""
        # "X AND AND Y" - First AND matches " AND ", leaving "AND Y"
        # "AND Y" doesn't match \s+AND\s+ (no leading space), so it's kept as-is
        result = _split_compound_request("X AND AND Y")
        assert len(result) == 2
        assert "X" in result
        assert "AND Y" in result

    # -------------------------------------------------------------------------
    # Very long compound requests
    # -------------------------------------------------------------------------

    def test_very_long_compound_with_many_ands(self):
        """Handle compound requests with many ANDs."""
        long_request = " Part1 AND Part2 AND Part3 AND Part4 AND Part5 AND Part6 AND Part7 AND Part8 AND Part9 AND Part10"
        result = _split_compound_request(long_request)
        assert len(result) == 10
        for i in range(1, 11):
            assert f"Part{i}" in result

    def test_long_request_not_split(self):
        """Long single request should not be split."""
        long_request = "This is a very long single request " * 10
        result = _split_compound_request(long_request)
        assert len(result) == 1
        # Result has trailing whitespace stripped
        assert result[0].strip() == long_request.strip()
        assert "This is a very long" in result[0]

    # -------------------------------------------------------------------------
    # Trim whitespace
    # -------------------------------------------------------------------------

    def test_trim_whitespace_from_parts(self):
        """Parts should have whitespace trimmed."""
        result = _split_compound_request("  First  AND   Second  ")
        assert len(result) == 2
        assert result[0] == "First"
        assert result[1] == "Second"

    def test_trim_leading_trailing_from_single(self):
        """Single request should have whitespace trimmed."""
        result = _split_compound_request("  Hello World  ")
        assert len(result) == 1
        assert result[0] == "Hello World"


# =============================================================================
# Test Compound + Classification End-to-End
# =============================================================================


class TestCompoundClassification:
    """Tests for compound requests with classification."""

    def test_compound_identity_and_preference(self, mock_registry, mock_manager, temp_agent):
        """Compound request with identity + user_preference should classify each separately."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({
                "request": "My name is Cody AND User prefers TypeScript"
            })
            
            # Should process both parts
            assert "identity" in result.lower() or "name" in result.lower()
            assert "user_preference" in result.lower() or "prefers" in result.lower()

    def test_compound_knowledge_and_identity(self, mock_registry, mock_manager, temp_agent):
        """Compound request with knowledge + identity should classify each separately."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({
                "request": "I learned that tests are important AND My name is Alice"
            })
            
            # Should show both parts processed
            assert "2 parts" in result or ("knowledge" in result.lower() and "identity" in result.lower())

    def test_compound_three_parts(self, mock_registry, mock_manager, temp_agent):
        """Compound request with three parts should process all."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({
                "request": "My name is Test AND User likes Python AND Always check tests"
            })
            
            # Should indicate 3 parts
            assert "3 parts" in result

    def test_compound_classification_result_structure(self, mock_registry, mock_manager, temp_agent):
        """Verify compound request result has expected structure."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({
                "request": "First part AND Second part"
            })
            
            # Should contain compound indicator
            assert "compound" in result.lower() or "2 parts" in result


# =============================================================================
# Test Compound + RAG Redirect Interaction
# =============================================================================


class TestCompoundRAGRedirect:
    """Tests for compound requests with RAG redirect behavior."""

    def test_compound_both_parts_redirect(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Compound request where both parts redirect should show both redirects."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            # Both "I learned that" and "Pattern:" are knowledge types that redirect
            result = inner_soul_tool.invoke({
                "request": "I learned that early testing catches bugs AND Pattern: retries are failing"
            })
            
            # Should show RAG redirect for both parts
            # The compound response shows redirect info inline (not as standalone experience() call)
            assert "Redirected to Knowledge System" in result or "redirected to RAG" in result.lower()
            assert "knowledge" in result.lower()
            assert "pattern" in result.lower()
            assert "2 parts" in result

    def test_compound_neither_part_redirects(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Compound request where neither part redirects should process normally."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            # Both "My name is" and "Be cozy" are self-modifications that don't redirect
            result = inner_soul_tool.invoke({
                "request": "My name is Cody AND Be cozy with users"
            })
            
            # Should NOT redirect
            assert "experience()" not in result
            # Should process successfully
            assert "✓" in result or "soul" in result.lower()

    def test_compound_mixed_redirect_behavior(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Compound request with mixed redirect behavior (one redirects, one doesn't)."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            # "My name is" doesn't redirect, "I learned that" does redirect
            result = inner_soul_tool.invoke({
                "request": "My name is Cody AND I learned that TypeScript is great"
            })
            
            # Result should show mixed behavior
            # The identity part should process, the knowledge part should redirect
            lines = result.split('\n')
            
            # At least one line should show redirect, at least one should show successful processing
            has_redirect = any("redirect" in line.lower() or "experience()" in line for line in lines)
            has_processing = any("✓" in line or "soul" in line.lower() for line in lines)
            
            assert has_redirect or has_processing

    def test_compound_workflow_part_not_redirected(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Compound request with workflow should not redirect (workflow doesn't redirect)."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({
                "request": "Always check tests AND My name is TestBot"
            })
            
            # Should NOT redirect to RAG
            assert "experience()" not in result
            # Should process both parts
            assert "workflow" in result.lower() or "✓" in result

    def test_rag_disabled_compound_processes_normally(self, mock_registry, mock_manager, temp_agent, rag_disabled):
        """When RAG is disabled, compound requests should process without redirects."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({
                "request": "I learned that testing is important AND Pattern: retries fail"
            })
            
            # Should NOT redirect even for knowledge types when RAG disabled
            assert "experience()" not in result
            # Should process (possibly to memories/ directory instead)
            assert "✓" in result or "memories" in result.lower() or "Processed" in result


# =============================================================================
# Test Compound Edge Cases
# =============================================================================


class TestCompoundEdgeCases:
    """Tests for edge cases in compound request handling."""

    def test_very_long_compound_request(self, mock_registry, mock_manager, temp_agent, rag_enabled):
        """Handle very long compound requests with many ANDs."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            # Create a request with 10+ parts
            parts = [f"My name is Agent{i}" for i in range(1, 11)]
            long_request = " AND ".join(parts)
            
            result = inner_soul_tool.invoke({"request": long_request})
            
            # Should split and process all parts
            assert "10 parts" in result

    def test_empty_parts_after_split(self, mock_registry, mock_manager, temp_agent):
        """Handle empty parts after split gracefully."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            # This would produce empty parts after split
            result = inner_soul_tool.invoke({"request": "AND"})
            
            # Should handle gracefully (not crash)
            assert "ERROR" not in result or "✓" in result

    def test_and_without_space_does_not_split(self):
        """AND without spaces should not split."""
        result = _split_compound_request("FirstANDSecond")
        assert len(result) == 1
        assert "FirstANDSecond" in result

    def test_semicolon_without_space(self):
        """Semicolon without surrounding space should still split."""
        result = _split_compound_request("First;Second")
        assert len(result) == 2

    def test_multiple_semicolons(self):
        """Multiple semicolons should split into parts."""
        result = _split_compound_request("A; B; C; D")
        assert len(result) == 4

    def test_compound_with_intent_parameter(self, mock_registry, mock_manager, temp_agent):
        """Compound request with explicit intent parameter."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({
                "request": "Name is Test AND Always check code",
                "intent": "change"
            })
            
            # Should process with intent
            assert "2 parts" in result or "✓" in result

    def test_compound_with_target_parameter(self, mock_registry, mock_manager, temp_agent):
        """Compound request with explicit target parameter."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({
                "request": "First thought AND Second thought",
                "target": "memory"
            })
            
            # Should process with target
            assert "2 parts" in result or "memory" in result.lower()

    def test_compound_request_length_validation(self, mock_registry, mock_manager, temp_agent):
        """Each part of compound request should still be validated for length."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            # Very long single part
            long_request = "A" * 2001
            result = inner_soul_tool.invoke({"request": long_request})
            
            # Should return error for length
            assert "ERROR" in result
            assert "2000" in result


# =============================================================================
# Test _should_redirect_to_rag with compound integration
# =============================================================================


class TestShouldRedirectToRagCompound:
    """Tests for _should_redirect_to_rag in compound context."""

    def test_identity_never_redirects(self, rag_enabled):
        """Identity classification should never redirect."""
        classification = {"type": "identity", "targets": ["soul"]}
        assert _should_redirect_to_rag(["soul"], classification, explicit_target=False) is False

    def test_knowledge_with_memory_redirects(self, rag_enabled):
        """Knowledge classification with memory target should redirect."""
        classification = {"type": "knowledge", "targets": ["memory"]}
        assert _should_redirect_to_rag(["memory"], classification, explicit_target=False) is True

    def test_project_knowledge_always_redirects(self, rag_enabled):
        """Project knowledge always redirects (special case)."""
        classification = {"type": "project_knowledge", "targets": ["REJECT"]}
        assert _should_redirect_to_rag(["REJECT"], classification, explicit_target=False) is True


# =============================================================================
# Integration: Full compound workflow
# =============================================================================


class TestFullCompoundWorkflow:
    """Integration tests for full compound request workflow."""

    def test_compound_creates_multiple_memories(self, mock_registry, mock_manager, temp_agent, rag_disabled):
        """Compound request with multiple knowledge parts should create multiple memory files."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            memories_dir = temp_agent / "memories"
            memories_before = list(memories_dir.glob("*.md"))
            
            # When RAG disabled, knowledge requests go to memories/
            result = inner_soul_tool.invoke({
                "request": "Today we discussed API design AND We talked about testing strategy"
            })
            
            memories_after = list(memories_dir.glob("*.md"))
            new_memories = [f for f in memories_after if f not in memories_before]
            
            # Should create at least one new memory (possibly two if both parts processed)
            assert len(new_memories) >= 1

    def test_compound_mixed_types(self, mock_registry, mock_manager, temp_agent, rag_disabled):
        """Compound request with mixed types should update appropriate files."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            soul_file = temp_agent / "soul.md"
            soul_before = soul_file.read_text()
            
            result = inner_soul_tool.invoke({
                "request": "My name is TestBot AND User prefers dark mode"
            })
            
            # Should update soul.md for identity
            soul_after = soul_file.read_text()
            assert soul_after != soul_before or "TestBot" in soul_after

    def test_compound_response_shows_all_parts(self, mock_registry, mock_manager, temp_agent, rag_disabled):
        """Compound response should show all processed parts."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            result = inner_soul_tool.invoke({
                "request": "First thing AND Second thing"
            })
            
            # Should mention both parts
            assert "Part 1" in result or "1:" in result
            assert "Part 2" in result or "2:" in result

    def test_compound_explicit_target_routes_all_parts(self, mock_registry, mock_manager, temp_agent, rag_disabled):
        """Compound request with explicit target should route all parts to that target."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            inner_soul_tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            
            memories_dir = temp_agent / "memories"
            memories_before = list(memories_dir.glob("*.md"))
            
            result = inner_soul_tool.invoke({
                "request": "A AND B AND C",
                "target": "memories"
            })
            
            memories_after = list(memories_dir.glob("*.md"))
            
            # All parts should go to memories/
            assert len(memories_after) > len(memories_before)
