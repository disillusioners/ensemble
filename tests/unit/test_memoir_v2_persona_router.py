"""Unit tests for Memoir Store v2 Phase 5 — Persona Router (/api/memoir/persona).

Plan: .agents/shared/planning/ari-memoir-store/v2-phase5-plan.md (§3.4, §4 T5.5; §5 AC5, AC6).
Test Pack: test/packs/memoir_v2_persona_router_test.sh.

Coverage:
* Minimal REST surface (/api/memoir/persona):
  - GET /pending (prompts:read) -> returns pending versions only (status == 'pending_approval')
  - POST /{version_id}/approve (prompts:approve) -> human session approval, moves pointer
  - POST /{version_id}/reject (prompts:approve) -> transitions to rejected
* Human session auth requirement (403 HUMAN_LANE_REQUIRED):
  - Calling approve with bearer token (even with prompts:approve) -> 403
  - Calling reject with bearer token -> 403
* State transitions and error mapping:
  - Already approved / rejected -> 409 INVALID_TRANSITION
  - Version not found -> 404 VERSION_NOT_FOUND
  - Invalid path param -> 422 INVALID_PATH_PARAM
* Scope discipline:
  - Missing scope -> 403
  - Zero create/edit/delete endpoints exist
* Auth-OFF posture:
  - 503 AUTH_NOT_CONFIGURED when auth is off
* Hermetic auth environment:
  - Ambient ENSEMBLE_AUTH_* scrubbed via autouse fixture per project POK
* The PRODUCTION repository-resolution branch (F3):
  - the manager exposes the ENGINE only -> the engine fallback is used
* AC6 Mutation leg (c):
  - Approving via bearer token flips AC5 RED.
"""

from __future__ import annotations

import os
import types
from unittest.mock import MagicMock
import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select
from starlette.middleware.base import BaseHTTPMiddleware

from daemon.auth.context import (
    AUTH_METHOD_BEARER,
    AUTH_METHOD_SESSION,
    AuthContext,
)
from daemon.repositories.memoir.models import MemoirPersona, MemoirPersonaVersion
from daemon.repositories.memoir.repository import MemoirPersonaRepository
from daemon.routers.memoir import router as memoir_router
from tests.helpers.auth_fixture import on_auth_config


@pytest.fixture(autouse=True)
def hermetic_auth_env():
    """Autouse fixture to scrub ambient ENSEMBLE_AUTH_* per project POK."""
    scrubbed = {}
    for key in list(os.environ):
        if key.startswith("ENSEMBLE_AUTH_"):
            scrubbed[key] = os.environ.pop(key)
    yield
    for key, val in scrubbed.items():
        os.environ[key] = val


class _AuthInjectionMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, role: str = "session"):
        super().__init__(app)
        self._role = role

    async def dispatch(self, request, call_next):
        role_hdr = request.headers.get("X-Test-Role", self._role)
        if role_hdr == "session":
            ctx = AuthContext(
                subject="operator-human",
                scopes={"prompts:read", "prompts:approve"},
                auth_method=AUTH_METHOD_SESSION,
                expires_at="2099-01-01T00:00:00+00:00",
                meta={},
            )
        elif role_hdr.startswith("bearer:"):
            granted = set(s for s in role_hdr[len("bearer:") :].split(",") if s)
            ctx = AuthContext(
                subject="machine-token",
                scopes=granted,
                auth_method=AUTH_METHOD_BEARER,
                expires_at="2099-01-01T00:00:00+00:00",
                meta={},
            )
        elif role_hdr == "no_scope":
            ctx = AuthContext(
                subject="unprivileged-session",
                scopes=set(),
                auth_method=AUTH_METHOD_SESSION,
                expires_at="2099-01-01T00:00:00+00:00",
                meta={},
            )
        else:
            ctx = None

        if ctx is not None:
            request.state.auth_context = ctx
        return await call_next(request)


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


class _ProductionManager:
    """THE PRODUCTION MANAGER SHAPE — it exposes the ENGINE only.

    NO production code ever sets ``manager._memoir_persona_repo``: the
    production branch of ``_get_persona_repo`` is the ENGINE FALLBACK
    (``daemon/routers/memoir.py:91-94``).  A ``MagicMock`` manager would
    answer ``hasattr(manager, "_memoir_persona_repo")`` with True (the
    mock auto-creates attributes) and would therefore exercise a branch
    production never takes.
    """

    def __init__(self, engine):
        self.engine = engine


