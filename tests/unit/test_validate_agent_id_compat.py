"""Tests for validate_agent_id backward compatibility.

This verifies that validate_agent_id can be imported from both
the old location (daemon.api) and new location (daemon.utils),
and that both imports resolve to the same function.
"""

from unittest.mock import MagicMock
import pytest
from fastapi import HTTPException


class TestValidateAgentIdCompat:
    """Tests for validate_agent_id backward compatibility."""

    def test_import_from_daemon_api(self):
        """Should be importable from daemon.api (old path)."""
        from daemon.api import validate_agent_id
        assert callable(validate_agent_id)

    def test_import_from_daemon_utils(self):
        """Should be importable from daemon.utils (new path)."""
        from daemon.utils import validate_agent_id
        assert callable(validate_agent_id)

    def test_same_function_object(self):
        """Both imports should return the same function object."""
        from daemon.api import validate_agent_id as api_import
        from daemon.utils import validate_agent_id as utils_import
        # Both imports should be the exact same function
        assert api_import is utils_import

    def test_raises_404_for_nonexistent_agent(self):
        """Should raise HTTPException(404) for non-existent agent."""
        from daemon.utils import validate_agent_id
        
        # Mock the registry to return None (agent not found)
        with pytest.raises(HTTPException) as exc_info:
            validate_agent_id("nonexistent-agent")
        
        assert exc_info.value.status_code == 404
        # Detail should contain the agent_id in the error message
        detail = exc_info.value.detail
        if isinstance(detail, dict):
            assert "nonexistent-agent" in str(detail["message"])
        else:
            assert "nonexistent-agent" in str(detail)

    def test_returns_tuple_for_valid_agent(self):
        """Should return (agent_id, path) tuple for valid agent."""
        from unittest.mock import patch
        from pathlib import Path
        from daemon.utils import validate_agent_id

        mock_path = Path("/path/to/agent")
        mock_metadata = MagicMock()
        mock_metadata.id = "valid-agent"
        mock_metadata.path = mock_path

        with patch("daemon.utils.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_resolved.return_value = mock_metadata
            mock_get_registry.return_value = mock_registry

            result = validate_agent_id("valid-agent")

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert result[0] == "valid-agent"
        assert result[1] == mock_path
