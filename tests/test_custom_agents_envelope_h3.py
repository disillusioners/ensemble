"""H3 — custom_agents error envelope {error,message} contract test pack.

Pins the H3 fix-cycle's promise in ``daemon/routers/custom_agents.py``
module docstring §1 API shape — the body of every error response MUST
be exactly ``{"error": <token>, "message": <str>}`` regardless of
status code (400 / 404 / 409 / 503). The router switched the OLD
``HTTPException(detail=ErrorResponse(...).model_dump())`` shape to the
``_err(status, code, message)`` helper that the prompts family uses;
this pack asserts the surface contract from the client side.

Coverage:

* 503 — service-unavailable (manager missing → manager wired, lifecycle
  service still missing → 503 ``INTERNAL_ERROR``)
* 404 — ``CUSTOM_AGENT_NOT_FOUND`` token on GET-single / DELETE when
  no live row matches the wire id.
* 400 — ``INVALID_REQUEST`` token on user-supplied body validation
  failures (the H3 shape contract; the M2 Pydantic pack owns the
  422 path).
* 409 — ``INVALID_REQUEST`` token on wire-id collision via the
  H5 typed discrimination.

Auth posture: the F1 directive keeps the inline ``_require_auth_on``
503 gate; auth must be ON for these tests to exercise the
router-side ``_err`` paths (the auth-OFF pack lives in
``test_custom_agents_router_authz.py``). The auth middleware is
borrowed verbatim from ``test_prompts_router.py`` (the F1 finding
§6.3 ledger).

Fence discipline (2026-10-12 critical note): source
``.smoke/test-fence.sh`` before any invocation; ``FAKE_HOME``
mktemp + ``ENSEMBLE_SELF_ENV=dev``; no HOME-derived or install-dir
path ever resolves here.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from daemon.auth.context import AUTH_METHOD_SESSION, AuthContext
from daemon.auth.scopes import SCOPE_TAXONOMY
from daemon.routers.custom_agents import router as custom_agents_router


# ────────────────────────────────────────────────────────────────────
# Fence — mirrors test_custom_agents_router_authz.py convention.
# ────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def _fence_session():
    allowed_dbs = {"ensemble_dev_test_only", "ensemble_test_only"}
    os.environ["ENSEMBLE_SELF_ENV"] = "dev"
    if os.environ.get("POSTGRES_DB") in (None, "", "ensemble_prod"):
        os.environ["POSTGRES_DB"] = "ensemble_dev_test_only"
    elif os.environ.get("POSTGRES_DB") not in allowed_dbs:
        pytest.exit(
            f"HARD ABORT: this test session targets "
            f"POSTGRES_DB={os.environ.get('POSTGRES_DB')!r}; "
            f"refuse to point custom-agents H3 tests at a non-dev DB.",
            returncode=3,
        )
    yield


# ────────────────────────────────────────────────────────────────────
# Auth-ON middleware — borrowed verbatim from test_prompts_router.py
# (the F1 finding §6.3 ledger; the prompts family is the canonical
# pattern the custom-agents F1 directive mirrors).
# ────────────────────────────────────────────────────────────────────


class _AuthInjectionMiddleware(BaseHTTPMiddleware):
    """Stamp ``request.state.auth_context`` for every request.

    The middleware reads a header ``X-Test-Role`` to switch lanes:
    ``session`` (default) carries the FULL operator family
    (``prompts:write`` + ``prompts:read`` + the rest of the
    taxonomy), so the ``@require_scope`` decorator on the
    custom-agents routes passes.
    """

    def __init__(self, app, *, role: str = "session") -> None:
        super().__init__(app)
        self._role = role

    async def dispatch(self, request, call_next):
        role_hdr = request.headers.get("X-Test-Role", self._role)
        ctx = AuthContext(
            subject="operator-cli",
            scopes=set(SCOPE_TAXONOMY),
            auth_method=AUTH_METHOD_SESSION,
            expires_at="2099-01-01T00:00:00+00:00",
            meta={},
        )
        request.state.auth_context = ctx
        return await call_next(request)


# ────────────────────────────────────────────────────────────────────
# App fixtures — auth ON, lifecycle service present (or missing,
# depending on the test).
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def auth_on_app():
    app = FastAPI()
    api = APIRouter(prefix="/api")
    api.include_router(custom_agents_router)
    app.include_router(api)
    # Auth ON — the F1 503 gate passes.
    app.state.auth_config = object()
    app.state.manager = MagicMock()
    # Default: lifecycle service present.
    app.state.manager._custom_agent_lifecycle_service = MagicMock()
    app.add_middleware(_AuthInjectionMiddleware, role="session")
    return app


@pytest.fixture
def client(auth_on_app):
    return TestClient(auth_on_app, raise_server_exceptions=False)


# ────────────────────────────────────────────────────────────────────
# Envelope contract — EVERY error body has EXACTLY these two keys
# with the expected types. Mirrors the prompts-family shape the
# router docstring promises.
# ────────────────────────────────────────────────────────────────────


def _assert_envelope(
    resp: Any,
    *,
    status: int,
    error: str,
    message_in: bool = False,
) -> dict[str, Any]:
    """Assert the response body matches the ``{error, message}`` shape.

    Args:
        resp: The TestClient response.
        status: Expected HTTP status code.
        error: Expected ``error`` token (e.g. ``"CUSTOM_AGENT_NOT_FOUND"``).
        message_in: When ``True``, only assert the ``message`` key is a
            non-empty string (the exact text is implementation-detail).

    Returns:
        The detail dict for any post-assert inspection.
    """
    assert resp.status_code == status, (
        f"expected {status}, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    detail = body.get("detail", body)
    assert isinstance(detail, dict), (
        f"error envelope must be a dict; got {detail!r}"
    )
    assert set(detail.keys()) == {"error", "message"}, (
        f"envelope must have EXACTLY {{error, message}}; "
        f"got keys={sorted(detail.keys())}"
    )
    assert detail["error"] == error, (
        f"error code token expected {error!r}, got {detail['error']!r}"
    )
    assert isinstance(detail["message"], str), (
        f"message must be a string; got {type(detail['message']).__name__}"
    )
    if message_in:
        assert detail["message"], (
            f"message must be a non-empty string; got {detail['message']!r}"
        )
    return detail


# ────────────────────────────────────────────────────────────────────
# 503 INTERNAL_ERROR — service-unavailable (manager / lifecycle
# service not wired).
# ────────────────────────────────────────────────────────────────────


class Test503InternalErrorEnvelope:
    """503 service-unavailable path → ``INTERNAL_ERROR`` envelope.

    Note: the ``manager is None`` branch of ``_lifecycle_service``
    is masked by the conftest's ``_ensure_app_state_manager``
    autouse fixture, which substitutes a ``MagicMock`` for any
    absent or ``None`` ``app.state.manager``. That branch is
    therefore only reachable in production; the
    envelope-shape contract is pinned via the
    ``_custom_agent_lifecycle_service missing`` branch below.
    """

    def test_503_when_lifecycle_service_missing(self):
        """Manager is wired but ``_custom_agent_lifecycle_service``
        is missing — the second 503 branch."""
        app = FastAPI()
        api = APIRouter(prefix="/api")
        api.include_router(custom_agents_router)
        app.include_router(api)
        app.state.auth_config = object()
        app.state.manager = MagicMock(spec=[])  # no lifecycle svc attr
        app.add_middleware(_AuthInjectionMiddleware, role="session")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/custom-agents")
        _assert_envelope(
            resp,
            status=503,
            error="INTERNAL_ERROR",
            message_in=True,
        )
        assert (
            "_custom_agent_lifecycle_service missing"
            in resp.json()["detail"]["message"]
        )


# ────────────────────────────────────────────────────────────────────
# 404 CUSTOM_AGENT_NOT_FOUND — GET-single / DELETE on no-live-row.
# ────────────────────────────────────────────────────────────────────


class Test404CustomAgentNotFound:
    """404 path on GET-single / DELETE — ``CUSTOM_AGENT_NOT_FOUND``."""

    def test_get_returns_404_envelope(self, client):
        svc = client.app.state.manager._custom_agent_lifecycle_service
        svc.get_by_agent_id = AsyncMock(return_value=None)
        resp = client.get("/api/custom-agents/missing-helper")
        _assert_envelope(
            resp,
            status=404,
            error="CUSTOM_AGENT_NOT_FOUND",
            message_in=True,
        )
        assert "missing-helper" in resp.json()["detail"]["message"]

    def test_delete_returns_404_envelope(self, client):
        svc = client.app.state.manager._custom_agent_lifecycle_service
        svc.soft_delete_custom_agent = AsyncMock(return_value=False)
        resp = client.delete("/api/custom-agents/missing-helper")
        _assert_envelope(
            resp,
            status=404,
            error="CUSTOM_AGENT_NOT_FOUND",
            message_in=True,
        )
        assert "missing-helper" in resp.json()["detail"]["message"]


# ────────────────────────────────────────────────────────────────────
# 409 INVALID_REQUEST — wire-id collision via H5 typed discrimination.
# ────────────────────────────────────────────────────────────────────


class Test409InvalidRequestCollision:
    """H5 typed discrimination: a duplicate ``agent_id`` raises
    ``CustomAgentCollisionError`` in the service → 409 envelope."""

    def test_post_collision_returns_409_envelope(self, client):
        from daemon.services.custom_agent_lifecycle_service import (
            CustomAgentCollisionError,
        )

        svc = client.app.state.manager._custom_agent_lifecycle_service
        svc.create_custom_agent = AsyncMock(
            side_effect=CustomAgentCollisionError(
                "custom agent 'custom:dup' already exists"
            )
        )
        resp = client.post(
            "/api/custom-agents",
            json={
                "user_name": "dup",
                "name": "Dup",
                "soul_md": "# soul",
            },
        )
        _assert_envelope(
            resp,
            status=409,
            error="INVALID_REQUEST",
            message_in=True,
        )
        assert "already exists" in resp.json()["detail"]["message"]


# ────────────────────────────────────────────────────────────────────
# 400 INVALID_REQUEST — typed creation error (H5: the substring
# sniff is gone; ``CustomAgentCreationError`` maps cleanly to 400).
# ────────────────────────────────────────────────────────────────────


class Test400InvalidRequestCreation:
    """H5 typed discrimination: an FK / NOT-NULL / wide-txn error
    surfaces as 400 ``INVALID_REQUEST``. The OLD substring sniff
    ('already exists' in msg.lower()) would have flipped a
    FK violation that happens to mention a duplicate into a 409;
    that path is RETIRED."""

    def test_post_creation_error_returns_400_envelope(self, client):
        from daemon.services.custom_agent_lifecycle_service import (
            CustomAgentCreationError,
        )

        svc = client.app.state.manager._custom_agent_lifecycle_service
        # The OLD substring sniff would have flipped this to a 409
        # because the message contains 'already exists'. The H5 fix
        # routes it to 400 instead — proves the sniff is gone.
        svc.create_custom_agent = AsyncMock(
            side_effect=CustomAgentCreationError(
                "creation failed: IntegrityError on FK already exists"
            )
        )
        resp = client.post(
            "/api/custom-agents",
            json={
                "user_name": "fk",
                "name": "FK",
                "soul_md": "# soul",
            },
        )
        _assert_envelope(
            resp,
            status=400,
            error="INVALID_REQUEST",
            message_in=True,
        )
        # The envelope MUST NOT contain an extra "error" or nested
        # ErrorResponse shape — exactly {error, message}.
        detail = resp.json()["detail"]
        assert list(detail.keys()) == ["error", "message"]