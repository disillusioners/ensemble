"""Custom-agents router authorization tests (F1, overnight 2026-10-14).

Leader-gate fix-cycle F1 — the custom-agents REST family shipped with
ZERO authorization. This file pins the remediation (the dispatcher's
six F1 tasks, items 3 + 6):

* **Decorator presence cage** — every route on
  ``daemon/routers/custom_agents.py`` carries an explicit
  ``@require_scope`` stamp of the expected class (write vs read),
  read straight off the route table (``endpoint.required_scopes``
  — the same attribute the D4.7 scope-manifest cage reads; see
  ``tests/unit/test_router_scope_manifest.py::_route_required_scopes``):

  - ``POST ""``            → ``prompts:write`` (write-class)
  - ``DELETE /{user_name}`` → ``prompts:write`` (write-class)
  - ``GET ""``             → ``prompts:read``  (read-class)
  - ``GET /{user_name}``   → ``prompts:read``  (read-class)

  The route COUNT is pinned at 4 — a new undecorated route fails
  the census loud (mirrors the manifest-cage posture).

* **Auth-OFF 503 gate** — every endpoint fails closed with
  ``503 AUTH_NOT_CONFIGURED`` when ``app.state.auth_config`` is
  absent (project posture S8 / D3). Mirrors the prompts-family
  T14 exemplar ``tests/test_prompts_router_auth_off.py`` (same
  fresh-app + TestClient shape; the ``@require_scope`` decorator
  is inert when auth is OFF — decorators.py:74-84 — so the inline
  ``_require_auth_on`` gate is the fail-closed surface).

* **Import-repair regression pins** — the F1 finding #3: the
  router lazily imported ``AuthContextMissingError`` from
  ``daemon.repositories.prompt_version.repository`` (WRONG module
  — the class is defined at
  ``daemon/services/prompt_version_service.py:226``). Because the
  import is lazy, every POST 500-ed on the ImportError. The pins
  assert (a) the class resolves from its defining module, (b) the
  repo module does NOT export it (re-importing from there is the
  regression), and (c) the router's helper no longer trips the
  ImportError path when invoked.

Fence discipline (2026-10-12 critical note — zero tolerance):
``ENSEMBLE_SELF_ENV=dev`` + ``POSTGRES_DB`` test-only guard +
``FAKE_HOME`` mktemp; these tests resolve ZERO HOME-derived or
install-dir paths (pure in-process route inspection + a fresh
FastAPI app with a MagicMock manager).
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from daemon.routers import custom_agents as daemon_routers_custom_agents
from daemon.routers.custom_agents import router as custom_agents_router


# ──────────────────────────────────────────────────────────────────────────
# Fence — mirrors the family convention (tests/test_custom_agent_lifecycle.py
# + .smoke/test-fence.sh). No HOME-derived path is ever resolved here.
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def _fence_session():
    """Auto-used session fence: lock the test envs to dev mode."""
    allowed_dbs = {"ensemble_dev_test_only", "ensemble_test_only"}
    os.environ["ENSEMBLE_SELF_ENV"] = "dev"
    if os.environ.get("POSTGRES_DB") in (None, "", "ensemble_prod"):
        os.environ["POSTGRES_DB"] = "ensemble_dev_test_only"
    elif os.environ.get("POSTGRES_DB") not in allowed_dbs:
        pytest.exit(
            f"HARD ABORT: this test session targets "
            f"POSTGRES_DB={os.environ.get('POSTGRES_DB')!r}; "
            f"refuse to point custom-agents authz tests at a "
            f"non-dev DB (env-poison 3-guard convention).",
            returncode=3,
        )
    yield


# ──────────────────────────────────────────────────────────────────────────
# Expected scope map — the F1 write/read split (scope-REUSE convention:
# prompts:write / prompts:read, NO new scope minted — see the
# daemon/routers/custom_agents.py module docstring §Authorization).
# ──────────────────────────────────────────────────────────────────────────

_EXPECTED_SCOPES: dict[tuple[str, str], frozenset[str]] = {
    ("POST", ""): frozenset({"prompts:write"}),
    ("GET", ""): frozenset({"prompts:read"}),
    ("GET", "/{user_name}"): frozenset({"prompts:read"}),
    ("DELETE", "/{user_name}"): frozenset({"prompts:write"}),
}


def _route_scopes(route: APIRoute) -> frozenset[str] | None:
    """The route's declared scope set (wrapper OR dependency form).

    Same detection as the D4.7 cage's ``_route_required_scopes``.
    """
    scopes = getattr(getattr(route, "endpoint", None), "required_scopes", None)
    if scopes:
        return frozenset(scopes)
    for dep in getattr(getattr(route, "dependant", None), "dependencies", None) or []:
        dep_scopes = getattr(dep.call, "required_scopes", None)
        if dep_scopes:
            return frozenset(dep_scopes)
    return None


class TestScopeDecoratorPresence:
    """F1 task 6a — every route carries the expected scope class."""

    def test_route_count_pinned(self):
        """Exactly 4 routes — a 5th route without a manifest + census
        update fails loud here first."""
        routes = [r for r in custom_agents_router.routes if isinstance(r, APIRoute)]
        assert len(routes) == 4, (
            f"custom-agents route count drifted: {len(routes)} (pinned 4 — "
            "update _EXPECTED_SCOPES + both manifest cages when adding one)"
        )

    @pytest.mark.parametrize(
        "method,path_suffix",
        sorted(_EXPECTED_SCOPES.keys()),
        ids=[f"{m}-{p or 'list'}" for m, p in sorted(_EXPECTED_SCOPES.keys())],
    )
    def test_route_carries_expected_scope(self, method: str, path_suffix: str):
        """Each route's required_scopes matches the F1 class split."""
        matching = [
            r
            for r in custom_agents_router.routes
            if isinstance(r, APIRoute)
            and method in (r.methods or set())
            and r.path == "/custom-agents" + path_suffix
        ]
        assert len(matching) == 1, (
            f"expected exactly one {method} /custom-agents{path_suffix} route, "
            f"found {len(matching)}"
        )
        actual = _route_scopes(matching[0])
        expected = _EXPECTED_SCOPES[(method, path_suffix)]
        assert actual == expected, (
            f"{method} /custom-agents{path_suffix}: expected scopes "
            f"{sorted(expected)}, got {actual!r} (F1 write/read class split)"
        )

    def test_no_undecorated_route(self):
        """No route in the family may be scope-free (zero-authz is
        the F1 finding — this is its direct negative pin)."""
        undecorated = [
            f"{sorted(r.methods or set())} {r.path}"
            for r in custom_agents_router.routes
            if isinstance(r, APIRoute) and _route_scopes(r) is None
        ]
        assert not undecorated, (
            f"zero-authz regression: undecorated routes {undecorated}"
        )

    def test_scope_constants_reuse_prompts_family(self):
        """The F1 scope-REUSE convention: no new scope minted — the
        constants delegate to the prompts:* taxonomy verbatim."""
        from daemon.routers import custom_agents as ca

        assert ca._SCOPE_WRITE == "prompts:write"
        assert ca._SCOPE_READ == "prompts:read"