class _StubRequest:
    """A minimal request stub for the repository-resolution seam."""

    def __init__(self, manager):
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(manager=manager)
        )


@pytest.fixture
def repo(db_engine):
    r = MemoirPersonaRepository(db_engine)
    actor = MagicMock()
    actor.auth_method = "session"
    # Seed v1 approved
    v1 = r.create_draft("ari", "# Persona v1", source="seed")
    r.submit(v1.id)
    r.approve(v1.id, actor)
    return r


@pytest.fixture
def app(db_engine, repo):
    application = FastAPI()
    api = APIRouter(prefix="/api")
    api.include_router(memoir_router)
    application.include_router(api)

    application.state.auth_config = on_auth_config()
    # THE PRODUCTION SHAPE — the engine fallback branch.
    application.state.manager = _ProductionManager(db_engine)

    application.add_middleware(_AuthInjectionMiddleware, role="session")
    return application


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


class TestMemoirPersonaRouter:
    """Minimal REST surface tests (AC5)."""

    def test_get_pending_returns_pending_versions_only(self, client, repo):
        # Create a pending draft v2
        d2 = repo.create_draft("ari", "# Persona v2", source="ari")
        repo.submit(d2.id)

        # Create another draft v3 that stays in 'draft'
        d3 = repo.create_draft("ari", "# Persona v3", source="ari")

        resp = client.get("/api/memoir/persona/pending", headers={"X-Test-Role": "session"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == d2.id
        assert data[0]["version"] == 2
        assert data[0]["source"] == "ari"
        assert data[0]["body"] == "# Persona v2"

    def test_approve_with_session_lane_succeeds_and_moves_pointer(self, client, repo):
        d2 = repo.create_draft("ari", "# Persona v2", source="ari")
        repo.submit(d2.id)

        resp = client.post(
            f"/api/memoir/persona/{d2.id}/approve",
            headers={"X-Test-Role": "session"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == d2.id
        assert data["status"] == "approved"
        assert data["version"] == 2

        # Check pointer row in DB
        current = repo.get_current("ari")
        assert current is not None
        assert current.id == d2.id
        assert current.version == 2

    def test_approve_with_bearer_token_returns_403_human_lane_required(self, client, repo):
        d2 = repo.create_draft("ari", "# Persona v2", source="ari")
        repo.submit(d2.id)

        # Bearer token carrying prompts:approve scope
        resp = client.post(
            f"/api/memoir/persona/{d2.id}/approve",
            headers={"X-Test-Role": "bearer:prompts:approve"},
        )
        assert resp.status_code == 403
        data = resp.json()
        assert data["detail"]["error"] == "HUMAN_LANE_REQUIRED"

        # Check that pointer was NOT moved
        current = repo.get_current("ari")
        assert current.version == 1

    def test_reject_with_session_lane_succeeds(self, client, repo):
        d2 = repo.create_draft("ari", "# Persona v2", source="ari")
        repo.submit(d2.id)

        resp = client.post(
            f"/api/memoir/persona/{d2.id}/reject",
            headers={"X-Test-Role": "session"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected"

        current = repo.get_current("ari")
        assert current.version == 1

    def test_reject_with_bearer_token_returns_403_human_lane_required(self, client, repo):
        d2 = repo.create_draft("ari", "# Persona v2", source="ari")
        repo.submit(d2.id)

        resp = client.post(
            f"/api/memoir/persona/{d2.id}/reject",
            headers={"X-Test-Role": "bearer:prompts:approve"},
        )
        assert resp.status_code == 403
        data = resp.json()
        assert data["detail"]["error"] == "HUMAN_LANE_REQUIRED"

    def test_approve_already_approved_version_returns_409(self, client, repo):
        d2 = repo.create_draft("ari", "# Persona v2", source="ari")
        repo.submit(d2.id)
        actor = MagicMock()
        actor.auth_method = "session"
        repo.approve(d2.id, actor)

        resp = client.post(
            f"/api/memoir/persona/{d2.id}/approve",
            headers={"X-Test-Role": "session"},
        )
        assert resp.status_code == 409
        data = resp.json()
        assert data["detail"]["error"] == "INVALID_TRANSITION"

    def test_approve_nonexistent_version_returns_404(self, client):
        resp = client.post(
            "/api/memoir/persona/nonexistent-id/approve",
            headers={"X-Test-Role": "session"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "VERSION_NOT_FOUND"

    def test_malformed_version_id_returns_422(self, client):
        resp = client.post(
            "/api/memoir/persona/bad!id$/approve",
            headers={"X-Test-Role": "session"},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PATH_PARAM"

    def test_missing_scope_returns_403(self, client, repo):
        d2 = repo.create_draft("ari", "# Persona v2", source="ari")
        repo.submit(d2.id)

        # Session with no scopes
        resp = client.get(
            "/api/memoir/persona/pending",
            headers={"X-Test-Role": "no_scope"},
        )
        assert resp.status_code == 403

    def test_auth_off_posture_returns_503(self, db_engine, repo):
        application = FastAPI()
        api = APIRouter(prefix="/api")
        api.include_router(memoir_router)
        application.include_router(api)

        # auth_config is None (auth OFF)
        application.state.auth_config = None
        # THE PRODUCTION SHAPE — the engine fallback branch.
        application.state.manager = _ProductionManager(db_engine)

        c = TestClient(application, raise_server_exceptions=False)
        resp = c.get("/api/memoir/persona/pending")
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "AUTH_NOT_CONFIGURED"

    def test_zero_create_edit_delete_endpoints_exist(self, app):
        """Strict scope discipline: exactly 3 endpoints exist under /api/memoir/persona."""
        routes = [
            r for r in app.routes
            if getattr(r, "path", "").startswith("/api/memoir/persona")
        ]
        assert len(routes) == 3
        paths_and_methods = {
            (r.path, tuple(sorted(m for m in r.methods if m != "HEAD")))
            for r in routes
        }
        assert paths_and_methods == {
            ("/api/memoir/persona/pending", ("GET",)),
            ("/api/memoir/persona/{version_id}/approve", ("POST",)),
            ("/api/memoir/persona/{version_id}/reject", ("POST",)),
        }


class TestProductionRepositoryResolution:
    """F3: the PRODUCTION branch — the ENGINE FALLBACK.

    ``daemon/routers/memoir.py:89-94`` prefers a
    ``manager._memoir_persona_repo`` attribute that NO production code
    ever sets; production resolves the repository from the manager's
    ENGINE.  These legs cover that branch directly.
    """

    def test_the_production_manager_resolves_the_repo_through_the_engine_fallback(
        self, db_engine
    ):
        from daemon.routers.memoir import _get_persona_repo

        manager = _ProductionManager(db_engine)
        # NO production code sets this attribute.
        assert not hasattr(manager, "_memoir_persona_repo")

        request = _StubRequest(manager)
        resolved = _get_persona_repo(request)
        assert isinstance(resolved, MemoirPersonaRepository)
        assert resolved.engine is db_engine

    def test_an_explicit_repo_attribute_is_still_honored(self, db_engine, repo):
        """The fake-attribute branch — an EXTRA case, never the only one."""
        from daemon.routers.memoir import _get_persona_repo

        manager = _ProductionManager(db_engine)
        manager._memoir_persona_repo = repo

        request = _StubRequest(manager)
        resolved = _get_persona_repo(request)
        assert resolved is repo

    def test_a_manager_without_an_engine_is_refused(self):
        from daemon.routers.memoir import _get_persona_repo

        manager = _ProductionManager(None)
        request = _StubRequest(manager)
        with pytest.raises(HTTPException) as excinfo:
            _get_persona_repo(request)
        assert excinfo.value.status_code == 503
        assert excinfo.value.detail["error"] == "MEMOIR_UNAVAILABLE"


class TestMutationLegC:
    """AC6 (c): Approving via bearer token flips AC5 RED."""

    def test_bearer_approval_is_refused(self, client, repo):
        d2 = repo.create_draft("ari", "# Persona v2", source="ari")
        repo.submit(d2.id)

        resp = client.post(
            f"/api/memoir/persona/{d2.id}/approve",
            headers={"X-Test-Role": "bearer:prompts:approve"},
        )
        # AC5 requires that bearer approval is refused with 403 HUMAN_LANE_REQUIRED
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "HUMAN_LANE_REQUIRED"
