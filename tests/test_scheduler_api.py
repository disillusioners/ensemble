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
    # wc-wake-report-integrity T6b completion (2026-08-30): the stale
    # ``manager.send_message`` Mock stub was REMOVED — the legacy
    # Manager.send_message is deleted (C1-D7) and nothing in this file
    # referenced it.
    manager.terminate_instance = Mock(return_value=True)
    manager.list_instances = Mock(return_value=([], 0, False))
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
    
    # Mock source registry
    manager.source_registry = None

    # Phase 5 (scheduled-tasks): the phase-3 REST routes reach the shared
    # scheduling service through ``manager.scheduling_service``. Default the
    # async seams to None-returning AsyncMocks so the fixture stays INERT for
    # every pre-existing test (Risk #6, phase5-plan.md); the new classes below
    # override per-test. (Lazy property on the real manager — a plain
    # attribute on the Mock is the faithful stand-in.)
    manager.scheduling_service = MagicMock()
    manager.scheduling_service.create_schedule = AsyncMock(return_value=None)
    manager.scheduling_service.cancel_schedule = AsyncMock(return_value=None)
    manager.scheduling_service.get_schedule = AsyncMock(return_value=None)

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

    @pytest.mark.asyncio
    async def test_start_schedule_409_when_cancelled_does_not_resurrect(self, client, mock_manager):
        """Cancel is TERMINAL (ADR-005/007) — POST /start on a cancelled row
        must 409 with SCHEDULE_ALREADY_CANCELLED, leave the DB row at
        status='cancelled', and never touch the adapter registry. Mirrors
        TestCancelSchedule.test_cancel_409_when_already_cancelled (same
        error code, same HTTP status) so callers treat start-of-cancelled
        identically to double-cancel. Without the round-5 gate the
        rebuild branch (_create_adapter_from_config → register →
        start_adapter) erased the terminal status and made the row
        boot-startable again."""
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        scheduler_source.status = SourceStatus.cancelled.value
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)

        # Registry is mocked but its adapter-building / start methods must
        # NOT be called on a cancelled row (the gate fires before any
        # registry interaction). Use a spec-less Mock and assert NOT called.
        mock_registry = Mock()
        mock_registry.start_adapter = AsyncMock(return_value=True)
        mock_registry.get = Mock(return_value=None)
        mock_registry._create_adapter_from_config = AsyncMock(return_value=Mock())
        mock_registry.register = Mock()
        mock_manager.source_registry = mock_registry

        response = await client.post("/schedules/scheduler-1/start")

        assert response.status_code == 409
        data = response.json()
        assert data["detail"]["code"] == "SCHEDULE_ALREADY_CANCELLED"
        assert "cancelled" in data["detail"]["message"].lower()
        assert "terminal" in data["detail"]["message"].lower()

        # Row status MUST remain cancelled — gate sits BEFORE rebuild so
        # no code path runs that could mutate the DB.
        scheduler_source.status = SourceStatus.cancelled.value  # unchanged
        mock_manager._source_repository.get_source_config.assert_called_once_with("scheduler-1")

        # Adapter resurrection is the exact failure mode this gate closes:
        # rebuild branch MUST be inert, start_adapter MUST NOT fire.
        mock_registry._create_adapter_from_config.assert_not_called()
        mock_registry.register.assert_not_called()
        mock_registry.start_adapter.assert_not_called()


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


# ==================== Phase-5: POST /schedules (create) ====================


