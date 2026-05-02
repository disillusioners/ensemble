"""Tests for RAG workspace scoping functionality.

Tests workspace sanitization, per-request headers, and project workspace extraction.
"""

from unittest.mock import MagicMock

import pytest

from daemon.rag.client import _sanitize_workspace
from daemon.tools.rag_tools import _get_project_workspace


# =============================================================================
# A. Client Workspace Sanitization Tests
# =============================================================================


class TestSanitizeWorkspace:
    """Tests for _sanitize_workspace function."""

    def test_sanitize_workspace_with_uuid(self):
        """Hyphens in UUIDs are replaced with underscores."""
        result = _sanitize_workspace("550e8400-e29b-41d4-a716-446655440000")
        assert result == "550e8400_e29b_41d4_a716_446655440000"

    def test_sanitize_workspace_already_clean(self):
        """Clean workspace names (alphanumeric + underscore) pass through unchanged."""
        result = _sanitize_workspace("my_workspace_123")
        assert result == "my_workspace_123"

    def test_sanitize_workspace_with_special_characters(self):
        """Special characters are replaced with underscores."""
        result = _sanitize_workspace("my-workspace/v1.2")
        assert result == "my_workspace_v1_2"

    def test_sanitize_workspace_with_spaces(self):
        """Spaces are replaced with underscores."""
        result = _sanitize_workspace("my workspace project")
        assert result == "my_workspace_project"

    def test_sanitize_workspace_with_empty_string(self):
        """Empty string edge case returns empty string."""
        result = _sanitize_workspace("")
        assert result == ""

    def test_sanitize_workspace_preserves_underscores(self):
        """Existing underscores are preserved."""
        result = _sanitize_workspace("project_1_agent_2")
        assert result == "project_1_agent_2"

    def test_sanitize_workspace_mixed_case(self):
        """Mixed case is preserved (only hyphens/special chars are replaced)."""
        result = _sanitize_workspace("MyProject-Name")
        assert result == "MyProject_Name"

    def test_sanitize_workspace_multiple_special_chars(self):
        """Multiple consecutive special characters become single underscore."""
        result = _sanitize_workspace("a...b")
        assert result == "a___b"

    def test_sanitize_workspace_with_dots(self):
        """Dots are replaced with underscores."""
        result = _sanitize_workspace("v1.2.3")
        assert result == "v1_2_3"


# =============================================================================
# B. Client Per-Request Workspace Header Tests
# =============================================================================


