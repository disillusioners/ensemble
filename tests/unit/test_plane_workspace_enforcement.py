"""P3 — the E2/E3 enforcement chokepoints (lane ``plane-pm-issue-tools`` Phase 2).

Covers the chokepoint pin **P3** (E2 REST + E3 MCP refusal/allow legs),
**P2-C** (one canonical slug normalization consumed by BOTH lanes),
**P2-D** (the explicit-constructor bypass is closed) and the no-regression
obligations **NR1** (live behaviour is byte-identical) and **NR9** (the
REST lane stays inert when no REST key is present).

Every case controls the process environment explicitly — the resolver reads
``ENSEMBLE_SELF_ENV`` / ``PLANE_MCP_WORKSPACE_SLUG`` / ``POSTGRES_DB``, and
an ambient value would silently re-classify the environment under test.
"""

from __future__ import annotations

import pytest

from daemon.clients.plane_http_client import (
    PlaneAPIError,
    PlaneHttpClient,
    _rest_base_url,
)
from daemon.mcp.builtin_servers.plane import PlaneServerDefinition
from daemon.plane_workspace import (
    HISTORICAL_STRICT_LIVE_REASON,
    current_plane_workspace,
    reset_resolution_cache,
)

MCP_URL = "https://mcp.example.test/plane/http/api-key/mcp"
REST_URL = "https://plane.example.test"


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_resolution_cache()
    yield
    reset_resolution_cache()


@pytest.fixture
def env(monkeypatch):
    """A fully-controlled Plane environment (no ambient leakage)."""

    def _set(
        *,
        self_env: str | None = "dev",
        slug: str | None = "e2e",
        db: str = "ensemble_dev",
        mcp_url: str | None = MCP_URL,
        mcp_key: str | None = "dummy-plane-mcp-key",
        base_url: str | None = REST_URL,
        api_key: str | None = "dummy-plane-rest-key",
    ):
        for name, value in (
            ("ENSEMBLE_SELF_ENV", self_env),
            ("PLANE_MCP_WORKSPACE_SLUG", slug),
            ("POSTGRES_DB", db),
            ("PLANE_MCP_URL", mcp_url),
            ("PLANE_MCP_API_KEY", mcp_key),
            ("PLANE_BASE_URL", base_url),
            ("PLANE_API_KEY", api_key),
        ):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
        reset_resolution_cache()
        return current_plane_workspace()

    return _set


# ── P3: the MCP lane (E3) ────────────────────────────────────────────────────


class TestMcpChokepoint:
    """E3 — ``is_available`` gains the resolution gate; a refusal ⇒ no row ⇒
    no tools."""

    def test_non_live_with_nea_is_refused(self, env):
        env(self_env="dev", slug="nea")
        assert PlaneServerDefinition.is_available() is False

    def test_non_live_with_unset_slug_is_refused(self, env):
        env(self_env="dev", slug=None)
        assert PlaneServerDefinition.is_available() is False

    def test_non_live_with_e2e_is_allowed(self, env):
        env(self_env="dev", slug="e2e")
        assert PlaneServerDefinition.is_available() is True

    def test_non_live_with_third_slug_is_refused(self, env):
        env(self_env="dev", slug="other")
        assert PlaneServerDefinition.is_available() is False

    def test_live_with_nea_is_allowed(self, env):
        env(self_env="live", slug="nea", db="ensemble_prod")
        assert PlaneServerDefinition.is_available() is True

    def test_live_with_unset_slug_is_allowed_account_default(self, env):
        """P3 (G2 fold NB8): the MCP lane with an unset slug on live is
        ALLOWED and emits the account-default header — no refusal, no
        typed reason."""
        env(self_env="live", slug=None, db="ensemble_prod")
        assert PlaneServerDefinition.is_available() is True
        config = PlaneServerDefinition().get_base_config()
        assert config["headers"]["x-workspace-slug"] == ""

    def test_live_with_e2e_is_allowed_no_strict_string(self, env):
        env(self_env="live", slug="e2e", db="ensemble_prod")
        assert PlaneServerDefinition.is_available() is True
        config = PlaneServerDefinition().get_base_config()
        assert config["headers"]["x-workspace-slug"] == "e2e"
        assert HISTORICAL_STRICT_LIVE_REASON not in str(config)

    def test_live_with_third_slug_is_allowed(self, env):
        env(self_env="live", slug="other", db="ensemble_prod")
        assert PlaneServerDefinition.is_available() is True

    def test_missing_url_still_refuses(self, env):
        """The pre-existing presence gate survives the workspace gate."""
        env(self_env="dev", slug="e2e", mcp_url=None)
        assert PlaneServerDefinition.is_available() is False

    def test_missing_api_key_still_refuses(self, env):
        env(self_env="dev", slug="e2e", mcp_key=None)
        assert PlaneServerDefinition.is_available() is False

    def test_kill_switch_off_still_refuses(self, env, monkeypatch):
        env(self_env="dev", slug="e2e")
        monkeypatch.setenv("PLANE_MCP_ENABLED", "false")
        assert PlaneServerDefinition.is_available() is False

    def test_base_config_emits_the_resolved_slug(self, env):
        """E3 — the emitted header carries the RESOLVED slug, never the raw
        env value."""
        env(self_env="dev", slug="e2e")
        config = PlaneServerDefinition().get_base_config()
        assert config["headers"]["x-workspace-slug"] == "e2e"

    def test_base_config_accepts_a_threaded_resolution(self, env):
        """P2-M — the resolution can be threaded in (resolve-once)."""
        env(self_env="dev", slug="e2e")
        resolution = current_plane_workspace()
        config = PlaneServerDefinition().get_base_config(resolution=resolution)
        assert config["headers"]["x-workspace-slug"] == "e2e"


