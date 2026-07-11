"""End-to-end integration tests for the Skill Evolution System.

Phase 6 of the Skill Evolution System — full lifecycle coverage
across the REST API and the underlying service layer.

What this covers
----------------

The full skill lifecycle through every Phase of the system:

1. **Create / list / view / update / delete** — exercises the
   REST API surface and the ``SkillStoreService`` CRUD layer.
2. **Search** — ``SkillSearchService`` three-stage pipeline with
   the embedding service mocked out (BM25-only path).
3. **Usage recording + feedback** — direct call to
   ``SkillMetricsService.record_task_completion`` plus the
   ``skill_feedback`` REST endpoint.
4. **Metrics endpoint** — ``GET /api/skills/{id}/metrics``
   returns the bundled payload from
   ``SkillEvolutionService.get_skill_metrics``.
5. **Fix dispatch** — ``POST /api/skills/{id}/fix`` enqueues a
   ``skill_evolution`` JobItem via ``SkillJobDispatcher``.
6. **A/B testing** — direct creation of ``skill_ab_tests`` rows
   plus ``SkillEvolutionService.check_ab_test_resolution`` to
   exercise the resolution flow.
7. **CAPTURED flow** — direct call to
   ``SkillEvolutionService.capture_skill`` with a stubbed LLM
   that returns a deterministic JSON body.
8. **Lineage endpoint** — ``GET /api/skills/{id}/lineage``
   returns the parents/children graph.
9. **Share endpoint** — ``POST /api/skills/{id}/share`` clears
   the ``project_id`` column.

Mocking policy
--------------

LLM calls are stubbed by patching the
:meth:`SkillEvolutionService._call_llm` method on the
service instance — every evolution / capture / generation path
flows through this single chokepoint, so a per-test override
keeps the test self-contained and avoids the global
``openai.OpenAI`` patch lifecycle.

Embedding refresh is best-effort and the embedding service is
mocked via the constructor (``AsyncMock``), so no OpenAI calls
are made.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.config import SkillEvolutionConfig
from daemon.repositories.skill import (
    SkillABTestRepository,
    SkillEmbeddingRepository,
    SkillLineageRepository,
    SkillRepository,
    SkillTriggerRepository,
    SkillUsageRepository,
)
from daemon.routers import skills as skills_router_module
from daemon.services.skill_embedding_service import SkillEmbeddingService
from daemon.services.skill_evolution_service import SkillEvolutionService
from daemon.services.skill_metrics_service import (
    INJECTED_SKILLS_METADATA_KEY,
    SkillMetricsService,
)
from daemon.services.skill_search_service import SkillSearchService
from daemon.services.skill_store_service import SkillStoreService


# ---------------------------------------------------------------------------
# Engine + repos fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    """In-memory SQLite engine with all skill tables created."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repos(engine):
    """All six skill repositories bound to the test engine."""
    return SimpleNamespace(
        skill=SkillRepository(engine),
        lineage=SkillLineageRepository(engine),
        usage=SkillUsageRepository(engine),
        trigger=SkillTriggerRepository(engine),
        embedding=SkillEmbeddingRepository(engine),
        ab_test=SkillABTestRepository(engine),
    )


# ---------------------------------------------------------------------------
# Service-layer fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    """Default SkillEvolutionConfig — keep thresholds at defaults."""
    return SkillEvolutionConfig()


@pytest.fixture
def embedding_service():
    """Mocked SkillEmbeddingService — no OpenAI calls."""
    svc = MagicMock(spec=SkillEmbeddingService)
    svc.update_skill_embeddings = AsyncMock(return_value=0)
    svc.embed_user_message = AsyncMock(return_value=[0.0, 0.1, 0.2, 0.3])
    svc.cosine_similarity = MagicMock(return_value=0.5)
    return svc


