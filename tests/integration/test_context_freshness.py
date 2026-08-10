"""Phase 5 freshness tests for ``assemble_context_messages()``.

These integration tests verify the per-turn freshness guarantee of the
Context Injection Restructure (Phase 5, Tasks 4 & 5):
``assemble_context_messages`` reads fresh state from the DB and
filesystem on EVERY call, so changes made mid-session are reflected
on the next turn.

The tests call ``assemble_context_messages`` directly (not via a full
``agent_node`` graph run) to keep the integration surface small while
still exercising real repositories and real filesystem I/O.

Patterns mirrored from the existing suite:

* ``tests/integration/test_shared_context_e2e.py`` — real
  ``SharedMetaKVRepository`` + ``SQLModelInstanceRepository``
  over an in-memory SQLite engine.
* ``tests/integration/test_skill_injection_persistence.py`` — real
  ``SkillRepository`` with a mocked ``SkillInjectionService``.
* ``tests/unit/test_context_messages.py`` — the ``assemble_context_messages``
  call shape.

No ``@pytest.mark.integration`` marker is used (matching
``tests/integration/test_context_in_graph.py``) so the tests run under
the default ``pytest`` invocation without ``-m integration``.

Run only this file:

    pytest tests/integration/test_context_freshness.py -v
"""

from __future__ import annotations

import tempfile
import uuid
from types import SimpleNamespace
from typing import Any

import pytest


# ============================================================================
# Engine + repository helpers
# ============================================================================


def _build_engine_with_shared_context_and_instance():
    """Build an in-memory SQLite engine with shared-context + instance tables.

    Mirrors ``_build_in_memory_engine`` in
    ``tests/integration/test_shared_context_e2e.py`` — the same engine is
    shared by both repositories so the SharedMetaKV KV table and
    the Instance/InstanceHierarchy tables live in the same SQLite session.

    Returns:
        SQLAlchemy :class:`Engine` bound to an in-memory SQLite database.
    """
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel, create_engine

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Importing the models registers their tables on SQLModel.metadata.
    # ``create_all`` then provisions both the shared-context metadata
    # table and the instance hierarchy tables in the same engine so
    # the two repositories can share it.
    from daemon.repositories.shared_meta_kv.models import SharedMetaKV
    from daemon.repositories.instance.models import Instance, InstanceHierarchy

    _ = (SharedMetaKV, Instance, InstanceHierarchy)
    SQLModel.metadata.create_all(engine)
    return engine


