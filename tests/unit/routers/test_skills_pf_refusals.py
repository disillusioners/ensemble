"""A14 ``pf:`` refusal pins — evolution tools + REST fix endpoint.

Commission: project-skill-loader, Phase 3 (task 11 / architect A14,
MUST 2026-10-31 — the P7 hole closure).

Surfaces pinned
---------------

* ``skill_analyze`` / ``skill_evolve`` / ``skill_get_metrics``
  (``daemon/tools/skill_evolution_tools.py`` — the skill-keeper lane
  is currently OFF, but on re-enable these would run evolution
  machinery against file skills): one-line refusal with git-PR
  guidance; the underlying service is NEVER invoked.
* ``POST /api/skills/{skill_id}/fix``
  (``daemon/routers/skills.py`` — dispatches with NO existence check
  today, so a ``pf:`` id would be admitted as a fix job whenever the
  lane is enabled): 400 with the mirrored guidance; the dispatcher is
  never invoked.
* Other REST id endpoints 404 cleanly on ``pf:`` (TEXT ids, no uuid
  validation — the store lookup simply misses).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

PF_ID = "pf:tester:blast-radius-leg"
INSTANCE_ID = "inst-a14-1"


# ============================================================
# Tool surface
# ============================================================


class TestEvolutionToolRefusals:
    @pytest.fixture
    def tools(self):
        manager = MagicMock()
        service = MagicMock()
        service.analyze_skill = AsyncMock(return_value={"should_evolve": False})
        service.evolve_skill = AsyncMock(return_value={"evolved": True})
        service.get_skill_metrics = AsyncMock(return_value={"rows": []})
        manager._skill_evolution_service = service
        built = {t.name: t for t in create_skill_evolution_tools(manager, INSTANCE_ID)}
        yield built, service

    async def test_skill_analyze_refuses_pf(self, tools):
        built, service = tools
        r = await built["skill_analyze"].ainvoke({"skill_id": PF_ID})
        assert "git PR" in r
        assert "P7" in r
        assert "skill_analyze" in r
        service.analyze_skill.assert_not_awaited()

    async def test_skill_evolve_refuses_pf(self, tools):
        built, service = tools
        r = await built["skill_evolve"].ainvoke(
            {"skill_id": PF_ID, "evolution_type": "FIX", "direction": "d"}
        )
        assert "git PR" in r and "P7" in r and "skill_evolve" in r
        service.evolve_skill.assert_not_awaited()

    async def test_skill_get_metrics_refuses_pf(self, tools):
        built, service = tools
        r = await built["skill_get_metrics"].ainvoke({"skill_id": PF_ID})
        assert "git PR" in r and "P7" in r and "skill_get_metrics" in r
        service.get_skill_metrics.assert_not_awaited()

    async def test_non_pf_ids_still_reach_the_service(self, tools):
        """The refusal is prefix-shaped only — ordinary ids pass
        through unchanged (regression guard)."""
        built, service = tools
        await built["skill_analyze"].ainvoke({"skill_id": "legacy-skill-id"})
        service.analyze_skill.assert_awaited_once()
        r = await built["skill_analyze"].ainvoke({"skill_id": "legacy-skill-id"})
        assert "git PR" not in r


# ============================================================
# REST surface
# ============================================================


@pytest.fixture
def rest_client():
    from daemon.routers import skills as skills_module

    app = FastAPI()
    app.state.manager = MagicMock(is_write_paused=False)
    api = APIRouter(prefix="/api")
    api.include_router(skills_module.router)
    app.include_router(api)
    client = TestClient(app)

    dispatcher = MagicMock()
    dispatcher.dispatch_fix = AsyncMock(return_value="job-should-not-exist")
    skills_module.set_skill_job_dispatcher(dispatcher)

    store = MagicMock()
    store.view_skill = AsyncMock(return_value=None)
    store.get_skill = AsyncMock(return_value=None)
    skills_module.get_store.set_service(store)

    metrics = MagicMock()
    metrics.get_skill_stats = AsyncMock(
        return_value={"total_applied": 0, "usage_count": 0}
    )
    skills_module.get_metrics.set_service(metrics)

    yield client, dispatcher, skills_module
    skills_module.set_skill_job_dispatcher(None)
    skills_module.get_store.set_service(None)
    skills_module.get_metrics.set_service(None)


class TestRestFixRefusal:
    @pytest.mark.parametrize(
        "skill_id",
        [PF_ID, "pf:foo", "pf:"],
        ids=["well-formed-pf-address", "malformed-pf-foo", "malformed-bare-prefix"],
    )
    def test_post_fix_pf_refused_400_dispatcher_never_invoked(
        self, rest_client, skill_id
    ):
        """Low-3 (pre-gate follow-up): the ``["pf:foo", "pf:"]``
        parametrize mirrored from the tool-path refusal
        (``test_skill_tools_pf_surface.py`` — A4) onto the REST pin,
        which previously covered only the well-formed
        ``pf:<ns>:<name>`` spelling. The REST refusal keys on the
        prefix, so every file-skill-shaped spelling must 400 with the
        same guidance and never reach ``dispatch_fix``."""
        client, dispatcher, _ = rest_client
        resp = client.post(
            f"/api/skills/{skill_id}/fix",
            json={"issue_description": "stale paths"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "git PR" in body["detail"]["error"]
        assert "P7" in body["detail"]["error"]
        dispatcher.dispatch_fix.assert_not_awaited()

    def test_post_fix_uuid_still_dispatches(self, rest_client):
        client, dispatcher, _ = rest_client
        resp = client.post(
            "/api/skills/12345678-1111-2222-3333-444444444444/fix",
            json={"issue_description": "x"},
        )
        assert resp.status_code == 202
        assert resp.json()["job_id"] == "job-should-not-exist"
        dispatcher.dispatch_fix.assert_awaited_once()


class TestRestOtherIdEndpoints404:
    """A14: TEXT ids, no uuid validation — every other id endpoint
    misses cleanly on ``pf:`` (404, never a 500/misroute). The
    ``/metrics`` endpoint's DOCUMENTED missing-skill shape is a 200
    zero-stat dict (``skills.py``: "a missing skill surfaces as a
    zero-stat dict rather than 404 here") — pinned as not-500 with
    empty stats."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/skills/{pid}"),
            ("GET", "/api/skills/{pid}/view"),
            ("GET", "/api/skills/{pid}/lineage"),
        ],
    )
    def test_id_endpoints_404_on_pf(self, rest_client, method, path):
        client, _, _ = rest_client
        url = path.format(pid=PF_ID.replace(":", "%3A"))
        resp = client.request(method, url)
        assert resp.status_code == 404, (url, resp.status_code, resp.text[:200])

    def test_metrics_endpoint_zero_stats_not_500(self, rest_client):
        client, _, _ = rest_client
        url = f"/api/skills/{PF_ID.replace(':', '%3A')}/metrics"
        resp = client.get(url)
        assert resp.status_code != 500
        assert resp.status_code == 200
        assert "total_applied" in resp.json()  # documented zero-stat shape


