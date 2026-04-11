"""Tests for instructive error messages in spawn_instance validation.

Tests the error messages produced when invalid agent IDs are provided,
including skill detection, typo suggestions, and path traversal protection.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from daemon.api import validate_agent_id
from daemon.registry import AgentMetadata, AgentRegistry


class TestSkillDetectionInErrorMessage:
    """Test 1: Skill detection in error message."""

    @pytest.mark.skip(reason="Instructive error messages not yet implemented")
    def test_skill_not_agent_error_contains_skill_info(self) -> None:
        """When input is a skill (not an agent), error message should list agents with that skill."""
        # Create mock metadata
        mock_meta = MagicMock(spec=AgentMetadata)
        mock_meta.id = "opencode"
        mock_meta.system = False

        # Create mock registry
        mock_registry = MagicMock()
        mock_registry.get.return_value = None  # Agent not found
        mock_registry.find_skill.return_value = ["coder", "tester", "reviewer"]  # It's a skill
        mock_registry.list_all.return_value = [
            MagicMock(id="coder", system=False),
            MagicMock(id="tester", system=False),
            MagicMock(id="reviewer", system=False),
            MagicMock(id="_mother", system=True),  # System agent - should be excluded
        ]

        with patch("daemon.api.get_registry", return_value=mock_registry):
            with pytest.raises(HTTPException) as exc_info:
                validate_agent_id("opencode")

            assert exc_info.value.status_code == 404
            detail = exc_info.value.detail
            message = detail["message"]

            # Should contain skill detection message
            assert "is a skill, not an agent" in message
            assert "opencode" in message

            # Should list agents with this skill
            assert "coder" in message
            assert "tester" in message
            assert "reviewer" in message

            # Should NOT contain system agents
            assert "_mother" not in message

            # Should show available agents section
            assert "Available agents:" in message
            assert "coder" in message
            assert "tester" in message
            assert "reviewer" in message


class TestUnknownAgentName:
    """Test 2: Unknown agent name (not a skill)."""

    @pytest.mark.skip(reason="Instructive error messages not yet implemented")
    def test_unknown_agent_not_skill_error(self) -> None:
        """When input is not a skill and not found, error should say 'Agent not found'."""
        mock_registry = MagicMock()
        mock_registry.get.return_value = None  # Agent not found
        mock_registry.find_skill.return_value = []  # Not a skill either
        mock_registry.list_all.return_value = [
            MagicMock(id="coder", system=False),
            MagicMock(id="leader", system=False),
        ]

        with patch("daemon.api.get_registry", return_value=mock_registry):
            with pytest.raises(HTTPException) as exc_info:
                validate_agent_id("database")

            assert exc_info.value.status_code == 404
            detail = exc_info.value.detail
            message = detail["message"]

            # Should say agent not found
            assert "Agent not found" in message
            assert "database" in message

            # Should list available agents
            assert "Available agents:" in message
            assert "coder" in message
            assert "leader" in message

            # Should NOT mention skills
            assert "skill" not in message.lower()


class TestTypoSuggestion:
    """Test 3: Typo suggestion for close agent names."""

    @pytest.mark.skip(reason="Instructive error messages not yet implemented")
    def test_typo_suggests_close_match(self) -> None:
        """When input is close to an existing agent, suggest it."""
        mock_registry = MagicMock()
        mock_registry.get.return_value = None  # Agent not found
        mock_registry.find_skill.return_value = []  # Not a skill
        mock_registry.list_all.return_value = [
            MagicMock(id="coder", system=False),
            MagicMock(id="leader", system=False),
        ]

        with patch("daemon.api.get_registry", return_value=mock_registry):
            with pytest.raises(HTTPException) as exc_info:
                validate_agent_id("code")  # Typo for "coder"

            assert exc_info.value.status_code == 404
            detail = exc_info.value.detail
            message = detail["message"]

            # Should contain typo suggestion
            assert "Did you mean 'coder'?" in message


class TestPathTraversalProtection:
    """Test 4: Path traversal protection in find_skill."""

    def test_find_skill_rejects_path_traversal(self, tmp_path: Path) -> None:
        """find_skill should return empty list for paths with .., /, or \\."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        # Create a real registry
        registry = AgentRegistry(agents_dir)
        registry.discover()

        # These should all return empty lists
        assert registry.find_skill("../config") == []
        assert registry.find_skill("foo/bar") == []
        assert registry.find_skill("..") == []
        assert registry.find_skill("foo\\bar") == []
        assert registry.find_skill("foo/bar/../../../etc") == []

    def test_find_skill_normal_input_works(self, tmp_path: Path) -> None:
        """find_skill should work normally for valid skill names."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        # Create agent with a skill
        agent_dir = agents_dir / "coder"
        agent_dir.mkdir()
        skill_dir = agent_dir / "skills" / "coding"
        skill_dir.mkdir(parents=True)
        (skill_dir / "skill.md").write_text("# Coding skill")

        meta = {
            "id": "coder",
            "name": "Coder",
            "description": "Test coder",
            "icon": "🤖",
            "color": "accent-blue",
        }
        with open(agent_dir / "meta.json", "w") as f:
            json.dump(meta, f)

        registry = AgentRegistry(agents_dir)
        registry.discover()

        # Should find the skill
        result = registry.find_skill("coding")
        assert "coder" in result


class TestEmptyAgentListEdgeCase:
    """Test 5: Empty agent list edge case."""

    @pytest.mark.skip(reason="Instructive error messages not yet implemented")
    def test_empty_registry_shows_no_agents_message(self) -> None:
        """When no agents are registered, error should not show 'Available agents: .'"""
        mock_registry = MagicMock()
        mock_registry.get.return_value = None
        mock_registry.find_skill.return_value = []
        mock_registry.list_all.return_value = []  # Empty registry

        with patch("daemon.api.get_registry", return_value=mock_registry):
            with pytest.raises(HTTPException) as exc_info:
                validate_agent_id("nonexistent")

            assert exc_info.value.status_code == 404
            detail = exc_info.value.detail
            message = detail["message"]

            # Should say no agents are registered
            assert "No agents are currently registered" in message

            # Should NOT show empty list or malformed message
            assert "Available agents: ." not in message
            assert "Available agents:" not in message


class TestValidAgentId:
    """Test 6: Valid agent_id (happy path)."""

    def test_valid_agent_returns_tuple(self) -> None:
        """When agent exists, should return (agent_id, path) without raising."""
        mock_path = Path("/path/to/agent")
        mock_meta = MagicMock(spec=AgentMetadata)
        mock_meta.id = "coder"
        mock_meta.path = mock_path

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_meta

        with patch("daemon.api.get_registry", return_value=mock_registry):
            result = validate_agent_id("coder")

            assert result == ("coder", mock_path)
            mock_registry.get.assert_called_once_with("coder")


class TestManagerSpawnInstanceErrors:
    """Test 7: Manager spawn_instance raises ValueError with instructive messages.
    
    These tests verify that the error message construction logic in InstanceManager.spawn_instance
    produces the same instructive error messages as validate_agent_id.
    """

    @pytest.mark.skip(reason="Instructive error messages not yet implemented")
    def test_manager_skill_not_agent_raises_value_error(self) -> None:
        """spawn_instance should raise ValueError with skill info when agent is a skill."""
        from daemon.manager import InstanceManager
        from daemon.config import Config

        # Create minimal config - use plain MagicMock without spec
        config = MagicMock()
        config.persistence.db_path = ":memory:"
        config.persistence.checkpointer_db_path = ":memory:"
        config.llm.base_url = "https://api.example.com"
        config.llm.api_key = "test-key"
        config.llm.model = "test-model"
        config.llm.temperature = 0.7
        config.llm.request_timeout = 60
        config.limits.max_instances = 10
        config.limits.max_children_per_instance = 5
        config.limits.graph_recursion_limit = 1000
        config.queue.llm_max_retries = 3
        config.queue.discard_on_startup = False

        # Mock registry
        mock_registry = MagicMock()
        mock_registry.resolve_to_id.return_value = None  # Not a valid agent ID
        mock_registry.get.return_value = None  # Agent not found
        mock_registry.find_skill.return_value = ["coder", "tester"]  # It's a skill
        mock_registry.list_all.return_value = [
            MagicMock(id="coder", system=False),
            MagicMock(id="tester", system=False),
        ]

        with patch("daemon.manager.get_registry", return_value=mock_registry):
            with patch("daemon.manager.InstanceManager.__init__", lambda self, cfg: None):
                manager = InstanceManager(config)
                manager.config = config
                manager.instances = {}  # No active instances
                manager._checkpointer = None
                manager._loop = None

                with pytest.raises(ValueError) as exc_info:
                    manager.spawn_instance("opencode")

                message = str(exc_info.value)

                # Should mention skill
                assert "is a skill, not an agent" in message
                assert "opencode" in message

                # Should list agents with skill
                assert "coder" in message
                assert "tester" in message

                # Should list available agents
                assert "Available agents:" in message

    def test_manager_unknown_agent_raises_value_error(self) -> None:
        """spawn_instance should raise ValueError with 'Agent not found' for unknown agents."""
        from daemon.manager import InstanceManager
        from daemon.config import Config

        config = MagicMock()
        config.persistence.db_path = ":memory:"
        config.persistence.checkpointer_db_path = ":memory:"
        config.llm.base_url = "https://api.example.com"
        config.llm.api_key = "test-key"
        config.llm.model = "test-model"
        config.llm.temperature = 0.7
        config.llm.request_timeout = 60
        config.limits.max_instances = 10
        config.limits.max_children_per_instance = 5
        config.limits.graph_recursion_limit = 1000
        config.queue.llm_max_retries = 3
        config.queue.discard_on_startup = False

        mock_registry = MagicMock()
        mock_registry.resolve_to_id.return_value = None
        mock_registry.get.return_value = None
        mock_registry.find_skill.return_value = []  # Not a skill
        mock_registry.list_all.return_value = [
            MagicMock(id="coder", system=False),
        ]

        with patch("daemon.manager.get_registry", return_value=mock_registry):
            with patch("daemon.manager.InstanceManager.__init__", lambda self, cfg: None):
                manager = InstanceManager(config)
                manager.config = config
                manager.instances = {}
                manager._checkpointer = None
                manager._loop = None

                with pytest.raises(ValueError) as exc_info:
                    manager.spawn_instance("database")

                message = str(exc_info.value)

                # Should say agent not found
                assert "Agent not found" in message
                assert "database" in message

                # Should NOT mention skills
                assert "is a skill" not in message

    @pytest.mark.skip(reason="Instructive error messages not yet implemented")
    def test_manager_typo_suggests_correction(self) -> None:
        """spawn_instance should suggest close match for typos."""
        from daemon.manager import InstanceManager
        from daemon.config import Config

        config = MagicMock()
        config.persistence.db_path = ":memory:"
        config.persistence.checkpointer_db_path = ":memory:"
        config.llm.base_url = "https://api.example.com"
        config.llm.api_key = "test-key"
        config.llm.model = "test-model"
        config.llm.temperature = 0.7
        config.llm.request_timeout = 60
        config.limits.max_instances = 10
        config.limits.max_children_per_instance = 5
        config.limits.graph_recursion_limit = 1000
        config.queue.llm_max_retries = 3
        config.queue.discard_on_startup = False

        mock_registry = MagicMock()
        mock_registry.resolve_to_id.return_value = None
        mock_registry.get.return_value = None
        mock_registry.find_skill.return_value = []
        mock_registry.list_all.return_value = [
            MagicMock(id="coder", system=False),
        ]

        with patch("daemon.manager.get_registry", return_value=mock_registry):
            with patch("daemon.manager.InstanceManager.__init__", lambda self, cfg: None):
                manager = InstanceManager(config)
                manager.config = config
                manager.instances = {}
                manager._checkpointer = None
                manager._loop = None

                with pytest.raises(ValueError) as exc_info:
                    manager.spawn_instance("code")  # Typo for coder

                message = str(exc_info.value)

                # Should suggest coder
                assert "Did you mean 'coder'?" in message

    @pytest.mark.skip(reason="Instructive error messages not yet implemented")
    def test_manager_empty_registry_value_error(self) -> None:
        """spawn_instance with empty registry should show appropriate message."""
        from daemon.manager import InstanceManager
        from daemon.config import Config

        config = MagicMock()
        config.persistence.db_path = ":memory:"
        config.persistence.checkpointer_db_path = ":memory:"
        config.llm.base_url = "https://api.example.com"
        config.llm.api_key = "test-key"
        config.llm.model = "test-model"
        config.llm.temperature = 0.7
        config.llm.request_timeout = 60
        config.limits.max_instances = 10
        config.limits.max_children_per_instance = 5
        config.limits.graph_recursion_limit = 1000
        config.queue.llm_max_retries = 3
        config.queue.discard_on_startup = False

        mock_registry = MagicMock()
        mock_registry.resolve_to_id.return_value = None
        mock_registry.get.return_value = None
        mock_registry.find_skill.return_value = []
        mock_registry.list_all.return_value = []  # Empty registry

        with patch("daemon.manager.get_registry", return_value=mock_registry):
            with patch("daemon.manager.InstanceManager.__init__", lambda self, cfg: None):
                manager = InstanceManager(config)
                manager.config = config
                manager.instances = {}
                manager._checkpointer = None
                manager._loop = None

                with pytest.raises(ValueError) as exc_info:
                    manager.spawn_instance("nonexistent")

                message = str(exc_info.value)

                # Should say no agents registered
                assert "No agents are currently registered" in message

                # Should NOT show malformed Available agents message
                assert "Available agents: ." not in message


class TestErrorMessageConsistency:
    """Additional tests to ensure consistency between api.validate_agent_id and manager.spawn_instance."""

    @pytest.mark.skip(reason="Instructive error messages not yet implemented")
    def test_api_and_manager_skill_error_consistency(self) -> None:
        """API and manager should produce similar skill error messages."""
        # Get error from API
        mock_registry = MagicMock()
        mock_registry.get.return_value = None
        mock_registry.find_skill.return_value = ["agent1", "agent2"]
        mock_registry.list_all.return_value = [
            MagicMock(id="agent1", system=False),
            MagicMock(id="agent2", system=False),
        ]

        with patch("daemon.api.get_registry", return_value=mock_registry):
            try:
                validate_agent_id("some_skill")
            except HTTPException as e:
                api_message = e.detail["message"]

        # Get error from Manager
        from daemon.manager import InstanceManager
        from daemon.config import Config

        config = MagicMock()
        config.persistence.db_path = ":memory:"
        config.persistence.checkpointer_db_path = ":memory:"
        config.llm.base_url = "https://api.example.com"
        config.llm.api_key = "test-key"
        config.llm.model = "test-model"
        config.llm.temperature = 0.7
        config.llm.request_timeout = 60
        config.limits.max_instances = 10
        config.limits.max_children_per_instance = 5
        config.limits.graph_recursion_limit = 1000
        config.queue.llm_max_retries = 3
        config.queue.discard_on_startup = False

        mock_registry2 = MagicMock()
        mock_registry2.resolve_to_id.return_value = None
        mock_registry2.get.return_value = None
        mock_registry2.find_skill.return_value = ["agent1", "agent2"]
        mock_registry2.list_all.return_value = [
            MagicMock(id="agent1", system=False),
            MagicMock(id="agent2", system=False),
        ]

        with patch("daemon.manager.get_registry", return_value=mock_registry2):
            with patch("daemon.manager.InstanceManager.__init__", lambda self, cfg: None):
                manager = InstanceManager(config)
                manager.config = config
                manager.instances = {}
                manager._checkpointer = None
                manager._loop = None

                try:
                    manager.spawn_instance("some_skill")
                except ValueError as e:
                    manager_message = str(e)

        # Both should contain key phrases
        assert "is a skill, not an agent" in api_message
        assert "is a skill, not an agent" in manager_message
        assert "some_skill" in api_message
        assert "some_skill" in manager_message
        assert "agent1" in api_message
        assert "agent1" in manager_message
