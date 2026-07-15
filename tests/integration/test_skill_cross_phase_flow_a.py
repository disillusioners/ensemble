"""Cross-phase integration tests for the Skill Evolution System.

Flow A — Create → Search → Inject → Metrics → Feedback
======================================================

This test file exercises a continuous cross-phase integration flow that
verifies the Skill Evolution System's services work together as a single
pipeline when wired through real repositories and a real in-memory
SQLite database.

The Skill Evolution System spans six phases:

* **Phase 1** — Repositories (SQLModel CRUD on six tables).
* **Phase 2** — Store / Search / Embedding services.
* **Phase 3** — Injection service (A/B routing + formatting).
* **Phase 4** — Metrics service (per-task recording + feedback).
* **Phase 5** — Evolution service (skill capture / A/B resolve).
* **Phase 6** — REST API surface.

Flow A verifies the most common end-to-end happy path used by the
LangGraph agent runtime: a skill is created, surfaced for an incoming
message, formatted into a `[System Inject]` block, attributed back to
the per-task metrics record, and finally graded by user feedback.

Mocking policy
--------------

Per the project guideline, only **external LLM endpoints** are mocked:

* The ``SkillEmbeddingService`` instance is a ``MagicMock`` — the three
  methods invoked by the services under test
  (``update_skill_embeddings``, ``embed_user_message``,
  ``cosine_similarity``) return deterministic stand-ins.
* The chat-completion endpoint in stage 3 of search is intercepted by
  monkeypatching :data:`daemon.services.skill_search_service.openai.OpenAI`
  to return a mock client whose ``chat.completions.create`` returns a
  parseable JSON ``{"selected": [...], "low_match": [...]}``.

Everything else is **real**:

* Six repositories (Phase 1) are wired through a real ``Engine``.
* The six SQLModel tables are created via
  :func:`SQLModel.metadata.create_all` on the in-memory database.
* All services are constructed normally with the real repositories.
* The Pure-Python BM25 prefilter in :class:`SkillSearchService` runs
  on real rows fetched from the in-memory DB.
* The :class:`SkillInjectionService` formatter and A/B router run
  against real skill rows.
* The :class:`SkillMetricsService` records feedback on real usage
  rows.

Test isolation
--------------

The engine fixture is **self-contained** — it does not import the
``tests/repositories/conftest.py`` fixtures (which are directory-scoped
and may not be visible depending on ``pytest`` ``rootdir`` config).
Instead, the engine is built inline with the same ``StaticPool`` +
``PRAGMA foreign_keys=ON`` + ``SQLModel.metadata.create_all`` pattern
used in the existing fixtures.

Each test instantiates a fresh engine + repository set, so tests run in
isolation without any cross-test state leakage.

What this file does NOT test
----------------------------

* :class:`SkillEvolutionService` (Phase 5) — covered by
  ``tests/services/test_skill_evolution_service.py``.
* The CAPTURED-flow eligibility check inside
  :meth:`SkillMetricsService.record_task_completion` — disabled by
  passing ``evolution_service=None``.
* REST API surface — covered by
  ``tests/integration/test_skill_evolution_e2e.py``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

pytestmark = pytest.mark.integration


# =============================================================================
# Engine + repositories (self-contained — no cross-directory conftest)
# =============================================================================


def _build_engine() -> Engine:
    """Build a fresh in-memory SQLite engine with all six skill tables.

    Mirrors the engine setup used by
    :mod:`tests.repositories.conftest`: ``StaticPool`` so the
    in-memory DB survives across threads, ``PRAGMA foreign_keys=ON``
    so cascading deletes on the skill FKs fire, and
    :func:`SQLModel.metadata.create_all` to create every table
    currently registered on the global SQLModel metadata.

    Importing the model module is REQUIRED for ``create_all`` to
    pick up the tables — the metadata is populated at class
    definition time, not at engine creation time.

    Returns:
        A configured :class:`Engine` ready for repository use.
    """

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Import models so SQLModel.metadata knows about the six tables.
    from daemon.repositories.skill.models import (
        Skill,
        SkillABTest,
        SkillEmbedding,
        SkillLineage,
        SkillTrigger,
        SkillUsageRecord,
    )

    _ = (Skill, SkillLineage, SkillUsageRecord, SkillTrigger,
         SkillEmbedding, SkillABTest)
    SQLModel.metadata.create_all(engine)
    return engine


def _build_repositories(engine: Engine) -> SimpleNamespace:
    """Instantiate every skill repository bound to ``engine``.

    Returns:
        A :class:`SimpleNamespace` exposing ``skill``, ``lineage``,
        ``usage``, ``trigger``, ``embedding``, and ``ab_test``
        repository handles. The shape mirrors
        ``tests.integration.test_skill_evolution_e2e``'s
        ``repos`` fixture for ease of cross-test reading.
    """
    from daemon.repositories.skill.repository import (
        SkillABTestRepository,
        SkillEmbeddingRepository,
        SkillLineageRepository,
        SkillRepository,
        SkillTriggerRepository,
        SkillUsageRepository,
    )

    return SimpleNamespace(
        skill=SkillRepository(engine),
        lineage=SkillLineageRepository(engine),
        usage=SkillUsageRepository(engine),
        trigger=SkillTriggerRepository(engine),
        embedding=SkillEmbeddingRepository(engine),
        ab_test=SkillABTestRepository(engine),
    )


# =============================================================================
# Helper classes — minimal stand-ins for production dependencies
# =============================================================================


class FakeInstance:
    """Minimal stand-in for the Instance row.

    Implements only the surface the metrics service reads:
    :attr:`instance_id` and :attr:`instance_metadata`. Mirrors the
    equivalent class in
    :mod:`tests.services.test_skill_metrics_service`.
    """

    def __init__(
        self,
        instance_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        self.id = instance_id
        self.instance_id = instance_id
        self.instance_metadata = dict(metadata or {})


class FakeInstanceRepo:
    """In-memory replacement for :class:`SQLModelInstanceRepository`.

    The metrics service reads ``last_injected_skill_ids`` from
    ``instance_repo.get(instance_id).instance_metadata`` and clears
    the key via ``delete_metadata(instance_id, key)`` after recording.

    Attributes:
        _instances: Map from ``instance_id`` to :class:`FakeInstance`.
    """

    def __init__(self) -> None:
        self._instances: dict[str, FakeInstance] = {}

    # -- Public API used by SkillMetricsService ---------------------

    def get(self, instance_id: str) -> Optional[FakeInstance]:
        """Return the fake instance, or ``None`` when missing."""
        return self._instances.get(instance_id)

    def delete_metadata(self, instance_id: str, key: str) -> Any:
        """Delete ``key`` from the instance's metadata (no-op when missing)."""
        inst = self._instances.get(instance_id)
        if inst is not None and key in inst.instance_metadata:
            del inst.instance_metadata[key]
        return inst

    def set_metadata(
        self,
        instance_id: str,
        key: str,
        value: Any,
    ) -> Any:
        """Set ``key=value`` on the instance's metadata.

        Auto-creates the instance row when ``instance_id`` is new —
        this matches the production contract where the message-processing
        pipeline creates the instance before injecting skills.
        """
        inst = self._instances.get(instance_id)
        if inst is None:
            inst = FakeInstance(instance_id, {key: value})
            self._instances[instance_id] = inst
        else:
            inst.instance_metadata[key] = value
        return inst

    # -- Test helpers ----------------------------------------------

    def seed(self, instance_id: str, metadata: Optional[dict] = None) -> FakeInstance:
        """Insert (or replace) a fake instance with the given metadata.

        Convenience used by the test setup to materialize an instance
        row before the metrics service reads it.
        """
        inst = FakeInstance(instance_id, metadata)
        self._instances[instance_id] = inst
        return inst


