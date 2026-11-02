"""P8 — the E4 schema-bump heal re-points the persisted workspace slug.

Covers pin **P8** (lane ``plane-pm-issue-tools`` Phase 2): after the
``schema_version`` bump ``"2" → "3"`` the persisted
``config.headers['x-workspace-slug']`` equals the **resolved** slug, and an
operator-set ``config.env`` sentinel SURVIVES the heal.

The P2-H (heal conditionality) and P2-I (slug-equality trigger) legs are
**Phase-3** concerns — their implementation is the ``daemon/manager.py``
heal site — and are pinned by the Phase-3 pack
(``tests/unit/test_plane_mcp_row_heal.py``'s Phase-3 sibling
``tests/unit/test_plane_loud_skip_observability.py``). The Phase-2 leg of
this file proves the template-rebuild mechanism the heal relies on.
"""

from __future__ import annotations

from daemon.manager import _heal_config_preserving_env
from daemon.mcp.builtin_servers.plane import PlaneServerDefinition
from tests.helpers.plane_workspace_fixtures import (
    make_plane_env_fixture,
    reset_plane_workspace_cache,  # noqa: F401 — the autouse fixture
)

MCP_URL = "https://mcp.example.test/plane/http/api-key/mcp"


env = make_plane_env_fixture(
    (
        ("self_env", "ENSEMBLE_SELF_ENV", "dev"),
        ("slug", "PLANE_MCP_WORKSPACE_SLUG", "e2e"),
        ("db", "POSTGRES_DB", "ensemble_dev"),
        (None, "PLANE_MCP_URL", MCP_URL),
        (None, "PLANE_MCP_API_KEY", "dummy-plane-mcp-key"),
    ),
    return_resolution=True,
)


class TestSchemaVersionBump:
    def test_schema_version_is_three(self):
        """E4 — the bump from ``"2"`` (the quote-leak heal) to ``"3"``
        (workspace scoping)."""
        assert PlaneServerDefinition().schema_version == "3"

    def test_the_previous_version_is_not_reintroduced(self):
        assert PlaneServerDefinition().schema_version != "2"


class TestHealRepointsTheSlug:
    def test_template_config_carries_the_resolved_slug(self, env):
        env(self_env="dev", slug="e2e")
        default_config = PlaneServerDefinition().build_config({})
        assert default_config["headers"]["x-workspace-slug"] == "e2e"

    def test_heal_rebuilds_headers_from_the_template(self, env):
        """THE P8 leg: a row whose persisted header still carries the OLD
        slug is re-materialised from the current env by the heal (the
        template is rebuilt; only the ``env`` axis is preserved)."""
        env(self_env="dev", slug="e2e")
        default_config = PlaneServerDefinition().build_config({})
        stale_row_config = {
            "transport": "streamable-http",
            "url": MCP_URL,
            "headers": {
                "Authorization": "Bearer stale-key",
                "x-workspace-slug": "nea",
            },
            "env": {},
        }
        healed = _heal_config_preserving_env(default_config, stale_row_config)
        assert healed["headers"]["x-workspace-slug"] == "e2e"
        assert healed["headers"]["Authorization"] == "Bearer dummy-plane-mcp-key"

    def test_operator_env_sentinel_survives_the_heal(self, env):
        """The operator-set env axis is PRESERVED by the heal (C3) — the
        P8 contract's second half."""
        env(self_env="dev", slug="e2e")
        default_config = PlaneServerDefinition().build_config({})
        row_config = {
            "transport": "streamable-http",
            "url": MCP_URL,
            "headers": {"x-workspace-slug": "nea"},
            "env": {"CONTEXT7_API_URL": "https://ctx.example.test"},
        }
        healed = _heal_config_preserving_env(default_config, row_config)
        assert healed["env"]["CONTEXT7_API_URL"] == "https://ctx.example.test"
        assert healed["headers"]["x-workspace-slug"] == "e2e"

    def test_live_heal_keeps_the_configured_slug(self, env):
        """On live the heal re-points the header at the LIVE-resolved slug
        (byte-identical to the pre-lane value)."""
        env(self_env="live", slug="nea", db="ensemble_prod")
        default_config = PlaneServerDefinition().build_config({})
        healed = _heal_config_preserving_env(
            default_config, {"headers": {"x-workspace-slug": "stale"}}
        )
        assert healed["headers"]["x-workspace-slug"] == "nea"
