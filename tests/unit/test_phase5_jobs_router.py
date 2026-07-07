"""Tests for Phase 5 Jobs Router Cleanup & Lock Deduplication.

This module verifies that Phase 5 correctly:
1. Split daemon/routers/jobs.py into 3 sub-routers + thin aggregator
2. Extracted _release_job_lock() helper with proper parameter handling
3. Maintains backward compatibility

Phase 5 refactoring:
- daemon/routers/jobs.py: Thin aggregator including all 3 sub-routers
- daemon/routers/jobs_crud.py: CRUD endpoints (POST /, GET /{job_id}, GET /)
- daemon/routers/jobs_management.py: Management endpoints (DELETE, cancel, restore, retry)
- daemon/routers/jobs_streaming.py: SSE streaming (GET /{job_id}/events)
- daemon/services/job_queue_service.py: _release_job_lock() with 4 scenarios
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI, HTTPException


# =============================================================================
# Group 1: Jobs Endpoint Route Registration
# =============================================================================

class TestJobsRouteRegistration:
    """Test that all jobs endpoints are correctly registered."""

    @pytest.fixture
    def app(self):
        """Create app with jobs router for route inspection."""
        from fastapi import APIRouter
        
        # Import the aggregator router from jobs.py
        from daemon.routers.jobs import router as jobs_router
        
        # Create app
        app = FastAPI(title="Test App")
        
        # Create API router with /api prefix
        api_router = APIRouter(prefix="/api")
        api_router.include_router(jobs_router)
        app.include_router(api_router)
        
        return app

    def test_all_jobs_routes_exist(self, app):
        """Verify all jobs endpoints are correctly registered."""
        paths = {r.path for r in app.routes if hasattr(r, 'methods')}

        expected_paths = [
            "/api/jobs",                      # POST create, GET list
            "/api/jobs/{job_id}",             # GET, DELETE
            "/api/jobs/{job_id}/cancel",      # POST
            "/api/jobs/{job_id}/restore",     # POST
            "/api/jobs/{job_id}/retry",       # POST
            "/api/jobs/{job_id}/events",      # GET SSE
            "/api/jobs/cleanup",              # POST System Jobs Cleanup
        ]

        print(f"\nRegistered job paths: {sorted(paths)}")
        
        for path in expected_paths:
            assert path in paths, f"Missing expected path: {path}"

    def test_http_methods_are_correct(self, app):
        """Verify HTTP methods match expected for each route."""
        # Build a map of path -> methods
        path_methods: dict[str, set] = {}
        for route in app.routes:
            if hasattr(route, 'methods') and route.methods:
                path = route.path
                methods = route.methods - {"HEAD", "OPTIONS"}
                if path not in path_methods:
                    path_methods[path] = set()
                path_methods[path].update(methods)
        
        # Verify expected methods
        expected_methods = {
            "/api/jobs": {"POST", "GET"},
            "/api/jobs/{job_id}": {"GET", "DELETE"},
            "/api/jobs/{job_id}/cancel": {"POST"},
            "/api/jobs/{job_id}/restore": {"POST"},
            "/api/jobs/{job_id}/retry": {"POST"},
            "/api/jobs/{job_id}/events": {"GET"},
        }
        
        print(f"\nPath methods map: {path_methods}")
        
        for path, expected in expected_methods.items():
            actual = path_methods.get(path, set())
            assert actual == expected, (
                f"Path {path}: expected {expected}, got {actual}"
            )

    def test_total_route_count(self, app):
        """Verify the jobs router has the expected total route count.

        After the System Jobs Cleanup feature landed, the count grew
        from 8 to 9 (the new ``POST /api/jobs/cleanup`` endpoint). The
        original test was written pre-cleanup against the Phase 5
        split-router refactor and pinned ``len == 8``; the matching
        ``test_all_jobs_routes_exist`` pins the actual paths so the
        shape is still validated against drift.
        """
        routes_with_methods = []
        for route in app.routes:
            if hasattr(route, 'methods') and route.methods:
                for method in route.methods:
                    if method not in ('HEAD', 'OPTIONS'):
                        routes_with_methods.append(f"{method} {route.path}")

        # Filter to only include /api/jobs routes (exclude internal FastAPI routes)
        jobs_routes = [r for r in routes_with_methods if '/api/jobs' in r]

        print(f"\nJobs routes ({len(jobs_routes)}):")
        for r in sorted(jobs_routes):
            print(f"  {r}")

        # We expect:
        # - POST /api/jobs
        # - GET /api/jobs
        # - GET /api/jobs/{job_id}
        # - DELETE /api/jobs/{job_id}
        # - POST /api/jobs/{job_id}/cancel
        # - POST /api/jobs/{job_id}/restore
        # - POST /api/jobs/{job_id}/retry
        # - GET /api/jobs/{job_id}/events
        # - POST /api/jobs/cleanup                           (System Jobs Cleanup)
        # = 9 endpoints
        assert len(jobs_routes) == 9, (
            f"Expected 9 job endpoints, found {len(jobs_routes)}"
        )


# =============================================================================
# Group 2: _release_job_lock() - All 4 Scenarios
# =============================================================================

class TestReleaseJobLock:
    """Test _release_job_lock() with all 4 scenarios."""

    @pytest.fixture
    def mock_lock_manager(self):
        """Create a mock lock manager."""
        lock_manager = MagicMock()
        lock_manager.release_queue_lock = AsyncMock()
        lock_manager.release_by_instance = AsyncMock(return_value=[])
        lock_manager.release = AsyncMock()
        return lock_manager

    @pytest.fixture
    def mock_service(self, mock_lock_manager):
        """Create a JobQueueService with mocked dependencies."""
        from daemon.services.job_queue_service import JobQueueService
        
        service = MagicMock(spec=JobQueueService)
        service._lock_manager = mock_lock_manager
        service._release_job_lock = JobQueueService._release_job_lock.__get__(
            service, JobQueueService
        )
        return service

    @pytest.mark.asyncio
    async def test_scenario_1_queue_id_and_project_id(self, mock_service, mock_lock_manager):
        """Scenario 1: queue_id set + project_id set → calls release_queue_lock."""
        await mock_service._release_job_lock(
            project_id="proj-123",
            queue_id="queue-456",
            job_id="job-789",
            release_by_instance=True,
        )
        
        mock_lock_manager.release_queue_lock.assert_called_once_with(
            "proj-123", "queue-456", "job-789"
        )
        mock_lock_manager.release_by_instance.assert_not_called()
        mock_lock_manager.release.assert_not_called()

    @pytest.mark.asyncio
    async def test_scenario_2_no_queue_id_with_instance_id_and_release_flag(
        self, mock_service, mock_lock_manager
    ):
        """Scenario 2: queue_id NOT set, project_id set, release_by_instance=True + instance_id → calls release_by_instance."""
        await mock_service._release_job_lock(
            project_id="proj-123",
            queue_id=None,
            job_id="job-789",
            instance_id="inst-abc",
            release_by_instance=True,
        )
        
        mock_lock_manager.release_by_instance.assert_called_once_with("inst-abc")
        mock_lock_manager.release_queue_lock.assert_not_called()
        mock_lock_manager.release.assert_not_called()

    @pytest.mark.asyncio
    async def test_scenario_3_no_queue_id_no_instance_id_with_release_flag(
        self, mock_service, mock_lock_manager
    ):
        """Scenario 3: queue_id NOT set, project_id set, release_by_instance=True + NO instance_id → does nothing."""
        await mock_service._release_job_lock(
            project_id="proj-123",
            queue_id=None,
            job_id="job-789",
            instance_id=None,
            release_by_instance=True,
        )
        
        # Should not call any release method
        mock_lock_manager.release_queue_lock.assert_not_called()
        mock_lock_manager.release_by_instance.assert_not_called()
        mock_lock_manager.release.assert_not_called()

    @pytest.mark.asyncio
    async def test_scenario_4_no_queue_id_with_release_flag_false(
        self, mock_service, mock_lock_manager
    ):
        """Scenario 4: queue_id NOT set, project_id set, release_by_instance=False → calls release(project_id, job_id)."""
        await mock_service._release_job_lock(
            project_id="proj-123",
            queue_id=None,
            job_id="job-789",
            release_by_instance=False,
        )
        
        mock_lock_manager.release.assert_called_once_with("proj-123", "job-789")
        mock_lock_manager.release_queue_lock.assert_not_called()
        mock_lock_manager.release_by_instance.assert_not_called()


# =============================================================================
# Group 3: Backward Compatibility
# =============================================================================

class TestBackwardCompatibility:
    """Test backward compatibility of the jobs router."""

    def test_router_import_from_jobs_module(self):
        """Test that 'from daemon.routers.jobs import router' works."""
        from daemon.routers.jobs import router
        
        # Should be a valid router
        assert router is not None

    def test_router_has_correct_prefix(self):
        """Test that the jobs aggregator router has correct prefix and tags."""
        from daemon.routers.jobs import router
        
        # The aggregator router itself has no prefix (sub-routers have /jobs)
        # but it should have the "jobs" tag
        assert "jobs" in router.tags

    def test_aggregator_includes_all_sub_routers(self):
        """Test that the aggregator includes all 3 sub-routers."""
        from daemon.routers.jobs import router
        
        # The router should have routes from sub-routers
        route_paths = {r.path for r in router.routes if hasattr(r, 'path')}
        
        # All sub-routers have /jobs prefix
        assert any(p.startswith('/jobs') for p in route_paths)

    def test_exports_from_jobs_module(self):
        """Test that jobs module exports expected items."""
        from daemon.routers import jobs
        
        # Should export router
        assert hasattr(jobs, 'router')
        
        # Should export service dependency accessors
        assert hasattr(jobs, 'get_job_queue_service')
        assert hasattr(jobs, 'get_dead_letter_svc')
        
        # Should export backward compatibility setters
        assert hasattr(jobs, 'set_job_queue_service')
        assert hasattr(jobs, 'set_dead_letter_service')

    def test_backward_compatibility_aliases(self):
        """Test backward compatibility aliases exist."""
        from daemon.routers import jobs
        
        # Task aliases should exist for backward compatibility
        assert hasattr(jobs, 'TaskResponse')
        assert hasattr(jobs, 'TaskListResponse')
        assert hasattr(jobs, 'TaskCreateRequest')
        assert hasattr(jobs, 'TaskValidationError')
        assert hasattr(jobs, 'TaskNotFoundResponse')

    def test_shared_utilities_exported(self):
        """Test that shared utilities are exported."""
        from daemon.routers import jobs
        
        # _job_to_response and TERMINAL_STATUSES should be exported
        assert hasattr(jobs, '_job_to_response')
        assert hasattr(jobs, 'TERMINAL_STATUSES')

    def test_jobs_router_from_routers_init(self):
        """Test that jobs_router can be imported from daemon.routers."""
        from daemon.routers import jobs_router
        
        assert jobs_router is not None


# =============================================================================
# Group 4: Service Dependency for Jobs Routers
# =============================================================================

class TestServiceDependency:
    """Test create_service_dependency() usage in jobs routers."""

    def test_get_job_queue_service_dependency(self):
        """Test that get_job_queue_service is a callable dependency."""
        from daemon.routers.jobs_crud import get_job_queue_service
        
        # Should be a callable
        assert callable(get_job_queue_service)
        
        # Should have set_service method
        assert hasattr(get_job_queue_service, 'set_service')
        assert callable(get_job_queue_service.set_service)

    def test_get_dead_letter_service_dependency(self):
        """Test that get_dead_letter_svc is a callable dependency."""
        from daemon.routers.jobs_crud import get_dead_letter_svc
        
        # Should be a callable
        assert callable(get_dead_letter_svc)
        
        # Should have set_service method
        assert hasattr(get_dead_letter_svc, 'set_service')
        assert callable(get_dead_letter_svc.set_service)

    def test_sub_routers_import_same_dependencies(self):
        """Test that sub-routers import dependencies from jobs_crud."""
        from daemon.routers.jobs_crud import (
            get_job_queue_service as crud_svc,
            get_dead_letter_svc as crud_dlq,
        )
        from daemon.routers.jobs_management import (
            get_job_queue_service as mgmt_svc,
            get_dead_letter_svc as mgmt_dlq,
        )
        from daemon.routers.jobs_streaming import (
            get_job_queue_service as stream_svc,
        )
        
        # All should reference the same dependency objects
        assert crud_svc is mgmt_svc
        assert crud_svc is stream_svc
        assert crud_dlq is mgmt_dlq


# =============================================================================
# Group 5: Service Dependency Raises 503 When Not Initialized
# =============================================================================

class TestServiceDependency503:
    """Test that service dependency raises 503 when service is not initialized."""

    def test_get_job_queue_service_raises_503_when_not_set(self):
        """Test that get_job_queue_service raises HTTPException 503 when not initialized."""
        from daemon.routers.jobs_crud import get_job_queue_service
        
        # Create a fresh dependency instance to test
        from daemon.utils import create_service_dependency
        from daemon.services.job_queue_service import JobQueueService
        
        fresh_dep = create_service_dependency(JobQueueService)
        
        with pytest.raises(HTTPException) as exc_info:
            fresh_dep()
        
        assert exc_info.value.status_code == 503
        assert "not initialized" in exc_info.value.detail.lower()

    def test_get_dead_letter_svc_raises_503_when_not_set(self):
        """Test that get_dead_letter_svc raises HTTPException 503 when not initialized."""
        from daemon.utils import create_service_dependency
        from daemon.services.dead_letter_service import DeadLetterService
        
        fresh_dep = create_service_dependency(DeadLetterService)
        
        with pytest.raises(HTTPException) as exc_info:
            fresh_dep()
        
        assert exc_info.value.status_code == 503
        assert "not initialized" in exc_info.value.detail.lower()

    def test_service_dependency_returns_instance_when_set(self):
        """Test that service dependency returns instance when set."""
        from daemon.utils import create_service_dependency
        from daemon.services.job_queue_service import JobQueueService
        
        fresh_dep = create_service_dependency(JobQueueService)
        
        # Create mock instance
        mock_instance = MagicMock(spec=JobQueueService)
        
        # Set the service
        fresh_dep.set_service(mock_instance)
        
        # Should return the instance
        result = fresh_dep()
        assert result is mock_instance

    def test_service_dependency_can_be_reset(self):
        """Test that service dependency can be reset by setting to None."""
        from daemon.utils import create_service_dependency
        from daemon.services.job_queue_service import JobQueueService
        
        fresh_dep = create_service_dependency(JobQueueService)
        
        # Set an instance
        mock_instance = MagicMock(spec=JobQueueService)
        fresh_dep.set_service(mock_instance)
        assert fresh_dep() is mock_instance
        
        # Reset by setting to None (using set_service with None)
        fresh_dep.set_service(None)
        
        # Should raise 503 again
        with pytest.raises(HTTPException) as exc_info:
            fresh_dep()
        
        assert exc_info.value.status_code == 503


# =============================================================================
# Group 6: Sub-Router Structure Verification
# =============================================================================

class TestSubRouterStructure:
    """Test that sub-routers have correct structure."""

    def test_jobs_crud_has_correct_prefix(self):
        """Test jobs_crud router has /jobs prefix."""
        from daemon.routers.jobs_crud import router
        
        assert router.prefix == "/jobs"
        assert "jobs" in router.tags

    def test_jobs_management_has_correct_prefix(self):
        """Test jobs_management router has /jobs prefix."""
        from daemon.routers.jobs_management import router
        
        assert router.prefix == "/jobs"
        assert "jobs" in router.tags

    def test_jobs_streaming_has_correct_prefix(self):
        """Test jobs_streaming router has /jobs prefix."""
        from daemon.routers.jobs_streaming import router
        
        assert router.prefix == "/jobs"
        assert "jobs" in router.tags

    def test_crud_endpoints_only_in_jobs_crud(self):
        """Test that CRUD endpoints are only in jobs_crud."""
        from daemon.routers.jobs_crud import router
        
        paths = {r.path for r in router.routes if hasattr(r, 'path')}
        
        # Should have POST "", GET "", GET "/{job_id}" (paths have /jobs prefix)
        assert "/jobs" in paths
        assert "/jobs/{job_id}" in paths

    def test_management_endpoints_only_in_jobs_management(self):
        """Test that management endpoints are only in jobs_management."""
        from daemon.routers.jobs_management import router
        
        paths = {r.path for r in router.routes if hasattr(r, 'path')}
        
        # Should have DELETE, POST /cancel, POST /restore, POST /retry (paths have /jobs prefix)
        assert "/jobs/{job_id}" in paths
        assert "/jobs/{job_id}/cancel" in paths
        assert "/jobs/{job_id}/restore" in paths
        assert "/jobs/{job_id}/retry" in paths

    def test_streaming_endpoints_only_in_jobs_streaming(self):
        """Test that streaming endpoints are only in jobs_streaming."""
        from daemon.routers.jobs_streaming import router
        
        paths = {r.path for r in router.routes if hasattr(r, 'path')}
        
        # Should have GET /{job_id}/events (paths have /jobs prefix)
        assert "/jobs/{job_id}/events" in paths


# =============================================================================
# Group 7: Lock Release Integration with Service Methods
# =============================================================================

class TestLockReleaseIntegration:
    """Test that _release_job_lock is called correctly from service methods."""

    def test_release_job_lock_signature(self):
        """Test that _release_job_lock has correct signature."""
        import inspect
        from daemon.services.job_queue_service import JobQueueService
        
        sig = inspect.signature(JobQueueService._release_job_lock)
        params = list(sig.parameters.keys())
        
        # Should have these parameters
        expected_params = [
            'self',
            'project_id',
            'queue_id',
            'job_id',
            'instance_id',
            'release_by_instance',
        ]
        
        for param in expected_params:
            assert param in params, f"Missing parameter: {param}"

    def test_complete_job_uses_release_job_lock(self):
        """Test that complete_job calls _release_job_lock with correct params."""
        import inspect
        from daemon.services.job_queue_service import JobQueueService
        
        source = inspect.getsource(JobQueueService.complete_job)
        
        # Should call _release_job_lock
        assert "_release_job_lock" in source
        # Should pass release_by_instance=False (for complete_job pattern)
        assert "release_by_instance=False" in source

    def test_fail_job_uses_release_job_lock(self):
        """Test that _fail_job calls _release_job_lock with correct params."""
        import inspect
        from daemon.services.job_queue_service import JobQueueService
        
        source = inspect.getsource(JobQueueService._fail_job)
        
        # Should call _release_job_lock
        assert "_release_job_lock" in source
        # Should pass release_by_instance=True (for _fail_job pattern)
        assert "release_by_instance=True" in source

    def test_complete_job_sync_uses_direct_lock_calls(self):
        """Test that complete_job_sync uses direct lock calls (not _release_job_lock)."""
        import inspect
        from daemon.services.job_queue_service import JobQueueService
        
        source = inspect.getsource(JobQueueService.complete_job_sync)
        
        # Should use asyncio.run_coroutine_threadsafe with direct lock calls
        assert "release_queue_lock" in source or "release" in source
        # Should NOT call _release_job_lock (sync context)
        assert "_release_job_lock" not in source


# =============================================================================
# Group 8: Quick Fix Verification
# =============================================================================

class TestQuickFixVerification:
    """Verify quick fixes applied during Phase 5."""

    def test_jobs_module_exports_terminal_statuses(self):
        """Verify TERMINAL_STATUSES is accessible from jobs module."""
        from daemon.routers.jobs import TERMINAL_STATUSES
        
        # Should be a set of terminal status values
        assert isinstance(TERMINAL_STATUSES, set)
        assert len(TERMINAL_STATUSES) > 0

    def test_jobs_module_exports_job_to_response(self):
        """Verify _job_to_response is accessible from jobs module."""
        from daemon.routers.jobs import _job_to_response
        
        # Should be callable
        assert callable(_job_to_response)

    def test_cancel_job_releases_lock(self):
        """Verify cancel_job in service properly releases locks."""
        import inspect
        from daemon.services.job_queue_service import JobQueueService
        
        source = inspect.getsource(JobQueueService.cancel_job)
        
        # Should release lock for PROCESSING jobs
        # Either directly or via helper
        assert "release" in source.lower() or "lock" in source.lower()