class FakeConfig:
    """Minimal :class:`SkillEvolutionConfig` stub.

    Only the attributes the services under test actually read are
    populated. Default values mirror the production defaults from
    :class:`daemon.config.SkillEvolutionConfig` so the test runs
    match what production code sees.

    Attributes:
        bm25_top_k: Top-K cutoff for the BM25 prefilter.
        llm_select_top_k: Top-K cutoff after embedding re-rank.
        max_inject_skills: Maximum skills injected per task.
        ab_sample_size: Required comparison count for A/B resolve.
        ab_min_difference: Minimum effect size for A/B resolve.
        max_extensions: Hard cap on A/B test extensions.
        capture_min_iterations: Phase 5 gate (unused here).
        capture_min_duration_seconds: Phase 5 gate (unused here).
    """

    def __init__(
        self,
        *,
        bm25_top_k: int = 10,
        llm_select_top_k: int = 5,
        max_inject_skills: int = 2,
        ab_sample_size: int = 10,
        ab_min_difference: float = 0.15,
        max_extensions: int = 3,
        capture_min_iterations: int = 5,
        capture_min_duration_seconds: int = 60,
    ) -> None:
        self.bm25_top_k = bm25_top_k
        self.llm_select_top_k = llm_select_top_k
        self.max_inject_skills = max_inject_skills
        self.ab_sample_size = ab_sample_size
        self.ab_min_difference = ab_min_difference
        self.max_extensions = max_extensions
        self.capture_min_iterations = capture_min_iterations
        self.capture_min_duration_seconds = capture_min_duration_seconds


# =============================================================================
# Mock factories
# =============================================================================


