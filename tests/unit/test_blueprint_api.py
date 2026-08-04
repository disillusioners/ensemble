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
* /rebuild + /update (Phase 4): routes through the C7 coordinator,
  with coalesce/conflict/corpus-missing edge cases.
* /initialize (deprecated): still 202 + adds Deprecation/Sunset/Link
  headers and logs a WARNING.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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
from daemon.services.blueprint_trigger_coordinator import ClaimResult
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


# ─── Phase 4 fixtures ─────────────────────────────────────────────────────


class _FakeJobService:
    """Minimal stand-in for a ``JobQueueService`` for trigger endpoints.

    The router reads three things from it:
      * ``_queue_repo.get_by_name`` (sync) → returns ``bg_queue``
      * ``enqueue`` (async) → returns ``fake_job``
    Both are :class:`MagicMock` so tests can override return values or
    assert call counts. ``enqueue`` is :class:`AsyncMock`-compatible via
    ``MagicMock`` because the router ``await``s it — FastAPI/Starlette
    runs the coroutine via the test client's event loop.
    """

    def __init__(self) -> None:
        self.fake_queue = MagicMock()
        self.fake_queue.queue_id = "queue-123"
        self._queue_repo = MagicMock()
        self._queue_repo.get_by_name = MagicMock(return_value=self.fake_queue)
        self.fake_job = MagicMock()
        self.fake_job.job_id = "job-456"
        self.enqueue = AsyncMock(return_value=self.fake_job)


class _FakeCoordinator:
    """Minimal stand-in for ``BlueprintTriggerCoordinator``.

    Both ``try_claim`` and ``release`` are :class:`AsyncMock` so the
    router can ``await`` them. Tests assign the ``try_claim_result``
    attribute before invoking the endpoint to drive the response.
    """

    def __init__(self) -> None:
        self.try_claim_result = ClaimResult(
            claimed=True,
            job_id="coordinator-job-id",
            run_token="run-token-xyz",
        )
        self.try_claim = AsyncMock(
            side_effect=lambda *a, **kw: self.try_claim_result,
        )
        self.release = AsyncMock(return_value=True)
        self.release_calls: list[tuple[str, str | None]] = []
        self.try_claim_calls: list[tuple[str, str, str]] = []

        async def _track_try_claim(project_id, mode, job_id, *args, **kwargs):
            self.try_claim_calls.append((project_id, mode, job_id))
            return self.try_claim_result

        async def _track_release(project_id, run_token, *args, **kwargs):
            self.release_calls.append((project_id, run_token))
            return True

        self.try_claim.side_effect = _track_try_claim
        self.release.side_effect = _track_release


@pytest.fixture
def coordinator_app(engine, repo: BlueprintRepository):
    """FastAPI app with a mocked coordinator + job service.

    Wired for the trigger endpoints (``/rebuild``, ``/update``,
    ``/initialize``, ``/scan``). Real ``BlueprintRepository`` is used so
    the corpus-existence guard (``/update`` with empty corpus) exercises
    the real SQL path. Coordinator + job service are mocks so each
    test controls the outcome.
    """
    manager = MagicMock()
    manager._blueprint_repo = repo
    manager.is_write_paused = False
    manager._blueprint_trigger_coordinator = _FakeCoordinator()
    manager._job_queue_service = _FakeJobService()
    # Write-service factory is not used by trigger endpoints but is
    # referenced by other endpoints sharing the same router.
    manager.get_blueprint_write_service = MagicMock()

    app = FastAPI()
    app.include_router(blueprints_router, prefix="/api")
    app.state.manager = manager
    return app


@pytest.fixture
def coordinator_client(coordinator_app: FastAPI):
    with TestClient(coordinator_app) as c:
        yield c


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


# ──────────────────────────────────────────────────────────────────────────────
# Phase 4: /rebuild, /update (C7 coordinator), /initialize deprecation
# ──────────────────────────────────────────────────────────────────────────────


