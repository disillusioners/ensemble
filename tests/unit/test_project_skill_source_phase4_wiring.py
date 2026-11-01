"""Phase-4 wiring pack — knob matrix (M0–M11), M10 acceptance, B1
None-tolerance, stage-2 dual-source adapter, inject_skills threading,
construction-cheap, A17 purge + MAX_ROOTS.

Commission: project-skill-loader, Phase 4 (tasks 2/3/5/6 + A10 sites
1-5 + B1 rulings + leader-commissioned stage-2 adapter).

Pins in this pack
-----------------

``TestWiringSites`` (structural, source-level)
    A10 — all six wiring sites present in ``manager.py`` with the
    exact kwarg shapes; regression guards against accidental
    re-darkening (the "wiring accidentally gates the source behind
    config.skill_evolution" risk).
``TestKnobMatrix``
    Task 5 — all four matrix cells per Appendix A: (ON, present)
    full fusion; (ON, absent) M10 file-only; (OFF, present) and
    (OFF, absent) byte-identical RESULTS + zero ``pf-`` rows.
``TestM10FileOnly``
    The moved Phase-3-exit acceptance — BM25-only over the file
    corpus, ``top_k=max_results``, stage 2 skipped EXPLICITLY (no
    embedding call), stage 3 skipped (no LLM call), standard shape +
    ``degraded:"file_only"``, ``source:"project-file"`` tags, caps via
    module defaults (``_config=None`` getattr fallback).
``TestStage2DualSourceAdapter``
    Leader-commissioned — file skills participate in embedding rerank
    through ``ProjectSkillEmbeddingRepository.list_by_skill``; DB
    skills' rerank path unchanged (byte-identical fetch repo).
``TestInjectSkillsThreading``
    THE last dark path — ``SkillInjectionService.inject_skills``
    threads ``instance_id`` into search (auto-injection of file
    skills goes live); ``file_source=None`` keeps the DB path
    byte-identical (the miss-path).
``TestConstructionCheap``
    Task 2 — ZERO filesystem reads during service construction (the
    manager-init cost pin; scan is lazy per root).
``TestOrphanPurge`` / ``TestMaxRootsCap``
    A17 — one-statement orphan purge (deleted project ⇒ index rows +
    embedding rows purged on the gc tick) + the MAX_ROOTS=32
    INFO-refuse cap.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import daemon.services.skill_search_service as sss_module
from daemon.config import ProjectSkillSourceConfig
from daemon.repositories.project_skill_file.repository import (
    ProjectSkillEmbeddingRepository,
    ProjectSkillFileRepository,
)
from daemon.services.project_skill_source.service import (
    MAX_ROOTS,
    ProjectSkillSourceService,
)
from daemon.services.skill_injection_service import SkillInjectionService
from daemon.services.skill_search_service import SkillSearchService

from tests.helpers.project_skill_source_fixtures import (
    OTHER_PROJECT_ID,
    TEST_PROJECT_ID,
    FakeInstance,
    FakeInstanceRepository,
    FakeProject,
    FakeProjectRepository,
    canon_root,  # noqa: F401 — fixture re-exported for pytest
    make_service,
    sqlite_engine,  # noqa: F401 — fixture re-exported for pytest
    write_skill,
)

INSTANCE_ID = "inst-phase4-0001"


# ============================================================
# Builders (manager-shaped — these mirror the EXACT construction
# branches manager.py runs per cell, so the matrix pins the wiring
# logic, not just the service constructors)
# ============================================================


class _StaticSkillRepo:
    def __init__(self, skills):
        self._skills = list(skills)
        self.calls: list[dict] = []

    def list(self, project_id=None, active_only=True, limit=200):
        self.calls.append(
            {"project_id": project_id, "active_only": active_only}
        )
        return (list(self._skills), len(self._skills))


class _RecordingEmbeddingService:
    """Counts embed calls — proves stage 2 ran (or was skipped)."""

    def __init__(self):
        self.embed_calls = 0

    async def embed_user_message(self, message):
        self.embed_calls += 1
        return [1.0, 0.0]

    def cosine_similarity(self, a, b):
        try:
            return sum(x * y for x, y in zip(a, b))
        except Exception:
            return 0.0


class _RecordingEmbeddingRepo:
    def __init__(self):
        self.fetches: list[str] = []

    def get_by_skill(self, skill_id):
        self.fetches.append(str(skill_id))
        return []


def make_db_skill(skill_id="db-1", name="code-review", content=""):
    return SimpleNamespace(
        id=skill_id,
        name=name,
        description=f"{name} description",
        content=content or f"{name} body",
        project_id=None,
        is_active=True,
        status="active",
        source_skill_bank_id=None,
    )


def make_manager_shaped(
    *,
    knob_on: bool,
    evolution_present: bool,
    engine,
    root: str,
    db_skills=None,
    embedding_service=None,
    embedding_repo=None,
    llm_transport=None,
):
    """Mirror manager.py's construction branches for one matrix cell.

    Returns ``(search_service, injection_service, file_source)`` —
    exactly the objects the manager builds at sites 1-4, with the
    knob applied the way the manager applies it.
    """
    # Site 4 (unconditional construction) + knob gate:
    file_source = make_service(
        sqlite_engine=engine,
        instance_repo=FakeInstanceRepository(
            {
                INSTANCE_ID: FakeInstance(
                    instance_id=INSTANCE_ID,
                    instance_metadata={"project_id": TEST_PROJECT_ID},
                    project_id=TEST_PROJECT_ID,
                )
            }
        ),
        project_repo=FakeProjectRepository(
            [FakeProject(project_id=TEST_PROJECT_ID, main_directory=root)]
        ),
        embedding_service=embedding_service,
    )
    if not knob_on:
        file_source = None  # manager: _project_skill_source = None

    if evolution_present:
        # Sites 1+2 (the evolution-present branch): real deps,
        # file_source threaded when the knob is ON.
        search = SkillSearchService(
            skill_repo=_StaticSkillRepo(db_skills or []),
            embedding_repo=embedding_repo or _RecordingEmbeddingRepo(),
            embedding_service=embedding_service,
            llm_config={},
            config=SimpleNamespace(
                bm25_top_k=10, llm_select_top_k=5, max_inject_skills=2
            ),
            skill_bank_repo=None,
            file_source=file_source,
        )
        injection = SkillInjectionService(
            search_service=search,
            config=SimpleNamespace(max_inject_skills=2),
            ab_test_repo=None,
            skill_repo=None,
        )
        if file_source is not None:
            injection.set_file_source(file_source)
        return search, injection, file_source

    # Site 3 (else-branch): knob OFF ⇒ everything stays None (M4).
    if not knob_on:
        return None, None, None
    # B1 None-safe construction (knob ON + evolution absent ⇒ M10).
    search = SkillSearchService(
        skill_repo=None,
        embedding_repo=None,
        embedding_service=None,
        llm_config={},
        config=None,
        skill_bank_repo=None,
        file_source=file_source,
    )
    injection = SkillInjectionService(
        search_service=search,
        config=None,
        ab_test_repo=None,
        skill_repo=None,
    )
    injection.set_file_source(file_source)
    return search, injection, file_source


def _pf_row_counts(engine) -> dict[str, int]:
    """Count rows in the three lane tables (the zero-pf- pin)."""
    from sqlmodel import Session, text

    counts = {}
    with Session(engine) as session:
        for table in (
            "project_skill_files",
            "project_skill_embeddings",
            "project_skill_usage_records",
        ):
            try:
                counts[table] = int(
                    session.exec(text(f"SELECT COUNT(*) FROM {table}")).one()[0]
                )
            except Exception:
                counts[table] = -1
    return counts


def _entry_names(result):
    names = []
    for entry in result.get("injected") or []:
        skill = entry.get("skill")
        names.append(str(getattr(skill, "name", "") or ""))
    for entry in result.get("low_match") or []:
        names.append(str(entry.get("name") or ""))
    return names


# ============================================================
# A10 — the six wiring sites (structural)
# ============================================================


class TestWiringSites:
    """Source-level guards: every A10 site stays wired in manager.py.

    These are cheap structural pins (the house ``source substring``
    precedent) — behavioral coverage lives in the matrix classes. A
    refactor that renames a kwarg breaks the exact assertion here
    BEFORE it silently re-darkens a path.
    """

    def _manager_source(self) -> str:
        import daemon.manager as m

        return inspect.getsource(m)

    def test_site_1_search_ctor_threads_file_source(self):
        src = self._manager_source()
        assert "file_source=self._project_skill_source," in src

    def test_site_2_injection_set_file_source(self):
        src = self._manager_source()
        assert src.count("set_file_source(") >= 2  # site 2 + site 3

    def test_site_3_none_safe_construction_branch(self):
        src = self._manager_source()
        assert "skill_repo=None," in src
        assert "A10 SITE 3" in src

    def test_site_4_unconditional_construction(self):
        src = self._manager_source()
        assert "A10 SITE 4" in src
        # The construction is NOT inside the skill_evolution branch:
        # the marker sits BEFORE the evolution cluster gate.
        site4 = src.index("A10 SITE 4")
        cluster = src.index("Skill Evolution services (Phase 2")
        assert site4 < cluster

    def test_site_5_pre_create_all_registration(self):
        src = self._manager_source()
        reg = src.index("A10 SITE 5")
        create_all = src.index("SQLModel.metadata.create_all(self._engine)")
        assert reg < create_all
        assert "ProjectSkillFile," in src

    def test_knob_off_logs_and_gates(self):
        src = self._manager_source()
        assert "self._project_skill_source = None" in src
        assert "file source DISABLED" in src


# ============================================================
# Task 5 — the knob matrix (all four cells, per M0–M11)
# ============================================================


class TestKnobMatrix:
    @pytest.mark.asyncio
    async def test_cell_on_present_full_fusion(self, canon_root, sqlite_engine):
        """M9 — (ON, present, healthy): DB + file corpus fused."""
        from unittest.mock import patch

        search, _, _ = make_manager_shaped(
            knob_on=True,
            evolution_present=True,
            engine=sqlite_engine,
            root=canon_root,
            db_skills=[make_db_skill(name="only-db-skill")],
            embedding_service=_RecordingEmbeddingService(),
        )
        selector_payload = {"selected": [], "low_match": []}

        async def _invoke(fn, cfg):
            resp = MagicMock()
            choice = MagicMock()
            choice.message.content = json.dumps(selector_payload)
            resp.choices = [choice]
            return resp

        with patch.object(sss_module, "invoke_raw_with_failover", _invoke):
            result = await search.search(
                "alpha demo",
                project_id=TEST_PROJECT_ID,
                max_results=5,
                instance_id=INSTANCE_ID,
            )
        # The FUSION happened: the file corpus was consulted (stats
        # populated) and the file skill is searchable.
        stats = search.last_file_corpus_stats
        assert stats.get("file_total") == 3
        assert stats.get("refused") is False

    @pytest.mark.asyncio
    async def test_cell_on_absent_m10_file_only(self, canon_root, sqlite_engine):
        """M10 — (ON, absent, healthy): file-only degraded (see
        TestM10FileOnly for the full pinned semantics)."""
        search, injection, file_source = make_manager_shaped(
            knob_on=True,
            evolution_present=False,
            engine=sqlite_engine,
            root=canon_root,
        )
        assert search is not None and injection is not None
        assert search.has_db_corpus is False
        result = await search.search(
            "alpha demo",
            project_id=TEST_PROJECT_ID,
            max_results=5,
            instance_id=INSTANCE_ID,
        )
        assert result.get("degraded") == "file_only"
        assert "alpha-skill" in " ".join(_entry_names(result))

    @pytest.mark.asyncio
    async def test_cell_off_present_results_byte_identical_and_zero_pf_rows(
        self, canon_root, sqlite_engine
    ):
        """M0 — (OFF, present): file source ABSENT ⇒ results identical
        to a never-fused service; zero pf- rows in every lane table."""
        from unittest.mock import patch

        db = [make_db_skill(skill_id="db-1", name="code-review")]
        embedding = _RecordingEmbeddingService()

        def build(on):
            search, _, _ = make_manager_shaped(
                knob_on=on,
                evolution_present=True,
                engine=sqlite_engine,
                root=canon_root,
                db_skills=db,
                embedding_service=embedding,
            )
            return search

        off_search = build(False)
        never_fused = SkillSearchService(
            skill_repo=_StaticSkillRepo(db),
            embedding_repo=_RecordingEmbeddingRepo(),
            embedding_service=embedding,
            llm_config={},
            config=SimpleNamespace(
                bm25_top_k=10, llm_select_top_k=5, max_inject_skills=2
            ),
        )
        selector_payload = {"selected": [], "low_match": []}

        async def _invoke(fn, cfg):
            resp = MagicMock()
            choice = MagicMock()
            choice.message.content = json.dumps(selector_payload)
            resp.choices = [choice]
            return resp

        with patch.object(sss_module, "invoke_raw_with_failover", _invoke):
            off_result = await off_search.search(
                "code review", project_id=TEST_PROJECT_ID, max_results=5
            )
            plain_result = await never_fused.search(
                "code review", project_id=TEST_PROJECT_ID, max_results=5
            )
        # RESULTS byte-identical (A11: /readyz detail legitimately
        # differs — not asserted here).
        assert _entry_names(off_result) == _entry_names(plain_result)
        assert json.dumps(off_result, default=str, sort_keys=True) == json.dumps(
            plain_result, default=str, sort_keys=True
        )
        assert off_search.last_file_corpus_stats == {}
        # Zero pf- rows: nothing indexed under OFF.
        await off_search.search(
            "alpha demo",
            project_id=TEST_PROJECT_ID,
            max_results=5,
            instance_id=INSTANCE_ID,
        )
        assert all(
            c == 0 for c in _pf_row_counts(sqlite_engine).values()
        ), _pf_row_counts(sqlite_engine)

    @pytest.mark.asyncio
    async def test_cell_off_absent_today_behavior_zero_pf_rows(
        self, canon_root, sqlite_engine
    ):
        """M4 — (OFF, absent): today's behavior exactly — both services
        None ⇒ the tool layer's soft-fail path; no writes."""
        search, injection, file_source = make_manager_shaped(
            knob_on=False,
            evolution_present=False,
            engine=sqlite_engine,
            root=canon_root,
        )
        assert search is None
        assert injection is None
        assert file_source is None
        assert all(
            c == 0 for c in _pf_row_counts(sqlite_engine).values()
        )


