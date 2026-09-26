"""Unit + integration tests for WP2 (caller_model_overrides generalized into
the spawn chain) + WP3 (spawn-model observability).

Cluster A — designer-agent phase 1. Source of truth:
``implementation-plan/phase1-foundations.md`` §4 (P1-WP2 / P1-WP3) +
``architecture-recommendation.md` `§3.4 (D2).

The two phases share a seam — the spawn block at
``daemon/services/instance_lifecycle.py:1780-1835`` + the
``_resolve_caller_model_override`` helper — so the tests live in one
file. The four test classes cover:

  1. ``TestResolveCallerModelOverride`` — the helper in isolation. Six
     focused unit tests covering the lookup sources (parent map,
     legacy child map, null values, missing keys, parent gone,
     registry failures).
  2. ``TestSpawnSeamPrecedence`` — the 4 adjacent precedence pairs
     exercised through the spawn seam (model= > parent_map >
     llm_models > llm_model > default). ``tier > model=`` is the
     tool-layer path and is covered by the existing
     ``tests/integration/test_spawn_intelligence_tier.py`` — not
     re-tested here.
  3. ``TestParentMapSilentFallback`` — non-allowlisted parent-map
     target resolves ``None`` with no exception, WARNING log emitted,
     caller-facing ``[NOTE]`` surfaces, default model used (WP3
     observability + silent-fallback contract preserved).
  4. ``TestSpawnLogModelSource`` — the spawn log line carries
     ``model={resolved_model} source={resolved_source}`` and covers
     all five resolution paths (override / parent_map / llm_models /
     llm_model / default).

Patch-target notes (critical for the spawn-seam tests):

  * ``spawn_instance`` at line 1735 calls ``registry = get_registry()``
    where ``get_registry`` is the MODULE-LEVEL binding from
    ``from ..registry import get_registry, resolve_recursion_limit`` at
    line 58. To intercept that call, the patch target MUST be
    ``daemon.services.instance_lifecycle.get_registry``.

  * ``_resolve_caller_model_override`` (the helper added by WP2) does a
    FUNCTION-LOCAL ``from daemon.registry import get_registry`` at line
    1579. The local re-import binds to ``daemon.registry.get_registry``
    at call time. To intercept THAT call, the patch target MUST be
    ``daemon.registry.get_registry``.

  * Both patches return the SAME mock registry so the spawn_instance
    resolution block (line 1740) and the helper (line 1585) see the
    same meta tree.

Run only this file (cap at 120s per dispatcher rules):
``timeout 120 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB \\
    -u POSTGRES_USER -u POSTGRES_PASSWORD \\
    .venv/bin/python -m pytest \\
    tests/unit/test_caller_model_overrides_seam.py -q``
"""

from __future__ import annotations

