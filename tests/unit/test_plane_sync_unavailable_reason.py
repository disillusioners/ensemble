"""P2-L — the Plane sync service reports the resolver's TYPED refusal reason.

Covers pin **P2-L** (lane ``plane-pm-issue-tools`` Phase 2):
``PlaneSyncService.unavailable_reason()`` must carry the resolver's typed
refusal reason when the refusal cause is a WORKSPACE refusal — never the
"API key not set" diagnostic, which would send the operator hunting for a
credential that is present and working.

The kill-switch still outranks the policy (an operator decision beats a
policy decision), and a permitted resolution with no REST key still reports
the missing key.
"""

from __future__ import annotations

from daemon.services.plane_sync_service import PlaneSyncService
from tests.helpers.plane_workspace_fixtures import (
    make_plane_env_fixture,
    reset_plane_workspace_cache,  # noqa: F401 — the autouse fixture
)


env = make_plane_env_fixture(
    (
        ("self_env", "ENSEMBLE_SELF_ENV", "dev"),
        ("slug", "PLANE_MCP_WORKSPACE_SLUG", "e2e"),
        ("db", "POSTGRES_DB", "ensemble_dev"),
        ("sync_enabled", "PLANE_SYNC_ENABLED", None),
        ("base_url", "PLANE_BASE_URL", "https://plane.example.test"),
        ("api_key", "PLANE_API_KEY", "dummy-rest-key"),
    ),
)


class TestSyncUnavailableReason:
    def test_workspace_refusal_carries_the_typed_reason(self, env):
        env(self_env="dev", slug="nea")
        reason = PlaneSyncService.unavailable_reason()
        assert reason is not None
        assert reason == (
            "workspace 'nea' is production-only; this environment resolves to dev"
        )

    def test_refusal_is_not_rendered_as_a_missing_key(self, env):
        env(self_env="dev", slug="nea", api_key=None)
        reason = PlaneSyncService.unavailable_reason()
        assert reason is not None
        assert "PLANE_API_KEY" not in reason
        assert "not configured" not in reason

    def test_unset_slug_refusal_carries_the_typed_reason(self, env):
        env(self_env="dev", slug=None)
        reason = PlaneSyncService.unavailable_reason()
        assert reason == (
            "PLANE_MCP_WORKSPACE_SLUG is unset in a non-production "
            "environment; expected 'e2e'"
        )

    def test_kill_switch_outranks_the_policy(self, env):
        env(self_env="dev", slug="nea", sync_enabled="false")
        reason = PlaneSyncService.unavailable_reason()
        assert reason == "disabled by kill-switch (PLANE_SYNC_ENABLED=false)"

    def test_missing_key_still_reports_the_key(self, env):
        env(self_env="dev", slug="e2e", api_key=None)
        reason = PlaneSyncService.unavailable_reason()
        assert reason == "not configured (PLANE_BASE_URL / PLANE_API_KEY not set)"

    def test_permitted_environment_reports_no_reason(self, env):
        env(self_env="dev", slug="e2e")
        assert PlaneSyncService.unavailable_reason() is None
        assert PlaneSyncService.is_available() is True

    def test_live_reports_no_reason(self, env):
        env(self_env="live", slug="nea", db="ensemble_prod")
        assert PlaneSyncService.unavailable_reason() is None


class TestInjectedClientSeam:
    """P2-D (NB3) — the sync service's ``http_client=`` injection seam is
    gated: an injected client that does not carry the resolved slug is
    rejected (the fast-path must not become a split-brain bypass)."""

    def test_injected_client_with_the_resolved_slug_is_accepted(self, env):
        from daemon.clients.plane_http_client import PlaneHttpClient

        env(self_env="dev", slug="e2e")
        client = PlaneHttpClient()
        service = PlaneSyncService(project_repo=None, http_client=client)
        assert service._get_client() is client

    def test_injected_client_with_a_foreign_slug_is_rejected(self, env):
        from daemon.clients.plane_http_client import PlaneHttpClient

        env(self_env="dev", slug="e2e")
        client = PlaneHttpClient()
        # Simulate a client aimed at a different workspace.
        client._workspace_slug = "nea"
        service = PlaneSyncService(project_repo=None, http_client=client)
        assert service._get_client() is None

    def test_injected_client_under_a_refusal_is_rejected(self, env):
        from daemon.clients.plane_http_client import PlaneHttpClient

        env(self_env="dev", slug="nea")
        client = PlaneHttpClient()
        service = PlaneSyncService(project_repo=None, http_client=client)
        assert service._get_client() is None
