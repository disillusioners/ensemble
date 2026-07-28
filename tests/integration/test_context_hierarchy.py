"""Instance hierarchy context resolution tests for the Context Injection Restructure.

These integration tests verify that context_key (tree-root instance ID) is
correctly inherited and resolved across parent→child instance hierarchies:

1. Child instance inherits the parent's context_key, not a new one.
2. ``assemble_context_messages()`` reads shared context from the inherited
   context_key's directory for child instances.
3. Each context_key has an isolated KV store — a child sees only its
   inherited context_key's KV, not a sibling or unrelated context_key's.
4. ``append_context_key`` PERSONA appender injects the correct root
   context_key for child instances.
5. ``human_messages`` mode (``assemble_context_messages``) uses the
   inherited context_key for child instances.

Patterns mirror:
- ``tests/integration/test_context_freshness.py`` — same engine/repo
  setup, same ``_build_manager_stub`` approach.
- ``tests/integration/test_shared_context_e2e.py`` — same parent→child
  instance creation via ``SQLModelInstanceRepository``.
- ``tests/integration/test_context_in_graph.py`` — same
  ``SimpleNamespace`` stub pattern for agent_meta.

Run only this file::

    pytest tests/integration/test_context_hierarchy.py -v --timeout=30
"""

from __future__ import annotations

import tempfile
import uuid
from types import SimpleNamespace
from typing import Any

import pytest


# ============================================================================
# Engine + repository helpers (mirrors test_context_freshness.py)
# ============================================================================


