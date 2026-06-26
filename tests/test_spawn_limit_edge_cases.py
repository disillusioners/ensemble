"""Edge case tests for spawn limit logic (per-parent instance limits)."""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from daemon.manager import InstanceManager
from daemon.services.instance_lifecycle import InstanceLifecycleService


@pytest.fixture
def mock_config():
    """Create a mock config with known limits."""
    config = MagicMock()
    config.llm.base_url = "https://api.openai.com/v1"
    config.llm.api_key = "test-key"
    config.llm.model = "gpt-4"
    config.llm.model_vision = None
    config.llm.temperature = 0.7
    config.llm.request_timeout = 60
    config.llm.model_title = "gpt-4"
    config.daemon.host = "0.0.0.0"
    config.daemon.port = 8079
    config.limits.max_children_per_instance = 5  # Small limit for testing
    config.limits.instance_timeout_minutes = 60
    config.limits.graph_recursion_limit = 100
    config.limits.llm_concurrency = 10
    config.compaction.enabled = True
    config.compaction.threshold = 0.80
    config.compaction.recent_message_window = 10
    config.compaction.min_recent_window = 3
    config.compaction.context_window_overrides = {}
    config.compaction.context_window_default = 0
    config.compaction.target_ratio = 0.40
    config.compaction.summarization_model = ""
    config.compaction.min_messages_before_compaction = 10
    config.compaction.summarization_chunk_threshold = 0.60
    config.queue.llm_retry_transient_attempts = 10
    config.queue.llm_retry_timeout_attempts = 3
    config.queue.discard_on_startup = None
    config.persistence.db_path = ":memory:"
    config.persistence.checkpoint_interval = 1
    config.persistence.checkpoint_ttl_hours = 168
    config.persistence.checkpoint_cleanup_interval = 24
    config.persistence.max_instance_history = 300
    config.agents.directory = "./agents"
    config.services.worker_poll_interval = 0.5
    config.services.stale_task_recovery_interval = 60
    config.services.task_timeout_minutes = 60.0
    config.services.max_task_retries = 3
    config.services.task_retry_backoff_base = 60
    config.services.task_retry_backoff_max = 3600
    config.services.stale_task_cancel_grace_seconds = 10
    config.services.graph_timeout_minutes = 55.0
    config.job_system.default_max_retries = 3
    config.job_system.retry_backoff_base_seconds = 60
    config.job_system.retry_backoff_max_seconds = 3600
    config.job_system.retry_backoff_multiplier = 2.0
    config.job_system.dlq_enabled = True
    config.job_system.event_dispatch_enabled = True
    config.job_system.observer_health_check_interval_seconds = 300
    config.job_system.idempotency_key_ttl_hours = 24
    config.job_system.job_retry_scheduler_enabled = None
    config.mcp_pool.enabled = True
    config.mcp_pool.default_pool_size = 1
    config.mcp_pool.servers = {}
    config.mcp_pool.health_check_interval = 60
    config.mcp_pool.health_check_timeout = 5
    config.mcp_pool.tool_call_timeout = 120
    return config


@pytest.fixture
def mock_checkpointer():
    """Create a mock checkpointer."""
    return MagicMock()


@pytest.fixture
def mock_prompt_cache():
    """Create a mock prompt cache."""
    cache = MagicMock()
    cache.get.return_value = None
    return cache


@pytest.fixture
def mock_graph():
    """Create a mock compiled graph."""
    graph = MagicMock()
    graph.invoke = MagicMock(return_value={"messages": []})
    return graph


@pytest.fixture
def mock_instance_repository():
    """Create a mock instance repository."""
    repo = MagicMock()
    repo.get.return_value = MagicMock(instance_metadata={}, agent_id="developer")
    repo.count_children.return_value = 0
    return repo


