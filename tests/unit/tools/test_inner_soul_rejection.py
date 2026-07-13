"""Tests for inner_soul tool's project content rejection (Phase 3).

Tests verify that the 3-stage classification flow in `_classify_request()`
correctly identifies project-related content and routes it to REJECT,
regardless of RAG state. The companion file
`test_inner_soul_persona_preservation.py` covers the inverse: legitimate
persona content must NOT be rejected.

Covered behavior:
1. Project-content patterns (git ops, task completion, code changes,
   tech stack mentions, deployment status) classify as `project_knowledge`
   with `["REJECT"]` targets.
2. The 3-stage ordering — Stage 1 persona prefix, Stage 2 project
   pre-check, Stage 3 persona category rescue — behaves as documented.
3. `_format_project_rejection()` produces a clear, multi-line rejection
   message with the right tool pointers and truncates long content.
4. REJECT targets never bubble up as "Unknown target: REJECT" errors.
5. Compound requests split on ` AND ` handle mixed accepted/rejected
   parts without crashing.
"""

import pytest
from unittest.mock import MagicMock, patch

from daemon.registry import AgentMetadata
from daemon.tools.inner_soul import (
    _classify_request,
    _format_project_rejection,
    create_inner_soul_tool,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def default_rag_disabled():
    """Force RAG off so rejection (not redirect) is the primary path.

    `_should_redirect_to_rag` consults `is_rag_enabled()` which reads a
    module-level flag. Other test files may have mutated it. Scoping the
    patch to this file keeps state clean.
    """
    with patch("daemon.tools.inner_soul.is_rag_enabled", return_value=False):
        yield


@pytest.fixture
def temp_agent(tmp_path):
    """Minimal test agent in a temp directory."""
    agent_dir = tmp_path / "test_agent"
    agent_dir.mkdir()
    (agent_dir / "soul.md").write_text("# Who I Am\n\nI am a test agent.\n")
    (agent_dir / "growth.md").write_text(
        "# Growth\n\nmax_memory_words: 2000\nmax_soul_chars: 2000\n"
    )
    (agent_dir / "workflow.md").write_text("# Workflow\n\n1. Process\n")
    (agent_dir / "rule.md").write_text("# Rules\n\n- Follow rules\n")
    (agent_dir / "memory.md").write_text("# Memory\n\n## Known Patterns\n\n(Empty)\n")
    (agent_dir / "memories").mkdir()
    (agent_dir / "history").mkdir()
    return agent_dir


@pytest.fixture
def mock_registry(temp_agent):
    """Mock registry pointing at the temp agent."""
    agent_metadata = AgentMetadata(
        id="test_agent",
        name="Test Agent",
        description="Test agent",
        path=temp_agent,
        system=False,
    )
    mock_reg = MagicMock()
    mock_reg.get_resolved.return_value = agent_metadata
    mock_reg.resolve_to_id.return_value = "test_agent"
    return mock_reg


@pytest.fixture
def mock_manager():
    """Mock InstanceManager with a stub prompt_cache."""
    mgr = MagicMock()
    mgr.prompt_cache = MagicMock()
    return mgr


# =============================================================================
# Project content → REJECT (one test per pattern family)
# =============================================================================


class TestProjectContentRejection:
    """Project-related content must classify as `project_knowledge`/REJECT.

    Each test exercises a real pattern in `CLASSIFICATION_RULES["project_knowledge"]`.
    These tests intentionally call `_classify_request()` directly — the
    3-stage flow is RAG-agnostic, so RAG state does not affect the
    classification result.
    """

    # --- Git operations -------------------------------------------------

    def test_git_push_rejected(self):
        result = _classify_request("git push origin main")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    def test_created_branch_rejected(self):
        result = _classify_request("Created a branch called feature/auth")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    def test_merged_pull_request_rejected(self):
        result = _classify_request("merged the pull request")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    def test_committed_to_branch_rejected(self):
        result = _classify_request("committed to the main branch")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    # --- Task / work completion -----------------------------------------

    def test_completed_build_rejected(self):
        result = _classify_request("Completed the build successfully")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    def test_finished_task_rejected(self):
        result = _classify_request("Finished the task")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    def test_setup_complete_rejected(self):
        result = _classify_request("setup complete")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    def test_deployed_to_production_rejected(self):
        result = _classify_request("Deployed to production")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    # --- Code changes ----------------------------------------------------

    def test_refactored_service_rejected(self):
        result = _classify_request("Refactored the payment service")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    def test_fixed_bug_in_handler_rejected(self):
        result = _classify_request("fixed a bug in the handler")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    def test_updated_api_endpoint_rejected(self):
        result = _classify_request("Updated the API endpoint")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    def test_added_database_table_rejected(self):
        result = _classify_request("added a database table")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    def test_created_new_config_file_rejected(self):
        result = _classify_request("created a new config.py")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    # --- Tech stack mentions --------------------------------------------

    def test_docker_mention_rejected(self):
        result = _classify_request("docker is configured")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    def test_postgres_mention_rejected(self):
        result = _classify_request("postgres connection string")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    def test_kubernetes_mention_rejected(self):
        result = _classify_request("kubernetes deployment")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    # --- Bare deployment / CI status (G1 fix patterns) ------------------

    def test_deploy_is_done_rejected(self):
        result = _classify_request("The deploy is done")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    def test_pipeline_is_green_rejected(self):
        result = _classify_request("CI/CD pipeline is green")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]


