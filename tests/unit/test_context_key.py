"""Tests for CONTEXT_KEY feature in instance lifecycle.

Tests cover:
- append_context_key(): standalone function unit tests
- spawn_instance(): verifies append_context_key is called correctly
- _restore_instance(): verifies append_context_key is called correctly
"""

import pytest
from unittest.mock import MagicMock, patch

from daemon.services.instance_lifecycle import append_context_key, InstanceLifecycleService


# =============================================================================
# Part A: Function Unit Tests
# =============================================================================

class TestAppendContextKey:
    """Tests for append_context_key standalone function."""

    def test_root_instance_uses_instance_id(self):
        """When parent_id=None, the function should use instance_id as root_id."""
        # Setup
        system_prompt = "You are a helpful assistant."
        instance_id = "instance-123"
        instance_repository = MagicMock()

        # Execute
        result = append_context_key(system_prompt, instance_id, instance_repository, parent_id=None)

        # Verify
        assert "CONTEXT_KEY: instance-123" in result
        assert system_prompt in result  # Original prompt preserved
        # get_tree_root_id should NOT be called when parent_id is None
        instance_repository.get_tree_root_id.assert_not_called()

    def test_child_instance_uses_tree_root(self):
        """When parent_id is set, should call get_tree_root_id and use returned root ID."""
        # Setup
        system_prompt = "You are a helpful assistant."
        instance_id = "child-instance-456"
        instance_repository = MagicMock()
        instance_repository.get_tree_root_id.return_value = "root-instance-000"

        # Execute
        result = append_context_key(
            system_prompt, instance_id, instance_repository, parent_id="parent-789"
        )

        # Verify
        assert "CONTEXT_KEY: root-instance-000" in result
        # Should call get_tree_root_id with the parent_id
        instance_repository.get_tree_root_id.assert_called_once_with("parent-789")

    def test_grandchild_traversal(self):
        """Multi-hop scenario: get_tree_root_id returns topmost ancestor even for intermediate nodes."""
        # Setup
        system_prompt = "You are a helpful assistant."
        instance_id = "grandchild-999"
        instance_repository = MagicMock()
        # Simulate: grandchild -> child -> root, so get_tree_root_id(child) returns root
        instance_repository.get_tree_root_id.return_value = "topmost-root-000"

        # Execute - simulating a grandchild spawned under a child
        result = append_context_key(
            system_prompt, instance_id, instance_repository, parent_id="child-xyz"
        )

        # Verify
        assert "CONTEXT_KEY: topmost-root-000" in result
        # The function calls get_tree_root_id on the parent to find the actual root
        instance_repository.get_tree_root_id.assert_called_once_with("child-xyz")

    def test_traversal_returns_none_fallback(self):
        """When get_tree_root_id() returns None, fallback to using parent_id as root."""
        # Setup
        system_prompt = "You are a helpful assistant."
        instance_id = "instance-abc"
        instance_repository = MagicMock()
        instance_repository.get_tree_root_id.return_value = None  # Tree root not found

        # Execute
        result = append_context_key(
            system_prompt, instance_id, instance_repository, parent_id="orphaned-parent"
        )

        # Verify
        # Fallback should use parent_id when get_tree_root_id returns None
        assert "CONTEXT_KEY: orphaned-parent" in result
        # get_tree_root_id was called but returned None, so fallback was used
        instance_repository.get_tree_root_id.assert_called_once_with("orphaned-parent")

    def test_prompt_format(self):
        """Verify exact format of appended context section."""
        # Setup
        system_prompt = "You are a helpful assistant."
        instance_id = "test-instance"
        instance_repository = MagicMock()
        instance_repository.get_tree_root_id.return_value = "expected-root-id"

        # Execute
        result = append_context_key(
            system_prompt, instance_id, instance_repository, parent_id="some-parent"
        )

        # Verify exact format matches the implementation
        expected_suffix = "\n---\n\n## Context Key\n\nCONTEXT_KEY: expected-root-id\n"
        assert result == system_prompt + expected_suffix
        assert result.endswith(expected_suffix)


# =============================================================================
# Part B: Injection Site Tests
# =============================================================================

