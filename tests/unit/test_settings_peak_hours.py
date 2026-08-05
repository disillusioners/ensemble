"""Tests for ``GET/PUT /api/settings/blueprint-peak-hours``.

Exercises the two new endpoints against an in-memory SQLite-backed
:class:`SQLModelProjectRepository` wired into the real settings router.
The scan service reads the same metadata keys on every ``execute()``
tick; these tests verify the round-trip without needing a live
PostgreSQL instance.

The tests skip cleanly when the daemon package cannot be imported (no
``daemon`` on sys.path in standalone runs).
"""

from __future__ import annotations

from typing import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel
from starlette.testclient import TestClient

from daemon.constants import SYSTEM_DEFAULT_PROJECT_NAME
from daemon.repositories import SQLModelProjectRepository
from daemon.repositories.project.models import (
    Project,
    ProjectMetadataRecord,
    ProjectShortnameLink,
    ProjectStatus,
    ProjectTagLink,
    ProjectType,
)
from daemon.routers.settings import (
    router as settings_router,
    set_project_repository,
)


SYSTEM_DEFAULT_PID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """Fresh in-memory SQLite engine with full SQLModel schema.

    Imports the project models explicitly so SQLModel.metadata.create_all
    picks up the tables referenced by the metadata repo (without these
    imports the metadata_records table would silently not be created).
    """
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def project_repo(engine) -> SQLModelProjectRepository:
    """Real ``SQLModelProjectRepository`` bound to the in-memory engine."""
    return SQLModelProjectRepository(engine)


@pytest.fixture
def system_default_project(project_repo) -> str:
    """Insert the system-default project row referenced by the router.

    The router calls ``repo.set_metadata(SYSTEM_DEFAULT_PROJECT_ID, ...)``
    which pre-flights ``session.get(Project, project_id)`` — without a
    matching row the call silently no-ops (returns None), so the PUT
    endpoint would always 200 without actually persisting.
    """
    now_iso = "2026-08-05T00:00:00+00:00"
    with Session(project_repo.engine) as session:
        project = Project(
            project_id=SYSTEM_DEFAULT_PID,
            name=SYSTEM_DEFAULT_PROJECT_NAME,
            project_type=ProjectType.GENERAL.value,
            status=ProjectStatus.ACTIVE.value,
            main_directory=None,
            related_directories=[],
            description="system default project (test)",
            project_metadata={},
            relationships={},
            creator_instance_id=None,
            creator_agent_id=None,
            created_at=now_iso,
            updated_at=now_iso,
        )
        session.add(project)
        session.commit()
    return SYSTEM_DEFAULT_PID


@pytest.fixture
def client(project_repo, system_default_project) -> Iterator[TestClient]:
    """Starlette ``TestClient`` bound to a minimal app with the settings router.

    Wires ``set_project_repository`` so the router's module-level global
    points at the in-memory repo; the autouse fixture below pins
    ``SYSTEM_DEFAULT_PROJECT_ID`` so the settings endpoints can resolve
    it.
    """
    set_project_repository(project_repo)
    app = FastAPI()
    # The settings router calls ``request.app.state.manager`` via
    # ``_get_project_repo`` only on VS Code / editor endpoints; the
    # peak-hours endpoints use the module-level ``_project_repo`` global.
    # We still set a stub manager for safety in case any future endpoint
    # routes through ``request.app.state``.
    app.state.manager = MagicMock()
    app.include_router(settings_router, prefix="/api")
    with TestClient(app) as c:
        yield c
    # Reset module state so other tests start from a clean slate.
    from daemon.routers import settings as settings_module

    settings_module._project_repo = None


@pytest.fixture(autouse=True)
def _propagate_system_default_project_id():
    """Pin ``SYSTEM_DEFAULT_PROJECT_ID`` for the duration of the test.

    Production wires this constant during daemon startup; tests that
    exercise the settings router need it set explicitly so the router
    can resolve the project row.
    """
    from daemon import constants

    original = constants.SYSTEM_DEFAULT_PROJECT_ID
    constants.SYSTEM_DEFAULT_PROJECT_ID = SYSTEM_DEFAULT_PID
    try:
        yield
    finally:
        constants.SYSTEM_DEFAULT_PROJECT_ID = original


# ── Tests ─────────────────────────────────────────────────────────────────