# =============================================================================
# 3-stage flow ordering
# =============================================================================


class TestClassificationOrdering:
    """Verify the 3-stage flow: persona prefix → project pre-check → persona rescue.

    Stage 1 detects persona intent. Stage 2 always runs and can still REJECT
    even when Stage 1 matched (Stage 3 must also find a persona category for
    the request to be accepted). These tests pin the documented ordering.
    """

    def test_remember_that_docker_is_rejected(self):
        """`Remember that` is NOT a persona prefix (no my/your/the user).

        Stage 1: `^remember\\s+(my|your|the user)` requires a pronoun —
        "Remember that docker..." has none, so persona_intent_matched=False.
        Stage 2: `docker` matches a project pattern → REJECT.
        """
        result = _classify_request("Remember that docker is configured")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    def test_remember_my_name_is_not_rejected(self):
        """`Remember my name` IS a persona prefix → Stage 1 catches it."""
        result = _classify_request("Remember my name is Cody")
        assert result["type"] != "project_knowledge"
        assert "REJECT" not in result["targets"]

    def test_project_keyword_without_persona_prefix_rejected(self):
        """A non-persona-prefixed project keyword still hits Stage 2."""
        # No persona prefix, but "deployed to" matches a Stage 2 pattern.
        result = _classify_request("deployed to production")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    def test_persona_prefix_plus_project_keyword_rejected_when_no_rescue(self):
        """Persona prefix + project keyword, no persona category → REJECT.

        The Stage 3 "rescue" only works when a persona category (identity,
        personality, user_preference, user_identity, workflow) also matches.
        "I should improve my deployment strategy" has "I should" (persona
        prefix) and "deployment" (project keyword) but no persona CATEGORY
        pattern — so it is REJECTED. This pins the documented behavior.
        """
        result = _classify_request("I should improve my deployment strategy")
        assert result["type"] == "project_knowledge"
        assert result["targets"] == ["REJECT"]

    def test_dual_match_rescue_workflow_with_deployment(self):
        """G3 rescue: persona prefix + project pattern + workflow category → ACCEPTED.

        'I should always verify before deployment' — 'I should' matches persona
        prefix, 'deployment' matches project pattern, but 'always verify' matches
        workflow category. The workflow category rescues it from rejection.
        """
        result = _classify_request("I should always verify before deployment")
        assert result["type"] != "project_knowledge", \
            f"Expected rescue (workflow), got project_knowledge: {result}"
        assert result["type"] == "workflow"

    def test_dual_match_reject_singular_deployment(self):
        """G3 reject: persona prefix + project pattern + no persona category → REJECTED.

        'I should improve my deployment strategy' — 'I should' matches persona
        prefix, 'deployment' matches project pattern, but NO persona category
        matches in Stage 3. Project content hiding behind persona prefix → REJECTED.
        """
        result = _classify_request("I should improve my deployment strategy")
        assert result["type"] == "project_knowledge"
        assert "REJECT" in result["targets"]


