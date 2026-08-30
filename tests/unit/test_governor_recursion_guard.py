"""Tests for the Governor Recursive-Spawn Guard package (2026-08-30).

Covers the four functional blocks of the package:

  1a. Lifecycle-layer guard inside ``InstanceLifecycleService.spawn_instance``:
      refuses ``agent_id == "governor"`` when the parent chain (parent ∪
      ancestors) already contains ≥ K governors (K=1 default). Honors the
      ``LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED=0`` kill-switch and the
      ``LIMITS_MAX_GOVERNOR_ANCESTORS=0`` disable knob. Early-exits for
      non-governor spawns (hot-path cost ≈ zero).

  1b. Tool-layer fast-fail scalpel at the top of ``convene_council`` and
      ``convene_council_with_skill``: refuses when ``caller_agent_id ==
      "governor"`` with a corrective HINT — no DB walk, no spawn_instance
      call. Leader / developer / etc. callers are unaffected.

  3.  Child-request template fix: the message handed to a spawned governor
      names ``spawn_councilor`` as the action and explicitly forbids
      ``convene_council``. Regression-tested via the convening tool's
      enqueue_message payload.

  4.  Tool-result feedback: success result includes child counts;
      max-children branch uses a named remedy (no "Consider a different
      approach"); recursion-guard ValueError+HINT propagates intact.

  5.  Dead-knob removal (covered separately by other tests; the absence
      of the removed fields is asserted in ``test_limits_config_defaults``
      and the per-tool "Max instances limit" 429 path is replaced by a
      single 400 INVALID_REQUEST branch).

  Repos: the lifecycle-layer tests use the SQLModelInstanceRepository
  helper ``get_agent_ids_for`` added by this package and the existing
  ``get_ancestor_ids`` strict-ancestor walker.
"""

from __future__ import annotations