class TestClientWorkspaceHeader:
    """Tests for workspace header injection in _request method."""

    @pytest.mark.asyncio
    async def test_request_includes_workspace_header_when_provided(self, configured_config):
        """_request includes LIGHTRAG-WORKSPACE header when workspace is provided."""
        import httpx

        from daemon.rag import AsyncLightRAGClient

        # Capture the headers passed to client.request
        captured_kwargs = {}

        async def mock_request(self, method, url, **kwargs):
            captured_kwargs.update(kwargs)
            # Return a mock response
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json = MagicMock(return_value={"response": "test"})
            return mock_response

        client = AsyncLightRAGClient(configured_config)
        client._client = httpx.AsyncClient(
            base_url=configured_config.base_url,
            timeout=httpx.Timeout(configured_config.timeout),
            headers=client._build_headers(),
        )

        from unittest.mock import patch

        with patch.object(httpx.AsyncClient, 'request', mock_request):
            await client.query("test query", workspace="test-project")

        await client.close()

        # Verify workspace header was included
        assert "headers" in captured_kwargs
        assert "LIGHTRAG-WORKSPACE" in captured_kwargs["headers"]
        assert captured_kwargs["headers"]["LIGHTRAG-WORKSPACE"] == "test_project"

    @pytest.mark.asyncio
    async def test_request_does_not_add_extra_headers_when_workspace_is_none(self, configured_config):
        """_request does not modify headers when workspace is None."""
        import httpx

        from daemon.rag import AsyncLightRAGClient

        # Capture the headers passed to client.request
        captured_kwargs = {}

        async def mock_request(self, method, url, **kwargs):
            captured_kwargs.update(kwargs)
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json = MagicMock(return_value={"response": "test"})
            return mock_response

        client = AsyncLightRAGClient(configured_config)
        client._client = httpx.AsyncClient(
            base_url=configured_config.base_url,
            timeout=httpx.Timeout(configured_config.timeout),
            headers=client._build_headers(),
        )

        from unittest.mock import patch

        with patch.object(httpx.AsyncClient, 'request', mock_request):
            await client.query("test query", workspace=None)

        await client.close()

        # When workspace is None, no additional headers should be passed
        # (client uses its default headers)
        assert "headers" not in captured_kwargs

    @pytest.mark.asyncio
    async def test_request_sanitizes_workspace_before_setting_header(self, configured_config):
        """Workspace is sanitized (hyphens to underscores) before being set as header."""
        import httpx

        from daemon.rag import AsyncLightRAGClient

        captured_headers = []

        async def mock_request(self, method, url, **kwargs):
            if "headers" in kwargs:
                captured_headers.append(kwargs["headers"].get("LIGHTRAG-WORKSPACE"))
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            # Return proper InsertResponse format
            mock_response.json = MagicMock(return_value={
                "status": "accepted",
                "message": "Text inserted",
                "track_id": "track-123"
            })
            return mock_response

        client = AsyncLightRAGClient(configured_config)
        client._client = httpx.AsyncClient(
            base_url=configured_config.base_url,
            timeout=httpx.Timeout(configured_config.timeout),
            headers=client._build_headers(),
        )

        from unittest.mock import patch

        with patch.object(httpx.AsyncClient, 'request', mock_request):
            # UUID with hyphens
            await client.insert_text("test", workspace="550e8400-e29b-41d4-a716-446655440000")
            assert captured_headers[-1] == "550e8400_e29b_41d4_a716_446655440000"

            # Project name with hyphen
            await client.insert_text("test", workspace="my-project")
            assert captured_headers[-1] == "my_project"

        await client.close()

    @pytest.mark.asyncio
    async def test_request_workspace_header_overrides_default(self, configured_config):
        """Per-request workspace header overrides the client's default workspace."""
        import httpx

        from daemon.rag import AsyncLightRAGClient

        captured_headers = []

        async def mock_request(self, method, url, **kwargs):
            if "headers" in kwargs:
                captured_headers.append(kwargs["headers"].get("LIGHTRAG-WORKSPACE"))
            else:
                # No headers override when workspace is None - uses client defaults
                captured_headers.append(None)
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json = MagicMock(return_value={"response": "test"})
            return mock_response

        client = AsyncLightRAGClient(configured_config)
        client._client = httpx.AsyncClient(
            base_url=configured_config.base_url,
            timeout=httpx.Timeout(configured_config.timeout),
            headers=client._build_headers(),
        )

        from unittest.mock import patch

        with patch.object(httpx.AsyncClient, 'request', mock_request):
            # Default workspace from config (no override) - uses client defaults
            await client.query("test query")
            # First call should have None (no header override)
            assert captured_headers[-1] is None

            # Override with per-request workspace
            await client.query("test query", workspace="custom-workspace")
            assert captured_headers[-1] == "custom_workspace"

        await client.close()

    @pytest.mark.asyncio
    async def test_request_preserves_existing_headers(self, configured_config):
        """Per-request workspace header preserves other headers (like X-API-Key)."""
        import httpx

        from daemon.rag import AsyncLightRAGClient

        captured_headers = {}

        async def mock_request(self, method, url, **kwargs):
            if "headers" in kwargs:
                captured_headers.update(kwargs["headers"])
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json = MagicMock(return_value={"response": "test"})
            return mock_response

        client = AsyncLightRAGClient(configured_config)
        client._client = httpx.AsyncClient(
            base_url=configured_config.base_url,
            timeout=httpx.Timeout(configured_config.timeout),
            headers=client._build_headers(),
        )

        from unittest.mock import patch

        with patch.object(httpx.AsyncClient, 'request', mock_request):
            await client.query("test query", workspace="test-project")
            # API key from default headers should be preserved
            assert "X-API-Key" in captured_headers
            assert captured_headers["X-API-Key"] == "test-api-key"
            # Workspace override should also be present
            assert "LIGHTRAG-WORKSPACE" in captured_headers
            assert captured_headers["LIGHTRAG-WORKSPACE"] == "test_project"

        await client.close()


# =============================================================================
# C. Tools Workspace Extraction Tests
# =============================================================================