# ============================================================
# M10 — the moved acceptance (Phase-3 exit → Phase 4)
# ============================================================


class TestM10FileOnly:
    @pytest.fixture
    def m10(self, canon_root, sqlite_engine):
        search, injection, file_source = make_manager_shaped(
            knob_on=True,
            evolution_present=False,
            engine=sqlite_engine,
            root=canon_root,
        )
        return search, injection, file_source

    @pytest.mark.asyncio
    async def test_bm25_only_over_file_corpus_standard_shape(self, m10):
        search, _, _ = m10
        result = await search.search(
            "alpha demo body",
            project_id=TEST_PROJECT_ID,
            max_results=5,
            instance_id=INSTANCE_ID,
        )
        assert set(result.keys()) >= {"injected", "low_match"}
        assert result["degraded"] == "file_only"
        injected = result["injected"]
        assert injected, "file corpus must surface in M10"
        names = [str(getattr(e["skill"], "name", "")) for e in injected]
        assert "alpha-skill" in names
        # A9: source tag on every entry.
        for e in injected:
            assert e["source"] == "project-file"
            assert str(e["skill"].address).startswith("pf:")

    @pytest.mark.asyncio
    async def test_stage2_skipped_explicitly(self, m10, monkeypatch):
        """B1 (i) — no embedding service ⇒ stage 2 SKIPPED, not an
        exception lottery: the embed transport is never touched."""
        search, _, _ = m10

        async def _explode(*a, **k):
            raise AssertionError("stage 2 must be SKIPPED in M10")

        monkeypatch.setattr(
            sss_module.SkillSearchService,
            "_embedding_rerank",
            _explode,
            raising=True,
        )
        result = await search.search(
            "alpha", project_id=TEST_PROJECT_ID, max_results=3,
            instance_id=INSTANCE_ID,
        )
        assert result["degraded"] == "file_only"

    @pytest.mark.asyncio
    async def test_stage3_skipped_no_llm_call(self, m10, monkeypatch):
        """B1 (ii) — ``_config is None`` ⇒ stage 3 SKIPPED: the LLM
        transport is never invoked; selection is deterministic."""
        search, _, _ = m10

        async def _explode(*a, **k):
            raise AssertionError("stage 3 must be SKIPPED in M10")

        monkeypatch.setattr(
            sss_module.SkillSearchService, "_llm_select", _explode
        )
        r1 = await search.search(
            "alpha demo", project_id=TEST_PROJECT_ID, max_results=3,
            instance_id=INSTANCE_ID,
        )
        r2 = await search.search(
            "alpha demo", project_id=TEST_PROJECT_ID, max_results=3,
            instance_id=INSTANCE_ID,
        )
        # Deterministic: identical calls ⇒ identical results.
        assert json.dumps(r1, default=str, sort_keys=True) == json.dumps(
            r2, default=str, sort_keys=True
        )

    @pytest.mark.asyncio
    async def test_top_k_equals_max_results(self, m10):
        """A9 — stage-1 cut pinned to ``max_results`` in file-only
        mode (not the config default 10)."""
        search, _, _ = m10
        result = await search.search(
            "skill demo body",  # matches all three canon skills
            project_id=TEST_PROJECT_ID,
            max_results=1,
            instance_id=INSTANCE_ID,
        )
        assert len(result["injected"]) == 1

    @pytest.mark.asyncio
    async def test_caps_fall_back_to_module_defaults(self, m10):
        """B1 (iii) — ``_config=None`` ⇒ caps via the getattr-default
        path (search works at all; max_results respected)."""
        search, _, _ = m10
        assert search._config is None
        result = await search.search(
            "skill",
            project_id=TEST_PROJECT_ID,
            max_results=2,
            instance_id=INSTANCE_ID,
        )
        assert len(result["injected"]) <= 2

    @pytest.mark.asyncio
    async def test_inject_explicit_skill_with_ab_test_repo_none(self, m10):
        """B1 (iv) — explicit injection works with ``ab_test_repo=None``
        (the A/B branch None-guarded): the file-canon STEP-0 wins."""
        _, injection, _ = m10
        text, ids = await injection.inject_explicit_skill(
            "alpha-skill",
            TEST_PROJECT_ID,
            INSTANCE_ID,
            "msg-1",
            "worker",
        )
        assert text is not None
        assert "Alpha body." in text
        assert ids and str(ids[0]).startswith("pf-")

    @pytest.mark.asyncio
    async def test_view_list_serve_and_create_fix_gated(self, m10):
        """M10 surface: lookup_explicit serves fresh bodies; the
        create/fix gates are the Phase-3 tool guards (they key on the
        manager's DB stack being absent) — pinned here at the service
        level: lookup works, and there is NO DB write path at all
        (skill_repo None ⇒ store service unavailable by construction).
        """
        search, _, file_source = m10
        resolution = await file_source.resolve_active_project(
            instance_id=INSTANCE_ID
        )
        for root in resolution.roots:
            await file_source.ensure_index(root, resolution.project_id)
        skill = await file_source.lookup_explicit(
            "beta-skill", resolution.project_id
        )
        assert skill is not None
        assert "Beta body." in (skill.content or "")
        # The DB stack is structurally absent in M10:
        assert search.has_db_corpus is False


