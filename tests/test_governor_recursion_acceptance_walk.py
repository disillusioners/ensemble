"""Governor Recursion Guard — ACCEPTANCE WALK (2026-08-30).

One pytest file that walks the original incident scenario end-to-end and
proves the recursion guard dead at every vector, using REAL components:

  * REAL file-backed SQLite (tmp_path; default pool, ``check_same_thread=
    False`` — the in-memory StaticPool write-interleaving hazard documented
    in the repo blueprint is deliberately avoided).
  * REAL ``SQLModelInstanceRepository`` / ``SQLModelProjectRepository`` over
    that engine; seeded instance rows are REAL DB rows.
  * REAL ``InstanceLifecycleService.spawn_instance`` — the guard, the
    parent-inclusive chain walk (``get_ancestor_ids`` + ``get_agent_ids_for``),
    the max-children check, and the M8 ``_spawn_instance_db_sync`` row+hierarchy
    insert all run for real.
  * REAL tool closures from the REAL ``create_instance_tools`` factory
    (same construction path production uses), bound to REAL instance rows.
  * REAL agent registry (``get_registry()`` discovered over ``agents/``) —
    membership/auth checks (``_check_team_membership``) and
    ``resolve_to_id`` run against the REAL meta.json declarations.
  * REAL config path for the kill-switch legs: ``LIMITS_*`` env vars are set
    and a REAL ``Config()`` (pydantic-settings, ``env_prefix="LIMITS_"``) is
    constructed from that env. No internal/config-attribute patching.
  * REAL ``invoke_agent_and_wait`` routing (daemon.utils).

MOCK FIDELITY RULE compliance — functions under test are NEVER mocked:
``spawn_instance`` (lifecycle), the guard logic, the chain walk, the convene
tools, ``spawn_councilor``, and ``invoke_agent_and_wait`` are all real.
Mocks are confined to TRUE EXTERNALS only:

  * ``daemon.manager.load_and_cache_prompt`` — prompt file content cache
    (external: agent prompt material, not under test).
  * ``daemon.services.instance_lifecycle._apply_post_cache_appends`` —
    context/metadata/time append chain (external context injection).
  * ``daemon.tools.instance`` heavy factory helpers (RAG / knowledge /
    project / job / MCP / opencode / db / infra / context / chart tool
    builders) — external tool categories; the 18-patch factory stack is the
    exact list proven in ``tests/unit/test_governor_recursion_guard.py``.
  * ``svc._get_mcp_tool_names`` — external MCP service.
  * ``daemon.manager.build_instance_graph`` — LangGraph/LLM assembly (the
    walk must not boot graphs or call LLMs; everything UP TO graph assembly
    — including the DB row insert — stays real).
  * Harness manager plumbing that is external infrastructure in production:
    ``_live_hub`` (SSE hub), ``_task_repo`` (job/task queue stub whose
    ``get_by_message`` returns None), ``enqueue_message`` (message queue —
    returns a message-id carrier).

Contract note (V1): the TOOL-LAYER convene refusal's byte-stable text is
``"convene_council refused: you are already a governor. ... HINT: Spawn
councilors via spawn_councilor(...)"`` (see
``daemon/tools/instance.py::_governor_recursion_refusal`` and the shipped
unit suite ``TestToolLayerConveneRefusal``). The literal ``"Spawn refused"``
is the LIFECYCLE guard's message prefix (V2/V4/V6 contracts). This walk
asserts each surface against ITS OWN real contract.

Runtime target: < 3 min. Deterministic; no network, no ports, no daemon
boot, no real LLM calls.
"""

from __future__ import annotations

import os
import re
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, create_engine


# =============================================================================
# Env / kill-switch control (REAL config path)
# =============================================================================

_KILL_ENV = "LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED"
_K_ENV = "LIMITS_MAX_GOVERNOR_ANCESTORS"