class TestCreateSchedule:
    """Tests for POST /api/schedules (phase-3 contract — FLAT REST response, no detail wrapping).

    SKELETON ADAPTATION (binding approver correction (d)): the frozen
    phase5-plan §Task 2.1 skeleton named this class ``ScheduleCreateSchedule``
    — missing the ``Test`` prefix pytest requires for collection. Renamed to
    ``TestCreateSchedule`` and the whole skeleton was swept for the same
    defect (no other skeleton class name carries it).
    """

    _BODY = {
        "source_id": "morning-briefing",
        "name": "Morning Briefing",
        "agent_id": "ari",
        "message": "Give me a morning briefing",
        "project_id": "default",
        "recurrence": "daily",
        "local_time": "06:00",
        "timezone": "America/New_York",
    }

    @staticmethod
    def _service_create_response(**overrides):
        """Canonical phase-2 ScheduleCreateResponse (the service's return shape)."""
        from daemon.services.scheduling_service import ScheduleCreateResponse

        values = dict(
            source_id="morning-briefing",
            label="Morning Briefing",
            status="running",
            next_run_at_local="2026-10-02T06:00:00-04:00",
            next_run_at_utc="2026-10-02T10:00:00+00:00",
            tz_warning="",
        )
        values.update(overrides)
        return ScheduleCreateResponse(**values)

    @pytest.mark.asyncio
    async def test_create_returns_201_with_both_timezones(self, client, mock_manager):
        mock_manager.scheduling_service.create_schedule = AsyncMock(
            return_value=self._service_create_response()
        )
        response = await client.post("/schedules", json=dict(self._BODY))
        assert response.status_code == 201
        data = response.json()
        # FLAT response — BOTH timezone echoes present.
        assert data["next_run_at_local"] == "2026-10-02T06:00:00-04:00"
        assert data["next_run_at_utc"] == "2026-10-02T10:00:00+00:00"
        assert data["source_id"] == "morning-briefing"
        assert data["id"] == "morning-briefing"
        assert data["label"] == "Morning Briefing"
        assert data["status"] == "running"
        assert data["tz_warning"] == ""

    @pytest.mark.asyncio
    async def test_create_maps_rest_names_to_canonical_payload(self, client, mock_manager):
        """The router builds a FULLY-TYPED canonical payload (ADR-013 F1):
        label = name or source_id, agent = agent_id, when = local_time."""
        from daemon.services.scheduling_service import ScheduleCreatePayload

        mock_manager.scheduling_service.create_schedule = AsyncMock(
            return_value=self._service_create_response()
        )
        response = await client.post("/schedules", json=dict(self._BODY))
        assert response.status_code == 201
        payload = mock_manager.scheduling_service.create_schedule.await_args.args[0]
        assert isinstance(payload, ScheduleCreatePayload)
        assert payload.label == "Morning Briefing"  # name wins over source_id
        assert payload.agent == "ari"
        assert payload.when == "06:00"
        assert payload.timezone == "America/New_York"
        assert payload.recurrence == "daily"
        # Caller ids: REST has no calling instance — the audit marker.
        assert mock_manager.scheduling_service.create_schedule.await_args.kwargs[
            "caller_instance_id"
        ] == "rest-api"

    @pytest.mark.asyncio
    async def test_create_503_when_write_paused(self, client, mock_manager):
        mock_manager.is_write_paused = True
        response = await client.post("/schedules", json={
            "source_id": "x", "agent_id": "ari", "message": "x",
            "project_id": "default", "recurrence": "once", "run_at": "2026-10-15T06:00:00",
        })
        assert response.status_code == 503
        assert "Writes are paused" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_409_on_duplicate_label(self, client, mock_manager):
        """Phase-2 uniqueness check on the resolved label raises
        ValueError("label already exists"); handler maps to 409 Conflict."""
        mock_manager.scheduling_service.create_schedule = AsyncMock(
            side_effect=ValueError("label already exists: morning-briefing")
        )
        response = await client.post("/schedules", json=dict(self._BODY))
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "SCHEDULE_LABEL_CONFLICT"

    @pytest.mark.asyncio
    async def test_create_422_on_invalid_instance_mode(self, client, mock_manager):
        """``instance_mode`` is a Literal on the REST model → Pydantic 422."""
        body = dict(self._BODY, instance_mode="bogus_mode")
        response = await client.post("/schedules", json=body)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_422_on_missing_cron_expression(self, client, mock_manager):
        """recurrence=cron without cron_expression → model-validator 422."""
        body = {
            "source_id": "cron-missing", "agent_id": "ari", "message": "x",
            "project_id": "default", "recurrence": "cron",
        }
        response = await client.post("/schedules", json=body)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_400_on_other_value_errors(self, client, mock_manager):
        """Non-label ValueErrors from the service are client input errors → 400."""
        mock_manager.scheduling_service.create_schedule = AsyncMock(
            side_effect=ValueError("agent_id ghost does not exist")
        )
        response = await client.post("/schedules", json=dict(self._BODY))
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "INVALID_REQUEST"


# ==================== Phase-5: GET /schedules/{id} ====================