# ---- sink sandbox (house safe redirect) ----
# The capture-lane sink resolves its data dir from a PROCESS-GLOBAL
# override.  A fixture that installs a redirect and then restores the
# override to ``None`` re-exposes the checkout's REAL data dir for every
# later write (the order-dependent sink leak).  This fixture NEVER
# restores ``None``: it restores the SAFE session scratch dir and runs
# the shared-path sentinel.  See tests/helpers/skill_sink_redirect.py.
from daemon.services import skill_capture_observability as observability
from tests.helpers.skill_sink_redirect import (
    lane_test_end,
    lane_test_start,
    restore_safe_sink_redirect,
    snapshot_real_sink_dir,
)


@pytest.fixture(autouse=True)
def _sink_sandbox(tmp_path, request):
    """Redirect the capture sink into ``tmp_path``; never restore None."""
    lane_test_start(request.node.nodeid)
    before = snapshot_real_sink_dir()
    observability.configure_sink(tmp_path)
    observability.reset_counters()
    observability.set_anomaly_reporter(None)
    try:
        yield tmp_path
    finally:
        # NEVER restore to None — that re-exposes the checkout's REAL
        # data dir for every later write (the order-dependent sink leak).
        restore_safe_sink_redirect()
        observability.reset_counters()
        observability.set_anomaly_reporter(None)
        lane_test_end(request.node.nodeid, before)
