"""HTTP-level integration tests for the ``search`` query parameter on
``GET /api/instances?search=...``.

The repository-layer behaviour (``SQLModelInstanceRepository.list`` with
``search``) is already locked in by ``tests/test_instance_search.py``. This
file goes ONE layer up and exercises the full router → manager → service →
repository path via FastAPI's ``TestClient`` against a real in-memory SQLite
engine.

Coverage (mirrors the task's edge-case list):

    * No ``search`` param at all  → backward compat (all instances returned)
    * Empty ``search`` (``?search=``) → no filtering (same as omitted)
    * Special chars ``%``, ``_``, ``\\`` are escaped and treated as literals
    * Case-insensitive matching in both directions
        (uppercase query ↔ lowercase data, lowercase query ↔ uppercase data)
    * ``search`` + ``project_id`` combined
    * ``search`` + pagination (``limit`` / ``offset``)
    * ``search`` + ``exclude_kb`` combined

The test wiring follows ``tests/test_instance_hard_delete.py::TestDeleteEndpoint``:
a real ``SQLModelInstanceRepository`` + ``InstanceLifecycleService`` is stood
up behind a ``MagicMock`` manager so the router's ``_get_manager(request)``
returns a manager whose ``list_instances`` actually hits the engine.

Run standalone:

    timeout 300 .venv/bin/pytest tests/test_instance_search_api.py --tb=short -q
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.instance_ui_prefs.repository import (
    InstanceUiPrefsRepository,
)
from daemon.routers.instances import router as instances_router
from daemon.services.instance_lifecycle import InstanceLifecycleService


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine with FK enforcement enabled.

    Mirrors ``tests/test_instance_hard_delete.py::engine``. ``StaticPool``
    keeps a single connection alive so reads after writes always see the
    latest data, even when the writer ran on a different thread.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _make_instance(
    instance_id: str,
    *,
    agent_id: str = "developer",
    agent_dir: str = "agents/coder",
    agent_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    parent_id: str | None = None,
    project_id: str | None = None,
    status: str = "idle",
) -> Instance:
    """Build an ``Instance`` row (NOT committed) mirroring the seed shape
    used by ``test_instance_search.py`` and the repo's own tests.

    ``agent_name`` defaults to the Title-Case basename of ``agent_dir`` —
    exactly what :func:`daemon.repositories.instance.repository.get_agent_name`
    produces — so tests can assert against ``agent_name`` without a surprise.
    """
    now = datetime.now(timezone.utc).isoformat()
    return Instance(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=agent_dir,
        agent_name=agent_name if agent_name is not None else agent_dir.rsplit("/", 1)[-1].title(),
        parent_id=parent_id,
        status=status,
        version=1,
        instance_metadata=metadata or {},
        created_at=now,
        updated_at=now,
        project_id=project_id,
    )


@pytest.fixture
def seed_instances(engine):
    """Seed the engine with a known set of instances.

    All are ROOT instances (parent_id IS NULL) because the API endpoint
    paginates by root. ``project_id`` varies so ``search`` + project filtering
    can be tested.

    Layout (all roots):
        id          title            agent_id      agent_name   project_id
        ---         -----            --------      ----------   ----------
        alpha       "Alpha Run"      developer     Coder        proj-1
        beta        "Beta Run"       fixer         Fixer        proj-1
        gamma       "OTHER"          reviewer      Reviewer     proj-2
        delta       (no title)       developer     Wanderer     proj-1
        kb-1        "Alpha Memory"   experiencer   Kb           proj-1  (KB agent)
    """
    ids = ["alpha", "beta", "gamma", "delta", "kb-1"]
    with Session(engine) as s:
        s.add(_make_instance("alpha", agent_id="developer", agent_dir="agents/coder",
                             metadata={"title": "Alpha Run"}, project_id="proj-1"))
        s.add(_make_instance("beta", agent_id="fixer", agent_dir="agents/fixer",
                             metadata={"title": "Beta Run"}, project_id="proj-1"))
        s.add(_make_instance("gamma", agent_id="reviewer", agent_dir="agents/reviewer",
                             metadata={"title": "OTHER"}, project_id="proj-2"))
        s.add(_make_instance("delta", agent_id="developer", agent_dir="agents/wanderer",
                             project_id="proj-1"))
        s.add(_make_instance("kb-1", agent_id="experiencer", agent_dir="agents/kb",
                             metadata={"title": "Alpha Memory"}, project_id="proj-1"))
        s.commit()
    # Return the literal IDs (captured before the session closes to avoid
    # DetachedInstanceError when the test reads the fixture's return value).
    return ids


@pytest.fixture
def app_and_client(engine):
    """Build a FastAPI ``TestClient`` wired to a real repo + lifecycle service.

    The router calls ``_get_manager(request).list_instances(...)``. We give
    it a ``MagicMock`` manager whose ``list_instances`` delegates to a real
    ``InstanceLifecycleService`` (which in turn hits the real repo + engine).
    The manager also exposes the UI-prefs repo used by the list endpoint's
    post-fetch merge step.
    """
    from unittest.mock import MagicMock

    repo = SQLModelInstanceRepository(engine)
    ui_prefs_repo = InstanceUiPrefsRepository(engine)

    manager = MagicMock()
    manager.is_write_paused = False
    manager.engine = engine
    manager._instance_repository = repo
    manager._instance_ui_prefs_repo = ui_prefs_repo

    service = InstanceLifecycleService(
        manager=manager,
        cancellation_service=MagicMock(),
    )
    manager.list_instances = service.list_instances

    app = FastAPI()
    app.include_router(instances_router)
    app.state.manager = manager

    with TestClient(app) as client:
        yield client


def _ids(resp_json: dict) -> list[str]:
    """Extract sorted instance_ids from a list response body."""
    return sorted(i["instance_id"] for i in resp_json["instances"])


# ─────────────────────────────────────────────────────────────────────────────
# Tests — backward-compat / no-op search
# ─────────────────────────────────────────────────────────────────────────────


class TestSearchBackwardCompat:
    """Omitting ``search`` and passing ``?search=`` must NOT filter anything.

    ``exclude_kb`` defaults to ``True`` on the endpoint, so the seeded KB
    instance (``kb-1``) is excluded from these counts. The non-KB set is
    alpha, beta, gamma, delta = 4 roots.
    """

    def test_no_search_param_returns_all(self, app_and_client, seed_instances):
        resp = app_and_client.get("/instances")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 4 non-KB roots (kb-1 is excluded by default exclude_kb=True)
        assert body["total"] == 4
        assert _ids(body) == ["alpha", "beta", "delta", "gamma"]

    def test_empty_search_returns_all(self, app_and_client, seed_instances):
        resp = app_and_client.get("/instances", params={"search": ""})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 4
        assert _ids(body) == ["alpha", "beta", "delta", "gamma"]


# ─────────────────────────────────────────────────────────────────────────────
# Tests — field matching (title / agent_name / agent_id)
# ─────────────────────────────────────────────────────────────────────────────


class TestSearchFieldMatching:
    """``search`` is matched (case-insensitively) against title, agent_name,
    and agent_id. KB exclusion still applies by default.
    """

    def test_search_matches_title_substring(self, app_and_client, seed_instances):
        resp = app_and_client.get("/instances", params={"search": "alpha"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # "alpha" matches alpha (title "Alpha Run") only. kb-1 has "Alpha
        # Memory" but is a KB agent and excluded by default.
        assert body["total"] == 1
        assert _ids(body) == ["alpha"]

    def test_search_matches_agent_name(self, app_and_client, seed_instances):
        # agent_name for gamma is "Reviewer"
        resp = app_and_client.get("/instances", params={"search": "reviewer"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert _ids(body) == ["gamma"]

    def test_search_matches_agent_id(self, app_and_client, seed_instances):
        # "fixer" is beta's agent_id (and agent_name Fixer, both match)
        resp = app_and_client.get("/instances", params={"search": "fixer"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert _ids(body) == ["beta"]


# ─────────────────────────────────────────────────────────────────────────────
# Tests — case-insensitivity (both directions)
# ─────────────────────────────────────────────────────────────────────────────


class TestSearchCaseInsensitivity:
    """ILIKE must be case-insensitive in both directions."""

    def test_uppercase_query_matches_lowercase_title(self, app_and_client, seed_instances):
        # title "Alpha Run" → query "ALPHA"
        resp = app_and_client.get("/instances", params={"search": "ALPHA"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert _ids(body) == ["alpha"]

    def test_lowercase_query_matches_uppercase_data(self, engine, app_and_client):
        """An uppercase title must be matched by a lowercase query."""
        with Session(engine) as s:
            s.add(_make_instance(
                "big-title", agent_id="dev", agent_dir="agents/coder",
                metadata={"title": "UPPERCASE TITLE"},
            ))
            s.commit()
        resp = app_and_client.get("/instances", params={"search": "uppercase"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert _ids(body) == ["big-title"]

    def test_mixed_case_query_matches_title(self, app_and_client, seed_instances):
        # "Run" matches alpha ("Alpha Run") + beta ("Beta Run")
        resp = app_and_client.get("/instances", params={"search": "RuN"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert _ids(body) == ["alpha", "beta"]


# ─────────────────────────────────────────────────────────────────────────────
# Tests — special character escaping
# ─────────────────────────────────────────────────────────────────────────────


class TestSearchSpecialChars:
    """``%``, ``_``, ``\\`` must be escaped and treated as literals."""

    def test_percent_is_literal(self, engine, app_and_client):
        """``50%`` must only match the literal '50%', not '50xyz'."""
        with Session(engine) as s:
            s.add(_make_instance(
                "literal", agent_id="x", agent_dir="agents/x",
                metadata={"title": "50% off sale"},
            ))
            s.add(_make_instance(
                "fuzzy", agent_id="x", agent_dir="agents/y",
                metadata={"title": "50xyz off sale"},
            ))
            s.commit()
        resp = app_and_client.get("/instances", params={"search": "50%"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert _ids(body) == ["literal"]

    def test_underscore_is_literal(self, engine, app_and_client):
        """``a_b`` must only match the literal 'a_b', not 'axb'."""
        with Session(engine) as s:
            s.add(_make_instance(
                "literal", agent_id="x", agent_dir="agents/x",
                metadata={"title": "value a_b here"},
            ))
            s.add(_make_instance(
                "fuzzy", agent_id="x", agent_dir="agents/y",
                metadata={"title": "value axb here"},
            ))
            s.commit()
        resp = app_and_client.get("/instances", params={"search": "a_b"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert _ids(body) == ["literal"]

    def test_backslash_is_literal(self, engine, app_and_client):
        """A backslash in the query must match a backslash in the data."""
        with Session(engine) as s:
            s.add(_make_instance(
                "literal", agent_id="x", agent_dir="agents/x",
                metadata={"title": r"path\to\file"},
            ))
            s.add(_make_instance(
                "other", agent_id="x", agent_dir="agents/y",
                metadata={"title": r"pathXtoXfile"},
            ))
            s.commit()
        resp = app_and_client.get("/instances", params={"search": r"\to"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert _ids(body) == ["literal"]


# ─────────────────────────────────────────────────────────────────────────────
# Tests — search combined with other filters
# ─────────────────────────────────────────────────────────────────────────────


class TestSearchCombined:
    """``search`` AND other filters (project_id, exclude_kb, pagination)."""

    def test_search_with_project_id(self, app_and_client, seed_instances):
        # "alpha" matches alpha (proj-1) and gamma has no 'alpha' → proj-1
        # filter keeps only alpha. kb-1 is also proj-1 + 'Alpha Memory' but
        # excluded by default exclude_kb=True.
        resp = app_and_client.get(
            "/instances", params={"search": "alpha", "project_id": "proj-1"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert _ids(body) == ["alpha"]

    def test_search_with_project_id_other_project(self, app_and_client, seed_instances):
        # gamma is proj-2 + title "OTHER" — searching "other" in proj-2.
        resp = app_and_client.get(
            "/instances", params={"search": "other", "project_id": "proj-2"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert _ids(body) == ["gamma"]

    def test_search_excludes_kb_by_default(self, app_and_client, seed_instances):
        # "alpha" matches both alpha (developer) and kb-1 (experiencer, title
        # "Alpha Memory"). Default exclude_kb=True hides kb-1.
        resp = app_and_client.get("/instances", params={"search": "alpha"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert _ids(body) == ["alpha"]

    def test_search_includes_kb_when_exclude_kb_false(
        self, app_and_client, seed_instances,
    ):
        # Same query but exclude_kb=False → kb-1 also matches.
        resp = app_and_client.get(
            "/instances", params={"search": "alpha", "exclude_kb": "false"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert _ids(body) == ["alpha", "kb-1"]

    def test_search_with_pagination(self, engine, app_and_client):
        """Pagination (limit/offset) must apply AFTER the search filter."""
        with Session(engine) as s:
            for i in range(5):
                s.add(_make_instance(
                    f"hit-{i}", agent_id="dev", agent_dir="agents/coder",
                    metadata={"title": f"Hit {i}"},
                ))
            s.add(_make_instance(
                "miss-1", agent_id="dev", agent_dir="agents/coder",
                metadata={"title": "Other 1"},
            ))
            s.commit()

        # total of search="hit" is 5
        page1 = app_and_client.get(
            "/instances", params={"search": "hit", "limit": 2, "offset": 0},
        )
        page2 = app_and_client.get(
            "/instances", params={"search": "hit", "limit": 2, "offset": 2},
        )
        page3 = app_and_client.get(
            "/instances", params={"search": "hit", "limit": 2, "offset": 4},
        )
        for r in (page1, page2, page3):
            assert r.status_code == 200, r.text

        b1, b2, b3 = page1.json(), page2.json(), page3.json()
        # Every page reports the SAME filtered total.
        assert b1["total"] == 5
        assert b2["total"] == 5
        assert b3["total"] == 5
        # Page sizes behave.
        assert len(b1["instances"]) == 2
        assert len(b2["instances"]) == 2
        assert len(b3["instances"]) == 1
        # has_more flag is correct.
        assert b1["has_more"] is True
        assert b2["has_more"] is True
        assert b3["has_more"] is False
        # The union of all pages covers every matching id exactly once.
        all_ids = _ids(b1) + _ids(b2) + _ids(b3)
        assert sorted(all_ids) == [f"hit-{i}" for i in range(5)]

    def test_search_no_match_returns_empty(self, app_and_client, seed_instances):
        resp = app_and_client.get(
            "/instances", params={"search": "zzz-no-such-thing"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 0
        assert body["instances"] == []