def make_embedding_service_mock() -> MagicMock:
    """Build a mock :class:`SkillEmbeddingService` for the full flow.

    Implements exactly the three methods consumed by the services
    under test:

    * :meth:`update_skill_embeddings` — ``AsyncMock`` returning
      ``3`` (simulating success without actually populating the
      DB; the search service's stage 2 degrades gracefully to 0.0
      scores).
    * :meth:`embed_user_message` — ``AsyncMock`` returning the
      unit vector ``[1.0, 0.0, 0.0]``. Matches the pattern in
      :mod:`tests.services.test_skill_search_service`.
    * :meth:`cosine_similarity` — sync ``MagicMock`` returning
      the dot product of the two input vectors. Pure-Python, no
      numpy. For the unit vector ``[1.0, 0.0, 0.0]`` this matches
      the cosine similarity for already-normalized inputs.

    The async methods are real :class:`AsyncMock` instances so
    tests can call ``assert_awaited_once()`` and
    ``await_args`` to verify invocation patterns.
    """
    svc = MagicMock()
    svc.update_skill_embeddings = AsyncMock(return_value=3)
    svc.embed_user_message = AsyncMock(return_value=[1.0, 0.0, 0.0])
    svc.cosine_similarity = MagicMock(
        side_effect=lambda a, b: float(sum(x * y for x, y in zip(a, b)))
    )
    return svc


def make_chat_response(content: str) -> MagicMock:
    """Build a mock chat-completion response with ``content`` as text.

    Mirrors the helper in
    :mod:`tests.services.test_skill_search_service`.

    Args:
        content: Text body for ``choices[0].message.content``.

    Returns:
        A :class:`MagicMock` shaped like an OpenAI chat-completion
        response.
    """
    choice = MagicMock()
    choice.message = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


def make_openai_client_with_json(json_payload: dict[str, Any]) -> MagicMock:
    """Build an OpenAI client mock that returns ``json_payload`` as content.

    The returned object's ``client.chat.completions.create(...)``
    returns a mock chat-completion response whose content is
    ``json.dumps(json_payload)``.

    Args:
        json_payload: The JSON-shaped dict the LLM should "return".

    Returns:
        A :class:`MagicMock` OpenAI client.
    """
    client = MagicMock()
    client.chat.completions.create = MagicMock(
        return_value=make_chat_response(json.dumps(json_payload))
    )
    return client


def patch_openai_for_skill_search(monkeypatch: pytest.MonkeyPatch,
                                  client: MagicMock) -> None:
    """Patch the OpenAI constructor used by :class:`SkillSearchService`.

    The skill search service constructs its own OpenAI client via
    ``daemon.services.skill_search_service.openai.OpenAI``. To avoid
    hitting the real API we monkeypatch that constructor to return
    our mock client.

    Args:
        monkeypatch: The pytest fixture used to set/restore
            attributes.
        client: The mock client to return from the patched
            constructor.
    """
    import daemon.services.skill_search_service as svc_mod

    factory = MagicMock(return_value=client)
    monkeypatch.setattr(svc_mod.openai, "OpenAI", factory)


# =============================================================================
# Per-test fixture builders
# =============================================================================


def build_services() -> dict[str, Any]:
    """Build a fresh engine, repositories, and the four services under test.

    This is the per-test setup helper. It does NOT use pytest fixtures
    so each test can be run independently or in arbitrary order.
    Returns a dictionary with keys:

    * ``engine``, ``repos`` — the engine + repository namespace.
    * ``store_service``, ``search_service``, ``injection_service``,
      ``metrics_service`` — the four services under test.
    * ``embedding_service`` — the mock embedding service.
    * ``config`` — the :class:`FakeConfig` instance.
    * ``instance_repo`` — the :class:`FakeInstanceRepo` instance.

    The metrics service is constructed with ``evolution_service=None``
    and ``agent_id_resolver=None`` so the CAPTURED-flow eligibility
    check is short-circuited — Flow A is about the data path, not
    about Phase 5 capture.

    Returns:
        Dict of wired-up services and dependencies.
    """
    from daemon.services.skill_injection_service import SkillInjectionService
    from daemon.services.skill_metrics_service import SkillMetricsService
    from daemon.services.skill_search_service import SkillSearchService
    from daemon.services.skill_store_service import SkillStoreService

    engine = _build_engine()
    repos = _build_repositories(engine)
    embedding_service = make_embedding_service_mock()
    config = FakeConfig()
    instance_repo = FakeInstanceRepo()

    store_service = SkillStoreService(
        skill_repo=repos.skill,
        lineage_repo=repos.lineage,
        embedding_service=embedding_service,
    )

    search_service = SkillSearchService(
        skill_repo=repos.skill,
        embedding_repo=repos.embedding,
        embedding_service=embedding_service,
        llm_config={
            "base_url": "http://test",
            "api_key": "test-key",
            "model": "gpt-4o-mini",
        },
        config=config,
    )

    injection_service = SkillInjectionService(
        search_service=search_service,
        config=config,
        ab_test_repo=repos.ab_test,
        skill_repo=repos.skill,
    )

    metrics_service = SkillMetricsService(
        usage_repo=repos.usage,
        skill_repo=repos.skill,
        trigger_repo=repos.trigger,
        ab_test_repo=repos.ab_test,
        config=config,
        instance_repo=instance_repo,
        evolution_service=None,
        agent_id_resolver=None,
    )

    return {
        "engine": engine,
        "repos": repos,
        "embedding_service": embedding_service,
        "config": config,
        "instance_repo": instance_repo,
        "store_service": store_service,
        "search_service": search_service,
        "injection_service": injection_service,
        "metrics_service": metrics_service,
    }


