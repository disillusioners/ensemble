"""Unit tests for restoring instances while preserving the original version tag.

These tests pin the contract for several related fixes in
``daemon.services.instance_lifecycle._restore_instance``:

* **S5 — preserve ``original_agent_tag`` across restore fallbacks.** When an
  instance was spawned against a tagged agent version (``agent_tag='v2'``)
  but the versioned directory is no longer on disk at restore time, the
  fallback to the base version mutates ``meta.agent_tag`` permanently in
  the DB. We preserve the originally-requested tag in
  ``instance_metadata['original_agent_tag']`` so that if the versioned dir
  reappears, a future restore can re-elevate the instance back to ``v2``.

* **S5 (clear-on-success) — drop stale ``original_agent_tag`` on a clean
  restore.** When restore succeeds with the correct version (no fallback
  needed) the ``original_agent_tag`` key, if present from a previous
  fallback, must be removed so we don't carry obsolete metadata forward.

* **S6-restore — pass ``validate_path=True`` to ``registry.get_version``.**
  ``AgentRegistry.get_version`` now accepts ``validate_path=False`` by
  default to avoid breaking call-site expectations; the restore path opts
  in so a missing versioned directory cleanly falls back to the base
  version rather than handing back cached metadata pointing at a
  non-existent path.

* **F3 — re-elevation consumer.** At restore time, BEFORE the existing
  fallback path, attempt to re-elevate to ``original_agent_tag`` if the
  versioned directory has reappeared on disk. On success, promote
  ``meta.agent_tag`` back to the original version, update ``meta.agent_dir``
  to the versioned path, and clear the stale ``original_agent_tag`` from
  ``instance_metadata``. On failure (dir still missing), the existing F2
  fallback logic proceeds unchanged.

* **F5 — thread context.** The ``get_version(..., validate_path=True)``
  call performs a blocking ``Path.exists()`` syscall. The restore path
  runs directly on the event loop (called from the async
  ``get_instance()`` without a ``to_thread`` wrapper), so the lookup is
  off-loaded via ``asyncio.to_thread`` to avoid stalling the loop.

The tests intentionally use ``MagicMock`` for the registry so the
``get_version`` / ``get_resolved`` return values can be swapped per test
without depending on the real filesystem-backed registry.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from daemon.services.instance_lifecycle import InstanceLifecycleService


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_mock_manager() -> MagicMock:
    """Build an ``InstanceManager`` mock sufficient for ``_restore_instance``.

    The restore path reads the following attributes off ``self._manager``:

    * ``_instance_repository`` — patched with a ``MagicMock`` whose
      ``update`` / ``set_metadata`` / ``delete_metadata`` are auto-mocked.
    * ``_project_repository`` — auto-mocked.
    * ``prompt_cache`` — set to a plain ``dict`` (the real cache contract).
    * ``_live_hub`` — auto-mocked.
    * ``_mcp_service`` — explicitly set to ``None`` so
      ``_get_mcp_tool_names`` short-circuits its MCP-cache branch and uses
      the stored / empty-list fallback.
    * ``shared_meta_kv_repo`` — accessed by
      ``_apply_post_cache_appends``; auto-mocked via ``MagicMock``.
    * ``config.queue.llm_retry_transient_attempts``,
      ``config.queue.llm_retry_timeout_attempts`` — integer defaults for
      retry config.
    * ``config.llm.*`` — model / base_url / api_key / temperature / etc.
    * ``config.limits.graph_recursion_limit`` — recursion guard.
    * ``config.language.check_enabled`` — language auto-detect toggle.

    Returns:
        A ``MagicMock`` whose attribute access mirrors the production
        manager well enough to exercise the restore path end-to-end without
        spinning up a real DB / LLM stack.
    """
    manager = MagicMock()
    manager._instance_repository = MagicMock()
    manager._project_repository = MagicMock()
    manager._engine = MagicMock()
    manager._live_hub = MagicMock()
    manager._checkpointer = None
    manager._compactor = None
    manager.instances = {}
    manager.prompt_cache = MagicMock()
    manager._mcp_service = None  # force _get_mcp_tool_names fallback path
    manager.shared_meta_kv_repo = MagicMock()

    mock_config = MagicMock()
    mock_config.queue.llm_retry_transient_attempts = 3
    mock_config.queue.llm_retry_timeout_attempts = 2
    mock_config.llm.base_url = None
    mock_config.llm.api_key = "test-key"
    mock_config.llm.model = "gpt-4"
    mock_config.llm.model_vision = False
    mock_config.llm.temperature = 0.7
    mock_config.llm.request_timeout = 60
    mock_config.llm.allowed_models = []
    mock_config.limits.graph_recursion_limit = 1000
    mock_config.language.check_enabled = False
    manager.config = mock_config

    return manager


def _make_meta(
    *,
    agent_id: str = "test-agent",
    instance_id: str = "test-instance-uuid",
    agent_tag: str | None = None,
    instance_metadata: dict | None = None,
) -> MagicMock:
    """Build a mock ``Instance`` row carrying the fields the restore path reads.

    Attributes populated:
        * ``instance_id`` — primary key, used as ``args[0]`` to
          ``instance_repository.update`` / ``set_metadata`` / ``delete_metadata``.
        * ``agent_id`` — base agent id passed to ``registry.get_version``.
        * ``agent_tag`` — the requested version tag (``"v2"`` etc.).
        * ``agent_dir`` — current on-disk path (mutated by the F2 fallback).
        * ``parent_id`` — parent instance id (or ``None`` for roots).
        * ``project_id`` — project identifier (for auto-loaded skills).
        * ``instance_metadata`` — real ``dict`` (not a ``MagicMock``) so
          ``.get()`` returns a literal value. Tests can pass a pre-populated
          dict to simulate a stale ``original_agent_tag`` from a previous
          fallback.

    Args:
        agent_id: Base agent id passed to the registry.
        instance_id: Instance UUID used to route DB writes.
        agent_tag: Requested version tag (``None`` = base version).
        instance_metadata: Pre-populated metadata dict (or ``None`` for empty).

    Returns:
        ``MagicMock`` standing in for the ``Instance`` row model.
    """
    meta = MagicMock()
    meta.instance_id = instance_id
    meta.agent_id = agent_id
    meta.agent_tag = agent_tag
    meta.agent_dir = "/tmp/test"
    meta.parent_id = None
    meta.project_id = "test-project"
    # Real dict — MagicMock would return MagicMock on ``.get()`` and break
    # ``meta.instance_metadata.get("mcp_tool_names")`` (a list is expected).
    meta.instance_metadata = (
        dict(instance_metadata) if instance_metadata is not None else {}
    )
    return meta


@contextmanager
def _patch_restore_deps(
    mock_registry: MagicMock,
    *,
    update_calls: list | None = None,
    set_metadata_calls: list | None = None,
    delete_metadata_calls: list | None = None,
):
    """Patch every external dependency of ``_restore_instance``.

    Mirrors the ``_patch_restore_dependencies`` helper in
    ``test_llm_config_override.py`` but exposes the registry mock so each
    test can configure ``get_version`` / ``get_resolved`` ``return_value``
    (or ``side_effect``) without re-importing the registry module.

    All three repository write methods (``update`` / ``set_metadata`` /
    ``delete_metadata``) record their ``(instance_id, kwargs)`` /
    ``(instance_id, key, value)`` arguments into the corresponding
    accumulator list so tests can assert which calls were emitted.

    Args:
        mock_registry: Pre-configured ``MagicMock`` for the registry. Tests
            configure ``get_version.return_value`` and ``get_resolved``
            before entering the context.
        update_calls: Optional list that will receive every
            ``instance_repository.update(...)`` call (as
            ``(instance_id, kwargs_dict)``).
        set_metadata_calls: Optional list that will receive every
            ``instance_repository.set_metadata(...)`` call (as
            ``(instance_id, key, value)``).
        delete_metadata_calls: Optional list that will receive every
            ``instance_repository.delete_metadata(...)`` call (as
            ``(instance_id, key)``).

    Yields:
        None — patch effects are active inside the ``with`` block.
    """
    update_rec: list = update_calls if update_calls is not None else []
    set_metadata_rec: list = set_metadata_calls if set_metadata_calls is not None else []
    delete_metadata_rec: list = (
        delete_metadata_calls if delete_metadata_calls is not None else []
    )

    def _record_update(instance_id: str, **kwargs: Any) -> MagicMock:
        update_rec.append((instance_id, dict(kwargs)))
        return MagicMock()

    def _record_set_metadata(instance_id: str, key: str, value: Any) -> MagicMock:
        set_metadata_rec.append((instance_id, key, value))
        return MagicMock()

    def _record_delete_metadata(instance_id: str, key: str) -> MagicMock:
        delete_metadata_rec.append((instance_id, key))
        return MagicMock()

    mock_registry.update = MagicMock()
    # Rebind the repository's auto-mocked write methods to recorders so
    # each call is observable from the test body.
    with patch(
        "daemon.services.instance_lifecycle.get_registry",
        return_value=mock_registry,
    ), patch(
        "daemon.manager.load_and_cache_prompt",
        return_value=("prompt", 100),
    ), patch(
        "daemon.manager.create_instance_tools",
        return_value=[],
    ), patch(
        "daemon.manager.build_instance_graph",
        return_value=MagicMock(),
    ):
        # Inject the recorders AFTER patch.daemon.manager.X so they don't
        # interfere with the build_instance_graph mock.
        yield

    # NOTE: We bind recorders in ``with`` block via post-patch attribute
    # assignment on the mock manager; see ``_restore_instance`` test setup
    # in test_coder_developer_migration.py for the same lazy-binding trick.
    # Here we don't bind because the manager is fixed per test below.


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestS5FallbackPreservesOriginalAgentTag:
    """S5 fix: capture & persist ``original_agent_tag`` when restore falls back.

    When the requested versioned directory is missing on disk,
    ``_restore_instance`` falls back to ``registry.get_resolved`` (base).
    The F2 fallback block mutates ``meta.agent_tag`` and ``meta.agent_dir``
    in-memory AND persists the mutation. The S5 fix additionally captures
    the originally-requested tag into
    ``meta.instance_metadata['original_agent_tag']`` so a future restore
    can re-elevate if the versioned dir reappears.
    """

    async def test_fallback_persists_original_agent_tag(self) -> None:
        """Fallback must capture ``original_agent_tag='v2'`` and persist it.

        Setup:
          * ``meta.agent_tag = "v2"`` (the requested versioned agent)
          * ``registry.get_version(..., 'v2', validate_path=True)`` →
            ``None`` (versioned dir is missing on disk)
          * ``registry.get_resolved(...)`` → base meta with
            ``version_tag=None`` and ``path='/some/base/path'``

        Assertions:
          * ``meta.instance_metadata['original_agent_tag'] == 'v2'``
          * ``meta.agent_tag`` is mutated to ``None`` (the resolved base)
          * ``meta.agent_dir`` is updated to the base path string
          * ``instance_repository.set_metadata(...)`` was called with
            ``('test-instance-uuid', 'original_agent_tag', 'v2')``
        """
        manager = _make_mock_manager()

        # Track every repository write the restore path makes.
        update_calls: list = []
        set_metadata_calls: list = []
        delete_metadata_calls: list = []

        # Wire recorders onto the manager's auto-mocked repository so we
        # observe the actual call arguments without interfering with the
        # patch context manager.
        manager._instance_repository.update.side_effect = (
            lambda instance_id, **kwargs: update_calls.append((instance_id, dict(kwargs)))
            or MagicMock()
        )
        manager._instance_repository.set_metadata.side_effect = (
            lambda instance_id, key, value: set_metadata_calls.append(
                (instance_id, key, value)
            )
            or MagicMock()
        )
        manager._instance_repository.delete_metadata.side_effect = (
            lambda instance_id, key: delete_metadata_calls.append((instance_id, key))
            or MagicMock()
        )

        mock_registry = MagicMock()
        # CRITICAL: MagicMock.get_version() returns truthy by default. We
        # MUST explicitly set ``return_value = None`` so the ``agent_meta
        # is None`` branch fires and the fallback path is exercised.
        mock_registry.get_version.return_value = None

        base_meta = MagicMock()
        base_meta.path = Path("/some/base/path")
        base_meta.version_tag = None
        base_meta.id = "test-agent"
        mock_registry.get_resolved.return_value = base_meta

        service = InstanceLifecycleService(manager, MagicMock())
        meta = _make_meta(agent_id="test-agent", agent_tag="v2", instance_id="test-instance-uuid")
        # Pre-populate with another metadata key so we verify we ADD rather
        # than REPLACE the whole dict.
        meta.instance_metadata["mcp_tool_names"] = []

        with patch(
            "daemon.services.instance_lifecycle.get_registry",
            return_value=mock_registry,
        ), patch(
            "daemon.manager.load_and_cache_prompt",
            return_value=("prompt", 100),
        ), patch(
            "daemon.manager.create_instance_tools",
            return_value=[],
        ), patch(
            "daemon.manager.build_instance_graph",
            return_value=MagicMock(),
        ):
            result = await service._restore_instance(meta.instance_id, meta)

        # Restore path must succeed (no exception) and produce a graph.
        assert result is not None, (
            "_restore_instance must return the compiled graph even on fallback"
        )

        # S5 core assertion: original_agent_tag is captured.
        assert meta.instance_metadata.get("original_agent_tag") == "v2", (
            f"Expected meta.instance_metadata['original_agent_tag'] == 'v2', "
            f"got {meta.instance_metadata.get('original_agent_tag')!r}. "
            f"Full metadata: {meta.instance_metadata!r}"
        )

        # F2 invariant: meta.agent_tag was mutated to the resolved tag (None
        # for base), and agent_dir was updated to the base path string.
        assert meta.agent_tag is None, (
            f"Expected meta.agent_tag mutated to None (base), got {meta.agent_tag!r}"
        )
        assert meta.agent_dir == "/some/base/path", (
            f"Expected meta.agent_dir updated to '/some/base/path', got "
            f"{meta.agent_dir!r}"
        )

        # Pre-existing key survived (we mutate in place, not replace).
        assert meta.instance_metadata.get("mcp_tool_names") == [], (
            "Pre-existing instance_metadata keys must be preserved"
        )

        # The original_agent_tag key MUST have been persisted to the DB
        # via set_metadata (atomic JSONB write — ``update`` rejects
        # instance_metadata= to avoid a read-modify-write race).
        assert len(set_metadata_calls) == 1, (
            f"Expected exactly one set_metadata call, got {len(set_metadata_calls)}: "
            f"{set_metadata_calls!r}"
        )
        instance_id, key, value = set_metadata_calls[0]
        assert instance_id == "test-instance-uuid", (
            f"set_metadata instance_id mismatch: got {instance_id!r}"
        )
        assert key == "original_agent_tag", (
            f"set_metadata key mismatch: got {key!r}"
        )
        assert value == "v2", (
            f"set_metadata value mismatch: got {value!r}"
        )

        # The F2 fallback update() must have been called with the new
        # agent_tag + agent_dir but NOT with instance_metadata.
        assert len(update_calls) == 1, (
            f"Expected exactly one update call, got {len(update_calls)}: "
            f"{update_calls!r}"
        )
        uid, ukwargs = update_calls[0]
        assert uid == "test-instance-uuid"
        assert ukwargs.get("agent_tag") is None
        assert ukwargs.get("agent_dir") == "/some/base/path"
        assert "instance_metadata" not in ukwargs, (
            "instance_repository.update() rejects instance_metadata=; we use "
            "set_metadata() instead — never combine them."
        )

        # S5 clear-on-success is the OPPOSITE branch from the F2 fallback,
        # so it must NOT fire here (resolved tag=None != requested 'v2').
        assert delete_metadata_calls == [], (
            f"delete_metadata must NOT fire when fallback occurred "
            f"(resolved tag differs from requested tag). Got: "
            f"{delete_metadata_calls!r}"
        )


class TestS5ClearOnSuccess:
    """S5 fix (clear-on-success): drop stale ``original_agent_tag`` on clean restore.

    When ``registry.get_version`` successfully returns the requested tagged
    meta (no fallback needed), any stale ``original_agent_tag`` left over
    from a previous fallback must be removed. Otherwise the metadata would
    carry an obsolete version reference even though the instance is now
    running on the requested version.

    NOTE: with the F3 re-elevation consumer active, the F3 probe fires
    first. If ``original_agent_tag`` is set AND the corresponding
    versioned dir is on disk, re-elevation takes precedence over
    S5-clear-on-success. These tests use a ``side_effect`` that returns
    ``None`` for the F3 probe (v1 dir still missing) and the requested
    meta for the regular lookup, simulating the realistic state where
    the operator has moved on to a different version and the stale
    original_agent_tag should be cleared.
    """

    async def test_stale_original_agent_tag_is_cleared(self) -> None:
        """Restore with matching tag must pop ``original_agent_tag`` and persist.

        Setup:
          * ``meta.agent_tag = "v2"``
          * ``meta.instance_metadata = {"original_agent_tag": "v1",
             "mcp_tool_names": []}``  ← stale from a previous fallback
          * ``registry.get_version(..., 'v2', validate_path=True)`` →
            meta with ``version_tag='v2'`` (success — versioned dir
            is back on disk)
          * ``registry.get_resolved`` is unused (get_version returned a
            truthy meta so the fallback branch is skipped)

        Assertions:
          * ``original_agent_tag`` is no longer in ``meta.instance_metadata``
          * ``mcp_tool_names`` survives (we only pop the one key)
          * ``instance_repository.delete_metadata`` was called once with
            ``('test-instance-uuid', 'original_agent_tag')``
          * ``instance_repository.set_metadata`` was NOT called (no fallback
            occurred, nothing to persist)
          * ``meta.agent_tag`` and ``meta.agent_dir`` were NOT mutated
        """
        manager = _make_mock_manager()

        update_calls: list = []
        set_metadata_calls: list = []
        delete_metadata_calls: list = []

        manager._instance_repository.update.side_effect = (
            lambda instance_id, **kwargs: update_calls.append((instance_id, dict(kwargs)))
            or MagicMock()
        )
        manager._instance_repository.set_metadata.side_effect = (
            lambda instance_id, key, value: set_metadata_calls.append(
                (instance_id, key, value)
            )
            or MagicMock()
        )
        manager._instance_repository.delete_metadata.side_effect = (
            lambda instance_id, key: delete_metadata_calls.append((instance_id, key))
            or MagicMock()
        )

        mock_registry = MagicMock()

        # Success: get_version returns a meta whose version_tag matches
        # the requested tag, so the F2 fallback block is skipped.
        # BUT the F3 re-elevation block fires first (because
        # ``original_agent_tag='v1'`` is set in instance_metadata). We
        # use ``side_effect`` to differentiate the F3 probe (with
        # original_tag='v1') from the regular lookup (with
        # agent_tag='v2'):
        #   * F3 probe: v1 dir is missing on disk → return None
        #     (re-elevation skipped, original_agent_tag survives)
        #   * Regular lookup: v2 dir is present → return success_meta
        #     (clean restore succeeds, S5 clear-on-success fires)
        success_meta = MagicMock()
        success_meta.path = Path("/agents/test-agent/v2")
        success_meta.version_tag = "v2"
        success_meta.id = "test-agent"

        def _resolve_by_tag(agent_id: str, version_tag, *, validate_path: bool = False):
            # F3 probe uses the ORIGINAL tag ("v1") — simulate the v1
            # dir still being missing.
            if version_tag == "v1":
                return None
            # Regular lookup uses the requested agent_tag ("v2") — v2
            # dir is present.
            return success_meta

        mock_registry.get_version.side_effect = _resolve_by_tag

        service = InstanceLifecycleService(manager, MagicMock())
        meta = _make_meta(
            agent_id="test-agent",
            agent_tag="v2",
            instance_id="test-instance-uuid",
            instance_metadata={"original_agent_tag": "v1", "mcp_tool_names": []},
        )
        original_mcp = list(meta.instance_metadata.get("mcp_tool_names", []))

        with patch(
            "daemon.services.instance_lifecycle.get_registry",
            return_value=mock_registry,
        ), patch(
            "daemon.manager.load_and_cache_prompt",
            return_value=("prompt", 100),
        ), patch(
            "daemon.manager.create_instance_tools",
            return_value=[],
        ), patch(
            "daemon.manager.build_instance_graph",
            return_value=MagicMock(),
        ):
            result = await service._restore_instance(meta.instance_id, meta)

        assert result is not None, (
            "_restore_instance must succeed when get_version returns the "
            "requested tag"
        )

        # S5 clear-on-success core assertion: stale key removed.
        assert "original_agent_tag" not in meta.instance_metadata, (
            f"original_agent_tag must be popped on a clean restore. "
            f"Full metadata: {meta.instance_metadata!r}"
        )

        # Pre-existing unrelated metadata must survive.
        assert meta.instance_metadata.get("mcp_tool_names") == original_mcp, (
            f"mcp_tool_names key must survive the clear; got "
            f"{meta.instance_metadata.get('mcp_tool_names')!r}"
        )

        # The original_agent_tag key MUST be deleted from the DB.
        assert len(delete_metadata_calls) == 1, (
            f"Expected exactly one delete_metadata call, got "
            f"{len(delete_metadata_calls)}: {delete_metadata_calls!r}"
        )
        did, dkey = delete_metadata_calls[0]
        assert did == "test-instance-uuid", (
            f"delete_metadata instance_id mismatch: got {did!r}"
        )
        assert dkey == "original_agent_tag", (
            f"delete_metadata key mismatch: got {dkey!r}"
        )

        # No fallback occurred → no update() / set_metadata() writes.
        assert update_calls == [], (
            f"F2 fallback must NOT fire on a clean restore. "
            f"update_calls={update_calls!r}"
        )
        assert set_metadata_calls == [], (
            f"set_metadata must NOT fire on a clean restore. "
            f"set_metadata_calls={set_metadata_calls!r}"
        )

        # meta.agent_tag / agent_dir untouched — restore succeeded with
        # the requested version, no mutation required.
        assert meta.agent_tag == "v2", (
            f"meta.agent_tag must remain 'v2' on a clean restore, got "
            f"{meta.agent_tag!r}"
        )
        assert meta.agent_dir == "/tmp/test", (
            f"meta.agent_dir must remain unchanged on a clean restore, got "
            f"{meta.agent_dir!r}"
        )


class TestS6RestoreUsesValidatePath:
    """S6 fix: ``_restore_instance`` must call ``get_version(..., validate_path=True)``.

    The registry's ``get_version`` and ``get_resolved`` accept an opt-in
    ``validate_path`` flag. The default (``False``) is unchanged for hot
    paths (spawn / tool resolution). Only ``_restore_instance`` opts in,
    because a versioned directory may have been deleted while the daemon
    was down — without validation, ``get_version`` returns cached
    metadata pointing at a non-existent path and the restore silently
    builds a broken graph.
    """

    async def test_get_version_called_with_validate_path_true(self) -> None:
        """``registry.get_version`` must receive ``validate_path=True``.

        Setup:
          * ``meta.agent_tag = "v2"``
          * ``mock_registry.get_version.return_value = None``
            (explicit — see the ``MagicMock`` default-truthy gotcha)
          * ``mock_registry.get_resolved.return_value`` returns a base meta

        Assertion:
          * ``mock_registry.get_version.assert_called_with(
                'test-agent', 'v2', validate_path=True)``
        """
        manager = _make_mock_manager()

        update_calls: list = []
        set_metadata_calls: list = []
        delete_metadata_calls: list = []

        manager._instance_repository.update.side_effect = (
            lambda instance_id, **kwargs: update_calls.append((instance_id, dict(kwargs)))
            or MagicMock()
        )
        manager._instance_repository.set_metadata.side_effect = (
            lambda instance_id, key, value: set_metadata_calls.append(
                (instance_id, key, value)
            )
            or MagicMock()
        )
        manager._instance_repository.delete_metadata.side_effect = (
            lambda instance_id, key: delete_metadata_calls.append((instance_id, key))
            or MagicMock()
        )

        mock_registry = MagicMock()
        # CRITICAL gotcha: MagicMock.get_version() returns truthy by
        # default — that would short-circuit the fallback branch and
        # break the assertion below (the kwarg is still passed, but the
        # fallback won't fire so the original_agent_tag path won't be
        # exercised and we'd be testing a different code path).
        mock_registry.get_version.return_value = None

        base_meta = MagicMock()
        base_meta.path = Path("/some/base/path")
        base_meta.version_tag = None
        base_meta.id = "test-agent"
        mock_registry.get_resolved.return_value = base_meta

        service = InstanceLifecycleService(manager, MagicMock())
        meta = _make_meta(agent_id="test-agent", agent_tag="v2", instance_id="test-instance-uuid")

        with patch(
            "daemon.services.instance_lifecycle.get_registry",
            return_value=mock_registry,
        ), patch(
            "daemon.manager.load_and_cache_prompt",
            return_value=("prompt", 100),
        ), patch(
            "daemon.manager.create_instance_tools",
            return_value=[],
        ), patch(
            "daemon.manager.build_instance_graph",
            return_value=MagicMock(),
        ):
            await service._restore_instance(meta.instance_id, meta)

        # S6 core assertion: get_version received the validate_path kwarg.
        mock_registry.get_version.assert_called_with(
            "test-agent", "v2", validate_path=True,
        )
        # Also verify the call args explicitly (more debuggable when it
        # fails) — the agent_id and tag are the ones we set.
        call_args, call_kwargs = mock_registry.get_version.call_args
        assert call_args == ("test-agent", "v2"), (
            f"get_version positional args mismatch: got {call_args!r}"
        )
        assert call_kwargs.get("validate_path") is True, (
            f"get_version must be called with validate_path=True, got "
            f"{call_kwargs.get('validate_path')!r}. Full kwargs: {call_kwargs!r}"
        )

        # Sanity: fallback did fire (because get_version returned None), so
        # the set_metadata write for original_agent_tag should be present.
        assert len(set_metadata_calls) == 1, (
            f"Fallback path must fire (set_metadata for original_agent_tag), "
            f"got {len(set_metadata_calls)} calls: {set_metadata_calls!r}"
        )


class TestF3ReelevationConsumer:
    """F3 fix: S5 re-elevation consumer at restore time.

    The previous fix (commit 93e4911c) captured the originally-requested tag
    in ``instance_metadata['original_agent_tag']`` when a restore fell back
    from a tagged version to base because the versioned directory was
    missing on disk. F3 is the missing consumer: at restore time, BEFORE
    the existing fallback path, attempt to re-elevate back to the original
    tagged version in case the versioned directory has reappeared on disk.

    When the re-elevation succeeds:
      * ``meta.agent_tag`` is promoted back to ``original_agent_tag``
      * ``meta.agent_dir`` is updated to the versioned dir's path
      * ``instance_metadata['original_agent_tag']`` is popped and
        persisted via ``delete_metadata`` (atomic JSONB / JSON write)

    When the re-elevation fails (versioned dir is STILL missing),
    the existing F2 fallback logic must proceed unchanged — the original
    ``original_agent_tag`` MUST survive so a *future* restore can still
    attempt re-elevation.
    """

    async def test_reelevation_succeeds_when_versioned_dir_reappeared(self) -> None:
        """Re-elevate when ``original_agent_tag='v2'`` and versioned dir is back.

        Setup:
          * ``meta.agent_tag = None`` (fell back to base on a prior restore)
          * ``meta.instance_metadata = {"original_agent_tag": "v2",
              "mcp_tool_names": []}``
          * First ``registry.get_version(..., 'v2', validate_path=True)``
            call (the re-elevation probe) returns a versioned meta with
            ``version_tag='v2'`` and ``path='/agents/test-agent/v2'`` —
            simulates the versioned dir having reappeared on disk.
          * Second ``registry.get_version(..., None, validate_path=True)``
            call (the normal restore path, skipped when re-elevated) is
            NOT exercised.

        Assertions:
          * ``meta.agent_tag`` was promoted back to ``"v2"``
          * ``meta.agent_dir`` was updated to ``"/agents/test-agent/v2"``
          * ``original_agent_tag`` is removed from
            ``meta.instance_metadata``
          * ``mcp_tool_names`` survives (we only pop the one key)
          * ``instance_repository.update`` was called with the new
            ``agent_tag='v2'`` and ``agent_dir``
          * ``instance_repository.delete_metadata`` was called with
            ``('test-instance-uuid', 'original_agent_tag')``
          * ``instance_repository.set_metadata`` was NOT called (no
            fallback occurred — re-elevation succeeded instead)
        """
        manager = _make_mock_manager()

        update_calls: list = []
        set_metadata_calls: list = []
        delete_metadata_calls: list = []

        manager._instance_repository.update.side_effect = (
            lambda instance_id, **kwargs: update_calls.append((instance_id, dict(kwargs)))
            or MagicMock()
        )
        manager._instance_repository.set_metadata.side_effect = (
            lambda instance_id, key, value: set_metadata_calls.append(
                (instance_id, key, value)
            )
            or MagicMock()
        )
        manager._instance_repository.delete_metadata.side_effect = (
            lambda instance_id, key: delete_metadata_calls.append((instance_id, key))
            or MagicMock()
        )

        # Configure registry mock: re-elevation probe returns a truthy
        # versioned meta, simulating the versioned dir reappearing.
        mock_registry = MagicMock()
        versioned_meta = MagicMock()
        versioned_meta.path = Path("/agents/test-agent/v2")
        versioned_meta.version_tag = "v2"
        versioned_meta.id = "test-agent"
        # Use ``side_effect`` to return the versioned meta on the FIRST
        # call (the re-elevation probe with original_tag="v2") and
        # ``None`` on any subsequent call. We never expect a second call
        # because re-elevation short-circuits the regular path.
        mock_registry.get_version.side_effect = [versioned_meta]

        service = InstanceLifecycleService(manager, MagicMock())
        # Previous restore fell back to base, so agent_tag is None but
        # original_agent_tag='v2' is preserved in instance_metadata.
        meta = _make_meta(
            agent_id="test-agent",
            agent_tag=None,
            instance_id="test-instance-uuid",
            instance_metadata={"original_agent_tag": "v2", "mcp_tool_names": []},
        )
        original_mcp = list(meta.instance_metadata.get("mcp_tool_names", []))

        with patch(
            "daemon.services.instance_lifecycle.get_registry",
            return_value=mock_registry,
        ), patch(
            "daemon.manager.load_and_cache_prompt",
            return_value=("prompt", 100),
        ), patch(
            "daemon.manager.create_instance_tools",
            return_value=[],
        ), patch(
            "daemon.manager.build_instance_graph",
            return_value=MagicMock(),
        ):
            result = await service._restore_instance(meta.instance_id, meta)

        assert result is not None, (
            "_restore_instance must return the compiled graph after "
            "successful re-elevation"
        )

        # F3 core assertion: meta.agent_tag promoted back to 'v2'.
        assert meta.agent_tag == "v2", (
            f"Re-elevation must set meta.agent_tag back to 'v2', got "
            f"{meta.agent_tag!r}"
        )
        # And agent_dir updated to the versioned dir path.
        assert meta.agent_dir == "/agents/test-agent/v2", (
            f"Re-elevation must update meta.agent_dir to the versioned "
            f"path '/agents/test-agent/v2', got {meta.agent_dir!r}"
        )

        # original_agent_tag is gone from in-memory metadata.
        assert "original_agent_tag" not in meta.instance_metadata, (
            f"original_agent_tag must be popped after successful "
            f"re-elevation. Full metadata: {meta.instance_metadata!r}"
        )
        # Unrelated keys survive.
        assert meta.instance_metadata.get("mcp_tool_names") == original_mcp, (
            f"mcp_tool_names key must survive the re-elevation pop; got "
            f"{meta.instance_metadata.get('mcp_tool_names')!r}"
        )

        # update() was called with the promoted tag + dir.
        assert len(update_calls) == 1, (
            f"Expected exactly one update() call for re-elevation, got "
            f"{len(update_calls)}: {update_calls!r}"
        )
        uid, ukwargs = update_calls[0]
        assert uid == "test-instance-uuid"
        assert ukwargs.get("agent_tag") == "v2", (
            f"update() must persist agent_tag='v2', got "
            f"{ukwargs.get('agent_tag')!r}"
        )
        assert ukwargs.get("agent_dir") == "/agents/test-agent/v2", (
            f"update() must persist agent_dir='/agents/test-agent/v2', got "
            f"{ukwargs.get('agent_dir')!r}"
        )

        # delete_metadata('original_agent_tag') was called to clear the
        # stale key.
        assert len(delete_metadata_calls) == 1, (
            f"Expected exactly one delete_metadata call after "
            f"re-elevation, got {len(delete_metadata_calls)}: "
            f"{delete_metadata_calls!r}"
        )
        did, dkey = delete_metadata_calls[0]
        assert did == "test-instance-uuid"
        assert dkey == "original_agent_tag", (
            f"delete_metadata must target 'original_agent_tag', got "
            f"{dkey!r}"
        )

        # set_metadata must NOT fire — re-elevation succeeded, no
        # fallback occurred.
        assert set_metadata_calls == [], (
            f"set_metadata must NOT fire on successful re-elevation "
            f"(no fallback occurred). Got: {set_metadata_calls!r}"
        )

        # Sanity: get_version was called exactly once (the re-elevation
        # probe). The regular lookup was short-circuited by the
        # ``if not re_elevated`` guard.
        assert mock_registry.get_version.call_count == 1, (
            f"Expected exactly one get_version call (re-elevation probe), "
            f"got {mock_registry.get_version.call_count}: "
            f"{mock_registry.get_version.call_args_list!r}"
        )
        # And the probe was called with the original_tag, NOT the
        # current (base) agent_tag.
        call_args, call_kwargs = mock_registry.get_version.call_args
        assert call_args == ("test-agent", "v2"), (
            f"Re-elevation probe must call get_version with original_tag "
            f"'v2', got {call_args!r}"
        )
        assert call_kwargs.get("validate_path") is True, (
            f"Re-elevation probe must call get_version with "
            f"validate_path=True, got {call_kwargs.get('validate_path')!r}"
        )

    async def test_reelevation_skipped_when_versioned_dir_still_missing(self) -> None:
        """Re-elevation is skipped when versioned dir is STILL missing.

        Setup:
          * ``meta.agent_tag = "v2"`` (input was the tagged version; the
            versioned dir was previously missing so the DB row is still
            tagged 'v2' AND ``original_agent_tag='v2'`` is set from the
            original F2-fallback capture)
          * ``meta.instance_metadata = {"original_agent_tag": "v2",
              "mcp_tool_names": []}``
          * First ``registry.get_version(..., 'v2', validate_path=True)``
            call (the re-elevation probe) returns ``None`` — v2 dir is
            STILL missing on disk.
          * Second ``registry.get_version(..., 'v2', validate_path=True)``
            call (the regular restore path) ALSO returns ``None``.
          * ``registry.get_resolved`` returns the base meta.

        Assertions:
          * Re-elevation was NOT performed (``re_elevated=False``):
            - ``meta.agent_tag`` was DOWNGRADED to ``None`` by the F2
              fallback (NOT promoted back to 'v2' as re-elevation would
              do)
            - ``meta.agent_dir`` was UPDATED to the base path string by
              the F2 fallback
            - ``original_agent_tag`` is RECAPTURED (preserved for a
              future restore attempt — this is the whole point of F3)
          * ``set_metadata('original_agent_tag', 'v2')`` was called
            (existing F2 fallback re-writes it)
          * ``delete_metadata`` was NOT called (we keep the original_tag
            so a future restore can re-elevate)
        """
        manager = _make_mock_manager()

        update_calls: list = []
        set_metadata_calls: list = []
        delete_metadata_calls: list = []

        manager._instance_repository.update.side_effect = (
            lambda instance_id, **kwargs: update_calls.append((instance_id, dict(kwargs)))
            or MagicMock()
        )
        manager._instance_repository.set_metadata.side_effect = (
            lambda instance_id, key, value: set_metadata_calls.append(
                (instance_id, key, value)
            )
            or MagicMock()
        )
        manager._instance_repository.delete_metadata.side_effect = (
            lambda instance_id, key: delete_metadata_calls.append((instance_id, key))
            or MagicMock()
        )

        # Configure registry mock: both get_version calls return None
        # (re-elevation probe misses; regular lookup also misses).
        # CRITICAL gotcha: ``MagicMock.get_version.return_value = None``
        # is required because the default MagicMock return is a truthy
        # MagicMock object — setting ``return_value = None`` explicitly
        # is what makes both probes "miss".
        mock_registry = MagicMock()
        mock_registry.get_version.return_value = None

        base_meta = MagicMock()
        base_meta.path = Path("/some/base/path")
        base_meta.version_tag = None
        base_meta.id = "test-agent"
        mock_registry.get_resolved.return_value = base_meta

        service = InstanceLifecycleService(manager, MagicMock())
        # Input agent_tag="v2" so the F2 fallback mutation fires when the
        # regular restore lookup also misses. The instance_metadata
        # already has original_agent_tag="v2" preserved from a prior
        # restore.
        meta = _make_meta(
            agent_id="test-agent",
            agent_tag="v2",
            instance_id="test-instance-uuid",
            instance_metadata={"original_agent_tag": "v2", "mcp_tool_names": []},
        )

        with patch(
            "daemon.services.instance_lifecycle.get_registry",
            return_value=mock_registry,
        ), patch(
            "daemon.manager.load_and_cache_prompt",
            return_value=("prompt", 100),
        ), patch(
            "daemon.manager.create_instance_tools",
            return_value=[],
        ), patch(
            "daemon.manager.build_instance_graph",
            return_value=MagicMock(),
        ):
            result = await service._restore_instance(meta.instance_id, meta)

        assert result is not None, (
            "_restore_instance must return the compiled graph even when "
            "re-elevation is skipped and the existing fallback fires"
        )

        # F3 skip-assertion 1: meta.agent_tag was DOWNGRADED to None by
        # the F2 fallback (NOT promoted back to 'v2' as re-elevation
        # would do). This proves re-elevation did NOT fire.
        assert meta.agent_tag is None, (
            f"meta.agent_tag must be downgraded to None by the F2 "
            f"fallback when re-elevation is skipped (NOT promoted to "
            f"'v2' by re-elevation), got {meta.agent_tag!r}"
        )

        # F3 skip-assertion 2: meta.agent_dir was UPDATED to the base
        # path by the F2 fallback. The input was '/tmp/test' (from
        # _make_meta); the fallback rewrites it to '/some/base/path'.
        assert meta.agent_dir == "/some/base/path", (
            f"F2 fallback must update meta.agent_dir to base path when "
            f"re-elevation fails, got {meta.agent_dir!r}"
        )

        # F3 skip-assertion 3: original_agent_tag is RECAPTURED (preserved
        # for a future restore attempt — this is the whole point of F3).
        assert meta.instance_metadata.get("original_agent_tag") == "v2", (
            f"original_agent_tag must be preserved when re-elevation "
            f"fails, got {meta.instance_metadata.get('original_agent_tag')!r}. "
            f"Full metadata: {meta.instance_metadata!r}"
        )

        # get_version was called TWICE: once for the re-elevation probe
        # (original_tag='v2'), once for the regular restore path
        # (agent_tag='v2').
        assert mock_registry.get_version.call_count == 2, (
            f"Expected exactly two get_version calls (re-elevation probe "
            f"then regular restore), got {mock_registry.get_version.call_count}: "
            f"{mock_registry.get_version.call_args_list!r}"
        )
        # First call was the re-elevation probe with original_tag='v2'.
        first_call_args, first_call_kwargs = mock_registry.get_version.call_args_list[0]
        assert first_call_args == ("test-agent", "v2"), (
            f"First get_version call must be the re-elevation probe with "
            f"original_tag='v2', got {first_call_args!r}"
        )
        assert first_call_kwargs.get("validate_path") is True, (
            f"Re-elevation probe must call get_version with "
            f"validate_path=True, got {first_call_kwargs.get('validate_path')!r}"
        )

        # Second call was the regular restore path with agent_tag='v2'.
        second_call_args, second_call_kwargs = mock_registry.get_version.call_args_list[1]
        assert second_call_args == ("test-agent", "v2"), (
            f"Second get_version call must be the regular restore path "
            f"with agent_tag='v2', got {second_call_args!r}"
        )
        assert second_call_kwargs.get("validate_path") is True, (
            f"Regular restore path must call get_version with "
            f"validate_path=True, got {second_call_kwargs.get('validate_path')!r}"
        )

        # set_metadata('original_agent_tag', 'v2') was called by the F2
        # fallback to re-capture the original tag.
        assert len(set_metadata_calls) == 1, (
            f"Expected exactly one set_metadata call (F2 fallback "
            f"re-capturing original_agent_tag), got "
            f"{len(set_metadata_calls)}: {set_metadata_calls!r}"
        )
        smid, smkey, smvalue = set_metadata_calls[0]
        assert smid == "test-instance-uuid"
        assert smkey == "original_agent_tag"
        assert smvalue == "v2"

        # update() was called by the F2 fallback to persist the new
        # agent_tag=None and agent_dir='/some/base/path'.
        assert len(update_calls) == 1, (
            f"Expected exactly one update() call (F2 fallback), got "
            f"{len(update_calls)}: {update_calls!r}"
        )
        uid, ukwargs = update_calls[0]
        assert uid == "test-instance-uuid"
        assert ukwargs.get("agent_tag") is None
        assert ukwargs.get("agent_dir") == "/some/base/path"

        # delete_metadata must NOT fire — re-elevation failed and the
        # original_agent_tag must survive for a future restore attempt.
        assert delete_metadata_calls == [], (
            f"delete_metadata must NOT fire when re-elevation fails "
            f"(the original_agent_tag must survive for a future "
            f"restore attempt). Got: {delete_metadata_calls!r}"
        )
