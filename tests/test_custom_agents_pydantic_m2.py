"""M2 + M17 — custom_agents POST body Pydantic validation tests.

Pins the M2 / M17 fix-cycle finding: the POST ``/api/custom-agents``
handler accepts a ``_CustomAgentCreate`` Pydantic model (not
``dict[str, Any]``), so:

* Required-field violations surface as 422 with a structured error
  envelope (Pydantic's framework-default response), NOT as a 500
  from ``int("yes")`` / ``float("xyz")`` (the M17 finding).
* ``user_name`` rejects values outside ``[A-Za-z0-9_-]+`` and empty
  strings with a 422 (Pydantic ``pattern`` + ``min_length``).
* ``name`` rejects an empty string with a 422.
* ``soul_md`` rejects non-string / missing values with a 422.

The OLD ``dict[str, Any]`` handler did ``int(body.get(...))`` /
``float(body.get(...))`` which crashed with a 500 on any un-coercible
input. The Pydantic model closes that hole — framework-level coercion
returns 422 for non-numeric strings to numeric fields.

Coverage:

* Missing required fields (one-by-one) → 422.
* Empty ``user_name`` / ``name`` / ``soul_md`` → 422 (min_length).
* ``user_name`` with forbidden characters → 422 (pattern).
* Valid full body → 200 / 201 via the stubbed manager.
* Numeric fields accepting strings that coerce (Pydantic strict-coerce
  is OFF by default — the field accepts ``"42"`` for an int field).

Fence discipline: source ``.smoke/test-fence.sh`` before any invocation;
``FAKE_HOME`` mktemp + ``ENSEMBLE_SELF_ENV=dev``.
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
# Fence
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
            f"refuse to point M2 tests at a non-dev DB.",
            returncode=3,
        )
    yield


# ────────────────────────────────────────────────────────────────────
# Auth-ON middleware — same pattern as the H3 pack.
# ────────────────────────────────────────────────────────────────────


class _AuthInjectionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request.state.auth_context = AuthContext(
            subject="m2-test",
            scopes=set(SCOPE_TAXONOMY),
            auth_method=AUTH_METHOD_SESSION,
            expires_at="2099-01-01T00:00:00+00:00",
            meta={},
        )
        return await call_next(request)


@pytest.fixture
def app():
    a = FastAPI()
    api = APIRouter(prefix="/api")
    api.include_router(custom_agents_router)
    a.include_router(api)
    a.state.auth_config = object()
    a.state.manager = MagicMock()
    a.state.manager._custom_agent_lifecycle_service = MagicMock()
    a.state.manager._custom_agent_lifecycle_service.create_custom_agent = AsyncMock(
        return_value={
            "agent_id": "custom:ok",
            "name": "OK",
            "config_id": "uuid-1",
            "v1_versions": [{"artifact": "soul", "version_id": "v-1"}],
            "created_at": "2026-10-14T00:00:00+00:00",
        }
    )
    a.add_middleware(_AuthInjectionMiddleware)
    return a


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


# ────────────────────────────────────────────────────────────────────
# Required-field rejection — 422 from Pydantic
# ────────────────────────────────────────────────────────────────────


class TestRequiredFieldRejection:
    """Each required field — when missing or empty — surfaces as a
    422 with Pydantic's structured error envelope (NOT a 500 from
    the OLD ``dict[str, Any]`` handler)."""

    def test_missing_user_name_returns_422(self, client):
        resp = client.post(
            "/api/custom-agents",
            json={"name": "Foo", "soul_md": "# s"},
        )
        assert resp.status_code == 422, (
            f"M2 regression: expected 422 on missing user_name, got "
            f"{resp.status_code}: {resp.text}"
        )

    def test_missing_soul_md_returns_422(self, client):
        resp = client.post(
            "/api/custom-agents",
            json={"user_name": "ok", "name": "Foo"},
        )
        assert resp.status_code == 422, resp.text

    def test_missing_name_returns_422(self, client):
        resp = client.post(
            "/api/custom-agents",
            json={"user_name": "ok", "soul_md": "# s"},
        )
        assert resp.status_code == 422, resp.text

    def test_empty_user_name_returns_422(self, client):
        """``min_length=1`` rejects empty string."""
        resp = client.post(
            "/api/custom-agents",
            json={"user_name": "", "name": "Foo", "soul_md": "# s"},
        )
        assert resp.status_code == 422, resp.text

    def test_empty_name_returns_422(self, client):
        resp = client.post(
            "/api/custom-agents",
            json={"user_name": "ok", "name": "", "soul_md": "# s"},
        )
        assert resp.status_code == 422, resp.text

    def test_empty_soul_md_returns_422(self, client):
        resp = client.post(
            "/api/custom-agents",
            json={"user_name": "ok", "name": "Foo", "soul_md": ""},
        )
        assert resp.status_code == 422, resp.text

    def test_user_name_with_invalid_chars_returns_422(self, client):
        """The ``pattern=r"^[A-Za-z0-9_-]+$"`` rejects anything
        outside the wire-id alphabet."""
        resp = client.post(
            "/api/custom-agents",
            json={
                "user_name": "bad chars!",
                "name": "Foo",
                "soul_md": "# s",
            },
        )
        assert resp.status_code == 422, resp.text


# ────────────────────────────────────────────────────────────────────
# Numeric coercion — M17 close-out
#
# The OLD handler did ``int(body.get("skill_injection", 0) or 0)``
# which raised ``ValueError: invalid literal for int() with base 10:
# 'yes'`` → 500. The Pydantic model coerces strings to int when
# the value is a valid numeric; un-coercible strings raise 422.
# ────────────────────────────────────────────────────────────────────


class TestNumericCoercionM17:
    """M17: un-coercible strings to numeric fields → 422 (NOT 500)."""

    def test_uncoercible_int_field_returns_422(self, client):
        resp = client.post(
            "/api/custom-agents",
            json={
                "user_name": "ok",
                "name": "Foo",
                "soul_md": "# s",
                "skill_injection": "yes",
            },
        )
        assert resp.status_code == 422, (
            f"M17 regression: expected 422 on 'yes' for int, got "
            f"{resp.status_code}: {resp.text}"
        )

    def test_uncoercible_float_field_returns_422(self, client):
        resp = client.post(
            "/api/custom-agents",
            json={
                "user_name": "ok",
                "name": "Foo",
                "soul_md": "# s",
                "recursion_limit_multiplier": "xyz",
            },
        )
        assert resp.status_code == 422, (
            f"M17 regression: expected 422 on 'xyz' for float, got "
            f"{resp.status_code}: {resp.text}"
        )

    def test_coercible_int_field_accepted(self, client):
        """A numeric string is accepted by Pydantic (strict-coerce
        is NOT on by default; ``Field`` accepts ``"42"`` for an
        ``int``-annotated field)."""
        resp = client.post(
            "/api/custom-agents",
            json={
                "user_name": "ok",
                "name": "Foo",
                "soul_md": "# s",
                "skill_injection": "42",
            },
        )
        assert resp.status_code == 201, (
            f"M2 regression: numeric-string int field rejected: "
            f"{resp.status_code}: {resp.text}"
        )

    def test_coercible_float_field_accepted(self, client):
        resp = client.post(
            "/api/custom-agents",
            json={
                "user_name": "ok",
                "name": "Foo",
                "soul_md": "# s",
                "recursion_limit_multiplier": "1.5",
            },
        )
        assert resp.status_code == 201, (
            f"M2 regression: numeric-string float field rejected: "
            f"{resp.status_code}: {resp.text}"
        )


# ────────────────────────────────────────────────────────────────────
# Happy-path — the handler reaches the lifecycle service with the
# expected keyword-arg translation.
# ────────────────────────────────────────────────────────────────────


class TestHappyPath:
    """A valid full body reaches the lifecycle service and returns
    the 201 envelope."""

    def test_valid_full_body_returns_201(self, client):
        resp = client.post(
            "/api/custom-agents",
            json={
                "user_name": "ok",
                "name": "OK",
                "soul_md": "# Soul",
                "rule_md": "Be concise.",
                "description": "An M2 happy-path agent",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["agent_id"] == "custom:ok"
        # Lifecycle service received the kwarg translation.
        svc = client.app.state.manager._custom_agent_lifecycle_service
        kwargs = svc.create_custom_agent.await_args.kwargs
        assert kwargs["user_name"] == "ok"
        assert kwargs["name"] == "OK"
        assert kwargs["soul_md"] == "# Soul"
        assert kwargs["rule_md"] == "Be concise."
        assert kwargs["description"] == "An M2 happy-path agent"

    def test_optional_fields_default(self, client):
        """Optional fields absent in the body default to the
        service's own defaults (description="" and the icon/color
        fallbacks happen service-side)."""
        resp = client.post(
            "/api/custom-agents",
            json={
                "user_name": "ok",
                "name": "OK",
                "soul_md": "# s",
            },
        )
        assert resp.status_code == 201, resp.text
        svc = client.app.state.manager._custom_agent_lifecycle_service
        kwargs = svc.create_custom_agent.await_args.kwargs
        # description absent → router passes empty string.
        assert kwargs["description"] == ""
        # icon absent → router passes the 🤖 default.
        assert kwargs["icon"] == "🤖"
        # color absent → router passes the "accent-blue" default.
        assert kwargs["color"] == "accent-blue"