# ── P3: the REST lane (E2) ───────────────────────────────────────────────────


class TestRestChokepoint:
    """E2 — ``_rest_base_url`` / ``is_available`` / client construction."""

    def test_non_live_with_nea_is_refused(self, env):
        env(self_env="dev", slug="nea")
        assert _rest_base_url() is None
        assert PlaneHttpClient.is_available() is False
        assert PlaneHttpClient.create() is None

    def test_non_live_with_unset_slug_is_refused(self, env):
        env(self_env="dev", slug=None)
        assert _rest_base_url() is None
        assert PlaneHttpClient.is_available() is False

    def test_non_live_with_e2e_composes_e2e(self, env):
        env(self_env="dev", slug="e2e")
        assert _rest_base_url() == (
            f"{REST_URL}/api/v1/workspaces/e2e/projects/"
        )
        assert PlaneHttpClient.is_available() is True

    def test_live_with_nea_is_allowed(self, env):
        env(self_env="live", slug="nea", db="ensemble_prod")
        assert _rest_base_url() == (
            f"{REST_URL}/api/v1/workspaces/nea/projects/"
        )

    def test_live_with_unset_slug_leaves_the_rest_lane_absent(self, env):
        """P3 (G2 fold NB8): the REST lane's unset-slug behavior differs
        from the MCP lane's — it is ABSENT (no URL can be composed)."""
        env(self_env="live", slug=None, db="ensemble_prod")
        assert _rest_base_url() is None
        assert PlaneHttpClient.is_available() is False

    def test_rest_key_absent_keeps_the_lane_inert(self, env):
        """NR9 — the REST lane needs its OWN key; the MCP key is never a
        substitute."""
        env(self_env="dev", slug="e2e", api_key=None)
        assert PlaneHttpClient.is_available() is False
        assert _rest_base_url() is not None  # the URL composes…

    def test_rest_lane_is_inert_without_base_url(self, env):
        env(self_env="dev", slug="e2e", base_url=None)
        assert PlaneHttpClient.is_available() is False


# ── P2-D: the explicit-constructor bypass ───────────────────────────────────


