"""Integration tests for scheduler API endpoints."""

import pytest
import pytest_asyncio
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime, timezone
import sqlite3
import tempfile
import os

import httpx
from fastapi import FastAPI

# Import the app and manager directly
from daemon import api as api_module
from daemon.models.source import SourceStatus


# ==================== Fixtures ====================


@pytest_asyncio.fixture
async def mock_manager():
    """Create a mock InstanceManager with scheduler support."""
    manager = Mock()

    # Basic instance manager mocks
    manager.spawn_instance_with_mcp = AsyncMock(return_value="test-instance-id")
    manager.get_instance = AsyncMock()
    manager.send_message = Mock(return_value="Test response")
    manager.terminate_instance = Mock(return_value=True)
    manager.list_instances = Mock(return_value=([], 0))
    manager.get_instance_info = Mock()
    manager.enqueue_message = AsyncMock()
    manager.get_messages = AsyncMock(return_value=[])
    # Phase 3: routers check manager.is_write_paused; Mock auto-attr is truthy → 503.
    manager.is_write_paused = False
    
    # Set up temp SQLite database
    temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    temp_db_path = temp_db.name
    temp_db.close()
    conn = sqlite3.connect(temp_db_path)
    
    # Create source tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_configs (
            source_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            name TEXT NOT NULL,
            config TEXT NOT NULL,
            credentials TEXT,
            enabled BOOLEAN DEFAULT TRUE,
            status TEXT DEFAULT 'stopped',
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS instance_mappings (
            mapping_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            external_user_id TEXT NOT NULL,
            agent_instance_id TEXT NOT NULL,
            agent_dir TEXT NOT NULL,
            metadata TEXT,
            last_message_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_id, external_user_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schedule_executions (
            execution_id TEXT PRIMARY KEY,
            schedule_id TEXT NOT NULL,
            triggered_at TEXT NOT NULL,
            instance_id TEXT,
            status TEXT NOT NULL,
            error_message TEXT,
            completed_at TEXT,
            FOREIGN KEY (schedule_id) REFERENCES source_configs(source_id)
        )
    """)
    conn.commit()
    manager.conn = conn
    manager._temp_db_path = temp_db_path
    
    # Create mock source repository
    mock_repo = Mock()
    mock_repo.list_source_configs = Mock(return_value=[])
    mock_repo.get_source_config = Mock(return_value=None)
    mock_repo.create_source_config = Mock()
    mock_repo.update_source_config = Mock()
    mock_repo.delete_source_config = Mock(return_value=True)
    mock_repo.list_schedule_executions = Mock(return_value=[])
    mock_repo.record_execution_start = Mock()
    mock_repo.record_execution_complete = Mock()
    mock_repo.get_latest_execution = Mock(return_value=None)
    manager._source_repository = mock_repo
    
    # Mock source_registry
    manager.source_registry = None
    
    yield manager
    
    # Cleanup
    try:
        conn.close()
    except Exception:
        pass
    try:
        os.unlink(temp_db_path)
    except Exception:
        pass


@pytest.fixture
def app_with_mock_manager(mock_manager):
    """Create FastAPI app with mocked manager."""
    # Import app and set manager on app.state (Phase 3: routers use request.app.state.manager)
    from daemon.api import app
    app.state.manager = mock_manager
    app.state.start_time = 1000.0
    return mock_manager


@pytest_asyncio.fixture
async def client(app_with_mock_manager):
    """Create async test client."""
    from daemon.api import app
    
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api") as ac:
        yield ac


def create_scheduler_source(source_id: str, name: str, schedule_config: dict) -> Mock:
    """Helper to create a mock scheduler source config."""
    source = Mock()
    source.source_id = source_id
    source.source_type = "scheduler"
    source.name = name
    source.config = schedule_config  # Pass dict directly (not JSON string)
    source.credentials = {}
    source.enabled = True
    source.status = "stopped"
    source.error_message = None
    source.created_at = "2024-01-01T00:00:00+00:00"
    source.updated_at = "2024-01-01T00:00:00+00:05"
    return source


def create_execution(execution_id: str, schedule_id: str, status: str = "completed") -> Mock:
    """Helper to create a mock schedule execution."""
    execution = Mock()
    execution.execution_id = execution_id
    execution.schedule_id = schedule_id
    execution.triggered_at = "2024-01-01T09:00:00+00:00"
    execution.instance_id = "instance-123"
    execution.status = status
    execution.error_message = None
    execution.completed_at = "2024-01-01T09:00:05+00:00"
    return execution


# ==================== GET /schedules Tests ====================


class TestListSchedules:
    """Tests for GET /api/schedules endpoint."""

    @pytest.mark.asyncio
    async def test_list_schedules_empty(self, client, mock_manager):
        """Test listing schedules when none exist."""
        mock_manager._source_repository.list_source_configs = Mock(return_value=[])
        
        response = await client.get("/schedules")
        
        assert response.status_code == 200
        data = response.json()
        assert "schedules" in data
        assert data["schedules"] == []

    @pytest.mark.asyncio
    async def test_list_schedules_returns_only_schedulers(self, client, mock_manager):
        """Test that only scheduler-type sources are returned."""
        # Create mixed source types
        telegram_source = Mock()
        telegram_source.source_id = "telegram-1"
        telegram_source.source_type = "telegram"
        telegram_source.name = "Telegram Bot"
        telegram_source.config = "{}"
        telegram_source.enabled = True
        telegram_source.status = "running"
        telegram_source.error_message = None
        telegram_source.created_at = "2024-01-01T00:00:00+00:00"
        telegram_source.updated_at = "2024-01-01T00:00:00+00:00"
        
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Daily Report",
            {"schedule": "0 9 * * *", "agent": "./agents/developer", "message": "Report"}
        )
        scheduler_source.status = "running"
        
        mock_manager._source_repository.list_source_configs = Mock(
            return_value=[telegram_source, scheduler_source]
        )
        
        response = await client.get("/schedules")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["schedules"]) == 1
        assert data["schedules"][0]["id"] == "scheduler-1"

    @pytest.mark.asyncio
    async def test_list_schedules_multiple_schedulers(self, client, mock_manager):
        """Test listing multiple scheduler sources."""
        scheduler1 = create_scheduler_source(
            "cron-schedule",
            "Cron Job",
            {"schedule": "0 9 * * 1-5", "agent": "./agents/developer", "message": "Weekday report"}
        )
        scheduler2 = create_scheduler_source(
            "interval-schedule",
            "Interval Job",
            {"interval_seconds": 300, "agent": "./agents/developer", "message": "Every 5 min"}
        )
        scheduler3 = create_scheduler_source(
            "onetime-schedule",
            "One-time Job",
            {"run_at": "2025-12-25T10:00:00Z", "agent": "./agents/developer", "message": "Christmas"}
        )
        
        mock_manager._source_repository.list_source_configs = Mock(
            return_value=[scheduler1, scheduler2, scheduler3]
        )
        
        response = await client.get("/schedules")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["schedules"]) == 3
        
        schedule_ids = [s["id"] for s in data["schedules"]]
        assert "cron-schedule" in schedule_ids
        assert "interval-schedule" in schedule_ids
        assert "onetime-schedule" in schedule_ids


# ==================== POST /schedules/{id}/trigger Tests ====================


class TestTriggerSchedule:
    """Tests for POST /api/schedules/{schedule_id}/trigger endpoint."""

    @pytest.mark.asyncio
    async def test_trigger_schedule_not_found(self, client, mock_manager):
        """Test triggering a non-existent schedule."""
        mock_manager._source_repository.get_source_config = Mock(return_value=None)
        
        response = await client.post("/schedules/nonexistent/trigger")
        
        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["code"] == "SOURCE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_trigger_non_scheduler_source(self, client, mock_manager):
        """Test that triggering a non-scheduler source returns error."""
        telegram_source = Mock()
        telegram_source.source_id = "telegram-1"
        telegram_source.source_type = "telegram"
        
        mock_manager._source_repository.get_source_config = Mock(return_value=telegram_source)
        
        response = await client.post("/schedules/telegram-1/trigger")
        
        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["code"] == "INVALID_REQUEST"
        assert "not a scheduler" in data["detail"]["message"]

    @pytest.mark.asyncio
    async def test_trigger_schedule_registry_not_available(self, client, mock_manager):
        """Test triggering when source registry is not available."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        mock_manager.source_registry = None
        
        response = await client.post("/schedules/scheduler-1/trigger")
        
        assert response.status_code == 503
        data = response.json()
        assert data["detail"]["code"] == "INTERNAL_ERROR"

    @pytest.mark.asyncio
    async def test_trigger_schedule_adapter_not_running(self, client, mock_manager):
        """Test triggering when adapter is not in registry."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        
        # Registry exists but adapter not found
        mock_registry = Mock()
        mock_registry.get = Mock(return_value=None)
        mock_manager.source_registry = mock_registry
        
        response = await client.post("/schedules/scheduler-1/trigger")
        
        assert response.status_code == 503
        data = response.json()
        assert data["detail"]["code"] == "INTERNAL_ERROR"
        assert "not running" in data["detail"]["message"]

    @pytest.mark.asyncio
    async def test_trigger_schedule_success(self, client, mock_manager):
        """Test successful schedule trigger."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        
        # Create mock adapter
        mock_adapter = Mock()
        mock_adapter.manual_trigger = AsyncMock(return_value="exec-123")
        
        mock_registry = Mock()
        mock_registry.get = Mock(return_value=mock_adapter)
        mock_manager.source_registry = mock_registry
        
        response = await client.post("/schedules/scheduler-1/trigger")
        
        assert response.status_code == 200
        data = response.json()
        assert data["execution_id"] == "exec-123"
        assert data["schedule_id"] == "scheduler-1"
        assert data["message"] == "Schedule triggered successfully"
        
        # Verify adapter was called
        mock_adapter.manual_trigger.assert_called_once()
        
        # Note: Execution recording is now handled by the scheduler's execution_callback,
        # not by the API directly. This avoids duplicate records.

    @pytest.mark.asyncio
    async def test_trigger_schedule_adapter_error(self, client, mock_manager):
        """Test handling adapter error during trigger."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        
        # Create mock adapter that raises error
        mock_adapter = Mock()
        mock_adapter.manual_trigger = AsyncMock(side_effect=RuntimeError("Adapter error"))
        
        mock_registry = Mock()
        mock_registry.get = Mock(return_value=mock_adapter)
        mock_manager.source_registry = mock_registry
        
        response = await client.post("/schedules/scheduler-1/trigger")
        
        assert response.status_code == 500
        data = response.json()
        assert data["detail"]["code"] == "INTERNAL_ERROR"
        assert "Failed to trigger schedule" in data["detail"]["message"]


# ==================== GET /schedules/{id}/executions Tests ====================


class TestGetScheduleExecutions:
    """Tests for GET /api/schedules/{schedule_id}/executions endpoint."""

    @pytest.mark.asyncio
    async def test_get_executions_schedule_not_found(self, client, mock_manager):
        """Test getting executions for non-existent schedule."""
        mock_manager._source_repository.get_source_config = Mock(return_value=None)
        
        response = await client.get("/schedules/nonexistent/executions")
        
        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["code"] == "SOURCE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_get_executions_non_scheduler_source(self, client, mock_manager):
        """Test that getting executions for non-scheduler returns error."""
        telegram_source = Mock()
        telegram_source.source_id = "telegram-1"
        telegram_source.source_type = "telegram"
        
        mock_manager._source_repository.get_source_config = Mock(return_value=telegram_source)
        
        response = await client.get("/schedules/telegram-1/executions")
        
        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["code"] == "INVALID_REQUEST"

    @pytest.mark.asyncio
    async def test_get_executions_empty(self, client, mock_manager):
        """Test getting executions when none exist."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        mock_manager._source_repository.list_schedule_executions = Mock(return_value=[])
        
        response = await client.get("/schedules/scheduler-1/executions")
        
        assert response.status_code == 200
        data = response.json()
        assert "executions" in data
        assert data["executions"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_get_executions_with_data(self, client, mock_manager):
        """Test getting executions with data."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        
        # Create mock executions
        exec1 = create_execution("exec-1", "scheduler-1", "completed")
        exec2 = create_execution("exec-2", "scheduler-1", "completed")
        exec3 = create_execution("exec-3", "scheduler-1", "failed")
        exec3.error_message = "Something went wrong"
        
        mock_manager._source_repository.list_schedule_executions = Mock(
            return_value=[exec1, exec2, exec3]
        )
        
        response = await client.get("/schedules/scheduler-1/executions")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["executions"]) == 3
        assert data["total"] == 3
        
        # Verify execution data
        execution_ids = [e["execution_id"] for e in data["executions"]]
        assert "exec-1" in execution_ids
        assert "exec-2" in execution_ids
        assert "exec-3" in execution_ids

    @pytest.mark.asyncio
    async def test_get_executions_with_pagination(self, client, mock_manager):
        """Test getting executions with limit and offset."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        
        # Create mock executions
        executions = [create_execution(f"exec-{i}", "scheduler-1") for i in range(5)]
        
        # Mock should return limited results
        mock_manager._source_repository.list_schedule_executions = Mock(
            return_value=executions[2:4]  # Simulating offset=2, limit=2
        )
        
        response = await client.get("/schedules/scheduler-1/executions?limit=2&offset=2")
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify pagination params were passed correctly
        mock_manager._source_repository.list_schedule_executions.assert_called_once_with(
            schedule_id="scheduler-1",
            limit=2,
            offset=2
        )

    @pytest.mark.asyncio
    async def test_get_executions_default_pagination(self, client, mock_manager):
        """Test that default pagination values are applied."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        mock_manager._source_repository.list_schedule_executions = Mock(return_value=[])
        
        response = await client.get("/schedules/scheduler-1/executions")
        
        assert response.status_code == 200
        
        # Verify default values (limit=100, offset=0)
        mock_manager._source_repository.list_schedule_executions.assert_called_once_with(
            schedule_id="scheduler-1",
            limit=100,
            offset=0
        )

    @pytest.mark.asyncio
    async def test_get_executions_limit_clamping(self, client, mock_manager):
        """Test that limit is clamped to valid range."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        mock_manager._source_repository.list_schedule_executions = Mock(return_value=[])
        
        # Test limit > 1000 (should be clamped to 1000)
        response = await client.get("/schedules/scheduler-1/executions?limit=5000")
        assert response.status_code == 200
        mock_manager._source_repository.list_schedule_executions.assert_called_with(
            schedule_id="scheduler-1",
            limit=1000,  # Clamped
            offset=0
        )
        
        # Reset mock
        mock_manager._source_repository.list_schedule_executions.reset_mock()
        
        # Test limit < 1 (should be clamped to 1)
        response = await client.get("/schedules/scheduler-1/executions?limit=0")
        assert response.status_code == 200
        mock_manager._source_repository.list_schedule_executions.assert_called_with(
            schedule_id="scheduler-1",
            limit=1,  # Clamped
            offset=0
        )

    @pytest.mark.asyncio
    async def test_get_executions_offset_clamping(self, client, mock_manager):
        """Test that offset is clamped to valid range."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        mock_manager._source_repository.list_schedule_executions = Mock(return_value=[])
        
        # Test negative offset (should be clamped to 0)
        response = await client.get("/schedules/scheduler-1/executions?offset=-5")
        assert response.status_code == 200
        mock_manager._source_repository.list_schedule_executions.assert_called_with(
            schedule_id="scheduler-1",
            limit=100,
            offset=0  # Clamped
        )

    @pytest.mark.asyncio
    async def test_get_executions_with_different_statuses(self, client, mock_manager):
        """Test getting executions with various status values."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        
        # Create executions with different statuses
        triggered_exec = create_execution("exec-triggered", "scheduler-1", "triggered")
        triggered_exec.instance_id = None
        triggered_exec.completed_at = None
        
        completed_exec = create_execution("exec-completed", "scheduler-1", "completed")
        
        failed_exec = create_execution("exec-failed", "scheduler-1", "failed")
        failed_exec.error_message = "Task failed: timeout"
        failed_exec.completed_at = "2024-01-01T09:00:02+00:00"
        
        mock_manager._source_repository.list_schedule_executions = Mock(
            return_value=[triggered_exec, completed_exec, failed_exec]
        )
        
        response = await client.get("/schedules/scheduler-1/executions")
        
        assert response.status_code == 200
        data = response.json()
        
        # Find and verify each status
        statuses = {e["execution_id"]: e["status"] for e in data["executions"]}
        assert statuses["exec-triggered"] == "triggered"
        assert statuses["exec-completed"] == "completed"
        assert statuses["exec-failed"] == "failed"
        
        # Verify error message is present for failed execution
        failed_data = next(e for e in data["executions"] if e["execution_id"] == "exec-failed")
        assert failed_data["error_message"] == "Task failed: timeout"


# ==================== PUT /schedules/{id} Tests ====================


class TestUpdateSchedule:
    """Tests for PUT /api/schedules/{schedule_id} endpoint."""

    @pytest.mark.asyncio
    async def test_update_schedule_name(self, client, mock_manager):
        """Test updating schedule name only."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Old Name",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)

        # Mock updated source with new name
        updated_source = create_scheduler_source(
            "scheduler-1",
            "New Name",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        mock_manager._source_repository.update_source_config = Mock(return_value=updated_source)

        # Mock source registry
        mock_registry = Mock()
        mock_registry.get = Mock(return_value=None)
        mock_manager.source_registry = mock_registry

        response = await client.put("/schedules/scheduler-1", json={"name": "New Name"})

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New Name"

    @pytest.mark.asyncio
    async def test_update_schedule_config_partial_merge(self, client, mock_manager):
        """Test that partial config updates are merged with existing config."""
        existing_config = {
            "schedule": "0 9 * * *",
            "agent": "./agents/developer",
            "message": "Daily report",
            "max_concurrent": 3
        }
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            existing_config
        )
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)

        # Mock updated source with merged config
        merged_config = {
            **existing_config,
            "interval_seconds": 600  # New value
        }
        updated_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            merged_config
        )
        mock_manager._source_repository.update_source_config = Mock(return_value=updated_source)

        # Mock source registry
        mock_registry = Mock()
        mock_registry.get = Mock(return_value=None)
        mock_manager.source_registry = mock_registry

        response = await client.put("/schedules/scheduler-1", json={"config": {"interval_seconds": 600}})

        assert response.status_code == 200

        # Verify update was called with merged config
        call_kwargs = mock_manager._source_repository.update_source_config.call_args
        merged_call_config = call_kwargs.kwargs["config"]

        # Should have both original and new values
        assert "schedule" in merged_call_config
        assert "agent" in merged_call_config
        assert "message" in merged_call_config
        assert merged_call_config["interval_seconds"] == 600

    @pytest.mark.asyncio
    async def test_update_schedule_instance_mode_valid(self, client, mock_manager):
        """Test updating schedule with valid instance_mode."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)

        # Mock updated source with instance_mode
        updated_config = {
            "interval_seconds": 3600,
            "agent": "./agents/developer",
            "message": "Test",
            "instance_mode": "reuse_instance"
        }
        updated_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            updated_config
        )
        mock_manager._source_repository.update_source_config = Mock(return_value=updated_source)

        # Mock source registry
        mock_registry = Mock()
        mock_registry.get = Mock(return_value=None)
        mock_manager.source_registry = mock_registry

        response = await client.put("/schedules/scheduler-1", json={"instance_mode": "reuse_instance"})

        assert response.status_code == 200
        data = response.json()
        assert data["config"]["instance_mode"] == "reuse_instance"

    @pytest.mark.asyncio
    async def test_update_schedule_reuse_instance_max_concurrent_enforced(self, client, mock_manager):
        """Test that max_concurrent is adjusted to 1 when instance_mode is reuse_instance."""
        existing_config = {
            "interval_seconds": 3600,
            "agent": "./agents/developer",
            "message": "Test",
            "max_concurrent": 5
        }
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            existing_config
        )
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)

        # Mock updated source with max_concurrent=1 enforced
        updated_config = {
            **existing_config,
            "max_concurrent": 1,
            "instance_mode": "reuse_instance"
        }
        updated_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            updated_config
        )
        mock_manager._source_repository.update_source_config = Mock(return_value=updated_source)

        # Mock source registry
        mock_registry = Mock()
        mock_registry.get = Mock(return_value=None)
        mock_manager.source_registry = mock_registry

        response = await client.put("/schedules/scheduler-1", json={"instance_mode": "reuse_instance"})

        assert response.status_code == 200

        # Verify max_concurrent was adjusted to 1
        call_kwargs = mock_manager._source_repository.update_source_config.call_args
        merged_call_config = call_kwargs.kwargs["config"]
        assert merged_call_config["max_concurrent"] == 1

    @pytest.mark.asyncio
    async def test_update_schedule_not_found(self, client, mock_manager):
        """Test updating a non-existent schedule returns 404."""
        mock_manager._source_repository.get_source_config = Mock(return_value=None)
        
        response = await client.put("/schedules/nonexistent", json={"name": "New Name"})
        
        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["code"] == "SOURCE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_update_schedule_non_scheduler_type(self, client, mock_manager):
        """Test updating a non-scheduler source returns 400."""
        telegram_source = Mock()
        telegram_source.source_id = "telegram-1"
        telegram_source.source_type = "telegram"
        
        mock_manager._source_repository.get_source_config = Mock(return_value=telegram_source)
        
        response = await client.put("/schedules/telegram-1", json={"name": "New Name"})
        
        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["code"] == "INVALID_REQUEST"
        assert "not a scheduler" in data["detail"]["message"]

    @pytest.mark.asyncio
    async def test_update_schedule_last_run_at_populated(self, client, mock_manager):
        """Test that last_run_at is populated from execution history after update."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)

        # Mock updated source
        updated_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        mock_manager._source_repository.update_source_config = Mock(return_value=updated_source)

        # Mock latest execution
        latest_execution = create_execution("exec-1", "scheduler-1")
        latest_execution.triggered_at = "2024-01-15T09:00:00+00:00"
        mock_manager._source_repository.get_latest_execution = Mock(return_value=latest_execution)

        # Mock source registry
        mock_registry = Mock()
        mock_registry.get = Mock(return_value=None)
        mock_manager.source_registry = mock_registry

        response = await client.put("/schedules/scheduler-1", json={"name": "Updated Name"})

        assert response.status_code == 200
        data = response.json()
        assert data["last_run_at"] is not None
        assert "2024-01-15" in data["last_run_at"]


# ==================== POST /schedules/{id}/start Tests ====================


class TestStartSchedule:
    """Tests for POST /api/schedules/{schedule_id}/start endpoint."""

    @pytest.mark.asyncio
    async def test_start_schedule_success(self, client, mock_manager):
        """Test successful schedule start."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        
        # Create mock adapter with status=starting
        mock_adapter = Mock()
        mock_adapter.status = SourceStatus.starting
        
        # Mock registry
        mock_registry = Mock()
        mock_registry.start_adapter = AsyncMock(return_value=True)
        mock_registry.get = Mock(return_value=mock_adapter)
        mock_manager.source_registry = mock_registry
        
        response = await client.post("/schedules/scheduler-1/start")
        
        assert response.status_code == 200
        data = response.json()
        assert data["source_id"] == "scheduler-1"
        assert data["status"] == "starting"
        assert "started successfully" in data["message"]

        # Verify adapter was actually started
        mock_registry.start_adapter.assert_called_once_with("scheduler-1")

    @pytest.mark.asyncio
    async def test_start_schedule_not_found(self, client, mock_manager):
        """Test starting a non-existent schedule returns 404."""
        mock_manager._source_repository.get_source_config = Mock(return_value=None)
        
        response = await client.post("/schedules/nonexistent/start")
        
        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["code"] == "SOURCE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_start_schedule_non_scheduler_type(self, client, mock_manager):
        """Test starting a non-scheduler source returns 400."""
        telegram_source = Mock()
        telegram_source.source_id = "telegram-1"
        telegram_source.source_type = "telegram"
        
        mock_manager._source_repository.get_source_config = Mock(return_value=telegram_source)
        
        response = await client.post("/schedules/telegram-1/start")
        
        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["code"] == "INVALID_REQUEST"
        assert "not a scheduler" in data["detail"]["message"]

    @pytest.mark.asyncio
    async def test_start_schedule_adapter_start_failure(self, client, mock_manager):
        """Test handling adapter start failure."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        
        # Mock registry that raises exception
        mock_registry = Mock()
        mock_registry.start_adapter = AsyncMock(side_effect=RuntimeError("Failed to start"))
        mock_manager.source_registry = mock_registry
        
        response = await client.post("/schedules/scheduler-1/start")
        
        assert response.status_code == 500
        data = response.json()
        assert data["detail"]["code"] == "INTERNAL_ERROR"
        assert "Failed to start scheduler" in data["detail"]["message"]

    @pytest.mark.asyncio
    async def test_start_schedule_idempotent_already_running(self, client, mock_manager):
        """Test that starting an already running schedule succeeds (idempotent)."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        scheduler_source.status = "running"
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        
        # Create mock adapter that's already running
        mock_adapter = Mock()
        mock_adapter.status = "running"
        
        # Mock registry - start_adapter returns True for idempotent behavior
        mock_registry = Mock()
        mock_registry.start_adapter = AsyncMock(return_value=True)
        mock_registry.get = Mock(return_value=mock_adapter)
        mock_manager.source_registry = mock_registry
        
        response = await client.post("/schedules/scheduler-1/start")
        
        assert response.status_code == 200
        data = response.json()
        assert data["source_id"] == "scheduler-1"
        assert data["status"] == "running"


