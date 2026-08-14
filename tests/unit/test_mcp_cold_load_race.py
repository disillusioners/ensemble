"""Tests for MCP cold-load race condition fix.

Validates that ensure_mcp_preloaded() is awaited BEFORE _restore_instance()
is called when loading an instance from disk (cold-load path).

Bug: MCP tools not available when instance is cold-loaded from disk after
service restart. Root cause: get_instance() was sync while
ensure_mcp_preloaded() was async — LLM invoked before MCP tools finished loading.

Fix: get_instance() made async — awaits ensure_mcp_preloaded() BEFORE
_restore_instance() in cold-load path. In-memory fast path remains sync.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _make_mock_instance_meta(agent_id: str = "developer", instance_id: str = "test-instance"):
    """Create a mock instance metadata."""
    meta = MagicMock()
    meta.instance_id = instance_id
    meta.agent_id = agent_id
    meta.agent_dir = "/path/to/agent"
    meta.status = "running"
    meta.instance_metadata = {}
    meta.to_dict.return_value = {
        "instance_id": instance_id,
        "agent_id": agent_id,
        "status": "running",
    }
    return meta


def _make_mock_graph():
    """Create a mock CompiledStateGraph."""
    graph = MagicMock()
    graph.invoke.return_value = {"messages": []}
    return graph


@pytest.fixture
def mock_lifecycle_manager():
    """Create a mock manager for lifecycle service tests."""
    manager = MagicMock()
    manager.instances = {}  # Empty by default
    manager._instance_repository = MagicMock()
    manager.prompt_cache = MagicMock()
    manager._project_repository = MagicMock()
    manager._checkpointer = MagicMock()  # Checkpointer accessed via manager
    return manager


class TestColdLoadRaceConditionFix:
    """Test 1: Cold-load path awaits MCP preload before restore."""

    @pytest.mark.asyncio
    async def test_ensure_mcp_preloaded_called_before_restore(self, mock_lifecycle_manager):
        """Verify _restore_instance() is called AFTER ensure_mcp_preloaded() completes."""
        from daemon.services.instance_lifecycle import InstanceLifecycleService

        instance_id = "cold-load-instance"

        # Track call order
        call_order = []

        # Mock ensure_mcp_preloaded with a delay to ensure ordering
        async def delayed_preload(*args, **kwargs):
            call_order.append("ensure_mcp_preloaded_start")
            await asyncio.sleep(0.01)  # Small delay
            call_order.append("ensure_mcp_preloaded_end")
            return None

        # Mock _restore_instance
        mock_graph = _make_mock_graph()
        def track_restore(*args, **kwargs):
            call_order.append("_restore_instance_called")
            return mock_graph

        # Setup mocks
        mock_lifecycle_manager.ensure_mcp_preloaded = AsyncMock(side_effect=delayed_preload)
        mock_lifecycle_manager._instance_repository.get.return_value = _make_mock_instance_meta(
            instance_id=instance_id
        )

        # Create lifecycle service
        service = InstanceLifecycleService(mock_lifecycle_manager, MagicMock(), MagicMock())

        # Patch _restore_instance — async (production awaits it at
        # instance_lifecycle.get_instance); AsyncMock wraps the sync
        # tracker so the call-order side effect is preserved.
        service._restore_instance = AsyncMock(side_effect=track_restore)

        # Call get_instance - this should trigger cold-load
        await service.get_instance(instance_id)

        # Verify ordering: preload must complete before restore
        assert "ensure_mcp_preloaded_start" in call_order, f"Call order: {call_order}"
        assert "ensure_mcp_preloaded_end" in call_order, f"Call order: {call_order}"
        assert "_restore_instance_called" in call_order, f"Call order: {call_order}"
        assert call_order.index("ensure_mcp_preloaded_end") < call_order.index("_restore_instance_called"), \
            f"ensure_mcp_preloaded must complete before _restore_instance. Call order: {call_order}"

    @pytest.mark.asyncio
    async def test_ensure_mcp_preloaded_not_called_in_hot_path(self, mock_lifecycle_manager):
        """Verify ensure_mcp_preloaded is NOT called when instance is in memory (fast path)."""
        from daemon.services.instance_lifecycle import InstanceLifecycleService

        instance_id = "hot-instance"

        # Mock graph already in memory
        mock_graph = _make_mock_graph()

        # Setup mock manager with instance already loaded
        mock_lifecycle_manager.instances = {instance_id: (mock_graph, MagicMock())}
        mock_lifecycle_manager.ensure_mcp_preloaded = AsyncMock()

        service = InstanceLifecycleService(mock_lifecycle_manager, MagicMock(), MagicMock())

        # Call get_instance - this should hit hot path
        result = await service.get_instance(instance_id)

        # Verify ensure_mcp_preloaded was NOT called
        mock_lifecycle_manager.ensure_mcp_preloaded.assert_not_called()

        # Verify we got the graph from memory
        assert result == mock_graph


class TestMcpPreloadFailureGracefulDegradation:
    """Test 3: MCP preload failure — graceful degradation."""

    @pytest.mark.asyncio
    async def test_mcp_preload_failure_propagates(self, mock_lifecycle_manager):
        """Verify get_instance propagates MCP preload failure."""
        from daemon.services.instance_lifecycle import InstanceLifecycleService

        instance_id = "mcp-fail-instance"

        # Mock ensure_mcp_preloaded to raise an exception
        async def fail_preload(*args, **kwargs):
            raise RuntimeError("MCP server unavailable")

        # Setup mocks
        mock_lifecycle_manager.ensure_mcp_preloaded = AsyncMock(side_effect=fail_preload)
        mock_lifecycle_manager._instance_repository.get.return_value = _make_mock_instance_meta(
            instance_id=instance_id
        )

        service = InstanceLifecycleService(mock_lifecycle_manager, MagicMock(), MagicMock())

        # The current implementation propagates the error
        with pytest.raises(RuntimeError, match="MCP server unavailable"):
            await service.get_instance(instance_id)


class TestMcpPreloadSuccessToolsAvailable:
    """Test 4: MCP preload success — tools available."""

    @pytest.mark.asyncio
    async def test_mcp_preload_called_with_instance_id(self, mock_lifecycle_manager):
        """Verify ensure_mcp_preloaded is called with correct instance_id."""
        from daemon.services.instance_lifecycle import InstanceLifecycleService

        instance_id = "mcp-success-instance"

        # Mock _restore_instance
        mock_graph = _make_mock_graph()
        mock_lifecycle_manager.ensure_mcp_preloaded = AsyncMock()
        mock_lifecycle_manager._instance_repository.get.return_value = _make_mock_instance_meta(
            instance_id=instance_id
        )

        service = InstanceLifecycleService(mock_lifecycle_manager, MagicMock(), MagicMock())
        # _restore_instance is async in production (awaited in get_instance)
        service._restore_instance = AsyncMock(return_value=mock_graph)

        # Call get_instance
        result = await service.get_instance(instance_id)

        # Verify ensure_mcp_preloaded was called with correct instance_id
        mock_lifecycle_manager.ensure_mcp_preloaded.assert_called_once_with(instance_id)

        # Verify _restore_instance was called
        service._restore_instance.assert_called_once()

        # Verify we got a result
        assert result == mock_graph


class TestManagerGetInstanceAsync:
    """Test that manager.get_instance is async and awaits lifecycle service."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock config."""
        from daemon.config import Config, LLMConfig, LimitsConfig, PersistenceConfig, DaemonConfig, AgentsConfig
        return Config(
            llm=LLMConfig(
                base_url="https://api.openai.com/v1",
                api_key="test-key",
                model="gpt-4",
                temperature=0.7
            ),
            limits=LimitsConfig(
                max_instances=5,
                max_children_per_instance=3,
                instance_timeout_minutes=60,
                message_rate_limit=60
            ),
            persistence=PersistenceConfig(
                db_path=":memory:",
                checkpoint_interval=1,
                checkpoint_ttl_hours=168,
                checkpoint_cleanup_interval=24,
                max_instance_history=300
            ),
            daemon=DaemonConfig(host="0.0.0.0", port=8079),
            agents=AgentsConfig(directory="./agents")
        )

    @pytest.fixture
    def mock_instance_repository(self):
        """Create a mock instance repository."""
        mock_repo = MagicMock()
        mock_repo.create.return_value = MagicMock(instance_id="test-instance")
        mock_repo.get.return_value = None
        mock_repo.list.return_value = ([], 0)
        return mock_repo

    @pytest.mark.asyncio
    async def test_manager_get_instance_is_async(self, mock_config, mock_instance_repository):
        """Verify InstanceManager.get_instance is an async method."""
        import inspect
        from daemon.manager import InstanceManager

        # Verify get_instance is async
        assert inspect.iscoroutinefunction(InstanceManager.get_instance), \
            "InstanceManager.get_instance should be async"

    @pytest.mark.skip(reason="QUARANTINED: pre-existing SQLite DROP CONSTRAINT failure in migration 20260714_000001 (dual-driver issue, predates PM domain-access; see .agents/tester/QUARANTINE.md)")
    @pytest.mark.asyncio
    async def test_manager_get_instance_delegates_to_lifecycle_service(self, mock_config, mock_instance_repository):
        """Verify manager.get_instance awaits lifecycle service's get_instance."""
        from daemon.manager import InstanceManager

        mock_graph = _make_mock_graph()

        with patch('daemon.manager.PromptCache', return_value=MagicMock()):
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager._lifecycle_service.get_instance = AsyncMock(return_value=mock_graph)

            result = await manager.get_instance("test-instance")

            manager._lifecycle_service.get_instance.assert_called_once_with("test-instance")
            assert result == mock_graph
