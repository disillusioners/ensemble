"""Unit tests for ``daemon.routers.blueprints``.

Exercises the six REST endpoints against a real
:class:`BlueprintRepository` backed by an in-memory SQLite engine. The
manager is a small stub exposing ``_blueprint_repo`` and
``is_write_paused`` — the router only depends on those two attributes.

The DB schema is created via ``SQLModel.metadata.create_all(engine)``
on the same model registry used by the production ``BlueprintRepository``
tests, so we get the real ``project_blueprints`` /
``project_blueprint_revisions`` / ``project_blueprint_triggers`` tables.

Test surface
------------
* POST creates a blueprint, returns 201 with the expected fields.
* GET /{id} returns the blueprint.
* GET / lists all blueprints for a project.
* GET /?kind=core filters to core blueprints.
* PUT updates name and content; version bumps on content change.
* DELETE soft-deletes (is_active=False).
* GET /{id}/revisions returns revision history.
* Project scoping: a blueprint in project A is 404 from project B.
* 404 on missing blueprint for get / update / delete.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from starlette.testclient import TestClient

from daemon.repositories.blueprint.embedding_repository import (
    BlueprintEmbeddingRepository,
)
from daemon.repositories.blueprint.repository import BlueprintRepository
from daemon.routers.blueprints import router as blueprints_router
from daemon.services.blueprint_rate_limiter import BlueprintRateLimiter
from daemon.services.blueprint_write_service import BlueprintWriteService


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """A fresh in-memory SQLite engine with all tables created.

    Uses ``StaticPool`` + ``check_same_thread=False`` so the same
    connection (and its schema) is shared across threads. The router
    bridges sync repo calls via ``asyncio.to_thread``, so a default pool
    would open a new in-memory DB per worker thread and lose the tables.
    """
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def repo(engine):
    """A fresh BlueprintRepository over an in-memory SQLite engine."""
    return BlueprintRepository(engine)


@pytest.fixture
def embedding_repo(engine):
    """A BlueprintEmbeddingRepository over the same engine."""
    return BlueprintEmbeddingRepository(engine)


@pytest.fixture
def app(
    engine,
    repo: BlueprintRepository,
    embedding_repo: BlueprintEmbeddingRepository,
):
    """A FastAPI app with the blueprints router and a stub manager.

    The manager stub wires ``get_blueprint_write_service`` to return a
    real ``BlueprintWriteService`` backed by the in-memory engine, so
    the router's create/update/delete calls exercise the full canonical
    write boundary (revision capture, trigger replace, etc.) without an
    embedding service (``embedding_service=None`` → triggers skipped).
    """
    manager = MagicMock()
    manager._blueprint_repo = repo
    manager.is_write_paused = False

    def _get_write_service(project_id: str) -> BlueprintWriteService:
        return BlueprintWriteService(
            repository=repo,
            embedding_repository=embedding_repo,
            embedding_service=None,  # no embedding API in tests
            rate_limiter=None,  # no rate limiting in most API tests
            config=MagicMock(),
            project_id=project_id,
            manager=manager,
        )

    manager.get_blueprint_write_service = _get_write_service

    app = FastAPI()
    app.include_router(blueprints_router, prefix="/api")
    app.state.manager = manager
    return app


@pytest.fixture
def client(app: FastAPI):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def bp_factory(repo: BlueprintRepository):
    """Helper: create a blueprint directly through the repo.

    Bypasses the API so each test can set up its own starting state.
    """

    def _make(
        project: str = "00000000-0000-0000-0000-000000000001",
        slug: str = "slug",
        kind: str = "area",
        content: str = "body",
        name: str | None = None,
    ):
        return repo.create(
            project_id=project,
            slug=slug,
            name=name or slug,
            kind=kind,
            content=content,
        )

    return _make


# ──────────────────────────────────────────────────────────────────────────────
# POST / — create
# ──────────────────────────────────────────────────────────────────────────────


class TestCreateBlueprint:
    """POST /api/projects/{project_id}/blueprints."""

    def test_create_returns_201_with_expected_fields(self, client):
        r = client.post(
            "/api/projects/00000000-0000-0000-0000-000000000001/blueprints",
            json={
                "slug": "core",
                "name": "Core Doc",
                "kind": "core",
                "content": "# Core",
                "tags": [{"k": "v"}],
                "file_refs": ["docs/intro.md"],
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["project_id"] == "00000000-0000-0000-0000-000000000001"
        assert body["slug"] == "core"
        assert body["name"] == "Core Doc"
        assert body["kind"] == "core"
        assert body["content"] == "# Core"
        assert body["version"] == 1
        assert body["is_active"] is True
        assert body["tags"] == [{"k": "v"}]
        assert body["file_refs"] == ["docs/intro.md"]
        # Server-generated id should be present
        assert isinstance(body["id"], str) and body["id"]

    def test_create_uses_path_project_id_not_body(self, client):
        """The body has no project_id; the path param wins."""
        r = client.post(
            "/api/projects/00000000-0000-0000-0000-000000000003/blueprints",
            json={"slug": "s", "name": "n", "content": "c"},
        )
        assert r.status_code == 201
        assert r.json()["project_id"] == "00000000-0000-0000-0000-000000000003"

    def test_create_defaults_kind_to_area(self, client):
        r = client.post(
            "/api/projects/00000000-0000-0000-0000-000000000001/blueprints",
            json={"slug": "s", "name": "n", "content": "c"},
        )
        assert r.status_code == 201
        assert r.json()["kind"] == "area"

    def test_create_rejects_empty_content(self, client):
        r = client.post(
            "/api/projects/00000000-0000-0000-0000-000000000001/blueprints",
            json={"slug": "s", "name": "n", "content": ""},
        )
        assert r.status_code == 422

    def test_create_returns_503_when_writes_paused(self, repo):
        manager = MagicMock()
        manager._blueprint_repo = repo
        manager.is_write_paused = True
        # The write service factory must exist, but it should never be
        # called because the paused check fires first.
        manager.get_blueprint_write_service = MagicMock()
        app = FastAPI()
        app.include_router(blueprints_router, prefix="/api")
        app.state.manager = manager
        with TestClient(app) as c:
            r = c.post(
                "/api/projects/00000000-0000-0000-0000-000000000001/blueprints",
                json={"slug": "s", "name": "n", "content": "c"},
            )
        assert r.status_code == 503


# ──────────────────────────────────────────────────────────────────────────────
# GET /{id} — single
# ──────────────────────────────────────────────────────────────────────────────


class TestGetBlueprint:
    """GET /api/projects/{project_id}/blueprints/{blueprint_id}."""

    def test_get_returns_blueprint(self, client, bp_factory):
        bp = bp_factory(slug="alpha")
        r = client.get(f"/api/projects/00000000-0000-0000-0000-000000000001/blueprints/{bp.id}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == bp.id
        assert body["slug"] == "alpha"
        assert body["project_id"] == "00000000-0000-0000-0000-000000000001"

    def test_get_missing_returns_404(self, client):
        r = client.get("/api/projects/00000000-0000-0000-0000-000000000001/blueprints/does-not-exist")
        assert r.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# GET / — list
# ──────────────────────────────────────────────────────────────────────────────


class TestListBlueprints:
    """GET /api/projects/{project_id}/blueprints."""

    def test_list_returns_all_active_for_project(self, client, bp_factory):
        bp_factory(slug="a")
        bp_factory(slug="b")
        bp_factory(project="00000000-0000-0000-0000-000000000002", slug="other")  # cross-project: must be excluded

        r = client.get("/api/projects/00000000-0000-0000-0000-000000000001/blueprints")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2
        slugs = {item["slug"] for item in body["items"]}
        assert slugs == {"a", "b"}

    def test_list_kind_filter_returns_only_core(self, client, bp_factory):
        bp_factory(slug="core-doc", kind="core")
        bp_factory(slug="area-doc", kind="area")

        r = client.get("/api/projects/00000000-0000-0000-0000-000000000001/blueprints?kind=core")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["slug"] == "core-doc"
        assert body["items"][0]["kind"] == "core"

    def test_list_status_filter_client_side(self, client, repo, bp_factory):
        """status filter is applied client-side after the repo query."""
        bp_factory(slug="pub")  # default status="published"
        # Create a draft one via direct repo (the router cannot set status yet)
        repo.create(
            project_id="00000000-0000-0000-0000-000000000001",
            slug="draft",
            name="draft",
            kind="area",
            content="c",
            status="draft",
        )

        r = client.get("/api/projects/00000000-0000-0000-0000-000000000001/blueprints?status=published")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["slug"] == "pub"

        r = client.get("/api/projects/00000000-0000-0000-0000-000000000001/blueprints?status=draft")
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["slug"] == "draft"

    def test_list_excludes_soft_deleted(self, client, repo, bp_factory):
        b = bp_factory(slug="alive")
        bp_factory(slug="doomed")
        repo.soft_delete(b.id)

        r = client.get("/api/projects/00000000-0000-0000-0000-000000000001/blueprints")
        body = r.json()
        slugs = {item["slug"] for item in body["items"]}
        assert slugs == {"doomed"}


# ──────────────────────────────────────────────────────────────────────────────
# PUT /{id} — update
# ──────────────────────────────────────────────────────────────────────────────


class TestUpdateBlueprint:
    """PUT /api/projects/{project_id}/blueprints/{blueprint_id}."""

    def test_update_name_and_content(self, client, repo, bp_factory):
        bp = bp_factory(slug="s", content="v1")
        r = client.put(
            f"/api/projects/00000000-0000-0000-0000-000000000001/blueprints/{bp.id}",
            json={"name": "renamed", "content": "v2"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "renamed"
        assert body["content"] == "v2"
        # Repo auto-bumps version when content changes.
        assert body["version"] == 2

    def test_update_preserves_version_when_only_name_changes(
        self, client, repo, bp_factory
    ):
        bp = bp_factory(slug="s")
        r = client.put(
            f"/api/projects/00000000-0000-0000-0000-000000000001/blueprints/{bp.id}",
            json={"name": "renamed"},
        )
        assert r.status_code == 200
        # No content/file_refs/tags change → version stays at 1.
        assert r.json()["version"] == 1

    def test_update_empty_body_returns_400(self, client, bp_factory):
        bp = bp_factory(slug="s")
        r = client.put(
            f"/api/projects/00000000-0000-0000-0000-000000000001/blueprints/{bp.id}", json={}
        )
        assert r.status_code == 400

    def test_update_missing_returns_404(self, client):
        r = client.put(
            "/api/projects/00000000-0000-0000-0000-000000000001/blueprints/missing",
            json={"name": "x"},
        )
        assert r.status_code == 404

    def test_update_with_status_field(self, client, repo, bp_factory):
        """C1 fix: status-only PUT does not crash; status is applied."""
        bp = bp_factory(slug="s")
        r = client.put(
            f"/api/projects/00000000-0000-0000-0000-000000000001/blueprints/{bp.id}",
            json={"status": "draft"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "draft"
        # status is NOT a version-incrementing field → version unchanged.
        assert body["version"] == 1


# ─── C2(e)/C7: UUID validation ─────────────────────────────────────────────


class TestProjectIdValidation:
    """Invalid project_id (non-UUID) returns 400 (C2 fix e / C7)."""

    def test_invalid_project_id_returns_400_on_list(self, client):
        r = client.get("/api/projects/not-a-uuid/blueprints")
        assert r.status_code == 400

    def test_invalid_project_id_returns_400_on_create(self, client):
        r = client.post(
            "/api/projects/not-a-uuid/blueprints",
            json={"slug": "s", "name": "n", "content": "c"},
        )
        assert r.status_code == 400


# ──────────────────────────────────────────────────────────────────────────────
# DELETE /{id} — soft delete
# ──────────────────────────────────────────────────────────────────────────────


class TestDeleteBlueprint:
    """DELETE /api/projects/{project_id}/blueprints/{blueprint_id}."""

    def test_delete_sets_inactive(self, client, repo, bp_factory):
        bp = bp_factory(slug="s")
        r = client.delete(f"/api/projects/00000000-0000-0000-0000-000000000001/blueprints/{bp.id}")
        assert r.status_code == 200
        assert r.json() == {"deleted": True}
        # The repo still finds the row (soft delete) but is_active is False.
        assert repo.get_by_id(bp.id).is_active is False

    def test_delete_missing_returns_404(self, client):
        r = client.delete("/api/projects/00000000-0000-0000-0000-000000000001/blueprints/missing")
        assert r.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# GET /{id}/revisions
# ──────────────────────────────────────────────────────────────────────────────


class TestListRevisions:
    """GET /api/projects/{project_id}/blueprints/{blueprint_id}/revisions."""

    def test_revisions_returns_history(self, client, repo, bp_factory):
        bp = bp_factory(slug="s")
        # The repo's update() does NOT auto-write a revision; we add them
        # directly to exercise the endpoint.
        repo.add_revision(blueprint_id=bp.id, version=1, content_snapshot="v1")
        repo.add_revision(
            blueprint_id=bp.id, version=2, content_snapshot="v2",
            source="manual", reason="second pass",
        )

        r = client.get(f"/api/projects/00000000-0000-0000-0000-000000000001/blueprints/{bp.id}/revisions")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 2
        # list_revisions orders by version DESC.
        assert body[0]["version"] == 2
        assert body[1]["version"] == 1
        assert body[0]["content_snapshot"] == "v2"
        assert body[0]["reason"] == "second pass"

    def test_revisions_missing_blueprint_returns_404(self, client):
        r = client.get("/api/projects/00000000-0000-0000-0000-000000000001/blueprints/missing/revisions")
        assert r.status_code == 404

    def test_revisions_empty_returns_empty_list(self, client, bp_factory):
        bp = bp_factory(slug="s")
        r = client.get(f"/api/projects/00000000-0000-0000-0000-000000000001/blueprints/{bp.id}/revisions")
        assert r.status_code == 200
        assert r.json() == []


# ──────────────────────────────────────────────────────────────────────────────
# Cross-project security
# ──────────────────────────────────────────────────────────────────────────────


class TestProjectScoping:
    """A blueprint in project A must be invisible (404) from project B."""

    def test_get_other_project_returns_404(self, client, bp_factory):
        bp = bp_factory(project="00000000-0000-0000-0000-000000000001", slug="x")
        r = client.get(f"/api/projects/00000000-0000-0000-0000-000000000002/blueprints/{bp.id}")
        assert r.status_code == 404

    def test_update_other_project_returns_404(self, client, bp_factory):
        bp = bp_factory(project="00000000-0000-0000-0000-000000000001", slug="x")
        r = client.put(
            f"/api/projects/00000000-0000-0000-0000-000000000002/blueprints/{bp.id}",
            json={"name": "hijack"},
        )
        assert r.status_code == 404
        assert r.json()["detail"].startswith("Blueprint not found")

    def test_delete_other_project_returns_404(self, client, repo, bp_factory):
        bp = bp_factory(project="00000000-0000-0000-0000-000000000001", slug="x")
        r = client.delete(f"/api/projects/00000000-0000-0000-0000-000000000002/blueprints/{bp.id}")
        assert r.status_code == 404
        # The blueprint is still active in its own project.
        assert repo.get_by_id(bp.id).is_active is True

    def test_revisions_other_project_returns_404(self, client, bp_factory):
        bp = bp_factory(project="00000000-0000-0000-0000-000000000001", slug="x")
        r = client.get(f"/api/projects/00000000-0000-0000-0000-000000000002/blueprints/{bp.id}/revisions")
        assert r.status_code == 404