# =============================================================================
# _format_project_rejection() output
# =============================================================================


class TestFormatProjectRejection:
    """Pin the wording and structure of the project rejection message."""

    def _classification(self) -> dict:
        return {
            "type": "project_knowledge",
            "description": "Project-specific knowledge - must NOT enter agent memory",
        }

    def test_output_indicates_rejection(self):
        msg = _format_project_rejection("git push origin main", self._classification())
        # Accepts either emoji or word "Rejected"/"Reject".
        assert "Rejected" in msg or "⛔" in msg

    def test_output_mentions_project(self):
        msg = _format_project_rejection("git push origin main", self._classification())
        assert "project" in msg.lower()

    def test_output_points_to_project_history_add(self):
        msg = _format_project_rejection("git push origin main", self._classification())
        assert "project_history_add" in msg

    def test_output_points_to_experience_tool(self):
        msg = _format_project_rejection("git push origin main", self._classification())
        assert "experience(" in msg

    def test_output_includes_classification_label(self):
        msg = _format_project_rejection("git push origin main", self._classification())
        assert "project_knowledge" in msg

    def test_output_is_multiline(self):
        msg = _format_project_rejection("git push origin main", self._classification())
        assert "\n" in msg

    def test_long_content_truncated(self):
        """Requests over 80 chars are truncated with an ellipsis on line 1."""
        long_text = "x" * 200
        msg = _format_project_rejection(long_text, self._classification())
        first_line = msg.split("\n", 1)[0]
        assert "..." in first_line
        # First line should fit within reasonable bounds (well under 200 chars).
        assert len(first_line) < 120

    def test_short_content_not_truncated(self):
        msg = _format_project_rejection("git push", self._classification())
        first_line = msg.split("\n", 1)[0]
        assert "..." not in first_line


# =============================================================================
# REJECT target → graceful rejection (no "Unknown target" error)
# =============================================================================


