"""Behavioral pinned tests — MCP preload on every spawn lane.

Designer OD-lane fix (2026-10-06). Root cause: the agent-facing
``spawn_instance`` TOOL called the sync ``manager.spawn_instance`` facade,
which never writes ``McpService._tools_cache`` — leader-dispatched
MCP-dependent children (designer/planner) bound ZERO ``mcp_*`` tools with
zero log output, while every other creation lane routes through
``spawn_instance_with_mcp`` (preloads via ``ensure_mcp_preloaded`` first).

These pins ride the REAL ``create_instance_tools`` + a FakeManager
(stub style mirrors tests/job_queue/test_job_processor.py):

* ``test_tool_lane_preloads_mcp_for_child`` — THE fail@base → pass@fix
  discriminator (designer + planner): child UUID is pre-generated, the
  MCP cache is populated for it BEFORE the sync spawn, the preload is
  allow/CR-3 identity-bearing, and exactly one preload write happens.
* ``test_loaded_line_fires_at_construction`` — the "Loaded N MCP tools"
  log line (silent-by-default before the fix) fires on cache hit.
* ``test_warn_once_on_cache_miss_*`` — the (c) silence-killer: warns
  once, allow-gated ("mcp" in tools.allow), never on legit-empty
  (``[]`` entry = preload ran) and never without an MCP service.
* ``test_tool_lane_survives_missing_mcp_service`` — warmup failure can
  never kill a spawn.
* ``test_get_instance_cold_restore_preloads`` — no-regression: the
  cold-restore backfill still fires on a get_instance miss (revive and
  post-restart repair ride this path).

fail@base demonstration: ``git stash push -- daemon/`` → run → the tool-lane
and loaded-line tests FAIL (cache never written, line never fires) →
``git stash pop``. The AST source-contract pins (bare-site census, healthy
lanes, cache mechanics, async capability) live in
tests/unit/probe_designer_od_lane_binding.py.
"""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from daemon.tools.instance import (
    _load_mcp_tools,
    create_instance_tools,
)


# ─────────────────────────── fakes (job_processor stub style) ─────────────


def _fake_mcp_tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


class FakeMcpService:
    """Cache semantics mirror McpService: sole writer preload, silent-[]
    reads, key-existence is the preload marker."""

    def __init__(self) -> None:
        self._tools_cache: dict[str, list[Any]] = {}
        self.preload_calls: list[tuple[str, str | None, str | None]] = []

    async def preload_mcp_tools(
        self,
        instance_id: str,
        *,
        agent_id: str | None = None,
        version_tag: str | None = None,
    ) -> None:
        self.preload_calls.append((instance_id, agent_id, version_tag))
        if instance_id in self._tools_cache:  # idempotency (early-return)
            return
        self._tools_cache[instance_id] = [
            _fake_mcp_tool("mcp_opendesign_od_list_projects")
        ]

    def get_mcp_tools(self, instance_id: str) -> list[Any]:
        return self._tools_cache.get(instance_id, [])

    def is_preloaded(self, instance_id: str) -> bool:
        return instance_id in self._tools_cache


class FakeInstanceRow:
    def __init__(self, agent_id: str | None = "leader") -> None:
        self.agent_id = agent_id
        self.agent_tag = None
        self.project_id = None


class FakeInstanceRepo:
    def __init__(self, agent_id: str | None = "leader") -> None:
        self._agent_id = agent_id

    def get(self, instance_id: str) -> FakeInstanceRow:
        return FakeInstanceRow(self._agent_id)

    def count_children(self, parent_id: str) -> int:
        return 0


class FakeLifecycle:
    def _format_model_fallback_notice(self, model: Any, override: Any) -> str:
        return ""


class FakeProjectRepo:
    engine = None  # Session(None) raises → _resolve_default_version_tag → None


