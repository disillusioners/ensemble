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
@pytest.mark.parametrize("mode", ["resolve", "corpus"])
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