class TestRejectHandlerIntegration:
    """REJECT must surface as a friendly message, not an exception/error.

    Before Phase 1, a project_knowledge classification fell through to
    `_execute_update()` which returned `{"error": "Unknown target: REJECT"}`.
    The reform routes REJECT through `_format_project_rejection()` instead.
    """

    def test_reject_target_triggers_friendly_message(self, mock_registry, mock_manager, temp_agent):
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")

            result = tool.invoke({"request": "git push origin main"})

            # Must NOT bubble up the old error
            assert "Unknown target" not in result
            assert "ERROR" not in result

            # Must be a friendly rejection
            assert "Rejected" in result or "⛔" in result
            assert "project_history_add" in result
            assert "experience(" in result

    def test_reject_with_rag_disabled_still_rejects(self, mock_registry, mock_manager, temp_agent):
        """RAG-disabled project content is REJECTED, not redirected."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")

            result = tool.invoke({"request": "deployed to production"})

            # The rejection message names the experience() tool as the alternative.
            assert "experience(" in result
            # Should NOT be a RAG redirect (which would say "Redirected to Knowledge").
            assert "Redirected to Knowledge" not in result
            assert "Rejected" in result or "⛔" in result

    def test_reject_does_not_create_memory_file(self, mock_registry, mock_manager, temp_agent):
        """REJECT must not leak project content into memories/."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")

            memories_dir = temp_agent / "memories"
            before = set(memories_dir.glob("*.md"))

            tool.invoke({"request": "merged the pull request"})

            after = set(memories_dir.glob("*.md"))
            assert before == after, "REJECT must not write to memories/"

    def test_reject_does_not_create_soul_file_change(self, mock_registry, mock_manager, temp_agent):
        """REJECT must not modify soul.md either."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")

            soul_file = temp_agent / "soul.md"
            before = soul_file.read_text()

            tool.invoke({"request": "Completed the build successfully"})

            after = soul_file.read_text()
            assert before == after, "REJECT must not modify soul.md"

    def test_reject_with_explicit_intent_remember(self, mock_registry, mock_manager, temp_agent):
        """G2 fix: intent='remember' must not bypass REJECT.

        _resolve_targets(intent='remember') returns ['memories'], dropping the
        REJECT sentinel. Without the G2 fix at line 634, this would write project
        content to memories/. This test proves the classification-based REJECT
        check catches it.
        """
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            memories_dir = temp_agent / "memories"
            before = set(memories_dir.glob("*.md"))
            result = tool.invoke({"intent": "remember", "content": "git push origin main"})
            assert "Reject" in result or "REJECT" in result, f"Expected rejection, got: {result}"
            # No memory file should be written
            assert before == set(memories_dir.glob("*.md"))

    def test_reject_with_explicit_target_memory(self, mock_registry, mock_manager, temp_agent):
        """G2 fix: target='memory' must not bypass REJECT.

        _resolve_targets(target='memory') returns ['memory'], dropping the REJECT
        sentinel. Without the G2 fix, this would write project content to memory.md.
        """
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            memory_file = temp_agent / "memory.md"
            before = memory_file.read_text()
            result = tool.invoke({"target": "memory", "content": "database migration done"})
            assert "Reject" in result or "REJECT" in result, f"Expected rejection, got: {result}"
            # memory.md should not be modified
            assert before == memory_file.read_text()

    def test_reject_with_explicit_intent_learn(self, mock_registry, mock_manager, temp_agent):
        """G2 fix: intent='learn' must not bypass REJECT.

        _resolve_targets(intent='learn') returns ['memories', 'memory'], dropping
        the REJECT sentinel. Same vulnerability as intent='remember'.
        """
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")
            memories_dir = temp_agent / "memories"
            memory_file = temp_agent / "memory.md"
            before_memories = set(memories_dir.glob("*.md"))
            before_memory = memory_file.read_text()
            result = tool.invoke({"intent": "learn", "content": "created a new config.py"})
            assert "Reject" in result or "REJECT" in result, f"Expected rejection, got: {result}"
            # No files should be modified
            assert before_memories == set(memories_dir.glob("*.md"))
            assert before_memory == memory_file.read_text()


# =============================================================================
# Compound requests with mixed accepted / rejected parts
# =============================================================================


class TestCompoundRequestPerPartRejection:
    """Compound requests (` AND `) process each part independently.

    With RAG disabled:
    - An accepted part updates the appropriate file.
    - A rejected part gets the project rejection message.
    - An all-rejected compound must not crash and must reject every part.
    """

    def test_persona_accepted_project_rejected(
        self, mock_registry, mock_manager, temp_agent
    ):
        """Part 1 (persona) accepted, part 2 (project) rejected."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")

            result = tool.invoke({
                "request": "Be more concise AND I deployed the new build to k8s"
            })

            # Part 1 processed (personality), part 2 rejected
            assert "personality" in result.lower()
            assert "REJECTED" in result
            assert "project_knowledge" in result
            assert "2 parts" in result

    def test_persona_accepted_completion_rejected(
        self, mock_registry, mock_manager, temp_agent
    ):
        """Part 1 (persona prefix) accepted, part 2 (completed the build) rejected."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")

            result = tool.invoke({
                "request": "I should be more methodical AND I completed the build"
            })

            assert "2 parts" in result
            assert "REJECTED" in result
            assert "project_knowledge" in result

    def test_all_rejected_compound_does_not_crash(
        self, mock_registry, mock_manager, temp_agent
    ):
        """Two project parts → both rejected, no crash, no file writes."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")

            memories_dir = temp_agent / "memories"
            memories_before = set(memories_dir.glob("*.md"))
            soul_before = (temp_agent / "soul.md").read_text()

            result = tool.invoke({
                "request": "Created a branch AND merged a pull request"
            })

            # Both parts rejected
            assert "2 parts" in result
            assert result.count("REJECTED") == 2
            assert "ERROR" not in result or "Rejected" in result

            # No file writes
            memories_after = set(memories_dir.glob("*.md"))
            assert memories_before == memories_after
            assert (temp_agent / "soul.md").read_text() == soul_before

    def test_all_accepted_compound_processes_both(
        self, mock_registry, mock_manager, temp_agent
    ):
        """Two persona parts → both accepted, no rejection."""
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            tool = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")

            result = tool.invoke({
                "request": "Be more concise AND remember my name is Cody"
            })

            assert "2 parts" in result
            assert "REJECTED" not in result
            # Both parts show their categories
            assert "personality" in result.lower()
            assert "identity" in result.lower()
