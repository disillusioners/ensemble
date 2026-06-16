"""Tests for the LangChain ``Context`` tool category
(``daemon/tools/context_tools.py``).

These tools mirror the hosted MCP ``ensemble_context_list`` /
``ensemble_context_read`` tools but are bound to internal agents via the
LangChain factory. The factory delegates to the sync helpers in
``daemon.services.context_tools`` via :func:`asyncio.to_thread`.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from daemon.tools.context_tools import create_context_tools


# ─── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tools():
    manager = MagicMock()
    manager._instance_repository = MagicMock()
    return create_context_tools(manager, "current-instance-id")


@pytest.fixture
def tool_by_name(tools):
    """Name-based tool lookup — preferred over positional indices for stability."""
    return {t.name: t for t in tools}


# ─── Factory shape ────────────────────────────────────────────────────────────


class TestContextToolsFactory:
    def test_factory_returns_two_tools(self):
        tools = create_context_tools(MagicMock(), "instance-1")
        names = sorted(t.name for t in tools)
        assert names == ["list_context", "read_context"]

    def test_tools_have_correct_category(self, tools):
        for t in tools:
            assert getattr(t, "_tool_category", None) == "context"


# ─── list_context ──────────────────────────────────────────────────────────────


class TestListContextTool:
    @pytest.mark.asyncio
    async def test_returns_json_array(self, tool_by_name, tmp_path):
        context_dir = tmp_path / "ensemble" / "context" / "ctx-list"
        context_dir.mkdir(parents=True)
        (context_dir / "first_20260601_000000.md").write_text("alpha")
        (context_dir / "second_20260602_000000.md").write_text("beta")

        list_tool = tool_by_name["list_context"]
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await list_tool.ainvoke({"context_key": "ctx-list"})

        decoded = json.loads(result)
        assert isinstance(decoded, list)
        assert len(decoded) == 2
        assert {d["filename"] for d in decoded} == {
            "first_20260601_000000.md",
            "second_20260602_000000.md",
        }
        # slug is derived from the filename
        assert {d["slug"] for d in decoded} == {"first", "second"}

    @pytest.mark.asyncio
    async def test_returns_empty_json_array_for_missing_dir(self, tool_by_name, tmp_path):
        list_tool = tool_by_name["list_context"]
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await list_tool.ainvoke({"context_key": "no-such-key"})

        # Must be valid JSON "[]" — never an error string.
        assert json.loads(result) == []

    @pytest.mark.asyncio
    async def test_uses_asyncio_to_thread(self, tool_by_name, tmp_path):
        list_tool = tool_by_name["list_context"]

        with patch(
            "daemon.tools.context_tools.asyncio.to_thread",
            wraps=asyncio.to_thread,
        ) as mock_to_thread:
            with patch(
                "daemon.services.context_tools.tempfile.gettempdir",
                return_value=str(tmp_path),
            ):
                await list_tool.ainvoke({"context_key": "any"})

        # asyncio.to_thread must be called with the underlying sync helper.
        assert mock_to_thread.called
        first_call = mock_to_thread.call_args
        assert first_call.args[0].__name__ == "list_context_files"
        assert first_call.args[1] == "any"
        # Default query is "" — kept positional for backward compatibility.
        assert first_call.args[2] == ""

    @pytest.mark.asyncio
    async def test_passes_query_through_to_service(self, tool_by_name, tmp_path):
        """The `query` argument flows from the tool into the service layer."""
        list_tool = tool_by_name["list_context"]

        with patch(
            "daemon.tools.context_tools.asyncio.to_thread",
            wraps=asyncio.to_thread,
        ) as mock_to_thread:
            with patch(
                "daemon.services.context_tools.tempfile.gettempdir",
                return_value=str(tmp_path),
            ):
                await list_tool.ainvoke({
                    "context_key": "any",
                    "query": "auth",
                })

        first_call = mock_to_thread.call_args
        assert first_call.args[0].__name__ == "list_context_files"
        assert first_call.args[1] == "any"
        assert first_call.args[2] == "auth"

    @pytest.mark.asyncio
    async def test_rich_preview_includes_multiple_lines(self, tool_by_name, tmp_path):
        """Tool returns a multi-line concise_preview, not just one line."""
        context_dir = tmp_path / "ensemble" / "context" / "ctx-rich"
        context_dir.mkdir(parents=True)
        (context_dir / "doc_20260601_000000.md").write_text(
            "# Auth Flow\n\n"
            "How users authenticate via OAuth.\n"
            "Tokens last 24h and refresh on use.\n"
        )

        list_tool = tool_by_name["list_context"]
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await list_tool.ainvoke({"context_key": "ctx-rich"})

        decoded = json.loads(result)
        assert len(decoded) == 1
        preview = decoded[0]["concise_preview"]
        # Title heading AND real content lines are both present.
        assert "# Auth Flow" in preview
        assert "How users authenticate via OAuth." in preview
        assert "Tokens last 24h and refresh on use." in preview
        # Blank lines are skipped, so we see at most 5 non-empty lines.
        assert preview.count("\n") >= 1
        assert preview.count("\n") <= 4
        # Newlines in the preview make it readable in raw form.
        assert "\n" in preview

    @pytest.mark.asyncio
    async def test_rich_preview_truncated_to_300_chars(self, tool_by_name, tmp_path):
        """Rich preview is capped at ~300 chars (with "..." when truncated)."""
        context_dir = tmp_path / "ensemble" / "context" / "ctx-cap"
        context_dir.mkdir(parents=True)
        (context_dir / "long_20260601_000000.md").write_text(
            "a" * 100 + "\n" + "b" * 100 + "\n" + "c" * 100 + "\n" + "d" * 100 + "\n"
        )

        list_tool = tool_by_name["list_context"]
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await list_tool.ainvoke({"context_key": "ctx-cap"})

        preview = json.loads(result)[0]["concise_preview"]
        assert len(preview) == 300
        assert preview.endswith("...")

    @pytest.mark.asyncio
    async def test_query_filters_results_at_tool_layer(self, tool_by_name, tmp_path):
        """Passing `query` filters the files returned by the tool."""
        context_dir = tmp_path / "ensemble" / "context" / "ctx-q"
        context_dir.mkdir(parents=True)
        (context_dir / "auth_20260601_000000.md").write_text("# Auth\nlogin flow")
        (context_dir / "billing_20260602_000000.md").write_text("# Billing\ninvoices")
        (context_dir / "deployment_20260603_000000.md").write_text("# Deploy\nrunbook")

        list_tool = tool_by_name["list_context"]
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await list_tool.ainvoke({
                "context_key": "ctx-q",
                "query": "billing",
            })

        decoded = json.loads(result)
        assert [d["filename"] for d in decoded] == ["billing_20260602_000000.md"]

    @pytest.mark.asyncio
    async def test_query_case_insensitive(self, tool_by_name, tmp_path):
        context_dir = tmp_path / "ensemble" / "context" / "ctx-qci"
        context_dir.mkdir(parents=True)
        (context_dir / "auth-flow_20260601_000000.md").write_text("# Auth\nlogin")

        list_tool = tool_by_name["list_context"]
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await list_tool.ainvoke({
                "context_key": "ctx-qci",
                "query": "AUTH-FLOW",
            })

        decoded = json.loads(result)
        assert [d["filename"] for d in decoded] == ["auth-flow_20260601_000000.md"]

    @pytest.mark.asyncio
    async def test_query_no_match_returns_empty(self, tool_by_name, tmp_path):
        context_dir = tmp_path / "ensemble" / "context" / "ctx-qnm"
        context_dir.mkdir(parents=True)
        (context_dir / "doc_20260601_000000.md").write_text("hello world")

        list_tool = tool_by_name["list_context"]
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await list_tool.ainvoke({
                "context_key": "ctx-qnm",
                "query": "absolutely-not-here",
            })

        assert json.loads(result) == []

    @pytest.mark.asyncio
    async def test_no_query_arg_returns_all_files(self, tool_by_name, tmp_path):
        """Backward compatibility: omitting `query` returns all files."""
        context_dir = tmp_path / "ensemble" / "context" / "ctx-all"
        context_dir.mkdir(parents=True)
        (context_dir / "a_20260601_000000.md").write_text("alpha")
        (context_dir / "b_20260602_000000.md").write_text("beta")

        list_tool = tool_by_name["list_context"]
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await list_tool.ainvoke({"context_key": "ctx-all"})

        decoded = json.loads(result)
        assert len(decoded) == 2

    @pytest.mark.asyncio
    async def test_tool_docstring_documents_query(self, tool_by_name):
        """The tool's docstring should mention the new `query` param."""
        list_tool = tool_by_name["list_context"]
        assert "query" in list_tool.description
        assert "multi-line" in list_tool.description or "preview" in list_tool.description.lower()

    @pytest.mark.asyncio
    async def test_empty_context_key_returns_error_string(self, tool_by_name):
        """W2: empty / whitespace-only context_key short-circuits to a plain error string.

        Matches the `read_context` error convention ("Error: ...") and the MCP tool.
        Avoids the wasted filesystem call to resolve a context dir under ''.
        """
        list_tool = tool_by_name["list_context"]
        # Empty string
        result = await list_tool.ainvoke({"context_key": ""})
        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert "context_key" in result

        # Whitespace-only
        result = await list_tool.ainvoke({"context_key": "   "})
        assert isinstance(result, str)
        assert result.startswith("Error:")