class TestContextKeyInjection:
    """Tests verifying append_context_key is called at correct injection sites."""

    def test_spawn_instance_injects_context_key(self):
        """Verify spawn_instance() calls append_context_key with correct args."""
        # Setup mocks
        mock_manager = MagicMock()
        mock_cancellation_service = MagicMock()
        mock_instance_repository = MagicMock()
        mock_project_repository = MagicMock()

        # Configure manager mock
        mock_manager._instance_repository = mock_instance_repository
        mock_instance_repository.count_children.return_value = 0  # Below limit
        mock_manager._project_repository = mock_project_repository
        mock_manager._engine = MagicMock()
        mock_manager._live_hub = MagicMock()
        mock_manager._checkpointer = None
        mock_manager._compactor = None
        mock_manager._notification_broadcaster = MagicMock()
        mock_manager.instances = {}
        mock_manager.prompt_cache = MagicMock()
        mock_manager._request_registry = MagicMock()
        mock_manager._graph_tasks = {}
        mock_manager._watcher_repo = MagicMock()
        mock_manager._queue_repository = MagicMock()

        # Mock config
        mock_config = MagicMock()
        mock_config.limits.max_instances = 100
        mock_config.limits.max_children_per_instance = 50
        mock_config.limits.graph_recursion_limit = 1000
        mock_config.queue.llm_retry_transient_attempts = 3
        mock_config.queue.llm_retry_timeout_attempts = 2
        mock_config.llm.base_url = None
        mock_config.llm.api_key = "test-key"
        mock_config.llm.model = "gpt-4"
        mock_config.llm.model_vision = False
        mock_config.llm.temperature = 0.7
        mock_config.llm.request_timeout = 60
        mock_manager.config = mock_config

        # Create service
        service = InstanceLifecycleService(mock_manager, mock_cancellation_service)

        # Mock all dependencies to avoid actual logic execution
        with patch("daemon.services.instance_lifecycle.get_registry") as mock_get_registry, \
             patch("daemon.services.instance_lifecycle.append_context_key") as mock_append_context_key, \
             patch("daemon.manager.load_and_cache_prompt") as mock_load_prompt, \
             patch("daemon.manager.build_instance_graph") as mock_build_graph, \
             patch("daemon.manager.create_instance_tools") as mock_create_tools, \
             patch("sqlmodel.Session") as mock_session:

            # Configure mocks
            mock_registry = MagicMock()
            mock_metadata = MagicMock()
            mock_metadata.path = "/agents/test"
            mock_registry.get.return_value = mock_metadata
            mock_registry.resolve_to_id.return_value = "test-agent"
            mock_get_registry.return_value = mock_registry

            mock_load_prompt.return_value = ("You are a test agent.", 10)
            mock_create_tools.return_value = []
            mock_build_graph.return_value = MagicMock()
            mock_append_context_key.return_value = "You are a test agent."  # Return modified prompt

            # Mock Session context manager to prevent actual DB operations
            mock_session_instance = MagicMock()
            # Set up the parent mock that session.get() returns
            mock_parent = MagicMock()
            mock_parent.children = "[]"  # Valid JSON array
            mock_session_instance.get.return_value = mock_parent
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_session_instance)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)

            # Execute - call spawn_instance with a parent_id
            test_parent_id = "parent-123"
            service.spawn_instance(agent_id="test", parent_id=test_parent_id)

            # Verify append_context_key was called with the parent_id
            mock_append_context_key.assert_called_once()
            call_kwargs = mock_append_context_key.call_args.kwargs
            assert call_kwargs.get("parent_id") == test_parent_id, \
                f"Expected parent_id='{test_parent_id}', got {call_kwargs.get('parent_id')}"

    def test_restore_instance_injects_context_key(self):
        """Verify _restore_instance() calls append_context_key with meta.parent_id."""
        # Setup mocks
        mock_manager = MagicMock()
        mock_cancellation_service = MagicMock()
        mock_instance_repository = MagicMock()
        mock_project_repository = MagicMock()

        # Configure manager mock
        mock_manager._instance_repository = mock_instance_repository
        mock_manager._project_repository = mock_project_repository
        mock_manager._engine = MagicMock()
        mock_manager._live_hub = MagicMock()
        mock_manager._checkpointer = None
        mock_manager._compactor = None
        mock_manager.instances = {}
        mock_manager.prompt_cache = MagicMock()

        # Mock config
        mock_config = MagicMock()
        mock_config.queue.llm_retry_transient_attempts = 3
        mock_config.queue.llm_retry_timeout_attempts = 2
        mock_config.llm.base_url = None
        mock_config.llm.api_key = "test-key"
        mock_config.llm.model = "gpt-4"
        mock_config.llm.model_vision = False
        mock_config.llm.temperature = 0.7
        mock_config.llm.request_timeout = 60
        mock_config.limits.graph_recursion_limit = 1000
        mock_manager.config = mock_config

        # Create service
        service = InstanceLifecycleService(mock_manager, mock_cancellation_service)

        # Create mock Instance metadata with parent_id
        mock_meta = MagicMock()
        mock_meta.instance_id = "restore-instance-789"
        mock_meta.agent_id = "test-agent"
        mock_meta.agent_dir = "/agents/test"
        mock_meta.parent_id = "meta-parent-456"  # This is what we want to verify
        mock_meta.instance_metadata = {"mcp_tool_names": []}

        # Mock all dependencies
        with patch("daemon.services.instance_lifecycle.append_context_key") as mock_append_context_key, \
             patch("daemon.services.instance_lifecycle.get_registry") as mock_get_registry, \
             patch("daemon.manager.load_and_cache_prompt") as mock_load_prompt, \
             patch("daemon.manager.build_instance_graph") as mock_build_graph, \
             patch("daemon.manager.create_instance_tools") as mock_create_tools:

            # Configure mocks
            mock_registry = MagicMock()
            mock_metadata = MagicMock()
            mock_metadata.llm_model = None
            mock_registry.get.return_value = mock_metadata
            mock_get_registry.return_value = mock_registry

            mock_load_prompt.return_value = ("You are a test agent.", 10)
            mock_create_tools.return_value = []
            mock_build_graph.return_value = MagicMock()
            mock_append_context_key.return_value = "You are a test agent."

            # Execute - call _restore_instance
            service._restore_instance("restore-instance-789", mock_meta)

            # Verify append_context_key was called with meta.parent_id
            mock_append_context_key.assert_called_once()
            call_kwargs = mock_append_context_key.call_args.kwargs
            assert call_kwargs.get("parent_id") == "meta-parent-456", \
                f"Expected parent_id='meta-parent-456', got {call_kwargs.get('parent_id')}"
