"""REST-invisibility + REST-list pins (Phase-2 task 10; architect A6 + leader ruling D1).

v1 contract (NFR-COMPAT-3 annotated SUPERSEDED in v1): ``POST
/api/skills/search`` serves **ZERO file skills**. The router has no
instance context and passes a caller-supplied ``project_id`` straight
into :meth:`SkillSearchService.search`; the file-source resolver
(``resolve_active_project(instance_id=None, explicit_project_id=...)``)
therefore REFUSES the file corpus for that call (A6: explicit may only
confirm, never widen). A server-side REST binding (auth principal →
project) is the v2 path — leader ruling D1.

The pins are deliberately paired with a POSITIVE CONTROL so the
invisibility assertion cannot pass vacuously: the same service, called
directly WITH an instance id, DOES surface the canon file skill.

``GET /api/skills`` stays DB-only via the ``_LIST_SKILL_FIELDS``
allowlist strip (advisory S2): the projection can never carry the
fusion's additive keys or a ``pf:`` row.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

import daemon.services.skill_search_service as sss_module
from daemon.services.skill_search_service import SkillSearchService
from daemon.services.skill_store_service import _LIST_SKILL_FIELDS

from tests.helpers.project_skill_source_fixtures import (
    TEST_PROJECT_ID,
    canon_root,  # noqa: F401 — fixture re-exported for pytest
    make_service,
    sqlite_engine,  # noqa: F401 — fixture re-exported for pytest
)

INSTANCE_ID = "inst-rest-0001"


# ============================================================
# Helpers
# ============================================================


def _chat_response(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


def install_selector(monkeypatch, payload: dict) -> None:
    import json

    def _invoke(fn, cfg):
        return _chat_response(json.dumps(payload))

    monkeypatch.setattr(
        sss_module, "invoke_raw_with_failover", _invoke, raising=True
    )


class _EmptyRepo:
    def list(self, project_id=None, active_only=True, limit=200):
        return ([], 0)


class _EmptyEmbeddingRepo:
    def get_by_skill(self, skill_id):
        return []


class _DownEmbedding:
    async def embed_user_message(self, message):
        raise RuntimeError("embedding endpoint down")

    def cosine_similarity(self, a, b):
        return 0.0


def _build_service(file_source) -> SkillSearchService:
    return SkillSearchService(
        skill_repo=_EmptyRepo(),
        embedding_repo=_EmptyEmbeddingRepo(),
        embedding_service=_DownEmbedding(),
        llm_config={"selector_model": "quick", "base_url": "http://127.0.0.1:9/v1"},
        config=SimpleNamespace(bm25_top_k=10, llm_select_top_k=5, max_inject_skills=2),
        file_source=file_source,
    )


def _file_backed_source(engine, root):
    from tests.helpers.project_skill_source_fixtures import (
        FakeInstance,
        FakeInstanceRepository,
        FakeProject,
        FakeProjectRepository,
    )

    instance_repo = FakeInstanceRepository(
        {
            INSTANCE_ID: FakeInstance(
                instance_id=INSTANCE_ID,
                instance_metadata={"project_id": TEST_PROJECT_ID},
                project_id=TEST_PROJECT_ID,
            )
        }
    )
    project_repo = FakeProjectRepository(
        [FakeProject(project_id=TEST_PROJECT_ID, main_directory=root)]
    )
    return make_service(
        sqlite_engine=engine,
        instance_repo=instance_repo,
        project_repo=project_repo,
    )


@pytest.fixture
def wired_client():
    """FastAPI app with the skills router mounted at ``/api`` (the
    production nesting). DI services are cleared on teardown."""
    from daemon.routers import skills as skills_module

    app = FastAPI()
    app.state.manager = MagicMock(is_write_paused=False)
    api = APIRouter(prefix="/api")
    api.include_router(skills_module.router)
    app.include_router(api)
    client = TestClient(app)
    yield client, skills_module
    skills_module.get_search.set_service(None)
    skills_module.get_store.set_service(None)


# ============================================================
# Search — REST is file-blind
# ============================================================


class TestRestSearchInvisibility:
    async def test_positive_control_instance_lane_sees_the_canon(
        self, canon_root, sqlite_engine, monkeypatch  # noqa: F811
    ):
        """Sanity: the SAME fixture DOES yield file skills on the
        instance lane (so the HTTP pin below is not vacuous)."""
        install_selector(
            monkeypatch, {"selected": [{"name": "alpha-skill", "score": 0.9}]}
        )
        service = _build_service(_file_backed_source(sqlite_engine, canon_root))
        result = await service.search(
            "alpha", project_id=TEST_PROJECT_ID, instance_id=INSTANCE_ID
        )
        assert any(
            e["source"] == "project-file" for e in result["injected"]
        ), "positive control failed — fixture yields no file skills"

    def test_http_search_with_arbitrary_project_id_is_file_blind(
        self, canon_root, sqlite_engine, monkeypatch, wired_client  # noqa: F811
    ):
        """``POST /api/skills/search`` with a caller-supplied
        ``project_id`` returns ZERO file entries (A6/D1) — and the
        refusal is observable, proving the resolver was consulted."""
        install_selector(
            monkeypatch, {"selected": [{"name": "alpha-skill", "score": 0.9}]}
        )
        client, skills_module = wired_client
        service = _build_service(_file_backed_source(sqlite_engine, canon_root))
        skills_module.get_search.set_service(service)

        response = client.post(
            "/api/skills/search",
            json={"query": "alpha", "project_id": TEST_PROJECT_ID, "max_results": 5},
        )
        assert response.status_code == 200
        payload = response.json()
        entries = (payload.get("injected") or []) + (payload.get("low_match") or [])
        assert entries == []
        assert service.file_corpus_stats()["refused"] is True

    async def test_router_passes_no_instance_scope(
        self, monkeypatch, wired_client
    ):
        """Structural pin: the REST route cannot acquire instance scope —
        no ``instance_id`` kwarg is forwarded (A6 relies on it)."""
        client, skills_module = wired_client
        service = MagicMock()
        service.search = AsyncMock(return_value={"injected": [], "low_match": []})
        skills_module.get_search.set_service(service)

        response = client.post(
            "/api/skills/search",
            json={"query": "anything", "project_id": TEST_PROJECT_ID},
        )
        assert response.status_code == 200
        assert service.search.await_count == 1
        assert "instance_id" not in service.search.await_args.kwargs


# ============================================================
# List — DB-only projection
# ============================================================


class TestRestListStaysDbOnly:
    def test_list_projection_carries_no_fusion_keys(self):
        """The ``_LIST_SKILL_FIELDS`` allowlist is a fixed column set —
        no ``source`` / ``address`` / ``shadows`` can leak into the REST
        list surface (S2)."""
        forbidden = {"source", "address", "shadows", "manifest", "content"}
        assert forbidden.isdisjoint(set(_LIST_SKILL_FIELDS))

    def test_list_dict_projects_exactly_the_allowlist(self):
        from daemon.services.skill_store_service import _project_skill

        row = MagicMock()
        for field in _LIST_SKILL_FIELDS:
            setattr(row, field, "v")
        # Attributes the file source adds — must NOT appear.
        row.source = "project-file"
        row.address = "pf:tester:alpha-skill"
        row.shadows = ["9e79ccad"]
        projected = _project_skill(row)
        assert set(projected) == set(_LIST_SKILL_FIELDS)
        assert not str(projected["id"]).startswith("pf-")