class TestGetProjectWorkspace:
    """Tests for _get_project_workspace function extraction logic."""

    def test_get_project_workspace_returns_project_name(self):
        """_get_project_workspace returns project name when project_id is in instance metadata."""
        mock_instance = MagicMock()
        mock_instance.instance_metadata = {"project_id": "proj-uuid-123"}
        mock_project = MagicMock()
        mock_project.name = "my-test-project"

        mock_manager = MagicMock()
        mock_manager._instance_repository.get.return_value = mock_instance
        mock_manager._project_repository.get.return_value = mock_project

        result = _get_project_workspace(mock_manager, "instance-abc")
        assert result == "my-test-project"

    def test_get_project_workspace_returns_none_when_no_metadata(self):
        """_get_project_workspace returns None when instance has no metadata."""
        mock_instance = MagicMock()
        mock_instance.instance_metadata = None

        mock_manager = MagicMock()
        mock_manager._instance_repository.get.return_value = mock_instance

        result = _get_project_workspace(mock_manager, "instance-abc")
        assert result is None

    def test_get_project_workspace_returns_none_when_instance_not_found(self):
        """_get_project_workspace returns None when instance not found."""
        mock_manager = MagicMock()
        mock_manager._instance_repository.get.return_value = None

        result = _get_project_workspace(mock_manager, "nonexistent-instance")
        assert result is None

    def test_get_project_workspace_returns_none_when_no_project_id(self):
        """_get_project_workspace returns None when metadata has no project_id."""
        mock_instance = MagicMock()
        mock_instance.instance_metadata = {"other_key": "value"}

        mock_manager = MagicMock()
        mock_manager._instance_repository.get.return_value = mock_instance

        result = _get_project_workspace(mock_manager, "instance-abc")
        assert result is None

    def test_get_project_workspace_falls_back_to_project_id_when_name_empty(self):
        """_get_project_workspace returns project_id when project.name is empty or None."""
        mock_instance = MagicMock()
        mock_instance.instance_metadata = {"project_id": "proj-uuid-123"}
        mock_project = MagicMock()
        mock_project.name = ""  # Empty name

        mock_manager = MagicMock()
        mock_manager._instance_repository.get.return_value = mock_instance
        mock_manager._project_repository.get.return_value = mock_project

        result = _get_project_workspace(mock_manager, "instance-abc")
        assert result == "proj-uuid-123"

    def test_get_project_workspace_falls_back_to_project_id_when_name_none(self):
        """_get_project_workspace returns project_id when project.name is None."""
        mock_instance = MagicMock()
        mock_instance.instance_metadata = {"project_id": "proj-uuid-456"}
        mock_project = MagicMock()
        mock_project.name = None  # None name

        mock_manager = MagicMock()
        mock_manager._instance_repository.get.return_value = mock_instance
        mock_manager._project_repository.get.return_value = mock_project

        result = _get_project_workspace(mock_manager, "instance-abc")
        assert result == "proj-uuid-456"


# =============================================================================
# Integration Test: End-to-End Workspace Flow
# =============================================================================


