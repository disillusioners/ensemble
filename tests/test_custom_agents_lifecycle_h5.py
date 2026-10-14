"""H5 — custom_agents IntegrityError typed-discrimination tests.

Pins the H5 fix-cycle finding: the lifecycle service discriminates
an ``IntegrityError`` into two typed exceptions, NO substring sniffing
at the router boundary.

* **Pre-SELECT race-free discrimination** — the service queries the
  ``custom_agent_configs`` table for the wire id BEFORE the INSERT;
  on a hit, raises :class:`CustomAgentCollisionError` and rolls back
  via ``engine.begin()``'s exit-on-exception. The router maps this
  to 409 INVALID_REQUEST.

* **Genuine IntegrityError → CreationError** — any remaining
  ``IntegrityError`` (FK violation, NOT-NULL, CHECK constraint,
  or the race window where another writer slipped between the
  pre-SELECT and the INSERT) surfaces as
  :class:`CustomAgentCreationError`. The router maps this to
  400 INVALID_REQUEST.

Coverage:

* Service-level: duplicate ``user_name`` → ``CustomAgentCollisionError`` (NOT
  a ``CustomAgentCreationError``); a forced ``IntegrityError`` (mock
  via a bad FK reference) → ``CustomAgentCreationError`` (NOT a
  ``CustomAgentCollisionError``).
* Router-level: the two typed errors surface as 409 and 400 envelopes
  with the correct ``error`` token (mirrors the H3 contract).
* Substring-sniff regression: the OLD code did
  ``if "already exists" in msg.lower()`` at the router — assert the
  substring in a ``CustomAgentCreationError`` message NO LONGER
  flips it to a 409. The 400 wins.
* ``CustomAgentNotFoundError`` deleted (M4): the dead class is
  no longer importable from the service.

Fence discipline (2026-10-12 critical note): source
``.smoke/test-fence.sh`` before any invocation; ``FAKE_HOME``
mktemp + ``ENSEMBLE_SELF_ENV=dev``; file-SQLite per the canonical
pack convention.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel

from daemon.auth.context import AUTH_METHOD_SESSION, AuthContext
from daemon.auth.scopes import SCOPE_TAXONOMY
from daemon.repositories.custom_agent_configs import (
    CustomAgentConfigRepository,
    make_custom_agent_id,
)
from daemon.repositories.prompt_version.repository import (
    PromptVersionRepository,
)
from daemon.services.custom_agent_lifecycle_service import (
    CustomAgentCollisionError,
    CustomAgentCreationError,
    CustomAgentLifecycleService,
)
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from daemon.routers.custom_agents import router as custom_agents_router


# ────────────────────────────────────────────────────────────────────
# Fence — mirrors test_custom_agent_lifecycle.py convention.
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
            f"refuse to point H5 tests at a non-dev DB.",
            returncode=3,
        )
    yield


# ────────────────────────────────────────────────────────────────────
# Engine + service fixtures — file-SQLite per the canonical pack.
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_repo_engine(tmp_path):
    db_path = tmp_path / "h5_test.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def lifecycle(tmp_repo_engine) -> CustomAgentLifecycleService:
    cfg_repo = CustomAgentConfigRepository(engine=tmp_repo_engine)
    pv_repo = PromptVersionRepository(engine=tmp_repo_engine)
    return CustomAgentLifecycleService(
        config_repo=cfg_repo,
        prompt_version_repo=pv_repo,
    )


def _http_actor() -> Any:
    return ActorContext(
        subject="h5-test@example.com",
        auth_method="session",
        instance_id="h5-inst",
        agent_id="h5-agent",
        version_tag="",
    )


# Local import for the actor — keeps the import surface tight.
from daemon.repositories.prompt_version.repository import ActorContext  # noqa: E402


# ────────────────────────────────────────────────────────────────────
# Service-level: duplicate user_name → CustomAgentCollisionError
# (NOT a CustomAgentCreationError; the OLD IntegrityError path
# would have raised CreationError, forcing the router to
# substring-sniff for "already exists" — that path is gone).
# ────────────────────────────────────────────────────────────────────


class TestServiceCollisionTyped:
    """Pre-SELECT discrimination: a duplicate user_name raises
    ``CustomAgentCollisionError`` BEFORE the INSERT commits, so
    the router maps cleanly to 409."""

    def test_duplicate_user_name_raises_collision_error(self, lifecycle):
        async def _run():
            await lifecycle.create_custom_agent(
                user_name="dup",
                name="Dup",
                actor=_http_actor(),
                soul_md="# soul",
            )

        asyncio.run(_run())

        # Second attempt raises CustomAgentCollisionError specifically.
        async def _run_again():
            await lifecycle.create_custom_agent(
                user_name="dup",
                name="Dup Again",
                actor=_http_actor(),
                soul_md="# soul 2",
            )

        with pytest.raises(CustomAgentCollisionError) as ei:
            asyncio.run(_run_again())
        assert "dup" in str(ei.value).lower()
        assert "already exists" in str(ei.value).lower()

    def test_collision_is_not_a_creation_error(self, lifecycle):
        """The H5 fix RETIRED the ``if "already exists" in msg.lower()``
        sniff. The new typed error is ``CustomAgentCollisionError`` —
        NOT a subclass of ``CustomAgentCreationError``. Subclass
        would mean the router's except order could re-route it via
        the old creation path."""
        assert not issubclass(
            CustomAgentCollisionError, CustomAgentCreationError
        ), (
            "CustomAgentCollisionError must NOT subclass "
            "CustomAgentCreationError — the router's typed-except "
            "order depends on the two being disjoint types."
        )


# ────────────────────────────────────────────────────────────────────
# Service-level: a NON-collision IntegrityError still raises
# ``CustomAgentCreationError`` (FK violation, NOT-NULL, CHECK
# constraint, race window). The OLD substring sniff would have
# flipped an FK violation that happens to mention 'already exists'
# into a 409 — that path is gone.
# ────────────────────────────────────────────────────────────────────


class TestServiceGenuineIntegrityErrorTyped:
    """A genuine ``IntegrityError`` (e.g. a bad FK reference) that
    reaches the ``except IntegrityError`` handler raises
    ``CustomAgentCreationError``. The OLD substring sniff would
    have routed it to a 409 if the message mentioned 'already ' —
    the H5 fix RETIRES that substring logic entirely."""

    def test_genuine_integrity_error_raises_creation_error(
        self, lifecycle, tmp_repo_engine, monkeypatch,
    ):
        """Force a non-collision ``IntegrityError`` by stubbing
        ``insert_config`` to raise one. The outer ``except IntegrityError``
        handler MUST raise ``CustomAgentCreationError`` — NOT a
        collision error, NOT a generic ``Exception``."""

        from daemon.repositories.custom_agent_configs import (
            CustomAgentConfigRepository,
        )

        original_insert = CustomAgentConfigRepository.insert_config

        def _raise_integrity_error(self, *args: Any, **kwargs: Any):
            raise IntegrityError(
                "INSERT INTO custom_agent_configs ...",
                params={},
                orig=Exception("FOREIGN KEY constraint failed"),
            )

        monkeypatch.setattr(
            CustomAgentConfigRepository,
            "insert_config",
            _raise_integrity_error,
        )

        async def _run():
            await lifecycle.create_custom_agent(
                user_name="fk",
                name="FK",
                actor=_http_actor(),
                soul_md="# soul",
            )

        with pytest.raises(CustomAgentCreationError) as ei:
            asyncio.run(_run())
        # The ``from e`` chain is preserved for forensics.
        assert isinstance(ei.value.__cause__, IntegrityError), (
            f"H5 regression: __cause__ must be IntegrityError "
            f"(forensic chain), got {type(ei.value.__cause__).__name__}"
        )

        # Restore for the rest of the test.
        monkeypatch.setattr(
            CustomAgentConfigRepository,
            "insert_config",
            original_insert,
        )

    def test_substring_sniff_regression_creation_with_already_exists_text(
        self, client_with_real_service
    ):
        """The OLD router code did
        ``if "already exists" in msg.lower()`` inside
        ``except CustomAgentCreationError`` and flipped to a 409.
        H5 retires that path: a creation error mentioning
        'already exists' is a CREATION error → 400, NOT 409.
        """
        app, lifecycle_svc = client_with_real_service
        svc = app.state.manager._custom_agent_lifecycle_service
        svc.create_custom_agent = AsyncMock(
            side_effect=CustomAgentCreationError(
                # The OLD sniff would have flipped this to a 409.
                "creation failed: an IntegrityError already exists on FK"
            )
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/custom-agents",
            json={
                "user_name": "sniff",
                "name": "Sniff",
                "soul_md": "# soul",
            },
        )
        # The envelope is 400, not 409 — the substring sniff is gone.
        assert resp.status_code == 400, (
            f"H5 regression: expected 400 (creation error), got "
            f"{resp.status_code}: {resp.text}"
        )
        body = resp.json()
        detail = body.get("detail", body)
        assert detail["error"] == "INVALID_REQUEST"
        assert "already exists" in detail["message"]


# ────────────────────────────────────────────────────────────────────
# Router-level: H3 envelope contract for the two typed exceptions.
# ────────────────────────────────────────────────────────────────────


class TestRouterEnvelopeFromTypedErrors:
    """End-to-end through the router: typed exceptions map to the
    documented status + token, and the substring sniff is RETIRED."""

    @pytest.fixture
    def client(self):
        """Bare HTTP client wired against a stubbed lifecycle service.
        Each test sets the service's ``create_custom_agent`` side
        effect independently."""
        app = FastAPI()
        api = APIRouter(prefix="/api")
        api.include_router(custom_agents_router)
        app.include_router(api)
        app.state.auth_config = object()
        app.state.manager = MagicMock()
        app.state.manager._custom_agent_lifecycle_service = MagicMock()
        app.add_middleware(_AuthInjectionMiddleware)
        return TestClient(app, raise_server_exceptions=False)

    def test_collision_envelope_409(self, client):
        client.app.state.manager._custom_agent_lifecycle_service.create_custom_agent = (
            AsyncMock(
                side_effect=CustomAgentCollisionError(
                    "custom agent 'custom:dup' already exists"
                )
            )
        )
        resp = client.post(
            "/api/custom-agents",
            json={"user_name": "dup", "name": "Dup", "soul_md": "# s"},
        )
        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "INVALID_REQUEST"
        assert "already exists" in detail["message"]

    def test_creation_envelope_400_not_flipped_by_substring(self, client):
        client.app.state.manager._custom_agent_lifecycle_service.create_custom_agent = (
            AsyncMock(
                side_effect=CustomAgentCreationError(
                    # Substring 'already exists' used to flip a 400→409.
                    # H5 retires that — must remain 400.
                    "FK reference: row already exists"
                )
            )
        )
        resp = client.post(
            "/api/custom-agents",
            json={"user_name": "fk", "name": "FK", "soul_md": "# s"},
        )
        assert resp.status_code == 400, (
            f"H5 regression: expected 400 (creation error), got "
            f"{resp.status_code}: {resp.text}"
        )
        detail = resp.json()["detail"]
        assert detail["error"] == "INVALID_REQUEST"


# ────────────────────────────────────────────────────────────────────
# M4 regression: the deleted dead class is gone.
# ────────────────────────────────────────────────────────────────────


class TestM4DeadClassRemoved:
    """M4 retired ``CustomAgentNotFoundError`` — the dead class
    was in ``__all__`` but never raised."""

    def test_dead_class_not_exported(self):
        """``CustomAgentNotFoundError`` is no longer importable
        from the lifecycle service module."""
        import daemon.services.custom_agent_lifecycle_service as mod
        assert not hasattr(mod, "CustomAgentNotFoundError"), (
            "M4 regression: CustomAgentNotFoundError was retired "
            "but is still importable from the service module."
        )
        assert "CustomAgentNotFoundError" not in mod.__all__


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


class _AuthInjectionMiddleware(BaseHTTPMiddleware):
    """Auth-ON middleware — stamps ``request.state.auth_context``
    with the full operator scope set so the ``@require_scope``
    decorators on the custom-agents routes pass."""

    async def dispatch(self, request, call_next):
        request.state.auth_context = AuthContext(
            subject="h5-test",
            scopes=set(SCOPE_TAXONOMY),
            auth_method=AUTH_METHOD_SESSION,
            expires_at="2099-01-01T00:00:00+00:00",
            meta={},
        )
        return await call_next(request)


@pytest.fixture
def client_with_real_service():
    """Bare client fixture used by the substring-sniff regression
    test. Returns the FastAPI app + the lifecycle service stub."""
    app = FastAPI()
    api = APIRouter(prefix="/api")
    api.include_router(custom_agents_router)
    app.include_router(api)
    app.state.auth_config = object()
    app.state.manager = MagicMock()
    app.state.manager._custom_agent_lifecycle_service = MagicMock()
    app.add_middleware(_AuthInjectionMiddleware)
    return app, app.state.manager._custom_agent_lifecycle_service