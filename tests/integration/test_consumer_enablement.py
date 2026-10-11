"""Consumer enablement — oauth2-auth Phase 4 (D4.5, §4 row 7, G4.3).

Two deliverables in one file:

1. **The 3 in-repo YES-WITH-CONFIG consumers** (§7.1 rows 7/8/9)
   replay their EXACT request shapes against an auth-ON app:
   * with the env-var bearer minted per the §7.3 scope table → the
     auth layer admits every shape (zero code change — D4.5);
   * without any token → loud 401 with the machine-JSON body (the
     §7.4 fail-loudly contract).

2. **The 10 YES-UNCHANGED consumers verified tokenless** (G4.3, AC-32
   partial evidence — fixture level, no daemon boot): rows 1-6
   (upgrade-machinery probes: /livez, /readyz), row 10
   (boot-e2e-snapshots: /readyz), row 11 (boot-e2e-maintenance: the
   availability canary), row 17 (external webhook senders — module
   lane, tokenless), row 18 (MCP-KB clients — module lane, tokenless).
   The upgrade_tools.py in-process probe (row 6) is covered by the
   same /livez+/readyz parity.

Consumer-shape sources: scripts/test_builtin_mcp_servers.sh:143-422
(GET /api/health, GET /api/mcp-servers*),
scripts/e2e_pause_resume_test.py (POST /api/instances, POST
.../pause, POST /api/messages, POST /api/jobs), tests/e2e/
mock_source_server.py (POST /api/sources, GET /api/instances*).
"""

from __future__ import annotations

import hashlib
import warnings

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from daemon.api import create_app
from daemon.repositories.auth_session.models import (
    AuthBootstrapConsumption,
    AuthSession,
    AuthState,
    BearerToken,
)
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub

AUTH_TABLES = [
    AuthSession.__table__,
    AuthState.__table__,
    BearerToken.__table__,
    AuthBootstrapConsumption.__table__,
]

#: §7.1 rows 7/8/9 — the in-repo YES-WITH-CONFIG consumers: their env
#: var, §7.3 scopes, and replayed request shapes.
CONSUMERS: list[dict] = [
    {
        "label": "mcpservers-test",                      # row 7
        "env_var": "ENSEMBLE_BEARER_TOKEN",
        "scopes": ["system:read", "mcp:read"],
        "shapes": [
            ("GET", "/api/health", None),                # public
            ("GET", "/api/mcp-servers", None),           # mcp:read
        ],
    },
    {
        "label": "e2e-pause-resume-test",                # row 8
        "env_var": "ENSEMBLE_BEARER_TOKEN",
        "scopes": ["instances:read", "instances:write", "jobs:read", "jobs:write", "messages:write"],
        "shapes": [
            ("POST", "/api/instances", {"name": "e2e-instance", "agent": "developer"}),
            ("POST", "/api/instances/i1/pause", None),
            ("POST", "/api/messages", {"instance_id": "i1", "message": "ping"}),
            ("POST", "/api/jobs", {"prompt": "e2e", "agent": "developer"}),
        ],
    },
    {
        "label": "e2e-mock-source",                      # row 9
        "env_var": "ENSEMBLE_BEARER_TOKEN",
        "scopes": ["sources:write", "instances:read"],
        "shapes": [
            ("POST", "/api/sources", {"source_type": "webhook", "name": "mock", "config": {"agent": "./agents/developer"}}),
            ("GET", "/api/instances", None),
        ],
    },
]


@pytest.fixture(scope="module")
def on_env(tmp_path_factory):
    warnings.filterwarnings("ignore")
    tmp_path = tmp_path_factory.mktemp("consumer_enablement")
    stub = FakeOidcStub()
    cfg = on_auth_config()
    app = create_app(auth_config=cfg)
    attach_auth_state(app, cfg, stub, tmp_path)
    engine = app.state.auth_session_repository._engine
    tokens: dict[str, str] = {}
    for consumer in CONSUMERS:
        token = f"env-token-{consumer['label']}"
        with Session(engine) as s:
            s.add(
                BearerToken(
                    id_hash=hashlib.sha256(token.encode()).hexdigest(),
                    subject=consumer["label"],
                    scopes=",".join(consumer["scopes"]),
                    label=consumer["label"],
                    created_at="2026-10-11T00:00:00+00:00",
                    expires_at="2099-01-01T00:00:00+00:00",
                )
            )
            s.commit()
        tokens[consumer["label"]] = token
    client = TestClient(
        app, base_url="http://testserver", follow_redirects=False,
        raise_server_exceptions=False,
    )
    return client, tokens, app


