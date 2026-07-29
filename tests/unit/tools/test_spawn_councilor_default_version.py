"""Tests for default-version resolution in the ``spawn_councilor`` tool (W3 fix).

W3: ``spawn_councilor`` previously exposed ``version_tag`` to the LLM as an
optional parameter, creating an asymmetry vs. ``spawn_instance``: a v2
governor could accidentally spawn a v1 councilor with a wider tool set.
The fix removes the parameter and resolves the per-project default
internally via ``_resolve_default_version_tag`` — matching the frontend
UX (the user never picks the councilor's version tag) and matching
``spawn_instance``.

Covers:
1. ``SpawnCouncilorInput`` Pydantic schema does NOT have a ``version_tag``
   field (contract-level invariant).
2. The ``spawn_councilor`` tool body resolves the default via
   ``_resolve_default_version_tag`` (spy assertion).
3. The resolved tag is forwarded to ``manager.spawn_instance(..., version_tag=...)``.
4. No-default path: ``manager.spawn_instance`` receives ``version_tag=None``
   (lifecycle handles the base fallback).

Mirrors the assertion pattern from
``tests/unit/tools/test_spawn_instance_default_version.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from daemon import constants
from daemon.repositories import SQLModelProjectRepository
from daemon.repositories.project.models import (
    Project,
    ProjectMetadataRecord,
    ProjectShortnameLink,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures (mirror test_spawn_instance_default_version.py)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def system_default_project_id():
    """Pin ``SYSTEM_DEFAULT_PROJECT_ID`` for the duration of a test."""
    original = constants.SYSTEM_DEFAULT_PROJECT_ID
    pid = "00000000-0000-0000-0000-000000000001"
    constants.SYSTEM_DEFAULT_PROJECT_ID = pid
    try:
        yield pid
    finally:
        constants.SYSTEM_DEFAULT_PROJECT_ID = original


@pytest.fixture
def engine():
    """In-memory SQLite engine with project + project_metadata tables."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _ = (Project, ProjectMetadataRecord, ProjectShortnameLink)
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine):
    """``SQLModelProjectRepository`` bound to the test engine."""
    return SQLModelProjectRepository(engine)


def _seed_default_versions(
    repo: SQLModelProjectRepository, project_id: str, mapping: dict
) -> None:
    """Seed the ``default_agent_versions`` metadata record as JSON text."""
    repo.set_metadata(
        project_id,
        constants.DEFAULT_AGENT_VERSIONS_METADATA_KEY,
        json.dumps(mapping),
    )


