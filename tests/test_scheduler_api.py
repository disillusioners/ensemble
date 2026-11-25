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

# The auth env is captured when ``daemon.api`` builds the app at IMPORT
# time (``create_app`` → ``load_config().auth`` → AuthMiddleware), so a
# per-variable scrub must happen BEFORE that import — not in a
# function-scoped fixture, which runs after every module-level import.
# This module-level loop is the explicit, per-variable equivalent of the
# shell fence; it names ONLY the seven ENSEMBLE_AUTH_* variables this
# host exports (no family glob — ``env -u ENSEMBLE_AUTH_*`` is a NO-OP).
for _auth_env_name in (
    "ENSEMBLE_AUTH_ENABLED",
    "ENSEMBLE_AUTH_ISSUER",
    "ENSEMBLE_AUTH_DISCOVERY_URL",
    "ENSEMBLE_AUTH_CLIENT_ID",
    "ENSEMBLE_AUTH_CLIENT_SECRET",
    "ENSEMBLE_AUTH_ALLOWED_ORIGINS",
    "ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE",
):
    os.environ.pop(_auth_env_name, None)


# Import the app and manager directly
from daemon import api as api_module
from daemon.models.source import SourceStatus


# ==================== Fixtures ====================


@pytest.fixture(autouse=True)
def _hermetic_auth_env(monkeypatch):
    """Keep the file hermetic for the duration of every test.

    The module-level scrub above handles the import-time capture; this
    autouse fixture re-applies the SAME per-variable scrub at test time
    (and restores the process env afterwards via monkeypatch) so a test
    that mutates os.environ cannot leak auth state into its neighbours.
    """
    for _name in (
        "ENSEMBLE_AUTH_ENABLED",
        "ENSEMBLE_AUTH_ISSUER",
        "ENSEMBLE_AUTH_DISCOVERY_URL",
        "ENSEMBLE_AUTH_CLIENT_ID",
        "ENSEMBLE_AUTH_CLIENT_SECRET",
        "ENSEMBLE_AUTH_ALLOWED_ORIGINS",
        "ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE",
    ):
        monkeypatch.delenv(_name, raising=False)


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

    @pytest.mark.asyncio
    async def test_list_schedules_masks_webhook_secret(self, client, mock_manager):
        """M-2 (2026-10-11 review): the scheduler-source config echo
        goes through the SAME read-side mask as the sources router —
        webhook_secret must never leave in plaintext on a GET."""
        scheduler_source = create_scheduler_source(
            "scheduler-secret",
            "Secret Bearer Schedule",
            {
                "schedule": "0 9 * * *",
                "agent": "./agents/developer",
                "message": "Report",
                "webhook_secret": "super-secret-value",
            },
        )
        mock_manager._source_repository.list_source_configs = Mock(
            return_value=[scheduler_source]
        )

        response = await client.get("/schedules")

        assert response.status_code == 200
        entry = response.json()["schedules"][0]
        assert entry["config"]["webhook_secret"] == "********"
        assert "super-secret-value" not in response.text



