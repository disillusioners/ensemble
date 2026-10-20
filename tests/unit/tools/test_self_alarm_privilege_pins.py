"""Regression pins for TWO Phase-2 self-alarm corpus gaps (ari-tools P2).

These are NOT production defects — the production behaviour is correct.
They pin behaviour that the Phase-2 corpus left UNPINNED, so a future
change cannot silently break either property:

* **P1 — the D10 layer-3 (literal tool-name allow) property.**
  ``phase2-plan.md`` D10 names three independent layers and says the
  runtime ari-only gate "is **required even with** the privileged
  category, because a **literal tool-name** allow entry bypasses the
  category gate (``daemon/tools/instance.py:471-478``); the category
  alone cannot spell 'ari only'."
  The Phase-2 corpus pins the CATEGORY path (empty-allow default-deny,
  non-empty-allow-without-category) and the runtime gate in isolation,
  but never the LITERAL-NAME path — the one case the runtime gate
  actually exists to defend. This file pins both halves:
  the tool RESOLVES for a non-ari agent (the bypass is real), and the
  runtime gate REFUSES the call.

* **P2 — the reuse path echoes ``lateness_cap_seconds``.**
  ``daemon/tools/self_alarm.py`` puts ``lateness_cap_seconds`` in BOTH
  response shapes: the create branch (``:938``) and the reuse branch
  (``:1055``). The Phase-2 corpus pins the echo on the CREATE path only
  (both the ``None`` and the integer state); the reuse branch — where an
  agent re-arms an EXISTING alarm and therefore also needs to see its
  lateness exposure — was never asserted.

SAFETY FENCE: hermetic. The repo/registry doubles are staged under
``tmp_path`` (never a HOME-derived path), the scheduling service is the
REAL module with in-memory repository/registry doubles, and no daemon
boot or DB access occurs.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import daemon.registry as dr
from daemon.registry import AgentRegistry
from daemon.services import scheduling_service
from daemon.tools.instance import create_instance_tools
from tests.unit.tools.test_self_alarm_tools import (
    FakeRegistry,
    FakeRepo,
    FakeRow,
    SpyService,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TZ_NAME = "Asia/Ho_Chi_Minh"
PROJECT_ID = "proj-1"
LITERAL_TOOL_NAME = "self_alarm"


# ── P1 — the D10 layer-3 literal tool-name allow path ──────────────────────


def _stage_synthetic_agent(tmp_path: Path, agent_id: str, allow: list[str]) -> Path:
    """Stage a synthetic agent under ``tmp_path`` cloning the watcher shape."""
    real_meta = json.loads(
        (REPO_ROOT / "agents" / "watcher" / "meta.json").read_text(encoding="utf-8")
    )
    real_meta.pop("watchover", None)
    real_meta["id"] = agent_id
    real_meta["name"] = agent_id.title()
    real_meta["team_members"] = []
    real_meta["innate_skills"] = []
    real_meta["tools"] = {"allow": allow}
    agents_dir = tmp_path / "agents"
    agent_dir = agents_dir / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "meta.json").write_text(json.dumps(real_meta), encoding="utf-8")
    return agents_dir


def _manager():
    manager = MagicMock(name="InstanceManager")
    manager.config.daemon.port = 0
    manager.config.llm.allowed_models = []
    manager._secret_service = None
    manager._instance_repository = None
    manager.scheduling_service = scheduling_service
    return manager


def test_a_literal_tool_name_allow_RESOLVES_the_tool_for_a_non_ari_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bypass is REAL — this is exactly why the runtime gate exists.

    ``tools.allow: ["self_alarm"]`` names the TOOL, not the category, so
    the privileged-category strip never applies. If this test ever goes
    red because the tool stopped resolving, the D10 layer-3 rationale
    must be re-examined rather than "fixed" by assumption.
    """
    agents_dir = _stage_synthetic_agent(
        tmp_path, "syn-literal-selfalarm-allow", [LITERAL_TOOL_NAME]
    )
    registry = AgentRegistry(agents_dir)
    registry.discover()
    monkeypatch.setattr(dr, "_registry", registry)
    assert registry.exists("syn-literal-selfalarm-allow")

    tools = create_instance_tools(
        _manager(), "inst-syn", "syn-literal-selfalarm-allow"
    )
    by_name = {getattr(t, "name", "?"): t for t in tools}
    assert LITERAL_TOOL_NAME in by_name, (
        "a literal tool-name allow entry no longer resolves the self_alarm "
        "tool — the D10 layer-3 bypass premise changed"
    )


