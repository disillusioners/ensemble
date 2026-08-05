"""Unit tests for Project Blueprint injection in ``assemble_context_messages``.

Phase 2 — Injection Integration. These tests cover the persistent-block
blueprint integration seam added to
:func:`daemon.services.context_messages.assemble_context_messages`:

* On the first user turn (``project_already_injected=False``) the
  manager's ``_blueprint_matcher.match()`` is awaited and any
  non-empty result becomes a tagged ``[SYSTEM CONTEXT: Project
  Blueprint]`` ``HumanMessage`` (``context_kind="blueprint"``).
* ``blueprint_inactive=True`` short-circuits the match — no matcher
  call, no message.
* An empty match list is also a no-op (no message appended).
* ``project_already_injected=True`` skips the match entirely (the
  matcher is never called).
* A matcher exception degrades gracefully (warning logged, no
  message, orchestrator does not raise).

Mocking pattern follows
:class:`tests.unit.test_context_messages.TestAssembleContextMessages._make_manager`
— the manager, repos, and services are ``MagicMock``; async
matcher call sites use ``AsyncMock``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from daemon.constants import BLUEPRINT_ACTIVE_METADATA_KEY
from daemon.registry import ContextInjectionConfig
from daemon.services.blueprint_matcher import MatchedBlueprint
from daemon.services.context_messages import (
    CONTEXT_KIND_BLUEPRINT,
    _build_blueprint_block_text,
    assemble_context_messages,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_matched_blueprint(
    *,
    bp_id: str = "bp-1",
    name: str = "Core Architecture",
    kind: str = "core",
    version: int = 1,
    content: str = "Always-loaded core blueprint content.",
    file_refs: list[str] | None = None,
    score: float = 1.0,
) -> MatchedBlueprint:
    """Build a :class:`MatchedBlueprint` for test injection."""
    return MatchedBlueprint(
        id=bp_id,
        name=name,
        kind=kind,
        version=version,
        content=content,
        file_refs=list(file_refs or []),
        score=score,
    )


def _make_manager(
    *,
    matcher: Any = None,
    project: Any = None,
) -> tuple[Any, Any, Any]:
    """Build ``(manager, instance_repository, agent_meta)`` mocks.

    Mirrors the shape of
    :class:`tests.unit.test_context_messages.TestAssembleContextMessages._make_manager`
    but adds the ``_blueprint_matcher`` hook the Phase 2 block reads.
    Every async call site on the manager uses ``AsyncMock`` so the
    orchestrator's ``await`` calls succeed under ``asyncio.run``.
    """
    agent_meta = MagicMock()
    agent_meta.id = "developer"
    agent_meta.context_injection = ContextInjectionConfig(
        heuristic_match_shared_md_files=False,
    )
    agent_meta.skill_injection = False  # keep the skills block quiet
    agent_meta.blueprint_inactive = False

    project_repo = MagicMock()
    project_repo.get.return_value = project
    project_repo.list_critical_notes.return_value = []
    project_repo.get_recent_history.return_value = []
    # Opt the test project in to the blueprint system by default
    # (Phase 7 opt-in gate — absent = inactive). Tests that need to
    # exercise the inactive path override ``get_metadata`` per-case.
    project_repo.get_metadata = MagicMock(
        side_effect=lambda pid, key: (
            True if key == BLUEPRINT_ACTIVE_METADATA_KEY else None
        ),
    )

    kv_repo = MagicMock()
    kv_repo.get_all_as_dict.return_value = {}

    skill_service = MagicMock()
    skill_service.inject_skills = AsyncMock(return_value=(None, []))

    manager = MagicMock()
    manager._project_repository = project_repo
    manager._shared_context_metadata_repo = kv_repo
    manager._skill_injection_service = skill_service
    # Default: no matcher. Per-test setup swaps this in.
    manager._blueprint_matcher = matcher

    instance_repository = MagicMock()
    instance_repository.get_tree_root_id.return_value = "root-id"

    return manager, instance_repository, agent_meta


def _blueprint_messages(persistent: list[HumanMessage]) -> list[HumanMessage]:
    """Filter ``persistent_msgs`` to the blueprint slot only."""
    return [
        m
        for m in persistent
        if m.additional_kwargs.get("context_kind") == CONTEXT_KIND_BLUEPRINT
    ]


# ─── Builder unit test ───────────────────────────────────────────────────────


class TestBuildBlueprintBlockText:
    """Pure-text builder for the blueprint block."""

    def test_renders_header_and_sections(self) -> None:
        """Header lists every match; body emits one section per blueprint."""
        bps = [
            _make_matched_blueprint(
                name="Core Architecture",
                kind="core",
                content="core body",
                score=1.0,
            ),
            _make_matched_blueprint(
                bp_id="bp-2",
                name="API Patterns",
                kind="area",
                content="api body",
                file_refs=["docs/api.md", "daemon/api.py"],
                score=0.62,
            ),
        ]
        text = _build_blueprint_block_text(bps)

        # Header enumerates both
        assert "Matched Project Blueprints:" in text
        assert "✓ Core Architecture (score: 1.00, source: core)" in text
        assert "✓ API Patterns (score: 0.62, source: matched)" in text
        # Section dividers + bodies
        assert "--- Core Architecture ---" in text
        assert "core body" in text
        assert "--- API Patterns ---" in text
        assert "api body" in text
        # file_refs render as a "For more detail read" line
        assert "For more detail read: docs/api.md, daemon/api.py" in text

    def test_omits_file_refs_line_when_empty(self) -> None:
        """No file_refs → no trailing 'For more detail read' line."""
        bps = [_make_matched_blueprint(file_refs=[])]
        text = _build_blueprint_block_text(bps)
        assert "For more detail read" not in text


# ─── Orchestrator integration tests ──────────────────────────────────────────


class TestAssembleContextMessagesBlueprint:
    """End-to-end orchestrator tests for the blueprint block."""

    @staticmethod
    def _run(coro: Any) -> Any:
        """Drive an awaitable via a fresh event loop (no pytest-asyncio dep)."""
        return asyncio.run(coro)

    def test_blueprint_injected_on_first_turn(self) -> None:
        """Matcher returns 1+ blueprints → a blueprint message lands in persistent."""
        bp = _make_matched_blueprint(name="Core Architecture")
        matcher = MagicMock()
        matcher.match = AsyncMock(return_value=[bp])

        manager, instance_repo, agent_meta = _make_manager(matcher=matcher)

        persistent, _ = self._run(
            assemble_context_messages(
                instance_id="inst-1",
                user_query="how does the orchestrator work?",
                project_id="proj-1",
                agent_meta=agent_meta,
                manager=manager,
                instance_repository=instance_repo,
            )
        )

        bp_msgs = _blueprint_messages(persistent)
        assert len(bp_msgs) == 1
        msg = bp_msgs[0]
        assert msg.additional_kwargs["injected_message"] is True
        assert msg.additional_kwargs["context_kind"] == CONTEXT_KIND_BLUEPRINT
        assert "Core Architecture" in msg.content
        assert msg.content.startswith("[SYSTEM CONTEXT: Project Blueprint]\n\n")
        # Matcher was awaited once with the user_query.
        matcher.match.assert_awaited_once()

    def test_blueprint_inactive_skips_match(self) -> None:
        """``blueprint_inactive=True`` → no matcher call, no message."""
        bp = _make_matched_blueprint()
        matcher = MagicMock()
        matcher.match = AsyncMock(return_value=[bp])

        manager, instance_repo, agent_meta = _make_manager(matcher=matcher)
        agent_meta.blueprint_inactive = True

        persistent, _ = self._run(
            assemble_context_messages(
                instance_id="inst-1",
                user_query="hi",
                project_id="proj-1",
                agent_meta=agent_meta,
                manager=manager,
                instance_repository=instance_repo,
            )
        )

        assert _blueprint_messages(persistent) == []
        matcher.match.assert_not_called()

    def test_empty_match_appends_no_message(self) -> None:
        """Matcher returns [] → orchestrator stays quiet on the blueprint slot."""
        matcher = MagicMock()
        matcher.match = AsyncMock(return_value=[])

        manager, instance_repo, agent_meta = _make_manager(matcher=matcher)

        persistent, _ = self._run(
            assemble_context_messages(
                instance_id="inst-1",
                user_query="hi",
                project_id="proj-1",
                agent_meta=agent_meta,
                manager=manager,
                instance_repository=instance_repo,
            )
        )

        assert _blueprint_messages(persistent) == []
        matcher.match.assert_awaited_once()

    def test_project_already_injected_skips_matcher(self) -> None:
        """Turn 2+ path → the once-per-instance block is skipped, matcher not called."""
        bp = _make_matched_blueprint()
        matcher = MagicMock()
        matcher.match = AsyncMock(return_value=[bp])

        manager, instance_repo, agent_meta = _make_manager(matcher=matcher)

        persistent, _ = self._run(
            assemble_context_messages(
                instance_id="inst-1",
                user_query="hi",
                project_id="proj-1",
                agent_meta=agent_meta,
                manager=manager,
                instance_repository=instance_repo,
                project_already_injected=True,
            )
        )

        assert _blueprint_messages(persistent) == []
        matcher.match.assert_not_called()

    def test_matcher_exception_degrades_gracefully(self) -> None:
        """Matcher raises → warning logged, no message, orchestrator does not raise."""
        matcher = MagicMock()
        matcher.match = AsyncMock(side_effect=RuntimeError("DB hiccup"))

        manager, instance_repo, agent_meta = _make_manager(matcher=matcher)

        with patch(
            "daemon.services.context_messages.logger"
        ) as logger_mock:
            persistent, _ = self._run(
                assemble_context_messages(
                    instance_id="inst-1",
                    user_query="hi",
                    project_id="proj-1",
                    agent_meta=agent_meta,
                    manager=manager,
                    instance_repository=instance_repo,
                )
            )

        # No blueprint message lands.
        assert _blueprint_messages(persistent) == []
        # A warning was logged.
        assert any(
            call.args
            and "Blueprint matching failed" in str(call.args[0])
            for call in logger_mock.warning.call_args_list
        )


# ─── C10 reviewer gap tests ───────────────────────────────────────────────────


class TestC10BlueprintInjectionGaps:
    """Close the C10 coverage gaps around blueprint persistence and matching."""

    def test_matcher_none_skips_blueprint_injection(self) -> None:
        """A manager without a matcher must skip the blueprint slot quietly."""
        manager, instance_repo, agent_meta = _make_manager(matcher=None)

        persistent, ephemeral = asyncio.run(
            assemble_context_messages(
                instance_id="inst-c10-none",
                user_query="how does this work?",
                project_id="proj-c10",
                agent_meta=agent_meta,
                manager=manager,
                instance_repository=instance_repo,
            )
        )

        assert _blueprint_messages(persistent) == []
        assert ephemeral == []

    def test_blueprint_context_kind_is_recognized_for_checkpoint_persistence(
        self,
    ) -> None:
        """Checkpoint context detection must recognize the blueprint kind."""
        from daemon.persistence import _messages_have_context_block

        message = HumanMessage(
            content="[SYSTEM CONTEXT: Project Blueprint]",
            additional_kwargs={
                "injected_message": True,
                "context_kind": CONTEXT_KIND_BLUEPRINT,
            },
        )

        # ``_CONTEXT_KINDS`` is function-local in the current persistence
        # implementation, so exercise the canonical membership check through
        # its helper rather than importing a symbol that is not module-level.
        assert _messages_have_context_block([message]) is True

    def test_missing_blueprint_inactive_defaults_to_active_injection(self) -> None:
        """A plain agent metadata object missing the opt-out flag stays active."""
        from types import SimpleNamespace

        matcher = MagicMock()
        matcher.match = AsyncMock(return_value=[_make_matched_blueprint()])
        manager, instance_repo, _ = _make_manager(matcher=matcher)
        agent_meta = SimpleNamespace(
            id="developer",
            context_injection=ContextInjectionConfig(
                heuristic_match_shared_md_files=False,
            ),
            skill_injection=False,
        )

        assert not hasattr(agent_meta, "blueprint_inactive")
        persistent, _ = asyncio.run(
            assemble_context_messages(
                instance_id="inst-c10-default",
                user_query="what conventions apply?",
                project_id="proj-c10",
                agent_meta=agent_meta,
                manager=manager,
                instance_repository=instance_repo,
            )
        )

        bp_msgs = _blueprint_messages(persistent)
        assert len(bp_msgs) == 1
        matcher.match.assert_awaited_once()

    def test_real_matcher_always_returns_core_in_slot_one(self) -> None:
        """A real repository-backed matcher always reserves the core slot."""
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel

        from daemon.config import BlueprintConfig
        from daemon.repositories.blueprint.models import (  # noqa: F401
            Blueprint,
            BlueprintRevision,
            BlueprintTrigger,
        )
        from daemon.repositories.blueprint.repository import BlueprintRepository
        from daemon.services.blueprint_matcher import BlueprintMatcher

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)
        repository = BlueprintRepository(engine)
        core = repository.create(
            project_id="proj-c10-real",
            slug="core",
            name="Core Architecture",
            kind="core",
            content="Core project conventions.",
        )

        class FixedEmbeddingService:
            async def embed_text(self, _text: str) -> list[float]:
                return [1.0, 0.0]

            @staticmethod
            def cosine_similarity(a: list[float], b: list[float]) -> float:
                return sum(x * y for x, y in zip(a, b))

        matcher = BlueprintMatcher(
            repository=repository,
            embedding_service=FixedEmbeddingService(),
            config=BlueprintConfig(),
        )
        matched = asyncio.run(
            matcher.match(
                project_id="proj-c10-real",
                query="a query unrelated to the core wording",
            )
        )

        assert matched
        assert matched[0].id == core.id
        assert matched[0].kind == "core"
        assert matched[0].score >= 1.0


# ─── Phase 7: per-project opt-in gate ────────────────────────────────────


class TestPerProjectOptInGate:
    """Two-tier gate (Phase 7): a project must opt in to the
    blueprint system before its context-injection fires.

    * ``blueprint_active`` metadata absent → matcher is never called,
      no blueprint message lands.
    * ``blueprint_active=true`` → matcher is called, message lands
      as before.
    * Metadata lookup failure is treated as INACTIVE (the safer
      default; the orchestrator must not abort on a transient DB error).
    """

    def test_injection_skipped_when_project_not_active(self) -> None:
        """Default: absent metadata → no blueprint injection."""
        matcher = MagicMock()
        matcher.match = AsyncMock(return_value=[_make_matched_blueprint()])

        manager, instance_repo, agent_meta = _make_manager(matcher=matcher)
        # Project has NOT opted in (absent metadata).
        manager._project_repository.get_metadata = MagicMock(return_value=None)

        persistent, _ = asyncio.run(
            assemble_context_messages(
                instance_id="inst-phase7-inactive",
                user_query="what conventions apply?",
                project_id="proj-phase7-inactive",
                agent_meta=agent_meta,
                manager=manager,
                instance_repository=instance_repo,
            )
        )

        # No blueprint message — gate failed.
        assert _blueprint_messages(persistent) == []
        # Matcher was never called (cheaper than the gate trip).
        matcher.match.assert_not_called()
        # The gate queried the right key.
        meta_calls = manager._project_repository.get_metadata.call_args_list
        assert any(
            call.args[0] == "proj-phase7-inactive"
            and call.args[1] == BLUEPRINT_ACTIVE_METADATA_KEY
            for call in meta_calls
        ), meta_calls

    def test_injection_works_when_project_active(self) -> None:
        """Regression: opted-in project keeps the pre-Phase-7 behaviour."""
        matcher = MagicMock()
        matcher.match = AsyncMock(return_value=[_make_matched_blueprint()])

        manager, instance_repo, agent_meta = _make_manager(matcher=matcher)
        # Project opted in (the fixture already opts in by default; assert it).
        manager._project_repository.get_metadata = MagicMock(
            side_effect=lambda pid, key: (
                True if key == BLUEPRINT_ACTIVE_METADATA_KEY else None
            ),
        )

        persistent, _ = asyncio.run(
            assemble_context_messages(
                instance_id="inst-phase7-active",
                user_query="what conventions apply?",
                project_id="proj-phase7-active",
                agent_meta=agent_meta,
                manager=manager,
                instance_repository=instance_repo,
            )
        )

        # Blueprint message lands.
        assert len(_blueprint_messages(persistent)) == 1
        matcher.match.assert_awaited_once()

    def test_injection_skipped_on_metadata_lookup_failure(self) -> None:
        """A transient metadata failure must NOT crash the orchestrator
        — treat the project as inactive and silently skip injection.
        """
        matcher = MagicMock()
        matcher.match = AsyncMock(return_value=[_make_matched_blueprint()])

        manager, instance_repo, agent_meta = _make_manager(matcher=matcher)
        # Metadata lookup fails (e.g. transient DB error).
        manager._project_repository.get_metadata = MagicMock(
            side_effect=RuntimeError("DB hiccup"),
        )

        persistent, _ = asyncio.run(
            assemble_context_messages(
                instance_id="inst-phase7-err",
                user_query="hi",
                project_id="proj-phase7-err",
                agent_meta=agent_meta,
                manager=manager,
                instance_repository=instance_repo,
            )
        )

        # No blueprint message — gate treated the failure as inactive.
        assert _blueprint_messages(persistent) == []
        matcher.match.assert_not_called()
