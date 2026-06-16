"""Tests for the new context tools hosted on the KB MCP server:

- ``ensemble_context_list(context_key)``
- ``ensemble_context_read(context_key, filename)``

These mirror the LangChain tools in ``daemon.tools.context_tools`` but are
exposed to external agent systems via the FastMCP transport. We exercise
them by pulling the tool function out of the FastMCP ``_tool_manager``
and calling it directly (the existing ``test_kb_mcp_http`` integration
covers the HTTP/SSE wire path).
"""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ─── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_manager():
    manager = MagicMock()
    manager._job_queue_service = MagicMock()

    project_repo = MagicMock()

    def get_project(project_id):
        if project_id:
            mock_project = MagicMock()
            mock_project.project_id = project_id
            mock_project.name = project_id
            mock_project.shortnames = []
            mock_project.main_directory = f"/path/to/{project_id}"
            mock_project.status = "active"
            mock_project.tags = []
            return mock_project
        return None

    project_repo.get = get_project
    project_repo.get_by_name = lambda name: None
    project_repo.get_by_shortname = lambda shortname: None
    project_repo.get_by_directory = lambda directory: None
    project_repo.list_projects = lambda **kwargs: []
    project_repo.search = lambda query=None, limit=20: []
    manager._project_repository = project_repo
    return manager


@pytest.fixture
def reset_kb_server_module():
    """Reset the kb_server module state before and after each test."""
    import daemon.mcp.kb_server as kb_server_module

    original_manager = kb_server_module._manager
    original_mcp_server = kb_server_module._mcp_server
    original_http_app = kb_server_module._http_app

    kb_server_module._manager = None
    kb_server_module._mcp_server = None
    kb_server_module._http_app = None

    yield kb_server_module

    kb_server_module._manager = original_manager
    kb_server_module._mcp_server = original_mcp_server
    kb_server_module._http_app = original_http_app


@pytest.fixture
def kb_server_with_context_tools(reset_kb_server_module, mock_manager):
    """Create a fresh KB MCP server with the context tools registered."""
    import daemon.mcp.kb_server as kb_server_module
    from daemon.rag import config as rag_config_module

    kb_server_module.set_kb_mcp_manager(mock_manager)

    original_host = os.environ.get("LIGHTRAG_HOST")
    os.environ["LIGHTRAG_HOST"] = "http://localhost:8000"
    original_rag_enabled = rag_config_module._rag_enabled
    rag_config_module._rag_enabled = True

    mcp = kb_server_module.create_kb_mcp_server()
    list_tool = mcp._tool_manager.get_tool("ensemble_context_list")
    read_tool = mcp._tool_manager.get_tool("ensemble_context_read")

    yield {
        "module": kb_server_module,
        "mcp": mcp,
        "manager": mock_manager,
        "list_tool": list_tool,
        "read_tool": read_tool,
    }

    rag_config_module._rag_enabled = original_rag_enabled
    if original_host is None:
        os.environ.pop("LIGHTRAG_HOST", None)
    else:
        os.environ["LIGHTRAG_HOST"] = original_host


# ─── Tool registration ────────────────────────────────────────────────────────


class TestContextToolsRegistration:
    def test_context_list_tool_registered(self, kb_server_with_context_tools):
        assert kb_server_with_context_tools["list_tool"] is not None

    def test_context_read_tool_registered(self, kb_server_with_context_tools):
        assert kb_server_with_context_tools["read_tool"] is not None

    def test_context_tools_appear_in_tool_list(self, kb_server_with_context_tools):
        """`tools/list` should expose the new context tools."""
        mcp = kb_server_with_context_tools["mcp"]
        tool_names = list(mcp._tool_manager._tools.keys())
        assert "ensemble_context_list" in tool_names
        assert "ensemble_context_read" in tool_names

    def test_all_expected_tools_present(self, kb_server_with_context_tools):
        """Sanity: all 6 KB tools are registered (4 KB + 2 context)."""
        mcp = kb_server_with_context_tools["mcp"]
        tool_names = set(mcp._tool_manager._tools.keys())
        assert {
            "ensemble_kb_explore",
            "ensemble_kb_experience",
            "ensemble_kb_list_projects",
            "ensemble_kb_search_projects",
            "ensemble_context_list",
            "ensemble_context_read",
        } <= tool_names


# ─── ensemble_context_list ────────────────────────────────────────────────────


