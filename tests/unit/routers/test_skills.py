"""Unit tests for the ``/api/skills`` REST router.

Phase 6 of the Skill Evolution System. These tests pin the router
shape (path / method / status codes) and the DI behaviour using
mocked services — no DB, no LLM, no FastAPI lifespan. Real
service behaviour is exercised in ``tests/integration/``.

Coverage groups
----------------

1. ``TestSkillsRouterRegistration`` — pin the endpoint shape and
   response-model wiring.
2. ``TestSkillsEndpointsWithMockedService`` — every endpoint
   called against a mock service. Verifies request → service
   argument mapping and the HTTP-level error contracts (400 / 404
   / 503).
3. ``TestSkillsRouterServiceUnavailable`` — calling any endpoint
   before the DI setter runs returns 503 (covers both the
   ``create_service_dependency`` accessors and the manual
   ``set_skill_*`` setters).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wire_app(app: FastAPI) -> TestClient:
    """Return a TestClient with the skills router mounted at /api.

    The router is wired under ``/skills`` and is mounted by
    ``daemon/api.py`` under ``/api``. Tests reproduce that
    nesting so the path strings match production.
    """
    from daemon.routers import skills as skills_module
    api = APIRouter(prefix="/api")
    api.include_router(skills_module.router)
    app.include_router(api)
    return TestClient(app)


def _skill_dict(skill_id: str = "skill-abc", **overrides: Any) -> dict[str, Any]:
    """Return a serializable skill dict for assertions."""
    base = {
        "id": skill_id,
        "name": "test-skill",
        "description": "test description",
        "content": "test content body",
        "category": "workflow",
        "is_active": True,
        "status": "active",
        "lineage_origin": "imported",
        "generation": 0,
        "ab_test_group": None,
        "total_selections": 0,
        "total_applied": 0,
        "total_completions": 0,
        "total_fallbacks": 0,
        "consecutive_failures": 0,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "last_used_at": None,
        "project_id": None,
    }
    base.update(overrides)
    return base


def _skill_object(skill_id: str = "skill-abc", **overrides: Any) -> Any:
    """Return a duck-typed skill object exposing ``to_dict()``."""
    obj = MagicMock()
    obj.id = skill_id
    obj.to_dict = MagicMock(return_value=_skill_dict(skill_id, **overrides))
    return obj


# ---------------------------------------------------------------------------
# Group 1 — Endpoint registration
# ---------------------------------------------------------------------------


class TestSkillsRouterRegistration:
    """Pin the endpoint shape: path / method / response codes."""

    def test_router_is_exported_from_skills_module(self):
        from daemon.routers import skills

        assert hasattr(skills, "router"), "skills router must be exported"

    def test_router_has_skills_prefix(self):
        from daemon.routers.skills import router

        assert router.prefix == "/skills"

    def test_list_skills_endpoint_registered(self):
        from daemon.routers.skills import router

        # Router has prefix ``/skills`` so route paths start with it.
        paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert "/skills" in paths, "GET /skills (list) must be registered"
        assert "/skills/search" in paths, "POST /skills/search must be registered"
        assert "/skills/triggers" in paths, "GET /skills/triggers must be registered"

    def test_resource_endpoints_registered(self):
        from daemon.routers.skills import router

        expected_paths = {
            "/skills/{skill_id}",
            "/skills/{skill_id}/view",
            "/skills/{skill_id}/lineage",
            "/skills/{skill_id}/metrics",
            "/skills/{skill_id}/usage",
            "/skills/{skill_id}/feedback",
            "/skills/{skill_id}/fix",
            "/skills/{skill_id}/ab-test",
            "/skills/{skill_id}/ab-test/resolve",
            "/skills/{skill_id}/share",
            "/skills/{skill_id}/deactivate",
        }
        actual_paths = {r.path for r in router.routes if hasattr(r, "path")}
        missing = expected_paths - actual_paths
        assert not missing, f"missing skill sub-resource endpoints: {missing}"

    def test_list_skill_returns_404_when_service_missing(self):
        """Without a store service wired in, list_skills returns 503."""
        app = FastAPI()
        client = _wire_app(app)
        # No setter call → dependency fails.
        response = client.get("/api/skills")
        assert response.status_code == 503

    def test_create_skill_returns_503_when_service_missing(self):
        """POST /skills with no store service returns 503."""
        app = FastAPI()
        client = _wire_app(app)
        response = client.post(
            "/api/skills",
            json={"name": "x", "description": "y", "content": "z"},
        )
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# Group 2 — Endpoints with mocked services
# ---------------------------------------------------------------------------


@pytest.fixture
def skill_app_with_mock_services():
    """FastAPI app with the skills router + all four services mocked.

    The fixture builds a fresh app per test and tears down the
    singleton state on the four ``create_service_dependency``
    accessors (``get_store`` / ``get_search`` / ``get_metrics`` /
    ``get_evolution``) plus the two manual setters
    (``set_skill_trigger_repo`` / ``set_skill_job_dispatcher``).
    """
    app = FastAPI()
    app.state.manager = MagicMock(is_write_paused=False)
    client = _wire_app(app)

    from daemon.routers import skills as skills_module

    store = MagicMock()
    search = MagicMock()
    metrics = MagicMock()
    evolution = MagicMock()
    trigger_repo = MagicMock()
    dispatcher = MagicMock()
    usage_repo = MagicMock()

    # Async defaults
    store.create_skill = AsyncMock(return_value=_skill_object())
    store.get_skill = AsyncMock(return_value=_skill_object())
    store.list_skills = AsyncMock(return_value=([_skill_dict()], 1))
    store.update_skill = AsyncMock(return_value=_skill_object())
    store.deactivate_skill = AsyncMock(return_value=_skill_object())
    store.view_skill = AsyncMock(
        return_value={"skill": _skill_dict(), "lineage": {"parents": [], "children": []}}
    )

    search.search = AsyncMock(return_value={"injected": [], "low_match": []})

    metrics.get_skill_stats = AsyncMock(
        return_value={
            "total": 0,
            "selected": 0,
            "applied": 0,
            "completions": 0,
            "fallbacks": 0,
            "avg_iterations": 0.0,
            "avg_duration": 0.0,
            "completion_rate": 0.0,
            "fallback_rate": 0.0,
            "applied_rate": 0.0,
            "consecutive_failures": 0,
        }
    )
    metrics.record_feedback = AsyncMock(return_value=True)
    # Default for ``get_ab_comparison_stats`` — the ``ab-test/stats``
    # endpoint forwards the service output verbatim, so the test
    # only needs to assert that the dict lands inside the envelope.
    metrics.get_ab_comparison_stats = AsyncMock(
        return_value={
            "skill_id_a": "skill-old",
            "skill_id_b": "skill-new",
            "completion_rate_a": 0.4,
            "completion_rate_b": 0.6,
            "applied_rate_a": 0.5,
            "applied_rate_b": 0.7,
            "fallback_rate_a": 0.2,
            "fallback_rate_b": 0.1,
            "avg_iterations_a": 3.5,
            "avg_iterations_b": 2.5,
            "avg_duration_a": 12.0,
            "avg_duration_b": 8.0,
            "composite_score_a": 0.42,
            "composite_score_b": 0.61,
            "difference": 0.19,
            "comparisons": 12,
            "extension_count": 0,
            "sample_size": 10,
            "ready_to_resolve": False,
            "needs_more_data": False,
        }
    )

    evolution.get_skill_metrics = AsyncMock(
        return_value={
            "skill_id": "skill-abc",
            "found": True,
            "skill": _skill_dict(),
            "stats": {
                "total_selections": 0,
                "completion_rate": 0.0,
                "fallback_rate": 0.0,
            },
            "usage_recent_count": 0,
            "ab_test": None,
        }
    )
    evolution.check_ab_test_resolution = AsyncMock(
        return_value={
            "resolved": True,
            "winner_id": "skill-a",
            "loser_id": "skill-b",
            "reason": "threshold_met",
            "extension_count": 0,
        }
    )

    trigger_repo.list = MagicMock(return_value=[])
    trigger_repo.create = MagicMock(return_value=MagicMock(to_dict=MagicMock(return_value={"id": "tr-1"})))
    trigger_repo.update = MagicMock(return_value=MagicMock(to_dict=MagicMock(return_value={"id": "tr-1"})))
    trigger_repo.delete = MagicMock(return_value=True)

    dispatcher.dispatch_fix = AsyncMock(return_value="job-xyz")

    # Usage repo default — the ``usage-records`` endpoint runs
    # ``get_by_skill`` synchronously inside ``asyncio.to_thread`` so
    # the default ``MagicMock.return_value`` is replaced per-test.
    usage_repo.get_by_skill = MagicMock(return_value=([], 0))

    skills_module.get_store.set_service(store)
    skills_module.get_search.set_service(search)
    skills_module.get_metrics.set_service(metrics)
    skills_module.get_evolution.set_service(evolution)
    skills_module.set_skill_trigger_repo(trigger_repo)
    skills_module.set_skill_job_dispatcher(dispatcher)
    skills_module.set_skill_usage_repo(usage_repo)

    yield {
        "client": client,
        "store": store,
        "search": search,
        "metrics": metrics,
        "evolution": evolution,
        "trigger_repo": trigger_repo,
        "dispatcher": dispatcher,
        "usage_repo": usage_repo,
    }

    # Teardown — clear singleton state so the next test gets a fresh slate.
    skills_module.get_store.set_service(None)
    skills_module.get_search.set_service(None)
    skills_module.get_metrics.set_service(None)
    skills_module.get_evolution.set_service(None)
    skills_module.set_skill_trigger_repo(None)
    skills_module.set_skill_job_dispatcher(None)
    skills_module.set_skill_usage_repo(None)


class TestListSkills:
    def test_returns_flat_skill_array(self, skill_app_with_mock_services):
        """Phase 6 polish: list endpoint returns a flat array of skill
        dicts (matching the ``GET /api/work`` pattern). No
        ``{"items", "total"}`` envelope.
        """
        client = skill_app_with_mock_services["client"]
        response = client.get("/api/skills")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        assert len(body) == 1
        assert body[0]["name"] == "test-skill"

    def test_passes_active_only_query_to_service(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        # active_only defaults to True on the route — assert it's forwarded.
        response = client.get("/api/skills?active_only=false")
        assert response.status_code == 200
        store.list_skills.assert_awaited_once()
        kwargs = store.list_skills.await_args.kwargs
        assert kwargs.get("active_only") is False

    def test_passes_category_filter_as_post_filter(self, skill_app_with_mock_services):
        """category filter is applied client-side on the route."""
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.list_skills = AsyncMock(
            return_value=(
                [_skill_dict(skill_id="a", category="debug"), _skill_dict(skill_id="b", category="workflow")],
                2,
            )
        )
        response = client.get("/api/skills?category=workflow")
        assert response.status_code == 200
        body = response.json()
        # Post-filter keeps only the workflow row.
        assert {it["category"] for it in body} == {"workflow"}

    def test_500_when_service_raises(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.list_skills = AsyncMock(side_effect=RuntimeError("boom"))
        response = client.get("/api/skills")
        assert response.status_code == 500
        assert response.json()["detail"]["error"] == "Internal error"


class TestCreateSkill:
    def test_create_returns_201_with_skill(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        response = client.post(
            "/api/skills",
            json={
                "name": "new-skill",
                "description": "new description",
                "content": "new body",
                "project_id": "p1",
                "category": "workflow",
            },
        )
        assert response.status_code == 201
        body = response.json()
        # Phase 6 polish: skill object returned directly (no
        # ``{"skill": …}`` envelope).
        assert body["id"] == "skill-abc"
        store.create_skill.assert_awaited_once()

    def test_create_passes_fields_through(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        client.post(
            "/api/skills",
            json={"name": "n", "description": "d", "content": "c", "category": "debug"},
        )
        kwargs = store.create_skill.await_args.kwargs
        assert kwargs["name"] == "n"
        assert kwargs["content"] == "c"
        assert kwargs["category"] == "debug"

    def test_create_400_on_value_error(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.create_skill = AsyncMock(side_effect=ValueError("dup name"))
        response = client.post(
            "/api/skills",
            json={"name": "dup", "description": "d", "content": "c"},
        )
        assert response.status_code == 400
        assert "dup" in response.json()["detail"]["error"]


class TestGetSkill:
    def test_get_returns_skill(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        response = client.get("/api/skills/skill-abc")
        assert response.status_code == 200
        # Phase 6 polish: skill object returned directly (no
        # ``{"skill": …}`` envelope) and enriched with lineage +
        # metrics sub-payloads.
        body = response.json()
        assert body["id"] == "skill-abc"
        # The enriched detail includes the lineage + metrics bundles.
        assert "lineage" in body
        assert "metrics" in body

    def test_get_404_when_missing(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.get_skill = AsyncMock(return_value=None)
        response = client.get("/api/skills/missing-id")
        assert response.status_code == 404

    def test_get_400_on_blank_id(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        response = client.get("/api/skills/%20%20")
        assert response.status_code == 400
        assert "skill_id is required" in response.json()["detail"]["error"]


class TestUpdateSkill:
    def test_update_translates_is_active_to_status(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        response = client.put(
            "/api/skills/skill-abc",
            json={"is_active": False, "description": "new"},
        )
        assert response.status_code == 200
        kwargs = store.update_skill.await_args.kwargs
        # Public API uses is_active; service layer uses status.
        assert "is_active" not in kwargs
        assert kwargs["status"] == "inactive"
        assert kwargs["description"] == "new"

    def test_update_404_when_missing(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.update_skill = AsyncMock(return_value=None)
        response = client.put("/api/skills/missing", json={"description": "x"})
        assert response.status_code == 404


class TestDeleteSkill:
    def test_delete_returns_deactivated(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        response = client.delete("/api/skills/skill-abc")
        assert response.status_code == 200
        assert response.json() == {"deactivated": True}

    def test_delete_404_when_missing(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.deactivate_skill = AsyncMock(return_value=None)
        response = client.delete("/api/skills/missing")
        assert response.status_code == 404


class TestSearchSkills:
    def test_search_returns_dict(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        search = skill_app_with_mock_services["search"]
        search.search = AsyncMock(
            return_value={
                "injected": [{"skill": {"name": "x"}, "score": 0.9}],
                "low_match": [],
            }
        )
        response = client.post(
            "/api/skills/search",
            json={"query": "deploy", "max_results": 2},
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["injected"]) == 1
        search.search.assert_awaited_once()

    def test_search_validates_max_results(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        # max_results is bounded 1..20 in the schema.
        response = client.post(
            "/api/skills/search",
            json={"query": "x", "max_results": 99},
        )
        assert response.status_code == 422


class TestLineage:
    def test_lineage_strips_content(self, skill_app_with_mock_services):
        """Phase 6 polish: lineage returns the flat
        :class:`SkillLineage` shape directly (``skill_id``,
        ``parents``, ``children``, ``generation``, ``origin``).
        No doubly-nested ``.lineage.lineage`` envelope. ``content``
        is stripped from each parent / child row.
        """
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.view_skill = AsyncMock(
            return_value={
                "skill": _skill_dict(content="big body"),
                "lineage": {"parents": [], "children": []},
            }
        )
        response = client.get("/api/skills/skill-abc/lineage")
        assert response.status_code == 200
        body = response.json()
        assert body["skill_id"] == "skill-abc"
        # Flat shape: ``parents`` / ``children`` are at the top level.
        assert "parents" in body
        assert "children" in body
        assert "generation" in body
        assert "origin" in body

    def test_lineage_strips_content_from_parent_rows(self, skill_app_with_mock_services):
        """The ``content`` column is stripped from each parent /
        child row so the lineage payload stays compact."""
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.view_skill = AsyncMock(
            return_value={
                "skill": _skill_dict(),
                "lineage": {
                    "parents": [{"parent_skill_id": "p-1", "content": "secret"}],
                    "children": [],
                },
            }
        )
        response = client.get("/api/skills/skill-abc/lineage")
        assert response.status_code == 200
        body = response.json()
        assert all("content" not in p for p in body["parents"])

    def test_lineage_404_when_missing(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.view_skill = AsyncMock(return_value=None)
        response = client.get("/api/skills/missing/lineage")
        assert response.status_code == 404


class TestMetrics:
    def test_metrics_returns_flat_skill_metrics_dict(self, skill_app_with_mock_services):
        """Phase 6 polish: the metrics endpoint returns the
        :class:`SkillMetrics` shape directly — no
        ``{"skill_id", "found", "skill", "stats", "usage_recent_count", "ab_test"}``
        bundle.
        """
        client = skill_app_with_mock_services["client"]
        metrics = skill_app_with_mock_services["metrics"]
        metrics.get_skill_stats = AsyncMock(
            return_value={
                "total": 5,
                "selected": 5,
                "applied": 3,
                "completions": 2,
                "fallbacks": 1,
                "avg_iterations": 0.0,
                "avg_duration": 0.0,
                "completion_rate": 0.4,
                "fallback_rate": 0.2,
                "applied_rate": 0.6,
                "consecutive_failures": 0,
            }
        )
        response = client.get("/api/skills/skill-abc/metrics")
        assert response.status_code == 200
        body = response.json()
        # SkillMetrics shape — counters + derived rates directly.
        assert body["total"] == 5
        assert body["completions"] == 2
        assert body["completion_rate"] == 0.4
        metrics.get_skill_stats.assert_awaited_once()


class TestFeedback:
    def test_feedback_records_when_instance_id_omitted(self, skill_app_with_mock_services):
        """instance_id and agent_id are optional — when omitted the
        service has no usage record to stamp and returns False."""
        client = skill_app_with_mock_services["client"]
        metrics = skill_app_with_mock_services["metrics"]
        metrics.record_feedback = AsyncMock(return_value=False)
        response = client.post(
            "/api/skills/skill-abc/feedback",
            json={"applied": True, "note": "good"},
        )
        assert response.status_code == 200
        assert response.json() == {"recorded": False}
        metrics.record_feedback.assert_awaited_once()
        kwargs = metrics.record_feedback.await_args.kwargs
        # Router coerces missing IDs to empty strings so the
        # service-side query simply misses instead of raising.
        assert kwargs["skill_id"] == "skill-abc"
        assert kwargs["instance_id"] == ""
        assert kwargs["agent_id"] == ""

    def test_feedback_returns_recorded(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        metrics = skill_app_with_mock_services["metrics"]
        response = client.post(
            "/api/skills/skill-abc/feedback?instance_id=inst-1&agent_id=developer",
            json={"applied": True, "note": "helpful"},
        )
        assert response.status_code == 200
        assert response.json() == {"recorded": True}
        metrics.record_feedback.assert_awaited_once()
        kwargs = metrics.record_feedback.await_args.kwargs
        assert kwargs["skill_id"] == "skill-abc"
        assert kwargs["instance_id"] == "inst-1"
        assert kwargs["agent_id"] == "developer"
        assert kwargs["applied"] is True
        assert kwargs["note"] == "helpful"

    def test_feedback_recorded_false_when_no_usage(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        metrics = skill_app_with_mock_services["metrics"]
        metrics.record_feedback = AsyncMock(return_value=False)
        response = client.post(
            "/api/skills/skill-abc/feedback?instance_id=inst-1&agent_id=developer",
            json={"applied": True},
        )
        assert response.status_code == 200
        assert response.json() == {"recorded": False}


class TestFix:
    def test_fix_returns_job_id(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        dispatcher = skill_app_with_mock_services["dispatcher"]
        response = client.post(
            "/api/skills/skill-abc/fix",
            json={"issue_description": "broken", "suggested_fix": "add X"},
        )
        assert response.status_code == 202
        assert response.json() == {"job_id": "job-xyz"}
        dispatcher.dispatch_fix.assert_awaited_once()
        kwargs = dispatcher.dispatch_fix.await_args.kwargs
        assert kwargs["skill_id"] == "skill-abc"
        assert kwargs["issue_description"] == "broken"
        assert "add X" in kwargs["suggested_fix"]

    def test_fix_400_on_value_error(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        dispatcher = skill_app_with_mock_services["dispatcher"]
        dispatcher.dispatch_fix = AsyncMock(side_effect=ValueError("bad"))
        response = client.post(
            "/api/skills/skill-abc/fix",
            json={"issue_description": "broken"},
        )
        assert response.status_code == 400


class TestABTest:
    def test_get_ab_test_returns_dict(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        response = client.get("/api/skills/skill-abc/ab-test")
        assert response.status_code == 200
        body = response.json()
        assert body["skill_id"] == "skill-abc"
        assert body["ab_test"] is None

    def test_resolve_ab_test_returns_resolution(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        evolution = skill_app_with_mock_services["evolution"]
        # Need a skill in an A/B test to pass the group-id check.
        evolution.get_skill_metrics = AsyncMock(
            return_value={
                "skill_id": "skill-abc",
                "found": True,
                "skill": _skill_dict(),
                "stats": {},
                "usage_recent_count": 0,
                "ab_test": {"ab_test_group": "group-xyz", "comparisons": 12, "extension_count": 0},
            }
        )
        response = client.post("/api/skills/skill-abc/ab-test/resolve")
        assert response.status_code == 200
        body = response.json()
        assert body["ab_test_group"] == "group-xyz"
        assert body["resolved"] is True

    def test_resolve_ab_test_404_when_not_in_test(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        evolution = skill_app_with_mock_services["evolution"]
        evolution.get_skill_metrics = AsyncMock(
            return_value={
                "skill_id": "skill-abc",
                "found": True,
                "skill": _skill_dict(),
                "stats": {},
                "usage_recent_count": 0,
                "ab_test": None,
            }
        )
        response = client.post("/api/skills/skill-abc/ab-test/resolve")
        assert response.status_code == 404
        assert "not in an A/B test" in response.json()["detail"]["error"]

    def test_resolve_ab_test_404_when_skill_missing(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        evolution = skill_app_with_mock_services["evolution"]
        evolution.get_skill_metrics = AsyncMock(
            return_value={"skill_id": "missing", "found": False}
        )
        response = client.post("/api/skills/missing/ab-test/resolve")
        assert response.status_code == 404


class TestShare:
    def test_share_clears_project_id(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.update_skill = AsyncMock(return_value=_skill_object(skill_id="skill-abc", project_id=None))
        response = client.post("/api/skills/skill-abc/share")
        assert response.status_code == 200
        kwargs = store.update_skill.await_args.kwargs
        assert kwargs.get("project_id") is None

    def test_share_404_when_missing(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.update_skill = AsyncMock(return_value=None)
        response = client.post("/api/skills/missing/share")
        assert response.status_code == 404


class TestTriggers:
    def test_list_triggers_returns_items(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        repo = skill_app_with_mock_services["trigger_repo"]
        trigger = MagicMock()
        trigger.to_dict = MagicMock(return_value={"id": "tr-1", "name": "low_rate"})
        repo.list = MagicMock(return_value=[trigger])
        response = client.get("/api/skills/triggers")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["id"] == "tr-1"

    def test_create_trigger_returns_201(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        repo = skill_app_with_mock_services["trigger_repo"]
        response = client.post(
            "/api/skills/triggers",
            json={
                "name": "low-completion",
                "condition_type": "low_completion_rate",
                "condition_json": {"threshold": 0.3, "min_selections": 5},
                "action": "analyze",
            },
        )
        assert response.status_code == 201
        repo.create.assert_called_once()
        kwargs = repo.create.call_args.kwargs
        assert kwargs["name"] == "low-completion"
        assert kwargs["action"] == "analyze"

    def test_update_trigger_returns_dict(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        repo = skill_app_with_mock_services["trigger_repo"]
        trigger = MagicMock()
        trigger.to_dict = MagicMock(return_value={"id": "tr-1", "is_enabled": False})
        repo.update = MagicMock(return_value=trigger)
        response = client.put(
            "/api/skills/triggers/tr-1",
            json={"is_enabled": False},
        )
        assert response.status_code == 200

    def test_update_trigger_404(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        repo = skill_app_with_mock_services["trigger_repo"]
        repo.update = MagicMock(return_value=None)
        response = client.put(
            "/api/skills/triggers/missing",
            json={"is_enabled": False},
        )
        assert response.status_code == 404

    def test_delete_trigger_returns_deleted(self, skill_app_with_mock_services):
        client = skill_app_with_mock_services["client"]
        repo = skill_app_with_mock_services["trigger_repo"]
        response = client.delete("/api/skills/triggers/tr-1")
        assert response.status_code == 200
        assert response.json() == {"deleted": True}


# ---------------------------------------------------------------------------
# Group 2b — New endpoints: usage-records, ab-test/stats, lineage
# enrichment. Phase 7 additions.
# ---------------------------------------------------------------------------


def _usage_record_dict(**overrides: Any) -> dict[str, Any]:
    """Return a serializable SkillUsageRecord.to_dict() payload."""
    base = {
        "id": "rec-1",
        "skill_id": "skill-abc",
        "project_id": "proj-1",
        "instance_id": "inst-1",
        "agent_id": "developer",
        "task_message": "implement feature X",
        "selected": True,
        "applied": True,
        "task_succeeded": True,
        "iterations": 3,
        "duration_seconds": 12.5,
        "fallback": False,
        "feedback_applied": None,
        "feedback_note": "",
        "created_at": "2026-07-16T10:00:00+00:00",
    }
    base.update(overrides)
    return base


def _usage_record_object(**overrides: Any) -> Any:
    """Return a duck-typed SkillUsageRecord exposing ``to_dict()``."""
    obj = MagicMock()
    obj.to_dict = MagicMock(return_value=_usage_record_dict(**overrides))
    return obj


class TestUsageRecords:
    """``GET /api/skills/{skill_id}/usage-records`` — per-event timeline."""

    def test_envelope_shape(self, skill_app_with_mock_services):
        """The endpoint returns ``{skill_id, records, total,
        limit, offset}`` — no surprise envelope keys."""
        client = skill_app_with_mock_services["client"]
        repo = skill_app_with_mock_services["usage_repo"]
        record = _usage_record_object(id="rec-1")
        # Repo contract: returns ``(items, total)``.
        repo.get_by_skill = MagicMock(return_value=([record], 1))
        response = client.get("/api/skills/skill-abc/usage-records")
        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) == {"skill_id", "records", "total", "limit", "offset"}
        assert body["skill_id"] == "skill-abc"
        assert body["total"] == 1
        # Defaults
        assert body["limit"] == 50
        assert body["offset"] == 0

    def test_pagination_forwarded_to_repo(self, skill_app_with_mock_services):
        """``limit`` / ``offset`` query params are forwarded to the
        repo via ``get_by_skill(skill_id, limit, offset)``."""
        client = skill_app_with_mock_services["client"]
        repo = skill_app_with_mock_services["usage_repo"]
        repo.get_by_skill = MagicMock(return_value=([], 0))
        response = client.get(
            "/api/skills/skill-abc/usage-records?limit=25&offset=100"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["limit"] == 25
        assert body["offset"] == 100
        # Check repo was called with the forwarded args.
        repo.get_by_skill.assert_called_once()
        call_args = repo.get_by_skill.call_args
        assert call_args.args[0] == "skill-abc"
        assert call_args.args[1] == 25
        assert call_args.args[2] == 100

    def test_limit_clamped_to_200(self, skill_app_with_mock_services):
        """Asking for ``limit=10000`` clamps to ``200`` — no 422."""
        client = skill_app_with_mock_services["client"]
        repo = skill_app_with_mock_services["usage_repo"]
        repo.get_by_skill = MagicMock(return_value=([], 0))
        response = client.get(
            "/api/skills/skill-abc/usage-records?limit=10000"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["limit"] == 200
        # The repo must have been called with the clamped value,
        # not the raw 10000 the caller asked for.
        repo.get_by_skill.assert_called_once()
        assert repo.get_by_skill.call_args.args[1] == 200

    def test_record_payload_includes_core_fields(self, skill_app_with_mock_services):
        """Each record is a ``SkillUsageRecord.to_dict()`` payload
        — the FE timeline relies on these keys directly."""
        client = skill_app_with_mock_services["client"]
        repo = skill_app_with_mock_services["usage_repo"]
        record = _usage_record_object(id="rec-42")
        repo.get_by_skill = MagicMock(return_value=([record], 1))
        response = client.get("/api/skills/skill-abc/usage-records")
        assert response.status_code == 200
        records = response.json()["records"]
        assert len(records) == 1
        first = records[0]
        # Core signal booleans + identity fields.
        assert first["id"] == "rec-42"
        assert first["skill_id"] == "skill-abc"
        assert first["agent_id"] == "developer"
        assert first["task_message"] == "implement feature X"
        assert first["selected"] is True
        assert first["applied"] is True
        assert first["task_succeeded"] is True
        assert first["fallback"] is False

    def test_usage_records_503_when_repo_missing(self):
        """Without a usage repo wired in, the endpoint returns 503."""
        from daemon.routers import skills as skills_module

        # Reset the repo singleton so the 503 path is reached.
        skills_module.set_skill_usage_repo(None)  # type: ignore[arg-type]

        app = FastAPI()
        app.state.manager = MagicMock(is_write_paused=False)
        api = APIRouter(prefix="/api")
        api.include_router(skills_module.router)
        app.include_router(api)

        with TestClient(app) as client:
            response = client.get("/api/skills/skill-abc/usage-records")
            assert response.status_code == 503

        # Fixture teardown for ``skill_app_with_mock_services``
        # resets the singleton before the next test runs — no
        # manual restore needed here.

    def test_limit_zero_returns_422(self, skill_app_with_mock_services):
        """``limit=0`` is rejected by the ``Query(ge=1)`` validator
        on the route — callers must request at least one row.
        FastAPI surfaces the validation failure as 422."""
        client = skill_app_with_mock_services["client"]
        response = client.get(
            "/api/skills/skill-abc/usage-records?limit=0"
        )
        assert response.status_code == 422


class TestABTestStats:
    """``GET /api/skills/{skill_id}/ab-test/stats`` — per-variant metrics."""

    def test_active_group_returns_stats_envelope(
        self, skill_app_with_mock_services
    ):
        """When the skill is enrolled in an A/B test, the response
        carries the service's stats dict verbatim."""
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.get_skill = AsyncMock(
            return_value=_skill_object(skill_id="skill-abc", ab_test_group="group-7")
        )
        response = client.get("/api/skills/skill-abc/ab-test/stats")
        assert response.status_code == 200
        body = response.json()
        assert body["skill_id"] == "skill-abc"
        assert body["ab_test_group"] == "group-7"
        # Stats is the verbatim service output (mocked).
        assert isinstance(body["stats"], dict)
        assert body["stats"]["skill_id_a"] == "skill-old"

    def test_missing_group_returns_null_stats_envelope(
        self, skill_app_with_mock_services
    ):
        """When ``ab_test_group`` is None, the envelope returns
        ``ab_test_group=None, stats=None``."""
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.get_skill = AsyncMock(
            return_value=_skill_object(skill_id="skill-abc", ab_test_group=None)
        )
        response = client.get("/api/skills/skill-abc/ab-test/stats")
        assert response.status_code == 200
        body = response.json()
        assert body["skill_id"] == "skill-abc"
        assert body["ab_test_group"] is None
        assert body["stats"] is None

    def test_empty_string_group_returns_null_stats_envelope(
        self, skill_app_with_mock_services
    ):
        """An empty-string ``ab_test_group`` is treated the same as
        ``None`` — the router's ``if not ab_group`` check catches
        both, so the response envelope matches the null case."""
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.get_skill = AsyncMock(
            return_value=_skill_object(skill_id="skill-abc", ab_test_group="")
        )
        response = client.get("/api/skills/skill-abc/ab-test/stats")
        assert response.status_code == 200
        body = response.json()
        assert body["skill_id"] == "skill-abc"
        assert body["ab_test_group"] is None
        assert body["stats"] is None

    def test_stats_include_per_variant_metrics_and_sample_size(
        self, skill_app_with_mock_services
    ):
        """The stats dict carries the enriched per-variant fields
        (applied/fallback rate, avg iterations/duration) plus the
        ``sample_size`` knob."""
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        metrics = skill_app_with_mock_services["metrics"]
        store.get_skill = AsyncMock(
            return_value=_skill_object(skill_id="skill-abc", ab_test_group="group-7")
        )
        # Override the default mock with a known-value dict — only
        # assert that the service output is forwarded verbatim.
        metrics.get_ab_comparison_stats = AsyncMock(
            return_value={
                "applied_rate_a": 0.55,
                "applied_rate_b": 0.78,
                "fallback_rate_a": 0.20,
                "fallback_rate_b": 0.12,
                "avg_iterations_a": 4.0,
                "avg_iterations_b": 3.0,
                "avg_duration_a": 15.0,
                "avg_duration_b": 10.0,
                "sample_size": 10,
            }
        )
        response = client.get("/api/skills/skill-abc/ab-test/stats")
        assert response.status_code == 200
        stats = response.json()["stats"]
        # Per-variant a/b fields surfaced verbatim.
        assert stats["applied_rate_a"] == 0.55
        assert stats["applied_rate_b"] == 0.78
        assert stats["fallback_rate_a"] == 0.20
        assert stats["fallback_rate_b"] == 0.12
        assert stats["avg_iterations_a"] == 4.0
        assert stats["avg_iterations_b"] == 3.0
        assert stats["avg_duration_a"] == 15.0
        assert stats["avg_duration_b"] == 10.0
        assert stats["sample_size"] == 10

    def test_ab_test_stats_404_when_skill_missing(
        self, skill_app_with_mock_services
    ):
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.get_skill = AsyncMock(return_value=None)
        response = client.get("/api/skills/missing/ab-test/stats")
        assert response.status_code == 404
        assert response.json()["detail"]["skill_id"] == "missing"


