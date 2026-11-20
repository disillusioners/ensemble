"""Unit pins for the module-level hoist of instance-surface input models.

Hoist commit: factory-local pydantic input models moved to module level in
``daemon/tools/{instance_ops,instance_read,wedge_capture,notify_user,self_alarm}.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pydantic._internal._model_construction import ModelMetaclass

from daemon.tools import instance_ops, instance_read, notify_user, self_alarm, wedge_capture
from daemon.tools.instance_ops import (
    InstanceAnswerInput,
    InstanceDismissInput,
    InstanceMarkInput,
    InstanceNudgeInput,
    InstanceReclaimInput,
    InstanceTerminateInput,
)
from daemon.tools.instance_read import (
    FleetCapacitySummaryInput,
    InstanceFleetSummaryInput,
    InstanceGetInput,
    InstanceHealthInput,
    InstanceListInput,
)
from daemon.tools.notify_user import NotifyUserInput
from daemon.tools.self_alarm import SelfAlarmInput
from daemon.tools.wedge_capture import WedgeCaptureGetInput, WedgeCaptureListInput

FIXTURE = Path(__file__).parent / "fixtures" / "module_level_inputs_b_base.json"


# (a) module-level qualname + module pins
@pytest.mark.parametrize(
    "cls,mod",
    [
        (InstanceTerminateInput, "daemon.tools.instance_ops"),
        (InstanceReclaimInput, "daemon.tools.instance_ops"),
        (InstanceNudgeInput, "daemon.tools.instance_ops"),
        (InstanceMarkInput, "daemon.tools.instance_ops"),
        (InstanceAnswerInput, "daemon.tools.instance_ops"),
        (InstanceDismissInput, "daemon.tools.instance_ops"),
        (InstanceListInput, "daemon.tools.instance_read"),
        (InstanceGetInput, "daemon.tools.instance_read"),
        (InstanceHealthInput, "daemon.tools.instance_read"),
        (FleetCapacitySummaryInput, "daemon.tools.instance_read"),
        (InstanceFleetSummaryInput, "daemon.tools.instance_read"),
        (WedgeCaptureListInput, "daemon.tools.wedge_capture"),
        (WedgeCaptureGetInput, "daemon.tools.wedge_capture"),
        (NotifyUserInput, "daemon.tools.notify_user"),
        (SelfAlarmInput, "daemon.tools.self_alarm"),
    ],
)
def test_input_model_is_module_level(cls: Any, mod: str) -> None:
    assert "<locals>" not in cls.__qualname__
    assert cls.__module__ == mod


def _make_manager() -> MagicMock:
    manager = MagicMock(name="InstanceManager")
    manager.config.llm.allowed_models = []
    manager.config.daemon.port = 0
    return manager


def _build_all(manager: MagicMock, service: MagicMock) -> list:
    source_repo = MagicMock(name="source_repo")
    instance_repository = MagicMock(name="instance_repository")
    project_repository = MagicMock(name="project_repository")
    tools: list = []
    tools += instance_ops.create_instance_ops_tools(
        manager, current_instance_id="inst-1", agent_id="agent-1"
    )
    tools += instance_read.create_instance_read_tools(
        manager, current_instance_id="inst-1", agent_id="agent-1"
    )
    tools += wedge_capture.create_wedge_capture_tools(
        manager, current_instance_id="inst-1", agent_id="agent-1"
    )
    tools += notify_user.create_notify_user_tools(
        manager, current_instance_id="inst-1", agent_id="agent-1"
    )
    tools += self_alarm.create_self_alarm_tools(
        service,
        source_repo=source_repo,
        instance_repository=instance_repository,
        project_repository=project_repository,
        current_instance_id="inst-1",
        agent_id="agent-1",
    )
    return tools


# (b) mutation pin: second build constructs zero pydantic classes
def test_second_build_constructs_zero_pydantic_classes() -> None:
    manager = _make_manager()
    service = MagicMock(name="scheduling_service")

    counter = {"n": 0}
    original = ModelMetaclass.__new__

    def _counting(mcls, name, bases, namespace, **kwargs):  # noqa: ANN001
        counter["n"] += 1
        return original(mcls, name, bases, namespace, **kwargs)

    with patch.object(ModelMetaclass, "__new__", _counting):
        _build_all(manager, service)
        cold = counter["n"]
        counter["n"] = 0
        _build_all(manager, service)
        warm = counter["n"]

    assert warm == 0, (
        f"the second build still constructed {warm} pydantic model classes "
        "(factory-local input models not hoisted)"
    )


# (c) byte-identity against base fixture
def test_second_build_payloads_match_base_fixture() -> None:
    from langchain_core.utils.function_calling import convert_to_openai_tool

    manager = _make_manager()
    service = MagicMock(name="scheduling_service")

    # first build (warm the registry / caches)
    _build_all(manager, service)
    # second build is the measured one
    tools = _build_all(manager, service)

    actual = [[t.name, convert_to_openai_tool(t)] for t in tools]
    fixture = json.loads(FIXTURE.read_text())

    assert json.dumps(actual) == json.dumps(fixture), (
        "second-build OpenAI payloads drifted from the base fixture"
    )