class TestRebuildEndpoint:
    """POST /api/projects/{project_id}/blueprints/rebuild.

    Routes through ``coordinator.try_claim("rebuild", ...)`` then enqueues.
    """

    def test_rebuild_success(self, coordinator_client, coordinator_app):
        coordinator_app.state.manager._blueprint_trigger_coordinator.try_claim_result = (
            ClaimResult(claimed=True, job_id="c-job", run_token="c-token")
        )
        r = coordinator_client.post(
            "/api/projects/00000000-0000-0000-0000-000000000001/blueprints/rebuild"
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "accepted"
        assert body["mode"] == "rebuild"
        # The job_id returned by enqueue, not the coordinator's job_id.
        assert body["job_id"] == "job-456"
        # Coordinator saw the right (project_id, mode) tuple.
        calls = coordinator_app.state.manager._blueprint_trigger_coordinator.try_claim_calls
        assert len(calls) == 1
        assert calls[0][0] == "00000000-0000-0000-0000-000000000001"
        assert calls[0][1] == "rebuild"

    def test_rebuild_coalesced(self, coordinator_client, coordinator_app):
        """Same-mode in-flight → 202 with status=already_in_progress."""
        coordinator_app.state.manager._blueprint_trigger_coordinator.try_claim_result = (
            ClaimResult(
                claimed=False, coalesced=True,
                job_id="inflight-job", run_token=None,
            )
        )
        r = coordinator_client.post(
            "/api/projects/00000000-0000-0000-0000-000000000001/blueprints/rebuild"
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "already_in_progress"
        assert body["mode"] == "rebuild"
        assert body["job_id"] == "inflight-job"
        # No enqueue should have happened.
        coordinator_app.state.manager._job_queue_service.enqueue.assert_not_called()

    def test_rebuild_conflict(self, coordinator_client, coordinator_app):
        """Different-mode in-flight → 409 with conflict_mode in detail."""
        coordinator_app.state.manager._blueprint_trigger_coordinator.try_claim_result = (
            ClaimResult(
                claimed=False, coalesced=False,
                conflict_mode="incremental",
            )
        )
        r = coordinator_client.post(
            "/api/projects/00000000-0000-0000-0000-000000000001/blueprints/rebuild"
        )
        assert r.status_code == 409, r.text
        assert "incremental" in r.json()["detail"]
        # Coordinator was NOT asked to release — the claim never succeeded.
        coordinator_app.state.manager._blueprint_trigger_coordinator.release.assert_not_called()

    def test_rebuild_coordinator_unavailable(self, coordinator_client, coordinator_app):
        """No coordinator wired → 503, no enqueue."""
        coordinator_app.state.manager._blueprint_trigger_coordinator = None
        r = coordinator_client.post(
            "/api/projects/00000000-0000-0000-0000-000000000001/blueprints/rebuild"
        )
        assert r.status_code == 503
        assert "coordinator" in r.json()["detail"].lower()
        coordinator_app.state.manager._job_queue_service.enqueue.assert_not_called()

    def test_rebuild_enqueue_failure_releases_claim(
        self, coordinator_client, coordinator_app,
    ):
        """If enqueue raises, the coordinator claim must be released."""
        coordinator_app.state.manager._blueprint_trigger_coordinator.try_claim_result = (
            ClaimResult(claimed=True, job_id="c-job", run_token="c-token-release-me")
        )
        # Make the background queue lookup fail so enqueue is never reached
        # but the helper raises HTTPException → coordinator.release called.
        coordinator_app.state.manager._job_queue_service._queue_repo.get_by_name = (
            MagicMock(return_value=None)
        )
        r = coordinator_client.post(
            "/api/projects/00000000-0000-0000-0000-000000000001/blueprints/rebuild"
        )
        assert r.status_code == 404, r.text
        # The lease must have been released with the token the router got
        # from try_claim.
        releases = coordinator_app.state.manager._blueprint_trigger_coordinator.release_calls
        assert ("00000000-0000-0000-0000-000000000001", "c-token-release-me") in releases

    def test_rebuild_invalid_project_id(self, coordinator_client, coordinator_app):
        """Non-UUID project_id → 400, coordinator untouched."""
        r = coordinator_client.post("/api/projects/not-a-uuid/blueprints/rebuild")
        assert r.status_code == 400
        coordinator_app.state.manager._blueprint_trigger_coordinator.try_claim.assert_not_called()

    def test_rebuild_rejects_default_project(self, coordinator_client, coordinator_app):
        """The system default project is rejected with 400.

        The default project (``__system_default__``) is the virtual
        bookkeeping project — blueprints are never built for it. The
        router must refuse the request before claiming a lease.
        """
        import uuid as _uuid
        from daemon.constants import SYSTEM_DEFAULT_PROJECT_NAME

        default_project_id = str(
            _uuid.uuid5(_uuid.NAMESPACE_DNS, SYSTEM_DEFAULT_PROJECT_NAME)
        )

        # Wire the manager's _project_repository to return a project
        # matching the default name for the deterministic id.
        default_project = MagicMock()
        default_project.project_id = default_project_id
        default_project.name = SYSTEM_DEFAULT_PROJECT_NAME
        coordinator_app.state.manager._project_repository = MagicMock()
        coordinator_app.state.manager._project_repository.get = MagicMock(
            return_value=default_project,
        )

        r = coordinator_client.post(
            f"/api/projects/{default_project_id}/blueprints/rebuild"
        )
        assert r.status_code == 400, r.text
        assert "default project" in r.json()["detail"].lower()
        # The coordinator must never be reached.
        coordinator_app.state.manager._blueprint_trigger_coordinator.try_claim.assert_not_called()
        # And no enqueue happens.
        coordinator_app.state.manager._job_queue_service.enqueue.assert_not_called()

    def test_rebuild_forwards_job_id_to_enqueue(
        self, coordinator_client, coordinator_app,
    ):
        """The coordinator's lease ``job_id`` must match the enqueued job's id.

        Regression test for the lease/queue ``job_id`` mismatch bug: prior
        to this fix, the router passed its generated UUID to
        ``coordinator.try_claim`` (stored in the lease) but did NOT forward
        it to ``job_service.enqueue``, which generated its own UUID. The
        lease's ``job_id`` then pointed to a non-existent job — breaking
        coalescing (returning a ``job_id`` for a job that never existed)
        and orphaning the lease on the periodic sweep (job-not-found →
        released).
        """
        coordinator_app.state.manager._blueprint_trigger_coordinator.try_claim_result = (
            ClaimResult(claimed=True, job_id="c-job", run_token="c-token")
        )
        r = coordinator_client.post(
            "/api/projects/00000000-0000-0000-0000-000000000001/blueprints/rebuild"
        )
        assert r.status_code == 202, r.text

        # The job_id the router passed to coordinator.try_claim
        try_claim_calls = (
            coordinator_app.state.manager._blueprint_trigger_coordinator.try_claim_calls
        )
        assert len(try_claim_calls) == 1
        forwarded_job_id = try_claim_calls[0][2]
        assert forwarded_job_id, "router must generate a non-empty job_id for try_claim"

        # That same id must have been forwarded to enqueue so the lease
        # and the queued JobItem agree.
        enqueue_mock = coordinator_app.state.manager._job_queue_service.enqueue
        assert enqueue_mock.call_count == 1
        enqueue_kwargs = enqueue_mock.call_args.kwargs
        assert enqueue_kwargs.get("job_id") == forwarded_job_id, (
            "enqueue() must receive the same job_id the router passed to "
            "coordinator.try_claim; otherwise the lease's job_id points to "
            "a JobItem that doesn't exist."
        )


class TestUpdateEndpoint:
    """POST /api/projects/{project_id}/blueprints/update.

    Same coordinator routing as ``/rebuild`` plus a corpus-existence
    guard (404 if no blueprints, with claim released).
    """

    def test_update_success(self, coordinator_client, coordinator_app, repo):
        # Need an existing blueprint so the corpus guard passes.
        repo.create(
            project_id="00000000-0000-0000-0000-000000000001",
            slug="core-doc",
            name="core-doc",
            kind="core",
            content="body",
        )
        coordinator_app.state.manager._blueprint_trigger_coordinator.try_claim_result = (
            ClaimResult(claimed=True, job_id="c-job", run_token="c-token")
        )
        r = coordinator_client.post(
            "/api/projects/00000000-0000-0000-0000-000000000001/blueprints/update"
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "accepted"
        assert body["mode"] == "incremental"
        # Coordinator was called with mode="incremental".
        calls = coordinator_app.state.manager._blueprint_trigger_coordinator.try_claim_calls
        assert calls[0][1] == "incremental"

    def test_update_no_corpus(self, coordinator_client, coordinator_app):
        """Empty corpus → 404, claim released."""
        coordinator_app.state.manager._blueprint_trigger_coordinator.try_claim_result = (
            ClaimResult(
                claimed=True, job_id="c-job",
                run_token="token-to-release",
            )
        )
        r = coordinator_client.post(
            "/api/projects/00000000-0000-0000-0000-000000000001/blueprints/update"
        )
        assert r.status_code == 404, r.text
        assert "rebuild" in r.json()["detail"].lower()
        # Claim must have been released.
        releases = coordinator_app.state.manager._blueprint_trigger_coordinator.release_calls
        assert ("00000000-0000-0000-0000-000000000001", "token-to-release") in releases
        # And no enqueue happened.
        coordinator_app.state.manager._job_queue_service.enqueue.assert_not_called()

    def test_update_coalesced(self, coordinator_client, coordinator_app, repo):
        """Same-mode in-flight → 202 with status=already_in_progress."""
        repo.create(
            project_id="00000000-0000-0000-0000-000000000001",
            slug="exists",
            name="exists",
            kind="area",
            content="body",
        )
        coordinator_app.state.manager._blueprint_trigger_coordinator.try_claim_result = (
            ClaimResult(
                claimed=False, coalesced=True,
                job_id="inflight-inc", run_token=None,
            )
        )
        r = coordinator_client.post(
            "/api/projects/00000000-0000-0000-0000-000000000001/blueprints/update"
        )
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == "already_in_progress"
        assert body["mode"] == "incremental"
        assert body["job_id"] == "inflight-inc"
        coordinator_app.state.manager._job_queue_service.enqueue.assert_not_called()

    def test_update_conflict(self, coordinator_client, coordinator_app):
        """Different-mode in-flight → 409."""
        coordinator_app.state.manager._blueprint_trigger_coordinator.try_claim_result = (
            ClaimResult(
                claimed=False, coalesced=False,
                conflict_mode="rebuild",
            )
        )
        r = coordinator_client.post(
            "/api/projects/00000000-0000-0000-0000-000000000001/blueprints/update"
        )
        assert r.status_code == 409
        assert "rebuild" in r.json()["detail"]

    def test_update_forwards_job_id_to_enqueue(
        self, coordinator_client, coordinator_app, repo,
    ):
        """The coordinator's lease ``job_id`` must match the enqueued job's id.

        Mirrors :meth:`TestRebuildEndpoint.test_rebuild_forwards_job_id_to_enqueue`
        for the ``/update`` path. Both coordinator-gated endpoints share the
        same helper and the same lease/queue alignment requirement.
        """
        # Need an existing blueprint so the corpus guard passes.
        repo.create(
            project_id="00000000-0000-0000-0000-000000000001",
            slug="core-doc",
            name="core-doc",
            kind="core",
            content="body",
        )
        coordinator_app.state.manager._blueprint_trigger_coordinator.try_claim_result = (
            ClaimResult(claimed=True, job_id="c-job", run_token="c-token")
        )
        r = coordinator_client.post(
            "/api/projects/00000000-0000-0000-0000-000000000001/blueprints/update"
        )
        assert r.status_code == 202, r.text

        try_claim_calls = (
            coordinator_app.state.manager._blueprint_trigger_coordinator.try_claim_calls
        )
        assert len(try_claim_calls) == 1
        forwarded_job_id = try_claim_calls[0][2]
        assert forwarded_job_id

        enqueue_mock = coordinator_app.state.manager._job_queue_service.enqueue
        assert enqueue_mock.call_count == 1
        enqueue_kwargs = enqueue_mock.call_args.kwargs
        assert enqueue_kwargs.get("job_id") == forwarded_job_id

    def test_update_rejects_default_project(self, coordinator_client, coordinator_app):
        """The system default project is rejected with 400.

        Mirrors :meth:`TestRebuildEndpoint.test_rebuild_rejects_default_project`
        for the ``/update`` path. The default project never enters
        the coordinator's claim path.
        """
        import uuid as _uuid
        from daemon.constants import SYSTEM_DEFAULT_PROJECT_NAME

        default_project_id = str(
            _uuid.uuid5(_uuid.NAMESPACE_DNS, SYSTEM_DEFAULT_PROJECT_NAME)
        )

        default_project = MagicMock()
        default_project.project_id = default_project_id
        default_project.name = SYSTEM_DEFAULT_PROJECT_NAME
        coordinator_app.state.manager._project_repository = MagicMock()
        coordinator_app.state.manager._project_repository.get = MagicMock(
            return_value=default_project,
        )

        r = coordinator_client.post(
            f"/api/projects/{default_project_id}/blueprints/update"
        )
        assert r.status_code == 400, r.text
        assert "default project" in r.json()["detail"].lower()
        # Nothing reached the coordinator or the job queue.
        coordinator_app.state.manager._blueprint_trigger_coordinator.try_claim.assert_not_called()
        coordinator_app.state.manager._job_queue_service.enqueue.assert_not_called()


class TestInitializeDeprecation:
    """POST /initialize is deprecated but must remain backward-compatible.

    Behavior unchanged: returns 202 if no core exists, 409 if a core
    already exists, 503 if no job service. The new behavior is purely
    additive: deprecation headers + WARNING log.
    """

    def test_initialize_still_works(self, coordinator_client):
        """Empty corpus → 202 (no coordinator used)."""
        r = coordinator_client.post(
            "/api/projects/00000000-0000-0000-0000-000000000001/blueprints/initialize"
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "enqueued"
        assert body["job_id"] == "job-456"

    def test_initialize_deprecation_headers(self, coordinator_client):
        r = coordinator_client.post(
            "/api/projects/00000000-0000-0000-0000-000000000001/blueprints/initialize"
        )
        assert r.status_code == 202
        assert r.headers.get("deprecation") == "true"
        assert r.headers.get("sunset") == "Sun, 31 Dec 2026 23:59:59 GMT"
        link = r.headers.get("link", "")
        assert "/blueprints/rebuild" in link
        assert 'rel="successor-version"' in link

    def test_initialize_logs_warning(self, coordinator_client, caplog):
        import logging as _logging

        with caplog.at_level(_logging.WARNING, logger="daemon.routers.blueprints"):
            r = coordinator_client.post(
                "/api/projects/00000000-0000-0000-0000-000000000001/blueprints/initialize"
            )
        assert r.status_code == 202
        # The warning must surface in caplog.
        matching = [
            rec for rec in caplog.records
            if rec.levelno >= _logging.WARNING
            and "/initialize" in rec.getMessage()
        ]
        assert matching, f"no /initialize WARNING captured: {[r.getMessage() for r in caplog.records]}"

    def test_initialize_409_when_core_exists(self, coordinator_client, repo):
        """Backward-compat guard: 409 if a core blueprint already exists."""
        repo.create(
            project_id="00000000-0000-0000-0000-000000000001",
            slug="core-doc",
            name="core-doc",
            kind="core",
            content="body",
        )
        r = coordinator_client.post(
            "/api/projects/00000000-0000-0000-0000-000000000001/blueprints/initialize"
        )
        assert r.status_code == 409
        assert "already initialized" in r.json()["detail"].lower()
        # Enqueue must NOT have been called.
        coordinator_client.app.state.manager._job_queue_service.enqueue.assert_not_called()
