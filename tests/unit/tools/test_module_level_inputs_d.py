"""Module-level input-model pins — cohort D (prompt_reload / prompt_assent
/ guidelines).

The three cohort-D factories declared their pydantic input model INSIDE
the factory body (``class XInput(BaseModel): ...``), so the class
statement — and the whole pydantic model-construction pass — re-ran on
EVERY factory call, i.e. on EVERY graph assembly. The schema is a pure
function of the field declarations (zero closure dependency), so the
classes now live at MODULE level and the per-assembly rebuild is gone.

What is pinned here:

* **placement** — each hoisted class has a module-level
  ``__qualname__`` (no ``<locals>``) and the module's own
  ``__module__``;
* **wiring** — each factory's ``@tool(args_schema=...)`` binds THE
  module-level class (identity, not equality);
* **shape** — the field sets are unchanged by the move;
* **MUTATION PIN** — a counter around
  ``pydantic._internal._model_construction.ModelMetaclass.__new__``
  (the ``test_schema_registry`` pattern) proves a SECOND build of each
  factory constructs ZERO pydantic model classes: reverting the hoist
  re-introduces one class per factory-local input model and turns this
  red;
* **byte-identity** — the second build's
  ``[tool.name, convert_to_openai_tool(tool)]`` rows are byte-identical
  (exact JSON key order) to the BASE fixture captured at ``3506a326d``
  BEFORE the hoist.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "tests" / "unit" / "tools" / "fixtures" / "module_level_inputs_d_base.json"

#: (fixture key, module name, factory name, hoisted class name) per
#: cohort-D factory — the same triple the brief names.
CASES: list[tuple[str, str, str, str]] = [
    (
        "prompt_reload",
        "daemon.tools.prompt_reload",
        "create_prompt_reload_tools",
        "PromptReloadInput",
    ),
    (
        "prompt_assent",
        "daemon.tools.prompt_assent",
        "create_prompt_assent_tools",
        "PromptAssentInput",
    ),
    (
        "guidelines",
        "daemon.tools.guidelines",
        "create_guidelines_tools",
        "GuidelinesReadInput",
    ),
]

#: The field sets — unchanged by the move (field order included).
EXPECTED_FIELDS: dict[str, list[str]] = {
    "prompt_reload": ["force", "compact", "compact_note"],
    "prompt_assent": [
        "action",
        "version_id",
        "token",
        "user_confirmed",
        "reject_reason",
        "limit",
        "force",
    ],
    "guidelines": ["slug"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manager() -> MagicMock:
    """An inert manager — only the probed attrs are set explicitly."""
    manager = MagicMock(name="InstanceManager")
    manager.config.llm.allowed_models = []
    manager.config.daemon.port = 0
    return manager


def _factory(module: str, name: str) -> Callable[..., list]:
    import importlib

    return getattr(importlib.import_module(module), name)


def _hoisted_class(module: str, name: str) -> type:
    import importlib

    return getattr(importlib.import_module(module), name)


def _build(module: str, factory_name: str) -> list:
    """Build the factory's tools once (the steady-state build shape)."""
    factory = _factory(module, factory_name)
    return factory(
        _manager(),
        current_instance_id="module-level-inputs-d",
        agent_id="ari",
    )


def _build_twice(module: str, factory_name: str) -> list:
    """Build the factory TWICE and return the SECOND build.

    Mirrors the fixture capture: build #1 warms the schema registry,
    build #2 is the steady state that gets dumped.
    """
    _build(module, factory_name)
    return _build(module, factory_name)


def _payload_rows(tools: list) -> list:
    from langchain_core.utils.function_calling import convert_to_openai_tool

    return [[t.name, convert_to_openai_tool(t)] for t in tools]


def _count_model_classes(monkeypatch) -> dict[str, int]:
    """Count every pydantic v2 model-CLASS construction (the
    ``test_schema_registry`` instrument)."""
    from pydantic._internal._model_construction import ModelMetaclass

    counter = {"n": 0}
    original = ModelMetaclass.__new__

    def _counting_new(mcls, name, bases, namespace, **kwargs):  # noqa: ANN001
        counter["n"] += 1
        return original(mcls, name, bases, namespace, **kwargs)

    monkeypatch.setattr(ModelMetaclass, "__new__", _counting_new)
    return counter