@pytest.fixture
def store_service(repos, embedding_service):
    """Real SkillStoreService backed by the test repos."""
    return SkillStoreService(
        skill_repo=repos.skill,
        lineage_repo=repos.lineage,
        embedding_service=embedding_service,
    )


@pytest.fixture
def metrics_service(repos, config, store_service):
    """Real SkillMetricsService — wired with the real store service.

    The ``evolution_service`` is wired in after construction via the
    ``set_evolution_service`` setter to break the construction cycle.
    """
    dispatcher = MagicMock()
    dispatcher.enqueue_capture = AsyncMock(return_value="job-captured")

    instance_repo = MagicMock()
    instance_repo.get = MagicMock(return_value=None)
    instance_repo.delete_metadata = MagicMock(return_value=None)
    instance_repo.set_metadata = MagicMock(return_value=None)

    agent_meta = SimpleNamespace(skill_injection=True)
    agent_id_resolver = MagicMock(return_value=agent_meta)

    svc = SkillMetricsService(
        usage_repo=repos.usage,
        skill_repo=repos.skill,
        trigger_repo=repos.trigger,
        ab_test_repo=repos.ab_test,
        config=config,
        instance_repo=instance_repo,
        evolution_service=None,  # wired below
        agent_id_resolver=agent_id_resolver,
    )
    svc._instance_repo = instance_repo  # expose for tests
    svc._agent_id_resolver = agent_id_resolver  # expose for tests
    svc.set_job_dispatcher(dispatcher)
    return svc


@pytest.fixture
def evolution_service(repos, embedding_service, config, metrics_service):
    """Real SkillEvolutionService — closed loop with metrics_service."""
    svc = SkillEvolutionService(
        skill_repo=repos.skill,
        lineage_repo=repos.lineage,
        usage_repo=repos.usage,
        embedding_service=embedding_service,
        metrics_service=metrics_service,
        ab_test_repo=repos.ab_test,
        config=config,
        llm_config={"base_url": "http://test", "api_key": "test", "model": "gpt-4o-mini"},
    )
    # Close the metrics-service ↔ evolution-service loop.
    metrics_service.set_evolution_service(svc)
    return svc


@pytest.fixture
def search_service(repos, embedding_service, config):
    """Real SkillSearchService — BM25 works on real data, embedding mocked."""
    return SkillSearchService(
        skill_repo=repos.skill,
        embedding_repo=repos.embedding,
        embedding_service=embedding_service,
        llm_config={"base_url": "http://test", "api_key": "test", "model": "gpt-4o-mini"},
        config=config,
    )


@pytest.fixture
def trigger_repo(repos):
    """Real SkillTriggerRepository for trigger endpoint tests."""
    return repos.trigger


# ---------------------------------------------------------------------------
# Dispatcher mock — captures job-enqueue calls
# ---------------------------------------------------------------------------


@pytest.fixture
def dispatcher():
    """Mocked SkillJobDispatcher — captures dispatch_fix calls."""
    d = MagicMock()
    d.enqueue_capture = AsyncMock(return_value="job-captured")
    d.dispatch_fix = AsyncMock(return_value="job-fix-123")
    d.enqueue_analysis = AsyncMock(return_value="job-analysis")
    d.enqueue_evolution = AsyncMock(return_value="job-evolution")
    d.enqueue_metric_scan = AsyncMock(return_value="job-scan")
    return d