class TestR1PutResponseMask:
    """R1 (2026-10-11 merge gate): the PUT /schedules/{id} response
    echoes the persisted config through the SAME read-side mask as GET
    (sibling-verb defeat of the M-2 GET mask) — webhook_secret plaintext
    must never leave via ANY serialized schedule response."""

    @pytest.mark.asyncio
    async def test_put_response_never_contains_plaintext_webhook_secret(self, client, mock_manager):
        scheduler_source = create_scheduler_source(
            "scheduler-secret-put",
            "Secret Bearer Schedule",
            {
                "schedule": "0 9 * * *",
                "agent": "./agents/developer",
                "message": "Report",
                "webhook_secret": "super-secret-value",
            },
        )
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        # The repository returns the PERSISTED row (plaintext inside the
        # daemon) — the response serialization must still mask it.
        mock_manager._source_repository.update_source_config = Mock(
            return_value=scheduler_source
        )
        mock_registry = Mock()
        mock_registry.get = Mock(return_value=None)
        mock_manager.source_registry = mock_registry

        response = await client.put(
            "/schedules/scheduler-secret-put",
            json={"name": "Renamed Schedule"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["config"]["webhook_secret"] == "********"
        assert "super-secret-value" not in response.text


class TestPutSentinelRoundTrip:
    """Cheap minor (2026-10-11 merge gate): the ML-3 PUT-sentinel guard
    — an incoming webhook_secret equal to the mask sentinel means
    "unchanged" and must round-trip the STORED value, never persist the
    mask over the real secret (schedules router leg; sources router leg
    lives in tests/unit/test_oauth2_merge_gate_riders.py)."""

    @pytest.mark.asyncio
    async def test_put_mask_sentinel_keeps_stored_secret(self, client, mock_manager):
        stored = "the-real-secret"
        scheduler_source = create_scheduler_source(
            "scheduler-sentinel",
            "Sentinel Schedule",
            {
                "schedule": "0 9 * * *",
                "agent": "./agents/developer",
                "message": "Report",
                "webhook_secret": stored,
            },
        )
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        updated_source = create_scheduler_source(
            "scheduler-sentinel",
            "Sentinel Schedule",
            {
                "schedule": "0 9 * * *",
                "agent": "./agents/developer",
                "message": "Report",
                "webhook_secret": stored,
            },
        )
        mock_manager._source_repository.update_source_config = Mock(
            return_value=updated_source
        )
        mock_registry = Mock()
        mock_registry.get = Mock(return_value=None)
        mock_manager.source_registry = mock_registry

        response = await client.put(
            "/schedules/scheduler-sentinel",
            # The GET-then-PUT-back shape: the client echoes the mask.
            json={"config": {"message": "Report", "webhook_secret": "********"}},
        )

        assert response.status_code == 200
        call_kwargs = mock_manager._source_repository.update_source_config.call_args
        persisted = call_kwargs.kwargs["config"]
        # The STORED secret rode through — the mask never persisted.
        assert persisted["webhook_secret"] == stored
        assert persisted["webhook_secret"] != "********"
        # And the response still masks it (R1 composing with the guard).
        assert response.json()["config"]["webhook_secret"] == "********"


class TestPutCreatorPreservation:
    """N6/A6: the creator_instance_id captured at create time is immutable
    via the REST PUT merge — it survives partial updates and cannot be
    injected or overwritten by a client."""

    @pytest.mark.asyncio
    async def test_put_preserves_creator_instance_id(self, client, mock_manager):
        stored_creator = "creator-abc"
        base_config = {
            "schedule": "0 9 * * *",
            "agent": "./agents/developer",
            "message": "Report",
            "creator_instance_id": stored_creator,
        }
        scheduler_source = create_scheduler_source(
            "scheduler-creator", "Creator Schedule", dict(base_config),
        )
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        updated_source = create_scheduler_source(
            "scheduler-creator", "Creator Schedule", dict(base_config),
        )
        mock_manager._source_repository.update_source_config = Mock(return_value=updated_source)
        mock_registry = Mock()
        mock_registry.get = Mock(return_value=None)
        mock_manager.source_registry = mock_registry

        response = await client.put(
            "/schedules/scheduler-creator",
            json={"config": {"message": "Updated"}},
        )
        assert response.status_code == 200
        persisted = mock_manager._source_repository.update_source_config.call_args.kwargs["config"]
        assert persisted.get("creator_instance_id") == stored_creator

    @pytest.mark.asyncio
    async def test_put_cannot_inject_creator_instance_id(self, client, mock_manager):
        base_config = {
            "schedule": "0 9 * * *",
            "agent": "./agents/developer",
            "message": "Report",
        }
        scheduler_source = create_scheduler_source(
            "scheduler-inject", "Inject Schedule", dict(base_config),
        )
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        updated_source = create_scheduler_source(
            "scheduler-inject", "Inject Schedule", dict(base_config),
        )
        mock_manager._source_repository.update_source_config = Mock(return_value=updated_source)
        mock_registry = Mock()
        mock_registry.get = Mock(return_value=None)
        mock_manager.source_registry = mock_registry

        response = await client.put(
            "/schedules/scheduler-inject",
            json={"config": {"message": "Updated", "creator_instance_id": "evil-creator"}},
        )
        assert response.status_code == 200
        persisted = mock_manager._source_repository.update_source_config.call_args.kwargs["config"]
        assert "creator_instance_id" not in persisted


# ==================== POST /schedules/{id}/trigger Tests ====================


class TestCreatorCaptureRest:
    """B1: REST create carries the audit sentinel (no creator key), and a
    REST-created fire skips the identity gate silently — no repo read, no
    dropped-counter increment (the only pin distinguishing absent-key skip
    from a wrong absent-row drop)."""

    @pytest.fixture(autouse=True)
    def _reset_counters(self):
        from daemon.services import schedule_fire_notify_metrics as metrics

        metrics.reset_sent_total()
        metrics.reset_dropped_total()
        yield
        metrics.reset_sent_total()
        metrics.reset_dropped_total()

    @pytest.mark.asyncio
    async def test_rest_created_row_has_no_creator_key(self):
        import daemon.services.scheduling_service as ss
        repo = Mock()
        row = Mock()
        row.source_id = "src-1"
        row.name = "lbl"
        row.status = "running"
        row.config = None
        row.enabled = True
        repo.get_source_config_by_name = Mock(return_value=None)
        repo.list_source_configs = Mock(return_value=[])
        repo.create_source_config = Mock(return_value=row)
        repo.get_source_config = Mock(return_value=row)
        orig = ss._require_repo
        ss._require_repo = lambda: repo
        try:
            await ss.create_schedule(
                {
                    "label": "lbl",
                    "message": "hi",
                    "when": "06:00",
                    "recurrence": "daily",
                    "agent": "developer",
                    "instance_mode": "new_instance",
                },
                caller_instance_id="rest-api",
                caller_agent_id="",
            )
        finally:
            ss._require_repo = orig
        call = repo.create_source_config.call_args
        config = call.args[2] if len(call.args) >= 3 else call.kwargs.get("config")
        assert isinstance(config, dict)
        assert "creator_instance_id" not in config

    @pytest.mark.asyncio
    async def test_rest_fire_does_not_increment_dropped_total(self):
        """B1(b): absent creator key -> skip silently (no repo read, no drop)."""
        import asyncio
        from daemon.services import schedule_fire_notify as sfn
        from daemon.services import schedule_fire_notify_metrics as metrics

        metrics.reset_dropped_total()
        manager = Mock()
        manager._instance_repository = Mock()
        manager.enqueue_message_job = Mock()

        await sfn.notify_schedule_fire(
            manager,
            schedule_config={"agent": "developer", "message": "hi"},
            metadata={"scheduler": {"trigger_time": "2026-01-01T00:00:00"}},
            recipient_instance_id="some-recipient",
            label="rest-lbl",
            target_agent="developer",
            instance_mode="new_instance",
            recurrence="daily",
            job_id="j",
            message_id="m",
            target_instance_id="some-recipient",
        )
        await asyncio.sleep(0.1)
        assert metrics.get_dropped_total() == 0
        assert manager._instance_repository.get.call_count == 0
        assert manager.enqueue_message_job.call_count == 0


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

    @pytest.mark.asyncio
    async def test_trigger_schedule_409_when_cancelled_does_not_resurrect(self, client, mock_manager):
        """Cancel is TERMINAL (ADR-005/007) — POST /trigger on a cancelled
        row must 409 with SCHEDULE_ALREADY_CANCELLED, leave the DB row at
        status='cancelled', and never call registry.get() (adapter
        eviction invariant: cancelled rows have no live adapter). Mirrors
        the landed POST /start 409 (commit 2e7c5288) and the cancelled-
        mapping at :284 — same error code, same HTTP status — so callers
        treat trigger-of-cancelled identically to start-of-cancelled and
        double-cancel. Without the round-5 gate the registry.get() would
        return None and the route would fall through to a 503
        INTERNAL_ERROR ("adapter not running"), a misleading signal —
        the row is NOT "adapter-not-running", it is TERMINAL.
        """
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        scheduler_source.status = SourceStatus.cancelled.value
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)

        # Registry is mocked for the explicit NOT-called assertion. The
        # round-5 gate must fire BEFORE registry.get() / adapter.manual_trigger
        # so the trigger branch is unreachable from a cancelled row.
        mock_registry = Mock()
        mock_registry.get = Mock(return_value=None)
        mock_registry._create_adapter_from_config = AsyncMock(return_value=Mock())
        mock_registry.register = Mock()
        mock_manager.source_registry = mock_registry

        response = await client.post("/schedules/scheduler-1/trigger")

        assert response.status_code == 409
        data = response.json()
        assert data["detail"]["code"] == "SCHEDULE_ALREADY_CANCELLED"
        assert "cancelled" in data["detail"]["message"].lower()
        assert "terminal" in data["detail"]["message"].lower()

        # Row status MUST remain cancelled — gate sits BEFORE the trigger
        # branch so no code path runs that could mutate the DB.
        scheduler_source.status = SourceStatus.cancelled.value  # unchanged
        mock_manager._source_repository.get_source_config.assert_called_once_with("scheduler-1")

        # The trigger path MUST be inert: registry.get() (the seam that
        # would 503 with "adapter not running") and any rebuild/register
        # MUST NOT fire.
        mock_registry.get.assert_not_called()
        mock_registry._create_adapter_from_config.assert_not_called()
        mock_registry.register.assert_not_called()


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

    @pytest.mark.asyncio
    async def test_update_schedule_409_when_cancelled_does_not_mutate(self, client, mock_manager):
        """Cancel is TERMINAL (ADR-005/007) — PUT /schedules/{id} on a
        cancelled row must 409 with SCHEDULE_ALREADY_CANCELLED, leave the
        DB row at status='cancelled', and never call update_source_config
        or write any config merge. Mirrors the landed POST /start 409
        (commit 2e7c5288) and the cancelled-mapping at :284 — same error
        code, same HTTP status — so callers treat update-of-cancelled
        identically to start-of-cancelled and double-cancel. Without the
        round-5 gate the PUT route silently re-wrote the persisted config,
        making the row re-cancellable / re-boot-startable and violating the
        "never fires again, not after a daemon restart" tools_note contract.
        """
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        scheduler_source.status = SourceStatus.cancelled.value
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)
        # update_source_config MUST NOT be called on a cancelled row —
        # the gate sits BEFORE the merge path, so no code path runs that
        # could mutate the persisted row.
        mock_manager._source_repository.update_source_config = Mock(
            return_value=scheduler_source
        )

        # Registry is mocked but unused on this gate path — assert it for
        # parity with the start-409 test (start-supplements-branch).
        mock_registry = Mock()
        mock_registry.get = Mock(return_value=None)
        mock_manager.source_registry = mock_registry

        response = await client.put(
            "/schedules/scheduler-1",
            json={"name": "Updated Name"},
        )

        assert response.status_code == 409
        data = response.json()
        assert data["detail"]["code"] == "SCHEDULE_ALREADY_CANCELLED"
        assert "cancelled" in data["detail"]["message"].lower()
        assert "terminal" in data["detail"]["message"].lower()

        # Row status MUST remain cancelled — gate sits BEFORE merge so no
        # code path runs that could mutate the DB.
        scheduler_source.status = SourceStatus.cancelled.value  # unchanged
        mock_manager._source_repository.get_source_config.assert_called_once_with("scheduler-1")
        # The DB-write-then-display path is the exact failure mode this gate
        # closes: update_source_config MUST NOT fire.
        mock_manager._source_repository.update_source_config.assert_not_called()


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

    @pytest.mark.asyncio
    async def test_stop_schedule_409_when_cancelled_does_not_resurrect(self, client, mock_manager):
        """Cancel is TERMINAL (ADR-005/007) — POST /stop on a cancelled row
        must 409 with SCHEDULE_ALREADY_CANCELLED, leave the DB row at
        status='cancelled', and never call registry.stop_adapter. Mirrors
        the landed POST /start 409 (commit 2e7c5288) and the cancelled-
        mapping at :284 — same error code, same HTTP status — so callers
        treat stop-of-cancelled identically to start-of-cancelled and
        double-cancel. Without the round-5 gate the route would call
        stop_adapter on a registry that (per seam-2 invariant) has no
        adapter for this id; the registry would log a "source not found"
        warning and the route would fall through to its success branch,
        returning 200 — a silent mutation of caller-visible state with no
        DB change. The 409 makes the terminal contract load-bearing.
        """
        scheduler_source = create_scheduler_source(
            "scheduler-1",
            "Test Schedule",
            {"interval_seconds": 3600, "agent": "./agents/developer", "message": "Test"}
        )
        scheduler_source.status = SourceStatus.cancelled.value
        mock_manager._source_repository.get_source_config = Mock(return_value=scheduler_source)

        # Registry is mocked for the explicit NOT-called assertion. The
        # round-5 gate must fire BEFORE stop_adapter so the stop branch is
        # unreachable from a cancelled row.
        mock_registry = Mock()
        mock_registry.stop_adapter = AsyncMock(return_value=True)
        mock_registry.get = Mock(return_value=None)
        mock_registry._create_adapter_from_config = AsyncMock(return_value=Mock())
        mock_registry.register = Mock()
        mock_manager.source_registry = mock_registry

        response = await client.post("/schedules/scheduler-1/stop")

        assert response.status_code == 409
        data = response.json()
        assert data["detail"]["code"] == "SCHEDULE_ALREADY_CANCELLED"
        assert "cancelled" in data["detail"]["message"].lower()
        assert "terminal" in data["detail"]["message"].lower()

        # Row status MUST remain cancelled — gate sits BEFORE the stop branch
        # so no code path runs that could mutate the DB.
        scheduler_source.status = SourceStatus.cancelled.value  # unchanged
        mock_manager._source_repository.get_source_config.assert_called_once_with("scheduler-1")

        # The stop path MUST be inert: stop_adapter MUST NOT fire.
        mock_registry.stop_adapter.assert_not_called()
        mock_registry.get.assert_not_called()
        mock_registry._create_adapter_from_config.assert_not_called()
        mock_registry.register.assert_not_called()


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