class TestLineageEdgeEnrichment:
    """``GET /api/skills/{skill_id}/lineage`` — edge metadata enrichment.

    Each parent / child entry now carries
    ``change_summary`` / ``content_diff`` from the
    :class:`SkillLineage` edge plus the related Skill's
    metadata. The pre-enrichment shape (id-only rows) is
    preserved as a fallback for orphaned edges.
    """

    def test_parent_entries_include_change_summary_and_content_diff(
        self, skill_app_with_mock_services
    ):
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        # Side-effect the lookup so the parent + child resolve
        # to distinct skill bodies — the enrichment merges the
        # related Skill's metadata with the edge metadata.
        store.get_skill = AsyncMock(
            side_effect=lambda related_id: _skill_object(
                skill_id=related_id,
                name=f"parent-of-{related_id}",
                ab_test_group=None,
            )
        )
        store.view_skill = AsyncMock(
            return_value={
                "skill": _skill_dict(),
                "lineage": {
                    "parents": [
                        {
                            "parent_skill_id": "p-1",
                            "change_summary": "tightened fallback",
                            "content_diff": "+a -b",
                            "content": "should be stripped",
                        }
                    ],
                    "children": [],
                },
            }
        )
        response = client.get("/api/skills/skill-abc/lineage")
        assert response.status_code == 200
        body = response.json()
        assert len(body["parents"]) == 1
        parent = body["parents"][0]
        # Edge metadata survived the enrichment.
        assert parent["change_summary"] == "tightened fallback"
        assert parent["content_diff"] == "+a -b"
        # Related Skill metadata was merged in — the FE uses the
        # name to render the ancestor tile.
        assert parent["name"] == "parent-of-p-1"
        # The ``content`` body stays stripped.
        assert "content" not in parent
        # Edge id-key preserved.
        assert parent["parent_skill_id"] == "p-1"

    def test_child_entries_include_change_summary_and_content_diff(
        self, skill_app_with_mock_services
    ):
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.get_skill = AsyncMock(
            side_effect=lambda related_id: _skill_object(
                skill_id=related_id,
                name=f"child-{related_id}",
                ab_test_group=None,
            )
        )
        store.view_skill = AsyncMock(
            return_value={
                "skill": _skill_dict(),
                "lineage": {
                    "parents": [],
                    "children": [
                        {
                            "skill_id": "c-1",
                            "change_summary": "split retry path",
                            "content_diff": "@@ -1 +1 @@",
                            "content": "should be stripped",
                        }
                    ],
                },
            }
        )
        response = client.get("/api/skills/skill-abc/lineage")
        assert response.status_code == 200
        body = response.json()
        assert len(body["children"]) == 1
        child = body["children"][0]
        assert child["change_summary"] == "split retry path"
        assert child["content_diff"] == "@@ -1 +1 @@"
        assert child["name"] == "child-c-1"
        assert "content" not in child
        # The child edge's ``skill_id`` is preserved.
        assert child["skill_id"] == "c-1"

    def test_parent_entry_surfaces_edge_created_at(
        self, skill_app_with_mock_services
    ):
        """The edge's ``created_at`` is surfaced under a dedicated
        ``edge_created_at`` key on the enriched entry — distinct
        from the related Skill's own ``created_at`` (the edge is
        recorded at evolution time, the skill at import time)."""
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        edge_ts = "2025-01-15T10:30:00Z"
        store.get_skill = AsyncMock(
            side_effect=lambda related_id: _skill_object(
                skill_id=related_id,
                name=f"parent-of-{related_id}",
                ab_test_group=None,
            )
        )
        store.view_skill = AsyncMock(
            return_value={
                "skill": _skill_dict(),
                "lineage": {
                    "parents": [
                        {
                            "parent_skill_id": "p-1",
                            "change_summary": "cs",
                            "content_diff": "diff",
                            "content": "stripped",
                            "created_at": edge_ts,
                        }
                    ],
                    "children": [],
                },
            }
        )
        response = client.get("/api/skills/skill-abc/lineage")
        assert response.status_code == 200
        parent = response.json()["parents"][0]
        # Edge ``created_at`` is forwarded under the dedicated key.
        assert parent["edge_created_at"] == edge_ts
        # Skill metadata + edge metadata still merged in.
        assert parent["parent_skill_id"] == "p-1"
        assert parent["name"] == "parent-of-p-1"

    def test_malformed_edge_without_related_id_falls_back_to_stripped_dict(
        self, skill_app_with_mock_services
    ):
        """An edge missing its related-skill pointer
        (``parent_skill_id`` absent or empty) cannot be enriched —
        the helper short-circuits to a stripped dict so the row
        still surfaces without crashing on the lookup."""
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.view_skill = AsyncMock(
            return_value={
                "skill": _skill_dict(),
                "lineage": {
                    "parents": [
                        {
                            # Missing ``parent_skill_id`` entirely.
                            "change_summary": "orphan",
                            "content_diff": "-stale",
                            "content": "stripped",
                        }
                    ],
                    "children": [],
                },
            }
        )
        response = client.get("/api/skills/skill-abc/lineage")
        assert response.status_code == 200
        parent = response.json()["parents"][0]
        # Stripped fallback: edge metadata kept, ``content`` dropped.
        assert parent["change_summary"] == "orphan"
        assert parent["content_diff"] == "-stale"
        assert "content" not in parent
        # No related Skill lookup happened — ``name`` must be absent.
        assert "name" not in parent
        # ``get_skill`` should NOT have been called for this malformed
        # edge (no related_id to look up).
        store.get_skill.assert_not_called()

    def test_enrichment_lookup_failure_does_not_500(
        self, skill_app_with_mock_services
    ):
        """When the related-Skill lookup raises (DB blip, etc.),
        the enrichment falls back to the stripped edge dict instead
        of propagating the exception as a 500. The endpoint must
        remain best-effort for orphan-style failures."""
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.get_skill = AsyncMock(
            side_effect=RuntimeError("DB connection lost")
        )
        store.view_skill = AsyncMock(
            return_value={
                "skill": _skill_dict(),
                "lineage": {
                    "parents": [
                        {
                            "parent_skill_id": "p-1",
                            "change_summary": "cs",
                            "content_diff": "diff",
                            "content": "stripped",
                        }
                    ],
                    "children": [],
                },
            }
        )
        response = client.get("/api/skills/skill-abc/lineage")
        # Endpoint must NOT 500 on the lookup failure — the
        # enrichment helper's best-effort ``except Exception``
        # catches the error and falls back to the stripped dict.
        assert response.status_code == 200
        parent = response.json()["parents"][0]
        # Stripped fallback: edge id + metadata preserved, no
        # related-Skill fields (since the lookup failed).
        assert parent["parent_skill_id"] == "p-1"
        assert parent["change_summary"] == "cs"
        assert parent["content_diff"] == "diff"
        assert "name" not in parent
        assert "content" not in parent

    def test_existing_skill_fields_preserved_after_enrichment(
        self, skill_app_with_mock_services
    ):
        """The enrichment merges the related Skill's metadata, so
        the lineage payload now carries every Skill field the FE
        renders in ancestor / descendant tiles (status,
        generation, counters, …)."""
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        store.get_skill = AsyncMock(
            side_effect=lambda related_id: _skill_object(
                skill_id=related_id,
                name="ancestor",
                status="active",
                generation=1,
                ab_test_group=None,
                total_selections=42,
            )
        )
        store.view_skill = AsyncMock(
            return_value={
                "skill": _skill_dict(),
                "lineage": {
                    "parents": [
                        {
                            "parent_skill_id": "p-1",
                            "change_summary": "cs",
                            "content_diff": "diff",
                            "content": "stripped",
                        }
                    ],
                    "children": [],
                },
            }
        )
        response = client.get("/api/skills/skill-abc/lineage")
        assert response.status_code == 200
        parent = response.json()["parents"][0]
        # Pre-existing Skill fields still present.
        assert parent["name"] == "ancestor"
        assert parent["status"] == "active"
        assert parent["generation"] == 1
        assert parent["total_selections"] == 42
        # New edge metadata added.
        assert parent["change_summary"] == "cs"
        assert parent["content_diff"] == "diff"

    def test_orphaned_edge_falls_back_to_stripped_dict(
        self, skill_app_with_mock_services
    ):
        """When the related Skill has been deleted (FK cascade),
        the related_id lookup returns ``None`` — the enrichment
        should fall back to the edge-only dict so the response
        shape never explodes on missing rows."""
        client = skill_app_with_mock_services["client"]
        store = skill_app_with_mock_services["store"]
        # Related skill missing — orphaned edge scenario.
        store.get_skill = AsyncMock(return_value=None)
        store.view_skill = AsyncMock(
            return_value={
                "skill": _skill_dict(),
                "lineage": {
                    "parents": [
                        {
                            "parent_skill_id": "p-gone",
                            "change_summary": "lost skill",
                            "content_diff": "-only",
                            "content": "stripped",
                        }
                    ],
                    "children": [],
                },
            }
        )
        response = client.get("/api/skills/skill-abc/lineage")
        assert response.status_code == 200
        parent = response.json()["parents"][0]
        # Fallback dict: edge id preserved + edge metadata kept.
        assert parent["parent_skill_id"] == "p-gone"
        assert parent["change_summary"] == "lost skill"
        assert parent["content_diff"] == "-only"
        # Skill-only fields like ``name`` absent (no related Skill).
        assert "name" not in parent
        # ``content`` still stripped.
        assert "content" not in parent