# ---------------------------------------------------------------------------
# FastAPI app fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def app(
    store_service,
    search_service,
    metrics_service,
    evolution_service,
    trigger_repo,
    dispatcher,
):
    """FastAPI app with the skills router + all services wired.

    The router is mounted at ``/api/skills`` (matches production).
    Singleton state on the four ``create_service_dependency``
    accessors plus the two manual setters is reset on teardown
    so the next test gets a clean slate.
    """
    app = FastAPI()
    app.state.manager = MagicMock(is_write_paused=False)

    # Wire all six singletons the router reads.
    skills_router_module.get_store.set_service(store_service)  # type: ignore[attr-defined]
    skills_router_module.get_search.set_service(search_service)  # type: ignore[attr-defined]
    skills_router_module.get_metrics.set_service(metrics_service)  # type: ignore[attr-defined]
    skills_router_module.get_evolution.set_service(evolution_service)  # type: ignore[attr-defined]
    skills_router_module.set_skill_trigger_repo(trigger_repo)
    skills_router_module.set_skill_job_dispatcher(dispatcher)

    api = APIRouter(prefix="/api")
    api.include_router(skills_router_module.router)
    app.include_router(api)

    with TestClient(app) as client:
        yield client

    # Teardown — clear singleton state so the next test gets a fresh slate.
    skills_router_module.get_store.set_service(None)  # type: ignore[attr-defined]
    skills_router_module.get_search.set_service(None)  # type: ignore[attr-defined]
    skills_router_module.get_metrics.set_service(None)  # type: ignore[attr-defined]
    skills_router_module.get_evolution.set_service(None)  # type: ignore[attr-defined]
    skills_router_module.set_skill_trigger_repo(None)  # type: ignore[arg-type]
    skills_router_module.set_skill_job_dispatcher(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_skill_via_api(client, name="lifecycle-skill", project_id=None):
    """POST /api/skills and return the created skill dict."""
    response = client.post(
        "/api/skills",
        json={
            "name": name,
            "description": "test description",
            "content": "test content body for search",
            "project_id": project_id,
            "category": "workflow",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["skill"]


def _set_injected_metrics(metrics_service, instance_id, skill_ids):
    """Configure the metrics service's instance_repo to return a skill list."""
    inst = SimpleNamespace(
        instance_id=instance_id,
        instance_metadata={INJECTED_SKILLS_METADATA_KEY: list(skill_ids)},
    )
    metrics_service._instance_repo.get = MagicMock(return_value=inst)


def _seed_usage(repos, skill_id, instance_id, agent_id, applied=False, succeeded=True):
    """Insert a usage record directly via the repo."""
    return repos.usage.create(
        skill_id=skill_id,
        project_id="",
        instance_id=instance_id,
        agent_id=agent_id,
        task_message="seeded",
        selected=True,
        applied=applied,
        task_succeeded=succeeded,
        iterations=10,
        duration_seconds=120,
        fallback=False,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSkillLifecycleCreateReadUpdateDelete:
    """Create, list, get, update, deactivate through the REST API."""

    def test_create_skill_returns_201(self, app):
        skill = _create_skill_via_api(app)
        assert skill["name"] == "lifecycle-skill"
        assert skill["status"] == "active"
        assert skill["category"] == "workflow"

    def test_create_then_list(self, app):
        _create_skill_via_api(app, name="a-skill")
        _create_skill_via_api(app, name="b-skill")

        response = app.get("/api/skills")
        assert response.status_code == 200
        body = response.json()
        names = {it["name"] for it in body["items"]}
        assert {"a-skill", "b-skill"} <= names
        assert body["total"] >= 2

    def test_list_filters_by_project_id(self, app):
        _create_skill_via_api(app, name="proj1-skill", project_id="proj-1")
        _create_skill_via_api(app, name="proj2-skill", project_id="proj-2")

        response = app.get("/api/skills?project_id=proj-1")
        body = response.json()
        names = {it["name"] for it in body["items"]}
        assert "proj1-skill" in names
        assert "proj2-skill" not in names

    def test_get_skill_returns_full_body(self, app):
        created = _create_skill_via_api(app)
        skill_id = created["id"]

        response = app.get(f"/api/skills/{skill_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["skill"]["id"] == skill_id
        # GET /skills/{id} returns the full body (including content).
        assert body["skill"]["content"] == "test content body for search"

    def test_get_unknown_skill_returns_404(self, app):
        response = app.get("/api/skills/does-not-exist")
        assert response.status_code == 404

    def test_update_skill_description(self, app):
        created = _create_skill_via_api(app)
        skill_id = created["id"]

        response = app.put(
            f"/api/skills/{skill_id}",
            json={"description": "updated description"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["skill"]["description"] == "updated description"

    def test_update_skill_is_active_translates_to_status(self, app):
        created = _create_skill_via_api(app)
        skill_id = created["id"]

        response = app.put(
            f"/api/skills/{skill_id}",
            json={"is_active": False},
        )
        body = response.json()
        # Public is_active=false must flip the underlying status column.
        assert body["skill"]["status"] == "inactive"

    def test_delete_deactivates_skill(self, app):
        created = _create_skill_via_api(app)
        skill_id = created["id"]

        response = app.delete(f"/api/skills/{skill_id}")
        assert response.status_code == 200
        assert response.json() == {"deactivated": True}

        # GET still returns it (soft delete).
        response = app.get(f"/api/skills/{skill_id}")
        assert response.status_code == 200
        assert response.json()["skill"]["status"] == "inactive"

        # But it drops out of the active-only list.
        response = app.get("/api/skills?active_only=true")
        ids = {it["id"] for it in response.json()["items"]}
        assert skill_id not in ids


class TestSkillSearch:
    """POST /api/skills/search — three-stage pipeline via the API."""

    def test_search_finds_created_skill_via_bm25(self, app, repos, embedding_service):
        # Make embedding fetch return nothing so stage 2 falls back to BM25,
        # and stage 3 (LLM) degrades to BM25-only results.
        embedding_service.embed_user_message = AsyncMock(side_effect=RuntimeError("no api"))

        skill = _create_skill_via_api(app, name="debug-deploy")
        _create_skill_via_api(app, name="other-thing")

        response = app.post(
            "/api/skills/search",
            json={"query": "debug deploy", "max_results": 5},
        )
        assert response.status_code == 200
        body = response.json()
        # BM25 should match the term "debug" in the description / content.
        injected_names = [
            it["skill"].get("name") if isinstance(it.get("skill"), dict) else None
            for it in body["injected"]
        ]
        # "debug-deploy" shares the "debug" term with the query → score > 0.
        assert "debug-deploy" in injected_names or any(
            it.get("skill") and it["skill"].get("id") == skill["id"]
            for it in body["injected"]
        )


class TestSkillMetricsRecording:
    """Direct service-level usage + feedback + REST feedback endpoint."""

    @pytest.mark.asyncio
    async def test_record_task_completion_inserts_usage(
        self, app, metrics_service, repos,
    ):
        # Create a skill via the API to keep DB ↔ service consistent.
        created = _create_skill_via_api(app, name="usage-target")
        skill_id = created["id"]

        _set_injected_metrics(metrics_service, "inst-1", [skill_id])
        # Without a skill applied → gate fires, but we already have a record.
        repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        inserted = await metrics_service.record_task_completion(
            instance_id="inst-1",
            agent_id="developer",
            project_id="p-1",
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
            task_message="seeded",
        )
        assert inserted == 1
        # Counter was bumped on the skill row.
        after = repos.skill.get(skill_id)
        assert after.total_selections == 1
        assert after.total_completions == 1

    def test_feedback_endpoint_records_applied(self, app, metrics_service, repos):
        created = _create_skill_via_api(app, name="fb-target")
        skill_id = created["id"]

        # Seed a usage record so feedback has something to stamp.
        _seed_usage(repos, skill_id, "inst-fb", "developer", applied=False)

        response = app.post(
            f"/api/skills/{skill_id}/feedback?instance_id=inst-fb&agent_id=developer",
            json={"applied": True, "note": "great"},
        )
        assert response.status_code == 200
        assert response.json() == {"recorded": True}

        # Confirm the feedback was persisted on the usage record.
        latest = repos.usage.get_latest_for_skill_instance(
            skill_id=skill_id, instance_id="inst-fb"
        )
        assert latest.feedback_applied is True
        assert latest.feedback_note == "great"

    @pytest.mark.asyncio
    async def test_metrics_endpoint_returns_bundle(
        self, app, metrics_service, repos,
    ):
        created = _create_skill_via_api(app, name="metrics-target")
        skill_id = created["id"]

        # Drive a real record_task_completion so the denormalized
        # counters on the skill row are bumped. The metrics endpoint
        # reads the counters off the row (not off usage records).
        _set_injected_metrics(metrics_service, "inst-m1", [skill_id])
        repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        await metrics_service.record_task_completion(
            instance_id="inst-m1",
            agent_id="developer",
            project_id="p-m",
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
            task_message="seeded metrics",
        )

        response = app.get(f"/api/skills/{skill_id}/metrics")
        assert response.status_code == 200
        body = response.json()
        assert body["skill_id"] == skill_id
        assert body["found"] is True
        assert "stats" in body
        assert body["stats"]["total_selections"] == 1
        assert body["stats"]["total_completions"] == 1
        assert body["usage_recent_count"] >= 1

    def test_metrics_404_for_unknown_skill(self, app):
        response = app.get("/api/skills/does-not-exist/metrics")
        assert response.status_code == 404


class TestSkillFix:
    """POST /api/skills/{id}/fix → SkillJobDispatcher.dispatch_fix."""

    def test_fix_returns_job_id(self, app, dispatcher):
        created = _create_skill_via_api(app, name="fix-target")
        skill_id = created["id"]

        response = app.post(
            f"/api/skills/{skill_id}/fix",
            json={
                "issue_description": "The skill misses error handling",
                "suggested_fix": "Add try/except",
            },
        )
        assert response.status_code == 202
        body = response.json()
        assert body["job_id"] == "job-fix-123"

        # Dispatcher saw the right arguments.
        dispatcher.dispatch_fix.assert_awaited_once()
        kwargs = dispatcher.dispatch_fix.await_args.kwargs
        assert kwargs["skill_id"] == skill_id
        assert "error handling" in kwargs["issue_description"]
        assert "Add try/except" in kwargs["suggested_fix"]


class TestSkillABTesting:
    """Drive A/B test creation + resolution via the service layer."""

    @pytest.mark.asyncio
    async def test_ab_test_resolves_when_threshold_met(
        self, app, repos, evolution_service,
    ):
        # Create an A/B test with the old + new variants and bump
        # ``comparisons`` high enough that the threshold triggers.
        old_skill = _create_skill_via_api(app, name="ab-old")
        new_skill = _create_skill_via_api(app, name="ab-new")
        group = "ab-group-1"
        repos.skill.update(old_skill["id"], ab_test_group=group, status="ab_testing")
        repos.skill.update(new_skill["id"], ab_test_group=group, status="ab_testing")
        repos.ab_test.create_ab_test(group, old_skill["id"], new_skill["id"])

        # Seed many usage records for the new variant — make it win clearly.
        # completion_rate >= sample_size (10) means 100% wins.
        for i in range(15):
            _seed_usage(
                repos, new_skill["id"], f"inst-new-{i}", "developer",
                applied=True, succeeded=True,
            )
        # Old variant: no completions, 5 records.
        for i in range(5):
            _seed_usage(
                repos, old_skill["id"], f"inst-old-{i}", "developer",
                applied=True, succeeded=False,
            )
        # Comparisons must be >= ab_sample_size (10) to even enter resolution.
        for _ in range(10):
            repos.ab_test.increment_comparison(group)

        result = await evolution_service.check_ab_test_resolution(group)
        assert result["resolved"] is True
        # New variant has 100% completion rate → wins.
        assert result["winner_id"] == new_skill["id"]
        assert result["loser_id"] == old_skill["id"]
        # Threshold-met reason (difference = 1.0 >= 0.15).
        assert result["reason"] == "threshold_met"

        # The three concurrent writes (deactivate loser / resolve AB /
        # promote winner) run via ``asyncio.to_thread`` against the same
        # StaticPool connection — give the writer threads a moment to
        # fully commit before re-querying, then check the persisted state.
        time.sleep(0.05)
        loser = repos.skill.get(old_skill["id"])
        winner = repos.skill.get(new_skill["id"])
        # Loser is_active flipped to False (deactivate() also sets status='inactive',
        # but the most reliable invariant is the is_active flag).
        assert loser.is_active is False
        # Winner is promoted back to active and cleared from the AB group.
        assert winner.status == "active"
        assert winner.ab_test_group is None

    @pytest.mark.asyncio
    async def test_ab_test_extends_when_below_threshold(
        self, app, repos, evolution_service,
    ):
        old_skill = _create_skill_via_api(app, name="ext-old")
        new_skill = _create_skill_via_api(app, name="ext-new")
        group = "ab-group-2"
        repos.skill.update(old_skill["id"], ab_test_group=group, status="ab_testing")
        repos.skill.update(new_skill["id"], ab_test_group=group, status="ab_testing")
        repos.ab_test.create_ab_test(group, old_skill["id"], new_skill["id"])

        # Both variants: equal completion rate → difference = 0 < threshold.
        for i in range(10):
            _seed_usage(repos, new_skill["id"], f"ext-new-{i}", "developer",
                        applied=True, succeeded=True)
            _seed_usage(repos, old_skill["id"], f"ext-old-{i}", "developer",
                        applied=True, succeeded=True)
        for _ in range(10):
            repos.ab_test.increment_comparison(group)

        result = await evolution_service.check_ab_test_resolution(group)
        assert result["resolved"] is False
        assert result["reason"] == "extended"
        assert result["extension_count"] == 1

        # Persisted counter bumped.
        ab_row = repos.ab_test.get_by_group(group)
        assert ab_row.extension_count == 1

    def test_ab_test_endpoint_404_when_not_in_test(self, app):
        created = _create_skill_via_api(app, name="no-ab")
        response = app.post(f"/api/skills/{created['id']}/ab-test/resolve")
        assert response.status_code == 404
        assert "not in an A/B test" in response.json()["detail"]["error"]


class TestSkillCaptureE2E:
    """CAPTURED flow end-to-end — service layer with stubbed LLM."""

    @pytest.mark.asyncio
    async def test_capture_creates_new_skill(
        self, app, repos, evolution_service,
    ):
        # Stub the LLM call used inside _evolve_captured to return a
        # deterministic JSON body.
        capture_response = json.dumps({
            "name": "auto-captured-skill",
            "description": "extracted from a successful task",
            "content": "## Steps\n1. do X\n2. do Y",
        })
        evolution_service._call_llm = AsyncMock(return_value=capture_response)

        result = await evolution_service.capture_skill(
            instance_id="inst-c1",
            task_details={
                "instance_id": "inst-c1",
                "agent_id": "developer",
                "project_id": "p-c1",
                "task_message": "build a thing",
                "iterations": 10,
                "duration_seconds": 120,
                "task_succeeded": True,
            },
        )

        assert result["skipped"] is False
        assert "new_skill_id" in result

        # New skill row exists with the captured content.
        new_id = result["new_skill_id"]
        new_skill = repos.skill.get(new_id)
        assert new_skill is not None
        assert new_skill.name == "auto-captured-skill"
        assert new_skill.lineage_origin == "captured"
        assert new_skill.generation == 0
        assert new_skill.status == "active"
        assert new_skill.project_id == "p-c1"

        # Count grew in the project's skill list.
        project_skills = repos.skill.list(project_id="p-c1", active_only=False)[0]
        names = [s.name for s in project_skills]
        assert "auto-captured-skill" in names

    @pytest.mark.asyncio
    async def test_capture_handles_llm_failure_gracefully(
        self, app, repos, evolution_service,
    ):
        # LLM returns garbage — capture should still create a row, just with
        # a derived name from the response body.
        evolution_service._call_llm = AsyncMock(return_value="Some unparseable response text.")

        result = await evolution_service.capture_skill(
            instance_id="inst-c2",
            task_details={
                "task_message": "do something",
                "iterations": 10,
                "duration_seconds": 120,
                "agent_id": "developer",
                "project_id": "p-c2",
            },
        )
        # Layer 5 fallback: still creates a skill, name derived from raw text.
        assert result["skipped"] is False
        skill = repos.skill.get(result["new_skill_id"])
        assert skill is not None
        # Body of the skill is the raw LLM text.
        assert "unparseable" in skill.content


class TestSkillLineage:
    """GET /api/skills/{id}/lineage — parents/children tree."""

    def test_lineage_endpoint_returns_empty_tree(self, app):
        created = _create_skill_via_api(app, name="lin-root")
        response = app.get(f"/api/skills/{created['id']}/lineage")
        assert response.status_code == 200
        body = response.json()
        assert body["skill_id"] == created["id"]
        assert body["lineage"]["skill"] is not None
        # ``content`` is stripped — only metadata is returned.
        assert "content" not in body["lineage"]["skill"]
        # No parents / children for a fresh skill.
        assert body["lineage"]["lineage"]["parents"] == []
        assert body["lineage"]["lineage"]["children"] == []

    def test_lineage_after_evolution(self, app, repos):
        # Create root, then create a child via the lineage repo.
        root = _create_skill_via_api(app, name="lin-parent")
        child = repos.skill.create(
            name="lin-child",
            description="child",
            content="body",
            project_id=None,
            category="workflow",
            lineage_origin="evolved",
        )
        repos.lineage.create(
            child.id, root["id"],
            change_summary="derived",
            content_diff="",
        )

        response = app.get(f"/api/skills/{child.id}/lineage")
        assert response.status_code == 200
        body = response.json()
        # The child has the root as a parent.
        parent_ids = [p["parent_skill_id"] for p in body["lineage"]["lineage"]["parents"]]
        assert root["id"] in parent_ids

        # The root sees the child.
        response = app.get(f"/api/skills/{root['id']}/lineage")
        body = response.json()
        child_ids = [c["skill_id"] for c in body["lineage"]["lineage"]["children"]]
        assert child.id in child_ids


class TestSkillShare:
    """POST /api/skills/{id}/share — promote a project skill to global."""

    def test_share_clears_project_id(self, app, repos):
        created = _create_skill_via_api(app, name="share-target", project_id="proj-share")
        skill_id = created["id"]
        # Pre-condition: project_id is set.
        assert created["project_id"] == "proj-share"

        response = app.post(f"/api/skills/{skill_id}/share")
        assert response.status_code == 200
        body = response.json()
        # After share: project_id is None (global).
        assert body["skill"]["project_id"] is None

    def test_share_404_for_unknown_skill(self, app):
        response = app.post("/api/skills/missing/share")
        assert response.status_code == 404


class TestSkillTriggers:
    """Trigger CRUD via the REST API."""

    def test_create_list_update_delete_trigger(self, app, repos):
        # Create
        response = app.post(
            "/api/skills/triggers",
            json={
                "name": "low-completion",
                "condition_type": "low_completion_rate",
                "condition_json": {"threshold": 0.3, "min_selections": 5},
                "action": "analyze",
            },
        )
        assert response.status_code == 201
        trigger_id = response.json()["trigger"]["id"]

        # List
        response = app.get("/api/skills/triggers")
        assert response.status_code == 200
        items = response.json()["items"]
        assert any(t["id"] == trigger_id for t in items)

        # Update
        response = app.put(
            f"/api/skills/triggers/{trigger_id}",
            json={"is_enabled": False},
        )
        assert response.status_code == 200

        # Delete
        response = app.delete(f"/api/skills/triggers/{trigger_id}")
        assert response.status_code == 200
        assert response.json() == {"deleted": True}