def _build_engine_with_skill_and_instance():
    """Build an in-memory SQLite engine with skill + instance tables.

    Mirrors ``_build_engine`` in
    ``tests/integration/test_skill_injection_persistence.py`` —
    registers all six skill tables plus the Instance/InstanceHierarchy
    tables so a real ``SkillRepository`` can be constructed and the
    mocked ``SkillInjectionService`` can read from a real DB to simulate
    a fresh search.

    Returns:
        SQLAlchemy :class:`Engine` bound to an in-memory SQLite database.
    """
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel, create_engine

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from daemon.repositories.instance.models import Instance, InstanceHierarchy
    from daemon.repositories.skill.models import (
        Skill,
        SkillABTest,
        SkillEmbedding,
        SkillLineage,
        SkillTrigger,
        SkillUsageRecord,
    )

    _ = (
        Instance,
        InstanceHierarchy,
        Skill,
        SkillUsageRecord,
        SkillLineage,
        SkillTrigger,
        SkillEmbedding,
        SkillABTest,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _build_manager_stub(
    engine,
    *,
    skill_service: Any = None,
    project_repo: Any = None,
):
    """Build a manager stub exposing only the slots ``assemble_context_messages`` reads.

    Mirrors the stub manager pattern from
    ``tests/unit/test_context_messages.py:TestAssembleContextMessages._make_manager``
    but wires REAL repositories on the same engine so the freshness
    contract is exercised end-to-end (real DB reads on every call).

    Args:
        engine: SQLAlchemy engine bound to the in-memory SQLite DB.
        skill_service: Optional mock for the manager's
            ``_skill_injection_service``. ``None`` (the default) means
            skill injection will short-circuit to ``(None, [])`` when
            the orchestrator's skills path runs.
        project_repo: Optional override for the manager's
            ``_project_repository``. ``None`` (the default) uses a
            ``MagicMock`` whose ``get`` returns ``None`` so the project
            payload is skipped cleanly.

    Returns:
        A ``SimpleNamespace`` exposing:

        * ``manager`` — duck-typed manager with
          ``_shared_meta_kv_repo``, ``_instance_repository``,
          ``_project_repository``, ``_skill_injection_service``.
        * ``shared_repo`` — the real
          :class:`SharedMetaKVRepository`.
        * ``instance_repo`` — the real
          :class:`SQLModelInstanceRepository`.
    """
    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    from daemon.repositories.shared_meta_kv.repository import (
        SharedMetaKVRepository,
    )

    shared_repo = SharedMetaKVRepository(engine)
    instance_repo = SQLModelInstanceRepository(engine)

    if project_repo is None:
        project_repo = MagicMock_get_returning_none()

    manager = SimpleNamespace(
        _shared_meta_kv_repo=shared_repo,
        _instance_repository=instance_repo,
        _project_repository=project_repo,
        _skill_injection_service=skill_service,
    )

    return SimpleNamespace(
        manager=manager,
        shared_repo=shared_repo,
        instance_repo=instance_repo,
    )


def MagicMock_get_returning_none():  # noqa: N802 (factory helper)
    """Build a MagicMock whose ``get`` returns ``None`` (no project)."""
    from unittest.mock import MagicMock

    repo = MagicMock()
    repo.get.return_value = None
    repo.list_critical_notes.return_value = []
    repo.get_recent_history.return_value = []
    return repo


from daemon.registry import ContextInjectionConfig


def _import_assemble_context_messages():
    """Lazy import of the orchestrator under test.

    The function lives in ``daemon.services.context_messages`` which
    closes a graph↔services circular import — pulling it at module
    import time can re-trigger the cycle during test collection.
    Importing on first use keeps the test file safe to collect.
    """
    from daemon.services.context_messages import assemble_context_messages

    return assemble_context_messages


def _flatten_context_result(t: tuple[list, list]) -> list:
    """Flatten ``(persistent, ephemeral)`` tuple into a single ordered list.

    Hybrid Context Injection (2026-07-29): the orchestrator now
    returns a tuple. Freshness tests assert the LLM-visible context
    (regardless of which half it lands in), so we flatten the tuple
    into a single ordered list. Tests that want to assert the split
    can call :func:`assemble_context_messages` directly and unpack
    the tuple.
    """
    persistent, ephemeral = t
    return list(persistent) + list(ephemeral)


def _create_root_instance(instance_repo: Any, instance_id: str) -> None:
    """Create a root instance via the real repo so tree-root resolution works.

    Mirrors the setup in
    ``tests/integration/test_shared_context_e2e.py:114-121``. Even
    though ``assemble_context_messages`` with ``parent_id=None`` does
    not touch the instance repo, creating the row keeps the test
    faithful to the production spawn path and makes the parent-less
    branch explicit.
    """
    instance_repo.create(
        instance_id=instance_id,
        agent_id="developer",
        agent_dir="/agents/developer",
        parent_id=None,
        project_id="default",
        metadata={"title": "freshness-root"},
    )


# ============================================================================
# Tests
# ============================================================================


class TestKVFreshness:
    """Task 4a: a KV written mid-session shows up on the next ``assemble_context_messages`` call.

    The orchestrator reads ``shared_meta_kv_repo.get_all_meta_kv_as_dict(context_key)``
    on every call — never caches. This test pins that contract end-to-end
    against the real ``SharedMetaKVRepository``: a value
    written between two consecutive ``assemble_context_messages`` calls
    must be reflected in the second call's output.
    """

    @pytest.mark.asyncio
    async def test_kv_written_mid_session_visible_next_call(self) -> None:
        """KV written between calls is visible on the second call.

        Steps:

        1. Set up real ``SharedMetaKVRepository`` +
           ``SQLModelInstanceRepository`` on an in-memory SQLite engine.
        2. Call ``assemble_context_messages`` — result must NOT contain
           the marker (KV is empty).
        3. Write the marker via ``shared_repo.set_many`` (same path the
           ``shared_meta_kv`` tool layer uses).
        4. Call ``assemble_context_messages`` again with identical args —
           result MUST now contain the marker.
        """
        engine = _build_engine_with_shared_context_and_instance()
        bundle = _build_manager_stub(engine)

        context_key = f"ctx-freshness-kv-{uuid.uuid4().hex[:8]}"
        _create_root_instance(bundle.instance_repo, context_key)

        # Agent meta: context injection on, skills off (isolate KV path).
        agent_meta = SimpleNamespace(
            context_injection=ContextInjectionConfig(heuristic_match_shared_md_files=True),
            skill_injection=False,
        )

        # ``project_id=None`` makes ``_fetch_project_payload`` short-circuit
        # to ``(None, [], [])`` — the project message is built from KV
        # alone. With an empty KV the whole ``build_project_context_message``
        # returns ``None``, so the first call yields an empty list.
        assemble = _import_assemble_context_messages()
        marker = "FRESHNESS_TEST_KV_MARKER_12345"

        # ── First call: KV is empty → no project message, RAG returns
        # sentinel because the context dir does not exist. Result: [].
        result1 = _flatten_context_result(await assemble(
            instance_id=context_key,
            user_query="any query",
            project_id=None,
            agent_meta=agent_meta,
            manager=bundle.manager,
            instance_repository=bundle.instance_repo,
        ))
        all_content_1 = "\n".join(str(m.content) for m in result1)
        assert marker not in all_content_1, (
            f"Marker unexpectedly present in first call: {all_content_1[:200]!r}"
        )
        assert result1 == [], (
            f"First call should be empty (no project, no KV, no RAG). "
            f"Got {[m.additional_kwargs.get('context_kind') for m in result1]}"
        )

        # ── Write the KV via the real repo. Same path as the
        # ``shared_meta_kv`` tool layer (see
        # ``daemon/tools/shared_meta_kv_tools.py:122``).
        bundle.shared_repo.set_many(context_key, {"marker_key": marker})

        # ── Second call: same instance, same everything. The orchestrator
        # must read the KV afresh and emit a ``[SYSTEM CONTEXT: Related
        # Project]`` message carrying the marker.
        result2 = _flatten_context_result(await assemble(
            instance_id=context_key,
            user_query="any query",
            project_id=None,
            agent_meta=agent_meta,
            manager=bundle.manager,
            instance_repository=bundle.instance_repo,
        ))
        all_content_2 = "\n".join(str(m.content) for m in result2)
        assert marker in all_content_2, (
            f"Marker missing from second call — KV freshness broken. "
            f"Got content (first 500 chars): {all_content_2[:500]!r}"
        )
        # And the message is the project-context message (KV lives there).
        kinds = [m.additional_kwargs.get("context_kind") for m in result2]
        assert "project" in kinds, (
            f"Project context message expected (it carries the KV section). "
            f"Got kinds: {kinds}"
        )


class TestFileFreshness:
    """Task 4b: a ``.md`` file written mid-session is matched + injected on the next call.

    The orchestrator calls ``get_shared_context`` on every turn, which
    walks ``resolve_context_dir(context_key)`` and re-reads every
    ``.md`` file in the directory via ``context_dir.glob("*.md")`` +
    ``read_text()``. A file added between calls must be picked up by
    the second call's RAG match.
    """

    @pytest.mark.asyncio
    async def test_file_written_mid_session_visible_next_call(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """A ``.md`` file written mid-session is matched + injected on the next call.

        Steps:

        1. Patch ``tempfile.gettempdir`` → ``tmp_path`` so
           ``resolve_context_dir(context_key)`` points inside the test
           sandbox (no leak into the real system tempdir).
        2. Create the context dir (initially empty).
        3. Call ``assemble_context_messages`` — no file exists, so the
           RAG helper returns the "no context" sentinel and the
           shared-context message is dropped.
        4. Write a ``.md`` file whose slug tokens overlap the query —
           ``_match_context_files`` will score it above ``MATCH_THRESHOLD``.
        5. Call ``assemble_context_messages`` again — the RAG helper now
           matches the file, ``_format_injection`` includes the file
           content as Match 1 (always included), and the marker is in
           the rendered output.

        Why the slug matters: ``_match_context_files`` tokenizes the
        filename's slug (the part before ``_YYYYMMDD_HHMMSS.md``),
        filters stop words, and computes
        ``len(intersection) / len(query_tokens)``. A filename like
        ``freshness-file-test_20260101_120000.md`` produces slug tokens
        ``{"freshness", "file", "test"}`` which overlap a query like
        ``"freshness file test query"`` — score = 3/4 = 0.75, well
        above the 0.10 threshold. With one matching file, ``_format_injection``
        always includes the full content (Match 1 path).
        """
        engine = _build_engine_with_shared_context_and_instance()
        bundle = _build_manager_stub(engine)

        context_key = f"ctx-freshness-file-{uuid.uuid4().hex[:8]}"
        _create_root_instance(bundle.instance_repo, context_key)

        # Patch gettempdir so resolve_context_dir builds
        # tmp_path/ensemble/context/{context_key}. Without this patch the
        # RAG helper would probe the real /tmp dir and (worst case) leak
        # the file there across test runs.
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        context_dir = tmp_path / "ensemble" / "context" / context_key
        context_dir.mkdir(parents=True, exist_ok=True)

        agent_meta = SimpleNamespace(
            context_injection=ContextInjectionConfig(heuristic_match_shared_md_files=True),
            skill_injection=False,
        )

        # Query tokens overlap the file slug so the file scores above the
        # MATCH_THRESHOLD and gets included as Match 1 (full content).
        # See ``daemon/services/context_injection.py:_match_context_files``.
        query = "freshness file test query please match"
        marker = "FRESHNESS_TEST_FILE_MARKER_67890"
        filename = "freshness-file-test_20260101_120000.md"

        assemble = _import_assemble_context_messages()

        # ── First call: context dir is empty → RAG returns the
        # "There is no context yet." sentinel → build_shared_context_message
        # returns None (the sentinel is in the drop-list). Result: [].
        result1 = _flatten_context_result(await assemble(
            instance_id=context_key,
            user_query=query,
            project_id=None,
            agent_meta=agent_meta,
            manager=bundle.manager,
            instance_repository=bundle.instance_repo,
        ))
        all_content_1 = "\n".join(str(m.content) for m in result1)
        assert marker not in all_content_1, (
            f"Marker unexpectedly present in first call: {all_content_1[:200]!r}"
        )

        # ── Write the .md file. The slug tokens (``freshness``, ``file``,
        # ``test``) overlap the query tokens (``freshness``, ``file``,
        # ``test``, ``query``, ``please``, ``match`` after stop-word
        # filtering) — score = 3 / 6 = 0.5, above the 0.10 threshold.
        file_path = context_dir / filename
        file_path.write_text(
            f"# Freshness Test\n\nMarker: {marker}\n",
            encoding="utf-8",
        )

        # ── Second call: file exists, _match_context_files scores it,
        # _format_injection includes the full content (Match 1 always
        # included), and build_shared_context_message wraps the body
        # under ``[SYSTEM CONTEXT: Shared Context]``.
        result2 = _flatten_context_result(await assemble(
            instance_id=context_key,
            user_query=query,
            project_id=None,
            agent_meta=agent_meta,
            manager=bundle.manager,
            instance_repository=bundle.instance_repo,
        ))
        all_content_2 = "\n".join(str(m.content) for m in result2)
        assert marker in all_content_2, (
            f"Marker missing from second call — file freshness broken. "
            f"Got content (first 500 chars): {all_content_2[:500]!r}"
        )
        # And the carrying message is the shared-context message.
        kinds = [m.additional_kwargs.get("context_kind") for m in result2]
        assert "shared_context" in kinds, (
            f"Shared-context message expected (it carries the matched file). "
            f"Got kinds: {kinds}"
        )


class TestSkillFreshness:
    """Task 5: a skill added mid-session is picked up by the next search.

    The orchestrator's skills path calls
    ``manager._skill_injection_service.inject_skills(...)`` on every
    turn (unless a ``skill_injection_result`` is supplied). The real
    ``SkillInjectionService.inject_skills`` runs a three-stage search
    (BM25 → embedding → LLM) against the live ``skill_repo`` state.
    A skill added between calls must therefore be visible on the next
    search.

    Why mock ``SkillInjectionService`` instead of wiring a real one:
    the underlying ``SkillSearchService`` requires BM25 indexing +
    an embedding service + a working LLM — heavy machinery to set up
    just to prove the orchestrator calls it fresh each turn. The KEY
    contract being tested is that ``assemble_context_messages``
    invokes the search afresh on every turn (and the new skill is
    visible to it). A fake ``inject_skills`` that reads the real
    ``skill_repo`` per call proves that contract: the second call's
    search sees the newly-added skill, mirroring what the real
    search would do.
    """

    @pytest.mark.asyncio
    async def test_skill_added_mid_session_visible_next_call(self) -> None:
        """Skill added between calls is picked up by the next search.

        Steps:

        1. Build real ``SkillRepository`` against an in-memory SQLite
           engine.
        2. Build a fake ``SkillInjectionService`` whose
           ``inject_skills`` reads ``skill_repo.list(project_id=...)``
           each call and returns the matching skill's content.
        3. Call ``assemble_context_messages`` — no matching skill exists
           → fake returns ``(None, [])`` → no skills message.
        4. Add a skill carrying the marker via ``skill_repo.create``.
        5. Call ``assemble_context_messages`` again — fake re-reads the
           real DB, finds the skill, returns a body that includes the
           marker → the skills message now carries the marker.

        The ``call_count`` assertion pins the freshness guarantee
        explicitly: the search ran exactly twice, once per
        ``assemble_context_messages`` call. If the orchestrator cached
        the result, ``call_count`` would be 1 and the freshness
        contract would be broken.
        """
        from daemon.repositories.skill.repository import SkillRepository

        engine = _build_engine_with_skill_and_instance()
        skill_repo = SkillRepository(engine)

        project_id = "freshness-project"
        marker = "FRESHNESS_TEST_SKILL_MARKER_99999"
        call_log: list[int] = []

        async def fake_inject_skills(
            user_message: str,
            project_id: str | None,
            instance_id: str,
            message_id: str,
        ) -> tuple[str | None, list[str]]:
            """Simulate a fresh search against the live ``skill_repo``.

            Each call reads ``skill_repo.list(project_id=...)`` afresh,
            counts the rows it saw (recorded in ``call_log`` for the
            freshness assertion), and returns the first skill whose
            content carries the marker. If no skill matches, returns
            ``(None, [])`` so the orchestrator drops the skills message.
            """
            items, total = skill_repo.list(
                project_id=project_id,
                active_only=True,
                limit=50,
            )
            call_log.append(total)
            matching = [s for s in items if marker in (s.content or "")]
            if not matching:
                return (None, [])
            injected = matching[0]
            text = (
                "[System Inject] Relevant skills loaded:\n\n"
                f"# {injected.name}\n{marker}\n"
            )
            return (text, [str(injected.id)])

        skill_service = SimpleNamespace(inject_skills=fake_inject_skills)

        bundle = _build_manager_stub(engine, skill_service=skill_service)

        context_key = f"ctx-freshness-skill-{uuid.uuid4().hex[:8]}"
        _create_root_instance(bundle.instance_repo, context_key)

        # Agent meta: context injection off (isolate the skills path),
        # skill injection on. With context_injection=False the orchestrator
        # never touches the project repo or the KV repo or the RAG
        # helper — only the skills path runs.
        agent_meta = SimpleNamespace(
            context_injection=False,
            skill_injection=True,
        )

        assemble = _import_assemble_context_messages()

        # ── First call: no skill exists → fake search returns
        # (None, []) → build_skills_message returns None → result is [].
        result1 = _flatten_context_result(await assemble(
            instance_id=context_key,
            user_query="any query",
            project_id=project_id,
            agent_meta=agent_meta,
            manager=bundle.manager,
            instance_repository=bundle.instance_repo,
        ))
        all_content_1 = "\n".join(str(m.content) for m in result1)
        assert marker not in all_content_1, (
            f"Marker unexpectedly present in first call: {all_content_1[:200]!r}"
        )
        assert result1 == [], (
            f"First call should be empty (no skill match). "
            f"Got kinds: {[m.additional_kwargs.get('context_kind') for m in result1]}"
        )

        # ── Add the marker skill via the real repo. Same path the
        # ``skill`` tool layer uses (see
        # ``daemon/services/skill_store_service.py``).
        skill_repo.create(
            name="freshness-marker-skill",
            description="freshness test skill",
            content=f"This skill carries {marker} for the freshness test.",
            project_id=project_id,
        )

        # ── Second call: fake re-reads the DB, finds the skill,
        # returns a body that includes the marker. The orchestrator
        # wraps it under ``[SYSTEM CONTEXT: Skills]``.
        result2 = _flatten_context_result(await assemble(
            instance_id=context_key,
            user_query="any query",
            project_id=project_id,
            agent_meta=agent_meta,
            manager=bundle.manager,
            instance_repository=bundle.instance_repo,
        ))
        all_content_2 = "\n".join(str(m.content) for m in result2)
        assert marker in all_content_2, (
            f"Marker missing from second call — skill freshness broken. "
            f"Got content (first 500 chars): {all_content_2[:500]!r}"
        )
        kinds = [m.additional_kwargs.get("context_kind") for m in result2]
        assert "skills" in kinds, (
            f"Skills message expected (it carries the matched skill body). "
            f"Got kinds: {kinds}"
        )

        # ── Freshness pin: the search ran exactly twice (once per
        # ``assemble_context_messages`` call) and saw strictly more
        # skills on the second call — proving both that the search is
        # called fresh AND that the new skill is visible to it.
        assert len(call_log) == 2, (
            f"Expected exactly 2 skill searches (one per assemble call), "
            f"got {len(call_log)} — freshness contract broken"
        )
        assert call_log[0] == 0, (
            f"First search should see 0 skills (none created yet). "
            f"Got {call_log[0]}"
        )
        assert call_log[1] > call_log[0], (
            f"Second search must see strictly more skills than the first "
            f"(proves fresh DB read). Got {call_log[0]} → {call_log[1]}"
        )