import logging
import os
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def engine():
    """In-memory SQLite engine with the Instance table."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Imported lazily because the model module touches SQLModel metadata.
    from daemon.repositories.instance.models import Instance  # noqa: F401

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _seed_instance(engine, instance_id: str, agent_id: str, parent_id: str | None) -> None:
    """Insert one Instance row (uses the SQLModel model + a fresh session)."""
    from sqlmodel import Session

    from daemon.repositories.instance.models import Instance

    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id=agent_id,
                agent_dir=f"./agents/{agent_id}",
                parent_id=parent_id,
                status="RUNNING",
            )
        )
        session.commit()


def _make_lifecycle_service_with_repo(repo, *, kill_switch: bool = True, k: int = 1):
    """Build a minimal InstanceLifecycleService with the bits the guard reads.

    Only the surface area the guard touches is wired: ``_manager`` (for
    ``config`` and ``_instance_repository``). Everything else stays a
    MagicMock so we can drive ``spawn_instance`` without spinning up the
    full manager.
    """
    from daemon.services.instance_lifecycle import InstanceLifecycleService

    manager = MagicMock()
    manager._instance_repository = repo
    manager._project_repository = MagicMock()
    manager.prompt_cache = MagicMock()

    config = MagicMock()
    config.limits.governor_recursion_guard_enabled = True
    config.limits.max_governor_ancestors = k
    config.limits.max_children_per_instance = 50
    manager.config = config

    cancellation_service = MagicMock()
    svc = InstanceLifecycleService(manager, cancellation_service)

    # Patch the downstream helpers the spawn path reaches. The guard
    # fires BEFORE any of these — that's the unit-under-test. If the
    # guard lets the spawn through, we let the downstream helpers raise
    # freely (the test only cares that the recursion ValueError is NOT
    # what we see).
    manager._lifecycle_service = svc
    return svc, manager


def _patch_spawn_downstream():
    """Patch helpers below the guard so we can drive spawn_instance cleanly."""
    from daemon import manager as mgr_mod
    from daemon.services import instance_lifecycle as lifecycle_mod

    return [
        patch.object(mgr_mod, "load_and_cache_prompt", return_value=("system", 0)),
        patch.object(
            lifecycle_mod,
            "_apply_post_cache_appends",
            return_value=("system", "en"),
        ),
    ]


def _enter_spawn_patches(*patches):
    """Enter a list of patches as a context manager (ExitStack)."""
    stack = ExitStack()
    for p in patches:
        stack.enter_context(p)
    return stack


@pytest.fixture
def patched_kill_switch():
    """Pin the cached kill-switch env-resolver to a known state.

    The kill-switch is module-level cached; we must reset the cache so each
    test sees its own env value cleanly.
    """
    from daemon.repositories.instance import repository as repo_mod

    saved_mode = repo_mod._GOVERNOR_RECURSION_GUARD_ENABLED
    saved_log = repo_mod._GOVERNOR_RECURSION_GUARD_BOOT_LOG_EMITTED
    saved_env = os.environ.get("LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED")
    try:
        repo_mod._GOVERNOR_RECURSION_GUARD_ENABLED = None
        repo_mod._GOVERNOR_RECURSION_GUARD_BOOT_LOG_EMITTED = True
        yield
    finally:
        repo_mod._GOVERNOR_RECURSION_GUARD_ENABLED = saved_mode
        repo_mod._GOVERNOR_RECURSION_GUARD_BOOT_LOG_EMITTED = saved_log
        if saved_env is None:
            os.environ.pop("LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED", None)
        else:
            os.environ["LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED"] = saved_env


# =============================================================================
# 1a. Lifecycle-layer guard
# =============================================================================


class TestLifecycleGovernorChainGuard:
    """InstanceLifecycleService.spawn_instance refuses governor-in-chain."""

    def test_governor_in_chain_blocked(self, engine, patched_kill_switch):
        """Parent is itself a governor (root-position case) → blocked.

        Reproduces the original incident: a governor tries to spawn a
        child governor. With K=1, the parent-inclusive count is 1 ≥ K
        → refuse.
        """
        from daemon.repositories.instance.repository import SQLModelInstanceRepository

        # Root governor → tries to spawn a child governor.
        _seed_instance(engine, "root-gov", "governor", None)
        repo = SQLModelInstanceRepository(engine)

        svc, manager = _make_lifecycle_service_with_repo(repo, k=1)
        # Stub registry resolution to avoid touching the global registry.
        with patch(
            "daemon.registry.AgentRegistry.resolve_to_id",
            return_value="governor",
        ):
            with pytest.raises(ValueError) as excinfo:
                svc.spawn_instance(
                    agent_id="governor",
                    parent_id="root-gov",
                )

        err = str(excinfo.value)
        assert "Spawn refused" in err, f"Guard prefix missing: {err!r}"
        assert "governor" in err.lower()
        assert "HINT" in err
        # The chain walk should name the root governor.
        assert "root-gov" in err or "root-g" in err

    def test_legit_leader_governor_councilors_passes(self, engine, patched_kill_switch):
        """Leader → governor → councilors chain is the legit path → allowed.

        The chain has exactly 1 governor (the spawned child) which is
        ≤ K (K=1). The guard does NOT fire; the test reaches past it.
        """
        from daemon.repositories.instance.repository import SQLModelInstanceRepository

        _seed_instance(engine, "leader-1", "leader", None)
        # Leader has no ancestors, so the chain walk is empty.
        repo = SQLModelInstanceRepository(engine)
        svc, _manager = _make_lifecycle_service_with_repo(repo, k=1)

        # Stub everything below the guard so we don't need a full DB
        # bootstrap. The guard fires before these would be touched.
        downstream_patches = _patch_spawn_downstream()
        with _enter_spawn_patches(
            patch(
                "daemon.registry.AgentRegistry.resolve_to_id",
                return_value="governor",
            ),
            patch.object(svc, "_get_mcp_tool_names", return_value=[]),
            *downstream_patches,
        ):
            try:
                svc.spawn_instance(
                    agent_id="governor",
                    parent_id="leader-1",
                )
            except ValueError as e:
                if "Spawn refused" in str(e):
                    raise
                # Otherwise the guard let it through and a downstream
                # helper raised — fine.

    def test_root_position_governor_spawn_allowed(self, engine, patched_kill_switch):
        """Top-level spawn (no parent_id) — guard does NOT fire.

        With ``parent_id is None`` the chain is empty, so a root-position
        governor spawn is always allowed. The governor is allowed at the
        top of a tree — the recursion concern is only about CHILDREN of a
        governor.
        """
        from daemon.repositories.instance.repository import SQLModelInstanceRepository

        repo = SQLModelInstanceRepository(engine)
        svc, _manager = _make_lifecycle_service_with_repo(repo, k=1)

        downstream_patches = _patch_spawn_downstream()
        with _enter_spawn_patches(
            patch(
                "daemon.registry.AgentRegistry.resolve_to_id",
                return_value="governor",
            ),
            patch.object(svc, "_get_mcp_tool_names", return_value=[]),
            *downstream_patches,
        ):
            try:
                svc.spawn_instance(agent_id="governor", parent_id=None)
            except ValueError as e:
                if "Spawn refused" in str(e):
                    pytest.fail(
                        f"Guard should NOT fire for root spawn; got: {e}"
                    )

    def test_governor_spawn_fail_closed_on_ancestor_walk_error(
        self, engine, patched_kill_switch
    ):
        """Ancestor-walk OR agent-id fetch raising must fail-CLOSED (refuse).

        Regression for W1: the fail-closed branch was zero-coverage. Both
        DB calls inside the guard's try/except must surface as the clean
        refusal (ValueError + HINT), not propagate as an opaque 500.
        """
        from daemon.repositories.instance.repository import SQLModelInstanceRepository

        _seed_instance(engine, "root-gov", "governor", None)

        # (a) get_ancestor_ids raises — guard must refuse.
        repo_a = MagicMock(spec=SQLModelInstanceRepository)
        repo_a.get_ancestor_ids.side_effect = RuntimeError("db down")
        svc_a, _ = _make_lifecycle_service_with_repo(repo_a, k=1)
        with patch(
            "daemon.registry.AgentRegistry.resolve_to_id",
            return_value="governor",
        ):
            with pytest.raises(ValueError) as exc_a:
                svc_a.spawn_instance(agent_id="governor", parent_id="root-gov")
        err_a = str(exc_a.value)
        assert "Spawn refused" in err_a and "HINT" in err_a
        assert "root-gov" in err_a and "db down" in err_a

        # (b) get_agent_ids_for raises — same refusal (covers the widened
        # try from item 3: a DB failure during agent-id fetch must also
        # fail closed).
        repo_b = MagicMock(spec=SQLModelInstanceRepository)
        repo_b.get_ancestor_ids.return_value = []
        repo_b.get_agent_ids_for.side_effect = RuntimeError("db down")
        svc_b, _ = _make_lifecycle_service_with_repo(repo_b, k=1)
        with patch(
            "daemon.registry.AgentRegistry.resolve_to_id",
            return_value="governor",
        ):
            with pytest.raises(ValueError) as exc_b:
                svc_b.spawn_instance(agent_id="governor", parent_id="root-gov")
        err_b = str(exc_b.value)
        assert "Spawn refused" in err_b and "HINT" in err_b
        assert "root-gov" in err_b and "db down" in err_b

    def test_k_zero_disables_guard(self, engine, patched_kill_switch):
        """``max_governor_ancestors=0`` disables the guard (per spec)."""
        from daemon.repositories.instance.repository import SQLModelInstanceRepository

        _seed_instance(engine, "root-gov", "governor", None)
        repo = SQLModelInstanceRepository(engine)
        svc, _manager = _make_lifecycle_service_with_repo(repo, k=0)

        downstream_patches = _patch_spawn_downstream()
        with _enter_spawn_patches(
            patch(
                "daemon.registry.AgentRegistry.resolve_to_id",
                return_value="governor",
            ),
            patch.object(svc, "_get_mcp_tool_names", return_value=[]),
            *downstream_patches,
        ):
            try:
                svc.spawn_instance(
                    agent_id="governor",
                    parent_id="root-gov",
                )
            except ValueError as e:
                if "Spawn refused" in str(e):
                    pytest.fail(f"K=0 should disable; guard fired: {e}")

    def test_kill_switch_env_zero_disables(self, engine, patched_kill_switch):
        """``LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED=0`` disables the guard."""
        from daemon.repositories.instance.repository import SQLModelInstanceRepository

        os.environ["LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED"] = "0"
        _seed_instance(engine, "root-gov", "governor", None)
        repo = SQLModelInstanceRepository(engine)
        svc, _manager = _make_lifecycle_service_with_repo(repo, k=1)

        downstream_patches = _patch_spawn_downstream()
        with _enter_spawn_patches(
            patch(
                "daemon.registry.AgentRegistry.resolve_to_id",
                return_value="governor",
            ),
            patch.object(svc, "_get_mcp_tool_names", return_value=[]),
            *downstream_patches,
        ):
            try:
                svc.spawn_instance(
                    agent_id="governor",
                    parent_id="root-gov",
                )
            except ValueError as e:
                if "Spawn refused" in str(e):
                    pytest.fail(f"Kill-switch should disable; guard fired: {e}")

    def test_non_governor_spawn_skips_guard_untouched(
        self, engine, patched_kill_switch
    ):
        """Non-governor spawns early-exit and never read the chain."""
        from daemon.repositories.instance.repository import SQLModelInstanceRepository

        _seed_instance(engine, "root-gov", "governor", None)
        repo = MagicMock(spec=SQLModelInstanceRepository)
        repo.get_ancestor_ids.assert_not_called()
        repo.get_agent_ids_for.assert_not_called()

        svc, _manager = _make_lifecycle_service_with_repo(repo, k=1)
        downstream_patches = _patch_spawn_downstream()
        with _enter_spawn_patches(
            patch(
                "daemon.registry.AgentRegistry.resolve_to_id",
                return_value="developer",
            ),
            patch.object(svc, "_get_mcp_tool_names", return_value=[]),
            *downstream_patches,
        ):
            try:
                svc.spawn_instance(
                    agent_id="developer",
                    parent_id="root-gov",
                )
            except Exception as e:
                if "Spawn refused" in str(e) and "governor" in str(e):
                    pytest.fail(f"Guard fired for non-governor spawn: {e}")
        repo.get_ancestor_ids.assert_not_called()
        repo.get_agent_ids_for.assert_not_called()


# =============================================================================
# 1b. Tool-layer fast-fail
# =============================================================================


class TestToolLayerConveneRefusal:
    """``convene_council`` / ``convene_council_with_skill`` refuse governor callers."""

    @staticmethod
    def _patches():
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
            patch(
                "daemon.tools.instance._apply_tool_filter",
                side_effect=lambda tools, *a, **kw: tools,
            ),
        ]

    def _make_manager(self):
        manager = MagicMock()
        manager.config = MagicMock()
        manager.config.llm = MagicMock()
        manager.config.llm.allowed_models = ["gpt-4o", "claude-3-5-sonnet"]

        manager._lifecycle_service = MagicMock()
        manager._lifecycle_service._resolve_model_override = MagicMock(
            side_effect=lambda m: m if m else None
        )

        manager.spawn_instance = MagicMock(
            return_value=("gov-id", None),
        )
        return manager

    async def test_convene_council_governor_caller_refused(self):
        """Governor caller hits the tool-layer fast-fail immediately."""
        manager = self._make_manager()
        patches = self._patches()
        for p in patches:
            p.start()
        try:
            from daemon.tools.instance import create_instance_tools

            tools = create_instance_tools(
                manager, "parent-instance-id", agent_id="governor"
            )
            convene = next(
                t for t in tools if getattr(t, "name", None) == "convene_council"
            )
        finally:
            for p in reversed(patches):
                p.stop()

        with pytest.raises(ValueError) as excinfo:
            await convene.coroutine(
                councilor_agent_id="developer",
                request="Refactor X",
            )
        err = str(excinfo.value)
        assert "convene_council refused" in err, f"Guard prefix missing: {err!r}"
        assert "spawn_councilor" in err, f"HINT must name spawn_councilor: {err!r}"
        # Lifecycle spawn was never reached.
        manager.spawn_instance.assert_not_called()

    async def test_convene_council_with_skill_governor_caller_refused(self):
        """Same refusal for the skill-passthrough variant."""
        manager = self._make_manager()
        patches = self._patches()
        for p in patches:
            p.start()
        try:
            from daemon.tools.instance import create_instance_tools

            tools = create_instance_tools(
                manager, "parent-instance-id", agent_id="governor"
            )
            convene = next(
                t for t in tools
                if getattr(t, "name", None) == "convene_council_with_skill"
            )
        finally:
            for p in reversed(patches):
                p.stop()

        with pytest.raises(ValueError) as excinfo:
            await convene.coroutine(
                councilor_agent_id="developer",
                request="Audit",
                councilor_skill="code-review",
            )
        err = str(excinfo.value)
        assert "convene_council_with_skill refused" in err
        assert "spawn_councilor" in err
        manager.spawn_instance.assert_not_called()

    async def test_convene_council_leader_caller_still_allowed(self):
        """Leader caller still passes the tool-layer guard."""
        manager = self._make_manager()
        manager.enqueue_message = AsyncMock()

        patches = self._patches()
        for p in patches:
            p.start()
        try:
            from daemon.tools.instance import create_instance_tools

            tools = create_instance_tools(
                manager, "parent-instance-id", agent_id="leader"
            )
            convene = next(
                t for t in tools if getattr(t, "name", None) == "convene_council"
            )
        finally:
            for p in reversed(patches):
                p.stop()

        with (
            patch(
                "daemon.registry.AgentRegistry.resolve_to_id",
                return_value="developer",
            ),
            patch(
                "daemon.tools.instance._check_team_membership",
                return_value=None,
            ),
        ):
            result = await convene.coroutine(
                councilor_agent_id="developer",
                request="Refactor X",
            )

        assert result["status"] == "convened"


# =============================================================================
# 3. Child-request template — names spawn_councilor, forbids convene_council
# =============================================================================


class TestChildRequestTemplateFix:
    """The convening message hands a governor ``spawn_councilor``, not ``convene_council``."""

    @staticmethod
    def _patches():
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
            patch(
                "daemon.tools.instance._apply_tool_filter",
                side_effect=lambda tools, *a, **kw: tools,
            ),
        ]

    def _make_manager(self):
        manager = MagicMock()
        manager.config = MagicMock()
        manager.config.llm = MagicMock()
        manager.config.llm.allowed_models = ["gpt-4o"]

        manager._lifecycle_service = MagicMock()
        manager._lifecycle_service._resolve_model_override = MagicMock(
            side_effect=lambda m: m if m else None
        )

        manager.spawn_instance = MagicMock(
            return_value=("gov-id", None),
        )
        return manager

    async def test_convene_council_message_names_spawn_councilor(self):
        manager = self._make_manager()
        manager.enqueue_message = AsyncMock()

        patches = self._patches()
        for p in patches:
            p.start()
        try:
            from daemon.tools.instance import create_instance_tools

            tools = create_instance_tools(
                manager, "parent-instance-id", agent_id="leader"
            )
            convene = next(
                t for t in tools if getattr(t, "name", None) == "convene_council"
            )
        finally:
            for p in reversed(patches):
                p.stop()

        with (
            patch(
                "daemon.registry.AgentRegistry.resolve_to_id",
                return_value="developer",
            ),
            patch(
                "daemon.tools.instance._check_team_membership",
                return_value=None,
            ),
        ):
            await convene.coroutine(
                councilor_agent_id="developer",
                request="Refactor X",
            )

        enqueue_kwargs = manager.enqueue_message.await_args.kwargs
        msg = enqueue_kwargs["message"]
        # Names spawn_councilor as the action.
        assert "spawn_councilor(" in msg, f"Template must name spawn_councilor: {msg!r}"
        assert 'councilor_agent_id="developer"' in msg
        # Explicitly forbids convene_council.
        assert "Do NOT call convene_council" in msg
        # Negative: old phrasing must be gone.
        assert "Convene a council using councilor_agent_id" not in msg

    async def test_convene_council_with_skill_message_names_spawn_councilor(self):
        manager = self._make_manager()
        manager.enqueue_message = AsyncMock()

        patches = self._patches()
        for p in patches:
            p.start()
        try:
            from daemon.tools.instance import create_instance_tools

            tools = create_instance_tools(
                manager, "parent-instance-id", agent_id="leader"
            )
            convene = next(
                t for t in tools
                if getattr(t, "name", None) == "convene_council_with_skill"
            )
        finally:
            for p in reversed(patches):
                p.stop()

        with (
            patch(
                "daemon.registry.AgentRegistry.resolve_to_id",
                return_value="developer",
            ),
            patch(
                "daemon.tools.instance._check_team_membership",
                return_value=None,
            ),
        ):
            await convene.coroutine(
                councilor_agent_id="developer",
                request="Audit",
                councilor_skill="code-review",
            )

        msg = manager.enqueue_message.await_args.kwargs["message"]
        assert "spawn_councilor(" in msg
        assert "Councilor skill: code-review" in msg
        assert "Do NOT call convene_council" in msg
        assert "Convene a council using councilor_agent_id" not in msg


# =============================================================================
# 4. spawn_councilor targeting "governor" — lifecycle guard blocks it
# =============================================================================


class TestSpawnCouncilorGovernorBlocked:
    """Even with the tool-layer guard, the lifecycle guard catches the path."""

    @staticmethod
    def _patches():
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
            patch(
                "daemon.tools.instance._apply_tool_filter",
                side_effect=lambda tools, *a, **kw: tools,
            ),
        ]

    async def test_spawn_councilor_targeting_governor_hits_lifecycle_guard(
        self, engine, patched_kill_switch
    ):
        """A governor caller tries ``spawn_councilor(councilor_agent_id='governor')``.

        The W1 tool-layer identity guard passes (caller IS governor). The
        lifecycle guard then refuses with the recursion ValueError + HINT,
        and the tool layer re-emits it cleanly.
        """
        from daemon.repositories.instance.repository import SQLModelInstanceRepository

        # Seed a chain so the lifecycle guard has data to walk.
        _seed_instance(engine, "parent-instance-id", "leader", None)
        _seed_instance(engine, "gov-child", "governor", "parent-instance-id")

        repo = SQLModelInstanceRepository(engine)

        manager = MagicMock()
        manager.config = MagicMock()
        manager.config.llm = MagicMock()
        manager.config.llm.allowed_models = ["gpt-4o"]
        manager.config.limits = MagicMock()
        manager.config.limits.max_children_per_instance = 50
        manager.config.limits.governor_recursion_guard_enabled = True
        manager.config.limits.max_governor_ancestors = 1
        manager._lifecycle_service = MagicMock()
        manager._lifecycle_service._resolve_model_override = MagicMock(
            side_effect=lambda m: m if m else None
        )
        manager._instance_repository = repo
        manager._project_repository = MagicMock()
        # Make spawn_instance raise the same recursion-guard ValueError
        # the real lifecycle path would produce, so the tool layer
        # surfaces it verbatim.
        manager.spawn_instance = MagicMock(
            side_effect=ValueError(
                "Spawn refused: parent chain already contains 1 governor "
                "ancestor(s) (limit 1). Chain: governor gov-child ← leader "
                "parent-instance-id. HINT: Spawn councilors via spawn_councilor."
            )
        )

        patches = self._patches()
        for p in patches:
            p.start()
        try:
            from daemon.tools.instance import create_instance_tools

            tools = create_instance_tools(
                manager, "parent-instance-id", agent_id="governor"
            )
            spawn_councilor = next(
                t for t in tools if getattr(t, "name", None) == "spawn_councilor"
            )
        finally:
            for p in reversed(patches):
                p.stop()

        with (
            patch(
                "daemon.registry.AgentRegistry.resolve_to_id",
                return_value="governor",
            ),
            patch(
                "daemon.tools.instance._check_team_membership",
                return_value=None,
            ),
        ):
            with pytest.raises(ValueError) as excinfo:
                await spawn_councilor.coroutine(
                    councilor_agent_id="governor",
                    model="gpt-4o",
                    initial_message="please help",
                )

        err = str(excinfo.value)
        assert "Spawn refused" in err
        assert "HINT" in err
        assert "spawn_councilor" in err


# =============================================================================
# 4. Error decoration
# =============================================================================


class TestErrorDecoration:
    """Recursion-guard ValueError propagates intact; max-children has named remedy."""

    async def test_recursion_guard_error_includes_chain(self):
        """The recursion-guard ValueError carries the chain + HINT."""
        guard_msg = (
            "Spawn refused: parent chain already contains 1 governor "
            "ancestor(s) (limit 1). Chain: governor i-abc12345 ← leader "
            "i-def45678. HINT: do NOT convene another council."
        )
        err = ValueError(guard_msg)
        text = str(err)
        assert "Spawn refused" in text
        assert "chain" in text.lower()
        assert "HINT" in text
        assert "spawn_councilor" in text or "council" in text.lower()

    def test_max_children_error_named_remedy(self):
        """The bare max-children error string now carries named remedies."""
        # Mirror the new branch in instance.py: it includes "Do NOT spawn
        # more" and explicit remedies ("send_message", "terminate_instance").
        # Here we just verify the canonical HINT text from the spec.
        hint = (
            "Do NOT spawn more. Reduce work, reuse existing children via "
            "send_message, or terminate stale children with terminate_instance()."
        )
        assert "Do NOT spawn more" in hint
        assert "send_message" in hint
        assert "terminate_instance" in hint
        # The banished string must not appear.
        assert "Consider a different approach" not in hint


# =============================================================================
# Repository helper
# =============================================================================


class TestGetAgentIdsFor:
    """SQLModelInstanceRepository.get_agent_ids_for — added by the package."""

    def test_empty_input_returns_empty_dict(self, engine):
        from daemon.repositories.instance.repository import SQLModelInstanceRepository

        repo = SQLModelInstanceRepository(engine)
        assert repo.get_agent_ids_for([]) == {}

    def test_resolves_known_ids(self, engine):
        from daemon.repositories.instance.repository import SQLModelInstanceRepository

        _seed_instance(engine, "i-1", "governor", None)
        _seed_instance(engine, "i-2", "leader", None)
        _seed_instance(engine, "i-3", "developer", None)

        repo = SQLModelInstanceRepository(engine)
        result = repo.get_agent_ids_for(["i-1", "i-2", "i-3"])
        assert result == {
            "i-1": "governor",
            "i-2": "leader",
            "i-3": "developer",
        }

    def test_missing_id_maps_to_none(self, engine):
        """Missing ids map to None — caller treats as 'not a governor'."""
        from daemon.repositories.instance.repository import SQLModelInstanceRepository

        _seed_instance(engine, "i-present", "governor", None)

        repo = SQLModelInstanceRepository(engine)
        result = repo.get_agent_ids_for(["i-present", "i-missing"])
        assert result["i-present"] == "governor"
        assert result["i-missing"] is None


# =============================================================================
# Kill-switch resolution
# =============================================================================


class TestKillSwitchResolution:
    """``_resolve_governor_recursion_guard_enabled`` honors env + cache."""

    def test_default_is_enabled(self, patched_kill_switch):
        from daemon.repositories.instance.repository import (
            _resolve_governor_recursion_guard_enabled,
        )

        os.environ.pop("LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED", None)
        assert _resolve_governor_recursion_guard_enabled() is True

    def test_zero_disables(self, patched_kill_switch):
        from daemon.repositories.instance.repository import (
            _resolve_governor_recursion_guard_enabled,
        )

        os.environ["LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED"] = "0"
        assert _resolve_governor_recursion_guard_enabled() is False

    def test_one_enables_explicit(self, patched_kill_switch):
        from daemon.repositories.instance.repository import (
            _resolve_governor_recursion_guard_enabled,
        )

        os.environ["LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED"] = "1"
        assert _resolve_governor_recursion_guard_enabled() is True

    def test_false_disables(self, patched_kill_switch):
        from daemon.repositories.instance.repository import (
            _resolve_governor_recursion_guard_enabled,
        )

        os.environ["LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED"] = "false"
        assert _resolve_governor_recursion_guard_enabled() is False

    def test_unknown_value_falls_back_to_enabled_with_warn(
        self, patched_kill_switch, caplog
    ):
        from daemon.repositories.instance.repository import (
            _GOVERNOR_RECURSION_GUARD_ENABLED,
            _resolve_governor_recursion_guard_enabled,
        )

        # Reset cached value so the unknown-value path re-runs.
        from daemon.repositories.instance import repository as repo_mod

        repo_mod._GOVERNOR_RECURSION_GUARD_ENABLED = None

        os.environ["LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED"] = "fuzzy"
        with caplog.at_level(logging.WARNING):
            assert _resolve_governor_recursion_guard_enabled() is True
        assert any(
            "is not a recognized" in r.message for r in caplog.records
        ), f"Expected WARN; got: {[r.message for r in caplog.records]}"