# ============================================================
# Stage-2 dual-source adapter (leader-commissioned)
# ============================================================


class TestStage2DualSourceAdapter:
    @pytest.mark.asyncio
    async def test_file_skill_uses_dedicated_table(self, canon_root, sqlite_engine):
        """A file skill's cached trigger embeddings are read through
        ``file_embeddings_for`` (the dedicated A1 table) and SCORE the
        skill above an unrelated DB skill with no embeddings."""
        embed_repo = _RecordingEmbeddingRepo()
        search = SkillSearchService(
            skill_repo=_StaticSkillRepo(
                [make_db_skill(skill_id="db-plain", name="database thing")]
            ),
            embedding_repo=embed_repo,
            embedding_service=_RecordingEmbeddingService(),
            llm_config={},
            config=SimpleNamespace(
                bm25_top_k=10, llm_select_top_k=5, max_inject_skills=2
            ),
            file_source=make_service(
                sqlite_engine=sqlite_engine,
                instance_repo=FakeInstanceRepository(
                    {
                        INSTANCE_ID: FakeInstance(
                            instance_id=INSTANCE_ID,
                            instance_metadata={"project_id": TEST_PROJECT_ID},
                            project_id=TEST_PROJECT_ID,
                        )
                    }
                ),
                project_repo=FakeProjectRepository(
                    [FakeProject(project_id=TEST_PROJECT_ID, main_directory=canon_root)]
                ),
            ),
        )
        # Index the canon (lands pf rows +, with a fake embedding
        # service, trigger embeddings in the DEDICATED table).
        await search._file_source.ensure_index(canon_root, TEST_PROJECT_ID)
        # Seed a strong embedding for alpha-skill in the dedicated
        # table so the adapter has something to score.
        file_repo = ProjectSkillFileRepository(sqlite_engine)
        alpha = next(
            r for r in file_repo.list_by_project(TEST_PROJECT_ID)
            if r.name == "alpha-skill"
        )
        pf_embed = ProjectSkillEmbeddingRepository(sqlite_engine)
        pf_embed.insert_many(alpha.id, [("alpha trigger", [1.0, 0.0])])

        corpus = await search._file_source.corpus_for(
            TEST_PROJECT_ID, canon_root
        )
        alpha_skill = next(c for c in corpus if c.name == "alpha-skill")
        assert str(getattr(alpha_skill, "address", "")).startswith("pf:")
        reranked = await search._embedding_rerank("alpha", [alpha_skill])
        # The adapter consulted the DEDICATED table (not the DB repo —
        # the recording repo saw zero fetches for the pf id).
        assert str(alpha.id) not in embed_repo.fetches
        # And the row produced a real (non-neutral-exception) score.
        assert reranked and reranked[0][1] > 0.0

    @pytest.mark.asyncio
    async def test_db_skills_rerank_path_unchanged(self, sqlite_engine):
        """The adapter must not alter DB skills' rerank: a DB id goes
        through ``embedding_repo.get_by_skill`` exactly as before."""
        embed_repo = _RecordingEmbeddingRepo()
        search = SkillSearchService(
            skill_repo=_StaticSkillRepo([]),
            embedding_repo=embed_repo,
            embedding_service=_RecordingEmbeddingService(),
            llm_config={},
            config=SimpleNamespace(bm25_top_k=10),
            file_source=MagicMock(),  # present — the adapter branch exists
        )
        db_skill = make_db_skill(skill_id="db-x")
        await search._embedding_rerank("q", [db_skill])
        assert embed_repo.fetches == ["db-x"]

    def test_file_embeddings_for_fail_soft(self, sqlite_engine):
        svc = ProjectSkillSourceService(
            embedding_repository=ProjectSkillEmbeddingRepository(sqlite_engine)
        )
        assert svc.file_embeddings_for("pf-doesnotexist") == []
        none_repo = ProjectSkillSourceService()
        assert none_repo.file_embeddings_for("pf-x") == []


