"""The factory-local input models of the memoir/room/metrics/nest surface
are MODULE LEVEL (c2r-hoist-c).

WHY: a ``class`` statement inside a tool factory body executes on EVERY
factory call — one fresh pydantic model class per graph assembly, per tool
input model. The schema registry (``daemon/tools/_schema_registry.py``)
memoises the *derived* schema, but it cannot prevent the per-assembly class
construction: the class statement runs before the registry is ever
consulted. Moving the class to module level builds it ONCE at import, and
the registry then serves every assembly from cache.

What is pinned here:

* **module level** — every hoisted class carries a factory-free qualname
  and the module it was hoisted INTO (no ``<locals>``, no ``<factory>``
  prefix in the qualname);
* **the mutation pin** — a SECOND build of each factory constructs ZERO
  pydantic model classes (the per-assembly rebuild is gone), with a
  deliberately factory-local CONTROL proving the counter actually bites;
* **byte-identity** — the OpenAI payload of the SECOND build is
  byte-identical (``json.dumps`` equality, EXACT key order, no
  ``sort_keys``) to the pre-hoist BASE fixture captured before the move
  (``fixtures/module_level_inputs_c_base.json``).

The invariant that matters: agent-visible tool schemas did not move.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "module_level_inputs_c_base.json"
)

#: The hoisted surface: module -> the classes hoisted out of its factory.
HOISTED: dict[str, dict[str, list[str]]] = {
    "daemon.tools.memoir": {
        "factory": "create_memoir_tools",
        "classes": [
            "MemoirWriteInput",
            "MemoirPinInput",
            "MemoirUnpinInput",
            "MemoirRecallInput",
            "MemoirTimelineInput",
            "MemoirAboutInput",
            "MemoirReviewInput",
            "MemoirPersonaRatifyInput",
        ],
    },
    "daemon.tools.room": {
        "factory": "create_room_tools",
        "classes": [
            "RoomCreateInput",
            "RoomPostInput",
            "RoomReadInput",
            "RoomTaskDecisionInput",
            "RoomCloseInput",
        ],
    },
    "daemon.tools.metrics_read": {
        "factory": "create_metrics_read_tools",
        "classes": [
            "MetricsFamiliesInput",
            "MetricsSnapshotInput",
            "MetricsHistoryInput",
        ],
    },
    "daemon.tools.nest_report_read": {
        "factory": "create_nest_report_read_tools",
        "classes": [
            "AnomalyClustersInput",
            "AnomalyReportDetailInput",
        ],
    },
}

#: The fixture keys, in the order the factories were captured.
FIXTURE_KEYS = {
    "daemon.tools.memoir": "memoir",
    "daemon.tools.room": "room",
    "daemon.tools.metrics_read": "metrics",
    "daemon.tools.nest_report_read": "nest_report_read",
}

#: The tool counts the surface shipped before the hoist — the hoist must
#: not have dropped or duplicated a tool.
TOOL_COUNTS = {
    "daemon.tools.memoir": 8,
    "daemon.tools.room": 5,
    "daemon.tools.metrics_read": 3,
    "daemon.tools.nest_report_read": 2,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager() -> MagicMock:
    manager = MagicMock(name="InstanceManager")
    manager.config.llm.allowed_models = []
    manager.config.daemon.port = 0
    return manager


def _count_model_classes(monkeypatch) -> dict[str, int]:
    """Count every pydantic v2 model-CLASS construction in the process."""
    from pydantic._internal._model_construction import ModelMetaclass

    counter = {"n": 0}
    original = ModelMetaclass.__new__

    def _counting_new(mcls, name, bases, namespace, **kwargs):  # noqa: ANN001
        counter["n"] += 1
        return original(mcls, name, bases, namespace, **kwargs)

    monkeypatch.setattr(ModelMetaclass, "__new__", _counting_new)
    return counter


def _build(module_name: str, build: int = 2) -> list:
    """Build the module's factory ``build`` times, return the LAST build."""
    import importlib

    factory = getattr(importlib.import_module(module_name), HOISTED[module_name]["factory"])
    tools: list = []
    for _ in range(build):
        tools = factory(
            _make_manager(), current_instance_id="hoist-c-probe", agent_id="ari"
        )
    return tools


def _payloads(tools: list) -> list[list]:
    from langchain_core.utils.function_calling import convert_to_openai_tool

    return [[t.name, convert_to_openai_tool(t)] for t in tools]


