"""A8 seam-guard pin at the TOOL RESULT SURFACE (Phase-2, architect A8).

The contract (MUST, CRITICAL): a broken file source must NEVER turn the
agent-visible tool result into the ``"ERROR: skill_search failed: …"``
string. The service-level guard is necessary but NOT sufficient — the
pin has to assert on the string the agent actually receives
(``skill_tools.py:167`` is the escape site; it is NOT modified).

Fixture: a healthy DB corpus + a file source that raises on every seam
call. Expected: the tool returns valid JSON with the DB results and no
error envelope.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import daemon.services.skill_search_service as sss_module
from daemon.services.skill_search_service import SkillSearchService

PROJECT_ID = "11111111-2222-3333-4444-555555555555"
INSTANCE_ID = "inst-guard-1"

ERROR_STRING = "ERROR: skill_search failed"


def _chat_response(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


class _HealthyDbRepo:
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


class _ExplodingFileSource:
    """Raises on every seam entry point (modes M3/M7/M8)."""

    def __init__(self, mode: str = "resolve") -> None:
        self.mode = mode

    async def resolve_active_project(self, instance_id=None, explicit_project_id=None):
        if self.mode == "resolve":
            raise RuntimeError("root unreadable (M3 fixture)")
        return SimpleNamespace(project_id=PROJECT_ID, roots=["/nonexistent-root"], refused=False)

    async def corpus_for(self, project_id, root):
        raise RuntimeError("corpus raises (M8 fixture)")


def _make_tools(file_source):
    from daemon.tools.skill_tools import create_skill_tools

    service = SkillSearchService(
        skill_repo=_HealthyDbRepo(
            [
                SimpleNamespace(
                    id="db-skill-1",
                    name="alpha-db-skill",
                    description="alpha database skill",
                    content="alpha",
                    project_id=PROJECT_ID,
                    is_active=True,
                    status="active",
                    source_skill_bank_id=None,
                )
            ]
        ),
        embedding_repo=_EmptyEmbeddingRepo(),
        embedding_service=_DownEmbedding(),
        llm_config={"selector_model": "quick", "base_url": "http://127.0.0.1:9/v1"},
        config=SimpleNamespace(
            bm25_top_k=10, llm_select_top_k=5, max_inject_skills=2
        ),
        file_source=file_source,
    )
    manager = MagicMock()
    manager._skill_search_service = service
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(
        return_value=SimpleNamespace(project_id=PROJECT_ID, agent_id=None)
    )
    tools = {t.name: t for t in create_skill_tools(manager, INSTANCE_ID)}
    return tools, service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        # OUTER A8 seam (skill_search_service.py:1149 — the try/except
        # around the _merge_project_file_corpus CALL): the resolver
        # raises BEFORE the per-root loop is reached, so the failure
        # escapes ``_merge_project_file_corpus`` and is caught by the
        # outer guard only.
        "resolve",
        # PER-ROOT resilience (A8 layer 1, :789-797 — INSIDE
        # ``_merge_project_file_corpus``): ``corpus_for`` raises for
        # one root; the handler absorbs it and the remaining roots
        # still contribute. This mode does NOT exercise the outer
        # try/except (Phase-2 gate finding F2 / rider R2 — the split
        # is deliberate; the direct outer-seam pin below covers the
        # guard itself).
        "corpus",
    ],
    ids=["outer-a8-seam-resolver-raises", "per-root-layer1-corpus-raises"],
)
async def test_broken_file_source_never_surfaces_the_error_string(
    monkeypatch, mode
):
    def _invoke(fn, cfg):
        return _chat_response(
            json.dumps({"selected": [{"name": "alpha-db-skill", "score": 0.9}]})
        )

    monkeypatch.setattr(
        sss_module, "invoke_raw_with_failover", _invoke, raising=True
    )

    tools, service = _make_tools(_ExplodingFileSource(mode=mode))
    result = await tools["skill_search"].ainvoke({"query": "alpha"})

    assert ERROR_STRING not in result
    payload = json.loads(result)
    assert [e["skill"]["id"] for e in payload["injected"]] == ["db-skill-1"]
    assert payload["injected"][0]["source"] == "db"
    assert service.file_corpus_stats().get("last_error")


@pytest.mark.asyncio
async def test_outer_a8_seam_direct_pin(monkeypatch):
    """R2 (Phase-2 gate finding F2): exercise the OUTER A8 try/except
    DIRECTLY — a failure raised by the fusion call itself, bypassing
    the per-root handler entirely.

    Fixture: a HEALTHY file source whose merge is monkeypatched to
    raise. Expected: the guard at the ``_merge_project_file_corpus``
    call site catches it — the tool returns the DB results as JSON
    (never the raw error string), and ``file_corpus_stats()``
    records ``last_error`` with the exception class name.
    """
    def _invoke(fn, cfg):
        return _chat_response(
            json.dumps({"selected": [{"name": "alpha-db-skill", "score": 0.9}]})
        )

    monkeypatch.setattr(
        sss_module, "invoke_raw_with_failover", _invoke, raising=True
    )

    healthy = SimpleNamespace()

    async def _resolve(instance_id=None, explicit_project_id=None):
        return SimpleNamespace(
            project_id=PROJECT_ID, roots=["/unused-root"], refused=False
        )

    healthy.resolve_active_project = _resolve

    async def _exploding_merge(self, items, project_id, instance_id):
        raise RuntimeError("outer seam direct (R2 fixture)")

    tools, service = _make_tools(healthy)
    # Patch on the INSTANCE so the seam itself is the only thing
    # changed — the per-root handler inside the real merge never runs.
    monkeypatch.setattr(
        service, "_merge_project_file_corpus", _exploding_merge.__get__(service)
    )

    result = await tools["skill_search"].ainvoke({"query": "alpha"})

    assert ERROR_STRING not in result
    assert "outer seam direct" not in result  # raw error never surfaces
    payload = json.loads(result)
    assert [e["skill"]["id"] for e in payload["injected"]] == ["db-skill-1"]
    stats = service.file_corpus_stats()
    assert stats.get("last_error") == "RuntimeError: outer seam direct (R2 fixture)"
    assert stats.get("file_included") == 0


@pytest.mark.asyncio
async def test_outer_a8_seam_service_level_sibling(monkeypatch):
    """R2 service-level sibling: ``search()`` itself must not raise
    when the fusion call raises at the outer layer (the guard
    degrades to DB-only results)."""

    async def _resolve(instance_id=None, explicit_project_id=None):
        return SimpleNamespace(
            project_id=PROJECT_ID, roots=["/unused-root"], refused=False
        )

    tools, service = _make_tools(
        SimpleNamespace(resolve_active_project=_resolve)
    )

    async def _boom(self, items, project_id, instance_id):
        raise RuntimeError("service sibling outer seam")

    monkeypatch.setattr(
        service, "_merge_project_file_corpus", _boom.__get__(service)
    )
    result = await service.search(
        "alpha", project_id=PROJECT_ID, instance_id=INSTANCE_ID
    )
    assert any(
        e.get("skill") and getattr(e["skill"], "id", None) == "db-skill-1"
        for e in result["injected"]
    )
    assert "RuntimeError: service sibling outer seam" == (
        service.file_corpus_stats().get("last_error")
    )


# ============================================================
# A3(a) — accessor unification (tidier fold)
# ============================================================


@pytest.mark.asyncio
async def test_fusion_stats_accessors_unified_and_none_tolerant():
    """A3(a): the property DELEGATES to the method, and the single
    tolerant ``or {}`` form lives in ONE place.

    Pre-fold the two accessors had drifted — the property guarded a
    ``None`` snapshot, the method did not, so a partially-constructed
    service (or a test double assigning ``None``) raised on exactly one
    of the two paths. Both must read the honest empty zero.
    """
    tools, service = _make_tools(file_source=None)
    assert service.file_corpus_stats() == {}
    assert service.last_file_corpus_stats == {}

    service._last_file_corpus_stats = None  # the drifted shape
    assert service.file_corpus_stats() == {}
    assert service.last_file_corpus_stats == {}