# ============================================================
# THE LAST DARK PATH — inject_skills threading
# ============================================================


class TestInjectSkillsThreading:
    @pytest.mark.asyncio
    async def test_inject_skills_threads_instance_id(self, canon_root, sqlite_engine):
        """Auto-injection consults the file corpus: the search call
        carries ``instance_id`` (the scope resolver's key)."""
        search, injection, _ = make_manager_shaped(
            knob_on=True,
            evolution_present=True,
            engine=sqlite_engine,
            root=canon_root,
            db_skills=[make_db_skill(name="code-review")],
            embedding_service=_RecordingEmbeddingService(),
        )
        seen: dict[str, dict] = {}
        real_search = search.search

        async def _spy(user_message, **kwargs):
            seen["kwargs"] = kwargs
            return await real_search(user_message, **kwargs)

        search.search = _spy
        text, ids = await injection.inject_skills(
            "alpha demo body",
            TEST_PROJECT_ID,
            INSTANCE_ID,
            "msg-thread-1",
            agent_id="worker",
        )
        assert seen["kwargs"].get("instance_id") == INSTANCE_ID

    @pytest.mark.asyncio
    async def test_file_source_none_keeps_db_path_byte_identical(
        self, sqlite_engine
    ):
        """N8 — ``file_source=None`` ⇒ the miss-path is byte-identical:
        an empty DB corpus yields the same (None, []) as before the
        threading landed."""
        search = SkillSearchService(
            skill_repo=_StaticSkillRepo([]),
            embedding_repo=_RecordingEmbeddingRepo(),
            embedding_service=_RecordingEmbeddingService(),
            llm_config={},
            config=SimpleNamespace(max_inject_skills=2),
        )
        injection = SkillInjectionService(
            search_service=search,
            config=SimpleNamespace(max_inject_skills=2),
            ab_test_repo=None,
            skill_repo=None,
        )
        text, ids = await injection.inject_skills(
            "anything", TEST_PROJECT_ID, INSTANCE_ID, "m1", agent_id="w"
        )
        assert text is None
        assert ids == []