class TestConstructorBypassClosed:
    """P2-D — an explicit parameter may never introduce a workspace the
    resolver did not return; a refused resolution ⇒ an INERT client."""

    def test_refused_resolution_yields_an_inert_client(self, env):
        env(self_env="dev", slug="nea")
        client = PlaneHttpClient()
        assert client.inert_reason is not None
        assert client._base_url is None

    @pytest.mark.asyncio
    async def test_inert_client_raises_with_the_typed_reason(self, env):
        env(self_env="dev", slug="nea")
        client = PlaneHttpClient()
        with pytest.raises(PlaneAPIError) as exc:
            await client.list_projects()
        assert "inert" in str(exc.value)
        assert "production-only" in str(exc.value)

    def test_explicit_workspace_slug_cannot_introduce_a_workspace(self, env):
        """The explicit slug is DISCARDED as an authority — the resolved
        slug wins, so the header and the URL can never disagree."""
        env(self_env="dev", slug="e2e")
        client = PlaneHttpClient(workspace_slug="nea")
        assert client.workspace_slug == "e2e"

    def test_explicit_base_url_with_a_foreign_workspace_is_inert(self, env):
        """An explicit base URL carrying ``/workspaces/nea/`` under an
        ``e2e`` resolution is INERT — the split-brain URL/header pair is
        unrepresentable."""
        env(self_env="dev", slug="e2e")
        client = PlaneHttpClient(
            base_url="https://plane.example.test/api/v1/workspaces/nea/projects/"
        )
        assert client.inert_reason is not None
        assert client._base_url is None

    def test_explicit_base_url_with_the_resolved_workspace_is_fine(self, env):
        env(self_env="dev", slug="e2e")
        client = PlaneHttpClient(
            base_url="https://plane.example.test/api/v1/workspaces/e2e/projects/"
        )
        assert client.inert_reason is None
        assert client.workspace_slug == "e2e"

    def test_injected_resolution_is_honoured(self, env):
        """P2-M — the gate-(iv) injection seam: a refused resolution handed
        to the constructor produces an inert client even when the env
        would allow it."""
        env(self_env="dev", slug="e2e")
        from daemon.plane_workspace import resolve_plane_workspace

        refused = resolve_plane_workspace(
            {"ENSEMBLE_SELF_ENV": "dev", "PLANE_MCP_WORKSPACE_SLUG": "nea"}
        )
        client = PlaneHttpClient(resolution=refused)
        assert client.inert_reason is not None


# ── P2-C: one normalization, both lanes ─────────────────────────────────────


class TestOneCanonicalNormalization:
    """P2-C — the same env value yields the SAME slug in both lanes."""

    @pytest.mark.parametrize(
        "raw", ["e2e", " e2e ", '"e2e"', "'e2e'"]
    )
    def test_both_lanes_agree(self, env, raw):
        env(self_env="dev", slug=raw)
        mcp_slug = PlaneServerDefinition().get_base_config()["headers"][
            "x-workspace-slug"
        ]
        rest_slug = PlaneHttpClient().workspace_slug
        assert mcp_slug == "e2e"
        assert rest_slug == mcp_slug

    def test_the_rest_lane_no_longer_reads_the_slug_itself(self):
        """Structural: the REST module must not READ
        ``PLANE_MCP_WORKSPACE_SLUG`` on its own any more — the resolver
        owns the value (one normalization, one source). A docstring may
        still NAME the variable; a read may not."""
        from pathlib import Path

        import daemon.clients.plane_http_client as phc

        source = Path(phc.__file__).read_text(encoding="utf-8")
        for read_form in (
            'os.environ.get("PLANE_MCP_WORKSPACE_SLUG"',
            '_env("PLANE_MCP_WORKSPACE_SLUG")',
            'environ["PLANE_MCP_WORKSPACE_SLUG"]',
        ):
            assert read_form not in source, (
                f"REST lane still reads the slug directly: {read_form}"
            )


# ── NR1: live is byte-identical ──────────────────────────────────────────────


class TestLiveByteIdentical:
    """NR1 — on live the resolution changes nothing the operator sees."""

    def test_live_emits_the_configured_slug_verbatim(self, env):
        env(self_env="live", slug="nea", db="ensemble_prod")
        config = PlaneServerDefinition().get_base_config()
        assert config["headers"]["x-workspace-slug"] == "nea"
        assert config["transport"] == "streamable-http"
        assert config["url"] == MCP_URL

    def test_live_rest_url_keeps_its_shape(self, env):
        env(self_env="live", slug="nea", db="ensemble_prod")
        assert _rest_base_url() == f"{REST_URL}/api/v1/workspaces/nea/projects/"
