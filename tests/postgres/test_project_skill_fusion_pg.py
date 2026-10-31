"""Real-PostgreSQL legs for the Phase-2 search fusion (project-skill-loader).

Why real PG is mandatory here: the file corpus is INDEX-BACKED. Every
``corpus_for`` call runs the stat gate and reconciles ``project_skill_files``
/ ``project_skill_embeddings`` rows — SQLite cannot prove the PG-visible
half (the A1 dedicated no-FK table, the column-ensure path, JSONB manifest
round-trip). These legs therefore run the fusion against the real repos.

Run::

    PG_TEST_DB=<scratch> PG_TEST_USER=<scratch-role> \
    PG_TEST_PASSWORD=<pw> PG_TEST_ALLOWED_DBS=<scratch> \
    .venv/bin/python -m pytest tests/postgres/test_project_skill_fusion_pg.py \
        --override-ini="addopts=" -m postgres -q

(One scratch role+DB per leg, created and dropped by the caller — never
shared; the conftest fence refuses live database names.)

Pins
----
``TestFusionOnRealPg``
    index → embedding rows land on PG; the fused search returns the file
    skill tagged ``project-file``; the DB twin is shadow-retired; the
    legacy ``skill_embeddings`` table is never touched (P1: search is
    read-only).
``TestA7PreBudgetShadowPg``
    the A7 over-budget pin, on the real PG index.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

import daemon.repositories.project_skill_file.models  # noqa: F401
import daemon.repositories.skill.models  # noqa: F401
import daemon.services.skill_search_service as sss_module
from daemon.repositories.project_skill_file.models import (
    ProjectSkillEmbedding,
    ProjectSkillFile,
)
from daemon.services.project_skill_source.service import ProjectSkillSourceService
from daemon.services.skill_search_service import SkillSearchService

from tests.helpers.project_skill_source_fixtures import (
    TEST_PROJECT_ID,
    FakeEmbeddingService,
    FakeInstance,
    FakeInstanceRepository,
    FakeProject,
    FakeProjectRepository,
    write_skill,
)

pytestmark = pytest.mark.postgres

INSTANCE_ID = "inst-pg-fusion"


# ============================================================
# Builders
# ============================================================


class _StaticSkillRepo:
    def __init__(self, skills):
        self._skills = list(skills)

    def list(self, project_id=None, active_only=True, limit=200):
        return (list(self._skills), len(self._skills))


class _EmptyEmbeddingRepo:
    def get_by_skill(self, skill_id):
        return []


class _DownEmbedding:
    async def embed_user_message(self, message):
        raise RuntimeError("embedding endpoint down")

    def cosine_similarity(self, a, b):
        return 0.0


def make_db_skill(skill_id: str, name: str, content: str = ""):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=skill_id,
        name=name,
        description=f"{name} description",
        content=content or f"{name} body",
        project_id=TEST_PROJECT_ID,
        is_active=True,
        status="active",
        source_skill_bank_id=None,
    )


def make_file_source(pg_engine, root: str) -> ProjectSkillSourceService:
    from daemon.repositories.project_skill_file.repository import (
        ProjectSkillEmbeddingRepository,
        ProjectSkillFileRepository,
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
    return ProjectSkillSourceService(
        instance_repository=instance_repo,
        project_repository=project_repo,
        file_repository=ProjectSkillFileRepository(pg_engine),
        embedding_repository=ProjectSkillEmbeddingRepository(pg_engine),
        embedding_service=FakeEmbeddingService(),
        llm_config={"model": "coding"},
        extra_roots=[],
        refresh_debounce_s=0.01,
    )


def make_search(file_source) -> SkillSearchService:
    from types import SimpleNamespace

    return SkillSearchService(
        skill_repo=_StaticSkillRepo([]),
        embedding_repo=_EmptyEmbeddingRepo(),
        embedding_service=_DownEmbedding(),
        llm_config={"selector_model": "quick", "base_url": "http://127.0.0.1:9/v1"},
        config=SimpleNamespace(bm25_top_k=10, llm_select_top_k=5, max_inject_skills=2),
        file_source=file_source,
    )


def install_selector(monkeypatch, payload: dict) -> None:
    from unittest.mock import MagicMock

    def _chat_response(content: str) -> MagicMock:
        choice = MagicMock()
        choice.message = MagicMock()
        choice.message.content = content
        response = MagicMock()
        response.choices = [choice]
        return response

    monkeypatch.setattr(
        sss_module,
        "invoke_raw_with_failover",
        lambda fn, cfg: _chat_response(json.dumps(payload)),
        raising=True,
    )


# ============================================================
# Legs
# ============================================================


class TestFusionOnRealPg:
    async def test_index_rows_then_fused_search(
        self, pg_engine, tmp_path, monkeypatch
    ):
        root = str(tmp_path / "root")
        write_skill(root, "tester", "alpha-skill", body_text="alpha alpha body\n")
        service = make_file_source(pg_engine, root)

        # 1. The index materializes on real PG (files + dedicated
        #    embeddings table are both written by the index path).
        stats = await service.ensure_index(root, TEST_PROJECT_ID)
        assert stats.skills_indexed == 1
        with Session(pg_engine) as session:
            files = list(session.exec(select(ProjectSkillFile)))
            assert [row.name for row in files] == ["alpha-skill"]
            pseudo_id = files[0].id
            assert pseudo_id.startswith("pf-")
            # Row carries metadata only — never body bytes (P1/A16).
            assert "alpha alpha body" not in json.dumps(files[0].manifest_json)

        # 2. Populate the dedicated embedding rows directly (the
        #    background refresh task is debounced; this leg is about the
        #    TABLE + the fusion, not the refresh scheduling).
        from daemon.repositories.project_skill_file.repository import (
            ProjectSkillEmbeddingRepository,
        )

        embed_repo = ProjectSkillEmbeddingRepository(pg_engine)
        assert embed_repo.replace_for_skill(pseudo_id, [("alpha trigger", [1.0, 0.0])])[0] == 1
        with Session(pg_engine) as session:
            vectors = list(
                session.exec(
                    select(ProjectSkillEmbedding).where(
                        ProjectSkillEmbedding.skill_id == pseudo_id
                    )
                )
            )
            assert len(vectors) == 1

        # 3. Fusion over the real index: file skill surfaces + tagged.
        twin_id = "9e79ccad-1e2c-456b-8762-82d7f5eeec0f"
        search = make_search(service)
        search._skill_repo = _StaticSkillRepo(
            [make_db_skill(twin_id, "alpha-skill", "alpha alpha stale")]
        )
        install_selector(
            monkeypatch, {"selected": [{"name": "alpha-skill", "score": 0.9}]}
        )
        result = await search.search(
            "alpha", project_id=TEST_PROJECT_ID, instance_id=INSTANCE_ID
        )
        assert len(result["injected"]) == 1
        entry = result["injected"][0]
        assert entry["source"] == "project-file"
        assert entry["skill"].id == pseudo_id
        assert entry["shadows"] == [twin_id]

        # 4. P1 — the search path is READ-ONLY on the legacy tables.
        with Session(pg_engine) as session:
            legacy = session.execute(
                text("SELECT count(*) FROM skill_embeddings")
            ).scalar()
            assert legacy == 0

    async def test_no_context_is_db_only_on_pg(
        self, pg_engine, tmp_path, monkeypatch
    ):
        root = str(tmp_path / "root")
        write_skill(root, "tester", "alpha-skill", body_text="alpha alpha\n")
        service = make_file_source(pg_engine, root)
        search = make_search(service)
        search._skill_repo = _StaticSkillRepo([make_db_skill("db-1", "alpha-db")])
        install_selector(
            monkeypatch, {"selected": [{"name": "alpha-db", "score": 0.9}]}
        )
        result = await search.search("alpha", project_id=None)
        assert [e["source"] for e in result["injected"]] == ["db"]


class TestA7PreBudgetShadowPg:
    async def test_over_budget_twin_still_shadowed_on_pg(
        self, pg_engine, tmp_path, monkeypatch
    ):
        root = str(tmp_path / "root")
        for idx in range(54):
            write_skill(root, "a-ns", f"cap-skill-{idx:03d}")
        write_skill(root, "z-ns", "zz-twin-skill", body_text="twin twin body\n")
        service = make_file_source(pg_engine, root)

        twin_id = "fd1e01dc-37d5-4a0b-a05e-1933d18a006d"
        search = make_search(service)
        search._skill_repo = _StaticSkillRepo(
            [make_db_skill(twin_id, "zz-twin-skill", "twin twin stale")]
        )
        install_selector(
            monkeypatch, {"selected": [{"name": "zz-twin-skill", "score": 0.9}]}
        )
        result = await search.search(
            "twin", project_id=TEST_PROJECT_ID, instance_id=INSTANCE_ID
        )
        assert len(result["injected"]) == 1
        assert result["injected"][0]["source"] == "project-file"
        assert result["injected"][0]["shadows"] == [twin_id]
        stats = search.file_corpus_stats()
        assert stats["file_total"] == 55
        assert stats["budget_dropped"] == 4