class _EnvControl:
    """Set LIMITS_* env and reset the kill-switch resolver cache.

    ``daemon.repositories.instance.repository`` caches the env-resolved
    kill-switch at module level (``_GOVERNOR_RECURSION_GUARD_ENABLED``).
    Resetting that cache to None is the documented invalidation knob (the
    shipped unit suite's ``patched_kill_switch`` fixture does exactly this)
    — it forces the REAL ``_resolve_governor_recursion_guard_enabled`` to
    RE-READ the REAL environment; the resolution logic itself is untouched.
    """

    def __init__(self) -> None:
        self._saved = {k: os.environ.get(k) for k in (_KILL_ENV, _K_ENV)}
        from daemon.repositories.instance import repository as repo_mod

        self._repo_mod = repo_mod
        self._saved_cache = repo_mod._GOVERNOR_RECURSION_GUARD_ENABLED
        self._saved_boot = repo_mod._GOVERNOR_RECURSION_GUARD_BOOT_LOG_EMITTED

    def set(self, *, kill: str | None = None, k: str | None = None) -> None:
        """Apply env values (None = unset) and invalidate the resolver cache."""
        for name, value in ((_KILL_ENV, kill), (_K_ENV, k)):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self._repo_mod._GOVERNOR_RECURSION_GUARD_ENABLED = None
        # Silence the one-time boot log so test output stays clean.
        self._repo_mod._GOVERNOR_RECURSION_GUARD_BOOT_LOG_EMITTED = True

    def restore(self) -> None:
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self._repo_mod._GOVERNOR_RECURSION_GUARD_ENABLED = self._saved_cache
        self._repo_mod._GOVERNOR_RECURSION_GUARD_BOOT_LOG_EMITTED = self._saved_boot


@pytest.fixture
def envctl():
    ctl = _EnvControl()
    ctl.set()  # default: both env vars unset (guard ON, K=1 defaults)
    yield ctl
    ctl.restore()


# =============================================================================
# REAL SQLite engine (file-backed) + REAL repositories
# =============================================================================


@pytest.fixture
def engine(tmp_path):
    """File-backed SQLite engine with the REAL instance/project tables."""
    eng = create_engine(
        f"sqlite:///{tmp_path / 'walk.db'}",
        connect_args={"check_same_thread": False},
    )
    import daemon.repositories.instance.models  # noqa: F401 (Instance + InstanceHierarchy)
    import daemon.repositories.project.models  # noqa: F401 (Project + metadata)

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def seed_instance(engine, instance_id: str, agent_id: str, parent_id: str | None,
                  status: str = "RUNNING") -> str:
    """Insert one REAL Instance row and return its id."""
    from daemon.repositories.instance.models import Instance

    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id=agent_id,
                agent_dir=f"./agents/{agent_id}",
                parent_id=parent_id,
                status=status,
            )
        )
        session.commit()
    return instance_id


def set_row_status(engine, instance_id: str, status: str) -> None:
    """Update one REAL row's status through the ORM (real DB write)."""
    from daemon.repositories.instance.models import Instance

    with Session(engine) as session:
        row = session.get(Instance, instance_id)
        assert row is not None, f"row {instance_id} missing"
        row.status = status
        session.add(row)
        session.commit()


def count_rows(engine) -> int:
    """Count Instance rows — REAL proof a spawn did (not) happen."""
    from sqlalchemy import func, select

    from daemon.repositories.instance.models import Instance

    with Session(engine) as session:
        return session.exec(select(func.count()).select_from(Instance)).one()