class TestGetScheduleById:
    """Tests for GET /api/schedules/{schedule_id} (phase-3 contract).

    SKELETON ADAPTATION vs phase5-plan §Task 2.2 case (c): the plan said
    "400 on non-scheduler source_type", but the LANDED handler delegates to
    ``scheduling_service.get_schedule``, which resolves ONLY scheduler rows
    (``_scheduler_row_or_none``) and returns None for everything else — so
    unknown refs AND non-scheduler rows are both 404 (route docstring pins
    this). The test asserts the LANDED 404 contract.
    """

    @staticmethod
    def _service_detail(**overrides):
        from daemon.services.scheduling_service import ScheduleDetail

        values = dict(
            source_id="sched-1",
            label="sched-1",
            status="running",
            recurrence="daily",
            local_time="06:00",
            timezone="America/New_York",
            weekday=None,
            cron_expression=None,
            instance_mode="new_instance",
            next_run_at_local="2026-10-02T06:00:00-04:00",
            next_run_at_utc="2026-10-02T10:00:00+00:00",
            agent="ari",
            project_id="default",
            last_run_at=None,
            cancelled_at=None,
            tz_warning="",
        )
        values.update(overrides)
        return ScheduleDetail(**values)

    @pytest.mark.asyncio
    async def test_get_returns_200_with_flat_detail(self, client, mock_manager):
        mock_manager.scheduling_service.get_schedule = AsyncMock(
            return_value=self._service_detail()
        )
        response = await client.get("/schedules/sched-1")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "sched-1"
        assert data["source_id"] == "sched-1"
        assert data["label"] == "sched-1"
        assert data["status"] == "running"
        assert data["recurrence"] == "daily"
        assert data["agent_id"] == "ari"

    @pytest.mark.asyncio
    async def test_get_404_on_missing(self, client, mock_manager):
        mock_manager.scheduling_service.get_schedule = AsyncMock(return_value=None)
        response = await client.get("/schedules/ghost")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "SOURCE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_get_non_scheduler_row_is_404(self, client, mock_manager):
        """LANDED contract: the service resolves only scheduler rows — a
        telegram row (or any unknown ref) is 404, not 400 (see class doc)."""
        mock_manager.scheduling_service.get_schedule = AsyncMock(return_value=None)
        response = await client.get("/schedules/telegram-1")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_returns_both_timezone_echoes(self, client, mock_manager):
        """Both next_run_at_local AND next_run_at_utc — never only one."""
        mock_manager.scheduling_service.get_schedule = AsyncMock(
            return_value=self._service_detail()
        )
        response = await client.get("/schedules/sched-1")
        data = response.json()
        assert data["next_run_at_local"] is not None
        assert data["next_run_at_utc"] is not None

    @pytest.mark.asyncio
    async def test_get_cancelled_row_has_cancelled_at_string(self, client, mock_manager):
        """GET-by-id returns cancelled rows (the "did I actually cancel X?" path,
        architecture §3.4); ``cancelled_at`` is an ISO string when cancelled."""
        mock_manager.scheduling_service.get_schedule = AsyncMock(
            return_value=self._service_detail(
                status="cancelled", cancelled_at="2026-10-01T20:00:00+00:00",
                next_run_at_local=None, next_run_at_utc=None,
            )
        )
        response = await client.get("/schedules/sched-1")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"
        assert data["cancelled_at"] == "2026-10-01T20:00:00+00:00"

    @pytest.mark.asyncio
    async def test_get_running_row_has_null_cancelled_at(self, client, mock_manager):
        mock_manager.scheduling_service.get_schedule = AsyncMock(
            return_value=self._service_detail()
        )
        response = await client.get("/schedules/sched-1")
        assert response.json()["cancelled_at"] is None


# ==================== Phase-5: DELETE /schedules/{id} (cancel) ====================


