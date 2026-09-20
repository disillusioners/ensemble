"""Phase 3 / plane-integration-revival — HTTP endpoint integration tests.

Covers the ``POST /api/plane/sync/{project_id}`` contract:

* 200 — sync attempted; response body carries the resulting state.
* 404 — unknown project_id.
* 409 — re-entrancy guard (a sync is already in flight).
* 503 — integration disabled (PLANE_API_KEY absent).

The router is mounted on a minimal FastAPI app (not the full daemon
app) so the test runs without booting the daemon lifespan. The
endpoint exercises the full PlaneSyncService path; Plane HTTP calls
are mocked via the same AsyncMock surface the unit tests use.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from daemon.clients.plane_http_client import PlaneAPIError
from daemon.constants import (
    PLANE_PROJECT_ID_METADATA_KEY,
    PLANE_SYNC_STATE_ERROR,
    PLANE_SYNC_STATE_LINKED,
    PLANE_SYNC_STATE_METADATA_KEY,
    PLANE_SYNC_STATE_SYNCING,
)
from daemon.repositories import SQLModelProjectRepository
from daemon.repositories.project.models import (
    Project,
    ProjectMetadataRecord,
    ProjectShortnameLink,
)
from daemon.routers.plane import router as plane_router
from daemon.routers.plane import set_project_repository

pytestmark = pytest.mark.integration


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """In-memory SQLite engine."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _ = (Project, ProjectMetadataRecord, ProjectShortnameLink)
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine) -> SQLModelProjectRepository:
    return SQLModelProjectRepository(engine)


@pytest.fixture
def client(repo):
    """Build a FastAPI TestClient with the plane router mounted + repo wired."""
    app = FastAPI()
    app.include_router(plane_router, prefix="/api")
    set_project_repository(repo)
    return TestClient(app)


@pytest.fixture
def mock_plane_env(monkeypatch):
    monkeypatch.setenv("PLANE_BASE_URL", "https://plane.example.com")
    monkeypatch.setenv("PLANE_API_KEY", "test-api-key-xyz")
    monkeypatch.setenv("PLANE_MCP_WORKSPACE_SLUG", "test-ws")
    yield


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaneSyncEndpoint:
    """POST /api/plane/sync/{project_id} contract."""

    def test_disabled_returns_503(
        self, client, repo, monkeypatch
    ):
        """No PLANE_API_KEY → 503 with disabled reason; no state mutation."""
        monkeypatch.delenv("PLANE_BASE_URL", raising=False)
        monkeypatch.delenv("PLANE_API_KEY", raising=False)
        monkeypatch.delenv("PLANE_MCP_WORKSPACE_SLUG", raising=False)

        project = repo.create(name="DisabledEndpointProj")

        response = client.post(f"/api/plane/sync/{project.project_id}")
        assert response.status_code == 503
        body = response.json()
        # FastAPI wraps HTTPException detail under ``detail``.
        detail = body["detail"]
        assert detail["error"] == "plane_disabled"
        assert detail["project_id"] == project.project_id
        assert "PLANE_API_KEY" in detail["message"]

        # No state mutation.
        with Session(repo.engine) as session:
            records = repo.list_metadata_records(session, project.project_id)
        assert all(r.meta_key != PLANE_SYNC_STATE_METADATA_KEY for r in records)

    def test_unknown_project_returns_404(self, client, mock_plane_env):
        """Unknown project_id → 404 with a stable error code."""
        response = client.post("/api/plane/sync/nonexistent-uuid")
        assert response.status_code == 404
        body = response.json()
        detail = body["detail"]
        assert detail["error"] == "project_not_found"
        assert detail["project_id"] == "nonexistent-uuid"

    def test_successful_sync_returns_200(self, client, repo, mock_plane_env):
        """Happy path → 200 with status="linked" + plane_project_id echoed."""
        project = repo.create(name="LinkedEndpointProj")

        # Patch the sync service to a fake that returns a linked result.
        with patch("daemon.routers.plane.PlaneSyncService") as MockSvc:
            instance = MockSvc.return_value
            instance.is_available.return_value = True
            instance.claim_sync_slot.return_value = True
            # The endpoint constructs PlaneSyncService once via the
            # ``plane.py`` router module; that instance is used for
            # both ``is_available``, ``claim_sync_slot``, and
            # ``sync_project``.
            instance.sync_project = AsyncMock(
                return_value={
                    "status": PLANE_SYNC_STATE_LINKED,
                    "action": "updated",
                    "plane_project_id": "plane-existing",
                    "synced_at": "2026-09-20T17:00:00+00:00",
                }
            )

            response = client.post(f"/api/plane/sync/{project.project_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["project_id"] == project.project_id
        assert body["status"] == PLANE_SYNC_STATE_LINKED
        assert body["action"] == "updated"
        assert body["plane_project_id"] == "plane-existing"

    def test_reentrancy_returns_409(self, client, repo, mock_plane_env):
        """Second call while slot is held → 409."""
        project = repo.create(name="ReentrantProj")

        with patch("daemon.routers.plane.PlaneSyncService") as MockSvc:
            instance = MockSvc.return_value
            instance.is_available.return_value = True
            instance.claim_sync_slot.return_value = False  # slot held

            response = client.post(f"/api/plane/sync/{project.project_id}")

        assert response.status_code == 409
        body = response.json()
        detail = body["detail"]
        assert detail["error"] == "sync_in_flight"
        assert detail["project_id"] == project.project_id
        assert "in flight" in detail["message"].lower()

    def test_error_response_carries_diagnostics(
        self, client, repo, mock_plane_env
    ):
        """Sync that fails at the API level → 200 with status="error"
        + diagnostic message. The HTTP code stays 200 because the
        sync attempt WAS made — the 503/404/409 surface integration /
        not-found / re-entrancy failures only.
        """
        project = repo.create(name="ErrorEndpointProj")

        with patch("daemon.routers.plane.PlaneSyncService") as MockSvc:
            instance = MockSvc.return_value
            instance.is_available.return_value = True
            instance.claim_sync_slot.return_value = True
            instance.sync_project = AsyncMock(
                return_value={
                    "status": PLANE_SYNC_STATE_ERROR,
                    "action": None,
                    "message": "Plane API error: 503 from server",
                    "attempt": 1,
                }
            )

            response = client.post(f"/api/plane/sync/{project.project_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == PLANE_SYNC_STATE_ERROR
        assert "503 from server" in body["message"]
        assert body["attempt"] == 1

    def test_syncing_response_carries_status(
        self, client, repo, mock_plane_env
    ):
        """Status="syncing" → 200 with the in-flight marker so the
        caller can poll rather than re-POST. The endpoint already
        returns 409 on the slot-claim race; ``syncing`` as a status
        surfaces when the slot was claimed successfully but the
        sync_project result returned ``syncing`` (rare — e.g. a
        nested sync call).
        """
        project = repo.create(name="SyncInFlightProj")

        with patch("daemon.routers.plane.PlaneSyncService") as MockSvc:
            instance = MockSvc.return_value
            instance.is_available.return_value = True
            instance.claim_sync_slot.return_value = True
            instance.sync_project = AsyncMock(
                return_value={
                    "status": PLANE_SYNC_STATE_SYNCING,
                    "action": None,
                    "message": "A sync is already in flight for this project",
                }
            )

            response = client.post(f"/api/plane/sync/{project.project_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == PLANE_SYNC_STATE_SYNCING
        assert body["message"] is not None
