"""Scope matrix — oauth2-auth Phase 4 (§4 row 5, G4.1).

Table-driven: route family × token scope → expected auth-layer outcome,
one row per §7.3 consumer label, over the REAL decorated app (the
D4.7-decorated families from tests/unit/test_router_scope_manifest.py
are the authoritative family → scope map; this file is the CONSUMER
view: does a token minted with the §7.3 scopes pass or 403?).

Assertion model: the auth layer's verdict is 200/4xx-from-handler vs
401/403. Decorated routes execute their scope dependency BEFORE the
handler, so an INSUFFICIENT row must yield exactly 403
SCOPE_INSUFFICIENT regardless of the (absent) daemon manager. A
SUFFICIENT row is admitted by the auth layer — the assertion is
`status not in (401, 403)` (a handler-level 5xx from the missing
manager is OUT of the auth matrix's scope; fixtures attach only the
auth half, per the harness contract).

§7.3 consumer labels and their minimum scopes (verbatim from the
requirements table):
"""

from __future__ import annotations

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

#: §7.3 rows — (consumer label, minted scopes). ``<vm-name>`` rows are
#: the generic remote-VM pattern (row 14): "tailored to use case" —
#: the row below models the minimal tailoring the runbook documents.
CONSUMER_LABELS: list[tuple[str, tuple[str, ...]]] = [
    ("mcpservers-test", ("system:read", "mcp:read")),                      # row 7
    ("e2e-pause-resume-test", ("instances:read", "instances:write", "jobs:read", "jobs:write", "messages:write")),  # row 8
    ("e2e-mock-source", ("sources:write", "instances:read")),              # row 9
    ("boot-e2e-canary", ()),                                               # row — public probes only
    ("frontend-spa", ()),                                                  # row — cookie auth
    ("promote-rotate", ()),                                                # row — public probes only
    ("operator-cli", ("auth:admin",)),                                     # §7.3
    ("vm-edge-01", ("instances:read", "jobs:read")),                       # row 14 minimal tailoring
]

#: Family probes: (probe path, family scope requirement per the D4.7
#: manifest). Each probe targets a route whose decoration is the
#: family's representative; the auth verdict is decided by the
#: decorated requirement vs the presented grant.
PROBES: list[tuple[str, str, tuple[str, ...]]] = [
    ("GET", "/api/mcp/kb/keys", ("mcpkb:admin",)),                 # T3.12 family
    ("GET", "/api/mcp-servers", ("mcp:read",)),                    # mcp-servers family
    ("GET", "/api/instances", ("instances:read",)),                # instances read
    ("POST", "/api/instances", ("instances:write",)),              # instances write
    ("POST", "/api/instances/i1/messages", ("messages:write",)),   # messages write
    ("GET", "/api/jobs", ("jobs:read",)),                          # jobs read
    ("POST", "/api/jobs", ("jobs:write",)),                        # jobs write
    ("GET", "/api/sources", ("sources:write",)),                   # sources (write-class family)
    ("POST", "/api/database/switch", ("auth:admin",)),             # DB switch admin
    ("POST", "/api/recovery/recover_report_delivery", ("auth:admin",)),  # recovery admin
    ("GET", "/api/workspace/w1/tree", ("workspace:read",)),        # workspace read
]


def _mint(engine, label: str, scopes: tuple[str, ...]) -> str:
    import hashlib

    token = f"matrix-{label}"
    with Session(engine) as s:
        s.add(
            BearerToken(
                id_hash=hashlib.sha256(token.encode()).hexdigest(),
                subject=label,
                scopes=",".join(scopes),
                label=label,
                created_at="2026-10-11T00:00:00+00:00",
                expires_at="2099-01-01T00:00:00+00:00",
            )
        )
        s.commit()
    return token


@pytest.fixture(scope="module")
def matrix_client(tmp_path_factory):
    warnings.filterwarnings("ignore")
    tmp_path = tmp_path_factory.mktemp("scope_matrix")
    stub = FakeOidcStub()
    cfg = on_auth_config()
    app = create_app(auth_config=cfg)
    attach_auth_state(app, cfg, stub, tmp_path)
    engine = app.state.auth_session_repository._engine
    tokens = {label: _mint(engine, label, scopes) for label, scopes in CONSUMER_LABELS}
    client = TestClient(
        app, base_url="http://testserver", follow_redirects=False,
        raise_server_exceptions=False,
    )
    return client, tokens


def _expected(granted: tuple[str, ...], required: tuple[str, ...]) -> str:
    """The §7.3 table logic: 'admit' when the grant covers the route's
    declared requirement; insufficient → 403. (The requirement is the
    PROBE's own decoration — the per-route truth; the manifest-level
    ``|*`` mixed entries mean "some routes undeclared", not "family
    admits all".)"""
    if any(scope in granted for scope in required):
        return "admit"
    return "403"


class TestScopeMatrix:
    @pytest.mark.parametrize("method,path,required", PROBES, ids=lambda v: None)
    def test_row_per_consumer_label(self, matrix_client, method, path, required):
        """One §7.3 consumer label per row-group: the minted scopes
        decide admit (auth layer passes) vs 403 (SCOPE_INSUFFICIENT)."""
        client, tokens = matrix_client
        for label, granted in CONSUMER_LABELS:
            r = client.request(
                method, path, headers={"Authorization": f"Bearer {tokens[label]}"}
            )
            expected = _expected(granted, required)
            if expected == "403":
                assert r.status_code == 403, (
                    f"{label} on {method} {path}: expected 403, got {r.status_code}"
                )
                assert r.json()["detail"]["error"] == "SCOPE_INSUFFICIENT"
            else:
                assert r.status_code not in (401, 403), (
                    f"{label} on {method} {path}: auth layer refused a "
                    f"sufficient grant ({r.status_code} {r.text[:160]})"
                )

    def test_every_consumer_label_has_a_row(self):
        """G4.1: the matrix has a row for every §7.3 consumer label."""
        s73_labels = {
            "mcpservers-test",
            "e2e-pause-resume-test",
            "e2e-mock-source",
            "boot-e2e-canary",
            "frontend-spa",
            "promote-rotate",
            "operator-cli",
            "<vm-name>",
        }
        present = {label for label, _ in CONSUMER_LABELS}
        assert present >= (s73_labels - {"<vm-name>"})
        assert "vm-edge-01" in present  # the <vm-name> modeling row