# ──────────────────────────────────────────────────────────────────────────
# Auth-OFF 503 gate — mirrors tests/test_prompts_router_auth_off.py
# (the T14 exemplar shape: fresh app, auth_config NEVER set, the
# service stub exists only to prove the gate fires BEFORE the call).
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_app():
    """Fresh FastAPI app with auth OFF + a stubbed manager.

    The router's ``_lifecycle_service`` helper looks up
    ``request.app.state.manager._custom_agent_lifecycle_service`` —
    the auth-OFF gate fires BEFORE the service is touched, so a
    MagicMock sentinel is sufficient (and its ``called`` assertions
    prove gate-before-service ordering). The production wiring
    mounts the router under ``/api`` (api.py:3773); mirror that.
    """
    app = FastAPI()
    api = APIRouter(prefix="/api")
    api.include_router(custom_agents_router)
    app.include_router(api)
    # Auth OFF: NEVER set ``auth_config`` (the decorator's OFF
    # signal — decorators.py:74-84).
    app.state.manager = MagicMock()
    app.state.manager._custom_agent_lifecycle_service = MagicMock()
    return app


@pytest.fixture
def client(fresh_app):
    return TestClient(fresh_app, raise_server_exceptions=False)


_ENDPOINTS = [
    ("POST", "/api/custom-agents",
     {"user_name": "myhelper", "name": "My Helper", "soul_md": "# soul"}),
    ("GET", "/api/custom-agents", None),
    ("GET", "/api/custom-agents/myhelper", None),
    ("DELETE", "/api/custom-agents/myhelper", None),
]


