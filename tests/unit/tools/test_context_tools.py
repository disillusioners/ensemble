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


def _find_tool(tools, name):
    for t in tools:
        if t.name == name:
            return t
    raise AssertionError(f"Tool {name!r} not found in {[t.name for t in tools]}")


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
    async def test_returns_json_array(self, tools, tmp_path):
        context_dir = tmp_path / "ensemble" / "context" / "ctx-list"
        context_dir.mkdir(parents=True)
        (context_dir / "first_20260601_000000.md").write_text("alpha")
        (context_dir / "second_20260602_000000.md").write_text("beta")

        list_tool = _find_tool(tools, "list_context")
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
    async def test_returns_empty_json_array_for_missing_dir(self, tools, tmp_path):
        list_tool = _find_tool(tools, "list_context")
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await list_tool.ainvoke({"context_key": "no-such-key"})

        # Must be valid JSON "[]" — never an error string.
        assert json.loads(result) == []

    @pytest.mark.asyncio
    async def test_uses_asyncio_to_thread(self, tools, tmp_path):
        list_tool = _find_tool(tools, "list_context")

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


# ─── read_context ──────────────────────────────────────────────────────────────


class TestReadContextTool:
    @pytest.mark.asyncio
    async def test_returns_file_contents(self, tools, tmp_path):
        context_dir = tmp_path / "ensemble" / "context" / "ctx-read"
        context_dir.mkdir(parents=True)
        (context_dir / "doc.md").write_text("hello world")

        read_tool = _find_tool(tools, "read_context")
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await read_tool.ainvoke({
                "context_key": "ctx-read",
                "filename": "doc.md",
            })

        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_missing_file_returns_error(self, tools, tmp_path):
        context_dir = tmp_path / "ensemble" / "context" / "ctx-missing"
        context_dir.mkdir(parents=True)

        read_tool = _find_tool(tools, "read_context")
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await read_tool.ainvoke({
                "context_key": "ctx-missing",
                "filename": "nope.md",
            })

        assert result.startswith("Error:")
        assert "nope.md" in result

    @pytest.mark.asyncio
    async def test_path_traversal_returns_error(self, tools, tmp_path):
        context_dir = tmp_path / "ensemble" / "context" / "ctx-trav"
        context_dir.mkdir(parents=True)
        (context_dir / "real.md").write_text("real")

        read_tool = _find_tool(tools, "read_context")
        with patch("daemon.services.context_tools.tempfile.gettempdir", return_value=str(tmp_path)):
            result = await read_tool.ainvoke({
                "context_key": "ctx-trav",
                "filename": "../etc/passwd",
            })

        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_uses_asyncio_to_thread(self, tools, tmp_path):
        read_tool = _find_tool(tools, "read_context")

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