# ─── read_context ──────────────────────────────────────────────────────────────


class TestReadContextTool:
    @pytest.mark.asyncio
    async def test_returns_file_contents(self, tool_by_name, tmp_path):
        context_dir = tmp_path / "ensemble" / "context" / "ctx-read"
        context_dir.mkdir(parents=True)
        (context_dir / "doc.md").write_text("hello world")

        read_tool = tool_by_name["read_context"]
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await read_tool.ainvoke({
                "context_key": "ctx-read",
                "filename": "doc.md",
            })

        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_missing_file_returns_error(self, tool_by_name, tmp_path):
        context_dir = tmp_path / "ensemble" / "context" / "ctx-missing"
        context_dir.mkdir(parents=True)

        read_tool = tool_by_name["read_context"]
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await read_tool.ainvoke({
                "context_key": "ctx-missing",
                "filename": "nope.md",
            })

        assert result.startswith("Error:")
        assert "nope.md" in result

    @pytest.mark.asyncio
    async def test_path_traversal_returns_error(self, tool_by_name, tmp_path):
        context_dir = tmp_path / "ensemble" / "context" / "ctx-trav"
        context_dir.mkdir(parents=True)
        (context_dir / "real.md").write_text("real")

        read_tool = tool_by_name["read_context"]
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await read_tool.ainvoke({
                "context_key": "ctx-trav",
                "filename": "../etc/passwd",
            })

        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_uses_asyncio_to_thread(self, tool_by_name, tmp_path):
        read_tool = tool_by_name["read_context"]

        with patch(
            "daemon.tools.context_tools.asyncio.to_thread",
            wraps=asyncio.to_thread,
        ) as mock_to_thread:
            with patch(
                "daemon.services.context_tools.tempfile.gettempdir",
                return_value=str(tmp_path),
            ):
                await read_tool.ainvoke({
                    "context_key": "any",
                    "filename": "doc.md",
                })

        assert mock_to_thread.called
        first_call = mock_to_thread.call_args
        assert first_call.args[0].__name__ == "read_context_file"
        assert first_call.args[1] == "any"
        assert first_call.args[2] == "doc.md"


# ─── Integration with create_instance_tools ────────────────────────────────────


class TestContextToolsWiredIntoInstance:
    def test_context_tools_appear_in_instance_tool_list(self):
        """`create_context_tools` is registered as a `context` category, so its
        tools should be discoverable by category and present in the final
        instance tool list."""
        from daemon.tools._tool_registry import (
            list_tools_by_category,
            _tool_metadata,
            scan_tools_for_full_docs,
        )
        from daemon.tools.context_tools import create_context_tools

        # The decorator only sets `_tool_category` on the function; the global
        # registry is populated by `scan_tools_for_full_docs` (called from
        # `create_instance_tools` after all tools are built).
        tools = create_context_tools(MagicMock(), "instance")
        scan_tools_for_full_docs(tools)

        categories = list_tools_by_category()
        assert "context" in categories
        assert "list_context" in categories["context"]
        assert "read_context" in categories["context"]

        for name in ("list_context", "read_context"):
            assert name in _tool_metadata
            assert _tool_metadata[name]["category"] == "context"