class TestYesWithConfigConsumers:
    @pytest.mark.parametrize("consumer", CONSUMERS, ids=lambda c: c["label"])
    def test_with_env_bearer_auth_layer_admits_every_shape(
        self, on_env, consumer
    ):
        """D4.5: env-var bearer + zero consumer code change — every
        replayed shape is ADMITTED by the auth layer (a handler-level
        5xx from the absent daemon manager is out of the auth matrix)."""
        client, tokens, _ = on_env
        for method, path, body in consumer["shapes"]:
            r = client.request(
                method,
                path,
                json=body,
                headers={"Authorization": f"Bearer {tokens[consumer['label']]}"},
            )
            assert r.status_code not in (401, 403), (
                f"{consumer['label']} {method} {path}: auth layer refused "
                f"({r.status_code} {r.text[:160]}) — consumer would be "
                "locked out, violating D4.5"
            )

    @pytest.mark.parametrize("consumer", CONSUMERS, ids=lambda c: c["label"])
    def test_without_token_loud_fail(self, on_env, consumer):
        """§7.4 fail-loudly: the same shapes tokenless are REJECTED by
        the auth layer — the FR-24 contract is 401 machine-JSON for an
        explicit machine Accept, or the browser 302 to /api/auth/login
        under the */* heuristic. PUBLIC shapes in a consumer's table
        (e.g. row 7's GET /api/health) answer 200 tokenless by design.
        Never a silent pass on a protected shape; the daemon does not
        crash."""
        client, _, _ = on_env
        from tests.unit.test_router_scope_manifest import (
            PINNED_SCOPE_MANIFEST,
            _family_of,
            _resolve_entry,
        )

        #: Pure-wildcard families (public/module posture — the D4.7
        #: manifest's explicit ``*``): tokenless passes by design.
        PURE_WILDCARD_FAMILIES = {
            family
            for family, entry in PINNED_SCOPE_MANIFEST.items()
            if _resolve_entry(entry) == {"*"}
        }

        for method, path, body in consumer["shapes"]:
            if _family_of(path) in PURE_WILDCARD_FAMILIES:
                # PUBLIC/module shape (e.g. row 7's GET /api/health).
                r = client.request(method, path, json=body)
                assert r.status_code not in (401, 403)
                continue
            # Machine Accept → the 401 machine-JSON contract.
            r = client.request(
                method, path, json=body, headers={"Accept": "application/json"}
            )
            assert r.status_code == 401, (
                f"{consumer['label']} {method} {path}: expected the loud "
                f"401, got {r.status_code}"
            )
            assert r.json()["error"] == "AUTH_REQUIRED"
            assert r.headers.get("www-authenticate", "").startswith("Bearer ")
            # Browser heuristic → the 302 contract.
            r2 = client.request(method, path, json=body, headers={"Accept": "*/*"})
            assert r2.status_code == 302
            assert r2.headers["location"].startswith("/api/auth/login")

    def test_public_probe_shape_untouched_tokenless(self, on_env):
        """Row 7's first leg is PUBLIC: GET /api/health answers tokenless
        even with auth ON (the consumer's public probe never needed a
        token)."""
        client, _, _ = on_env
        r = client.get("/api/health")
        assert r.status_code == 200


class TestYesUnchangedTokenless:
    """G4.3 / AC-32 partial evidence: the 10 YES-UNCHANGED consumers
    verified tokenless against an auth-ON app (public probes + module
    lanes; fixture level — no daemon boot)."""

    @pytest.fixture(scope="class")
    def tokenless_client(self, tmp_path_factory):
        warnings.filterwarnings("ignore")
        tmp_path = tmp_path_factory.mktemp("yes_unchanged")
        stub = FakeOidcStub()
        cfg = on_auth_config()
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)
        return TestClient(
            app, base_url="http://testserver", follow_redirects=False,
            raise_server_exceptions=False,
        ), app

    def test_upgrade_machinery_probes_tokenless(self, tokenless_client):
        """Rows 1-6 + 10: promote.sh / rollback.sh / restart.sh /
        status.sh / adopt-unit.sh / deploy.sh / watchdog-watcher.sh /
        upgrade_tools._probe_self / boot-e2e-snapshots — ALL ride
        GET /livez + GET /readyz (public probes): tokenless requests
        reach the ROUTE and get the module's own answer (200, or the
        readiness 503 from the unbooted fixture manager) — never the
        OAuth2 401/302 contract."""
        client, _ = tokenless_client
        for probe in ("/livez", "/readyz"):
            r = client.get(probe)
            assert r.status_code not in (401, 403), (
                f"{probe} tokenless handed an auth rejection: {r.status_code}"
            )
            assert "AUTH_REQUIRED" not in r.text
        assert client.get("/livez").status_code == 200  # liveness is state-free

    def test_maintenance_availability_canary_tokenless(self, tokenless_client):
        """Row 11: boot-e2e-maintenance rides the availability canary —
        PUBLIC per §5.1, tokenless, answered by the MODULE (200 here;
        a module-level 503 would equally prove the lane — the point is
        the absence of the OAuth2 contract)."""
        client, _ = tokenless_client
        r = client.get("/api/maintenance/checkpoint-cleanup/availability")
        assert r.status_code not in (401, 403)
        assert "AUTH_REQUIRED" not in r.text

    def test_webhook_senders_tokenless_module_lane(self, tokenless_client):
        """Row 17 (AMEND v1.4): external webhook senders are
        auth-INDEPENDENT — the tokenless request reaches the module
        (404 source-not-found here proves the middleware passed it
        through; the module's own guard would decide with a real
        source)."""
        class _Repo:
            def get_source_config(self, source_id):
                return None

        class _Manager:
            _source_repository = _Repo()
            source_registry = {}
            is_write_paused = False

        client, app = tokenless_client
        app.state.manager = _Manager()
        r = client.post("/api/webhooks/any-source", json={"x": 1})
        assert r.status_code == 404
        assert "AUTH_REQUIRED" not in r.text

    def test_mcp_kb_clients_tokenless_module_lane(self, tokenless_client):
        """Row 18 (AMEND v1.4): MCP-KB clients ride the module-lane
        Mounts — tokenless, NOT handed the OAuth2 401 contract (the
        mount has no manager here; the point is the LANE, not the
        module's answer)."""
        client, _ = tokenless_client
        r = client.post("/api/mcp/kb", json={"jsonrpc": "2.0", "method": "initialize", "id": 1})
        assert r.status_code != 401
        assert "AUTH_REQUIRED" not in r.text

    def test_public_probe_census_tokenless(self, tokenless_client):
        """The remaining public-probe surface (the /views/livez variant,
        /api/info) tokenless with auth ON."""
        client, _ = tokenless_client
        assert client.get("/api/info").status_code == 200
