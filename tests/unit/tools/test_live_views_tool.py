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


# ===========================================================================
# Group 5 — Visibility matrix (REWORK 2026-10-07, M1)
# ===========================================================================


class TestVisibilityMatrix:
    """REWORK 2026-10-07 (M1, user refinement #1):
    ``view-views`` is in ``PRIVILEGED_TOOL_CATEGORIES`` so
    the empty-allow inherit universe must NOT auto-grant
    ``view_link``. The three commissioned users (ari, leader,
    designer) opt in via ``tools.allow: ["view-views"]`` in
    their meta.json.

    This test pins the FULL matrix:

    * empty-allow agent → ``view_link`` is NOT in the
      resolved tools (R-SR16 default-deny on a privileged
      category);
    * ari / leader / designer → ``view_link`` IS in the
      resolved tools (their meta.json carries the explicit
      ``view-views`` opt-in);
    * non-commissioned agents (worker, jober, watcher) →
      ``view_link`` is NOT in the resolved tools (no
      explicit allow entry; the privileged strip
      applies).

    Mirrors the pattern at
    ``tests/unit/tools/test_upgrade_registration.py::
    TestRealAgentResolution`` so the matrix pin sits
    alongside the other category-grant pins.
    """

    @pytest.fixture
    def tools_by_agent(self, tmp_path, monkeypatch):
        from pathlib import Path

        import daemon.registry as dr
        from daemon.registry import AgentRegistry
        from daemon.tools.instance import create_instance_tools
        from unittest.mock import MagicMock

        REPO_ROOT = Path(__file__).resolve().parents[3]
        registry = AgentRegistry(REPO_ROOT / "agents")
        registry.discover()
        monkeypatch.setattr(dr, "_registry", registry)

        def _build(agent_id: str) -> dict[str, object]:
            manager = MagicMock(name="InstanceManager")
            manager.config.daemon.port = 0
            manager.config.llm.allowed_models = []
            tools = create_instance_tools(
                manager, f"inst-{agent_id}", agent_id
            )
            return {getattr(t, "name", "?"): t for t in tools}

        return _build

    def test_empty_allow_agent_does_not_get_view_link(
        self, tools_by_agent
    ):
        # Empty allow list = inherit non-privileged universe;
        # the privileged ``view-views`` category is NOT
        # auto-granted.
        by_name = tools_by_agent("worker")
        assert "view_link" not in by_name, (
            "empty-allow (non-allow) agent must NOT see view_link "
            "— privileged-category default-deny regression"
        )

    def test_ari_resolves_view_link(self, tools_by_agent):
        # ari is a commissioned user (M1 allow-list addition).
        by_name = tools_by_agent("ari")
        assert "view_link" in by_name, (
            "ari must resolve view_link via the M1 "
            "tools.allow: [view-views] opt-in"
        )

    def test_leader_resolves_view_link(self, tools_by_agent):
        # leader is a commissioned user (M1 allow-list addition).
        by_name = tools_by_agent("leader")
        assert "view_link" in by_name, (
            "leader must resolve view_link via the M1 "
            "tools.allow: [view-views] opt-in"
        )

    def test_designer_resolves_view_link(self, tools_by_agent):
        # designer's entry pre-existed (the schema-visibility
        # allow from Phase 1); the M1 change raised the
        # category to privileged so the empty-allow universe
        # stops auto-granting it.
        by_name = tools_by_agent("designer")
        assert "view_link" in by_name, (
            "designer must still resolve view_link "
            "(schema-visibility entry from Phase 1)"
        )

    @pytest.mark.parametrize("agent_id", ["worker", "jober", "watcher"])
    def test_non_commissioned_agents_dont_get_view_link(
        self, tools_by_agent, agent_id: str
    ):
        # Non-commissioned agents (no M1 allow-list addition).
        # The privileged-category strip applies.
        by_name = tools_by_agent(agent_id)
        assert "view_link" not in by_name, (
            f"{agent_id} must NOT see view_link — no allow entry; "
            f"privileged-category default-deny"
        )