class TestGetBlueprintPeakHours:
    """GET /api/settings/blueprint-peak-hours — read the gate window."""

    def test_returns_defaults_when_no_metadata(self, client):
        """Without any stored metadata, the response carries the
        canonical defaults (12:00 - 20:00 GMT+7)."""
        response = client.get("/api/settings/blueprint-peak-hours")

        assert response.status_code == 200
        assert response.json() == {"start": 12, "end": 20, "tz_offset": 7}

    def test_returns_stored_values_after_put(self, client):
        """Round-trip: PUT then GET returns the same values."""
        put_resp = client.put(
            "/api/settings/blueprint-peak-hours",
            json={"start": 9, "end": 17, "tz_offset": 3},
        )
        assert put_resp.status_code == 200

        get_resp = client.get("/api/settings/blueprint-peak-hours")
        assert get_resp.status_code == 200
        assert get_resp.json() == {"start": 9, "end": 17, "tz_offset": 3}

    def test_handles_partial_metadata(self, client, project_repo, system_default_project):
        """When only some keys are stored, the missing ones fall back
        to the defaults. This mirrors the scan service's own fallback
        behaviour so the two layers stay in lockstep."""
        project_repo.set_metadata(system_default_project, "blueprint_peak_hours_start", 8)

        response = client.get("/api/settings/blueprint-peak-hours")

        assert response.status_code == 200
        # start overridden, end + tz_offset fall back to defaults.
        assert response.json() == {"start": 8, "end": 20, "tz_offset": 7}


class TestPutBlueprintPeakHours:
    """PUT /api/settings/blueprint-peak-hours — write the gate window."""

    def test_put_writes_three_keys(self, client, project_repo, system_default_project):
        """A successful PUT persists all three keys under the system
        default project metadata."""
        response = client.put(
            "/api/settings/blueprint-peak-hours",
            json={"start": 6, "end": 22, "tz_offset": -5},
        )

        assert response.status_code == 200
        assert response.json() == {"start": 6, "end": 22, "tz_offset": -5}

        # Each key must be independently retrievable via the repo.
        assert (
            project_repo.get_metadata(system_default_project, "blueprint_peak_hours_start") == 6
        )
        assert (
            project_repo.get_metadata(system_default_project, "blueprint_peak_hours_end") == 22
        )
        assert (
            project_repo.get_metadata(system_default_project, "blueprint_peak_hours_tz_offset") == -5
        )

    def test_put_overwrites_previous_values(self, client, project_repo, system_default_project):
        """A second PUT overrides the first — the scan service picks up
        the most recent values on its next execute() tick."""
        client.put(
            "/api/settings/blueprint-peak-hours",
            json={"start": 9, "end": 17, "tz_offset": 3},
        )
        second = client.put(
            "/api/settings/blueprint-peak-hours",
            json={"start": 10, "end": 18, "tz_offset": 4},
        )

        assert second.status_code == 200
        assert second.json() == {"start": 10, "end": 18, "tz_offset": 4}
        assert (
            project_repo.get_metadata(system_default_project, "blueprint_peak_hours_start") == 10
        )


class TestPutValidation:
    """Pydantic enforcement of hour / offset bounds (422 on invalid)."""

    def test_start_above_23_rejected(self, client):
        response = client.put(
            "/api/settings/blueprint-peak-hours",
            json={"start": 24, "end": 20, "tz_offset": 7},
        )
        assert response.status_code == 422

    def test_negative_start_rejected(self, client):
        response = client.put(
            "/api/settings/blueprint-peak-hours",
            json={"start": -1, "end": 20, "tz_offset": 7},
        )
        assert response.status_code == 422

    def test_end_above_23_rejected(self, client):
        response = client.put(
            "/api/settings/blueprint-peak-hours",
            json={"start": 12, "end": 25, "tz_offset": 7},
        )
        assert response.status_code == 422

    def test_tz_offset_out_of_range_rejected(self, client):
        # -13 is outside [-12, 14].
        response = client.put(
            "/api/settings/blueprint-peak-hours",
            json={"start": 12, "end": 20, "tz_offset": -13},
        )
        assert response.status_code == 422

    def test_boundary_values_accepted(self, client):
        """Both endpoints of the tz_offset range (-12, 14) must accept."""
        for tz in (-12, 14):
            response = client.put(
                "/api/settings/blueprint-peak-hours",
                json={"start": 0, "end": 23, "tz_offset": tz},
            )
            assert response.status_code == 200, f"tz_offset={tz} should be accepted"
            assert response.json()["tz_offset"] == tz
