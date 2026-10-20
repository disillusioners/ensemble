"""Regression pin — the literal-tool-name allow path has ZERO side effects.

WHY THIS FILE EXISTS (ari-tools Phase 2, D10 layer 3)
-----------------------------------------------------
``tests/unit/tools/test_self_alarm_privilege_pins.py`` (commit bfff96933)
pins two Phase-2 corpus gaps. Its GAP-1 node drives the LITERAL-TOOL-NAME
allow entry through the REAL tool-resolution path and asserts the runtime
ari-only gate refuses the call with "no create, no update, no row".

Three of its side-effect assertions cannot actually fail, because the tool
it builds is wired so the engine is UNREACHABLE:

* the ``SpyService`` is constructed but never passed to the tool — the tool
  binds ``manager.scheduling_service`` (the real module), so
  ``service.create_calls`` / ``service.update_calls`` observe nothing;
* ``manager._instance_repository`` is ``None``, so the tool refuses on
  "cannot resolve project_id" BEFORE any engine call;
* ``manager._source_repository`` is never set, so the tool's read-only label
  probe sees an auto-created ``MagicMock`` and every candidate label looks
  taken.

Consequence: with the runtime ari-gate removed, that node still refuses the
call — it just refuses with a DIFFERENT error — so its zero-side-effect
assertions stay green. The property it claims to pin is NOT pinned there.

This file pins the SAME property with the tool wired so that the engine IS
reachable: the SpyService IS the tool's service, the source repository IS
the in-memory repo, and the caller's project_id resolves. Then, if the
runtime gate ever stops refusing, the create call and the row write
actually happen and the assertions go red.

The second test is a NEGATIVE CONTROL: it neutralises the gate in memory
(monkeypatch — restored automatically) and asserts the engine IS reached.
It is what keeps the first test honest: if the wiring ever regresses, the
control fails loudly instead of the pin silently degrading to a tautology.

SAFETY FENCE: hermetic. The agent doubles are staged under ``tmp_path``
(never a HOME-derived path), the scheduling service is the REAL module with
in-memory repository/registry doubles, and no daemon boot or DB access
occurs.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import daemon.registry as dr
import daemon.tools.self_alarm as sa
from daemon.registry import AgentRegistry
from daemon.services import scheduling_service
from daemon.tools.instance import create_instance_tools
from tests.unit.tools.test_self_alarm_tools import (
    FakeRegistry,
    FakeRepo,
    SpyService,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TZ_NAME = "Asia/Ho_Chi_Minh"
PROJECT_ID = "proj-1"
LITERAL_TOOL_NAME = "self_alarm"
NON_ARI_AGENT = "syn-literal-nonari"
CALLER_INSTANCE = "inst-syn-literal"


def _stage_agents(tmp_path: Path, allow: list[str]) -> Path:
    """Stage a non-ari agent with a LITERAL tool-name allow entry.

    The real ``agents/ari/meta.json`` is staged alongside it because the
    engine validates the alarm's TARGET agent ("ari") against the registry
    before it writes a row — without it the create would be rejected for the
    wrong reason and the side-effect assertions would be vacuous again.
    """
    agents_dir = tmp_path / "agents"

    non_ari_meta = json.loads(
        (REPO_ROOT / "agents" / "watcher" / "meta.json").read_text(encoding="utf-8")
    )
    non_ari_meta.pop("watchover", None)
    non_ari_meta["id"] = NON_ARI_AGENT
    non_ari_meta["name"] = NON_ARI_AGENT.title()
    non_ari_meta["team_members"] = []
    non_ari_meta["innate_skills"] = []
    non_ari_meta["tools"] = {"allow": list(allow)}
    non_ari_dir = agents_dir / NON_ARI_AGENT
    non_ari_dir.mkdir(parents=True, exist_ok=True)
    (non_ari_dir / "meta.json").write_text(json.dumps(non_ari_meta), encoding="utf-8")

    ari_meta = json.loads(
        (REPO_ROOT / "agents" / "ari" / "meta.json").read_text(encoding="utf-8")
    )
    ari_dir = agents_dir / "ari"
    ari_dir.mkdir(parents=True, exist_ok=True)
    (ari_dir / "meta.json").write_text(json.dumps(ari_meta), encoding="utf-8")

    return agents_dir


def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Stage the registry, the repo and the tool — engine-reachable wiring.

    The tool is built through the REAL ``create_instance_tools`` factory so
    the LITERAL-TOOL-NAME allow entry is expanded by the real
    ``resolve_tool_filter`` (the privileged-category strip does NOT apply to
    a literal tool name — that bypass is what the runtime gate defends).
    """
    agents_dir = _stage_agents(tmp_path, [LITERAL_TOOL_NAME])
    registry = AgentRegistry(agents_dir)
    registry.discover()
    monkeypatch.setattr(dr, "_registry", registry)

    repo = FakeRepo()
    previous = (
        scheduling_service._deps.source_repo,
        scheduling_service._deps.source_registry,
        scheduling_service._deps.project_repository,
    )
    scheduling_service.configure(
        source_repo=repo, source_registry=FakeRegistry(repo)
    )

    spy = SpyService(scheduling_service, repo)

    manager = MagicMock(name="InstanceManager")
    manager.config.daemon.port = 0
    manager.config.llm.allowed_models = []
    manager._secret_service = None
    # The three wirings the sibling pin file leaves unset — WITHOUT them the
    # tool never reaches the engine and the side-effect assertions are
    # vacuous.
    manager._source_repository = repo
    instance_repository = MagicMock()
    instance_repository.get = MagicMock(
        return_value=SimpleNamespace(project_id=PROJECT_ID)
    )
    manager._instance_repository = instance_repository
    # The SPY is the tool's service, so the spy observes every engine call.
    manager.scheduling_service = spy

    tools = create_instance_tools(manager, CALLER_INSTANCE, NON_ARI_AGENT)
    by_name = {getattr(t, "name", "?"): t for t in tools}

    def restore() -> None:
        (
            scheduling_service._deps.source_repo,
            scheduling_service._deps.source_registry,
            scheduling_service._deps.project_repository,
        ) = previous

    return by_name, spy, repo, restore