# ---------------------------------------------------------------------------
# 1. Placement — the classes are module level
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key,module,factory_name,class_name", CASES)
def test_hoisted_input_model_is_module_level(key, module, factory_name, class_name):
    """No ``<locals>`` qualname and the module's own ``__module__``."""
    cls = _hoisted_class(module, class_name)
    assert "<locals>" not in cls.__qualname__, (
        f"{class_name} is still declared inside a factory body "
        f"(qualname {cls.__qualname__!r}) — the per-assembly rebuild is back"
    )
    assert cls.__module__ == module, (
        f"{class_name}.__module__ is {cls.__module__!r}, expected {module!r}"
    )


@pytest.mark.parametrize("key,module,factory_name,class_name", CASES)
def test_factory_binds_the_module_level_class(key, module, factory_name, class_name):
    """The ``@tool(args_schema=...)`` reference binds THE hoisted class."""
    cls = _hoisted_class(module, class_name)
    tools = _build(module, factory_name)
    bound = [t for t in tools if getattr(t, "args_schema", None) is not None]
    schemas = [t.args_schema for t in bound]
    assert cls in schemas, (
        f"no tool in {factory_name}() binds {class_name} — "
        f"schemas present: {[type(s).__name__ for s in schemas]}"
    )
    # the binding must be by IDENTITY to the module-level class, not
    # merely structural equality with a leftover factory-local twin
    assert any(s is cls for s in schemas)


def test_hoisted_model_field_sets_unchanged():
    """The move moved NOTHING about the field declarations."""
    for key, module, _factory_name, class_name in CASES:
        cls = _hoisted_class(module, class_name)
        assert list(cls.model_fields) == EXPECTED_FIELDS[key], (
            f"{class_name} fields drifted: {list(cls.model_fields)} "
            f"!= {EXPECTED_FIELDS[key]}"
        )


# ---------------------------------------------------------------------------
# 2. MUTATION PIN — a second build constructs ZERO pydantic classes
# ---------------------------------------------------------------------------


def test_model_class_counter_has_teeth(monkeypatch):
    """Positive control: the instrument counts a real model construction."""
    from pydantic import BaseModel

    counter = _count_model_classes(monkeypatch)

    class _Probe(BaseModel):  # noqa: F841 — constructed exactly to be counted
        x: int = 1

    assert counter["n"] == 1, (
        f"the counter recorded {counter['n']} constructions for one "
        "explicit model class — the MUTATION PIN would be vacuous"
    )


def test_second_factory_build_constructs_zero_model_classes(monkeypatch):
    """Reverting the hoist re-introduces one class per factory call."""
    counter = _count_model_classes(monkeypatch)

    cold_tools: dict[str, list] = {}
    for _key, module, factory_name, _cls in CASES:
        cold_tools[module] = _build(module, factory_name)
    cold = counter["n"]

    counter["n"] = 0
    warm_tools: dict[str, list] = {}
    for _key, module, factory_name, _cls in CASES:
        warm_tools[module] = _build(module, factory_name)
    warm = counter["n"]

    assert warm == 0, (
        f"the SECOND build of the cohort-D factories still constructed "
        f"{warm} pydantic model classes (cold build: {cold}) — a "
        "factory-local input model (or another per-assembly rebuild) is back"
    )
    for module in cold_tools:
        assert len(cold_tools[module]) == len(warm_tools[module])


# ---------------------------------------------------------------------------
# 3. Byte-identity — the provider payload did not move
# ---------------------------------------------------------------------------


def test_openai_payloads_byte_identical_to_base_fixture():
    """Second-build payloads == the BASE fixture, exact JSON key order.

    The fixture was captured at base ``3506a326d`` BEFORE the hoist
    (second build of each factory, schema registry warm), so this pin
    proves the hoist moved the class declarations without moving a
    single byte of the provider-visible tool payload.
    """
    fixture_text = FIXTURE_PATH.read_text(encoding="utf-8")
    fixture = json.loads(fixture_text)

    built: dict[str, Any] = {}
    for key, module, factory_name, _cls in CASES:
        built[key] = _payload_rows(_build_twice(module, factory_name))

    built_text = json.dumps(built, indent=2) + "\n"
    assert built_text == fixture_text, (
        "the hoist changed the provider-visible payload — rebuilt bytes "
        f"differ from {FIXTURE_PATH.relative_to(REPO_ROOT)} "
        "(field sets, descriptions, required-ness, or key order moved)"
    )
    for key in fixture:
        assert [row[0] for row in built[key]] == [
            row[0] for row in fixture[key]
        ], f"tool names drifted for {key}"