class FakeManager:
    def __init__(self, with_mcp: bool = True, row_agent_id: str | None = "leader") -> None:
        self._mcp_service = FakeMcpService() if with_mcp else None
        self._instance_repository = FakeInstanceRepo(row_agent_id)
        self._project_repository = FakeProjectRepo()
        self._lifecycle_service = FakeLifecycle()
        self.config = SimpleNamespace(
            llm=SimpleNamespace(allowed_models=[]),
            limits=SimpleNamespace(max_children_per_instance=50),
        )
        self.instances: dict[str, Any] = {}
        self.spawn_calls: list[dict[str, Any]] = []
        self._mock_attrs: dict[str, Any] = {}

    def __getattr__(self, name: str) -> Any:
        # Construction-only manager surface (project_store, job queue mgmt,
        # scheduling services, ...) — the tool factory merely captures these
        # in closures. Only reached for attributes NOT defined above.
        if name.startswith("__"):
            raise AttributeError(name)
        try:
            return self._mock_attrs[name]
        except KeyError:
            from unittest.mock import MagicMock

            self._mock_attrs[name] = MagicMock(name=f"FakeManager.{name}")
            return self._mock_attrs[name]

    def spawn_instance(self, *, agent_id, instance_id=None, parent_id=None,
                       project_id=None, instance_name=None, model=None,
                       version_tag=None, **kwargs):
        if instance_id is None:  # legacy: lifecycle auto-generates
            instance_id = str(uuid.uuid4())
        self.spawn_calls.append(
            dict(agent_id=agent_id, instance_id=instance_id, parent_id=parent_id,
                 project_id=project_id, instance_name=instance_name,
                 model=model, version_tag=version_tag)
        )
        self.instances[instance_id] = ("graph", "dir")
        return instance_id, None  # (instance_id, validated_model_override)

    async def ensure_mcp_preloaded(self, instance_id, *, agent_id=None,
                                   version_tag=None):
        if not self._mcp_service:
            return
        try:
            await self._mcp_service.preload_mcp_tools(
                instance_id, agent_id=agent_id, version_tag=version_tag
            )
        except Exception:  # best-effort, never kills a spawn
            pass


PARENT_ID = str(uuid.uuid4())


def _get_spawn_tool(manager: FakeManager):
    tools = create_instance_tools(manager, PARENT_ID, agent_id="leader")
    spawn_tool = next(
        (t for t in tools if getattr(t, "name", "") == "spawn_instance"), None
    )
    assert spawn_tool is not None, "spawn_instance tool not found in factory output"
    return spawn_tool


# ───────────────────────────── the discriminator ──────────────────────────


@pytest.mark.parametrize("agent_id", ["designer", "planner"])
async def test_tool_lane_preloads_mcp_for_child(agent_id: str) -> None:
    """Designer/planner spawn via the agent-TOOL lane must bind mcp_* tools.

    FAILS on pre-fix daemon/ (``git stash push -- daemon/``): no preload
    ran, cache empty, zero mcp_* bound. PASSES after the fix.
    """
    manager = FakeManager()
    spawn_tool = _get_spawn_tool(manager)
    run = getattr(spawn_tool, "coroutine", None) or spawn_tool.func

    result = await run(agent_id=agent_id)

    assert manager.spawn_calls, "sync spawn never invoked"
    call = manager.spawn_calls[-1]
    new_id = call["instance_id"]
    assert new_id is not None, "child UUID must be pre-generated for preload"
    assert call["agent_id"] == agent_id
    assert isinstance(result, str) and "ERROR" not in result[:20]
    assert new_id in result, "spawned child id must appear in the tool result"

    svc = manager._mcp_service
    assert svc.is_preloaded(new_id), "MCP cache never written for the child UUID"
    tools = svc.get_mcp_tools(new_id)
    names = [getattr(t, "name", "") for t in tools]
    assert names, "zero MCP tools bound for the child"
    assert all(n.startswith("mcp_") for n in names)

    # exactly one preload write, carrying the CR-3 identity
    assert len(svc.preload_calls) == 1
    assert svc.preload_calls[0][0] == new_id
    assert svc.preload_calls[0][1] == agent_id


def test_loaded_line_fires_at_construction(caplog: pytest.LogCaptureFixture) -> None:
    """The 'Loaded N MCP tools' line must fire on a populated cache — its
    total absence was the only live symptom before the fix."""
    manager = FakeManager()
    child = str(uuid.uuid4())
    manager._mcp_service._tools_cache[child] = [_fake_mcp_tool("mcp_a_b")]
    with caplog.at_level(logging.INFO, logger="daemon.tools.instance"):
        create_instance_tools(manager, child, agent_id="leader")
    assert any(
        f"Loaded 1 MCP tools for instance {child[:8]}" in r.message
        for r in caplog.records
    ), "expected the 'Loaded 1 MCP tools' log line at tool construction"