@pytest.fixture
def wired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE", TZ_NAME)
    by_name, spy, repo, restore = _build(tmp_path, monkeypatch)
    try:
        yield by_name, spy, repo
    finally:
        restore()


@pytest.mark.asyncio
async def test_the_literal_allow_tool_is_refused_with_NO_engine_call_and_NO_row(
    wired,
) -> None:
    """A non-ari agent that named the tool literally gets ZERO side effects.

    The tool RESOLVES for this agent (the literal tool-name allow entry
    bypasses the privileged-category strip), the runtime ari-only gate
    refuses the call, and NOTHING is written: no create call, no update
    call, no schedule row.
    """
    by_name, spy, repo = wired
    tool = by_name[LITERAL_TOOL_NAME]

    result = await tool.coroutine(
        label="probe", when_or_every="in 45m", message_to_self="Do the thing."
    )

    # The gate refused (a string envelope, not the success dict).
    assert isinstance(result, str), f"the call was NOT refused: {result!r}"
    assert result.startswith("ERROR: ")
    # The refusal came from the GATE, not from a later check: the gate's
    # message names the authorized agent and the caller.
    assert "ari" in result
    assert "only" in result
    assert NON_ARI_AGENT in result
    assert "cannot resolve project_id" not in result, (
        "the refusal came from the project resolve, not from the ari-only gate"
    )

    # ZERO side effects.
    assert spy.create_calls == [], f"the engine create was called: {spy.create_calls}"
    assert spy.update_calls == [], f"the engine update was called: {spy.update_calls}"
    assert repo.rows == [], f"a schedule row was written: {repo.rows}"


@pytest.mark.asyncio
async def test_the_wiring_reaches_the_engine_so_the_pin_above_can_go_red(
    wired, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NEGATIVE CONTROL — the side-effect assertions above are load-bearing.

    Neutralising the runtime gate in memory (monkeypatch — restored
    automatically) must let the call reach the engine. If this test ever
    fails, the wiring above regressed and the pin above has silently
    become a tautology again.
    """
    by_name, spy, repo = wired
    tool = by_name[LITERAL_TOOL_NAME]

    monkeypatch.setattr(sa, "SELF_ALARM_AUTHORIZED_AGENT", NON_ARI_AGENT)

    result = await tool.coroutine(
        label="probe", when_or_every="in 45m", message_to_self="Do the thing."
    )

    assert not (isinstance(result, str) and result.startswith("ERROR: ")), (
        f"the engine call was still refused: {result!r}"
    )
    assert len(spy.create_calls) == 1, (
        f"the engine create was not reached — the wiring is not engine-reachable: "
        f"{spy.create_calls}"
    )
    assert len(repo.rows) == 1, (
        f"no schedule row was written — the wiring cannot observe a row write: "
        f"{repo.rows}"
    )