class TestSpawnLimitEdgeCases:
    """Edge case tests for per-parent spawn limit logic."""

    def test_root_instance_bypasses_check_parent_id_none(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Root instances (parent_id=None) should bypass the check (no ValueError raised)."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            mock_config.limits.max_children_per_instance = 5
            mock_instance_repository.count_children.return_value = 0

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository

            with patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
                 patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
                 patch('daemon.manager.create_instance_tools', return_value=[]):

                # This should NOT raise ValueError
                instance_id, _ = manager.spawn_instance(
                    agent_id="developer",
                    parent_id=None,  # Root instance
                )

                # count_children should NOT be called for root instances
                mock_instance_repository.count_children.assert_not_called()

    def test_root_instance_bypasses_check_parent_id_empty_string(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Root instances (parent_id='') should bypass the check (no ValueError raised).

        Note: Empty string is falsy in Python, so `if parent_id:` evaluates to False.
        This means count_children is NOT called and the limit check is bypassed.
        """
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            mock_config.limits.max_children_per_instance = 5
            mock_instance_repository.count_children.return_value = 0
            # Empty string is falsy, so get_tree_root_id should NOT be called
            # But we mock it anyway to prevent MagicMock issues
            mock_instance_repository.get_tree_root_id.return_value = "fallback-root"

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository

            with patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
                 patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
                 patch('daemon.manager.create_instance_tools', return_value=[]):

                # This should NOT raise ValueError even with empty string
                # because empty string is falsy and bypasses the check
                instance_id, _ = manager.spawn_instance(
                    agent_id="developer",
                    parent_id="",  # Root instance (empty string is falsy)
                )

                # count_children should NOT be called for root instances (empty string is falsy)
                mock_instance_repository.count_children.assert_not_called()

    def test_parent_at_limit_raises_value_error(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Parent with children count >= max_children_per_instance should raise ValueError."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            mock_config.limits.max_children_per_instance = 5
            mock_instance_repository.count_children.return_value = 5  # At limit

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository

            with patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
                 patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
                 patch('daemon.manager.create_instance_tools', return_value=[]):

                with pytest.raises(ValueError) as exc_info:
                    manager.spawn_instance(
                        agent_id="developer",
                        parent_id="parent-instance",
                    )

                assert "Max children limit reached" in str(exc_info.value)

    def test_parent_below_limit_succeeds(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Parent with children count < max_children_per_instance should spawn successfully."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            mock_config.limits.max_children_per_instance = 5
            mock_instance_repository.count_children.return_value = 3  # Below limit
            mock_instance_repository.get_tree_root_id.return_value = "parent-instance"

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository

            with patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
                 patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
                 patch('daemon.manager.create_instance_tools', return_value=[]):

                # This should succeed
                instance_id, _ = manager.spawn_instance(
                    agent_id="developer",
                    parent_id="parent-instance",
                )

                assert instance_id is not None

    def test_error_message_includes_parent_id(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Error message should include the parent_id and limit number."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            mock_config.limits.max_children_per_instance = 10
            mock_instance_repository.count_children.return_value = 10  # At limit

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository

            with patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
                 patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
                 patch('daemon.manager.create_instance_tools', return_value=[]):

                with pytest.raises(ValueError) as exc_info:
                    manager.spawn_instance(
                        agent_id="developer",
                        parent_id="test-parent-123",
                    )

                error_msg = str(exc_info.value)
                assert "test-parent-123" in error_msg, f"Error should include parent_id, got: {error_msg}"
                assert "10" in error_msg, f"Error should include limit, got: {error_msg}"

    def test_count_children_called_with_correct_parent_id(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Verify count_children is called with the correct parent_id."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            mock_config.limits.max_children_per_instance = 5
            mock_instance_repository.count_children.return_value = 2  # Below limit
            mock_instance_repository.get_tree_root_id.return_value = "specific-parent-id"

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository

            with patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
                 patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
                 patch('daemon.manager.create_instance_tools', return_value=[]):

                manager.spawn_instance(
                    agent_id="developer",
                    parent_id="specific-parent-id",
                )

                # Verify count_children was called with the correct parent_id
                mock_instance_repository.count_children.assert_called_once_with("specific-parent-id")

    def test_edge_case_at_limit_minus_one(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Edge case: parent with (limit - 1) children should allow spawn."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            mock_config.limits.max_children_per_instance = 5
            mock_instance_repository.count_children.return_value = 4  # limit - 1
            mock_instance_repository.get_tree_root_id.return_value = "parent-instance"

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository

            with patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
                 patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
                 patch('daemon.manager.create_instance_tools', return_value=[]):

                # This should succeed (4 < 5)
                instance_id, _ = manager.spawn_instance(
                    agent_id="developer",
                    parent_id="parent-instance",
                )

                assert instance_id is not None

    def test_edge_case_at_limit(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Edge case: parent with exactly limit children should NOT allow spawn."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            mock_config.limits.max_children_per_instance = 5
            mock_instance_repository.count_children.return_value = 5  # Exactly at limit

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository

            with patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
                 patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
                 patch('daemon.manager.create_instance_tools', return_value=[]):

                with pytest.raises(ValueError) as exc_info:
                    manager.spawn_instance(
                        agent_id="developer",
                        parent_id="parent-instance",
                    )

                assert "Max children limit reached" in str(exc_info.value)

    def test_edge_case_above_limit(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Edge case: parent with above limit children should NOT allow spawn."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            mock_config.limits.max_children_per_instance = 5
            mock_instance_repository.count_children.return_value = 10  # Above limit

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository

            with patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
                 patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
                 patch('daemon.manager.create_instance_tools', return_value=[]):

                with pytest.raises(ValueError) as exc_info:
                    manager.spawn_instance(
                        agent_id="developer",
                        parent_id="parent-instance",
                    )

                assert "Max children limit reached" in str(exc_info.value)