# ===========================================================================
# PIN — the schedule LISTING tells the truth (schedule listing truth, Phase 1)
#
# Architecture v3 §3.3 — the listing floor, on EVERY surface:
#   1. recurrence truth (a chain task NEVER renders as "once"),
#   2. window (start / end / weekday / timezone),
#   3. the ACTUAL next-fire from ONE authoritative source (the STORED
#      config) — non-null for an armed recurring row,
#   4. last-fire + status (rendered from ``schedule_executions`` AS
#      STORED — never synthesized),
#   5. project scope.
#
# The tool surface is pinned in
# ``tests/unit/services/test_schedule_listing_truth.py``; BOTH surfaces
# derive through the SAME functions (``listing_view`` +
# ``compute_next_run``), so one derivation mutation breaks both.
#
# MUTATION-VERIFIED (skill ``mutation-verified-regression-pins``): the
# "adapter-only next-fire source" mutation turns this pin RED; the
# restore turns it GREEN again.
# ===========================================================================


class TestScheduleListingTruthRest:
    """The REST list surface of the truth-telling listing.

    ENVIRONMENTAL NOTE (measured 2026-11-23): this whole file needs the
    ambient AUTH environment scrubbed, or every request is answered by
    the auth middleware with a 302 and the tests fail with
    ``assert 302 == 200`` (70 of 73 at the base commit — PRE-EXISTING,
    not a regression).  Scrub the auth variables PER VARIABLE, e.g.::

        unset ENSEMBLE_AUTH_ENABLED ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE \
              ENSEMBLE_AUTH_ISSUER ENSEMBLE_AUTH_DISCOVERY_URL \
              ENSEMBLE_AUTH_CLIENT_ID ENSEMBLE_AUTH_CLIENT_SECRET \
              ENSEMBLE_AUTH_ALLOWED_ORIGINS

    (``env -u ENSEMBLE_AUTH_*`` is a NO-OP — bash does not glob env
    names.)
    """

    def _chain_source(self, source_id="chain-1", name="central-maintenance-weekly"):
        """A chain-shaped (windowed) scheduler row — the shape the
        maintenance seeder writes: ``recurrence="once"`` + a ``chain``
        block, with a ``run_at`` in the future."""
        from datetime import datetime, timedelta, timezone as _tz

        run_at = datetime.now(_tz.utc) + timedelta(days=3)
        source = Mock()
        source.source_id = source_id
        source.source_type = "scheduler"
        source.name = name
        source.config = {
            "agent": "maintenancer",
            "message": "weekly run",
            "timezone": "Asia/Ho_Chi_Minh",
            "recurrence": "once",
            "local_time": "03:30",
            "run_at": run_at.isoformat(),
            "instance_mode": "new_instance",
            "project_id": "agents-ensemble",
            "chain": {
                "mode": "weekly-random",
                "window_start": "01:00",
                "window_end": "05:00",
                "weekday": 0,
                "timezone": "Asia/Ho_Chi_Minh",
                "run_at_utc": run_at.isoformat(),
            },
        }
        source.credentials = None
        source.enabled = True
        source.status = "running"
        source.error_message = None
        source.created_at = "2026-11-01T00:00:00+00:00"
        source.updated_at = "2026-11-01T00:00:00+00:00"
        return source

    @pytest.mark.asyncio
    async def test_rest_list_renders_the_truth_for_a_chain_row(self, client, mock_manager):
        """1 + 2 + 3 + 5 on the REST surface."""
        mock_manager._source_repository.list_source_configs = Mock(
            return_value=[self._chain_source()]
        )
        mock_manager._source_repository.get_latest_execution = Mock(return_value=None)

        response = await client.get("/schedules")
        assert response.status_code == 200
        schedules = response.json()["schedules"]
        assert len(schedules) == 1
        item = schedules[0]
        # 1. recurrence truth — NEVER the stored "once".
        assert item["recurrence"] != "once"
        assert item["recurrence"] == "weekly (windowed)"
        # 2. window.
        assert item["window"] == {
            "start": "01:00",
            "end": "05:00",
            "weekday": 0,
            "timezone": "Asia/Ho_Chi_Minh",
        }
        # 3. the next-fire is NON-NULL (the stored config is the source).
        assert item["next_run_at"] is not None
        # 5. project scope.
        assert item["project_id"] == "agents-ensemble"
        # 4. last-fire + status are rendered from schedule_executions AS
        #    STORED — with no execution row they are null, never
        #    synthesized.
        assert item["last_run_at"] is None
        assert item["last_execution_status"] is None

    @pytest.mark.asyncio
    async def test_the_stored_config_wins_over_the_live_adapter(self, client, mock_manager):
        """ONE field, ONE source — the STORED config is authoritative on
        the REST surface too.  The adapter's in-memory value is a SECOND
        source (and it goes null whenever the adapter is absent); it
        survives only as a fallback for a row whose stored config yields
        no next-fire at all."""
        from datetime import datetime, timezone as _tz

        source = self._chain_source()
        mock_manager._source_repository.list_source_configs = Mock(
            return_value=[source]
        )
        mock_manager._source_repository.get_latest_execution = Mock(return_value=None)
        # A live adapter that claims a DIFFERENT next-fire.
        adapter = Mock()
        adapter._get_next_trigger_time = Mock(
            return_value=datetime(1999, 1, 1, 0, 0, tzinfo=_tz.utc)
        )
        registry = Mock()
        registry.get = Mock(return_value=adapter)
        mock_manager.source_registry = registry

        response = await client.get("/schedules")
        assert response.status_code == 200
        item = response.json()["schedules"][0]
        # The adapter's value would be 1999 — the STORED config wins.
        assert not item["next_run_at"].startswith("1999")
        assert item["next_run_at"].startswith(
            source.config["run_at"][:13]
        )