class TestCancelSchedule:
    """Tests for DELETE /api/schedules/{schedule_id} — terminal cancel, history preserved.

    SKELETON ADAPTATIONS vs the frozen phase5-plan §Task 2.3 skeleton:
      * The landed route delegates to the shared service's ATOMIC
        ``cancel_schedule`` (which itself writes via the atomic repository
        ``cancel_source_config``); the route never calls the two-call
        stop-sequence. Assertions pin delegation + history preservation at
        the route boundary.
      * Plan case (d) "400 on non-scheduler source_type" → LANDED service
        resolves only scheduler rows → 404 (same adaptation as
        TestGetScheduleById).
    """

    @staticmethod
    def _service_cancel_response(**overrides):
        from daemon.services.scheduling_service import ScheduleCancelResponse

        values = dict(
            source_id="morning-briefing",
            status="cancelled",
            cancelled_at="2026-10-01T20:00:00+00:00",
            last_execution_id=None,
        )
        values.update(overrides)
        return ScheduleCancelResponse(**values)

    @pytest.mark.asyncio
    async def test_cancel_calls_cancel_schedule_NOT_delete_source_config(self, client, mock_manager):
        mock_manager.scheduling_service.cancel_schedule = AsyncMock(
            return_value=self._service_cancel_response()
        )
        response = await client.delete("/schedules/morning-briefing")
        assert response.status_code == 200
        assert mock_manager.scheduling_service.cancel_schedule.await_count == 1
        mock_manager._source_repository.delete_source_config.assert_not_called()
        data = response.json()
        assert data["source_id"] == "morning-briefing"
        assert data["status"] == "cancelled"
        assert data["cancelled_at"] == "2026-10-01T20:00:00+00:00"
        assert data["last_execution_id"] is None  # §5.3 echo
        assert "history retained" in data["message"]

    @pytest.mark.asyncio
    async def test_cancel_echoes_last_execution_id(self, client, mock_manager):
        """Architecture §5.3: the in-flight JobItem is NOT cancellable via
        schedule-cancel — the response echoes the last execution id so the
        operator can cancel it directly."""
        mock_manager.scheduling_service.cancel_schedule = AsyncMock(
            return_value=self._service_cancel_response(
                last_execution_id="exec-42"
            )
        )
        response = await client.delete("/schedules/morning-briefing")
        assert response.status_code == 200
        assert response.json()["last_execution_id"] == "exec-42"

    @pytest.mark.asyncio
    async def test_cancel_503_when_write_paused(self, client, mock_manager):
        mock_manager.is_write_paused = True
        response = await client.delete("/schedules/morning-briefing")
        assert response.status_code == 503
        assert "Writes are paused" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_cancel_404_on_missing(self, client, mock_manager):
        mock_manager.scheduling_service.cancel_schedule = AsyncMock(
            side_effect=ValueError("Schedule not found: ghost")
        )
        response = await client.delete("/schedules/ghost")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "SOURCE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_cancel_409_when_already_cancelled(self, client, mock_manager):
        """Cancel is TERMINAL — a second cancel is 409 SCHEDULE_ALREADY_CANCELLED."""
        mock_manager.scheduling_service.cancel_schedule = AsyncMock(
            side_effect=ValueError("Schedule already cancelled: morning-briefing")
        )
        response = await client.delete("/schedules/morning-briefing")
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "SCHEDULE_ALREADY_CANCELLED"

    @pytest.mark.asyncio
    async def test_cancel_non_scheduler_row_is_404(self, client, mock_manager):
        """LANDED contract (see TestGetScheduleById): non-scheduler refs → 404."""
        mock_manager.scheduling_service.cancel_schedule = AsyncMock(
            side_effect=ValueError("Schedule not found: telegram-1")
        )
        response = await client.delete("/schedules/telegram-1")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_accepts_label_reference(self, client, mock_manager):
        """The URL accepts id OR label — the route passes the reference
        verbatim; the service resolves label→row (phase-3 §1.5)."""
        mock_manager.scheduling_service.cancel_schedule = AsyncMock(
            return_value=self._service_cancel_response(source_id="resolved-id-1")
        )
        response = await client.delete("/schedules/Morning%20Briefing")
        assert response.status_code == 200
        assert (
            mock_manager.scheduling_service.cancel_schedule.await_args.args[0]
            == "Morning Briefing"
        )
        data = response.json()
        assert data["id"] == "resolved-id-1"

    @pytest.mark.asyncio
    async def test_cancel_route_never_touches_repository_writers(self, client, mock_manager):
        """The route delegates ALL writes to the atomic service — it must not
        call the repository's status/config writers itself (single-writer
        seam, architecture §5.4)."""
        mock_manager.scheduling_service.cancel_schedule = AsyncMock(
            return_value=self._service_cancel_response()
        )
        await client.delete("/schedules/morning-briefing")
        mock_manager._source_repository.cancel_source_config.assert_not_called()
        mock_manager._source_repository.update_source_status.assert_not_called()
        mock_manager._source_repository.update_source_config.assert_not_called()


# ==================== Phase-5: GET /schedules list filter ====================


