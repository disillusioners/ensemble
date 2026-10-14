"""Phase B T14 — auth-OFF fail-closed tests for the prompts router.

Per phaseB-plan.md D3 + T14 acceptance (R4 mitigation): when auth
is OFF (``auth_config`` absent on ``app.state``), every endpoint on
the prompts router returns **503 AUTH_NOT_CONFIGURED** — reads
included per the amended scope. The conservative default forbids
silent local-operator trust (no decorator-inert path that lets a
loopback caller approve without an authenticated identity).

The fixtures build a FRESH FastAPI app (NOT the production
``create_app`` — that one runs the full lifespan + DB migrations +
manager construction, which is not what T14 tests). The fresh app:

* mounts ONLY the prompts router,
* leaves ``app.state.auth_config = None`` (the OFF signal — the
  decorator reads ``getattr(app.state, 'auth_config', None)``),
* leaves ``app.state.manager = MagicMock()`` so the router's
  ``_get_service`` helper resolves to a service stub (the service
  is NEVER reached on the auth-OFF path — the 503 gate fires first
  via ``_require_auth_on``; the service stub exists only to prove
  the gate precedes the call).

The 15 endpoints must each return 503. We use FastAPI's
``TestClient`` (no httpx / async machinery) so the test reads as
straightforward request/response assertions.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daemon.routers.prompts import router as prompts_router


@pytest.fixture
def fresh_app():
    """Fresh FastAPI app with auth OFF + a stubbed manager.

    The router's ``_get_service`` helper looks up
    ``request.app.state.manager._prompt_version_service`` — the
    auth-OFF gate fires BEFORE the service is touched, so a
    bare MagicMock with a sentinel attribute is sufficient.

    The prompts router ships with ``prefix="/prompts"``; the
    production wiring in ``daemon/api.py`` mounts it on
    ``api_router`` which carries ``prefix="/api"`` (api.py:3406).
    Mirror that here so the test addresses the production path.
    """
    app = FastAPI()
    api_router = type("APIRouter", (), {})  # noqa: F841 — placeholder
    # Use a sub-router pattern that mirrors api.py's prefix layout:
    # create the parent with /api prefix, include the prompts router.
    from fastapi import APIRouter
    api = APIRouter(prefix="/api")
    api.include_router(prompts_router)
    app.include_router(api)
    # Auth OFF: NEVER set ``auth_config`` (the decorator's
    # ``_auth_enabled`` reads ``getattr(app.state, 'auth_config',
    # None)`` and treats absence as OFF).
    app.state.manager = MagicMock()
    app.state.manager._prompt_version_service = MagicMock()
    return app


@pytest.fixture
def client(fresh_app):
    return TestClient(fresh_app, raise_server_exceptions=False)


# ─────────────────────────────────────────────────────────────────────
# Endpoint table — the 15-endpoint family (12 canonical + the 3
# USER-DIRECTIVE-#2 deltas 2026-10-14).
# Each entry: (method, path, optional body).
# ─────────────────────────────────────────────────────────────────────

_ENDPOINTS = [
    # 1. list versions
    ("GET", "/api/prompts/versions", None),
    # 2. get version
    ("GET", "/api/prompts/versions/abc-123", None),
    # 3. diff version
    ("GET", "/api/prompts/versions/abc-123/diff", None),
    # 4. approve
    ("POST", "/api/prompts/versions/abc-123/approve", None),
    # 5. reject
    ("POST", "/api/prompts/versions/abc-123/reject", None),
    # 6. submit
    ("POST", "/api/prompts/versions/abc-123/submit", None),
    # 7. retract
    ("POST", "/api/prompts/versions/abc-123/retract", None),
    # 8. create-draft
    ("POST", "/api/prompts/versions",
     {"agent_id": "developer", "version_tag": "v2", "artifact": "soul", "content": "x"}),
    # 9. set selection
    ("POST", "/api/prompts/selections",
     {"agent_id": "developer", "version_tag": "v2", "artifact": "soul", "version_id": "abc-123"}),
    # 10. clear selection
    ("DELETE", "/api/prompts/selections/developer/v2/soul", None),
    # 11. effective
    ("GET", "/api/prompts/effective/developer/v2/soul", None),
    # 12. audit
    ("GET", "/api/prompts/audit", None),
    # 13. update version content (UD#2)
    ("PATCH", "/api/prompts/versions/abc-123", {"content": "x"}),
    # 14. delete version (UD#2)
    ("DELETE", "/api/prompts/versions/abc-123", None),
    # 15. pending-count (UD#2 badge feed)
    ("GET", "/api/prompts/pending-count", None),
]


def _assert_auth_not_configured(resp: Any) -> None:
    """Assert the response body matches the auth-OFF 503 contract."""
    assert resp.status_code == 503, (
        f"expected 503 AUTH_NOT_CONFIGURED, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    detail = body.get("detail", body)
    if isinstance(detail, dict):
        assert detail.get("error") == "AUTH_NOT_CONFIGURED", (
            f"expected error=AUTH_NOT_CONFIGURED, got {detail}"
        )


# ─────────────────────────────────────────────────────────────────────
# The T14 acceptance: every endpoint returns 503.
# ─────────────────────────────────────────────────────────────────────


class TestAuthOffAllEndpoints:
    """D3 + T14 acceptance — every endpoint fails closed when auth OFF."""

    @pytest.mark.parametrize("method,path,body", _ENDPOINTS, ids=lambda x: x if isinstance(x, str) else "")
    def test_endpoint_returns_503_auth_not_configured(
        self, client, method, path, body
    ):
        if body is not None:
            resp = client.request(method, path, json=body)
        else:
            resp = client.request(method, path)
        _assert_auth_not_configured(resp)

    def test_auth_config_absent_on_state(self, fresh_app):
        """The OFF signal is the absence of ``auth_config`` (the
        decorator's contract — decorators.py:74-84). The test fixture
        never sets it; assert it stays unset so the contract is
        unambiguous. ``hasattr`` distinguishes the OFF signal
        ("attribute never set") from a hypothetical ON state where
        ``auth_config=None`` would also evaluate truthy-falsy."""
        assert not hasattr(fresh_app.state, "auth_config"), (
            "auth_config must be absent on the auth-OFF app (the "
            "decorator's OFF signal — decorators.py:74-84)"
        )


class TestAuthOffNoLocalOperatorTrust:
    """R4 mitigation — no silent local-operator path bypasses the gate.

    The decorator (``@require_scope``) is INERT when auth is OFF
    (decorators.py:74-84). The router adds an explicit
    ``_require_auth_on`` gate so the 503 fires EVEN WHEN the
    decorator would pass. Tests cover the four classes of caller
    that might otherwise reach the handler:

    * No headers at all (loopback-style)
    * Loopback-style hostname (the legacy local-trust heuristic)
    * Bearer token (machine lane)
    * Session cookie (cookie lane)
    """

    def test_no_headers_returns_503(self, client):
        resp = client.get("/api/prompts/versions")
        _assert_auth_not_configured(resp)

    def test_loopback_hostname_returns_503(self, client):
        """Legacy local-trust heuristic must NOT short-circuit the
        gate — explicit 503 even when the Host header names loopback."""
        resp = client.get(
            "/api/prompts/versions", headers={"Host": "localhost"}
        )
        _assert_auth_not_configured(resp)

    def test_bearer_header_returns_503(self, client):
        """A bearer token without an auth context still hits the
        503 (the decorator passes inert, the explicit gate fires)."""
        resp = client.get(
            "/api/prompts/versions",
            headers={"Authorization": "Bearer fake-token"},
        )
        _assert_auth_not_configured(resp)

    def test_session_cookie_returns_503(self, client):
        """A session cookie without an auth context still hits the
        503 — no cookie-path bypass."""
        resp = client.get(
            "/api/prompts/versions",
            cookies={"ensemble_sid": "fake-session-id"},
        )
        _assert_auth_not_configured(resp)

    def test_post_with_body_returns_503(self, client):
        """A mutating POST with a body still 503s (the gate runs
        BEFORE body parsing). The body is NOT echoed in the response
        — D3 fail-closed posture guards against any pre-parse
        dispatch leak."""
        resp = client.post(
            "/api/prompts/versions",
            json={"agent_id": "developer", "version_tag": "v2",
                  "artifact": "soul", "content": "x"},
        )
        _assert_auth_not_configured(resp)

    def test_service_is_never_called_on_auth_off_path(
        self, client, fresh_app
    ):
        """D3 / T14: the service stub MUST NOT receive any call when
        auth is OFF — the gate fires first. This proves no silent
        path leaks through to the service layer (e.g. via a future
        refactor that wires service-before-auth)."""
        svc = fresh_app.state.manager._prompt_version_service
        # Run the full endpoint sweep; collect 503s.
        for method, path, body in _ENDPOINTS:
            if body is not None:
                client.request(method, path, json=body)
            else:
                client.request(method, path)
        # The service must be untouched.
        assert not svc.method_calls, (
            f"service received calls on the auth-OFF path: "
            f"{[c[0] for c in svc.method_calls]}"
        )


class TestAuthOffNoNestedCallStack:
    """T14 acceptance — even deep-link paths (with parametrized
    segments) return 503 — no path-template-match bypass."""

    def test_diff_with_against_query_returns_503(self, client):
        resp = client.get("/api/prompts/versions/abc-123/diff?against=approved")
        _assert_auth_not_configured(resp)

    def test_effective_with_all_segments_returns_503(self, client):
        resp = client.get("/api/prompts/effective/developer/v2/soul")
        _assert_auth_not_configured(resp)

    def test_clear_selection_with_all_segments_returns_503(self, client):
        resp = client.delete(
            "/api/prompts/selections/developer/v2/soul"
        )
        _assert_auth_not_configured(resp)

    def test_audit_with_filters_returns_503(self, client):
        resp = client.get(
            "/api/prompts/audit?agent_id=developer&version_tag=v2&artifact=soul"
        )
        _assert_auth_not_configured(resp)