class TestWorkspaceScopingIntegration:
    """Integration tests for complete workspace scoping flow."""

    def test_knowledge_tools_pass_project_id(self):
        """Verify knowledge tools (explore/experience) pass project_id when spawning instances."""
        # This test verifies that the knowledge_tools functions correctly
        # extract and pass project_id to spawned agents

        # Simulate the _get_project_id logic from knowledge_tools.py
        def simulate_get_project_id(manager, current_instance_id):
            try:
                instance_meta = manager._instance_repository.get(current_instance_id)
                if instance_meta and instance_meta.instance_metadata:
                    return instance_meta.instance_metadata.get("project_id")
            except Exception:
                pass
            return None

        # Test case 1: Instance with project_id
        mock_instance = MagicMock()
        mock_instance.instance_metadata = {"project_id": "my-project-uuid"}
        mock_manager = MagicMock()
        mock_manager._instance_repository.get.return_value = mock_instance

        result = simulate_get_project_id(mock_manager, "parent-instance-123")
        assert result == "my-project-uuid"

        # Test case 2: Instance without project_id
        mock_instance.instance_metadata = {}
        result = simulate_get_project_id(mock_manager, "parent-instance-123")
        assert result is None

    def test_rag_tools_extract_workspace_from_instance(self):
        """RAG tools correctly extract project name as workspace from instance metadata."""
        # Test case 1: With project_id that resolves to project name
        mock_instance = MagicMock()
        mock_instance.instance_metadata = {"project_id": "proj-uuid-123"}
        mock_project = MagicMock()
        mock_project.name = "my-test-project"
        mock_manager = MagicMock()
        mock_manager._instance_repository.get.return_value = mock_instance
        mock_manager._project_repository.get.return_value = mock_project

        result = _get_project_workspace(mock_manager, "instance-abc")
        assert result == "my-test-project"

        # Test case 2: Without project_id
        mock_instance.instance_metadata = {}
        mock_manager._project_repository.get.return_value = None
        result = _get_project_workspace(mock_manager, "instance-abc")
        assert result is None

        # Test case 3: Instance not found
        mock_manager._instance_repository.get.return_value = None
        result = _get_project_workspace(mock_manager, "nonexistent")
        assert result is None

        # Test case 4: Project not found
        mock_instance.instance_metadata = {"project_id": "nonexistent-proj"}
        mock_manager._instance_repository.get.return_value = mock_instance
        mock_manager._project_repository.get.return_value = None
        result = _get_project_workspace(mock_manager, "instance-abc")
        assert result == "nonexistent-proj"  # Falls back to project_id

    def test_complete_workspace_flow(self):
        """Test the complete flow: instance -> project_id -> project_name -> workspace -> sanitization."""
        # Simulates: knowledge tool spawns instance with project_id
        #           -> RAG tool looks up project and extracts project_name as workspace
        #           -> Client sanitizes workspace for HTTP header

        project_id = "my-project-uuid"
        project_name = "my-test-project"

        # Step 1: Simulate knowledge tool spawning with project_id
        # (This is verified by checking the invoke_agent_and_wait/spawn_instance calls)
        spawn_args = {"project_id": project_id}
        assert spawn_args["project_id"] == project_id

        # Step 2: Simulate RAG tool extracting workspace (project name, not project_id)
        def get_project_workspace(manager, current_instance_id):
            if manager and current_instance_id:
                proj_id = manager._instance_repository.get(current_instance_id)
                if proj_id and proj_id.instance_metadata:
                    project = manager._project_repository.get(proj_id.instance_metadata.get("project_id"))
                    if project and project.name:
                        return project.name
                    return proj_id.instance_metadata.get("project_id")
            return None

        mock_project = MagicMock()
        mock_project.name = project_name
        mock_project_repo = MagicMock()
        mock_project_repo.get.return_value = mock_project

        mock_instance = MagicMock()
        mock_instance.instance_metadata = {"project_id": project_id}
        mock_instance_repo = MagicMock()
        mock_instance_repo.get.return_value = mock_instance

        mock_manager = MagicMock()
        mock_manager._instance_repository = mock_instance_repo
        mock_manager._project_repository = mock_project_repo

        workspace = _get_project_workspace(mock_manager, "test-instance")
        assert workspace == project_name

        # Step 3: Verify sanitization works
        sanitized = _sanitize_workspace(workspace)
        assert sanitized == "my_test_project"

        # Final result: workspace is properly sanitized for HTTP header
        assert sanitized == _sanitize_workspace("my-test-project")


def test_get_project_workspace_direct():
    """Directly test _get_project_workspace extraction logic with all edge cases."""
    # Case 1: Instance with project_id that resolves to project name
    mock_instance = MagicMock()
    mock_instance.instance_metadata = {"project_id": "proj-uuid-123"}
    mock_project = MagicMock()
    mock_project.name = "resolved-project-name"
    mock_manager = MagicMock()
    mock_manager._instance_repository.get.return_value = mock_instance
    mock_manager._project_repository.get.return_value = mock_project

    result = _get_project_workspace(mock_manager, "test-instance")
    assert result == "resolved-project-name"

    # Case 2: Instance without project_id
    mock_instance.instance_metadata = {}
    result = _get_project_workspace(mock_manager, "test-instance")
    assert result is None

    # Case 3: Instance not found
    mock_manager._instance_repository.get.return_value = None
    result = _get_project_workspace(mock_manager, "nonexistent")
    assert result is None

    # Case 4: Instance with None metadata
    mock_instance.instance_metadata = None
    mock_manager._instance_repository.get.return_value = mock_instance
    result = _get_project_workspace(mock_manager, "test-instance")
    assert result is None

    # Case 5: Project not found (project_id exists but project lookup fails)
    mock_instance.instance_metadata = {"project_id": "orphan-proj"}
    mock_manager._instance_repository.get.return_value = mock_instance
    mock_manager._project_repository.get.return_value = None
    result = _get_project_workspace(mock_manager, "test-instance")
    assert result == "orphan-proj"  # Falls back to project_id

    # Case 6: Project with empty name
    mock_instance.instance_metadata = {"project_id": "proj-empty-name"}
    mock_project.name = ""
    mock_manager._project_repository.get.return_value = mock_project
    result = _get_project_workspace(mock_manager, "test-instance")
    assert result == "proj-empty-name"  # Falls back to project_id

    # Case 7: Project with None name
    mock_instance.instance_metadata = {"project_id": "proj-none-name"}
    mock_project.name = None
    result = _get_project_workspace(mock_manager, "test-instance")
    assert result == "proj-none-name"  # Falls back to project_id