def _assert_auth_not_configured(resp: Any) -> None:
    """Assert the response matches the auth-OFF 503 contract."""
    assert resp.status_code == 503, (
        f"expected 503 AUTH_NOT_CONFIGURED, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    detail = body.get("detail", body)
    if isinstance(detail, dict):
        assert detail.get("error") == "AUTH_NOT_CONFIGURED", (
            f"expected error=AUTH_NOT_CONFIGURED, got {detail}"
        )


class TestAuthOffAllEndpoints:
    """S8 / D3 acceptance — every endpoint fails closed when auth OFF."""

    @pytest.mark.parametrize(
        "method,path,body", _ENDPOINTS, ids=lambda x: x if isinstance(x, str) else ""
    )
    def test_endpoint_returns_503_auth_not_configured(
        self, client, method, path, body
    ):
        if body is not None:
            resp = client.request(method, path, json=body)
        else:
            resp = client.request(method, path)
        _assert_auth_not_configured(resp)

    def test_auth_config_absent_on_state(self, fresh_app):
        """The OFF signal is the ABSENCE of ``auth_config`` (the
        decorator's contract — decorators.py:74-84)."""
        assert not hasattr(fresh_app.state, "auth_config"), (
            "auth_config must be absent on the auth-OFF app"
        )

    def test_gate_fires_before_service(self, fresh_app, client):
        """The 503 gate precedes ANY lifecycle-service call — the
        stub records a call only if the gate failed open."""
        svc = fresh_app.state.manager._custom_agent_lifecycle_service
        resp = client.post(
            "/api/custom-agents",
            json={"user_name": "x", "name": "X", "soul_md": "s"},
        )
        _assert_auth_not_configured(resp)
        svc.create_custom_agent.assert_not_called()


class TestAuthOffNoBypass:
    """No caller class slips past the gate (the decorator is inert
    when auth is OFF — decorators.py:74-84; the explicit
    ``_require_auth_on`` gate must refuse every lane)."""

    def test_no_headers_returns_503(self, client):
        _assert_auth_not_configured(client.get("/api/custom-agents"))

    def test_loopback_hostname_returns_503(self, client):
        resp = client.get("/api/custom-agents", headers={"Host": "localhost"})
        _assert_auth_not_configured(resp)

    def test_bearer_header_returns_503(self, client):
        resp = client.get(
            "/api/custom-agents",
            headers={"Authorization": "Bearer fake-token"},
        )
        _assert_auth_not_configured(resp)

    def test_session_cookie_returns_503(self, client):
        resp = client.get(
            "/api/custom-agents", cookies={"session": "fake-session"}
        )
        _assert_auth_not_configured(resp)


# ──────────────────────────────────────────────────────────────────────────
# Import-repair regression pins — F1 finding #3 (the lazy import was
# from the WRONG module; every POST 500-ed on ImportError at HEAD
# d8bf8b43e).
# ──────────────────────────────────────────────────────────────────────────


class TestAuthContextMissingErrorImportHome:
    """The class lives in the SERVICE layer — never the repo."""

    def test_defining_module_exports_it(self):
        from daemon.services.prompt_version_service import (  # noqa: F401
            AuthContextMissingError,
        )

    def test_repo_module_does_not_re_export_it(self):
        """Importing it from the repository module MUST fail — that
        was the pre-fix ImportError (a re-export added later would
        silently re-legitimize the wrong import path)."""
        with pytest.raises(ImportError):
            from daemon.repositories.prompt_version.repository import (  # noqa: F401
                AuthContextMissingError,
            )

    def test_router_source_pins_the_defining_module(self):
        """The router's lazy import names the service module — a
        source-scan pin so the regression cannot re-land silently."""
        from pathlib import Path

        src = Path(daemon_routers_custom_agents.__file__).read_text()
        assert (
            "from daemon.services.prompt_version_service import" in src
        ), "custom_agents.py must lazily import AuthContextMissingError from the service module"
        # The WRONG import shape must be absent: no AuthContextMissingError
        # import from the repository module anywhere in the file.
        for line in src.splitlines():
            stripped = line.strip()
            if "AuthContextMissingError" in stripped and stripped.startswith("from"):
                assert "prompt_version_service" in stripped, (
                    f"AuthContextMissingError imported from a non-defining "
                    f"module: {stripped}"
                )

    def test_actor_helper_raises_valueerror_not_importerror(self):
        """Calling ``_actor_from_request`` on a context-less request
        exercises BOTH lazy imports; at pre-fix HEAD this raised
        ImportError (500 on every POST). Now the repo helper's
        plain ``ValueError`` (no auth_context) propagates — which
        proves the imports resolved and documents the semantics:
        on THIS router the 503 gate fires first, so this branch is
        reachable only in direct helper misuse."""
        from starlette.requests import Request

        from daemon.routers.custom_agents import _actor_from_request

        req = Request(
            {"type": "http", "method": "GET", "path": "/", "headers": []}
        )
        with pytest.raises(ValueError) as ei:
            _actor_from_request(req)
        assert not isinstance(ei.value, ImportError)
