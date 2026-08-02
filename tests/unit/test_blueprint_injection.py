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
