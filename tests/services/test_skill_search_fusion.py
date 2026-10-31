"""Phase-2 fusion pack — project-file corpus fused into ``SkillSearchService``.

Commission: project-skill-loader, Phase 2 (tasks 1-9). Seam:
``skill_search_service.py::_bm25_prefilter`` — post-ownership-filter,
pre-tokenization (one corpus, one scorer).

Pins in this pack
-----------------

``TestCompatibility``
    NFR-COMPAT-1 — ``file_source=None`` keeps the pre-fusion result
    shape byte-identical (no additive keys, same dicts).
``TestSourceTagging``
    Task 3 — every injected/low_match entry carries ``source``; file
    entries also carry ``address`` / ``shadows``; additive-key tolerance
    is pinned (JSON round-trip + unknown-key-ignoring consumer).
``TestShadowing``
    Task 8 (FR-P4-1..6) — DB twin + file twin ⇒ file-only result,
    ``shadows`` populated, DB twin absent from BOTH lists, the stage-3
    real selector resolves the shadowed name to the FILE object, and
    the 3 legacy DB copies are shadow-retired against the Phase-0
    census fixture.
``TestA7PreBudgetShadow``
    Task 5 + architect A7 — >50 file skills incl. one DB twin: the twin
    is absent from injected AND low_match under the mandated
    budget-exempt variant, with the twin ordered LAST so a
    budget-before-shadow implementation would go RED.
``TestBudgetCounter``
    Task 5 — file-budget overflow surfaces in ``file_corpus_stats()``.
``TestCombinedCaps``
    Task 5 — combined ``max_results`` / low_match caps; no padding
    (single-source behavior returns that source only).
``TestNoContextInvisibility``
    Task 6 (FR-P2-4 / FR-P3-3) — no resolvable project/root ⇒ DB-only.
``TestCrossProjectLeak``
    Task 7 (FR-P3-4, security-grade) — zero project-A names under
    project-B context, in every conflicting-supply shape, with a
    positive control so the pin cannot pass vacuously.
``TestDefensiveDedupe``
    Task 4 — a DB twin that somehow reaches stage 3 alongside its file
    shadow is dropped (synthetic drift: the corpus shadow pass is
    bypassed by calling the tagger directly).
``TestDegradedParity``
    Task 9 (FR-P2-5) — embeddings down ⇒ file skills surface via BM25
    with a neutral 0.0; LLM stage failure ⇒ ``_degraded_select``
    includes file skills.
``TestFusionSeamGuard``
    Architect A8 — a file source that RAISES at the seam degrades to
    DB-only results (the tool-surface assertion lives in
    ``tests/tools/test_skill_search_fusion_guard.py``).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import daemon.services.skill_search_service as sss_module
from daemon.services.skill_search_service import (
    FILE_CORPUS_BUDGET,
    SOURCE_DB,
    SOURCE_PROJECT_FILE,
    SkillSearchService,
)

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

#: The 3 legacy DB gate-runner copies the Phase-0 census names
#: (FR-P4-5 shadow-retirement set). Ids verbatim; names are the canon
#: skill names they collide with.
LEGACY_IDS = {
    "9e79ccad-1e2c-456b-8762-82d7f5eeec0f": "whole-tree-base-proven-diff",
    "fd1e01dc-37d5-4a0b-a05e-1933d18a006d": "regression-ab-partition-run",
    "76e3e1a7-1a5a-4228-957f-f8e556e04213": "failed-digest-equality-gate",
}

INSTANCE_ID = "inst-aaaa-1111"
OTHER_INSTANCE_ID = "inst-bbbb-2222"


# ============================================================
# Fakes / builders
# ============================================================


def make_skill(
    *,
    skill_id: str = "skill-1",
    name: str = "code-review",
    description: str = "Review code for bugs.",
    content: str = "",
    project_id: str | None = None,
) -> SimpleNamespace:
    """A minimal DB-row stand-in (mirrors the service's duck typing)."""
    return SimpleNamespace(
        id=skill_id,
        name=name,
        description=description,
        content=content,
        project_id=project_id,
        is_active=True,
        status="active",
        source_skill_bank_id=None,
    )


class _StaticSkillRepo:
    """``list(project_id=..., active_only=..., limit=...)`` returning a
    fixed row set (the DB corpus under test)."""

    def __init__(self, skills: list[SimpleNamespace]) -> None:
        self._skills = list(skills)

    def list(self, project_id=None, active_only=True, limit=200):
        return (list(self._skills), len(self._skills))


class _EmptyEmbeddingRepo:
    def get_by_skill(self, skill_id):  # noqa: D401 - trivial
        return []


class _DownEmbeddingService:
    """Embedding provider that is DOWN — forces the documented BM25
    fallback (stage-2 neutral ``0.0``, BM25 order preserved)."""

    async def embed_user_message(self, message: str) -> list[float]:
        raise RuntimeError("embedding endpoint down")

    def cosine_similarity(self, a, b) -> float:  # pragma: no cover
        return 0.0


def make_search_service(
    *,
    skills: list[SimpleNamespace] | None = None,
    file_source=None,
) -> SkillSearchService:
    return SkillSearchService(
        skill_repo=_StaticSkillRepo(skills or []),
        embedding_repo=_EmptyEmbeddingRepo(),
        embedding_service=_DownEmbeddingService(),
        llm_config={
            "selector_model": "quick",
            "base_url": "http://127.0.0.1:9/v1",
            "api_key": "sk-test",
        },
        config=SimpleNamespace(
            bm25_top_k=10, llm_select_top_k=5, max_inject_skills=2
        ),
        skill_bank_repo=None,
        file_source=file_source,
    )


def make_file_source(
    engine,
    *,
    root: str,
    project_id: str = TEST_PROJECT_ID,
    instance_id: str = INSTANCE_ID,
    extra_instances: dict[str, str] | None = None,
):
    """A REAL :class:`ProjectSkillSourceService` over a tmp canon root.

    ``extra_instances`` maps extra instance ids to their derived project
    (used by the cross-project leak pin).
    """
    instance_repo = FakeInstanceRepository(
        {
            instance_id: FakeInstance(
                instance_id=instance_id,
                instance_metadata={"project_id": project_id},
                project_id=project_id,
            )
        }
    )
    for iid, pid in (extra_instances or {}).items():
        instance_repo.instances[iid] = FakeInstance(
            instance_id=iid,
            instance_metadata={"project_id": pid},
            project_id=pid,
        )
    project_repo = FakeProjectRepository(
        [FakeProject(project_id=project_id, main_directory=root)]
    )
    for iid, pid in (extra_instances or {}).items():
        if pid != project_id and project_repo.get(pid) is None:
            project_repo.projects.append(FakeProject(project_id=pid))
    return make_service(
        sqlite_engine=engine,
        instance_repo=instance_repo,
        project_repo=project_repo,
    )


class _BrokenFileSource:
    """Raises on every seam call — the A8/M3/M8 degradation fixture."""

    async def resolve_active_project(self, instance_id=None, explicit_project_id=None):
        raise RuntimeError("file source exploded (A8 fixture)")

    async def corpus_for(self, project_id, root):  # pragma: no cover
        raise RuntimeError("file source exploded (A8 fixture)")


def _chat_response(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


def install_selector(monkeypatch, payload: dict | Exception) -> None:
    """Patch the stage-3 LLM transport.

    ``payload`` is the JSON dict the model "returns" (the REAL
    ``_llm_select`` mapping runs), or an Exception to force the
    ``_degraded_select`` branch.
    """

    def _invoke(fn, cfg):
        if isinstance(payload, Exception):
            raise payload
        return _chat_response(json.dumps(payload))

    monkeypatch.setattr(
        sss_module, "invoke_raw_with_failover", _invoke, raising=True
    )


def _entry_names(result: dict) -> list[str]:
    names = []
    for entry in result.get("injected") or []:
        skill = entry.get("skill")
        names.append(str(getattr(skill, "name", "") or ""))
    for entry in result.get("low_match") or []:
        names.append(str(entry.get("name") or ""))
    return names


def _entry_ids(result: dict) -> list[str]:
    ids = []
    for entry in result.get("injected") or []:
        skill = entry.get("skill")
        ids.append(str(getattr(skill, "id", "") or ""))
    return ids


# ============================================================
# Task 1 — NFR-COMPAT-1
# ============================================================


class TestCompatibility:
    async def test_file_source_none_is_byte_identical_shape(self, monkeypatch):
        """No file source ⇒ no additive keys, same ``{skill, score}``
        entries as before the fusion landed."""
        install_selector(
            monkeypatch, {"selected": [{"name": "code-review", "score": 0.9}]}
        )
        service = make_search_service(
            skills=[make_skill(name="code-review", content="review code")]
        )
        result = await service.search("code review", project_id=TEST_PROJECT_ID)
        assert len(result["injected"]) == 1
        entry = result["injected"][0]
        assert set(entry.keys()) == {"skill", "score"}
        assert entry["skill"].name == "code-review"

    async def test_file_source_none_never_touches_resolver(self):
        """With ``file_source=None`` the seam is a no-op — no resolver
        call, no stats, DB-only corpus."""
        service = make_search_service(
            skills=[make_skill(name="alpha", content="alpha body")]
        )
        candidates = await service._bm25_prefilter("alpha", TEST_PROJECT_ID)
        assert [c.name for c in candidates] == ["alpha"]
        stats = service.file_corpus_stats()
        assert stats.get("file_total", 0) == 0
        assert stats.get("last_error") is None


# ============================================================
# Task 3 — source tagging
# ============================================================


class TestSourceTagging:
    async def test_db_entries_tagged_when_fusion_active(
        self, tmp_path, monkeypatch, sqlite_engine  # noqa: F811
    ):
        """A wired file source with NO canon ⇒ DB-only results, but the
        entries now carry the additive ``source`` tag."""
        root = str(tmp_path / "empty-root")
        file_source = make_file_source(sqlite_engine, root=root)
        install_selector(
            monkeypatch, {"selected": [{"name": "code-review", "score": 0.5}]}
        )
        service = make_search_service(
            skills=[make_skill(name="code-review", content="review code")],
            file_source=file_source,
        )
        result = await service.search(
            "code review",
            project_id=TEST_PROJECT_ID,
            instance_id=INSTANCE_ID,
        )
        assert result["injected"][0]["source"] == SOURCE_DB

    async def test_file_entries_carry_source_address_and_are_json_safe(
        self, tmp_path, monkeypatch, sqlite_engine  # noqa: F811
    ):
        root = str(tmp_path / "root")
        write_skill(root, "tester", "alpha-skill", body_text="alpha alpha body\n")
        file_source = make_file_source(sqlite_engine, root=root)
        install_selector(
            monkeypatch, {"selected": [{"name": "alpha-skill", "score": 0.9}]}
        )
        service = make_search_service(file_source=file_source)
        result = await service.search(
            "alpha", project_id=TEST_PROJECT_ID, instance_id=INSTANCE_ID
        )
        entry = result["injected"][0]
        assert entry["source"] == SOURCE_PROJECT_FILE
        assert entry["address"] == "pf:tester:alpha-skill"
        # Additive keys stay JSON-serializable for the tool surface.
        payload = json.dumps(
            {
                "injected": [
                    {
                        "name": entry["skill"].name,
                        "score": entry["score"],
                        "source": entry["source"],
                        "address": entry["address"],
                    }
                ]
            }
        )
        assert json.loads(payload)["injected"][0]["source"] == "project-file"

    async def test_low_match_file_entry_is_tagged(
        self, tmp_path, monkeypatch, sqlite_engine  # noqa: F811
    ):
        """FR-P2-6 parity: low_match entries get the same tag treatment."""
        root = str(tmp_path / "root")
        write_skill(root, "tester", "alpha-skill", body_text="alpha alpha body\n")
        file_source = make_file_source(sqlite_engine, root=root)
        install_selector(
            monkeypatch,
            {
                "selected": [],
                "low_match": [
                    {
                        "name": "alpha-skill",
                        "score": 0.2,
                        "description": "alpha candidate",
                    }
                ],
            },
        )
        service = make_search_service(file_source=file_source)
        result = await service.search(
            "alpha", project_id=TEST_PROJECT_ID, instance_id=INSTANCE_ID
        )
        assert result["low_match"][0]["source"] == SOURCE_PROJECT_FILE
        assert result["low_match"][0]["address"] == "pf:tester:alpha-skill"


# ============================================================
# Task 8 — shadowing e2e (FR-P4-1..6)
# ============================================================


class TestShadowing:
    async def test_file_shadow_retires_db_twin(
        self, tmp_path, monkeypatch, sqlite_engine  # noqa: F811
    ):
        root = str(tmp_path / "root")
        write_skill(root, "tester", "alpha-skill", body_text="alpha alpha body\n")
        file_source = make_file_source(sqlite_engine, root=root)
        twin_id = "9e79ccad-1e2c-456b-8762-82d7f5eeec0f"
        service = make_search_service(
            skills=[
                make_skill(
                    skill_id=twin_id,
                    name="alpha-skill",
                    description="stale DB copy of alpha",
                    content="alpha alpha db body",
                )
            ],
            file_source=file_source,
        )

        # Corpus level: exactly ONE alpha-skill, and it is the file one.
        candidates = await service._bm25_prefilter(
            "alpha", TEST_PROJECT_ID, instance_id=INSTANCE_ID
        )
        alpha = [c for c in candidates if getattr(c, "name", "") == "alpha-skill"]
        assert len(alpha) == 1
        assert alpha[0].address.startswith("pf:")

        # End-to-end: the REAL stage-3 selector picks the shadowed name
        # and resolves it to the FILE object.
        install_selector(
            monkeypatch, {"selected": [{"name": "alpha-skill", "score": 0.9}]}
        )
        result = await service.search(
            "alpha", project_id=TEST_PROJECT_ID, instance_id=INSTANCE_ID
        )
        assert len(result["injected"]) == 1
        entry = result["injected"][0]
        assert entry["source"] == SOURCE_PROJECT_FILE
        assert entry["skill"].address == "pf:tester:alpha-skill"
        assert twin_id in entry["shadows"]
        # The DB twin is absent from BOTH lists.
        assert twin_id not in _entry_ids(result)
        assert "alpha-skill" in _entry_names(result)

    async def test_legacy_three_ids_shadow_retired(
        self, tmp_path, sqlite_engine  # noqa: F811
    ):
        """FR-P4-5 against the Phase-0 census fixture: the 3 legacy DB
        gate-runner copies are retired by the canon files of the same
        name in one pass."""
        root = str(tmp_path / "root")
        for name in LEGACY_IDS.values():
            write_skill(root, "tester", name, body_text=f"{name} body\n")
        file_source = make_file_source(sqlite_engine, root=root)
        db_skills = [
            make_skill(skill_id=sid, name=name, content=f"{name} stale body")
            for sid, name in LEGACY_IDS.items()
        ]
        service = make_search_service(skills=db_skills, file_source=file_source)
        candidates = await service._bm25_prefilter(
            "body", TEST_PROJECT_ID, instance_id=INSTANCE_ID
        )
        kept_ids = {str(getattr(c, "id", "")) for c in candidates}
        assert not (set(LEGACY_IDS) & kept_ids), "legacy DB copies must be retired"
        file_names = {c.name for c in candidates if c.address.startswith("pf:")}
        assert set(LEGACY_IDS.values()) <= file_names


# ============================================================
# Task 5 (A7) — pre-budget shadow
# ============================================================


class TestA7PreBudgetShadow:
    async def test_over_budget_twin_still_shadowed(
        self, tmp_path, monkeypatch, sqlite_engine  # noqa: F811
    ):
        """>50 file skills incl. one DB twin ⇒ twin absent from injected
        AND low_match.

        The twin is ordered LAST (namespace ``z-ns`` / name
        ``zz-twin-skill``) so a budget-before-shadow implementation
        truncates it away and leaves the stale DB twin surfacing — the
        exact defect A7 forbids. Under the mandated budget-exempt
        variant the twin is kept and the DB row is dropped.
        """
        root = str(tmp_path / "root")
        # 54 non-shadowing file skills (over the 50 budget) …
        for idx in range(54):
            write_skill(root, "a-ns", f"cap-skill-{idx:03d}")
        # … plus the shadowing twin, ordered last.
        write_skill(root, "z-ns", "zz-twin-skill", body_text="twin twin body\n")

        file_source = make_file_source(sqlite_engine, root=root)
        twin_id = "fd1e01dc-37d5-4a0b-a05e-1933d18a006d"
        service = make_search_service(
            skills=[
                make_skill(
                    skill_id=twin_id,
                    name="zz-twin-skill",
                    content="twin twin stale db body",
                )
            ],
            file_source=file_source,
        )
        install_selector(
            monkeypatch, {"selected": [{"name": "zz-twin-skill", "score": 0.9}]}
        )
        result = await service.search(
            "twin", project_id=TEST_PROJECT_ID, instance_id=INSTANCE_ID
        )

        assert twin_id not in _entry_ids(result)
        assert "zz-twin-skill" in _entry_names(result)
        injected = result["injected"]
        assert injected and injected[0]["source"] == SOURCE_PROJECT_FILE

        stats = service.file_corpus_stats()
        assert stats["file_total"] == 55
        assert stats["shadowing"] == 1
        # 54 non-shadowing → 50 kept, 4 dropped.
        assert stats["file_included"] == 51
        assert stats["budget_dropped"] == 4
        assert stats["shadowed_db_ids"] == [twin_id]


# ============================================================
# Task 5 — budget counter + combined caps
# ============================================================


class TestBudgetCounter:
    async def test_overflow_surfaces_in_stats(self, tmp_path, sqlite_engine):  # noqa: F811
        root = str(tmp_path / "root")
        for idx in range(53):
            write_skill(root, "tester", f"cap-skill-{idx:03d}")
        file_source = make_file_source(sqlite_engine, root=root)
        service = make_search_service(file_source=file_source)
        await service.search(
            "cap", project_id=TEST_PROJECT_ID, instance_id=INSTANCE_ID
        )
        stats = service.file_corpus_stats()
        assert stats["budget_dropped"] == 53 - FILE_CORPUS_BUDGET
        assert stats["file_included"] == FILE_CORPUS_BUDGET


class TestCombinedCaps:
    async def test_max_results_is_combined_cap(
        self, tmp_path, monkeypatch, sqlite_engine  # noqa: F811
    ):
        root = str(tmp_path / "root")
        write_skill(root, "tester", "alpha-file", body_text="alpha alpha file\n")
        file_source = make_file_source(sqlite_engine, root=root)
        install_selector(
            monkeypatch,
            {
                "selected": [
                    {"name": "alpha-db", "score": 0.9},
                    {"name": "alpha-file", "score": 0.8},
                ]
            },
        )
        service = make_search_service(
            skills=[
                make_skill(skill_id="db-1", name="alpha-db", content="alpha db")
            ],
            file_source=file_source,
        )
        result = await service.search(
            "alpha",
            project_id=TEST_PROJECT_ID,
            max_results=1,
            instance_id=INSTANCE_ID,
        )
        assert len(result["injected"]) == 1
        assert result["injected"][0]["source"] in {SOURCE_DB, SOURCE_PROJECT_FILE}

    async def test_low_match_cap_three_combined(
        self, tmp_path, monkeypatch, sqlite_engine  # noqa: F811
    ):
        root = str(tmp_path / "root")
        write_skill(root, "tester", "alpha-file", body_text="alpha alpha file\n")
        file_source = make_file_source(sqlite_engine, root=root)
        install_selector(
            monkeypatch,
            {
                "selected": [],
                "low_match": [
                    {"name": f"alpha-{i}", "score": 0.1, "description": "x"}
                    for i in range(6)
                ],
            },
        )
        service = make_search_service(
            skills=[
                make_skill(skill_id=f"db-{i}", name=f"alpha-{i}", content="alpha")
                for i in range(6)
            ],
            file_source=file_source,
        )
        result = await service.search(
            "alpha", project_id=TEST_PROJECT_ID, instance_id=INSTANCE_ID
        )
        assert len(result["low_match"]) <= 3

    async def test_no_padding_single_source_only(self, tmp_path, sqlite_engine):  # noqa: F811
        """File-only corpus ⇒ file entries only (no DB padding); DB-only
        ⇒ DB entries only."""
        root = str(tmp_path / "root")
        write_skill(root, "tester", "alpha-file", body_text="alpha alpha file\n")
        file_source = make_file_source(sqlite_engine, root=root)
        service = make_search_service(file_source=file_source)
        result = await service.search(
            "alpha",
            project_id=TEST_PROJECT_ID,
            max_results=5,
            instance_id=INSTANCE_ID,
        )
        sources = {e["source"] for e in result["injected"]}
        assert sources == {SOURCE_PROJECT_FILE}
        assert all(e["source"] == SOURCE_PROJECT_FILE for e in result["low_match"])


# ============================================================
# Task 6 — no-context invisibility
# ============================================================


class TestNoContextInvisibility:
    async def test_project_none_is_db_only(
        self, tmp_path, monkeypatch, sqlite_engine  # noqa: F811
    ):
        root = str(tmp_path / "root")
        write_skill(root, "tester", "alpha-skill", body_text="alpha alpha\n")
        file_source = make_file_source(sqlite_engine, root=root)
        install_selector(
            monkeypatch, {"selected": [{"name": "alpha-db", "score": 0.9}]}
        )
        service = make_search_service(
            skills=[make_skill(name="alpha-db", content="alpha db")],
            file_source=file_source,
        )
        result = await service.search("alpha", project_id=None)
        assert [e["skill"].name for e in result["injected"]] == ["alpha-db"]
        assert all(e["source"] == SOURCE_DB for e in result["injected"])
        assert service.file_corpus_stats().get("file_total", 0) == 0

    async def test_instance_without_project_is_db_only(
        self, tmp_path, monkeypatch, sqlite_engine  # noqa: F811
    ):
        root = str(tmp_path / "root")
        write_skill(root, "tester", "alpha-skill", body_text="alpha alpha\n")
        file_source = make_file_source(sqlite_engine, root=root)
        install_selector(
            monkeypatch, {"selected": [{"name": "alpha-db", "score": 0.9}]}
        )
        service = make_search_service(
            skills=[make_skill(name="alpha-db", content="alpha db")],
            file_source=file_source,
        )
        # An instance id the fake repo does not know ⇒ derived project None.
        result = await service.search(
            "alpha", project_id=None, instance_id="unknown-instance"
        )
        assert [e["skill"].name for e in result["injected"]] == ["alpha-db"]


# ============================================================
# Task 7 — cross-project leak (security-grade)
# ============================================================


class TestCrossProjectLeak:
    async def _setup(self, tmp_path, sqlite_engine):  # noqa: F811
        root = str(tmp_path / "proj-a-root")
        write_skill(root, "tester", "alpha-secret-skill", body_text="alpha alpha\n")
        file_source = make_file_source(
            sqlite_engine,
            root=root,
            project_id=TEST_PROJECT_ID,
            instance_id=INSTANCE_ID,
            extra_instances={OTHER_INSTANCE_ID: OTHER_PROJECT_ID},
        )
        service = make_search_service(
            skills=[
                make_skill(skill_id="db-b-1", name="alpha-db-b", content="alpha")
            ],
            file_source=file_source,
        )
        return service

    async def test_positive_control_project_a_sees_its_canon(
        self, tmp_path, monkeypatch, sqlite_engine  # noqa: F811
    ):
        service = await self._setup(tmp_path, sqlite_engine)
        install_selector(
            monkeypatch,
            {"selected": [{"name": "alpha-secret-skill", "score": 0.9}]},
        )
        result = await service.search(
            "alpha",
            project_id=TEST_PROJECT_ID,
            instance_id=INSTANCE_ID,
        )
        assert "alpha-secret-skill" in _entry_names(result)

    async def test_project_b_context_never_sees_project_a_canon(
        self, tmp_path, monkeypatch, sqlite_engine  # noqa: F811
    ):
        service = await self._setup(tmp_path, sqlite_engine)
        install_selector(
            monkeypatch,
            {"selected": [{"name": "alpha-secret-skill", "score": 0.9}]},
        )
        result = await service.search(
            "alpha",
            project_id=OTHER_PROJECT_ID,
            instance_id=OTHER_INSTANCE_ID,
        )
        assert "alpha-secret-skill" not in _entry_names(result)
        assert all(
            e.get("source") != SOURCE_PROJECT_FILE
            for e in result["injected"] + result["low_match"]
        )
        assert all(
            not str(e.get("address", "")).startswith("pf:")
            for e in result["injected"] + result["low_match"]
        )

    async def test_conflicting_caller_project_id_widens_nothing(
        self, tmp_path, monkeypatch, sqlite_engine  # noqa: F811
    ):
        """Instance A + caller-supplied project B (A6 refusal) ⇒ zero
        file skills, DB results unaffected."""
        service = await self._setup(tmp_path, sqlite_engine)
        install_selector(
            monkeypatch,
            {"selected": [{"name": "alpha-secret-skill", "score": 0.9}]},
        )
        result = await service.search(
            "alpha",
            project_id=OTHER_PROJECT_ID,
            instance_id=INSTANCE_ID,
        )
        assert "alpha-secret-skill" not in _entry_names(result)
        stats = service.file_corpus_stats()
        assert stats["refused"] is True
        assert stats["file_included"] == 0

    async def test_caller_supplied_project_id_without_instance_is_refused(
        self, tmp_path, monkeypatch, sqlite_engine  # noqa: F811
    ):
        """The REST shape: explicit project_id, no instance context."""
        service = await self._setup(tmp_path, sqlite_engine)
        install_selector(
            monkeypatch,
            {"selected": [{"name": "alpha-secret-skill", "score": 0.9}]},
        )
        result = await service.search(
            "alpha", project_id=TEST_PROJECT_ID, instance_id=None
        )
        assert "alpha-secret-skill" not in _entry_names(result)
        assert service.file_corpus_stats()["refused"] is True

    # ------------------------------------------------------------------
    # R3 (Phase-2 gate finding F3, folded 2026-10-31): the HARD
    # policy-exclusion shape. The pins above prove absence-by-no-root
    # (project B has no root binding in the fixture ⇒ ``roots=[]``,
    # ``refused=False`` — absence is INCIDENTAL). Here project B HAS
    # its own canon root; the exclusion under test is POLICY (the A6
    # refusal), proven by a same-fixture positive control where B's
    # own canon IS served.
    # ------------------------------------------------------------------

    async def _setup_two_canons(self, tmp_path, sqlite_engine):  # noqa: F811
        """Two projects, each with its OWN canon root + instance."""
        root_a = str(tmp_path / "proj-a-root")
        write_skill(
            root_a, "tester", "alpha-secret-skill",
            body_text="alpha alpha\n",
        )
        root_b = str(tmp_path / "proj-b-root")
        write_skill(
            root_b, "tester", "bravo-own-skill", body_text="bravo bravo\n"
        )
        instance_repo = FakeInstanceRepository(
            {
                INSTANCE_ID: FakeInstance(
                    INSTANCE_ID,
                    {"project_id": TEST_PROJECT_ID},
                    TEST_PROJECT_ID,
                ),
                OTHER_INSTANCE_ID: FakeInstance(
                    OTHER_INSTANCE_ID,
                    {"project_id": OTHER_PROJECT_ID},
                    OTHER_PROJECT_ID,
                ),
            }
        )
        project_repo = FakeProjectRepository(
            [
                FakeProject(TEST_PROJECT_ID, main_directory=root_a),
                FakeProject(OTHER_PROJECT_ID, main_directory=root_b),
            ]
        )
        file_source = make_service(
            sqlite_engine,
            instance_repo=instance_repo,
            project_repo=project_repo,
        )
        return make_search_service(
            skills=[
                make_skill(skill_id="db-b-1", name="alpha-db-b", content="alpha")
            ],
            file_source=file_source,
        )

    async def test_r3_positive_control_project_b_sees_its_own_canon(
        self, tmp_path, monkeypatch, sqlite_engine  # noqa: F811
    ):
        """Positive control: B's instance + B's project (no conflict)
        ⇒ B's OWN canon IS fused (file_included > 0). Proves the
        corpus machinery works for B — the exclusion pins below are
        policy, not absence."""
        service = await self._setup_two_canons(tmp_path, sqlite_engine)
        install_selector(
            monkeypatch,
            {"selected": [{"name": "bravo-own-skill", "score": 0.9}]},
        )
        result = await service.search(
            "bravo",
            project_id=OTHER_PROJECT_ID,
            instance_id=OTHER_INSTANCE_ID,
        )
        assert "bravo-own-skill" in _entry_names(result)
        stats = service.file_corpus_stats()
        assert stats["refused"] is False
        assert stats["file_included"] >= 1
        assert stats["project_id"] == OTHER_PROJECT_ID

    async def test_r3_hard_shape_b_canon_exists_but_conflict_refuses_all(
        self, tmp_path, monkeypatch, sqlite_engine  # noqa: F811
    ):
        """The hard shape: B HAS its own canon (proven by the positive
        control), but B's instance + caller-supplied project A ⇒ the
        resolver REFUSES the file corpus for the call — B's OWN canon
        is excluded too (refused=True, file_included==0), and project
        A's canon never leaks."""
        service = await self._setup_two_canons(tmp_path, sqlite_engine)
        install_selector(
            monkeypatch,
            {"selected": [{"name": "alpha-secret-skill", "score": 0.9}]},
        )
        result = await service.search(
            "alpha",
            project_id=TEST_PROJECT_ID,  # conflicts with B's derived
            instance_id=OTHER_INSTANCE_ID,
        )
        names = _entry_names(result)
        assert "alpha-secret-skill" not in names  # A's canon never leaks
        assert "bravo-own-skill" not in names  # B's OWN canon excluded too
        assert all(
            e.get("source") != SOURCE_PROJECT_FILE
            for e in result["injected"] + result["low_match"]
        )
        stats = service.file_corpus_stats()
        assert stats["refused"] is True
        assert stats["file_included"] == 0
        assert stats["roots"] == []


# ============================================================
# Task 4 — defensive post-stage-3 dedupe (synthetic drift)
# ============================================================


class TestDefensiveDedupe:
    def test_db_twin_dropped_when_it_reaches_stage_three(self, tmp_path):
        """Bypass the corpus shadow pass entirely: hand the tagger a
        stage-3 result carrying BOTH the file twin and a DB row of the
        same name. The DB twin must be dropped from both lists."""
        from daemon.services.project_skill_source.model import ProjectFileSkill

        file_skill = ProjectFileSkill(
            id="pf-deadbeefdeadbeef",
            address="pf:tester:alpha-skill",
            name="alpha-skill",
            description="canon",
            content="alpha body",
            namespace="tester",
            project_id=TEST_PROJECT_ID,
            body_path="alpha-skill.md",
        )
        db_twin = make_skill(
            skill_id="9e79ccad-1e2c-456b-8762-82d7f5eeec0f",
            name="alpha-skill",
            description="stale duplicate",
        )
        service = make_search_service(file_source=object())
        result = {
            "injected": [
                {"skill": file_skill, "score": 0.9},
                {"skill": db_twin, "score": 0.8},
            ],
            "low_match": [
                {"name": "alpha-skill", "score": 0.2, "description": "db"},
            ],
        }
        tagged = service._tag_result_sources(result, [file_skill, db_twin])
        assert len(tagged["injected"]) == 1
        assert tagged["injected"][0]["skill"] is file_skill
        assert tagged["injected"][0]["source"] == SOURCE_PROJECT_FILE
        # The low_match entry shares the file name; it is re-tagged as the
        # FILE entry (the DB twin identity is gone) — never a DB-sourced
        # duplicate of a shadowed name.
        assert len(tagged["low_match"]) == 1
        assert tagged["low_match"][0]["source"] == SOURCE_PROJECT_FILE
        assert all(
            "9e79ccad" not in json.dumps(entry) for entry in tagged["low_match"]
        )


# ============================================================
# Task 9 — degraded parity
# ============================================================


class TestDegradedParity:
    async def test_embeddings_down_file_skill_surfaces_with_neutral_score(
        self, tmp_path, monkeypatch, sqlite_engine  # noqa: F811
    ):
        """Stage 2 raises ⇒ BM25 fallback with ALL scores 0.0
        (byte-parity with the pre-fusion fallback); BM25 order preserved.

        S4 wording (architect advisory): the pin asserts the file skill
        SURFACES IN THE TOP 5. Rank 6-10 exclusion is BY-DESIGN — the
        BM25/LLM caps apply uniformly to both sources (uniform
        competition), not a file-source penalty.
        """
        root = str(tmp_path / "root")
        write_skill(root, "tester", "alpha-file", body_text="alpha alpha file\n")
        file_source = make_file_source(sqlite_engine, root=root)
        service = make_search_service(
            skills=[
                make_skill(skill_id=f"db-{i}", name=f"alpha-db-{i}", content="alpha")
                for i in range(3)
            ],
            file_source=file_source,
        )
        result = await service.search(
            "alpha",
            project_id=TEST_PROJECT_ID,
            max_results=5,
            instance_id=INSTANCE_ID,
        )
        names = _entry_names(result)
        assert "alpha-file" in names[:5]
        file_entries = [
            e for e in result["injected"] if e["source"] == SOURCE_PROJECT_FILE
        ]
        assert file_entries, "file skill must surface through the degraded path"
        assert file_entries[0]["score"] == 0.0

    async def test_llm_failure_degraded_select_includes_file_skills(
        self, tmp_path, monkeypatch, sqlite_engine  # noqa: F811
    ):
        root = str(tmp_path / "root")
        write_skill(root, "tester", "alpha-file", body_text="alpha alpha file\n")
        file_source = make_file_source(sqlite_engine, root=root)
        install_selector(monkeypatch, RuntimeError("selector model unavailable"))
        service = make_search_service(file_source=file_source)
        result = await service.search(
            "alpha",
            project_id=TEST_PROJECT_ID,
            max_results=5,
            instance_id=INSTANCE_ID,
        )
        assert any(e["source"] == SOURCE_PROJECT_FILE for e in result["injected"])


# ============================================================
# A8 — seam guard (service level)
# ============================================================


class TestFusionSeamGuard:
    async def test_broken_file_source_degrades_to_db_only(
        self, monkeypatch
    ):
        install_selector(
            monkeypatch, {"selected": [{"name": "alpha-db", "score": 0.9}]}
        )
        service = make_search_service(
            skills=[make_skill(name="alpha-db", content="alpha db")],
            file_source=_BrokenFileSource(),
        )
        result = await service.search(
            "alpha", project_id=TEST_PROJECT_ID, instance_id=INSTANCE_ID
        )
        assert [e["skill"].name for e in result["injected"]] == ["alpha-db"]
        assert "A8" in (service.file_corpus_stats().get("last_error") or "")