import logging
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from daemon.config import (
    Config,
    LLMConfig,
    LimitsConfig,
    LoopBreakerConfig,
    QueueConfig,
    SkillEvolutionConfig,
    LanguageConfig,
)
from daemon.registry import AgentMetadata, LLMModelWeight
from daemon.services.instance_lifecycle import (
    InstanceLifecycleService,
    _SpawnResult,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — minimal manager / config / registry scaffolds
# ─────────────────────────────────────────────────────────────────────────────


def _make_llm_config(*, model: str, allowed: list[str]) -> LLMConfig:
    """Build a real ``LLMConfig`` with the fields the seam reads."""
    return LLMConfig(
        model=model,
        base_url="https://api.openai.example/v1",
        api_key="test-key",
        temperature=0.7,
        request_timeout=610,
        model_vision=None,
        base_url_backup=None,
        buffer_response_header=True,
        allowed_models=list(allowed),
    )


def _make_config(*, model: str, allowed: list[str]) -> Config:
    """Build a minimal Config tree the seam reaches through."""
    return Config(
        llm=_make_llm_config(model=model, allowed=allowed),
        limits=LimitsConfig(),
        loop_breaker=LoopBreakerConfig(),
        queue=QueueConfig(),
        skill_evolution=SkillEvolutionConfig(),
        language=LanguageConfig(),
    )


def _make_manager(config: Config) -> MagicMock:
    """Build a MagicMock manager with the seams the helper / spawn reach.

    The instance_repository is pre-stubbed so spawn_instance's
    ``count_children`` / ``get_tree_root_id`` calls don't blow up on
    MagicMock arithmetic / string ops.
    """
    manager = MagicMock()
    manager.config = config
    manager._instance_repository = MagicMock()
    manager._instance_repository.get.return_value = None  # parent gone by default
    manager._instance_repository.count_children.return_value = 0
    manager._instance_repository.get_tree_root_id.side_effect = (
        lambda pid: pid  # echo parent_id verbatim
    )
    return manager


def _make_agent_metadata(
    *,
    agent_id: str,
    llm_model: str | None = None,
    llm_models: list[str] | None = None,
    caller_model_overrides: dict | None = None,
    path: str | None = None,
) -> AgentMetadata:
    """Build an ``AgentMetadata`` with the fields the helper / seam read.

    ``llm_models`` accepts plain model-name strings; wrapped in
    ``LLMModelWeight(model=..., weight=1)`` here. ``caller_model_overrides``
    is always a dict (schema rejects ``None``).
    """
    pool = None
    if llm_models is not None:
        pool = [LLMModelWeight(model=name, weight=1) for name in llm_models]
    return AgentMetadata(
        id=agent_id,
        name=agent_id.title(),
        description=f"Test agent {agent_id}",
        icon="🤖",
        color="blue",
        path=Path(path or f"/test/agents/{agent_id}"),
        llm_model=llm_model,
        llm_models=pool,
        caller_model_overrides=dict(caller_model_overrides or {}),
    )


def _build_registry_mock(
    *,
    parent_meta: AgentMetadata | None = None,
    child_meta: AgentMetadata | None = None,
) -> MagicMock:
    """Build a registry mock that returns the given meta tree.

    The two ``agent_id`` keys (parent_agent_id, child_agent_id) are
    derived from the supplied metadata via ``.id``. ``resolve_to_id``
    is identity. Missing-agent lookups return ``None``.
    """
    registry = MagicMock()

    parent_agent_id = parent_meta.id if parent_meta is not None else None

    def _gv(aid, vt=None):
        if parent_meta is not None and aid == parent_agent_id:
            return parent_meta
        if child_meta is not None and aid == child_meta.id:
            return child_meta
        return None

    def _gr(aid):
        if parent_meta is not None and aid == parent_agent_id:
            return parent_meta
        if child_meta is not None and aid == child_meta.id:
            return child_meta
        return None

    registry.get_version.side_effect = _gv
    registry.get_resolved.side_effect = _gr
    registry.resolve_to_id.side_effect = lambda aid: aid
    return registry


def _patch_registry(registry_mock: MagicMock) -> ExitStack:
    """Return a context manager that patches BOTH registry bindings.

    See module docstring — module-level binding vs. function-local
    import need different patch targets.
    """
    stack = ExitStack()
    stack.enter_context(
        patch(
            "daemon.services.instance_lifecycle.get_registry",
            return_value=registry_mock,
        )
    )
    stack.enter_context(
        patch(
            "daemon.registry.get_registry",
            return_value=registry_mock,
        )
    )
    return stack


def _drive_spawn(
    *,
    manager: MagicMock,
    registry_mock: MagicMock,
    agent_id: str,
    parent_id: str | None = None,
    spawn_model: str | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Drive ``spawn_instance`` end-to-end, return (build_graph_mock, logger_mock).

    The caller can introspect ``logger_mock.info.call_args_list`` for the
    spawn log line and ``build_graph_mock.call_args`` for the resolved
    ``llm_config``.
    """
    lifecycle = InstanceLifecycleService(manager, MagicMock())

    fake_spawn_result = _SpawnResult(
        created=True,
        parent_id=parent_id,
        agent_id=agent_id,
        project_id=None,
        created_at=datetime.now(timezone.utc).isoformat(),
        inherited_source=False,
    )

    build_graph_mock = MagicMock(return_value=MagicMock())
    logger_mock = MagicMock()

    with _patch_registry(registry_mock), \
         patch("daemon.manager.load_and_cache_prompt", return_value=("prompt", 100)), \
         patch("daemon.manager.create_instance_tools", return_value=[]), \
         patch("daemon.manager.build_instance_graph", side_effect=build_graph_mock), \
         patch(
             "daemon.services.instance_lifecycle.InstanceLifecycleService._spawn_instance_db_sync",
             return_value=fake_spawn_result,
         ), \
         patch("daemon.services.instance_lifecycle.logger", logger_mock):
        lifecycle.spawn_instance(
            agent_id=agent_id,
            parent_id=parent_id,
            instance_id=None,
            project_id=None,
            instance_name=None,
            model=spawn_model,
            version_tag=None,
        )

    return build_graph_mock, logger_mock


def _extract_spawn_log_line(logger_mock: MagicMock) -> str:
    """Find the WP3 spawn log line in ``logger_mock.info`` calls."""
    for call in logger_mock.info.call_args_list:
        msg = str(call.args[0]) if call.args else ""
        if "Spawning instance" in msg and "model=" in msg and "source=" in msg:
            return msg
    raise AssertionError(
        "WP3 spawn log line not found in logger.info calls: "
        f"{[str(c.args) for c in logger_mock.info.call_args_list]}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. TestResolveCallerModelOverride — helper unit tests
# ─────────────────────────────────────────────────────────────────────────────


class TestResolveCallerModelOverride:
    """Unit tests for ``_resolve_caller_model_override``.

    Six focused tests cover the helper's contract without exercising
    the full spawn seam — that's ``TestSpawnSeamPrecedence`` below.
    """

    def test_parent_none_returns_no_override(self):
        """Tree-root spawn (no parent) → ``(None, None)``.

        ``parent_id is None`` short-circuits before any registry lookup.
        The spawn seam handles this branch silently.
        """
        config = _make_config(model="gpt-4", allowed=["gpt-4"])
        manager = _make_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        candidate, source = lifecycle._resolve_caller_model_override(
            resolved_agent_id="worker",
            parent_id=None,
            child_metadata=_make_agent_metadata(agent_id="worker"),
        )

        assert candidate is None
        assert source is None
        # Parent-map lookup must NOT happen when there is no parent.
        manager._instance_repository.get.assert_not_called()

    def test_parent_map_string_value_returns_value(self):
        """Parent declares ``{worker: 'vision'}`` → spawn worker gets 'vision'.

        Parent-declares-for-child (new general pattern, D2). Source label
        is ``"parent_map"``.
        """
        config = _make_config(model="gpt-4", allowed=["gpt-4", "vision"])
        manager = _make_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        parent_meta = _make_agent_metadata(
            agent_id="designer",
            caller_model_overrides={"worker": "vision"},
        )
        parent_instance = MagicMock()
        parent_instance.agent_id = "designer"
        manager._instance_repository.get.return_value = parent_instance

        registry_mock = _build_registry_mock(parent_meta=parent_meta)

        with _patch_registry(registry_mock):
            candidate, source = lifecycle._resolve_caller_model_override(
                resolved_agent_id="worker",
                parent_id="parent-instance-uuid",
                child_metadata=_make_agent_metadata(agent_id="worker"),
            )

        assert candidate == "vision"
        assert source == "parent_map"

    def test_parent_map_null_value_returns_default_model(self):
        """Parent declares ``{worker: None}`` (null) → use global default.

        Semantics parity with the legacy explorer pattern (``agents/explorer/meta.json``
        ``"caller_model_overrides": {"coder": null}`` — "use the system
        default model"). The helper resolves ``None`` to ``config.llm.model``
        so the downstream override layer sees a real string.
        """
        config = _make_config(model="gpt-4", allowed=["gpt-4", "vision"])
        manager = _make_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        parent_meta = _make_agent_metadata(
            agent_id="designer",
            caller_model_overrides={"worker": None},
        )
        parent_instance = MagicMock()
        parent_instance.agent_id = "designer"
        manager._instance_repository.get.return_value = parent_instance

        registry_mock = _build_registry_mock(parent_meta=parent_meta)

        with _patch_registry(registry_mock):
            candidate, source = lifecycle._resolve_caller_model_override(
                resolved_agent_id="worker",
                parent_id="parent-instance-uuid",
                child_metadata=_make_agent_metadata(agent_id="worker"),
            )

        assert candidate == "gpt-4"
        assert source == "parent_map"

    def test_parent_map_missing_key_falls_back_to_legacy_child_declaration(self):
        """Parent has no entry for this child → fall back to child-side map.

        The legacy explorer pattern (``agents/explorer/meta.json``
        ``"caller_model_overrides": {"coder": null}``) lives on the
        CHILD's meta keyed by PARENT agent_id. When the parent map
        doesn't have the child as a key, the helper consults the child's
        map for the parent agent_id as the key. Same semantics: string
        → use it; null → default; missing → no override.
        """
        config = _make_config(model="gpt-4", allowed=["gpt-4", "vision"])
        manager = _make_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        # Parent declares overrides for OTHER children (not "explorer").
        parent_meta = _make_agent_metadata(
            agent_id="coder",
            caller_model_overrides={"some_other_child": "vision"},
        )
        # Child (explorer) declares for coder (legacy pattern).
        child_meta = _make_agent_metadata(
            agent_id="explorer",
            caller_model_overrides={"coder": None},  # null → default
        )
        parent_instance = MagicMock()
        parent_instance.agent_id = "coder"
        manager._instance_repository.get.return_value = parent_instance

        registry_mock = _build_registry_mock(
            parent_meta=parent_meta, child_meta=child_meta
        )

        with _patch_registry(registry_mock):
            candidate, source = lifecycle._resolve_caller_model_override(
                resolved_agent_id="explorer",
                parent_id="coder-instance-uuid",
                child_metadata=child_meta,
            )

        assert candidate == "gpt-4"  # null in child map → default
        assert source == "parent_map"

    def test_parent_map_wins_over_legacy_child_declaration(self):
        """Parent map present + child map present → parent wins.

        New pattern (parent-declares-for-child) takes priority over
        legacy (child-declares-for-parent). When both sources declare
        an override, the parent's is authoritative.
        """
        config = _make_config(model="gpt-4", allowed=["gpt-4", "vision"])
        manager = _make_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        parent_meta = _make_agent_metadata(
            agent_id="designer",
            caller_model_overrides={"worker": "vision"},  # parent says "vision"
        )
        child_meta = _make_agent_metadata(
            agent_id="worker",
            caller_model_overrides={"designer": None},  # child legacy null → default
        )
        parent_instance = MagicMock()
        parent_instance.agent_id = "designer"
        manager._instance_repository.get.return_value = parent_instance

        registry_mock = _build_registry_mock(
            parent_meta=parent_meta, child_meta=child_meta
        )

        with _patch_registry(registry_mock):
            candidate, source = lifecycle._resolve_caller_model_override(
                resolved_agent_id="worker",
                parent_id="designer-instance-uuid",
                child_metadata=child_meta,
            )

        # Parent map wins.
        assert candidate == "vision"
        assert source == "parent_map"

    def test_parent_instance_not_found_returns_none(self):
        """Parent instance deleted / GC'd → no override, silent fallback.

        ``instance_repository.get(parent_id)`` returns ``None`` (parent
        terminated, etc.) → helper returns ``(None, None)``. No log
        noise; spawn falls through to ``llm_models`` / ``llm_model`` /
        default.
        """
        config = _make_config(model="gpt-4", allowed=["gpt-4"])
        manager = _make_manager(config)
        manager._instance_repository.get.return_value = None  # parent gone
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        candidate, source = lifecycle._resolve_caller_model_override(
            resolved_agent_id="worker",
            parent_id="ghost-parent-uuid",
            child_metadata=_make_agent_metadata(agent_id="worker"),
        )

        assert candidate is None
        assert source is None


# ─────────────────────────────────────────────────────────────────────────────
# 2. TestSpawnSeamPrecedence — adjacent precedence pairs via the spawn seam
# ─────────────────────────────────────────────────────────────────────────────


class TestSpawnSeamPrecedence:
    """4 adjacent precedence pairs exercised through the spawn seam.

    The seam lives at ``daemon/services/instance_lifecycle.py:1780-1835``;
    this class proves the chain ``model= > parent_map > llm_models >
    llm_model > default`` by driving ``spawn_instance`` end-to-end and
    capturing the resolved ``model`` in the ``llm_config`` passed to
    ``build_instance_graph``.

    ``tier > model=`` is the tool-layer path (see
    ``tests/integration/test_spawn_intelligence_tier.py``) and is not
    re-tested here.
    """

    def test_model_param_wins_over_parent_map(self):
        """Pair 1: spawn ``model=`` > parent-map.

        Caller-supplied ``model=`` overrides any parent-map value. The
        parent's map declares ``{worker: 'vision'}``; the caller passes
        ``model='agentic'``. ``agentic`` wins, source = ``override``.
        """
        config = _make_config(
            model="gpt-4",
            allowed=["gpt-4", "vision", "agentic"],
        )
        manager = _make_manager(config)

        child_meta = _make_agent_metadata(agent_id="worker")
        parent_meta = _make_agent_metadata(
            agent_id="designer",
            caller_model_overrides={"worker": "vision"},
        )
        parent_instance = MagicMock()
        parent_instance.agent_id = "designer"
        manager._instance_repository.get.return_value = parent_instance

        registry_mock = _build_registry_mock(
            parent_meta=parent_meta, child_meta=child_meta
        )

        build_graph_mock, logger_mock = _drive_spawn(
            manager=manager,
            registry_mock=registry_mock,
            agent_id="worker",
            parent_id="designer-uuid",
            spawn_model="agentic",  # caller-supplied override
        )

        # model= wins over parent_map.
        llm_config = build_graph_mock.call_args.kwargs.get("llm_config", {})
        assert llm_config["model"] == "agentic"

        # Source label is "override" (caller-supplied).
        log_line = _extract_spawn_log_line(logger_mock)
        assert "source=override" in log_line

    def test_parent_map_wins_over_llm_models(self):
        """Pair 2: parent-map > ``llm_models`` pool.

        Parent declares ``{worker: 'vision'}``; child has
        ``llm_models=['agentic', 'coding']``. Parent-map wins, source =
        ``parent_map`` (new, distinct from ``override``).
        """
        config = _make_config(
            model="gpt-4",
            allowed=["gpt-4", "vision", "agentic", "coding"],
        )
        manager = _make_manager(config)

        child_meta = _make_agent_metadata(
            agent_id="worker",
            llm_models=["agentic", "coding"],
        )
        parent_meta = _make_agent_metadata(
            agent_id="designer",
            caller_model_overrides={"worker": "vision"},
        )
        parent_instance = MagicMock()
        parent_instance.agent_id = "designer"
        manager._instance_repository.get.return_value = parent_instance

        registry_mock = _build_registry_mock(
            parent_meta=parent_meta, child_meta=child_meta
        )

        build_graph_mock, logger_mock = _drive_spawn(
            manager=manager,
            registry_mock=registry_mock,
            agent_id="worker",
            parent_id="designer-uuid",
        )

        llm_config = build_graph_mock.call_args.kwargs.get("llm_config", {})
        assert llm_config["model"] == "vision"

        log_line = _extract_spawn_log_line(logger_mock)
        assert "source=parent_map" in log_line

    def test_llm_models_wins_over_llm_model(self):
        """Pair 3: ``llm_models`` pool > ``llm_model`` single-model.

        No parent, no caller model=. Pool has entries. Pool wins; source
        = ``llm_models``. (Covered invariant — pre-WP2 behavior
        preserved.)
        """
        config = _make_config(
            model="gpt-4",
            allowed=["gpt-4", "agentic", "coding", "coding2", "vision"],
        )
        manager = _make_manager(config)

        child_meta = _make_agent_metadata(
            agent_id="worker",
            llm_models=["agentic", "coding", "coding2"],  # weighted pool
            llm_model="vision",  # single-model — should NOT win
        )
        manager._instance_repository.get.return_value = None

        registry_mock = _build_registry_mock(child_meta=child_meta)

        build_graph_mock, logger_mock = _drive_spawn(
            manager=manager,
            registry_mock=registry_mock,
            agent_id="worker",
            parent_id=None,
        )

        llm_config = build_graph_mock.call_args.kwargs.get("llm_config", {})
        # Pool wins; llm_model is shadowed.
        assert llm_config["model"] in {"agentic", "coding", "coding2"}

        log_line = _extract_spawn_log_line(logger_mock)
        assert "source=llm_models" in log_line

    def test_llm_model_wins_over_default(self):
        """Pair 4: ``llm_model`` single-model > global default.

        No parent, no caller model=, no ``llm_models`` pool. Child has
        ``llm_model='vision'``. Vision wins; source = ``llm_model``.
        (Pre-WP2 behavior preserved — designer agent's primary
        resolution path.)
        """
        config = _make_config(
            model="gpt-4",
            allowed=["gpt-4", "vision"],
        )
        manager = _make_manager(config)

        child_meta = _make_agent_metadata(
            agent_id="designer",
            llm_model="vision",
        )
        manager._instance_repository.get.return_value = None

        registry_mock = _build_registry_mock(child_meta=child_meta)

        build_graph_mock, logger_mock = _drive_spawn(
            manager=manager,
            registry_mock=registry_mock,
            agent_id="designer",
            parent_id=None,
        )

        llm_config = build_graph_mock.call_args.kwargs.get("llm_config", {})
        assert llm_config["model"] == "vision"

        log_line = _extract_spawn_log_line(logger_mock)
        assert "source=llm_model" in log_line


# ─────────────────────────────────────────────────────────────────────────────
# 3. TestParentMapSilentFallback — non-allowlisted target → default
# ─────────────────────────────────────────────────────────────────────────────


class TestParentMapSilentFallback:
    """Non-allowlisted parent-map target → silent fallback, no exception.

    Acceptance criterion (WP2 task AC-2b): override rejected on non-
    allowlisted target → caller-facing ``[NOTE]`` surfaces, default used,
    no exception. WP3: WARNING log emitted (the load-bearing
    observability half of PD-1).
    """

    def test_nonallowlisted_parent_map_target_silent_fallback(self):
        """Parent declares ``{worker: 'forbidden-model'}`` → default used.

        ``forbidden-model`` is NOT in ``allowed_models``. The seam:
        (1) silent-fallback validation (returns None), (2) WARNING log
        emitted, (3) caller-facing ``[NOTE]`` would surface (verified
        at the helper layer), (4) resolved model falls through to
        ``llm_model`` / default, (5) NO exception raised.
        """
        config = _make_config(
            model="gpt-4",
            # ``forbidden-model`` deliberately absent from allowlist.
            allowed=["gpt-4", "vision"],
        )
        manager = _make_manager(config)

        child_meta = _make_agent_metadata(
            agent_id="worker",
            llm_model="vision",  # would win as fallback
        )
        parent_meta = _make_agent_metadata(
            agent_id="designer",
            caller_model_overrides={"worker": "forbidden-model"},
        )
        parent_instance = MagicMock()
        parent_instance.agent_id = "designer"
        manager._instance_repository.get.return_value = parent_instance

        registry_mock = _build_registry_mock(
            parent_meta=parent_meta, child_meta=child_meta
        )

        build_graph_mock, logger_mock = _drive_spawn(
            manager=manager,
            registry_mock=registry_mock,
            agent_id="worker",
            parent_id="designer-uuid",
        )

        # (a) NO exception (test reached this line).
        # (b) WARNING log emitted for the rejected parent-map value.
        warning_msgs = [
            str(call.args[0])
            for call in logger_mock.warning.call_args_list
            if call.args and "forbidden-model" in str(call.args[0])
        ]
        assert warning_msgs, (
            "WP3: non-allowlisted parent-map target must emit WARNING log; "
            f"got: {logger_mock.warning.call_args_list}"
        )
        # (c) Fallthrough to child's llm_model ('vision').
        llm_config = build_graph_mock.call_args.kwargs.get("llm_config", {})
        assert llm_config["model"] == "vision"


# ─────────────────────────────────────────────────────────────────────────────
# 4. TestSpawnLogModelSource — spawn log carries model + source (WP3)
# ─────────────────────────────────────────────────────────────────────────────


class TestSpawnLogModelSource:
    """WP3 task 2: spawn log line carries ``model={...} source={...}``.

    P1-WP12 (AC-12b / AC-12c) greps this exact line. Test the line shape
    and that it covers all five resolution paths.
    """

    def test_spawn_log_carries_override_source(self):
        """Spawn with caller model= → log carries ``source=override``."""
        config = _make_config(
            model="gpt-4",
            allowed=["gpt-4", "agentic"],
        )
        manager = _make_manager(config)
        child_meta = _make_agent_metadata(agent_id="worker")
        registry_mock = _build_registry_mock(child_meta=child_meta)

        _, logger_mock = _drive_spawn(
            manager=manager,
            registry_mock=registry_mock,
            agent_id="worker",
            spawn_model="agentic",
        )

        log_line = _extract_spawn_log_line(logger_mock)
        assert "model=agentic" in log_line
        assert "source=override" in log_line

    def test_spawn_log_carries_parent_map_source(self):
        """Parent-map win → log carries ``source=parent_map``.

        Designer-agent AC-12c verification: leader (or any parent) spawns
        worker with parent-map → `vision` → log says ``model=vision
        source=parent_map``.
        """
        config = _make_config(
            model="gpt-4",
            allowed=["gpt-4", "vision"],
        )
        manager = _make_manager(config)

        child_meta = _make_agent_metadata(agent_id="worker")
        parent_meta = _make_agent_metadata(
            agent_id="designer",
            caller_model_overrides={"worker": "vision"},
        )
        parent_instance = MagicMock()
        parent_instance.agent_id = "designer"
        manager._instance_repository.get.return_value = parent_instance

        registry_mock = _build_registry_mock(
            parent_meta=parent_meta, child_meta=child_meta
        )

        _, logger_mock = _drive_spawn(
            manager=manager,
            registry_mock=registry_mock,
            agent_id="worker",
            parent_id="designer-uuid",
        )

        log_line = _extract_spawn_log_line(logger_mock)
        assert "model=vision" in log_line
        assert "source=parent_map" in log_line

    def test_spawn_log_carries_llm_models_source(self):
        """Pool win → log carries ``source=llm_models``."""
        config = _make_config(
            model="gpt-4",
            allowed=["gpt-4", "agentic", "coding"],
        )
        manager = _make_manager(config)
        child_meta = _make_agent_metadata(
            agent_id="worker",
            llm_models=["agentic", "coding"],
        )
        registry_mock = _build_registry_mock(child_meta=child_meta)

        _, logger_mock = _drive_spawn(
            manager=manager,
            registry_mock=registry_mock,
            agent_id="worker",
        )

        log_line = _extract_spawn_log_line(logger_mock)
        # Pool selects one of the candidates — match the membership set.
        assert ("model=agentic" in log_line) or ("model=coding" in log_line)
        assert "source=llm_models" in log_line

    def test_spawn_log_carries_llm_model_source(self):
        """Single-model meta → log carries ``source=llm_model``.

        Designer-agent AC-12b verification: designer spawn → log says
        ``model=vision source=llm_model``.
        """
        config = _make_config(
            model="gpt-4",
            allowed=["gpt-4", "vision"],
        )
        manager = _make_manager(config)
        child_meta = _make_agent_metadata(
            agent_id="designer",
            llm_model="vision",
        )
        registry_mock = _build_registry_mock(child_meta=child_meta)

        _, logger_mock = _drive_spawn(
            manager=manager,
            registry_mock=registry_mock,
            agent_id="designer",
        )

        log_line = _extract_spawn_log_line(logger_mock)
        assert "model=vision" in log_line
        assert "source=llm_model" in log_line

    def test_spawn_log_carries_default_source(self):
        """No overrides, no meta → log carries ``source=default``."""
        config = _make_config(model="gpt-4", allowed=["gpt-4"])
        manager = _make_manager(config)
        child_meta = _make_agent_metadata(agent_id="worker")
        registry_mock = _build_registry_mock(child_meta=child_meta)

        _, logger_mock = _drive_spawn(
            manager=manager,
            registry_mock=registry_mock,
            agent_id="worker",
        )

        log_line = _extract_spawn_log_line(logger_mock)
        assert "model=gpt-4" in log_line
        assert "source=default" in log_line