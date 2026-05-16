"""Tests for Phase 3 API Router Extraction.

This module verifies that the Phase 3 refactoring correctly extracted endpoints
from the monolithic daemon/api.py into dedicated router files.

The refactoring:
- Reduced daemon/api.py from ~2095 to ~500 lines
- Created 7 new router files: agents, instances, messages, sources, mappings, schedules, webhooks
- Migrated 8 globals to app.state
- No logic changes - purely structural reorganization
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


# =============================================================================
# Group 1: Route Registration Verification
# =============================================================================

class TestRouteRegistration:
    """Test that all endpoints are correctly registered."""

    @pytest.fixture
    def app(self):
        """Create app without lifespan (to avoid initialization)."""
        # Patch lifespan to be a no-op for testing route registration
        from daemon.api import create_app, lifespan
        
        # Create app without lifespan for route inspection
        app = FastAPI(
            title="Ensemble Daemon",
            version="test",
        )
        
        # Import and include routers
        from daemon.routers import (
            agents_router,
            instances_router,
            messages_router,
            sources_router,
            mappings_router,
            schedules_router,
            webhooks_router,
            jobs_router,
            projects_router,
            queues_router,
            dlq_router,
        )
        
        # Create API router with /api prefix
        from fastapi import APIRouter
        api_router = APIRouter(prefix="/api")
        
        # Include routers - order matters for route matching
        api_router.include_router(agents_router)
        api_router.include_router(instances_router)
        api_router.include_router(messages_router)
        api_router.include_router(sources_router)
        api_router.include_router(mappings_router)
        api_router.include_router(schedules_router)
        api_router.include_router(webhooks_router)
        api_router.include_router(jobs_router)
        api_router.include_router(projects_router)
        api_router.include_router(queues_router)
        api_router.include_router(dlq_router)
        
        app.include_router(api_router)
        
        return app

    def test_route_count_with_methods(self, app):
        """Verify all 33+ endpoints are registered."""
        # Count routes that have actual HTTP methods (not just mounted routers)
        routes_with_methods = []
        for route in app.routes:
            if hasattr(route, 'methods') and route.methods:
                for method in route.methods:
                    if method not in ('HEAD', 'OPTIONS'):
                        routes_with_methods.append(f"{method} {route.path}")
        
        print(f"\nRegistered routes with methods ({len(routes_with_methods)}):")
        for r in sorted(routes_with_methods):
            print(f"  {r}")
        
        # We expect at least 33 endpoints based on the refactoring
        assert len(routes_with_methods) >= 33, (
            f"Expected at least 33 endpoints, found {len(routes_with_methods)}"
        )

    def test_agents_endpoints_exist(self, app):
        """Verify agent endpoints are registered."""
        paths = {r.path for r in app.routes if hasattr(r, 'methods')}
        
        expected_paths = [
            "/api/agents",          # GET list, POST create
            "/api/agents/{agent_id}",  # DELETE
        ]
        
        for path in expected_paths:
            assert path in paths, f"Missing expected path: {path}"

    def test_instances_endpoints_exist(self, app):
        """Verify instance endpoints are registered."""
        paths = {r.path for r in app.routes if hasattr(r, 'methods')}
        
        expected_paths = [
            "/api/instances",           # GET list, POST create
            "/api/instances/{instance_id}",  # GET, DELETE
            "/api/instances/{instance_id}/pause",  # POST
            "/api/instances/{instance_id}/messages",  # GET
            "/api/instances/{instance_id}/events",  # SSE
        ]
        
        for path in expected_paths:
            assert path in paths, f"Missing expected path: {path}"

    def test_messages_endpoints_exist(self, app):
        """Verify message endpoints are registered."""
        paths = {r.path for r in app.routes if hasattr(r, 'methods')}
        
        expected_paths = [
            "/api/instances/{instance_id}/messages",  # POST send, GET status
        ]
        
        for path in expected_paths:
            assert path in paths, f"Missing expected path: {path}"

    def test_sources_endpoints_exist(self, app):
        """Verify source endpoints are registered."""
        paths = {r.path for r in app.routes if hasattr(r, 'methods')}
        
        expected_paths = [
            "/api/sources",                    # GET list, POST create
            "/api/sources/test",               # POST test
            "/api/sources/{source_id}",        # GET, PUT, DELETE
            "/api/sources/{source_id}/start",  # POST
            "/api/sources/{source_id}/stop",   # POST
        ]
        
        for path in expected_paths:
            assert path in paths, f"Missing expected path: {path}"

    def test_mappings_endpoints_exist(self, app):
        """Verify mapping endpoints are registered."""
        paths = {r.path for r in app.routes if hasattr(r, 'methods')}
        
        expected_paths = [
            "/api/sources/{source_id}/mappings",              # GET list, POST create
            "/api/sources/{source_id}/mappings/{mapping_id}", # DELETE
        ]
        
        for path in expected_paths:
            assert path in paths, f"Missing expected path: {path}"

    def test_schedules_endpoints_exist(self, app):
        """Verify schedule endpoints are registered."""
        paths = {r.path for r in app.routes if hasattr(r, 'methods')}
        
        expected_paths = [
            "/api/schedules",                                  # GET list
            "/api/schedules/{schedule_id}",                    # PUT update
            "/api/schedules/{schedule_id}/trigger",            # POST
            "/api/schedules/{schedule_id}/start",             # POST
            "/api/schedules/{schedule_id}/stop",              # POST
            "/api/schedules/{schedule_id}/executions",        # GET
        ]
        
        for path in expected_paths:
            assert path in paths, f"Missing expected path: {path}"

    def test_webhooks_endpoints_exist(self, app):
        """Verify webhook endpoints are registered."""
        paths = {r.path for r in app.routes if hasattr(r, 'methods')}
        
        expected_paths = [
            "/api/webhooks/{source_id}",  # POST receive
        ]
        
        for path in expected_paths:
            assert path in paths, f"Missing expected path: {path}"

    def test_jobs_endpoints_exist(self, app):
        """Verify jobs endpoints (pre-Phase 3) are still registered."""
        paths = {r.path for r in app.routes if hasattr(r, 'methods')}
        
        expected_paths = [
            "/api/jobs",                     # GET list, POST create
            "/api/jobs/{job_id}",            # GET
            "/api/jobs/{job_id}/cancel",     # POST
            "/api/jobs/{job_id}/retry",      # POST
            "/api/jobs/{job_id}/restore",    # POST
            "/api/jobs/{job_id}/events",     # SSE
        ]
        
        for path in expected_paths:
            assert path in paths, f"Missing expected path: {path}"

    def test_projects_endpoints_exist(self, app):
        """Verify projects endpoints (pre-Phase 3) are still registered."""
        paths = {r.path for r in app.routes if hasattr(r, 'methods')}
        
        expected_paths = [
            "/api/projects",                           # GET list, POST create
            "/api/projects/{project_id}",              # GET
            "/api/projects/{project_id}/pause-queue",  # POST
            "/api/projects/{project_id}/resume-queue", # POST
        ]
        
        for path in expected_paths:
            assert path in paths, f"Missing expected path: {path}"

    def test_queues_endpoints_exist(self, app):
        """Verify queues endpoints (pre-Phase 3) are still registered."""
        paths = {r.path for r in app.routes if hasattr(r, 'methods')}
        
        expected_paths = [
            "/api/projects/{project_id}/queues",                    # GET list, POST create
            "/api/projects/{project_id}/queues/{queue_id}",          # GET, PATCH, DELETE
            "/api/projects/{project_id}/queues/{queue_id}/start",   # POST
            "/api/projects/{project_id}/queues/{queue_id}/stop",    # POST
        ]
        
        for path in expected_paths:
            assert path in paths, f"Missing expected path: {path}"

    def test_dlq_endpoints_exist(self, app):
        """Verify DLQ endpoints (pre-Phase 3) are still registered."""
        paths = {r.path for r in app.routes if hasattr(r, 'methods')}
        
        expected_paths = [
            "/api/projects/{project_id}/dlq",                              # GET list, DELETE cleanup
            "/api/projects/{project_id}/dlq/{dlq_id}",                     # GET, DELETE
            "/api/projects/{project_id}/dlq/{dlq_id}/replay",             # POST
            "/api/projects/{project_id}/dlq/replay-all",                   # POST
        ]
        
        for path in expected_paths:
            assert path in paths, f"Missing expected path: {path}"

    def test_http_methods_are_correct(self, app):
        """Verify HTTP methods match expected behavior."""
        # Build a map of path -> methods (merge duplicate paths)
        path_methods: dict[str, set] = {}
        for route in app.routes:
            if hasattr(route, 'methods') and route.methods:
                path = route.path
                methods = route.methods - {"HEAD", "OPTIONS"}
                if path not in path_methods:
                    path_methods[path] = set()
                path_methods[path].update(methods)
        
        # Verify key endpoints have correct methods
        expected_methods = {
            "/api/agents": {"POST", "GET"},
            "/api/agents/{agent_id}": {"DELETE"},
            "/api/instances": {"POST", "GET"},
            "/api/instances/{instance_id}": {"GET", "DELETE"},
            "/api/instances/{instance_id}/pause": {"POST"},
            "/api/sources": {"POST", "GET"},
            "/api/sources/test": {"POST"},
            "/api/schedules": {"GET"},
            "/api/webhooks/{source_id}": {"POST"},
            "/api/jobs": {"POST", "GET"},
            "/api/projects": {"POST", "GET"},
        }
        
        for path, expected in expected_methods.items():
            actual = path_methods.get(path, set())
            assert actual == expected, (
                f"Path {path}: expected {expected}, got {actual}"
            )

    def test_sse_streaming_endpoint_exists(self, app):
        """Verify SSE streaming endpoint is registered."""
        paths = {r.path for r in app.routes if hasattr(r, 'methods')}
        
        # SSE endpoints should exist for instances
        assert "/api/instances/{instance_id}/events" in paths

    def test_api_prefix_applied(self, app):
        """Verify all routes have /api prefix."""
        # Exclude FastAPI internal routes that don't need /api prefix
        internal_routes = {
            '/',
            '/{path:path}',
            '/openapi.json',
            '/docs',
            '/redoc',
            '/docs/oauth2-redirect',
        }
        
        for route in app.routes:
            if hasattr(route, 'path') and route.path:
                # Skip internal routes and app-level routes
                if route.path in internal_routes:
                    continue
                if not route.path.startswith('/api'):
                    assert route.path.startswith('/api'), (
                        f"Route {route.path} should have /api prefix"
                    )


# =============================================================================
# Group 2: app.state Attributes
# =============================================================================

class TestAppStateAttributes:
    """Test that app.state has all required attributes."""

    def test_app_state_attributes_defined_in_lifespan(self):
        """Verify all 9 app.state attributes are set in lifespan function.
        
        Looking at daemon/api.py lifespan():
        - manager (InstanceManager)
        - start_time (float)
        - credential_manager (CredentialManager)
        - job_queue_service (JobQueueService)
        - job_processor (JobProcessor)
        - job_queue_mgmt_service (JobQueueMgmtService)
        - retry_scheduler (RetryScheduler or None)
        - dispatch_event_bus (DispatchEventBus)
        - live_hub (LiveEventHub) - pre-existing
        """
        import inspect
        from daemon.api import lifespan
        
        source = inspect.getsource(lifespan)
        
        expected_attributes = [
            'app.state.manager',
            'app.state.start_time',
            'app.state.credential_manager',
            'app.state.job_queue_service',
            'app.state.job_processor',
            'app.state.job_queue_mgmt_service',
            'app.state.retry_scheduler',
            'app.state.dispatch_event_bus',
            'app.state.live_hub',
        ]
        
        for attr in expected_attributes:
            assert attr in source, (
                f"Expected app.state attribute '{attr}' to be set in lifespan"
            )

    def test_lifespan_imports_all_required_services(self):
        """Verify lifespan imports all required service classes."""
        import inspect
        from daemon.api import lifespan
        
        source = inspect.getsource(lifespan)
        
        # These should be imported inside the lifespan function
        expected_imports = [
            'InstanceManager',
            'CredentialManager',
            'JobQueueService',
            'JobProcessor',
            'JobQueueMgmtService',
            'DispatchEventBus',
            'RetryScheduler',
            'LiveEventHub',
        ]
        
        for expected in expected_imports:
            assert expected in source, (
                f"Expected '{expected}' to be imported in lifespan"
            )

    def test_live_hub_from_manager(self):
        """Verify live_hub is sourced from manager._live_hub."""
        import inspect
        from daemon.api import lifespan
        
        source = inspect.getsource(lifespan)
        
        assert 'app.state.live_hub = manager._live_hub' in source


# =============================================================================
# Group 3: Backward Compatibility
# =============================================================================

class TestBackwardCompatibility:
    """Test that backward-compatible re-exports work."""

    def test_validate_agent_id_exported_from_api(self):
        """Test that validate_agent_id is re-exported from daemon.api."""
        from daemon.api import validate_agent_id
        
        # Should be callable
        assert callable(validate_agent_id)

    def test_validate_agent_id_from_utils_is_same_function(self):
        """Test that re-exported validate_agent_id is the same as in utils."""
        from daemon.api import validate_agent_id
        from daemon.utils import validate_agent_id as utils_validate_agent_id
        
        # Should be the same function object
        assert validate_agent_id is utils_validate_agent_id

    def test_send_message_exported_from_api(self):
        """Test that send_message is re-exported from daemon.api."""
        from daemon.api import send_message
        
        # Should be callable
        assert callable(send_message)

    def test_send_message_from_messages_is_same_function(self):
        """Test that re-exported send_message is the same as in messages router."""
        from daemon.api import send_message
        from daemon.routers.messages import send_message as router_send_message
        
        # Should be the same function object
        assert send_message is router_send_message

    def test_validate_instance_mode_in_utils(self):
        """Test that validate_instance_mode is accessible from daemon.utils."""
        from daemon.utils import validate_instance_mode
        
        # Should be callable
        assert callable(validate_instance_mode)

    def test_validate_instance_mode_function_signature(self):
        """Test validate_instance_mode accepts expected parameters."""
        from daemon.utils import validate_instance_mode
        import inspect
        
        sig = inspect.signature(validate_instance_mode)
        params = list(sig.parameters.keys())
        
        # Should have these parameters
        assert 'instance_mode' in params
        assert 'schedule_type' in params or 'config' in params


# =============================================================================
# Group 4: validate_instance_mode Behavior
# =============================================================================

class TestValidateInstanceMode:
    """Test validate_instance_mode function behavior."""

    def test_valid_new_instance_mode(self):
        """Test that 'new_instance' is accepted."""
        from daemon.utils import validate_instance_mode
        
        result = validate_instance_mode(instance_mode='new_instance')
        
        assert result == {'instance_mode': 'new_instance'}

    def test_valid_reuse_instance_mode(self):
        """Test that 'reuse_instance' is accepted."""
        from daemon.utils import validate_instance_mode
        
        result = validate_instance_mode(instance_mode='reuse_instance')
        
        assert result == {'instance_mode': 'reuse_instance'}

    def test_none_defaults_to_new_instance(self):
        """Test that None instance_mode defaults to 'new_instance'."""
        from daemon.utils import validate_instance_mode
        
        result = validate_instance_mode(instance_mode=None)
        
        assert result == {'instance_mode': 'new_instance'}

    def test_invalid_mode_raises_error(self):
        """Test that invalid instance_mode raises HTTPException."""
        from daemon.utils import validate_instance_mode
        from fastapi import HTTPException
        
        with pytest.raises(HTTPException) as exc_info:
            validate_instance_mode(instance_mode='invalid_mode')
        
        assert exc_info.value.status_code == 400

    def test_one_time_schedule_forces_new_instance(self):
        """Test that one_time schedule type forces new_instance."""
        from daemon.utils import validate_instance_mode
        
        # Even if reuse_instance is specified, one_time should force new_instance
        result = validate_instance_mode(
            instance_mode='reuse_instance',
            schedule_type='one_time'
        )
        
        assert result == {'instance_mode': 'new_instance'}

    def test_one_time_via_config_forces_new_instance(self):
        """Test that run_at in config forces new_instance (one_time detection)."""
        from daemon.utils import validate_instance_mode
        
        result = validate_instance_mode(
            instance_mode='reuse_instance',
            config={'run_at': '2025-01-01T00:00:00Z'}
        )
        
        assert result == {'instance_mode': 'new_instance'}

    def test_interval_schedule_preserves_mode(self):
        """Test that interval schedule preserves the specified mode."""
        from daemon.utils import validate_instance_mode
        
        result = validate_instance_mode(
            instance_mode='reuse_instance',
            config={'interval_seconds': 60}
        )
        
        assert result == {'instance_mode': 'reuse_instance'}

    def test_cron_schedule_preserves_mode(self):
        """Test that cron schedule preserves the specified mode."""
        from daemon.utils import validate_instance_mode
        
        result = validate_instance_mode(
            instance_mode='reuse_instance',
            config={'schedule': '0 * * * *'}
        )
        
        assert result == {'instance_mode': 'reuse_instance'}


# =============================================================================
# Group 5: _get_manager Dependency Injection
# =============================================================================

class TestGetManager:
    """Test _get_manager function behavior."""

    def test_get_manager_from_instances_router(self):
        """Test _get_manager in instances router extracts manager from app.state."""
        from daemon.routers.instances import _get_manager
        
        # Create mock request with app.state
        mock_manager = MagicMock()
        mock_request = MagicMock(spec=Request)
        mock_request.app.state.manager = mock_manager
        
        # Call _get_manager
        result = _get_manager(mock_request)
        
        # Should return the manager
        assert result is mock_manager

    def test_get_manager_from_messages_router(self):
        """Test _get_manager in messages router extracts manager from app.state."""
        from daemon.routers.messages import _get_manager
        
        # Create mock request with app.state
        mock_manager = MagicMock()
        mock_request = MagicMock(spec=Request)
        mock_request.app.state.manager = mock_manager
        
        # Call _get_manager
        result = _get_manager(mock_request)
        
        # Should return the manager
        assert result is mock_manager

    def test_get_manager_from_sources_router(self):
        """Test _get_manager in sources router extracts manager from app.state."""
        from daemon.routers.sources import _get_manager
        
        # Create mock request with app.state
        mock_manager = MagicMock()
        mock_request = MagicMock(spec=Request)
        mock_request.app.state.manager = mock_manager
        
        # Call _get_manager
        result = _get_manager(mock_request)
        
        # Should return the manager
        assert result is mock_manager

    def test_get_manager_from_mappings_router(self):
        """Test _get_manager in mappings router extracts manager from app.state."""
        from daemon.routers.mappings import _get_manager
        
        # Create mock request with app.state
        mock_manager = MagicMock()
        mock_request = MagicMock(spec=Request)
        mock_request.app.state.manager = mock_manager
        
        # Call _get_manager
        result = _get_manager(mock_request)
        
        # Should return the manager
        assert result is mock_manager

    def test_get_manager_from_schedules_router(self):
        """Test _get_manager in schedules router extracts manager from app.state."""
        from daemon.routers.schedules import _get_manager
        
        # Create mock request with app.state
        mock_manager = MagicMock()
        mock_request = MagicMock(spec=Request)
        mock_request.app.state.manager = mock_manager
        
        # Call _get_manager
        result = _get_manager(mock_request)
        
        # Should return the manager
        assert result is mock_manager

    def test_get_manager_from_webhooks_router(self):
        """Test _get_manager in webhooks router extracts manager from app.state."""
        from daemon.routers.webhooks import _get_manager
        
        # Create mock request with app.state
        mock_manager = MagicMock()
        mock_request = MagicMock(spec=Request)
        mock_request.app.state.manager = mock_manager
        
        # Call _get_manager
        result = _get_manager(mock_request)
        
        # Should return the manager
        assert result is mock_manager

    def test_get_manager_returns_request_app_state_manager(self):
        """Verify _get_manager directly accesses request.app.state.manager."""
        import inspect
        from daemon.routers.instances import _get_manager
        
        source = inspect.getsource(_get_manager)
        
        # Should directly access request.app.state.manager
        assert 'request.app.state.manager' in source


# =============================================================================
# Group 6: Router File Structure
# =============================================================================

class TestRouterFileStructure:
    """Test that router files exist and have expected structure."""

    def test_all_router_files_exist(self):
        """Verify all expected router files exist."""
        import os
        from pathlib import Path
        
        router_dir = Path(__file__).parent.parent.parent / 'daemon' / 'routers'
        
        expected_files = [
            'agents.py',
            'instances.py',
            'messages.py',
            'sources.py',
            'mappings.py',
            'schedules.py',
            'webhooks.py',
            'jobs.py',
            'projects.py',
            'queues.py',
            'dlq.py',
            '__init__.py',
        ]
        
        for filename in expected_files:
            filepath = router_dir / filename
            assert filepath.exists(), f"Expected router file not found: {filename}"

    def test_routers_init_exports_all(self):
        """Verify routers __init__.py exports all routers."""
        from daemon.routers import (
            agents_router,
            instances_router,
            messages_router,
            mappings_router,
            schedules_router,
            sources_router,
            webhooks_router,
            jobs_router,
            projects_router,
            queues_router,
            dlq_router,
        )
        
        # All routers should be defined (not None)
        assert agents_router is not None
        assert instances_router is not None
        assert messages_router is not None
        assert mappings_router is not None
        assert schedules_router is not None
        assert sources_router is not None
        assert webhooks_router is not None
        assert jobs_router is not None
        assert projects_router is not None
        assert queues_router is not None
        assert dlq_router is not None

    def test_phase3_routers_have_correct_prefixes(self):
        """Verify Phase 3 router files have correct path prefixes."""
        # Phase 3 routers: agents, instances, messages, sources, mappings, schedules, webhooks
        # (jobs, projects, queues, dlq are pre-Phase 3)
        
        from daemon.routers import (
            agents_router,
            instances_router,
            messages_router,
            sources_router,
            mappings_router,
            schedules_router,
            webhooks_router,
        )
        
        # List of (router_name, router, expected_prefix)
        routers_to_check = [
            ("agents", agents_router, '/agents'),
            ("instances", instances_router, '/instances'),
            ("messages", messages_router, '/instances'),  # messages uses /instances prefix
            ("sources", sources_router, '/sources'),
            ("mappings", mappings_router, '/sources'),   # mappings uses /sources prefix
            ("schedules", schedules_router, '/schedules'),
            ("webhooks", webhooks_router, '/webhooks'),
        ]
        
        for name, router, expected_prefix in routers_to_check:
            assert router.prefix == expected_prefix, (
                f"Router {name} expected prefix '{expected_prefix}', "
                f"got '{router.prefix}'"
            )


# =============================================================================
# Group 7: API Module Size Verification
# =============================================================================

class TestApiModuleSize:
    """Test that daemon/api.py was reduced to expected size."""

    def test_api_module_is_small(self):
        """Verify daemon/api.py is under 600 lines (was ~2095 before refactoring)."""
        from pathlib import Path
        
        api_path = Path(__file__).parent.parent.parent / 'daemon' / 'api.py'
        
        with open(api_path, 'r') as f:
            lines = f.readlines()
        
        # Filter out empty lines and comments
        code_lines = [
            line for line in lines
            if line.strip() and not line.strip().startswith('#')
        ]
        
        print(f"\ndaemon/api.py statistics:")
        print(f"  Total lines: {len(lines)}")
        print(f"  Non-empty/non-comment lines: {len(code_lines)}")
        
        # After refactoring, should be under 600 lines
        assert len(lines) < 600, (
            f"daemon/api.py has {len(lines)} lines, expected under 600 after refactoring"
        )

    def test_api_module_only_has_health_and_info_endpoints(self):
        """Verify daemon/api.py only has health and info endpoints.
        
        These are intentionally in api.py as they're app-level endpoints.
        All other endpoints should be in router files.
        """
        from pathlib import Path
        
        api_path = Path(__file__).parent.parent.parent / 'daemon' / 'api.py'
        
        with open(api_path, 'r') as f:
            content = f.read()
        
        # Check for endpoint decorator patterns
        endpoint_indicators = [
            '@router.post',
            '@router.get',
            '@router.put',
            '@router.delete',
            '@router.patch',
        ]
        
        found_endpoints = [
            indicator for indicator in endpoint_indicators
            if indicator in content
        ]
        
        # Only @api_router.get should exist (for /health and /info)
        assert len(found_endpoints) == 0, (
            f"daemon/api.py contains endpoint decorators that should be in routers: {found_endpoints}"
        )
        
        # @api_router.get is allowed for /health and /info
        # Verify these are the only ones
        import re
        api_router_get_matches = re.findall(r'@api_router\.get\([^)]+\)', content)
        
        # Should have exactly 2: /health and /info
        assert len(api_router_get_matches) <= 2, (
            f"daemon/api.py should only have /health and /info endpoints, "
            f"found: {api_router_get_matches}"
        )


# =============================================================================
# Group 8: Integration Smoke Tests
# =============================================================================

class TestRouterIntegration:
    """Integration tests for router functionality."""

    def test_agents_router_is_api_router_instance(self):
        """Verify routers are FastAPI APIRouter instances."""
        from fastapi import APIRouter
        from daemon.routers import agents_router
        
        assert isinstance(agents_router, APIRouter)

    def test_all_routers_have_tags(self):
        """Verify all routers have tags for OpenAPI documentation."""
        from daemon.routers import (
            agents_router,
            instances_router,
            jobs_router,
            projects_router,
            queues_router,
            dlq_router,
        )
        
        for router in [agents_router, instances_router, jobs_router, projects_router, queues_router, dlq_router]:
            assert router.tags, f"Router {router.prefix} should have tags"

    def test_schemas_router_imports_schemas(self):
        """Verify routers properly import from schemas module."""
        from daemon.routers.jobs import (
            JobCreateRequest,
            JobResponse,
            JobListResponse,
        )
        
        assert JobCreateRequest is not None
        assert JobResponse is not None
        assert JobListResponse is not None