class TestEnsembleContextList:
    @pytest.mark.asyncio
    async def test_returns_json_array(self, kb_server_with_context_tools, tmp_path):
        context_dir = tmp_path / "ensemble" / "context" / "ctx-list"
        context_dir.mkdir(parents=True)
        (context_dir / "alpha_20260601_000000.md").write_text("alpha content")
        (context_dir / "beta_20260602_000000.md").write_text("beta content")

        list_tool = kb_server_with_context_tools["list_tool"]
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await list_tool.fn(context_key="ctx-list")

        decoded = json.loads(result)
        assert isinstance(decoded, list)
        assert len(decoded) == 2
        assert {d["filename"] for d in decoded} == {
            "alpha_20260601_000000.md",
            "beta_20260602_000000.md",
        }

    @pytest.mark.asyncio
    async def test_empty_dir_returns_empty_array(self, kb_server_with_context_tools, tmp_path):
        list_tool = kb_server_with_context_tools["list_tool"]
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await list_tool.fn(context_key="nonexistent")
        assert json.loads(result) == []

    @pytest.mark.asyncio
    async def test_empty_context_key_returns_error(self, kb_server_with_context_tools):
        list_tool = kb_server_with_context_tools["list_tool"]
        result = await list_tool.fn(context_key="")
        assert result.startswith("Error:")
        assert "context_key" in result

    @pytest.mark.asyncio
    async def test_uses_asyncio_to_thread(self, kb_server_with_context_tools):
        import asyncio

        list_tool = kb_server_with_context_tools["list_tool"]

        with patch(
            "daemon.mcp.kb_server.asyncio.to_thread",
            wraps=asyncio.to_thread,
        ) as mock_to_thread:
            await list_tool.fn(context_key="any")

        # The tool must offload the sync filesystem helper to a thread.
        assert mock_to_thread.called
        first_call = mock_to_thread.call_args
        assert first_call.args[0].__name__ == "list_context_files"
        assert first_call.args[1] == "any"

    @pytest.mark.asyncio
    async def test_query_param_passes_through_to_service(self, kb_server_with_context_tools):
        """C2: `query` flows from MCP tool signature to the service layer."""
        import asyncio

        list_tool = kb_server_with_context_tools["list_tool"]

        with patch(
            "daemon.mcp.kb_server.asyncio.to_thread",
            wraps=asyncio.to_thread,
        ) as mock_to_thread:
            await list_tool.fn(context_key="any", query="oauth")

        first_call = mock_to_thread.call_args
        assert first_call.args[0].__name__ == "list_context_files"
        assert first_call.args[1] == "any"
        assert first_call.args[2] == "oauth"

    @pytest.mark.asyncio
    async def test_default_query_is_empty_string(self, kb_server_with_context_tools):
        """C2: omitting `query` should default to "" (backward compatible)."""
        import asyncio

        list_tool = kb_server_with_context_tools["list_tool"]

        with patch(
            "daemon.mcp.kb_server.asyncio.to_thread",
            wraps=asyncio.to_thread,
        ) as mock_to_thread:
            await list_tool.fn(context_key="any")

        first_call = mock_to_thread.call_args
        assert first_call.args[2] == ""

    @pytest.mark.asyncio
    async def test_query_actually_filters_files(self, kb_server_with_context_tools, tmp_path):
        """C2 end-to-end: a non-empty `query` filters the returned files."""
        context_dir = tmp_path / "ensemble" / "context" / "ctx-mcp-q"
        context_dir.mkdir(parents=True)
        (context_dir / "auth_20260601_000000.md").write_text("# Auth\nlogin flow")
        (context_dir / "billing_20260602_000000.md").write_text("# Billing\ninvoices")

        list_tool = kb_server_with_context_tools["list_tool"]
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await list_tool.fn(context_key="ctx-mcp-q", query="billing")

        decoded = json.loads(result)
        assert [d["filename"] for d in decoded] == ["billing_20260602_000000.md"]


# ─── ensemble_context_read ────────────────────────────────────────────────────


class TestEnsembleContextRead:
    @pytest.mark.asyncio
    async def test_returns_file_contents(self, kb_server_with_context_tools, tmp_path):
        context_dir = tmp_path / "ensemble" / "context" / "ctx-read"
        context_dir.mkdir(parents=True)
        (context_dir / "doc.md").write_text("the file body")

        read_tool = kb_server_with_context_tools["read_tool"]
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await read_tool.fn(context_key="ctx-read", filename="doc.md")

        assert result == "the file body"

    @pytest.mark.asyncio
    async def test_missing_file_returns_error(self, kb_server_with_context_tools, tmp_path):
        context_dir = tmp_path / "ensemble" / "context" / "ctx-missing"
        context_dir.mkdir(parents=True)

        read_tool = kb_server_with_context_tools["read_tool"]
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await read_tool.fn(context_key="ctx-missing", filename="nope.md")

        assert result.startswith("Error:")
        assert "nope.md" in result

    @pytest.mark.asyncio
    async def test_path_traversal_returns_error(self, kb_server_with_context_tools, tmp_path):
        context_dir = tmp_path / "ensemble" / "context" / "ctx-trav"
        context_dir.mkdir(parents=True)

        read_tool = kb_server_with_context_tools["read_tool"]
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await read_tool.fn(context_key="ctx-trav", filename="../etc/passwd")

        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_empty_args_return_error(self, kb_server_with_context_tools):
        read_tool = kb_server_with_context_tools["read_tool"]
        result = await read_tool.fn(context_key="", filename="x.md")
        assert result.startswith("Error:")
        result = await read_tool.fn(context_key="any", filename="")
        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_uses_asyncio_to_thread(self, kb_server_with_context_tools):
        import asyncio

        read_tool = kb_server_with_context_tools["read_tool"]

        with patch(
            "daemon.mcp.kb_server.asyncio.to_thread",
            wraps=asyncio.to_thread,
        ) as mock_to_thread:
            await read_tool.fn(context_key="any", filename="doc.md")

        assert mock_to_thread.called
        first_call = mock_to_thread.call_args
        assert first_call.args[0].__name__ == "read_context_file"
        assert first_call.args[1] == "any"
        assert first_call.args[2] == "doc.md"
