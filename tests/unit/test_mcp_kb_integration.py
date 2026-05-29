"""Integration tests for MCP KB server mounting and endpoint accessibility."""

import pytest
from unittest.mock import MagicMock
from fastapi import FastAPI
from starlette.testclient import TestClient


class TestKbMcpIntegration:
    """Integration tests for MCP KB server mounting and endpoint accessibility."""

    @pytest.fixture(autouse=True)
    def _reset_module_state(self):
        """Reset module-level state between tests."""
        import daemon.mcp.kb_server as mod
        mod._manager = None
        mod._mcp_server = None
        mod._http_app = None
        yield
        mod._manager = None
        mod._mcp_server = None
        mod._http_app = None

    @pytest.fixture
    def mock_manager(self):
        manager = MagicMock()
        manager._instance_repository = MagicMock()
        manager._job_queue_service = MagicMock()
        return manager

    @pytest.fixture
    def app(self, mock_manager):
        """Create FastAPI app with MCP KB server mounted (matching api.py pattern)."""
        from daemon.mcp.kb_server import (
            create_kb_mcp_server,
            set_kb_mcp_manager,
            get_kb_mcp_http_app,
            get_kb_mcp_sse_app,
        )
        
        set_kb_mcp_manager(mock_manager)
        create_kb_mcp_server()
        
        app = FastAPI()
        
        # Mount MCP KB transports (same order as production api.py)
        app.mount("/api/mcp/kb/sse", get_kb_mcp_sse_app("/api/mcp/kb/sse"))
        app.mount("/api/mcp/kb", get_kb_mcp_http_app())
        
        return app

    def test_streamable_http_endpoint_mounted(self, app):
        """Verify StreamableHTTP endpoint is mounted (request reaches MCP handler, not 404)."""
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/api/mcp/kb/mcp", json={
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 1,
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            }
        })
        # If endpoint is mounted (not 404), we get either:
        # - 200 with JSON response (MCP initialized properly)
        # - 500 with error (MCP runtime issue like missing async context)
        # - Any non-404 status
        # If endpoint is NOT mounted, we'd get 404
        assert response.status_code != 404, "StreamableHTTP endpoint should be mounted"

    def test_sse_endpoint_mounted(self, app):
        """Verify SSE endpoint is mounted (request reaches MCP handler, not 404)."""
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/mcp/kb/sse/sse")
        # Similar to above - any non-404 means endpoint is mounted
        assert response.status_code != 404, "SSE endpoint should be mounted"

    def test_tools_listable_via_streamable_http_mounted(self, app):
        """Verify MCP tools/list endpoint is mounted after initialization."""
        client = TestClient(app, raise_server_exceptions=False)
        # First initialize - endpoint should be mounted
        init_response = client.post("/api/mcp/kb/mcp", json={
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 1,
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            }
        })
        assert init_response.status_code != 404, "Initialize endpoint should be mounted"
        
        # Then list tools - endpoint should be mounted
        tools_response = client.post("/api/mcp/kb/mcp", json={
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 2,
        })
        assert tools_response.status_code != 404, "Tools/list endpoint should be mounted"