def _fixture() -> dict[str, Any]:
    with open(FIXTURE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# 1. Module level — no ``<locals>`` in any hoisted qualname
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", sorted(HOISTED))
def test_every_hoisted_class_is_module_level(module_name: str) -> None:
    import importlib

    module = importlib.import_module(module_name)
    for cls_name in HOISTED[module_name]["classes"]:
        cls = getattr(module, cls_name)
        assert "<locals>" not in cls.__qualname__, (
            f"{module_name}.{cls_name} is still factory-local "
            f"(qualname {cls.__qualname__!r}) — the per-assembly rebuild is back"
        )
        assert cls.__module__ == module_name, (
            f"{module_name}.{cls_name} carries module {cls.__module__!r}"
        )


@pytest.mark.parametrize("module_name", sorted(HOISTED))
def test_every_tool_binds_the_module_level_schema(module_name: str) -> None:
    """The factory must USE the module-level model, not merely host it.

    A factory-local class shadowing the module-level name (the pre-hoist
    shape) would leave the module attribute clean while the tools keep
    rebuilding a ``<locals>`` schema — this pin closes that hole by
    asserting IDENTITY between each tool's ``args_schema`` and the
    module-level class of the same name.
    """
    import importlib

    module = importlib.import_module(module_name)
    tools = _build(module_name, build=1)
    assert len(tools) == TOOL_COUNTS[module_name]
    offenders: list[str] = []
    for tool_obj in tools:
        schema = getattr(tool_obj, "args_schema", None)
        if schema is None:
            offenders.append(f"{getattr(tool_obj, 'name', '?')}: no args_schema")
            continue
        if "<locals>" in schema.__qualname__:
            offenders.append(f"{tool_obj.name}: {schema.__qualname__!r}")
            continue
        if schema.__module__ != module_name:
            offenders.append(f"{tool_obj.name}: module {schema.__module__!r}")
            continue
        if schema is not getattr(module, schema.__name__):
            offenders.append(
                f"{tool_obj.name}: args_schema {schema.__name__} is NOT the "
                "module-level class of the same name"
            )
    assert not offenders, (
        f"{module_name}: {len(offenders)} tool(s) do not bind the "
        f"module-level input model: {offenders}"
    )


# ---------------------------------------------------------------------------
# 2. The mutation pin — a warm build constructs ZERO pydantic classes
# ---------------------------------------------------------------------------


def test_the_counter_bites_on_a_factory_local_control(monkeypatch) -> None:
    """Non-vacuity guard: the counter sees a class statement inside a body.

    Without this leg a silently-broken counter would let the ZERO pins
    below pass for the wrong reason.
    """
    from pydantic import BaseModel

    counter = _count_model_classes(monkeypatch)

    def _control() -> type:
        class _FactoryLocalProbe(BaseModel):  # noqa: F841 — the point
            value: int = 0

        return _FactoryLocalProbe

    before = counter["n"]
    _control()
    assert counter["n"] > before, (
        "the ModelMetaclass counter did not observe a factory-local class "
        "statement — the ZERO pins below are measuring nothing"
    )


@pytest.mark.parametrize("module_name", sorted(HOISTED))
def test_the_warm_build_constructs_zero_pydantic_classes(
    monkeypatch, module_name: str
) -> None:
    """Build each factory TWICE; the SECOND build constructs ZERO classes."""
    counter = _count_model_classes(monkeypatch)

    _build(module_name, build=1)
    counter["n"] = 0
    tools = _build(module_name, build=1)

    assert counter["n"] == 0, (
        f"{module_name}: the warm factory call constructed "
        f"{counter['n']} pydantic model class(es) — an input schema is "
        "still being rebuilt per assembly"
    )
    assert len(tools) == TOOL_COUNTS[module_name]


# ---------------------------------------------------------------------------
# 3. Byte-identity — the warm payloads match the pre-hoist BASE fixture
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", sorted(HOISTED))
def test_warm_payloads_are_byte_identical_to_the_base_fixture(
    module_name: str,
) -> None:
    """The SECOND build's payloads == the fixture, key order included.

    ``json.dumps`` equality WITHOUT ``sort_keys`` is the point: dict key
    ORDER is part of the wire payload the provider sees, so a reordered
    payload is a drift even when the content is equal.
    """
    fixture = _fixture()
    key = FIXTURE_KEYS[module_name]
    expected = fixture[key]
    assert expected, f"the fixture entry {key!r} is empty — the fixture is broken"

    tools = _build(module_name, build=2)
    assert [name for name, _payload in expected] == [
        getattr(t, "name", "") for t in tools
    ], f"{module_name}: the tool names or their order moved"

    actual = _payloads(tools)
    assert json.dumps(actual) == json.dumps(expected), (
        f"{module_name}: the hoist moved the agent-visible schema — "
        "the warm OpenAI payload no longer matches the pre-hoist BASE "
        "fixture (json.dumps equality with exact key order)"
    )


def test_the_fixture_covers_the_whole_hoisted_surface() -> None:
    """The BASE fixture carries every hoisted factory, nothing missing."""
    fixture = _fixture()
    assert set(fixture) == set(FIXTURE_KEYS.values()), sorted(fixture)
    for module_name, key in FIXTURE_KEYS.items():
        assert len(fixture[key]) == TOOL_COUNTS[module_name], (
            f"{key}: fixture holds {len(fixture[key])} tools, "
            f"expected {TOOL_COUNTS[module_name]}"
        )