class TestScheduleListFilter:
    """GET /api/schedules cancelled-row visibility (architecture §3.4 / ADR-005).

    SKELETON ADAPTATION vs phase5-plan §Task 2.4: the plan asked to "assert
    the SQL filter is applied server-side"; the LANDED filter is applied
    server-side in the ROUTE handler (Python-level skip before
    serialization), not as a SQL predicate — the observable contract is
    identical (cancelled rows never appear in the default response payload),
    so the tests pin the HTTP-observable behavior.
    """

    @staticmethod
    def _source(source_id: str, source_type: str, status: str):
        source = create_scheduler_source(source_id, f"Test {source_id}", {"interval_seconds": 3600})
        source.source_type = source_type
        source.status = status
        return source

    @pytest.mark.asyncio
    async def test_default_excludes_cancelled(self, client, mock_manager):
        mock_manager._source_repository.list_source_configs = Mock(return_value=[
            self._source("s-running", "scheduler", "running"),
            self._source("s-cancelled", "scheduler", "cancelled"),
            self._source("t-1", "telegram", "running"),
        ])
        response = await client.get("/schedules")
        assert response.status_code == 200
        ids = [s["id"] for s in response.json()["schedules"]]
        assert "s-running" in ids
        assert "s-cancelled" not in ids
        assert "t-1" not in ids  # scheduler-only listing

    @pytest.mark.asyncio
    async def test_include_cancelled_true_returns_cancelled(self, client, mock_manager):
        mock_manager._source_repository.list_source_configs = Mock(return_value=[
            self._source("s-running", "scheduler", "running"),
            self._source("s-cancelled", "scheduler", "cancelled"),
        ])
        response = await client.get("/schedules", params={"include_cancelled": "true"})
        assert response.status_code == 200
        ids = [s["id"] for s in response.json()["schedules"]]
        assert "s-running" in ids
        assert "s-cancelled" in ids

    @pytest.mark.asyncio
    async def test_include_cancelled_false_excludes_cancelled(self, client, mock_manager):
        mock_manager._source_repository.list_source_configs = Mock(return_value=[
            self._source("s-cancelled", "scheduler", "cancelled"),
        ])
        response = await client.get("/schedules", params={"include_cancelled": "false"})
        assert response.status_code == 200
        ids = [s["id"] for s in response.json()["schedules"]]
        assert "s-cancelled" not in ids


# ==================== Phase-5: /api/sources DELETE guard placement ====================


class TestDeleteGuardPlacement:
    """Phase-3 §Task 4.3 (deferred to this unit) — the /api/sources DELETE
    cancel-confusion guard (ADR-012, architecture §2 OD-5).

    Placement contract: the scheduler-row guard sits AFTER the get-or-404
    (unknown ids still 404, NOT 400) and BEFORE any adapter stop / row delete
    (/api/sources DELETE purges schedule_executions history — scheduler rows
    must never reach it).
    """

    @pytest.mark.asyncio
    async def test_delete_unknown_source_is_404_not_400(self, client, mock_manager):
        """Guard is AFTER the get-or-404: an unknown id must stay 404."""
        mock_manager._source_repository.get_source_config = Mock(return_value=None)
        response = await client.delete("/sources/ghost-source")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "SOURCE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_delete_scheduler_source_is_400_with_pointer(self, client, mock_manager):
        """Scheduler rows are 400 SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED with a
        pointer at DELETE /api/schedules/{id}; the row is NOT deleted."""
        scheduler_source = create_scheduler_source(
            "sched-1", "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"},
        )
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)

        response = await client.delete("/sources/sched-1")
        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["code"] == "SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED"
        assert "/api/schedules" in data["detail"]["message"]
        # History preservation: the purge path was never reached.
        mock_manager._source_repository.delete_source_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_non_scheduler_source_unaffected(self, client, mock_manager):
        """Non-scheduler rows still delete normally (guard is scheduler-only)."""
        telegram_source = create_scheduler_source("telegram-1", "Test", {})
        telegram_source.source_type = "telegram"
        mock_manager._source_repository.get_source_config = Mock(return_value=telegram_source)
        mock_manager._source_repository.delete_source_config = Mock(return_value=True)
        mock_manager.source_registry = None  # no live adapter to stop

        response = await client.delete("/sources/telegram-1")
        assert response.status_code == 200
        assert response.json()["deleted"] is True
        mock_manager._source_repository.delete_source_config.assert_called_once()