# ───────────────────────── (c) warn-once cache-miss guard ─────────────────


def _miss(caplog: pytest.LogCaptureFixture) -> int:
    return sum(1 for r in caplog.records if "MCP preload cache miss" in r.message)


def test_warn_once_on_cache_miss_when_allow_has_mcp(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = FakeManager(row_agent_id="designer")  # designer allow has "mcp"
    child = str(uuid.uuid4())
    with caplog.at_level(logging.WARNING, logger="daemon.tools.instance"):
        first = _load_mcp_tools(manager, child)
        second = _load_mcp_tools(manager, child)
    assert first == [] and second == []
    assert _miss(caplog) == 1, "must warn exactly once per instance"


def test_no_warn_on_legit_empty_cache(caplog: pytest.LogCaptureFixture) -> None:
    """A [] ENTRY means preload RAN (e.g. zero active servers) — silence."""
    manager = FakeManager(row_agent_id="designer")
    child = str(uuid.uuid4())
    manager._mcp_service._tools_cache[child] = []
    with caplog.at_level(logging.WARNING, logger="daemon.tools.instance"):
        assert _load_mcp_tools(manager, child) == []
    assert _miss(caplog) == 0


def test_no_warn_when_allow_lacks_mcp(caplog: pytest.LogCaptureFixture) -> None:
    manager = FakeManager(row_agent_id="maintenancer")  # allow has NO "mcp"
    child = str(uuid.uuid4())
    with caplog.at_level(logging.WARNING, logger="daemon.tools.instance"):
        assert _load_mcp_tools(manager, child) == []
    assert _miss(caplog) == 0


def test_no_warn_without_mcp_service(caplog: pytest.LogCaptureFixture) -> None:
    manager = FakeManager(with_mcp=False)
    child = str(uuid.uuid4())
    with caplog.at_level(logging.WARNING, logger="daemon.tools.instance"):
        assert _load_mcp_tools(manager, child) == []
    assert _miss(caplog) == 0


def test_warned_set_is_marked_even_when_silent() -> None:
    """Mark-before-warn: the once-guard must short-circuit future dispatches
    regardless of the allow outcome (bounded work per instance)."""
    # Fix-only symbol — import lazily so this module still imports (and the
    # discriminator tests fail with real assertions) on pre-fix daemon/.
    from daemon.tools.instance import _MCP_PRELOAD_MISS_WARNED

    manager = FakeManager(row_agent_id=None)  # unresolvable identity
    child = str(uuid.uuid4())
    assert child not in _MCP_PRELOAD_MISS_WARNED
    _load_mcp_tools(manager, child)
    assert child in _MCP_PRELOAD_MISS_WARNED


# ─────────────────────── warmup failure never kills a spawn ───────────────


async def test_tool_lane_survives_missing_mcp_service() -> None:
    manager = FakeManager(with_mcp=False)
    spawn_tool = _get_spawn_tool(manager)
    run = getattr(spawn_tool, "coroutine", None) or spawn_tool.func
    result = await run(agent_id="designer")
    assert not result.startswith("ERROR"), f"spawn must not fail: {result}"
    assert manager.spawn_calls, "spawn completed without MCP service"


# ───────────────── cold-restore backfill (revive / restart repair) ────────


async def test_get_instance_cold_restore_preloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-regression: on a get_instance miss (revive / post-restart), the
    ensure_mcp_preloaded backfill must fire BEFORE restore."""
    from daemon.services.instance_lifecycle import InstanceLifecycleService

    manager = FakeManager()
    lifecycle = InstanceLifecycleService(manager, cancellation_service=None)
    missing = str(uuid.uuid4())

    async def fake_restore(instance_id: str, meta: Any) -> str:
        # by restore time the preload must ALREADY have happened
        assert manager._mcp_service.is_preloaded(instance_id), (
            "cold-restore must preload BEFORE restoring"
        )
        return "restored-graph"

    monkeypatch.setattr(lifecycle, "_restore_instance", fake_restore)
    graph = await lifecycle.get_instance(missing)
    assert graph == "restored-graph"
    assert manager._mcp_service.is_preloaded(missing)