# ============================================================
# Task 2 — construction-is-cheap
# ============================================================


class TestConstructionCheap:
    def test_zero_filesystem_reads_at_construction(self, tmp_path):
        """The manager-init cost pin: constructing the service touches
        NO filesystem path (no stat, no scandir, no walk, no listdir).
        The first scan is lazy per root on first touch."""
        touched: list[str] = []
        real = os.path

        class _FSProbe:
            def __init__(self, fn):
                self._fn = fn

            def __call__(self, *a, **k):
                touched.append(str(a[0] if a else ""))
                return self._fn(*a, **k)

        import daemon.services.project_skill_source.service as pss

        saved = {
            name: getattr(pss.os, name)
            for name in ("scandir", "walk", "listdir", "stat")
        }
        try:
            for name in saved:
                setattr(pss.os, name, _FSProbe(saved[name]))
            svc = ProjectSkillSourceService(
                extra_roots=[str(tmp_path / "never-touched")]
            )
            assert svc._roots == {}
        finally:
            for name, fn in saved.items():
                setattr(pss.os, name, fn)
        assert touched == [], f"filesystem touched during construction: {touched}"


# ============================================================
# A17 — orphan purge + MAX_ROOTS
# ============================================================


class TestOrphanPurge:
    @staticmethod
    def _seed_real_project_row(engine, project_id):
        """The A17 purge consults the REAL ``projects`` table (not the
        fake repo) — seed it so 'live project' is a DB fact."""
        from daemon.repositories.project.models import Project
        from sqlmodel import Session

        with Session(engine) as session:
            session.add(
                Project(
                    project_id=project_id,
                    name="phase4-purge-fixture",
                    project_type="software",
                    status="active",
                )
            )
            session.commit()

    @pytest.mark.asyncio
    async def test_deleted_project_rows_and_embeddings_purged_on_gc(
        self, canon_root, sqlite_engine
    ):
        """The A17 pin: delete the project ⇒ its index rows AND their
        embedding rows are purged on the next gc tick."""
        from daemon.repositories.project_skill_file.models import ProjectSkillFile

        self._seed_real_project_row(sqlite_engine, TEST_PROJECT_ID)
        svc = make_service(
            sqlite_engine=sqlite_engine,
            instance_repo=FakeInstanceRepository(
                {
                    INSTANCE_ID: FakeInstance(
                        instance_id=INSTANCE_ID,
                        instance_metadata={"project_id": TEST_PROJECT_ID},
                        project_id=TEST_PROJECT_ID,
                    )
                }
            ),
            project_repo=FakeProjectRepository(
                [FakeProject(project_id=TEST_PROJECT_ID, main_directory=canon_root)]
            ),
        )
        await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        file_repo = ProjectSkillFileRepository(sqlite_engine)
        rows = file_repo.list_by_project(TEST_PROJECT_ID)
        assert rows, "fixture failed to index"
        # Seed an embedding row riding one indexed skill.
        embed_repo = ProjectSkillEmbeddingRepository(sqlite_engine)
        embed_repo.insert_many(rows[0].id, [("q", [1.0])])
        assert embed_repo.count_by_skill(rows[0].id) == 1

        # The project vanishes (deleted upstream): drop the real DB row.
        from sqlmodel import Session
        from sqlalchemy import delete as sa_delete
        from daemon.repositories.project.models import Project

        with Session(sqlite_engine) as session:
            session.exec(sa_delete(Project).where(Project.project_id == TEST_PROJECT_ID))
            session.commit()

        removed = await svc.gc_sweep()
        assert removed >= 1
        assert file_repo.list_by_project(TEST_PROJECT_ID) == []
        assert embed_repo.count_by_skill(rows[0].id) == 0

    @pytest.mark.asyncio
    async def test_live_project_rows_survive_gc(self, canon_root, sqlite_engine):
        self._seed_real_project_row(sqlite_engine, TEST_PROJECT_ID)
        svc = make_service(
            sqlite_engine=sqlite_engine,
            project_repo=FakeProjectRepository(
                [FakeProject(project_id=TEST_PROJECT_ID, main_directory=canon_root)]
            ),
        )
        await svc.ensure_index(canon_root, TEST_PROJECT_ID)
        await svc.gc_sweep()
        assert (
            ProjectSkillFileRepository(sqlite_engine).list_by_project(
                TEST_PROJECT_ID
            )
        )