def ensure_system_project(engine) -> str | None:
    """Create the REAL system-default project row (mirrors startup).

    Production creates it during app lifespan (``api.py`` →
    ``ensure_system_default_project``) BEFORE any spawn; ``tests/conftest.py``
    pins ``constants.SYSTEM_DEFAULT_PROJECT_ID`` to a deterministic id for
    every test. ``normalize_project_id(None)`` resolves spawns to that id,
    and ``spawn_instance`` validates the project row exists — so the harness
    inserts the same REAL row startup would, keyed by the current global.
    """
    from datetime import datetime, timezone

    from daemon import constants
    from daemon.repositories.project.models import Project, ProjectStatus

    project_id = constants.SYSTEM_DEFAULT_PROJECT_ID
    if project_id is None:
        return None
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        if session.get(Project, project_id) is None:
            session.add(
                Project(
                    project_id=project_id,
                    name="__system_default__",
                    project_type="system",
                    status=ProjectStatus.ACTIVE.value,
                    description="System default project (harness; mirrors startup)",
                    project_metadata={},
                    relationships={},
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
    return project_id


# =============================================================================
# Harness manager facade (real components; externals stubbed)
# =============================================================================


class _StubTaskRepo:
    """Job/task-queue subsystem stub (external). ``get_by_message`` → None
    means "task row not resolvable", which drives
    ``_register_child_completion_watcher`` down its documented no-op path."""

    def get_by_message(self, message_id: str):
        return None


def build_manager(engine):
    """Assemble the harness manager from REAL components.

    ``Config()`` is the REAL pydantic-settings aggregate — ``config.limits``
    is a REAL ``LimitsConfig`` that resolves ``LIMITS_*`` env at construction
    time (the REAL env → config path; no attribute patching).
    """
    from daemon.config import Config
    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    from daemon.repositories.project.repository import SQLModelProjectRepository
    from daemon.services.instance_lifecycle import InstanceLifecycleService
    from daemon.write_pause_guard import WritePauseGuard

    class WalkerManager:
        pass

    mgr = WalkerManager()
    mgr.config = Config()  # REAL config; reads LIMITS_* / OPENAI_* env
    mgr.engine = engine
    mgr.write_guard = WritePauseGuard()
    mgr._instance_repository = SQLModelInstanceRepository(engine)
    mgr._project_repository = SQLModelProjectRepository(engine)
    ensure_system_project(engine)
    mgr.prompt_cache = MagicMock()  # external: prompt content cache
    mgr.instances = {}  # REAL dict (real spawn registers graph handles here)
    mgr._live_hub = MagicMock()  # external: SSE hub
    mgr._notification_broadcaster = None
    mgr._checkpointer = None
    mgr._compactor = None
    mgr._task_repo = _StubTaskRepo()  # external: job/task queue
    # Read (and discarded) by the REAL tool factory at construction time —
    # the corresponding tool-category builders are patched out as externals,
    # but the call-site arguments are still evaluated.
    mgr.project_store = MagicMock()  # external: project store
    mgr.db_connection_repository = MagicMock()
    mgr.db_pool_manager = MagicMock()
    mgr.infra_repository = MagicMock()
    mgr.shared_meta_kv_repo = MagicMock()  # external: meta KV store

    mgr._lifecycle_service = InstanceLifecycleService(
        mgr, cancellation_service=MagicMock()
    )

    # REAL lifecycle spawn (the guard lives here). Called through the same
    # ``manager.spawn_instance`` facade name the tools use.
    mgr.spawn_instance = mgr._lifecycle_service.spawn_instance

    async def _enqueue_message(instance_id, message, source, **_kw):
        # External: message queue insert + worker notification. The tools
        # only read ``.message_id`` off the result.
        return SimpleNamespace(message_id=f"msg-{instance_id[:8]}")

    async def _spawn_instance_with_mcp(**kwargs):
        # External delta vs the real manager method: MCP preload. The spawn
        # itself is the REAL lifecycle service call.
        return mgr._lifecycle_service.spawn_instance(**kwargs)

    mgr.enqueue_message = _enqueue_message
    mgr.spawn_instance_with_mcp = _spawn_instance_with_mcp
    return mgr


# =============================================================================
# Patch stacks
# =============================================================================


def _heavy_factory_patches() -> list:
    """The proven 18-patch factory stack from
    ``tests/unit/test_governor_recursion_guard.py::_patches`` — external
    tool categories only (RAG/KB/MCP/project/job/... )."""

    def _p():
        from unittest.mock import patch

        return [
            patch("daemon.tools.instance.is_rag_enabled", return_value=False),
            patch("daemon.tools.instance.create_rag_tools", return_value=[]),
            patch("daemon.tools.instance.create_knowledge_tools", return_value=[]),
            patch("daemon.tools.instance.create_inner_soul_tool", return_value=MagicMock()),
            patch(
                "daemon.tools.instance.create_access_memory_tool",
                return_value=MagicMock(),
            ),
            patch("daemon.tools.instance.create_project_tools", return_value=[]),
            patch("daemon.tools.instance.create_job_tools_if_available", return_value=[]),
            patch("daemon.tools.instance.create_help_tool", return_value=MagicMock()),
            patch("daemon.tools.instance.create_critical_notes_tools", return_value=[]),
            patch(
                "daemon.tools.instance.create_project_history_tools",
                return_value=[],
            ),
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

    return _p()


@contextmanager
def real_spawn_patches(svc):
    """Patches needed for a spawn that gets PAST the guard to complete.

    Everything guard-related is real; only true externals are patched
    (see module docstring). Includes the heavy tool-factory stack because
    the real spawn path constructs REAL tool closures.
    """
    with ExitStack() as stack:
        for p in _heavy_factory_patches():
            stack.enter_context(p)
        stack.enter_context(
            patch("daemon.manager.load_and_cache_prompt", return_value=("system", 10))
        )
        stack.enter_context(
            patch(
                "daemon.services.instance_lifecycle._apply_post_cache_appends",
                return_value=("system", "en"),
            )
        )
        stack.enter_context(
            patch("daemon.manager.build_instance_graph", return_value=MagicMock())
        )
        stack.enter_context(
            patch.object(svc, "_get_mcp_tool_names", return_value=[])
        )
        yield


@contextmanager
def tools_for(manager, instance_id: str, agent_id: str):
    """Build REAL tool closures via the REAL factory; yield them by name."""
    from daemon.tools.instance import create_instance_tools

    with ExitStack() as stack:
        for p in _heavy_factory_patches():
            stack.enter_context(p)
        tools = create_instance_tools(manager, instance_id, agent_id=agent_id)
        yield {t.name: t for t in tools if getattr(t, "name", None)}


def tool_of(tools: dict, name: str):
    assert name in tools, f"tool {name} not bound; have {sorted(tools)}"
    return tools[name]


# =============================================================================
# V1 — convene tools refuse the governor caller with a corrective HINT
# =============================================================================


class TestV1ConveneToolRefusal:
    @pytest.fixture
    def gov_tree(self, engine, envctl):
        mgr = build_manager(engine)
        root_gov = seed_instance(engine, "iid-root-gov-0001", "governor", None)
        return mgr, engine, root_gov

    async def test_v1_convene_council_refused_with_hint(self, gov_tree):
        mgr, engine, root_gov = gov_tree
        rows_before = count_rows(engine)
        with tools_for(mgr, root_gov, "governor") as tools:
            convene = tool_of(tools, "convene_council")
            with pytest.raises(ValueError) as excinfo:
                await convene.coroutine(
                    councilor_agent_id="worker", request="Do the thing"
                )
        err = str(excinfo.value)
        # Byte-stable tool-layer contract (lifecycle's "Spawn refused"
        # literal belongs to V2/V4/V6 surfaces — see module docstring).
        assert "convene_council refused" in err, f"missing refusal prefix: {err!r}"
        assert "governor" in err.lower()
        assert "HINT" in err, f"refusal must carry corrective HINT: {err!r}"
        assert "spawn_councilor" in err, f"HINT must name the remedy tool: {err!r}"
        # Not a bare error: the structured refusal text, not an opaque crash.
        assert "Unknown agent_id" not in err
        assert "Traceback" not in err
        # The lifecycle spawn was never reached (no REAL row inserted).
        assert count_rows(engine) == rows_before

    async def test_v1_convene_council_with_skill_refused_with_hint(self, gov_tree):
        mgr, engine, root_gov = gov_tree
        rows_before = count_rows(engine)
        with tools_for(mgr, root_gov, "governor") as tools:
            convene = tool_of(tools, "convene_council_with_skill")
            with pytest.raises(ValueError) as excinfo:
                await convene.coroutine(
                    councilor_agent_id="worker",
                    request="Audit it",
                    councilor_skill="code-review",
                )
        err = str(excinfo.value)
        assert "convene_council_with_skill refused" in err, f"prefix: {err!r}"
        assert "governor" in err.lower()
        assert "HINT" in err
        assert "spawn_councilor" in err
        assert "Traceback" not in err
        assert count_rows(engine) == rows_before


# =============================================================================
# V2 — spawn_instance tool refusal originates from the LIFECYCLE guard
# =============================================================================


class TestV2LifecycleGuardViaSpawnTool:
    async def test_v2_spawn_instance_tool_refused_by_lifecycle_guard(
        self, engine, envctl
    ):
        mgr = build_manager(engine)
        root_gov = seed_instance(engine, "iid-root-gov-0002", "governor", None)
        with tools_for(mgr, root_gov, "governor") as tools:
            spawn_tool = tool_of(tools, "spawn_instance")
            result = await spawn_tool.coroutine(agent_id="governor")
        # The tool converts (not raises) — refusal text preserves the
        # LIFECYCLE guard's message shape, proving the origin.
        assert result.startswith("ERROR:"), f"expected refusal: {result!r}"
        assert "Spawn refused" in result, f"lifecycle prefix missing: {result!r}"
        assert "parent chain already contains" in result, (
            f"lifecycle chain-walk message shape missing: {result!r}"
        )
        assert "Chain:" in result, f"chain walk missing: {result!r}"
        assert "governor" in result.lower()
        assert "HINT" in result
        # The walk names the parent (the root governor itself).
        assert "iid-root" in result, f"chain must name the parent: {result!r}"


# =============================================================================
# V3 — a council-spawned governor CHILD is refused on both vectors
# =============================================================================


class TestV3CouncilSpawnedGovernorChildBlocked:
    async def test_v3_governor_child_refused_on_convene_and_spawn(
        self, engine, envctl
    ):
        mgr = build_manager(engine)
        svc = mgr._lifecycle_service
        arch_iid = seed_instance(engine, "iid-arch-03000001", "architect", None)

        # LEGIT-1 flow: the non-governor convenes — governor child created.
        with real_spawn_patches(svc):
            with tools_for(mgr, arch_iid, "architect") as tools:
                convene = tool_of(tools, "convene_council")
                result = await convene.coroutine(
                    councilor_agent_id="governor", request="Investigate X"
                )
        assert isinstance(result, dict) and result["status"] == "convened", (
            f"convene should succeed for architect: {result!r}"
        )
        gov_child = result["governor_instance_id"]
        row = mgr._instance_repository.get(gov_child)
        assert row is not None and row.agent_id == "governor", (
            "council-manager child must be a REAL governor row"
        )
        assert row.parent_id == arch_iid

        # V3a: the governor CHILD convening is refused (tool scalpel).
        with tools_for(mgr, gov_child, "governor") as child_tools:
            child_convene = tool_of(child_tools, "convene_council")
            with pytest.raises(ValueError) as exc_a:
                await child_convene.coroutine(
                    councilor_agent_id="worker", request="recurse?"
                )
        assert "convene_council refused" in str(exc_a.value)

        # V3b: the governor CHILD spawning a governor is refused by the
        # LIFECYCLE guard — chain is parent-INCLUSIVE (the child itself
        # counts), so the chain walk names the child.
        with tools_for(mgr, gov_child, "governor") as child_tools:
            child_spawn = tool_of(child_tools, "spawn_instance")
            result_b = await child_spawn.coroutine(agent_id="governor")
        assert "Spawn refused" in result_b, f"lifecycle guard must fire: {result_b!r}"
        assert "parent chain already contains" in result_b
        assert gov_child[:8] in result_b, (
            f"chain must name the governor parent itself: {result_b!r}"
        )


# =============================================================================
# V4 — root-position governor (must BITE: parent-inclusive counting)
# =============================================================================


class TestV4RootPositionGovernor:
    async def test_v4a_root_governor_spawning_governor_refused(self, engine, envctl):
        """A root governor (parent_id=None) spawning a governor child is
        REFUSED. A strict-ancestors-only chain would compute ancestors(root)
        == [] and WRONGLY allow this — the refusal proves the count includes
        the parent itself."""
        mgr = build_manager(engine)
        root_gov = seed_instance(engine, "iid-root-gov-0004", "governor", None)
        with pytest.raises(ValueError) as excinfo:
            mgr._lifecycle_service.spawn_instance(
                agent_id="governor", parent_id=root_gov
            )
        err = str(excinfo.value)
        assert "Spawn refused" in err, f"guard must refuse: {err!r}"
        assert "parent chain already contains" in err
        assert "governor" in err.lower() and "HINT" in err
        # The chain text names the root governor — parent-inclusive walk.
        assert "iid-root" in err, f"chain must name the root governor: {err!r}"

    async def test_v4b_root_governor_convene_refused(self, engine, envctl):
        mgr = build_manager(engine)
        root_gov = seed_instance(engine, "iid-root-gov-0004", "governor", None)
        rows_before = count_rows(engine)
        with tools_for(mgr, root_gov, "governor") as tools:
            convene = tool_of(tools, "convene_council")
            with pytest.raises(ValueError) as excinfo:
                await convene.coroutine(
                    councilor_agent_id="worker", request="nope"
                )
        err = str(excinfo.value)
        assert "convene_council refused" in err
        assert "governor" in err.lower() and "HINT" in err
        assert count_rows(engine) == rows_before


# =============================================================================
# V5 — invoke_agent_and_wait routes guard refusals readably
# =============================================================================


class TestV5InvokeAndWaitRouting:
    async def test_v5_guard_refusal_readable_and_contrast_generic(
        self, engine, envctl
    ):
        from daemon.utils import invoke_agent_and_wait

        mgr = build_manager(engine)
        root_gov = seed_instance(engine, "iid-root-gov-0005", "governor", None)

        # (1) Guard refusal → structured, readable message (chain walk +
        # HINT), NOT a raw dump.
        guard_result = await invoke_agent_and_wait(
            mgr,
            "governor",
            "try to recurse",
            parent_id=root_gov,
            timeout=5.0,
        )
        assert isinstance(guard_result, str)
        assert guard_result.startswith("Error: Spawn refused"), (
            f"guard refusal must surface readably: {guard_result!r}"
        )
        assert "Chain:" in guard_result, f"chain walk missing: {guard_result!r}"
        assert "HINT:" in guard_result, f"corrective HINT missing: {guard_result!r}"
        assert "Traceback" not in guard_result
        assert "iid-root" in guard_result, f"chain must name the parent: {guard_result!r}"

        # (2) Contrast: a NON-guard ValueError keeps the generic
        # ``Error: {e}`` form (pre-batch NO-BEHAVIOR-CHANGE contract).
        generic_result = await invoke_agent_and_wait(
            mgr,
            "nonexistent-agent-zzz",
            "hello",
            timeout=5.0,
        )
        assert generic_result == "Error: Agent not found: nonexistent-agent-zzz", (
            f"non-guard ValueError must use the generic form: {generic_result!r}"
        )
        assert "Spawn refused" not in generic_result
        assert "HINT" not in generic_result


# =============================================================================
# V6 — spawn_councilor targeting governor is blocked by the lifecycle guard
# =============================================================================


class TestV6SpawnCouncilorGovernorTarget:
    async def test_v6_spawn_councilor_governor_target_refused(self, engine, envctl):
        mgr = build_manager(engine)
        root_gov = seed_instance(engine, "iid-root-gov-0006", "governor", None)
        with tools_for(mgr, root_gov, "governor") as tools:
            councilor = tool_of(tools, "spawn_councilor")
            # Caller IS governor → identity gate passes; membership passes
            # (council category implies governor); model unrestricted. The
            # refusal can only come from the REAL lifecycle guard via
            # manager.spawn_instance, re-raised verbatim by the tool.
            with pytest.raises(ValueError) as excinfo:
                await councilor.coroutine(
                    councilor_agent_id="governor",
                    model="gpt-4o",
                    initial_message="please recurse",
                )
        err = str(excinfo.value)
        assert "Spawn refused" in err, f"lifecycle guard must refuse: {err!r}"
        assert "parent chain already contains" in err
        assert "HINT" in err and "governor" in err.lower()
        assert "iid-root" in err, f"chain must name the parent: {err!r}"


# =============================================================================
# LEGIT flows — the guard must NOT produce false positives
# =============================================================================


class TestLegitFlows:
    async def test_legit1_non_governor_convenes_council_success(
        self, engine, envctl
    ):
        """Non-governor convenes → governor council-manager child created.

        NOTE: the REAL convening agent in this repo is ``architect``
        (``agents/architect/meta.json`` carries governor in team_members and
        council in tools.allow). ``leader`` has NEITHER — a leader convene is
        refused by the membership/auth gate (a different, pre-existing
        layer), not by the recursion guard.
        """
        mgr = build_manager(engine)
        svc = mgr._lifecycle_service
        arch_iid = seed_instance(engine, "iid-arch-legit100", "architect", None)
        with real_spawn_patches(svc):
            with tools_for(mgr, arch_iid, "architect") as tools:
                convene = tool_of(tools, "convene_council")
                result = await convene.coroutine(
                    councilor_agent_id="governor", request="Plan the migration"
                )
        assert isinstance(result, dict) and result["status"] == "convened", (
            f"no false positive expected: {result!r}"
        )
        row = mgr._instance_repository.get(result["governor_instance_id"])
        assert row is not None and row.agent_id == "governor"
        assert row.parent_id == arch_iid, "council-manager child must be REAL"

    async def test_legit2_governor_child_spawns_councilors_with_count_feedback(
        self, engine, envctl
    ):
        mgr = build_manager(engine)
        svc = mgr._lifecycle_service
        arch_iid = seed_instance(engine, "iid-arch-legit200", "architect", None)
        with real_spawn_patches(svc):
            gov_child, _model = svc.spawn_instance(
                agent_id="governor", parent_id=arch_iid
            )
        with real_spawn_patches(svc):
            with tools_for(mgr, gov_child, "governor") as tools:
                councilor = tool_of(tools, "spawn_councilor")
                result = await councilor.coroutine(
                    councilor_agent_id="worker",
                    model="gpt-4o",
                    initial_message="help the governor",
                )
        assert result.startswith("Successfully spawned councilor instance"), (
            f"councilor spawn must succeed: {result!r}"
        )
        # Child-count feedback line. Production label for spawn_councilor is
        # "Councilor {n} of {limit}" (the "Child {n} of {limit}" form belongs
        # to the spawn_instance tool — daemon/tools/instance.py:1815 vs :2036).
        assert re.search(r"Councilor \d+ of \d+", result), (
            f"child-count text missing: {result!r}"
        )
        assert "Councilor 1 of 50" in result, f"count must be 1 of 50: {result!r}"
        # The councilor row is REAL and parented under the governor child.
        worker_iid = re.search(r"councilor instance: ([0-9a-f\-]+)", result).group(1)
        row = mgr._instance_repository.get(worker_iid)
        assert row is not None and row.agent_id == "worker"
        assert row.parent_id == gov_child

    async def test_legit3_sibling_governors_in_separate_trees_succeed(
        self, engine, envctl
    ):
        mgr = build_manager(engine)
        svc = mgr._lifecycle_service
        arch1 = seed_instance(engine, "iid-arch-legit301", "architect", None)
        arch2 = seed_instance(engine, "iid-arch-legit302", "architect", None)
        with real_spawn_patches(svc):
            with tools_for(mgr, arch1, "architect") as tools:
                r1 = await tool_of(tools, "convene_council").coroutine(
                    councilor_agent_id="governor", request="Tree one"
                )
            with tools_for(mgr, arch2, "architect") as tools:
                r2 = await tool_of(tools, "convene_council").coroutine(
                    councilor_agent_id="governor", request="Tree two"
                )
        assert r1["status"] == "convened" and r2["status"] == "convened", (
            f"both siblings must convene: {r1!r} / {r2!r}"
        )
        for iid in (r1["governor_instance_id"], r2["governor_instance_id"]):
            row = mgr._instance_repository.get(iid)
            assert row is not None and row.agent_id == "governor"
        assert r1["governor_instance_id"] != r2["governor_instance_id"]

    async def test_legit4_terminated_governor_does_not_poison_fresh_convene(
        self, engine, envctl
    ):
        """A TERMINATED governor child must not block the next convene from
        the non-governor root (dead subtrees don't poison the chain)."""
        mgr = build_manager(engine)
        svc = mgr._lifecycle_service
        arch_iid = seed_instance(engine, "iid-arch-legit400", "architect", None)
        with real_spawn_patches(svc):
            with tools_for(mgr, arch_iid, "architect") as tools:
                r1 = await tool_of(tools, "convene_council").coroutine(
                    councilor_agent_id="governor", request="First council"
                )
        gov1 = r1["governor_instance_id"]
        set_row_status(engine, gov1, "TERMINATED")

        # Fresh convene from the same root — must succeed.
        with real_spawn_patches(svc):
            with tools_for(mgr, arch_iid, "architect") as tools:
                r2 = await tool_of(tools, "convene_council").coroutine(
                    councilor_agent_id="governor", request="Fresh council"
                )
        assert r2["status"] == "convened", (
            f"terminated governor must not poison: {r2!r}"
        )
        gov2 = r2["governor_instance_id"]
        assert gov2 != gov1
        row = mgr._instance_repository.get(gov2)
        assert row is not None and row.agent_id == "governor"


# =============================================================================
# KILL-SWITCH — env-driven disable via the REAL config path
# =============================================================================


class TestKillSwitch:
    async def test_killswitch_env_off_spawn_attempts_succeed(self, engine, envctl):
        """``LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED=0`` disables the
        lifecycle guard: governor may spawn governor (V2/V4a/V6 analogs now
        SUCCEED with REAL rows)."""
        envctl.set(kill="0")
        mgr = build_manager(engine)  # REAL Config() reads the env
        svc = mgr._lifecycle_service
        root_gov = seed_instance(engine, "iid-root-gov-k000", "governor", None)

        with real_spawn_patches(svc):
            # (i) lifecycle: root governor spawns a governor child.
            child1, _ = svc.spawn_instance(agent_id="governor", parent_id=root_gov)
            row = mgr._instance_repository.get(child1)
            assert row is not None and row.agent_id == "governor"
            assert row.parent_id == root_gov

        # (ii) spawn_instance TOOL from the governor now succeeds.
        with real_spawn_patches(svc):
            with tools_for(mgr, root_gov, "governor") as tools:
                result = await tool_of(tools, "spawn_instance").coroutine(
                    agent_id="governor"
                )
        assert result.startswith("Successfully spawned instance"), (
            f"kill-switch must disable the guard: {result!r}"
        )

        # (iii) spawn_councilor TOOL targeting governor now succeeds.
        with real_spawn_patches(svc):
            with tools_for(mgr, root_gov, "governor") as tools:
                result = await tool_of(tools, "spawn_councilor").coroutine(
                    councilor_agent_id="governor",
                    model="gpt-4o",
                    initial_message="allowed now",
                )
        assert result.startswith("Successfully spawned councilor instance"), (
            f"councilor-to-governor must succeed: {result!r}"
        )

    async def test_killswitch_env_off_convene_proceeds(
        self, engine, envctl
    ):
        """``LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED=0`` opens the
        tool-layer convene valve: a governor caller may convene a council
        (V1 analog now PROCEEDS, no ``convene_council refused`` raise).

        Final pre-merge coupling fix: the tool-layer ``convene_council``
        refusal at ``daemon/tools/instance.py`` is gated on the same
        ``_tool_layer_guard_armed(manager)`` predicate as the lifecycle
        guard (mirrored at ``daemon/services/instance_lifecycle.py``).
        When the kill-switch is open, both layers fall through and the
        convene request reaches the REAL spawn path — the council-manager
        child is created (status ``convened``, ``governor_instance_id``
        present).
        """
        envctl.set(kill="0")
        mgr = build_manager(engine)
        svc = mgr._lifecycle_service
        root_gov = seed_instance(engine, "iid-root-gov-k001", "governor", None)

        with real_spawn_patches(svc):
            with tools_for(mgr, root_gov, "governor") as tools:
                convene = tool_of(tools, "convene_council")
                result = await convene.coroutine(
                    councilor_agent_id="worker", request="now allowed"
                )

        assert isinstance(result, dict) and result["status"] == "convened", (
            f"kill-switch must open the tool-layer valve: {result!r}"
        )
        gov_child = result["governor_instance_id"]
        row = mgr._instance_repository.get(gov_child)
        assert row is not None and row.agent_id == "governor", (
            "council-manager child must be a REAL governor row "
            "(the tool-layer scalpel no longer fires under env=0)"
        )
        assert row.parent_id == root_gov

    async def test_killswitch_k_zero_disables(self, engine, envctl):
        """``LIMITS_MAX_GOVERNOR_ANCESTORS=0`` disables the guard via the
        REAL pydantic config path (K=0 short-circuits the guard block).
        The same disable path also opens the tool-layer convene valve
        (K=0 arms neither guard layer — see
        ``daemon/tools/instance.py::_tool_layer_guard_armed``)."""
        envctl.set(k="0")
        mgr = build_manager(engine)  # REAL LimitsConfig resolves K=0 from env
        assert mgr.config.limits.max_governor_ancestors == 0
        svc = mgr._lifecycle_service
        root_gov = seed_instance(engine, "iid-root-gov-k002", "governor", None)

        with real_spawn_patches(svc):
            # governor → governor → governor all allowed when K=0.
            child1, _ = svc.spawn_instance(agent_id="governor", parent_id=root_gov)
            child2, _ = svc.spawn_instance(agent_id="governor", parent_id=child1)
        for iid, parent in ((child1, root_gov), (child2, child1)):
            row = mgr._instance_repository.get(iid)
            assert row is not None and row.agent_id == "governor"
            assert row.parent_id == parent

        # K=0 also opens the tool-layer convene valve (V1 vector).
        with real_spawn_patches(svc):
            with tools_for(mgr, root_gov, "governor") as tools:
                result = await tool_of(tools, "convene_council").coroutine(
                    councilor_agent_id="worker", request="K=0 allowed"
                )
        assert isinstance(result, dict) and result["status"] == "convened", (
            f"K=0 must open the tool-layer convene valve: {result!r}"
        )

    async def test_default_env_guard_is_on(self, engine, envctl):
        """Default (no env override) → guard ON (independent of V2)."""
        envctl.set()  # ensure both env vars unset
        mgr = build_manager(engine)
        assert mgr.config.limits.governor_recursion_guard_enabled is True
        assert mgr.config.limits.max_governor_ancestors == 1
        root_gov = seed_instance(engine, "iid-root-gov-k003", "governor", None)
        with pytest.raises(ValueError) as excinfo:
            mgr._lifecycle_service.spawn_instance(
                agent_id="governor", parent_id=root_gov
            )
        assert "Spawn refused" in str(excinfo.value)
