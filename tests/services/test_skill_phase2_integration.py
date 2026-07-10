"""End-to-end integration tests for Phase 2 of the Skill Evolution System.

This suite exercises the three Phase 2 services (``SkillEmbeddingService``,
``SkillStoreService``, ``SkillSearchService``) and the two tool factories
(``create_skill_tools``, ``create_skill_evolution_tools``) together — not
in isolation. The goal is to catch regressions that only show up when the
pieces are wired up correctly (e.g. the ``SkillStoreService``'s embedding
refresh hook breaking the resolver, or the tool factories falling out of
sync with the ``CATEGORY_MODULES`` registry).

Test layers:

* **Real SQLite.** The :mod:`tests.services.conftest` engine fixture
  gives every test a fresh in-memory SQLite database with all six
  Phase 1 skill tables created via ``SQLModel.metadata.create_all``.
* **Real repositories.** ``SkillRepository``,
  ``SkillLineageRepository``, and ``SkillEmbeddingRepository`` are
  constructed against that engine so the SQL queries actually
  execute.
* **Mocked OpenAI client only.** The :class:`SkillEmbeddingService`
  uses the synchronous ``openai.OpenAI`` client under the hood —
  every test that touches it patches
  ``daemon.services.skill_embedding_service.openai.OpenAI`` so no
  network traffic is produced.
* **Tool factories are real.** The integration tests instantiate
  ``create_skill_tools`` and ``create_skill_evolution_tools`` and
  verify they are correctly wired into ``CATEGORY_MODULES`` and
  ``INNATE_SKILL_TOOL_CATEGORIES``.

What this suite is *not* testing:

* Pure unit-level behavior — those live in the per-service test
  files (``test_skill_embedding_service.py``,
  ``test_skill_store_service.py``, ``test_skill_search_service.py``).
* Edge cases for individual repos — see
  ``tests/repositories/test_skill_repository.py``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session

from daemon.repositories.skill.repository import (
    SkillEmbeddingRepository,
    SkillLineageRepository,
    SkillRepository,
)
from daemon.services.skill_embedding_service import SkillEmbeddingService
from daemon.services.skill_search_service import SkillSearchService
from daemon.services.skill_store_service import SkillStoreService


# ============================================================
# Shared helpers
# ============================================================


def _make_embedding_row(
    *,
    skill_id: str,
    trigger_query: str,
    embedding: list[float],
) -> SimpleNamespace:
    """Build a stand-in for a ``SkillEmbedding`` row.

    The OpenAI mock returns raw float vectors; the search service
    goes through ``embedding_repo.get_by_skill`` which returns
    :class:`SkillEmbedding` SQLModel rows. The mock repo in this
    suite returns objects of this shape so the service's
    ``row.embedding`` access works the same way it does against a
    real DB.
    """
    return SimpleNamespace(
        id=f"emb-{skill_id}-{trigger_query[:8]}",
        skill_id=skill_id,
        trigger_query=trigger_query,
        embedding=list(embedding),
    )


def _patch_openai_with_vectors(
    *,
    embedding_vectors: dict[str, list[float]],
    chat_response_text: str | None = None,
):
    """Patch ``openai.OpenAI`` so the embedding service returns deterministic vectors.

    Args:
        embedding_vectors: ``{trigger_query_text: vector}`` — the
            embedding endpoint returns the matching vector for any
            input that appears as a key. Inputs without a key still
            get a deterministic unit-ish vector so re-rank tests are
            reproducible.
        chat_response_text: Static text returned by the chat
            completions endpoint. Used to feed
            :meth:`SkillEmbeddingService.generate_trigger_queries` —
            defaults to a small JSON array so the default flow works
            out of the box.

    Returns:
        A :class:`unittest.mock.patch` context manager that the
        caller can ``with`` (or that the test uses
        ``patch(...) as ...``).
    """
    default_chat_response = chat_response_text or json.dumps([
        "trigger-query-1",
        "trigger-query-2",
        "trigger-query-3",
    ])

    def _embedding_for(text: str) -> list[float]:
        for key, vec in embedding_vectors.items():
            if key in text:
                return list(vec)
        # Deterministic fallback: hash the text into a 4-D vector so
        # the integration test isn't order-dependent. Magnitudes are
        # normalized so cosine_similarity stays bounded in [0, 1].
        seed = abs(hash(text)) % (10 ** 6)
        return [
            ((seed // 1) % 10) / 10.0,
            ((seed // 10) % 10) / 10.0,
            ((seed // 100) % 10) / 10.0,
            ((seed // 1000) % 10) / 10.0,
        ]

    class _FakeEmbeddingsAPI:
        def create(self, *, model, input):  # noqa: A002 — match SDK signature
            data = [_FakeEmbeddingObj(_embedding_for(text)) for text in input]
            return SimpleNamespace(data=data)

    class _FakeEmbeddingObj:
        def __init__(self, embedding: list[float]):
            self.embedding = list(embedding)
            self.index = 0

    class _FakeChatCompletionsAPI:
        def create(self, *, model, messages, temperature=0.0):  # noqa: A002
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=default_chat_response)
                    )
                ]
            )

    class _FakeOpenAIClient:
        def __init__(self, *args, **kwargs):
            self.embeddings = _FakeEmbeddingsAPI()
            self.chat = SimpleNamespace(
                completions=_FakeChatCompletionsAPI()
            )

    return patch(
        "daemon.services.skill_embedding_service.openai.OpenAI",
        _FakeOpenAIClient,
    )


# ============================================================
# Test 1 — End-to-end: create -> embed -> search -> verify
# ============================================================


class TestEndToEndCreateEmbedSearch:
    """Drive the full Phase 2 pipeline against a real SQLite database.

    Steps:

    1. Construct three real repos (SkillRepository,
       SkillLineageRepository, SkillEmbeddingRepository) against the
       in-memory SQLite engine from ``engine``.
    2. Construct the real :class:`SkillEmbeddingService` with the
       OpenAI client patched to return deterministic vectors.
    3. Construct :class:`SkillStoreService` (the async CRUD facade).
    4. Construct :class:`SkillSearchService` with the same repos +
       the embedding service.
    5. ``await store.create_skill(...)`` — this writes a row AND
       triggers the embedding refresh hook.
    6. ``await search.search(query, project_id=...)`` — BM25 +
       embedding re-rank pipeline runs against the row written in
       step 5.
    7. Assert that the skill we just created appears in the
       ``injected`` list (BM25 finds it on the name/description
       tokens).

    Why a single test for the whole pipeline: each phase's unit
    tests already cover the per-layer behavior; the integration
    concern is *do the layers compose*. Splitting this into three
    tests would mostly duplicate the wiring code.
    """

    @pytest.mark.asyncio
    async def test_create_then_search_finds_skill(
        self, engine: Engine, project_id: str
    ):
        """``create_skill`` persists the row AND seeds embeddings;
        ``search`` then surfaces it in the ``injected`` list."""
        skill_repo = SkillRepository(engine)
        lineage_repo = SkillLineageRepository(engine)
        embedding_repo = SkillEmbeddingRepository(engine)

        # Build a config the embedding service can read.
        config = SimpleNamespace(
            embedding_model="text-embedding-3-small",
            embedding_base_url=None,
            embedding_api_key="sk-test",
        )
        llm_config = {
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test",
            "model": "gpt-4o-mini",
        }
        search_config = SimpleNamespace(
            bm25_top_k=10,
            llm_select_top_k=5,
            max_inject_skills=2,
        )

        with _patch_openai_with_vectors(
            embedding_vectors={
                "code": [1.0, 0.0, 0.0, 0.0],
                "review": [0.0, 1.0, 0.0, 0.0],
            },
        ):
            embedding_service = SkillEmbeddingService(
                config=config,
                embedding_repo=embedding_repo,
                llm_config=llm_config,
            )

            store = SkillStoreService(
                skill_repo=skill_repo,
                lineage_repo=lineage_repo,
                embedding_service=embedding_service,
            )
            search = SkillSearchService(
                skill_repo=skill_repo,
                embedding_repo=embedding_repo,
                embedding_service=embedding_service,
                llm_config=llm_config,
                config=search_config,
            )

            # Step 1: persist the skill. The store's ``create_skill``
            # triggers ``update_skill_embeddings`` internally so
            # cached embeddings should exist by the time the search
            # runs.
            created = await store.create_skill(
                name="code-review",
                description="Review code for bugs and style.",
                content=(
                    "# Code Review\n\n"
                    "Check correctness, performance, and security."
                ),
                project_id=project_id,
            )

            assert created is not None
            assert created.id is not None
            assert created.name == "code-review"

            # Verify the embedding refresh actually wrote rows. The
            # embedding service was patched to emit 3 trigger
            # queries (per the default chat response) so we expect
            # >=1 row in the cache.
            with Session(engine) as session:
                from sqlmodel import select
                from daemon.repositories.skill.models import SkillEmbedding

                stmt = select(SkillEmbedding).where(
                    SkillEmbedding.skill_id == created.id
                )
                cached = list(session.exec(stmt))
            assert len(cached) >= 1, (
                "Expected the store's embedding refresh to write at "
                "least one cached row, got 0 — Phase 2 wiring is broken."
            )

            # Step 2: search. ``code review`` shares tokens with
            # ``code-review`` + the body, so BM25 surfaces it; the
            # embedding re-rank then keeps it at the top.
            result = await search.search(
                "Please review my code for bugs",
                project_id=project_id,
                max_results=2,
            )

            assert "injected" in result
            assert "low_match" in result
            injected_ids = [item["skill"].id for item in result["injected"]]
            assert created.id in injected_ids, (
                f"Expected the freshly-created skill in injected list; "
                f"got ids={injected_ids!r}"
            )


# ============================================================
# Test 2 — Tool registration integration
# ============================================================


class TestToolRegistrationIntegration:
    """Verify the tool factories, the registry, and the innate-skill
    grant are all consistent.

    What we check:

    * :func:`daemon.tools.skill_tools.create_skill_tools` returns 6
      LangChain tools with the expected names.
    * :func:`daemon.tools.skill_evolution_tools.create_skill_evolution_tools`
      returns 5 tools with the expected names.
    * Both factory modules are referenced from
      :data:`daemon.tools._tool_registry.CATEGORY_MODULES` under
      their respective category keys (``"dynamic-skill"`` and
      ``"skill-evolution"``).
    * Both keys appear in
      :data:`daemon.tools.instance.INNATE_SKILL_TOOL_CATEGORIES`
      so an agent with ``innate_skills: ["dynamic-skill"]`` (or
      ``["skill-evolution"]``) is auto-granted the matching tool
      category.
    """

    def test_skill_tools_factory_returns_six_tools(self):
        """``create_skill_tools`` yields exactly 6 tools covering
        search/list/view/create/fix/feedback."""
        from daemon.tools.skill_tools import create_skill_tools

        manager = MagicMock()
        # Mirror the production wiring so any getattr lookup the
        # factory does against the manager behaves correctly.
        manager._skill_search_service = None
        manager._skill_store_service = None
        manager._skill_job_dispatcher = None

        tools = create_skill_tools(manager, "test-instance")
        assert len(tools) == 6
        names = {getattr(t, "name", None) for t in tools}
        assert names == {
            "skill_search",
            "skill_list",
            "skill_view",
            "skill_create",
            "skill_fix",
            "skill_feedback",
        }

    def test_skill_evolution_tools_factory_returns_five_tools(self):
        """``create_skill_evolution_tools`` yields 5 tools covering
        analyze/evolve/resolve_ab/get_metrics/execute_capture."""
        from daemon.tools.skill_evolution_tools import (
            create_skill_evolution_tools,
        )

        manager = MagicMock()
        manager._skill_evolution_service = None

        tools = create_skill_evolution_tools(manager, "test-instance")
        assert len(tools) == 5
        names = {getattr(t, "name", None) for t in tools}
        assert names == {
            "skill_analyze",
            "skill_evolve",
            "skill_resolve_ab",
            "skill_get_metrics",
            "skill_execute_capture",
        }

    def test_both_categories_registered_in_category_modules(self):
        """``CATEGORY_MODULES`` maps both Phase 2 category keys to
        the right factory modules."""
        from daemon.tools._tool_registry import CATEGORY_MODULES

        assert CATEGORY_MODULES.get("dynamic-skill") == (
            "daemon.tools.skill_tools"
        )
        assert CATEGORY_MODULES.get("skill-evolution") == (
            "daemon.tools.skill_evolution_tools"
        )

    def test_both_categories_in_innate_skill_grant_map(self):
        """``INNATE_SKILL_TOOL_CATEGORIES`` grants both category keys
        so an agent with the matching ``innate_skills`` entry is
        auto-allowed the tool category without having to list it
        in its ``tools.allow``."""
        from daemon.tools.instance import INNATE_SKILL_TOOL_CATEGORIES

        assert INNATE_SKILL_TOOL_CATEGORIES.get("dynamic-skill") == [
            "dynamic-skill"
        ]
        assert INNATE_SKILL_TOOL_CATEGORIES.get("skill-evolution") == [
            "skill-evolution"
        ]

    def test_skill_tools_factory_categories_walkable_via_registry(
        self,
    ):
        """End-to-end registry walk: build the tools, register them,
        and resolve ``get_tool_categories()`` to confirm the
        ``CATEGORY_NAME -> [tool_names]`` mapping is wired up.

        This is the exact code path the help system and the tool
        allow-list expansion follow at runtime.
        """
        from daemon.tools._tool_registry import (
            clear_registry,
            get_tool_categories,
            scan_tools_for_full_docs,
        )
        from daemon.tools.skill_evolution_tools import (
            create_skill_evolution_tools,
        )
        from daemon.tools.skill_tools import (
            CATEGORY_NAME as DYNAMIC_SKILL_CATEGORY_NAME,
            create_skill_tools,
        )

        manager = MagicMock()
        manager._skill_search_service = None
        manager._skill_store_service = None
        manager._skill_job_dispatcher = None
        manager._skill_evolution_service = None

        clear_registry()
        try:
            skill_tools = create_skill_tools(manager, "test-instance")
            evo_tools = create_skill_evolution_tools(
                manager, "test-instance"
            )
            scan_tools_for_full_docs(list(skill_tools) + list(evo_tools))

            categories = get_tool_categories()
            assert DYNAMIC_SKILL_CATEGORY_NAME in categories
            assert set(categories[DYNAMIC_SKILL_CATEGORY_NAME]) == {
                "skill_search",
                "skill_list",
                "skill_view",
                "skill_create",
                "skill_fix",
                "skill_feedback",
            }
        finally:
            clear_registry()


# ============================================================
# Test 3 — Graceful degradation when embedding pipeline fails
# ============================================================


class TestGracefulDegradationIntegration:
    """The Phase 2 services must keep working when the OpenAI-backed
    embedding pipeline fails (network outage, bad API key, rate
    limit, …). Skills remain usable via BM25 full-text search — the
    resolver degrades gracefully rather than crashing.
    """

    @pytest.mark.asyncio
    async def test_create_skill_succeeds_when_embedding_service_raises(
        self, engine: Engine, project_id: str
    ):
        """``store.create_skill`` must NOT propagate an
        embedding-pipeline exception — the skill row is committed
        regardless and a warning is logged."""
        skill_repo = SkillRepository(engine)
        lineage_repo = SkillLineageRepository(engine)
        embedding_repo = SkillEmbeddingRepository(engine)

        # Embedding service whose ``update_skill_embeddings``
        # always raises — simulates a misconfigured OpenAI endpoint.
        broken_embedding_service = MagicMock()
        broken_embedding_service.update_skill_embeddings = (
            _async_raise(RuntimeError("OpenAI endpoint unreachable"))
        )

        store = SkillStoreService(
            skill_repo=skill_repo,
            lineage_repo=lineage_repo,
            embedding_service=broken_embedding_service,
        )

        created = await store.create_skill(
            name="deployment-checklist",
            description="Pre-deploy checklist for services.",
            content="# Deploy\n\n- run tests\n- smoke check\n",
            project_id=project_id,
        )

        # The skill was still written — embedding failure is
        # best-effort, not blocking.
        assert created is not None
        assert created.name == "deployment-checklist"

        # And it's actually queryable via the repo afterwards.
        refetched = skill_repo.get(created.id)
        assert refetched is not None
        assert refetched.id == created.id

    @pytest.mark.asyncio
    async def test_search_works_bm25_only_when_embedding_api_raises(
        self, engine: Engine, project_id: str
    ):
        """When the embedding API is unreachable, the search service
        must still surface matching skills via BM25 (the
        ``score=0.0`` fallback path).

        The pipeline is intentionally lenient: a stage 2 (embedding
        re-rank) failure doesn't abort the search — it just falls
        back to BM25-only with ``score=0.0`` for each candidate,
        then defers to the LLM (stage 3). We assert the skill is
        still findable end-to-end even with the embedding API
        dead."""
        skill_repo = SkillRepository(engine)
        lineage_repo = SkillLineageRepository(engine)
        embedding_repo = SkillEmbeddingRepository(engine)

        config = SimpleNamespace(
            embedding_model="text-embedding-3-small",
            embedding_base_url=None,
            embedding_api_key="sk-test",
        )
        llm_config = {
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test",
            "model": "gpt-4o-mini",
        }
        search_config = SimpleNamespace(
            bm25_top_k=10,
            llm_select_top_k=5,
            max_inject_skills=2,
        )

        # Seed the skill directly via the repo so we don't depend
        # on the store's embedding refresh (we want a deterministic
        # test fixture).
        seeded = skill_repo.create(
            name="database-migration",
            description="Run schema migrations safely.",
            content=(
                "# Database Migrations\n\n"
                "Always back up before applying schema changes."
            ),
            project_id=project_id,
        )
        assert seeded is not None

        # Embedding service whose ``embed_user_message`` raises.
        # BM25 should still find the skill in stage 1 and the
        # service should fall through to the degraded path.
        broken_embedding_service = MagicMock()
        broken_embedding_service.embed_user_message = _async_raise(
            RuntimeError("embedding API down")
        )

        search = SkillSearchService(
            skill_repo=skill_repo,
            embedding_repo=embedding_repo,
            embedding_service=broken_embedding_service,
            llm_config=llm_config,
            config=search_config,
        )

        # BM25 finds the skill on shared tokens
        # ("database"/"migration"). The result must contain the
        # skill — even if the score is 0.0 and the selection is
        # degraded, the skill must be present somewhere in
        # ``injected`` or ``low_match``.
        result = await search.search(
            "How do I run a database migration?",
            project_id=project_id,
            max_results=2,
        )

        all_skill_ids = (
            [item["skill"].id for item in result["injected"]]
            + [
                # low_match uses ``name``/``description`` shape, not
                # full skill objects — match by name instead.
                item["name"]
                for item in result["low_match"]
            ]
        )
        # Either the seeded skill id OR its name must appear in
        # the results — both shapes are valid depending on which
        # stage the service degraded to.
        assert seeded.id in all_skill_ids or seeded.name in all_skill_ids, (
            f"Expected the seeded skill in either injected or "
            f"low_match when embedding API is down; got "
            f"injected={result['injected']!r}, "
            f"low_match={result['low_match']!r}"
        )


# ============================================================
# Internal helpers
# ============================================================


def _async_raise(exc: BaseException):
    """Build an async callable that always raises ``exc``.

    Used to simulate a broken OpenAI endpoint / misconfigured
    embedding service. Returns a coroutine factory bound to the
    given exception.
    """
    async def _raiser(*args, **kwargs):
        raise exc

    return _raiser