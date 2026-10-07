"""Unit tests for ``daemon.tools.live_views`` (Phase 1).

The ``view_link`` tool is the agent-facing URL minter. The
tests verify the URL shape, the typed error envelope, and the
service-not-wired failure path. The service is mocked via a
real ``LiveViewsService`` with a small test config (the
mock-and-magic-mock approach is more brittle than just
running the real service — the tool is a thin wrapper).
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock

import pytest

from daemon.config import LiveViewsConfig, LiveViewsRootConfig
from daemon.services.live_views import LiveViewsService
from daemon.tools.live_views import create_live_view_tools


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service(tmp_path: pathlib.Path) -> LiveViewsService:
    """A ``LiveViewsService`` with one filesystem root."""
    cfg = LiveViewsConfig()
    cfg.roots["designer-artifact"] = LiveViewsRootConfig(
        type="filesystem",
        path=str(tmp_path),
        description="designer artifacts",
    )
    cfg.roots["planning"] = LiveViewsRootConfig(
        type="project_scoped", path=".agents/shared/planning"
    )
    return LiveViewsService(config=cfg)


@pytest.fixture
def service_with_base(tmp_path: pathlib.Path) -> LiveViewsService:
    """A ``LiveViewsService`` with ``external_base_url`` set."""
    cfg = LiveViewsConfig(external_base_url="https://ensemble.example.com")
    cfg.roots["docs"] = LiveViewsRootConfig(
        type="filesystem", path=str(tmp_path)
    )
    return LiveViewsService(config=cfg)


@pytest.fixture
def view_link(service: LiveViewsService):
    """The ``view_link`` tool with a real service wired."""
    mgr = MagicMock()
    mgr.live_views_service = service
    tools = create_live_view_tools(mgr, "instance-id")
    return tools[0]


@pytest.fixture
def view_link_with_base(service_with_base: LiveViewsService):
    mgr = MagicMock()
    mgr.live_views_service = service_with_base
    tools = create_live_view_tools(mgr, "instance-id")
    return tools[0]


# ===========================================================================
# Group 1 — URL shape (path-relative + fully-qualified)
# ===========================================================================


class TestUrlShape:
    def test_path_relative_default(self, view_link):
        # Path-relative when external_base_url is unset.
        result = view_link.invoke(
            {"root_name": "designer-artifact", "path": "foo/bar.html"}
        )
        assert result == "/views/designer-artifact/foo/bar.html"

    def test_fully_qualified_when_base_set(self, view_link_with_base):
        result = view_link_with_base.invoke(
            {"root_name": "docs", "path": "foo/bar.html"}
        )
        assert result == "https://ensemble.example.com/views/docs/foo/bar.html"


# ===========================================================================
# Group 2 — Typed error envelope
# ===========================================================================


class TestErrorEnvelope:
    """Every failure mode returns ``"Error: ..."`` — never a
    partial URL, never a crash, never a stack trace.
    """

    def test_unknown_root(self, view_link):
        result = view_link.invoke(
            {"root_name": "unknown-root", "path": "foo"}
        )
        assert result.startswith("Error: ")
        assert "unknown or disabled root" in result
        assert "configured:" in result  # list of valid roots

    def test_disabled_root(self):
        cfg = LiveViewsConfig()
        cfg.roots["off"] = LiveViewsRootConfig(
            type="filesystem", path="/tmp", enabled=False
        )
        service = LiveViewsService(config=cfg)
        mgr = MagicMock()
        mgr.live_views_service = service
        view_link = create_live_view_tools(mgr, "instance-id")[0]
        result = view_link.invoke({"root_name": "off", "path": "foo"})
        assert result.startswith("Error: ")
        assert "unknown or disabled root" in result

    def test_subsystem_disabled(self):
        cfg = LiveViewsConfig(enabled=False)
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path="/tmp")
        service = LiveViewsService(config=cfg)
        mgr = MagicMock()
        mgr.live_views_service = service
        view_link = create_live_view_tools(mgr, "instance-id")[0]
        result = view_link.invoke({"root_name": "docs", "path": "foo"})
        assert result == "Error: live-views subsystem is disabled"

    def test_service_not_wired(self):
        mgr = MagicMock()
        mgr.live_views_service = None
        view_link = create_live_view_tools(mgr, "instance-id")[0]
        result = view_link.invoke(
            {"root_name": "docs", "path": "foo"}
        )
        assert result.startswith("Error: ")
        assert "live-views service not initialized" in result

    def test_malformed_root_name(self, view_link):
        result = view_link.invoke(
            {"root_name": "Has_Underscore", "path": "foo"}
        )
        assert result.startswith("Error: ")
        assert "malformed root_name" in result

    def test_empty_root_name(self, view_link):
        result = view_link.invoke({"root_name": "", "path": "foo"})
        assert result.startswith("Error: ")
        assert "non-empty string" in result

    def test_empty_path(self, view_link):
        result = view_link.invoke(
            {"root_name": "designer-artifact", "path": ""}
        )
        assert result.startswith("Error: ")
        assert "non-empty string" in result

    def test_traversal_in_path(self, view_link):
        result = view_link.invoke(
            {"root_name": "designer-artifact", "path": "../etc/passwd"}
        )
        assert result.startswith("Error: ")
        assert "malformed path" in result

    def test_leading_slash_in_path(self, view_link):
        result = view_link.invoke(
            {"root_name": "designer-artifact", "path": "/etc/passwd"}
        )
        assert result.startswith("Error: ")
        assert "malformed path" in result


# ===========================================================================
# Group 3 — Tool name
# ===========================================================================


class TestToolName:
    def test_tool_name_is_view_link(self, view_link):
        # The tool name MUST be ``view_link`` — that's the
        # public surface an agent (and the KNOWN_TOOL_NAMES
        # registry) refers to.
        assert view_link.name == "view_link"

    def test_tool_has_description(self, view_link):
        # The description is what the LLM sees when deciding
        # whether to call the tool. It must mention both
        # URL shape (path-relative / fully-qualified) and the
        # three seed roots.
        desc = view_link.description
        assert "designer-artifact" in desc
        assert "planning" in desc
        assert "tmp-images" in desc or "tmp" in desc


# ===========================================================================
# Group 4 — Returns a string (not bytes / not a dict)
# ===========================================================================


class TestReturnType:
    def test_returns_string(self, view_link):
        # LangChain tools are typed to return a string. The
        # factory wraps the URL in a string envelope.
        result = view_link.invoke(
            {"root_name": "designer-artifact", "path": "foo.html"}
        )
        assert isinstance(result, str)
        assert result.startswith("/views/")

    def test_error_envelope_is_string(self, view_link):
        result = view_link.invoke({"root_name": "nope", "path": "x"})
        assert isinstance(result, str)
        assert result.startswith("Error: ")
