"""H4 — POST /api/prompts/selections on a non-approved target.

Pins the H4 fix-cycle finding: ``PromptVersionRepository.set_selection``
must raise the TYPED :class:`InvalidTransitionError` when the target
version is not in state ``approved``, so the router maps to 409
``INVALID_TRANSITION`` instead of a bare 500 (the OLD ``ValueError``
fell through the router's ``except _InvalidTransitionError`` and
bubbled as 500).

Coverage:

* Real-repo: ``set_selection`` on a ``pending_approval`` row →
  ``InvalidTransitionError`` (NOT a generic ``ValueError``).
* Real-repo: ``set_selection`` on a ``draft`` row → same typed error.
* Router: the same target via the ``/api/prompts/selections`` POST →
  409 ``INVALID_TRANSITION`` (the H4 contract from the client side).
* Regression: the existing 200 happy-path on an ``approved`` row still
  works (the H4 fix did NOT change the success path).

Fence discipline (2026-10-12 critical note): source
``.smoke/test-fence.sh`` before any invocation; ``FAKE_HOME``
mktemp + ``ENSEMBLE_SELF_ENV=dev``; SQLite file engine per the
canonical pack convention (``tests/test_prompt_version_resolution.py``).
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from daemon.repositories.prompt_version.models import (
    PromptAuditEvent,
    PromptVersion,
)
from daemon.repositories.prompt_version.repository import (
    AUTH_METHOD_INTERNAL,
    ActorContext,
    InvalidTransitionError,
    PromptVersionRepository,
)


# ────────────────────────────────────────────────────────────────────
# Fence — mirrors the canonical pack convention.
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
            f"refuse to point H4 tests at a non-dev DB.",
            returncode=3,
        )
    yield


# ────────────────────────────────────────────────────────────────────
# Engine fixtures — file SQLite with the schema created in-memory
# (mirrors the Phase A canonical pattern at
# ``tests/test_prompt_version_resolution.py``).
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path}/h4_selection.db",
        future=True,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine):
    return PromptVersionRepository(engine=engine)


def _internal_actor() -> ActorContext:
    return ActorContext(
        subject="h4-test",
        auth_method=AUTH_METHOD_INTERNAL,
        instance_id="instance-h4-test",
        agent_id="h4-test-agent",
        version_tag="",
    )


def _make_pending(repo: PromptVersionRepository, agent_id: str, artifact: str) -> Any:
    """Create a row in state ``pending_approval`` (the H4 target)."""
    return repo.create_and_submit(
        agent_id=agent_id,
        version_tag="",
        artifact=artifact,
        content_or_mutator=f"# {artifact} pending content",
        actor=_internal_actor(),
    )


def _make_approved(repo: PromptVersionRepository, agent_id: str, artifact: str) -> Any:
    """Create + approve a row in state ``approved`` (the happy path)."""
    v = repo.create_and_submit(
        agent_id=agent_id,
        version_tag="",
        artifact=artifact,
        content_or_mutator=f"# {artifact} approved content",
        actor=_internal_actor(),
    )
    return repo.approve(
        version_id=v.version_id,
        actor=ActorContext(
            subject="reviewer",
            auth_method="session",
            instance_id="reviewer-inst",
            agent_id="reviewer-agent",
            version_tag="",
        ),
    )


# ────────────────────────────────────────────────────────────────────
# H4 — repo-level: set_selection on a non-approved target raises
# the typed InvalidTransitionError (the OLD code raised a bare
# ``ValueError`` which the router did not catch → 500).
# ────────────────────────────────────────────────────────────────────


class TestRepoSetSelectionInvalidTransitionTyped:
    """The repo raises ``InvalidTransitionError`` directly on a
    non-approved selection target. The H4 fix moved away from a
    generic ``ValueError`` (which slipped through the router's
    ``except _InvalidTransitionError`` and surfaced as a 500) to the
    typed exception that the router maps to 409 INVALID_TRANSITION."""

    def test_pending_approval_target_raises_invalid_transition(self, repo):
        agent_id = f"dev-{uuid.uuid4().hex[:6]}"
        v = _make_pending(repo, agent_id, "soul")
        assert v.state == "pending_approval"

        with pytest.raises(InvalidTransitionError) as ei:
            repo.set_selection(
                agent_id=agent_id,
                version_tag="",
                artifact="soul",
                version_id=v.version_id,
                actor=_internal_actor(),
            )
        # The typed error carries the offending state.
        assert ei.value.from_state == "pending_approval"
        assert ei.value.to_state == "approved"
        assert ei.value.version_id == v.version_id

    def test_draft_target_raises_invalid_transition(self, repo):
        agent_id = f"dev-{uuid.uuid4().hex[:6]}"
        v = repo.create_draft(
            agent_id=agent_id,
            version_tag="",
            artifact="soul",
            content="# soul draft",
            actor=_internal_actor(),
        )
        assert v.state == "draft"

        with pytest.raises(InvalidTransitionError) as ei:
            repo.set_selection(
                agent_id=agent_id,
                version_tag="",
                artifact="soul",
                version_id=v.version_id,
                actor=_internal_actor(),
            )
        assert ei.value.from_state == "draft"
        assert ei.value.to_state == "approved"

    def test_pending_target_not_a_bare_value_error(self, repo):
        """The OLD code raised a bare ``ValueError`` — assert the
        H4 fix is NOT a ValueError but specifically a
        ``InvalidTransitionError``. The router's ``except
        _InvalidTransitionError`` catches ONLY the typed error."""
        agent_id = f"dev-{uuid.uuid4().hex[:6]}"
        v = _make_pending(repo, agent_id, "soul")

        with pytest.raises(Exception) as ei:
            repo.set_selection(
                agent_id=agent_id,
                version_tag="",
                artifact="soul",
                version_id=v.version_id,
                actor=_internal_actor(),
            )
        # Must be the typed error, not a generic ValueError.
        assert isinstance(ei.value, InvalidTransitionError), (
            f"H4 regression: expected InvalidTransitionError, got "
            f"{type(ei.value).__name__}"
        )


# ────────────────────────────────────────────────────────────────────
# H4 — repo-level: the success path (approved target) is unchanged.
# ────────────────────────────────────────────────────────────────────


class TestRepoSetSelectionHappyPathUnchanged:
    """The H4 fix is surgical: only the error path changed. The
    approved-target happy path MUST keep returning successfully."""

    def test_approved_target_succeeds(self, repo):
        agent_id = f"dev-{uuid.uuid4().hex[:6]}"
        v = _make_approved(repo, agent_id, "soul")
        assert v.state == "approved"

        # Must NOT raise.
        repo.set_selection(
            agent_id=agent_id,
            version_tag="",
            artifact="soul",
            version_id=v.version_id,
            actor=_internal_actor(),
        )

        # The selection pointer is now on this row.
        sel = repo.get_selection(agent_id, "", "soul")
        assert sel is not None
        assert sel.version_id == v.version_id


# ────────────────────────────────────────────────────────────────────
# H4 — router-level: the typed exception maps to 409 INVALID_TRANSITION.
#
# We exercise this via the real prompts router + a real
# :class:`PromptVersionRepository` so the selection flow reaches the
# H4-typed raise path end-to-end (the existing
# ``tests/test_prompts_router.py::TestSelections`` tests stub the
# service and never trip the repo path).
# ────────────────────────────────────────────────────────────────────


class TestRouterSetSelection409InvalidTransition:
    """End-to-end: a ``pending_approval`` target bubbles from the repo
    through the service to the router as a 409 ``INVALID_TRANSITION``
    (NOT a 500)."""

    @pytest.fixture
    def app_with_real_service(self, engine, tmp_path):
        """Build a minimal FastAPI app that mounts the prompts router
        and wires a real :class:`PromptVersionRepository` (and the
        real :class:`PromptVersionService` facade) onto
        ``app.state.manager``. Auth posture is ON — the inline
        ``_require_auth_on`` gate passes; the scope middleware
        stamps a session grant carrying the full taxonomy."""
        from fastapi import APIRouter, FastAPI
        from starlette.middleware.base import BaseHTTPMiddleware

        from daemon.auth.context import AUTH_METHOD_SESSION, AuthContext
        from daemon.auth.scopes import SCOPE_TAXONOMY
        from daemon.routers.prompts import router as prompts_router
        from daemon.services.prompt_version_service import (
            PromptVersionService,
        )

        # Build the real service against the same SQLite engine.
        repo = PromptVersionRepository(engine=engine)
        svc = PromptVersionService(repo=repo)

        app = FastAPI()
        api = APIRouter(prefix="/api")
        api.include_router(prompts_router)
        app.include_router(api)
        app.state.auth_config = object()
        app.state.manager = MagicMockManager(svc=svc)
        # Auth-on-starheadless middleware.
        class _AuthOn(BaseHTTPMiddleware):
            def __init__(self, app):
                super().__init__(app)
            async def dispatch(self, request, call_next):
                request.state.auth_context = AuthContext(
                    subject="h4-test",
                    scopes=set(SCOPE_TAXONOMY),
                    auth_method=AUTH_METHOD_SESSION,
                    expires_at="2099-01-01T00:00:00+00:00",
                    meta={},
                )
                return await call_next(request)
        app.add_middleware(_AuthOn)
        return app

    def test_router_returns_409_on_pending_approval_target(
        self, app_with_real_service, engine
    ):
        """End-to-end through the router: 409 INVALID_TRANSITION."""
        from fastapi.testclient import TestClient

        repo = PromptVersionRepository(engine=engine)
        agent_id = f"dev-{uuid.uuid4().hex[:6]}"
        v = _make_pending(repo, agent_id, "soul")
        assert v.state == "pending_approval"

        client = TestClient(app_with_real_service, raise_server_exceptions=False)
        resp = client.post(
            "/api/prompts/selections",
            json={
                "agent_id": agent_id,
                "version_tag": "",
                "artifact": "soul",
                "version_id": v.version_id,
            },
        )
        assert resp.status_code == 409, (
            f"H4 regression: expected 409, got {resp.status_code}: "
            f"{resp.text}"
        )
        body = resp.json()
        detail = body.get("detail", body)
        assert detail["error"] == "INVALID_TRANSITION", (
            f"expected error=INVALID_TRANSITION, got {detail}"
        )
        assert "approved" in detail["message"].lower()

    def test_router_returns_200_on_approved_target(
        self, app_with_real_service, engine
    ):
        """Regression: the H4 fix MUST NOT change the approved-target
        happy path."""
        from fastapi.testclient import TestClient

        repo = PromptVersionRepository(engine=engine)
        agent_id = f"dev-{uuid.uuid4().hex[:6]}"
        v = _make_approved(repo, agent_id, "soul")
        assert v.state == "approved"

        client = TestClient(app_with_real_service, raise_server_exceptions=False)
        resp = client.post(
            "/api/prompts/selections",
            json={
                "agent_id": agent_id,
                "version_tag": "",
                "artifact": "soul",
                "version_id": v.version_id,
            },
        )
        assert resp.status_code == 200, (
            f"H4 regression: approved-target happy path broke, got "
            f"{resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["version_id"] == v.version_id


# ────────────────────────────────────────────────────────────────────
# Helper: a MagicMock-shaped manager carrying the real service on the
# attribute the prompts router reads.
# ────────────────────────────────────────────────────────────────────


class MagicMockManager:
    """Minimal manager-shape stand-in: only carries
    ``_prompt_version_service`` (the attribute the prompts router
    reads via ``_get_service``). Avoids ``MagicMock`` so the real
    service methods execute end-to-end (the H4 finding)."""

    def __init__(self, svc):
        self._prompt_version_service = svc