class TestMaxRootsCap:
    @pytest.mark.asyncio
    async def test_over_cap_roots_refused_with_info(
        self, canon_root, sqlite_engine, caplog, monkeypatch
    ):
        """A17 insurance: >MAX_ROOTS resolved roots ⇒ first-32 kept,
        INFO-refuse log, never an unbounded walk."""
        import logging

        svc = make_service(
            sqlite_engine=sqlite_engine,
            project_repo=FakeProjectRepository(
                [FakeProject(project_id=TEST_PROJECT_ID, main_directory=canon_root)]
            ),
        )
        many = [os.path.join(canon_root, f"extra-r{i:03d}") for i in range(MAX_ROOTS + 5)]

        async def _fake_extra(project_id):
            return list(many)

        monkeypatch.setattr(svc, "_resolve_extra_roots", _fake_extra)
        with caplog.at_level(
            logging.INFO,
            logger="daemon.services.project_skill_source.service",
        ):
            kept = await svc._resolve_roots(TEST_PROJECT_ID, None, None)
        assert len(kept) == MAX_ROOTS
        assert any("root cap" in r.message for r in caplog.records)


# ============================================================
# Config knob surface (task 1/6)
# ============================================================


class TestKnobConfig:
    def test_default_on_and_csv(self):
        cfg = ProjectSkillSourceConfig()
        assert cfg.enabled is True
        assert cfg.extra_roots == []

    def test_csv_parses(self):
        cfg = ProjectSkillSourceConfig(extra_roots="/a,/b")
        assert cfg.extra_roots == ["/a", "/b"]

    def test_env_prefix_resolves(self):
        import os

        key = "ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED"
        old = os.environ.get(key)
        os.environ[key] = "0"
        try:
            assert ProjectSkillSourceConfig().enabled is False
        finally:
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