# ---------------------------------------------------------------------------
# Group 3 — Service-unavailable paths
# ---------------------------------------------------------------------------


class TestSkillsRouterServiceUnavailable:
    """Pin the 503 contract when the DI singletons aren't wired in."""

    @pytest.fixture
    def fresh_app(self):
        """FastAPI app with the router mounted but no service wiring."""
        from daemon.routers import skills as skills_module
        from daemon.routers.skills import (
            get_evolution,
            get_metrics,
            get_search,
            get_store,
            set_skill_job_dispatcher,
            set_skill_trigger_repo,
        )

        # Reset singletons before each test.
        get_store.set_service(None)  # type: ignore[attr-defined]
        get_search.set_service(None)  # type: ignore[attr-defined]
        get_metrics.set_service(None)  # type: ignore[attr-defined]
        get_evolution.set_service(None)  # type: ignore[attr-defined]
        set_skill_trigger_repo(None)  # type: ignore[arg-type]
        set_skill_job_dispatcher(None)  # type: ignore[arg-type]

        app = FastAPI()
        app.state.manager = MagicMock(is_write_paused=False)
        api = APIRouter(prefix="/api")
        api.include_router(skills_module.router)
        app.include_router(api)
        yield app

        # Reset singletons after each test.
        get_store.set_service(None)  # type: ignore[attr-defined]
        get_search.set_service(None)  # type: ignore[attr-defined]
        get_metrics.set_service(None)  # type: ignore[attr-defined]
        get_evolution.set_service(None)  # type: ignore[attr-defined]
        set_skill_trigger_repo(None)  # type: ignore[arg-type]
        set_skill_job_dispatcher(None)  # type: ignore[arg-type]

    @pytest.fixture
    def fresh_client(self, fresh_app):
        with TestClient(fresh_app) as client:
            yield client

    def test_get_skill_503(self, fresh_client):
        r = fresh_client.get("/api/skills/x")
        assert r.status_code == 503

    def test_create_skill_503(self, fresh_client):
        r = fresh_client.post("/api/skills", json={"name": "x", "description": "d", "content": "c"})
        assert r.status_code == 503

    def test_update_skill_503(self, fresh_client):
        r = fresh_client.put("/api/skills/x", json={"description": "y"})
        assert r.status_code == 503

    def test_delete_skill_503(self, fresh_client):
        r = fresh_client.delete("/api/skills/x")
        assert r.status_code == 503

    def test_search_503(self, fresh_client):
        r = fresh_client.post("/api/skills/search", json={"query": "q"})
        assert r.status_code == 503

    def test_lineage_503(self, fresh_client):
        r = fresh_client.get("/api/skills/x/lineage")
        assert r.status_code == 503

    def test_metrics_503(self, fresh_client):
        r = fresh_client.get("/api/skills/x/metrics")
        assert r.status_code == 503

    def test_usage_503(self, fresh_client):
        r = fresh_client.get("/api/skills/x/usage")
        assert r.status_code == 503

    def test_feedback_503(self, fresh_client):
        # Feedback specifically needs instance/agent — but the service
        # check fires first.
        r = fresh_client.post(
            "/api/skills/x/feedback?instance_id=i&agent_id=a",
            json={"applied": True},
        )
        assert r.status_code == 503

    def test_fix_503_when_dispatcher_missing(self, fresh_client):
        r = fresh_client.post("/api/skills/x/fix", json={"issue_description": "x"})
        assert r.status_code == 503

    def test_ab_test_get_503(self, fresh_client):
        r = fresh_client.get("/api/skills/x/ab-test")
        assert r.status_code == 503

    def test_ab_test_resolve_503(self, fresh_client):
        r = fresh_client.post("/api/skills/x/ab-test/resolve")
        assert r.status_code == 503

    def test_share_503(self, fresh_client):
        r = fresh_client.post("/api/skills/x/share")
        assert r.status_code == 503

    def test_list_triggers_503(self, fresh_client):
        r = fresh_client.get("/api/skills/triggers")
        assert r.status_code == 503

    def test_create_trigger_503(self, fresh_client):
        r = fresh_client.post(
            "/api/skills/triggers",
            json={"name": "x", "condition_type": "y", "condition_json": {}, "action": "z"},
        )
        assert r.status_code == 503