def _create_project_row(engine, project_id: str) -> None:
    """Insert a minimal ``projects`` row so the FK on metadata is satisfied."""
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            Project(
                project_id=project_id,
                name=f"project-{project_id[:8]}",
                project_type="general",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers for building the spawn_councilor tool (mirrors test_council_tools.py)
# ─────────────────────────────────────────────────────────────────────────────


def _patch_heavy_helpers() -> list:
    """Disable heavy ``create_instance_tools`` factory helpers."""
    return [
        patch("daemon.tools.instance.is_rag_enabled", return_value=False),
        patch("daemon.tools.instance.create_rag_tools", return_value=[]),
        patch("daemon.tools.instance.create_knowledge_tools", return_value=[]),
        patch("daemon.tools.instance.create_inner_soul_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_access_memory_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_project_tools", return_value=[]),
        patch("daemon.tools.instance.create_job_tools_if_available", return_value=[]),
        patch("daemon.tools.instance.create_help_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_critical_notes_tools", return_value=[]),
        patch("daemon.tools.instance.create_project_history_tools", return_value=[]),
        patch("daemon.tools.instance.create_opencode_tools", return_value=[]),
        patch("daemon.tools.instance.create_db_tools", return_value=[]),
        patch("daemon.tools.instance.create_infra_tools", return_value=[]),
        patch("daemon.tools.instance.create_context_tools", return_value=[]),
        patch("daemon.tools.instance.create_chart_tools", return_value=[]),
        patch("daemon.tools.instance._load_mcp_tools", return_value=[]),
        patch("daemon.tools.instance.scan_tools_for_full_docs"),
        patch("daemon.tools.instance._apply_tool_filter", side_effect=lambda tools, *a, **kw: tools),
    ]


def _make_council_manager(
    *,
    allowed_models: list[str] | None = None,
    spawn_result: tuple[str, str | None] = ("new-councilor-instance-id", "gpt-4o"),
    project_repository: SQLModelProjectRepository | None = None,
) -> MagicMock:
    """Build a mock manager wired for ``spawn_councilor``.

    Returns a manager with:
      * ``config.llm.allowed_models`` — list of allowed models.
      * ``_lifecycle_service._resolve_model_override`` — case-insensitive match.
      * ``_project_repository`` — the real (or mock) project repo.
      * ``spawn_instance`` — sync MagicMock returning
        ``(instance_id, validated_model_override)``.
      * ``enqueue_message`` — AsyncMock (used by convene_council; harmless here).
    """
    if allowed_models is None:
        allowed_models = ["gpt-4o", "claude-3-5-sonnet", "gemini-1.5-pro"]

    manager = MagicMock()
    manager.config = MagicMock()
    manager.config.llm = MagicMock()
    manager.config.llm.allowed_models = list(allowed_models)

    def _resolve(model: str | None) -> str | None:
        if not model or not str(model).strip():
            return None
        candidate = str(model).strip()
        if not allowed_models:
            return candidate
        lowered = candidate.lower()
        for entry in allowed_models:
            if entry and entry.lower() == lowered:
                return candidate
        return None

    manager._lifecycle_service = MagicMock()
    manager._lifecycle_service._resolve_model_override = MagicMock(side_effect=_resolve)
    manager.spawn_instance = MagicMock(return_value=spawn_result)
    manager.enqueue_message = AsyncMock()
    manager._project_repository = (
        project_repository if project_repository is not None else MagicMock()
    )
    manager._project_repository.engine = MagicMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get.return_value = None
    return manager


def _get_councilor_tool(manager: MagicMock) -> MagicMock:
    """Build instance tools via the patched factory and return the
    ``spawn_councilor`` StructuredTool."""
    from daemon.tools.instance import create_instance_tools

    patches = _patch_heavy_helpers()
    for p in patches:
        p.start()
    try:
        tools = create_instance_tools(
            manager, "parent-instance-id", agent_id="governor"
        )
    finally:
        for p in reversed(patches):
            p.stop()

    councilor_tool = None
    for t in tools:
        if getattr(t, "name", None) == "spawn_councilor":
            councilor_tool = t
            break
    if councilor_tool is None:
        raise RuntimeError(
            "spawn_councilor tool not found; got: "
            f"{[getattr(t, 'name', None) for t in tools]}"
        )
    return councilor_tool


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Pydantic schema no longer exposes version_tag
# ─────────────────────────────────────────────────────────────────────────────


class TestSpawnCouncilorInputSchema:
    """Contract-level invariant: ``version_tag`` is gone from the input model."""

    def test_input_model_has_no_version_tag_field(self):
        """W3 contract assertion: ``SpawnCouncilorInput`` does NOT define
        a ``version_tag`` field. Frontend / LLM can no longer set it."""
        from daemon.governor.contracts import SpawnCouncilorInput

        assert "version_tag" not in SpawnCouncilorInput.model_fields, (
            "W3 regression: SpawnCouncilorInput must not expose version_tag; "
            f"current model_fields = {list(SpawnCouncilorInput.model_fields)}"
        )

    def test_input_model_construction_without_version_tag_succeeds(self):
        """Sanity: the model constructs with only the required fields —
        confirms the contract still works after removing version_tag."""
        from daemon.governor.contracts import SpawnCouncilorInput

        inp = SpawnCouncilorInput(
            councilor_agent_id="developer",
            model="gpt-4o",
            instance_name=None,
            initial_message="please help",
        )
        assert inp.councilor_agent_id == "developer"
        assert inp.model == "gpt-4o"
        assert inp.initial_message == "please help"
        # And no `version_tag` attribute is exposed (not even default None).
        assert not hasattr(inp, "version_tag"), (
            "SpawnCouncilorInput instance must not carry version_tag; "
            "removal of the field should remove the attribute too."
        )

    def test_tool_args_schema_has_no_version_tag(self):
        """The StructuredTool's args_schema (Pydantic model) does not list
        ``version_tag`` as a tool parameter."""
        from daemon.governor.contracts import SpawnCouncilorInput

        manager = _make_council_manager()
        councilor_tool = _get_councilor_tool(manager)

        schema_fields = list(getattr(councilor_tool.args_schema, "model_fields", {}))
        assert "version_tag" not in schema_fields, (
            f"spawn_councilor's args_schema must not expose version_tag; "
            f"got fields = {schema_fields}"
        )

        # And the contract model itself is the one used (sanity).
        assert councilor_tool.args_schema is SpawnCouncilorInput


# ─────────────────────────────────────────────────────────────────────────────
# Test 2-4: Tool body resolves the default + forwards to manager
# ─────────────────────────────────────────────────────────────────────────────


class TestSpawnCouncilorResolvesDefaultVersion:
    """End-to-end: tool body calls ``_resolve_default_version_tag`` and
    forwards the resolved value to ``manager.spawn_instance``."""

    @pytest.mark.asyncio
    async def test_default_configured_forwards_resolved_tag(
        self, repo, engine, system_default_project_id
    ):
        """Default ``{"developer": "v2"}`` → manager.spawn_instance receives
        ``version_tag="v2"`` and the helper was called."""
        _create_project_row(engine, system_default_project_id)
        _seed_default_versions(repo, system_default_project_id, {"developer": "v2"})

        manager = _make_council_manager(project_repository=repo)
        councilor_tool = _get_councilor_tool(manager)

        # Spy on the helper to confirm it was invoked.
        with patch(
            "daemon.tools.instance._resolve_default_version_tag",
            new=AsyncMock(return_value="v2"),
        ) as spy_resolve:
            with patch(
                "daemon.tools.instance._check_team_membership",
                return_value=None,
            ):
                # Patch get_registry narrowly around the coroutine invocation
                # so registry.get_version() (used by the helper) returns a
                # truthy metadata → "v2" passes the W2 stale-tag guard.
                fake_registry = MagicMock()
                fake_registry.get_version.return_value = MagicMock()
                fake_registry.resolve_to_id.return_value = "developer"
                with patch("daemon.registry.get_registry", return_value=fake_registry):
                    result = await councilor_tool.coroutine(
                        councilor_agent_id="developer",
                        model="gpt-4o",
                        initial_message="please help",
                    )

        # Assert helper was called exactly once with the councilor agent id.
        assert spy_resolve.await_count == 1, (
            f"_resolve_default_version_tag must be called exactly once; "
            f"got await_count={spy_resolve.await_count}"
        )
        call_args = spy_resolve.await_args
        # Positional / keyword tolerant — we only care about agent_id.
        forwarded_agent_id: str | None = None
        if call_args is not None:
            if call_args.kwargs and "agent_id" in call_args.kwargs:
                forwarded_agent_id = call_args.kwargs["agent_id"]
            elif len(call_args.args) >= 2:
                # (project_repo, agent_id, registry)
                forwarded_agent_id = call_args.args[1]
        assert forwarded_agent_id == "developer", (
            f"helper should resolve for 'developer'; got {forwarded_agent_id!r}"
        )

        # Assert manager.spawn_instance was called with version_tag="v2".
        assert manager.spawn_instance.called, (
            f"manager.spawn_instance was not invoked (result={result!r})"
        )
        call_kwargs = manager.spawn_instance.call_args.kwargs
        assert call_kwargs.get("version_tag") == "v2", (
            f"W3: manager.spawn_instance must receive the resolved default "
            f"version_tag='v2'; got: {call_kwargs.get('version_tag')!r}"
        )
        assert call_kwargs.get("agent_id") == "developer"
        assert call_kwargs.get("model") == "gpt-4o"
        # Sanity: tool returned the spawned instance id.
        assert "new-councilor-instance-id" in result

    @pytest.mark.asyncio
    async def test_no_default_configured_forwards_none(
        self, repo, engine, system_default_project_id
    ):
        """No default seeded → helper returns None → manager receives
        ``version_tag=None`` (lifecycle falls back to base)."""
        _create_project_row(engine, system_default_project_id)
        # No metadata seeded.

        manager = _make_council_manager(project_repository=repo)
        councilor_tool = _get_councilor_tool(manager)

        # Spy on the helper; let it run for real (no DB row → None).
        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            fake_registry = MagicMock()
            fake_registry.get_version.return_value = MagicMock()
            fake_registry.resolve_to_id.return_value = "developer"
            with patch("daemon.registry.get_registry", return_value=fake_registry):
                await councilor_tool.coroutine(
                    councilor_agent_id="developer",
                    model="gpt-4o",
                    initial_message="please help",
                )

        # Assert manager.spawn_instance was called with version_tag=None.
        call_kwargs = manager.spawn_instance.call_args.kwargs
        assert call_kwargs.get("version_tag") is None, (
            "W3: when no default is configured, manager.spawn_instance must "
            f"receive version_tag=None; got: {call_kwargs.get('version_tag')!r}"
        )

    @pytest.mark.asyncio
    async def test_tool_does_not_accept_version_tag_kwarg(
        self, repo, engine, system_default_project_id
    ):
        """Pydantic input-model rejects extra ``version_tag=...`` kwarg.

        If a future caller (or a misbehaving agent) tried to pass it,
        the Pydantic schema would raise a ``ValidationError`` because the
        field no longer exists. This guards against silent re-introduction.
        """
        _create_project_row(engine, system_default_project_id)
        _seed_default_versions(repo, system_default_project_id, {"developer": "v2"})

        manager = _make_council_manager(project_repository=repo)
        councilor_tool = _get_councilor_tool(manager)

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            fake_registry = MagicMock()
            fake_registry.get_version.return_value = MagicMock()
            fake_registry.resolve_to_id.return_value = "developer"
            with patch("daemon.registry.get_registry", return_value=fake_registry):
                # The LangChain StructuredTool invocation MUST raise a
                # ValidationError when an unknown ``version_tag`` kwarg is
                # passed — Pydantic v2 rejects extra fields by default.
                with pytest.raises(Exception) as excinfo:
                    await councilor_tool.coroutine(
                        councilor_agent_id="developer",
                        model="gpt-4o",
                        initial_message="please help",
                        version_tag="v99",  # rejected by schema
                    )

        # Pydantic ValidationError mentions 'version_tag'.
        assert "version_tag" in str(excinfo.value), (
            f"ValidationError should mention version_tag; got: {excinfo.value}"
        )
        # And manager.spawn_instance must NOT have been called — the bad
        # arg must short-circuit before any spawn work.
        manager.spawn_instance.assert_not_called()