@pytest.mark.asyncio
async def test_the_runtime_gate_REFUSES_a_literal_allow_non_ari_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D10 layer 3 — the runtime gate is the ONLY thing that refuses.

    The tool is resolved (see the test above), the caller is NOT ari, so
    the call MUST be refused with the typed error and must not reach the
    engine (no create, no update, no row).
    """
    agents_dir = _stage_synthetic_agent(
        tmp_path, "syn-literal-selfalarm-call", [LITERAL_TOOL_NAME]
    )
    registry = AgentRegistry(agents_dir)
    registry.discover()
    monkeypatch.setattr(dr, "_registry", registry)

    tools = create_instance_tools(
        _manager(), "inst-syn", "syn-literal-selfalarm-call"
    )
    by_name = {getattr(t, "name", "?"): t for t in tools}
    tool = by_name[LITERAL_TOOL_NAME]

    repo = FakeRepo()
    previous = (
        scheduling_service._deps.source_repo,
        scheduling_service._deps.source_registry,
        scheduling_service._deps.project_repository,
    )
    scheduling_service.configure(
        source_repo=repo, source_registry=FakeRegistry(repo)
    )
    try:
        service = SpyService(scheduling_service, repo)
        result = await tool.coroutine(
            label="probe", when_or_every="in 45m", message_to_self="Do the thing."
        )
    finally:
        (
            scheduling_service._deps.source_repo,
            scheduling_service._deps.source_registry,
            scheduling_service._deps.project_repository,
        ) = previous

    assert isinstance(result, str), f"the call was NOT refused: {result!r}"
    assert result.startswith("ERROR: ")
    assert "ari" in result
    # No engine write, no row, no burned label.
    assert service.create_calls == []
    assert service.update_calls == []
    assert repo.rows == []


# ── P2 — the reuse path echoes lateness_cap_seconds ────────────────────────


@pytest.fixture
def tool_env(monkeypatch: pytest.MonkeyPatch):
    """The tool bound to the REAL engine (spied) + the in-memory repo."""
    monkeypatch.setenv("ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE", TZ_NAME)
    repo = FakeRepo()
    previous = (
        scheduling_service._deps.source_repo,
        scheduling_service._deps.source_registry,
        scheduling_service._deps.project_repository,
    )
    scheduling_service.configure(
        source_repo=repo, source_registry=FakeRegistry(repo)
    )
    try:
        service = SpyService(scheduling_service, repo)
        instance_repo = MagicMock()
        instance_repo.get = MagicMock(
            return_value=SimpleNamespace(project_id=PROJECT_ID)
        )
        from daemon.tools.self_alarm import create_self_alarm_tools

        tools = create_self_alarm_tools(
            service,
            source_repo=repo,
            instance_repository=instance_repo,
            project_repository=None,
            current_instance_id="inst-caller-1",
            agent_id="ari",
        )
        yield {t.name: t for t in tools}, service, repo
    finally:
        (
            scheduling_service._deps.source_repo,
            scheduling_service._deps.source_registry,
            scheduling_service._deps.project_repository,
        ) = previous


@pytest.mark.asyncio
async def test_the_REUSE_path_echoes_lateness_cap_seconds(
    tool_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-armed alarm must expose the SAME lateness exposure as a new one.

    ``daemon/tools/self_alarm.py`` puts ``lateness_cap_seconds`` in both
    response shapes (create ``:938``, reuse ``:1055``). The reuse branch
    is the one where the agent re-arms an EXISTING row and therefore also
    needs to see its exposure — it was never asserted.
    """
    tools, service, repo = tool_env
    # A stored, ari-owned, compatible row ⇒ the REUSE branch.
    repo.rows.append(FakeRow())

    result = await tools["self_alarm"].coroutine(
        label="deploy-check", when_or_every="in 45m", message_to_self="Do the thing."
    )
    assert isinstance(result, dict), result
    assert result["reused"] is True
    assert "lateness_cap_seconds" in result, (
        "the reuse response dropped lateness_cap_seconds"
    )
    assert result["lateness_cap_seconds"] is None
    assert len(service.update_calls) == 1
    assert service.create_calls == []

    monkeypatch.setenv("ENSEMBLE_SCHEDULING_ONE_SHOT_MAX_LATENESS_SECONDS", "900")
    result = await tools["self_alarm"].coroutine(
        label="deploy-check", when_or_every="in 45m", message_to_self="Do the thing."
    )
    assert isinstance(result, dict), result
    assert result["reused"] is True
    assert result["lateness_cap_seconds"] == 900