# ==================== Phase-1 fix-round REST pins (M-C / M-D / M-A) ====================


class TestSchedulePhase1GuardsRest:
    """REST-level pins for the Phase-1 fix-round (branch
    feature/schedule-listing-truth-phase1, round commits 38cb02c2c /
    a70b3980d / b6157743a).

    * **M-C** — a REST PUT RENAME enforces the live-label namespace
      EXCLUDING the row being renamed; a live/live collision is refused
      with HTTP 409 code ``SCHEDULE_LABEL_CONFLICT``.  Sub-cases:
      rename onto a CANCELLED row's label is ALLOWED; rename to the
      row's OWN name is a no-op (never a self-conflict); rename onto
      another LIVE row's label is 409.  A broken namespace scan is 503
      ``SCHEDULE_LABEL_CHECK_UNAVAILABLE`` (M-E's REST mapping).
    * **M-D/M-A REST legs** — the REST list renders the SAME instant as
      the shared service helper for the SAME row (ONE next-fire value);
      a disarmed (paused/cancelled) row renders ``next_run_at`` null
      with ``enabled``/``armed`` visible; REST GET likewise; a PUT on a
      disarmed row renders the same gate.

    The unit-side pins live in
    ``tests/unit/services/test_schedule_phase1_guards.py``; the seed
    (M-B) pins live in ``tests/unit/test_maintenance_seed_selfheal.py``.
    """

    @pytest.fixture(autouse=True)
    def _reset_svc_deps(self):
        """Reset the shared scheduling-service deps after each test so a
        configured service never leaks into a neighbour."""
        yield
        from daemon.services import scheduling_service as _svc

        _svc._deps.source_repo = None
        _svc._deps.source_registry = None
        _svc._deps.project_repository = None
        _svc._source_locks.clear()

    # -- row builders -------------------------------------------------------

    def _rest_source(
        self,
        source_id="sched-1",
        name="my-schedule",
        config=None,
        status="running",
        enabled=True,
    ):
        """A scheduler source Mock with EXPLICIT status/enabled (the
        file-level ``create_scheduler_source`` helper hardcodes
        status='stopped' — these pins need armed rows too)."""
        source = Mock()
        source.source_id = source_id
        source.source_type = "scheduler"
        source.name = name
        source.config = config or {
            "agent": "./agents/maintenancer",
            "message": "weekly run",
            "timezone": "Asia/Ho_Chi_Minh",
            "recurrence": "weekly",
            "local_time": "03:30",
            "schedule": "30 3 * * 0",
            "instance_mode": "new_instance",
            "project_id": "agents-ensemble",
        }
        source.credentials = None
        source.enabled = enabled
        source.status = status
        source.error_message = None
        source.created_at = "2026-11-01T00:00:00+00:00"
        source.updated_at = "2026-11-01T00:00:00+00:00"
        return source

    def _fake_repo(self):
        """A minimal PAGE-AWARE repo double for the TOOL surface (the
        identity pin derives the tool value through the REAL service)."""
        class _Repo:
            def __init__(self):
                self.rows = {}

            def add(self, row):
                self.rows[row.source_id] = row
                return row

            def get_source_config(self, source_id):
                return self.rows.get(source_id)

            def get_source_config_by_name(self, name):
                for row in self.rows.values():
                    if row.name == name:
                        return row
                return None

            def list_source_configs(self, enabled=None, status=None, limit=100, offset=0):
                rows = list(self.rows.values())
                if status is not None:
                    rows = [r for r in rows if r.status == status]
                if enabled is not None:
                    rows = [r for r in rows if r.enabled == enabled]
                return rows[offset : offset + limit]

            def get_latest_execution(self, schedule_id):
                return None

        return _Repo()

    # -- M-C: the REST PUT rename conflict gate ------------------------------

    @pytest.mark.asyncio
    async def test_put_rename_onto_another_live_row_label_is_409(
        self, client, mock_manager
    ):
        """M-C — renaming onto another LIVE row's label is refused with
        409 SCHEDULE_LABEL_CONFLICT."""
        existing = self._rest_source("sched-1", "my-schedule")
        live_other = self._rest_source("sched-2", "taken-label", status="running")
        mock_manager._source_repository.get_source_config = Mock(return_value=existing)
        mock_manager._source_repository.list_source_configs = Mock(
            return_value=[live_other]
        )

        response = await client.put("/schedules/sched-1", json={"name": "taken-label"})
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "SCHEDULE_LABEL_CONFLICT"
        # The rename never reached the repository.
        mock_manager._source_repository.update_source_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_put_rename_onto_a_cancelled_row_label_succeeds(
        self, client, mock_manager
    ):
        """M-C sub-case — a CANCELLED row keeps its name for history but
        no longer OWNS it: renaming onto its label is ALLOWED."""
        existing = self._rest_source("sched-1", "my-schedule")
        cancelled = self._rest_source("sched-3", "old-cancelled", status="cancelled")
        mock_manager._source_repository.get_source_config = Mock(return_value=existing)
        mock_manager._source_repository.list_source_configs = Mock(
            return_value=[cancelled]
        )
        updated = self._rest_source("sched-1", "old-cancelled")
        mock_manager._source_repository.update_source_config = Mock(return_value=updated)
        mock_manager._source_repository.get_latest_execution = Mock(return_value=None)

        response = await client.put("/schedules/sched-1", json={"name": "old-cancelled"})
        assert response.status_code == 200, response.text
        assert response.json()["name"] == "old-cancelled"

    @pytest.mark.asyncio
    async def test_put_rename_to_the_own_name_is_a_noop_never_a_self_conflict(
        self, client, mock_manager
    ):
        """M-C sub-case — renaming to the row's OWN name is a no-op: the
        conflict gate is skipped entirely (the exclusion guard keeps the
        row from ever conflicting with itself).  The scan sees the row
        ITSELF — so a broken exclusion (exclude-every-row mutation shape)
        turns this pin RED with a 409."""
        existing = self._rest_source("sched-1", "my-schedule")
        mock_manager._source_repository.get_source_config = Mock(return_value=existing)
        # The scan WILL see the row itself.
        mock_manager._source_repository.list_source_configs = Mock(
            return_value=[existing]
        )
        updated = self._rest_source("sched-1", "my-schedule")
        mock_manager._source_repository.update_source_config = Mock(return_value=updated)
        mock_manager._source_repository.get_latest_execution = Mock(return_value=None)

        response = await client.put("/schedules/sched-1", json={"name": "my-schedule"})
        assert response.status_code == 200, response.text
        assert response.json()["name"] == "my-schedule"

    @pytest.mark.asyncio
    async def test_put_rename_with_an_unavailable_scan_is_503(
        self, client, mock_manager
    ):
        """M-E REST mapping — a broken namespace scan on a rename is
        503 SCHEDULE_LABEL_CHECK_UNAVAILABLE (never 'no conflict')."""
        existing = self._rest_source("sched-1", "my-schedule")
        mock_manager._source_repository.get_source_config = Mock(return_value=existing)

        def _boom(*args, **kwargs):
            raise RuntimeError("repository unavailable")

        mock_manager._source_repository.list_source_configs = _boom

        response = await client.put("/schedules/sched-1", json={"name": "other-label"})
        assert response.status_code == 503, response.text
        assert (
            response.json()["detail"]["code"] == "SCHEDULE_LABEL_CHECK_UNAVAILABLE"
        )

    # -- M-D/M-A REST legs ---------------------------------------------------

    @pytest.mark.asyncio
    async def test_rest_list_next_run_is_the_same_instant_as_the_service_value(
        self, client, mock_manager
    ):
        """M-D — ONE next-fire value: the REST list renders the SAME
        instant as the shared service helper for the SAME row.  The row's
        stored config yields NO next-fire (a past-due one-shot) and a
        live adapter CLAIMS 1999 — the adapter's value must never reach
        either surface (the historical two-source divergence class)."""
        from datetime import datetime, timedelta, timezone as _tz
        from types import SimpleNamespace as _NS

        from daemon.services import scheduling_service as svc

        run_at_past = datetime.now(_tz.utc) - timedelta(days=1)
        row = _NS(
            source_id="past-1",
            source_type="scheduler",
            name="past-once",
            config={
                "agent": "ari",
                "message": "hi",
                "timezone": "UTC",
                "recurrence": "once",
                "local_time": run_at_past.strftime("%H:%M"),
                "run_at": run_at_past.isoformat(),
                "instance_mode": "new_instance",
                "project_id": "agents-ensemble",
            },
            credentials=None,
            enabled=True,
            autostart=True,
            status="running",
            error_message=None,
            created_at="2026-11-01T00:00:00+00:00",
            updated_at="2026-11-01T00:00:00+00:00",
        )
        fake = self._fake_repo()
        fake.add(row)
        adapter = Mock()
        adapter._get_next_trigger_time = Mock(
            return_value=datetime(1999, 1, 1, 0, 0, tzinfo=_tz.utc)
        )
        registry = Mock()
        registry.get = Mock(return_value=adapter)
        # The TOOL surface derives through the real service.
        svc.configure(source_repo=fake, source_registry=registry)
        # The REST surface sees the SAME row; the registry is visible to
        # it too (so a reintroduced adapter path WOULD be exercised).
        mock_manager._source_repository.list_source_configs = Mock(return_value=[row])
        mock_manager._source_repository.get_latest_execution = Mock(return_value=None)
        mock_manager.source_registry = registry

        response = await client.get("/schedules")
        assert response.status_code == 200
        items = response.json()["schedules"]
        assert len(items) == 1
        rest_next = items[0]["next_run_at"]
        tool_items = {i.source_id: i for i in await svc.list_schedules()}
        tool_next = tool_items["past-1"].next_run_at_utc
        # Same row, same instant — the stored-config helper's value
        # (null here), NEVER the adapter's 1999.
        assert tool_next is None
        assert rest_next is None, (
            "the REST list must render the shared helper's value "
            f"({tool_next!r}), not an adapter-derived one ({rest_next!r})"
        )
        assert not str(rest_next or "").startswith("1999")

    @pytest.mark.asyncio
    async def test_rest_list_disarmed_rows_render_null_next_run_with_enabled_armed_visible(
        self, client, mock_manager
    ):
        """M-A — a paused (enabled=False + stopped) row and a cancelled
        row render ``next_run_at`` null with ``enabled``/``armed``
        visible on the REST list."""
        paused = self._rest_source("paused-1", "paused-cron", status="stopped", enabled=False)
        cancelled = self._rest_source(
            "gone-1", "gone-row", status="cancelled", enabled=False
        )
        armed = self._rest_source("armed-1", "armed-cron", status="running", enabled=True)
        mock_manager._source_repository.list_source_configs = Mock(
            return_value=[paused, cancelled, armed]
        )
        mock_manager._source_repository.get_latest_execution = Mock(return_value=None)

        response = await client.get("/schedules", params={"include_cancelled": "true"})
        assert response.status_code == 200
        items = {s["id"]: s for s in response.json()["schedules"]}
        # PAUSED: no next-fire, disarmed, visible.
        assert items["paused-1"]["next_run_at"] is None
        assert items["paused-1"]["enabled"] is False
        assert items["paused-1"]["armed"] is False
        # CANCELLED: same gate.
        assert items["gone-1"]["next_run_at"] is None
        assert items["gone-1"]["armed"] is False
        # The ARMED neighbour keeps its next-fire (asymmetry control).
        assert items["armed-1"]["next_run_at"] is not None
        assert items["armed-1"]["enabled"] is True
        assert items["armed-1"]["armed"] is True

    @pytest.mark.asyncio
    async def test_rest_get_disarmed_row_renders_null_next_run(
        self, client, mock_manager
    ):
        """M-A — REST GET renders the same gate for a paused row, with
        ``enabled``/``armed`` visible."""
        from unittest.mock import AsyncMock

        from daemon.services import scheduling_service as svc

        repo = self._fake_repo()
        repo.add(
            self._rest_source("paused-2", "paused-weekly", status="stopped", enabled=False)
        )
        svc.configure(
            source_repo=repo, source_registry=Mock()
        )
        detail = await svc.get_schedule("paused-2")
        assert detail is not None
        mock_manager.scheduling_service.get_schedule = AsyncMock(return_value=detail)

        response = await client.get("/schedules/paused-2")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["next_run_at_local"] is None
        assert body["next_run_at_utc"] is None
        assert body["enabled"] is False
        assert body["armed"] is False

    @pytest.mark.asyncio
    async def test_rest_put_on_a_disarmed_row_renders_null_next_run(
        self, client, mock_manager
    ):
        """M-A — a PUT on a paused row renders NO next-fire in the
        response echo, with ``enabled``/``armed`` visible."""
        existing = self._rest_source("paused-3", "paused-daily", status="stopped", enabled=False)
        updated = self._rest_source(
            "paused-3", "paused-daily", status="stopped", enabled=False
        )
        mock_manager._source_repository.get_source_config = Mock(return_value=existing)
        mock_manager._source_repository.list_source_configs = Mock(return_value=[])
        mock_manager._source_repository.update_source_config = Mock(return_value=updated)
        mock_manager._source_repository.get_latest_execution = Mock(return_value=None)

        response = await client.put(
            "/schedules/paused-3", json={"config": {"priority": 3}}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["next_run_at"] is None
        assert body["enabled"] is False
        assert body["armed"] is False