# =============================================================================
# Test class
# =============================================================================


class TestCrossPhaseFlowA:
    """End-to-end cross-phase integration tests for Flow A.

    Each test runs as one continuous pipeline that passes state across
    the four service boundaries (Store → Search → Injection → Metrics →
    Feedback). Tests do NOT use pytest fixtures so each test starts
    with a fresh engine and a fresh set of repositories.

    The tests assert cross-service state — e.g. after the injection
    step the metrics service reads the injected skill IDs back from
    ``FakeInstanceRepo`` to record usage — so a regression in any
    one service boundary breaks the right test.
    """

    # ---------------------------------------------------------------------
    # Happy path
    # ---------------------------------------------------------------------

    async def test_full_flow_create_search_inject_metrics_feedback(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The canonical Create → Search → Inject → Metrics → Feedback flow.

        Steps:

        1. **Create**: ``SkillStoreService.create_skill`` writes a
           row and triggers the (mocked) embedding refresh.
        2. **Search**: ``SkillSearchService.search`` runs BM25,
           embedding re-rank, and LLM selection. The LLM mock
           selects the created skill.
        3. **Inject**: ``SkillInjectionService.inject_skills``
           returns ``(text, [skill_id])``.
        4. **Metrics**: ``SkillMetricsService.record_task_completion``
           reads the injected-skill list from instance metadata,
           writes a usage record, and bumps ``total_selections`` +
           ``total_completions``.
        5. **Feedback**: ``SkillMetricsService.record_feedback``
           stamps ``feedback_applied=True`` on the latest usage
           record and bumps ``total_applied``.

        Assertions verify each cross-service transition.
        """
        services = build_services()
        repos = services["repos"]
        store_service: Any = services["store_service"]
        search_service: Any = services["search_service"]
        injection_service: Any = services["injection_service"]
        metrics_service: Any = services["metrics_service"]
        instance_repo: FakeInstanceRepo = services["instance_repo"]
        embedding_service: MagicMock = services["embedding_service"]

        # ---- Step 1: Create ---------------------------------------
        skill_name = "csv-handler"
        skill = await store_service.create_skill(
            name=skill_name,
            description="Process CSV files in Python.",
            content="Read CSV with pandas and emit cleaned output.",
            project_id="test-project",
            category="workflow",
        )
        # Skill row exists in the DB.
        fetched = repos.skill.get(skill.id)
        assert fetched is not None
        assert fetched.name == skill_name
        assert fetched.status == "active"
        assert fetched.project_id == "test-project"
        # Embedding service was invoked with the skill object.
        # The mock returns 3; we verify the call was made.
        embedding_service.update_skill_embeddings.assert_awaited_once()
        call_arg = embedding_service.update_skill_embeddings.await_args.args[0]
        assert call_arg.id == skill.id

        # ---- Step 2: Search ---------------------------------------
        # Patch OpenAI BEFORE the search runs so stage 3 uses our
        # mock LLM. The LLM picks our newly-created skill.
        client = make_openai_client_with_json(
            {"selected": [{"name": skill_name, "score": 0.95}],
             "low_match": []}
        )
        patch_openai_for_skill_search(monkeypatch, client)

        user_message = "How do I parse a csv file in Python?"
        search_result = await search_service.search(
            user_message=user_message,
            project_id="test-project",
            max_results=2,
        )

        # The skill must appear in the injected list (BM25 matched
        # AND the LLM picked it). Stage 2 scores 0.0 for all
        # candidates because no real embeddings are cached, but
        # stage 3's deterministic JSON still surfaces the skill.
        assert "injected" in search_result
        assert len(search_result["injected"]) == 1
        injected_item = search_result["injected"][0]
        assert injected_item["skill"].id == skill.id
        assert injected_item["score"] == pytest.approx(0.95)

        # ---- Step 3: Inject ---------------------------------------
        instance_id = "inst-happy-001"
        message_id = "msg-happy-001"
        injection_text, injected_skill_ids = await injection_service.inject_skills(
            user_message=user_message,
            project_id="test-project",
            instance_id=instance_id,
            message_id=message_id,
        )

        # The injection returned our skill's id (no A/B routing
        # because ab_test_group is NULL → fast path).
        assert injection_text is not None
        assert "[System Inject]" in injection_text
        assert skill_name in injection_text
        assert injected_skill_ids == [skill.id]

        # Mirror what the production caller does: write the
        # injected-skill list to instance metadata so the
        # metrics service can read it back at task completion.
        instance_repo.set_metadata(
            instance_id,
            "last_injected_skill_ids",
            list(injected_skill_ids),
        )
        # Sanity: metadata was actually written.
        _meta_inst = instance_repo.get(instance_id)
        assert _meta_inst is not None
        assert _meta_inst.instance_metadata[
            "last_injected_skill_ids"
        ] == [skill.id]

        # ---- Step 4: Metrics (task completion) --------------------
        inserted = await metrics_service.record_task_completion(
            instance_id=instance_id,
            agent_id="agent-happy",
            project_id="test-project",
            task_succeeded=True,
            iterations=3,
            duration_seconds=42,
        )
        assert inserted == 1

        # Usage record landed in the DB.
        usage_records, total = repos.usage.get_by_skill(skill.id)
        assert total == 1
        usage_rec = usage_records[0]
        assert usage_rec.skill_id == skill.id
        assert usage_rec.instance_id == instance_id
        assert usage_rec.selected is True
        assert usage_rec.task_succeeded is True
        assert usage_rec.iterations == 3
        assert usage_rec.duration_seconds == 42
        # Default applied=False until feedback arrives.
        assert usage_rec.applied is False

        # Denormalized counters bumped.
        refreshed = repos.skill.get(skill.id)
        assert refreshed.total_selections == 1
        assert refreshed.total_completions == 1
        assert refreshed.total_fallbacks == 0
        assert refreshed.consecutive_failures == 0
        # last_used_at was stamped.
        assert refreshed.last_used_at is not None

        # Metadata key cleared so the next task starts fresh.
        _cleared_inst = instance_repo.get(instance_id)
        assert _cleared_inst is not None
        assert "last_injected_skill_ids" not in (
            _cleared_inst.instance_metadata
        )

        # ---- Step 5: Feedback -------------------------------------
        fb_ok = await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=instance_id,
            agent_id="agent-happy",
            project_id="test-project",
            applied=True,
            note="worked great",
        )
        assert fb_ok is True

        # Latest usage record has feedback stamped.
        latest = repos.usage.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=instance_id
        )
        assert latest is not None
        assert latest.id == usage_rec.id
        assert latest.feedback_applied is True
        assert latest.feedback_note == "worked great"

        # ``total_applied`` counter bumped.
        refreshed = repos.skill.get(skill.id)
        assert refreshed.total_applied == 1
        # Other counters unchanged by feedback alone.
        assert refreshed.total_selections == 1
        assert refreshed.total_completions == 1

    # ---------------------------------------------------------------------
    # Global skill (project_id=None)
    # ---------------------------------------------------------------------

    async def test_full_flow_with_global_skill(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A skill with ``project_id=None`` (global) is searchable globally.

        The cross-phase flow runs with ``project_id=None`` for the
        search/inject/metrics steps to verify that global skills are
        handled correctly across all four service boundaries.

        Notes on global skills:
        * Search scoped with ``project_id=None`` returns ONLY
          ``project_id IS NULL`` rows.
        * The metrics service tolerates ``project_id=None`` (it
          coerces to ``""`` for the non-null ``skill_usage_records``
          column).
        * Feedback stamp works the same regardless of project scope.
        """
        services = build_services()
        repos = services["repos"]
        store_service: Any = services["store_service"]
        search_service: Any = services["search_service"]
        injection_service: Any = services["injection_service"]
        metrics_service: Any = services["metrics_service"]
        instance_repo: FakeInstanceRepo = services["instance_repo"]

        # Create a global skill (project_id=None).
        global_name = "global-skill-helper"
        skill = await store_service.create_skill(
            name=global_name,
            description="A globally available helper.",
            content="Globally available skill body.",
            project_id=None,
        )
        assert skill.project_id is None
        # Also create a project-scoped skill to confirm it does NOT
        # leak into the global-only search results.
        project_skill = await store_service.create_skill(
            name="scoped-skill",
            description="Project-scoped.",
            content="Not visible in global search.",
            project_id="other-project",
        )

        # Patch LLM to pick the global skill.
        client = make_openai_client_with_json(
            {"selected": [{"name": global_name, "score": 0.9}],
             "low_match": []}
        )
        patch_openai_for_skill_search(monkeypatch, client)

        # Search with project_id=None → global-only.
        result = await search_service.search(
            user_message="use the helper",
            project_id=None,
            max_results=2,
        )

        # Only the global skill surfaces. The scoped one is filtered
        # out at the repo layer (``SkillRepository.list(project_id=None)``
        # returns ``WHERE project_id IS NULL`` only).
        assert len(result["injected"]) == 1
        assert result["injected"][0]["skill"].id == skill.id
        assert result["injected"][0]["skill"].project_id is None
        # And the scoped skill id must not appear in the result.
        all_ids = {item["skill"].id for item in result["injected"]}
        assert project_skill.id not in all_ids

        # Inject (project_id=None).
        instance_id = "inst-global-001"
        injection_text, ids = await injection_service.inject_skills(
            user_message="use the helper",
            project_id=None,
            instance_id=instance_id,
            message_id="msg-global-001",
        )
        assert ids == [skill.id]
        instance_repo.set_metadata(
            instance_id, "last_injected_skill_ids", list(ids)
        )

        # Metrics with project_id=None.
        await metrics_service.record_task_completion(
            instance_id=instance_id,
            agent_id="agent-global",
            project_id=None,
            task_succeeded=True,
            iterations=2,
            duration_seconds=10,
        )

        # Usage record was inserted with ``project_id=""`` (the
        # service's defensive coercion — SkillUsageRecord.project_id
        # is NOT NULL).
        usage_records, total = repos.usage.get_by_skill(skill.id)
        assert total == 1
        assert usage_records[0].project_id == ""

        # Feedback works on the global skill too.
        fb_ok = await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=instance_id,
            agent_id="agent-global",
            project_id=None,
            applied=True,
            note="good",
        )
        assert fb_ok is True
        latest = repos.usage.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=instance_id
        )
        assert latest.feedback_applied is True
        # Counter bumped.
        assert repos.skill.get(skill.id).total_applied == 1

    # ---------------------------------------------------------------------
    # Metrics failure is non-fatal — feedback still succeeds
    # ---------------------------------------------------------------------

    async def test_full_flow_metrics_failure_does_not_block_feedback(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failure during metrics recording must NOT block feedback.

        Production wires ``record_task_completion`` behind
        ``asyncio.to_thread`` with a defensive ``try/except`` so a
        transient DB error never propagates out. We simulate the
        failure by injecting a usage record directly via the repo
        AFTER the metrics step "fails" — the feedback step must
        still locate it and stamp it.

        In other words: even if ``record_task_completion`` no-ops
        on a real DB outage, a follow-up ``record_feedback`` should
        still be able to stamp an existing usage record.
        """
        services = build_services()
        repos = services["repos"]
        store_service: Any = services["store_service"]
        search_service: Any = services["search_service"]
        injection_service: Any = services["injection_service"]
        metrics_service: Any = services["metrics_service"]
        instance_repo: FakeInstanceRepo = services["instance_repo"]

        # Create + search + inject a skill.
        skill_name = "fragile-skill"
        skill = await store_service.create_skill(
            name=skill_name,
            description="Will be recorded + fed back.",
            content="Skill body.",
            project_id="test-project",
        )
        client = make_openai_client_with_json(
            {"selected": [{"name": skill_name, "score": 0.9}],
             "low_match": []}
        )
        patch_openai_for_skill_search(monkeypatch, client)
        await search_service.search(
            user_message="fragile test", project_id="test-project"
        )
        instance_id = "inst-fragile"
        _, ids = await injection_service.inject_skills(
            user_message="fragile test",
            project_id="test-project",
            instance_id=instance_id,
            message_id="msg-fragile",
        )
        instance_repo.set_metadata(
            instance_id, "last_injected_skill_ids", list(ids)
        )

        # Simulate a metrics failure: replace the instance_repo on
        # the metrics service with one that throws on .get. The
        # service's record_task_completion must catch the exception
        # and return 0 — no crash, no record inserted.
        class BrokenInstanceRepo:
            def get(self, _instance_id: str) -> None:
                raise RuntimeError("simulated DB outage")

            def delete_metadata(self, _instance_id: str, _key: str) -> None:
                raise RuntimeError("simulated DB outage")

            def set_metadata(self, _instance_id: str, _key: str,
                             _value: Any) -> None:
                raise RuntimeError("simulated DB outage")

        metrics_service.instance_repo = BrokenInstanceRepo()

        inserted = await metrics_service.record_task_completion(
            instance_id=instance_id,
            agent_id="agent-x",
            project_id="test-project",
            task_succeeded=True,
            iterations=1,
            duration_seconds=1,
        )
        # The soft-fail returned 0, no record was inserted.
        assert inserted == 0
        usage_records, total = repos.usage.get_by_skill(skill.id)
        assert total == 0

        # Restore the working instance repo and seed a usage record
        # directly. This stands in for the "metrics recovered"
        # scenario: the row exists, feedback must succeed against it.
        metrics_service.instance_repo = instance_repo
        seeded = repos.usage.create(
            skill_id=skill.id,
            project_id="test-project",
            instance_id=instance_id,
            agent_id="agent-x",
            selected=True,
            applied=False,
            task_succeeded=True,
            iterations=1,
            duration_seconds=1,
        )
        assert seeded.id is not None

        # Feedback must succeed against the seeded record.
        fb_ok = await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=instance_id,
            agent_id="agent-x",
            project_id="test-project",
            applied=True,
            note="recovered path",
        )
        assert fb_ok is True

        latest = repos.usage.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=instance_id
        )
        assert latest is not None
        assert latest.id == seeded.id
        assert latest.feedback_applied is True
        assert latest.feedback_note == "recovered path"

    # ---------------------------------------------------------------------
    # Ordering: feedback without metrics is a no-op
    # ---------------------------------------------------------------------

    async def test_full_flow_feedback_only_after_metrics(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``record_feedback`` requires a usage record produced by metrics.

        If feedback is recorded BEFORE ``record_task_completion``
        runs (i.e. no usage row exists yet), ``record_feedback``
        must return ``False`` and stamp nothing. Once
        ``record_task_completion`` runs, a follow-up feedback call
        succeeds.

        This verifies the ordering contract: feedback always
        attaches to the latest usage record for the
        ``(skill, instance)`` pair, and a missing record yields a
        graceful ``False``.
        """
        services = build_services()
        repos = services["repos"]
        store_service: Any = services["store_service"]
        metrics_service: Any = services["metrics_service"]
        instance_repo: FakeInstanceRepo = services["instance_repo"]

        skill_name = "ordered-skill"
        skill = await store_service.create_skill(
            name=skill_name,
            description="Ordering test.",
            content="Body.",
            project_id="test-project",
        )
        instance_id = "inst-order"

        # Pre-condition: no usage record exists.
        existing = repos.usage.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=instance_id
        )
        assert existing is None

        # Feedback BEFORE metrics → returns False, no state change.
        pre_fb = await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=instance_id,
            agent_id="a",
            project_id="test-project",
            applied=True,
            note="too early",
        )
        assert pre_fb is False
        # No usage record was created as a side-effect.
        after_pre = repos.usage.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=instance_id
        )
        assert after_pre is None
        # And total_applied was not bumped.
        assert repos.skill.get(skill.id).total_applied == 0

        # Now run the metrics step — seed metadata, record.
        instance_repo.set_metadata(
            instance_id, "last_injected_skill_ids", [skill.id]
        )
        inserted = await metrics_service.record_task_completion(
            instance_id=instance_id,
            agent_id="a",
            project_id="test-project",
            task_succeeded=True,
            iterations=1,
            duration_seconds=1,
        )
        assert inserted == 1
        # The freshly inserted record exists but feedback_applied is NULL.
        rec = repos.usage.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=instance_id
        )
        assert rec is not None
        assert rec.feedback_applied is None
        assert repos.skill.get(skill.id).total_applied == 0

        # Now feedback runs — succeeds, stamps, bumps counter.
        post_fb = await metrics_service.record_feedback(
            skill_id=skill.id,
            instance_id=instance_id,
            agent_id="a",
            project_id="test-project",
            applied=True,
            note="on time",
        )
        assert post_fb is True
        rec = repos.usage.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id=instance_id
        )
        assert rec.feedback_applied is True
        assert rec.feedback_note == "on time"
        assert repos.skill.get(skill.id).total_applied == 1

    # ---------------------------------------------------------------------
    # Multiple skills in one task
    # ---------------------------------------------------------------------

    async def test_full_flow_multiple_skills_one_task(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two+ skills injected for one task are all recorded in metrics.

        Verifies that the metrics service correctly writes one usage
        record per injected skill (per-skill isolation) and bumps the
        counters for every skill that participated in the task.
        """
        services = build_services()
        repos = services["repos"]
        store_service: Any = services["store_service"]
        search_service: Any = services["search_service"]
        injection_service: Any = services["injection_service"]
        metrics_service: Any = services["metrics_service"]
        instance_repo: FakeInstanceRepo = services["instance_repo"]

        # Create three skills. Each has unique keywords so BM25 can
        # prefilter them all, and we ask the LLM to pick two.
        alpha = await store_service.create_skill(
            name="alpha-loader",
            description="Load data from sources.",
            content="Alpha body.",
            project_id="test-project",
        )
        beta = await store_service.create_skill(
            name="beta-transformer",
            description="Transform data shapes.",
            content="Beta body.",
            project_id="test-project",
        )
        gamma = await store_service.create_skill(
            name="gamma-writer",
            description="Write data to destinations.",
            content="Gamma body.",
            project_id="test-project",
        )

        # LLM picks the first two. The third surfaces as low_match.
        client = make_openai_client_with_json(
            {
                "selected": [
                    {"name": "alpha-loader", "score": 0.95},
                    {"name": "beta-transformer", "score": 0.7},
                ],
                "low_match": [
                    {"name": "gamma-writer", "score": 0.25,
                     "description": "Write data to destinations."},
                ],
            }
        )
        patch_openai_for_skill_search(monkeypatch, client)

        # Use a user message that BM25-matches all three.
        user_message = "load data, transform shapes, then write output"
        result = await search_service.search(
            user_message=user_message,
            project_id="test-project",
            max_results=2,
        )
        # Two skills injected.
        assert len(result["injected"]) == 2
        injected_ids = [item["skill"].id for item in result["injected"]]
        assert alpha.id in injected_ids
        assert beta.id in injected_ids
        assert gamma.id not in injected_ids  # Low-match, not injected.

        # Inject.
        instance_id = "inst-multi"
        injection_text, ids = await injection_service.inject_skills(
            user_message=user_message,
            project_id="test-project",
            instance_id=instance_id,
            message_id="msg-multi",
        )
        # Two skills in injection_text.
        assert injection_text is not None
        assert "[System Inject]" in injection_text
        assert "alpha-loader" in injection_text
        assert "beta-transformer" in injection_text
        assert injection_text.count("📋 **Skill:") == 2
        assert ids == [alpha.id, beta.id] or ids == [beta.id, alpha.id]
        # Persist to instance metadata for metrics.
        instance_repo.set_metadata(
            instance_id, "last_injected_skill_ids", list(ids)
        )

        # Metrics — both skills recorded.
        inserted = await metrics_service.record_task_completion(
            instance_id=instance_id,
            agent_id="agent-multi",
            project_id="test-project",
            task_succeeded=True,
            iterations=7,
            duration_seconds=120,
        )
        assert inserted == 2

        # Per-skill isolation: one record per injected skill.
        for skill in (alpha, beta):
            records, total = repos.usage.get_by_skill(skill.id)
            assert total == 1, f"expected 1 record for {skill.name}"
            rec = records[0]
            assert rec.skill_id == skill.id
            assert rec.instance_id == instance_id
            assert rec.task_succeeded is True
            assert rec.iterations == 7
            # Per-skill counter bumped.
            refreshed = repos.skill.get(skill.id)
            assert refreshed.total_selections == 1
            assert refreshed.total_completions == 1
            assert refreshed.last_used_at is not None

        # The third (low-match) skill was NOT touched.
        records_gamma, total_gamma = repos.usage.get_by_skill(gamma.id)
        assert total_gamma == 0
        assert repos.skill.get(gamma.id).total_selections == 0

        # Metadata cleared after metrics.
        _multi_inst = instance_repo.get(instance_id)
        assert _multi_inst is not None
        assert "last_injected_skill_ids" not in (
            _multi_inst.instance_metadata
        )

        # Feedback on one of the two skills — only that skill's
        # total_applied bumps; the other remains at 0.
        ok = await metrics_service.record_feedback(
            skill_id=alpha.id,
            instance_id=instance_id,
            agent_id="agent-multi",
            project_id="test-project",
            applied=True,
            note="alpha worked",
        )
        assert ok is True
        assert repos.skill.get(alpha.id).total_applied == 1
        assert repos.skill.get(beta.id).total_applied == 0

        # And the latest usage record for alpha is the one we
        # stamped (since there's only one record per skill).
        latest = repos.usage.get_latest_for_skill_instance(
            skill_id=alpha.id, instance_id=instance_id
        )
        assert latest is not None
        assert latest.feedback_applied is True
        assert latest.feedback_note == "alpha worked"

        # Stats reflect the recorded state (new shape from
        # get_stats_filtered — uses usage-record aggregation).
        # Note: `applied` here counts SkillUsageRecord.applied=True,
        # which record_feedback does NOT set (it stamps
        # feedback_applied only — see skill_feedback metrics
        # reliability issue). So `applied` / `applied_rate`
        # reflect the completion-rate signal rather than the
        # feedback-applied counter.
        stats = await metrics_service.get_skill_stats(alpha.id)
        assert stats["total"] == 1
        assert stats["completions"] == 1
        assert stats["completion_rate"] == pytest.approx(1.0)