def _build_engine():
    """Build an in-memory SQLite engine with all relevant tables.

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
    from daemon.repositories.shared_context.models import SharedContextMetadata

    _ = (Instance, InstanceHierarchy, SharedContextMetadata)
    SQLModel.metadata.create_all(engine)
    return engine


def _build_manager_stub(
    engine,
    *,
    skill_service: Any = None,
    project_repo: Any = None,
):
    """Build a duck-typed manager stub exposing only the slots ``assemble_context_messages`` reads.

    Mirrors ``_build_manager_stub`` in ``tests/integration/test_context_freshness.py``.
    Real repositories on the same engine exercise end-to-end DB reads.

    Returns:
        A ``SimpleNamespace`` with:

        * ``manager`` — duck-typed manager with
          ``_shared_context_metadata_repo``, ``_instance_repository``,
          ``_project_repository``, ``_skill_injection_service``.
        * ``shared_repo`` — the real
          :class:`SharedContextMetadataRepository`.
        * ``instance_repo`` — the real
          :class:`SQLModelInstanceRepository`.
    """
    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    from daemon.repositories.shared_context.repository import (
        SharedContextMetadataRepository,
    )

    shared_repo = SharedContextMetadataRepository(engine)
    instance_repo = SQLModelInstanceRepository(engine)

    if project_repo is None:
        project_repo = _MagicMock_get_returning_none()

    manager = SimpleNamespace(
        _shared_context_metadata_repo=shared_repo,
        _instance_repository=instance_repo,
        _project_repository=project_repo,
        _skill_injection_service=skill_service,
    )

    return SimpleNamespace(
        manager=manager,
        shared_repo=shared_repo,
        instance_repo=instance_repo,
    )


def _MagicMock_get_returning_none():  # noqa: N802
    """Build a MagicMock whose ``get`` returns ``None`` (no project)."""
    from unittest.mock import MagicMock

    repo = MagicMock()
    repo.get.return_value = None
    repo.list_critical_notes.return_value = []
    repo.get_recent_history.return_value = []
    return repo


def _create_root_instance(instance_repo: Any, instance_id: str) -> None:
    """Create a root instance via the real repo.

    Mirrors ``_create_root_instance`` in ``tests/integration/test_context_freshness.py``.
    The row exists so tree-root resolution can walk the parent chain.
    """
    instance_repo.create(
        instance_id=instance_id,
        agent_id="developer",
        agent_dir="/agents/developer",
        parent_id=None,
        project_id="default",
        metadata={"title": "root"},
    )


def _create_child_instance(
    instance_repo: Any,
    instance_id: str,
    parent_id: str,
) -> None:
    """Create a child instance linked to an existing parent via the real repo.

    Args:
        instance_repo: SQLModelInstanceRepository.
        instance_id: Unique child instance identifier.
        parent_id: Existing parent instance identifier (must already exist).
    """
    instance_repo.create(
        instance_id=instance_id,
        agent_id="developer",
        agent_dir="/agents/developer",
        parent_id=parent_id,
        project_id="default",
        metadata={"title": "child"},
    )


from daemon.registry import ContextInjectionConfig


def _import_assemble_context_messages():
    """Lazy import of the orchestrator under test.

    Avoids circular import during test collection (graph ↔ services cycle).
    """
    from daemon.services.context_messages import assemble_context_messages

    return assemble_context_messages


# ============================================================================
# 1. Test context_key inheritance
# ============================================================================


class TestContextKeyInheritance:
    """Test 1: Child instance inherits the parent's context_key (tree-root ID).

    The context_key for any instance is its tree-root instance ID:
    - Root instance → context_key = its own instance_id.
    - Child instance → context_key = get_tree_root_id(parent_id).

    This test verifies the tree-root resolution via the real instance repository.
    """

    def test_child_context_key_is_parent_root_id(self):
        """Child's context_key resolves to the root instance's ID via tree walk.

        Setup:
        1. Create root instance (root-id).
        2. Create parent instance with parent_id=root-id.
        3. Create child instance with parent_id=parent-id.
        4. Call instance_repo.get_tree_root_id(child-id).

        Expected: returns root-id.
        """
        engine = _build_engine()
        bundle = _build_manager_stub(engine)

        root_id = f"root-hier-{uuid.uuid4().hex[:8]}"
        parent_id = f"parent-hier-{uuid.uuid4().hex[:8]}"
        child_id = f"child-hier-{uuid.uuid4().hex[:8]}"

        # Root first.
        _create_root_instance(bundle.instance_repo, root_id)

        # Parent is a child of root.
        _create_child_instance(bundle.instance_repo, parent_id, root_id)

        # Child is a grandchild of root.
        _create_child_instance(bundle.instance_repo, child_id, parent_id)

        # Verify tree-root resolution returns the root for every level.
        assert bundle.instance_repo.get_tree_root_id(child_id) == root_id
        assert bundle.instance_repo.get_tree_root_id(parent_id) == root_id
        assert bundle.instance_repo.get_tree_root_id(root_id) == root_id

    def test_orphan_child_falls_back_to_parent_id(self):
        """Child whose parent row is absent falls back to parent_id.

        Mirrors the defensive fallback in ``_resolve_tree_root_id``:
        ``get_tree_root_id`` returns None when the parent row doesn't exist,
        and the caller falls back to ``parent_id`` itself.
        """
        engine = _build_engine()
        bundle = _build_manager_stub(engine)

        orphan_id = f"orphan-{uuid.uuid4().hex[:8]}"
        phantom_parent = f"phantom-{uuid.uuid4().hex[:8]}"

        # Create an instance that references a non-existent parent.
        bundle.instance_repo.create(
            instance_id=orphan_id,
            agent_id="developer",
            agent_dir="/agents/developer",
            parent_id=phantom_parent,
            project_id="default",
            metadata={},
        )

        # Tree-root walk hits a missing row → returns None.
        assert bundle.instance_repo.get_tree_root_id(orphan_id) is None


# ============================================================================
# 2. Test context resolution uses correct context_key for child
# ============================================================================


class TestContextResolutionUsesCorrectKey:
    """Test 2: ``assemble_context_messages`` reads shared context for a child
    using the inherited (tree-root) context_key's directory."""

    @pytest.mark.asyncio
    async def test_child_reads_root_context_dir(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """A child instance reads .md files from its tree-root's context directory.

        Setup:
        1. Create root + child instances.
        2. Write a .md file inside root's context directory (not child's).
        3. Call ``assemble_context_messages`` for the child with parent_id=root.
        4. Assert the file content appears in the assembled messages.

        The RAG lookup uses ``get_shared_context(context_key, ...)`` where
        ``context_key`` is resolved via ``_resolve_tree_root_id`` →
        ``get_tree_root_id(parent_id)`` → root's id.
        """
        engine = _build_engine()
        bundle = _build_manager_stub(engine)

        root_id = f"root-resolve-{uuid.uuid4().hex[:8]}"
        child_id = f"child-resolve-{uuid.uuid4().hex[:8]}"
        _create_root_instance(bundle.instance_repo, root_id)
        _create_child_instance(bundle.instance_repo, child_id, root_id)

        # Patch tempfile.gettempdir so resolve_context_dir() lands in tmp_path.
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        # Write a .md file inside ROOT's context directory (not child's).
        context_dir = tmp_path / "ensemble" / "context" / root_id
        context_dir.mkdir(parents=True, exist_ok=True)

        marker = "CONTEXT_KEY_CHILD_READ_ROOT_MARKER"
        filename = "root-context-file_20260101_120000.md"
        file_path = context_dir / filename
        file_path.write_text(
            f"# Root Context File\n\nMarker: {marker}\n",
            encoding="utf-8",
        )

        agent_meta = SimpleNamespace(
            context_injection=ContextInjectionConfig(heuristic_match_shared_md_files=True),
            skill_injection=False,
        )

        assemble = _import_assemble_context_messages()

        result = await assemble(
            instance_id=child_id,
            user_query="root context file",
            project_id=None,
            agent_meta=agent_meta,
            manager=bundle.manager,
            instance_repository=bundle.instance_repo,
            parent_id=root_id,
        )

        all_content = "\n".join(str(m.content) for m in result)
        assert marker in all_content, (
            f"Marker missing — child did not read root's context directory. "
            f"Got content (first 500 chars): {all_content[:500]!r}"
        )


# ============================================================================
# 3. Test shared context metadata isolation
# ============================================================================


class TestSharedContextMetadataIsolation:
    """Test 3: Each context_key has an isolated KV store.

    A child instance sees only the KV belonging to its inherited
    context_key (tree-root), not a sibling's or unrelated context_key's KV.
    """

    @pytest.mark.asyncio
    async def test_child_sees_only_inherited_context_key_kv(self) -> None:
        """KV written to root-1's context_key is invisible to a child of root-2.

        Setup:
        1. Create two unrelated root instances (root-A, root-B).
        2. Write marker-A to root-A's KV.
        3. Write marker-B to root-B's KV.
        4. Create child-of-root-A.
        5. Call ``assemble_context_messages`` for child-of-root-A.
        6. Assert marker-A is present and marker-B is absent.
        """
        engine = _build_engine()
        bundle = _build_manager_stub(engine)

        root_a = f"root-iso-a-{uuid.uuid4().hex[:8]}"
        root_b = f"root-iso-b-{uuid.uuid4().hex[:8]}"
        child_of_a = f"child-iso-a-{uuid.uuid4().hex[:8]}"

        _create_root_instance(bundle.instance_repo, root_a)
        _create_root_instance(bundle.instance_repo, root_b)
        _create_child_instance(bundle.instance_repo, child_of_a, root_a)

        marker_a = "ISOLATION_ROOT_A_MARKER_123"
        marker_b = "ISOLATION_ROOT_B_MARKER_456"

        # Write KV to both roots.
        bundle.shared_repo.set_many(root_a, {"root_a_key": marker_a})
        bundle.shared_repo.set_many(root_b, {"root_b_key": marker_b})

        agent_meta = SimpleNamespace(
            context_injection=ContextInjectionConfig(heuristic_match_shared_md_files=True),
            skill_injection=False,
        )

        assemble = _import_assemble_context_messages()

        result = await assemble(
            instance_id=child_of_a,
            user_query="isolation test",
            project_id=None,
            agent_meta=agent_meta,
            manager=bundle.manager,
            instance_repository=bundle.instance_repo,
            parent_id=root_a,
        )

        all_content = "\n".join(str(m.content) for m in result)
        assert marker_a in all_content, (
            f"Root-A marker missing from child's context. "
            f"Got content: {all_content[:500]!r}"
        )
        assert marker_b not in all_content, (
            f"Root-B marker leaked into child-of-root-A's context — "
            f"isolation broken. Got content: {all_content[:500]!r}"
        )


# ============================================================================
# 4. Test append_context_key PERSONA appender output
# ============================================================================


class TestAppendContextKeyPersonaAppender:
    """Test 4: ``append_context_key`` PERSONA appender injects the correct
    root context_key for child instances.

    The appender must resolve the tree-root ID and inject it so the agent
    prompt receives the shared context directory identifier.
    """

    def test_root_instance_context_key_is_own_id(self):
        """Root instance: append_context_key injects the instance's own ID."""
        from daemon.services.instance_lifecycle import append_context_key

        engine = _build_engine()
        bundle = _build_manager_stub(engine)

        root_id = f"root-append-{uuid.uuid4().hex[:8]}"
        _create_root_instance(bundle.instance_repo, root_id)

        base_prompt = "# System\nYou are an agent."
        result = append_context_key(
            system_prompt=base_prompt,
            instance_id=root_id,
            instance_repository=bundle.instance_repo,
            parent_id=None,
        )

        # Root → context_key = its own instance_id.
        assert f"CONTEXT_KEY: {root_id}" in result
        # Placeholder resolved.
        assert "{{ENSEMBLE_CONTEXT_KEY}}" not in result

    def test_child_instance_context_key_is_root_id(self):
        """Child instance: append_context_key injects the tree-root ID (not child's own ID)."""
        from daemon.services.instance_lifecycle import append_context_key

        engine = _build_engine()
        bundle = _build_manager_stub(engine)

        root_id = f"root-child-append-{uuid.uuid4().hex[:8]}"
        child_id = f"child-child-append-{uuid.uuid4().hex[:8]}"
        _create_root_instance(bundle.instance_repo, root_id)
        _create_child_instance(bundle.instance_repo, child_id, root_id)

        base_prompt = "# System\nYou are a sub-agent."
        result = append_context_key(
            system_prompt=base_prompt,
            instance_id=child_id,
            instance_repository=bundle.instance_repo,
            parent_id=root_id,
        )

        # Child → context_key = root's instance_id (NOT child's own ID).
        assert f"CONTEXT_KEY: {root_id}" in result
        assert f"CONTEXT_KEY: {child_id}" not in result
        # Placeholder resolved.
        assert "{{ENSEMBLE_CONTEXT_KEY}}" not in result

    def test_grandchild_injects_root_id_via_chain(self):
        """Grandchild (child of child): append_context_key still resolves to root ID."""
        from daemon.services.instance_lifecycle import append_context_key

        engine = _build_engine()
        bundle = _build_manager_stub(engine)

        root_id = f"root-grandchild-{uuid.uuid4().hex[:8]}"
        parent_id = f"parent-grandchild-{uuid.uuid4().hex[:8]}"
        grandchild_id = f"grandchild-{uuid.uuid4().hex[:8]}"
        _create_root_instance(bundle.instance_repo, root_id)
        _create_child_instance(bundle.instance_repo, parent_id, root_id)
        _create_child_instance(bundle.instance_repo, grandchild_id, parent_id)

        base_prompt = "# System\nYou are a deeply nested agent."
        result = append_context_key(
            system_prompt=base_prompt,
            instance_id=grandchild_id,
            instance_repository=bundle.instance_repo,
            parent_id=parent_id,
        )

        # Grandchild → context_key = root's instance_id (chain walk).
        assert f"CONTEXT_KEY: {root_id}" in result
        assert f"CONTEXT_KEY: {grandchild_id}" not in result
        assert f"CONTEXT_KEY: {parent_id}" not in result


# ============================================================================
# 5. Test context messages in human_messages mode for child
# ============================================================================


class TestHumanMessagesModeForChild:
    """Test 5: ``assemble_context_messages`` uses the inherited context_key
    when the agent has ``context_injection_mode: human_messages``."""

    @pytest.mark.asyncio
    async def test_child_human_messages_mode_uses_inherited_context_key(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """Child in human_messages mode reads KV from its inherited context_key.

        Setup:
        1. Create root + child instances.
        2. Write a KV under root's context_key.
        3. Call ``assemble_context_messages`` for the child with
           ``context_injection_mode="human_messages"``.
        4. Assert the KV appears in the output.

        This verifies the ``_resolve_tree_root_id`` path inside
        ``assemble_context_messages`` correctly uses ``parent_id`` for
        tree-root resolution in human_messages mode.
        """
        engine = _build_engine()
        bundle = _build_manager_stub(engine)

        root_id = f"root-hm-{uuid.uuid4().hex[:8]}"
        child_id = f"child-hm-{uuid.uuid4().hex[:8]}"
        _create_root_instance(bundle.instance_repo, root_id)
        _create_child_instance(bundle.instance_repo, child_id, root_id)

        # Patch tempfile so the RAG path lands in tmp_path.
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        # Write KV under root's context_key.
        marker = "HUMAN_MESSAGES_CHILD_KV_MARKER"
        bundle.shared_repo.set_many(root_id, {"hm_child_key": marker})

        # Agent meta: human_messages mode, context_injection=ContextInjectionConfig(heuristic_match_shared_md_files=True).
        agent_meta = SimpleNamespace(
            context_injection=ContextInjectionConfig(heuristic_match_shared_md_files=True),
            skill_injection=False,
            context_injection_mode="human_messages",
        )

        assemble = _import_assemble_context_messages()

        result = await assemble(
            instance_id=child_id,
            user_query="human messages child test",
            project_id=None,
            agent_meta=agent_meta,
            manager=bundle.manager,
            instance_repository=bundle.instance_repo,
            parent_id=root_id,
        )

        # The KV marker must appear in the assembled messages.
        all_content = "\n".join(str(m.content) for m in result)
        assert marker in all_content, (
            f"KV marker missing — human_messages child did not read "
            f"inherited context_key. Got content (first 500 chars): "
            f"{all_content[:500]!r}"
        )
        # And it must be carried in a project-context message (KV lives there).
        kinds = [m.additional_kwargs.get("context_kind") for m in result]
        assert "project" in kinds, (
            f"Project context message expected. Got kinds: {kinds}"
        )

    @pytest.mark.asyncio
    async def test_human_messages_mode_empty_when_context_disabled(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        """Legacy mode (regardless of ``context_injection`` boolean) returns ``[]``.

        After the 2026-07-28 mode-gate fix, ``assemble_context_messages``
        short-circuits on ``context_injection_mode="legacy"`` — the 3
        CONTEXT appenders inside ``_apply_post_cache_appends`` own
        the legacy system-prompt path. The legacy
        ``context_injection: false`` boolean alone no longer gates
        the orchestrator: in ``human_messages`` mode (the default),
        agents with ``context_injection: false`` still receive
        context messages (see
        ``TestAssembleContextMessagesModeGate::test_human_messages_mode_without_context_injection_flag_returns_messages``).
        """
        engine = _build_engine()
        bundle = _build_manager_stub(engine)

        root_id = f"root-hm-off-{uuid.uuid4().hex[:8]}"
        child_id = f"child-hm-off-{uuid.uuid4().hex[:8]}"
        _create_root_instance(bundle.instance_repo, root_id)
        _create_child_instance(bundle.instance_repo, child_id, root_id)

        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        # KV written under root's context_key; in legacy mode the
        # orchestrator must skip it entirely.
        bundle.shared_repo.set_many(root_id, {"some_key": "some_value"})

        agent_meta = SimpleNamespace(
            context_injection=False,
            skill_injection=False,
            context_injection_mode="legacy",
        )

        assemble = _import_assemble_context_messages()

        result = await assemble(
            instance_id=child_id,
            user_query="context disabled",
            project_id=None,
            agent_meta=agent_meta,
            manager=bundle.manager,
            instance_repository=bundle.instance_repo,
            parent_id=root_id,
        )

        # Legacy mode → [] regardless of the legacy ``context_injection`` boolean.
        assert result == [], (
            f"Expected [] in legacy mode, got "
            f"{[m.additional_kwargs.get('context_kind') for m in result]}"
        )