# ==================== POST /schedules/{id}/stop Tests ====================


class TestStopSchedule:
    """Tests for POST /api/schedules/{schedule_id}/stop endpoint."""

    @pytest.mark.asyncio
    async def test_stop_schedule_success(self, client, mock_manager):
        """Test successful schedule stop."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        scheduler_source.status = "running"
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        
        # Mock registry
        mock_registry = Mock()
        mock_registry.stop_adapter = AsyncMock(return_value=True)
        mock_manager.source_registry = mock_registry
        
        response = await client.post("/schedules/scheduler-1/stop")
        
        assert response.status_code == 200
        data = response.json()
        assert data["source_id"] == "scheduler-1"
        assert data["status"] == "stopped"
        assert "stopped successfully" in data["message"]

        # Verify adapter was actually stopped
        mock_registry.stop_adapter.assert_called_once_with("scheduler-1")

    @pytest.mark.asyncio
    async def test_stop_schedule_not_found(self, client, mock_manager):
        """Test stopping a non-existent schedule returns 404."""
        mock_manager._source_repository.get_source_config = Mock(return_value=None)
        
        response = await client.post("/schedules/nonexistent/stop")
        
        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["code"] == "SOURCE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_stop_schedule_non_scheduler_type(self, client, mock_manager):
        """Test stopping a non-scheduler source returns 400."""
        telegram_source = Mock()
        telegram_source.source_id = "telegram-1"
        telegram_source.source_type = "telegram"
        
        mock_manager._source_repository.get_source_config = Mock(return_value=telegram_source)
        
        response = await client.post("/schedules/telegram-1/stop")
        
        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["code"] == "INVALID_REQUEST"
        assert "not a scheduler" in data["detail"]["message"]

    @pytest.mark.asyncio
    async def test_stop_schedule_adapter_stop_failure(self, client, mock_manager):
        """Test handling adapter stop failure."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        scheduler_source.status = "running"
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        
        # Mock registry that raises exception
        mock_registry = Mock()
        mock_registry.stop_adapter = AsyncMock(side_effect=RuntimeError("Failed to stop"))
        mock_manager.source_registry = mock_registry
        
        response = await client.post("/schedules/scheduler-1/stop")
        
        assert response.status_code == 500
        data = response.json()
        assert data["detail"]["code"] == "INTERNAL_ERROR"
        assert "Failed to stop scheduler" in data["detail"]["message"]

    @pytest.mark.asyncio
    async def test_stop_schedule_idempotent_already_stopped(self, client, mock_manager):
        """Test that stopping an already stopped schedule succeeds (idempotent)."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        scheduler_source.status = "stopped"
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        
        # Mock registry - stop_adapter succeeds even if already stopped
        mock_registry = Mock()
        mock_registry.stop_adapter = AsyncMock(return_value=True)
        mock_manager.source_registry = mock_registry
        
        response = await client.post("/schedules/scheduler-1/stop")
        
        assert response.status_code == 200
        data = response.json()
        assert data["source_id"] == "scheduler-1"
        assert data["status"] == "stopped"
        assert "stopped successfully" in data["message"]
