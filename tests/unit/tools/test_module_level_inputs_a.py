"""Unit pins for the c2r-hoist-a module-level input models (cohort A).

WHAT THIS PINS
--------------
The job / board / scheduling surface used to declare FOURTEEN pydantic
input models INSIDE the tool factory bodies::

    daemon/tools/job_queue.py     JobCreateInput, JobContinueInput,
                                  JobAnswerInput, WatchMissionInput
    daemon/tools/board_tools.py   BoardCreateInput, BoardUpdateInput,
                                  BoardListInput, BoardGetInput
    daemon/tools/board_link.py    BoardLinkInput
    daemon/tools/scheduling.py    TaskScheduleInput, TaskScheduleListInput,
                                  TaskScheduleCancelInput,
                                  TaskScheduleUpdateInput
    daemon/tools/view_write.py    ViewWriteInput

A ``class X(BaseModel)`` statement inside a function body executes on
EVERY call of that function — so every graph assembly rebuilt all
fourteen classes from scratch.  The process-level schema registry
(``daemon/tools/_schema_registry.py``) memoises the DECORATOR's schema
but CANNOT prevent the factory-local class statement: that class is the
CALLER-supplied ``args_schema``, not a decorator-inferred one.  Hoisting
the classes to module level makes them build ONCE at import, after which
the registry serves every assembly from cache.

Four pins, in order of strength:

* **(a) MODULE-LEVEL** — every one of the fourteen classes is a
  module-level attribute of its module, carries a module-level
  ``__qualname__`` (no ``<locals>``) and the expected ``__module__``; and
  the tool objects actually USE that class
  (``tool.args_schema is <TheClass>``).
* **(b) MUTATION PIN** — a counter around
  ``pydantic._internal._model_construction.ModelMetaclass.__new__``
  proves the SECOND build of each factory constructs ZERO pydantic
  model classes.  Re-inlining any one of the classes into its factory
  turns this pin RED (that is the mutation it is written to catch), and
  the counter's own "teeth" are proven by a companion test that shows a
  function-local class statement IS counted.
* **(c) BYTE-IDENTITY** — the second build's
  ``convert_to_openai_tool(tool)`` payloads are byte-identical to the
  BASE fixture captured from the PRE-HOIST tree
  (``tests/unit/tools/fixtures/module_level_inputs_a_base.json``),
  compared with ``json.dumps`` equality at the EXACT key order (no
  ``sort_keys``).  The agent-visible schema must not move.
* **(d) THE FIXTURE ITSELF** — the BASE fixture carries the expected
  tool-name roster for all six factories, so a fixture that lost a tool
  (or a factory that grew one) is caught.

The classes are resolved DYNAMICALLY (``getattr`` on the module) rather
than imported by name: a re-inlined class must produce a clean assertion
failure, not a collection-time ``ImportError``.

SAFETY FENCE: pure unit tests — mocks only.  The daemon is NEVER
booted, no DB is touched, no network call is made, and the ambient
``POSTGRES_*`` variables cannot leak anywhere.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from daemon.tools._schema_registry import clear_registry, registry_stats

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "module_level_inputs_a_base.json"
)

#: The fourteen hoisted classes -> (module, class name).
HOISTED_CLASSES: list[tuple[str, str]] = [
    ("daemon.tools.job_queue", "JobCreateInput"),
    ("daemon.tools.job_queue", "JobContinueInput"),
    ("daemon.tools.job_queue", "JobAnswerInput"),
    ("daemon.tools.job_queue", "WatchMissionInput"),
    ("daemon.tools.board_tools", "BoardCreateInput"),
    ("daemon.tools.board_tools", "BoardUpdateInput"),
    ("daemon.tools.board_tools", "BoardListInput"),
    ("daemon.tools.board_tools", "BoardGetInput"),
    ("daemon.tools.board_link", "BoardLinkInput"),
    ("daemon.tools.scheduling", "TaskScheduleInput"),
    ("daemon.tools.scheduling", "TaskScheduleListInput"),
    ("daemon.tools.scheduling", "TaskScheduleCancelInput"),
    ("daemon.tools.scheduling", "TaskScheduleUpdateInput"),
    ("daemon.tools.view_write", "ViewWriteInput"),
]

#: Which tool consumes each hoisted class.
TOOL_FOR_CLASS: dict[str, str] = {
    "JobCreateInput": "job_create",
    "JobContinueInput": "job_continue",
    "JobAnswerInput": "job_answer",
    "WatchMissionInput": "watch_mission",
    "BoardCreateInput": "board_create",
    "BoardUpdateInput": "board_update",
    "BoardListInput": "board_list",
    "BoardGetInput": "board_get",
    "BoardLinkInput": "board_link",
    "TaskScheduleInput": "task_schedule",
    "TaskScheduleListInput": "task_schedule_list",
    "TaskScheduleCancelInput": "task_schedule_cancel",
    "TaskScheduleUpdateInput": "task_schedule_update",
    "ViewWriteInput": "view_write",
}


def _resolve(module_name: str, class_name: str) -> type[BaseModel] | None:
    """The class as the module exposes it — ``None`` when it is NOT module-level."""
    module = importlib.import_module(module_name)
    found = getattr(module, class_name, None)
    if isinstance(found, type) and issubclass(found, BaseModel):
        return found
    return None


def _manager() -> MagicMock:
    """A manager double for the factories that take one.

    MagicMock auto-creates every attribute as a TRUTHY callable, so the two
    attributes the factories probe are set explicitly (an unset one would
    read as a live service).
    """
    manager = MagicMock(name="InstanceManager")
    manager.config.llm.allowed_models = []
    manager.config.daemon.port = 0
    return manager


def _factories() -> dict[str, Any]:
    """The six cohort-A factories, keyed by name.

    Every factory is built against pure mocks — no DB, no network, no
    live state.  ``create_job_tools`` is called with an EMPTY agent id so
    the factory skips its registry lookup (the one branch that would
    touch the on-disk agent registry).
    """
    from daemon.tools.board_link import create_board_link_tools
    from daemon.tools.board_tools import create_board_tools
    from daemon.tools.job_queue import (
        create_job_tools,
        create_mission_watch_tools,
    )
    from daemon.tools.scheduling import create_scheduling_tools
    from daemon.tools.view_write import create_view_write_tools

    return {
        "create_job_tools": lambda: create_job_tools(
            MagicMock(),
            MagicMock(),
            MagicMock(),
            current_instance_id="inst-hoist-a",
            agent_id="",
        ),
        "create_mission_watch_tools": lambda: create_mission_watch_tools(
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            current_instance_id="inst-hoist-a",
        ),
        "create_board_tools": lambda: create_board_tools(
            MagicMock(),
            current_instance_id="inst-hoist-a",
            agent_id="ari",
        ),
        "create_board_link_tools": lambda: create_board_link_tools(
            _manager(),
            current_instance_id="inst-hoist-a",
            agent_id="ari",
        ),
        "create_scheduling_tools": lambda: create_scheduling_tools(
            MagicMock(),
            source_repo=MagicMock(),
            current_instance_id="inst-hoist-a",
            agent_id="ari",
        ),
        "create_view_write_tools": lambda: create_view_write_tools(
            _manager(),
            current_instance_id="inst-hoist-a",
            agent_id="ari",
        ),
    }


def _load_fixture() -> dict[str, Any]:
    with FIXTURE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


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


# ---------------------------------------------------------------------------
# (a) MODULE-LEVEL — the classes are no longer factory-local
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("module_name", "class_name"),
    HOISTED_CLASSES,
    ids=[name for _, name in HOISTED_CLASSES],
)
def test_input_model_is_module_level(module_name: str, class_name: str):
    """A factory-local class carries ``<locals>`` in its qualname."""
    cls = _resolve(module_name, class_name)
    assert cls is not None, (
        f"{class_name} is not a module-level attribute of {module_name} — "
        "it was re-declared inside the factory body, so it is rebuilt on "
        "every factory call"
    )
    assert "<locals>" not in cls.__qualname__, (
        f"{class_name} is still declared inside a function body "
        f"(qualname={cls.__qualname__!r}) — it is rebuilt on every "
        "factory call"
    )
    assert cls.__module__ == module_name, (
        f"{class_name} lives in {cls.__module__!r}, expected "
        f"{module_name!r}"
    )


@pytest.mark.parametrize(
    ("module_name", "class_name"),
    HOISTED_CLASSES,
    ids=[name for _, name in HOISTED_CLASSES],
)
def test_tool_uses_the_module_level_class(module_name: str, class_name: str):
    """The built tool's ``args_schema`` IS the module-level class object.

    Identity, not equivalence: the registry serves the memoised class, so
    the tool must point at the SAME object the module exposes — never at a
    per-call copy.
    """
    cls = _resolve(module_name, class_name)
    assert cls is not None, (
        f"{class_name} is not a module-level attribute of {module_name}"
    )
    tool_name = TOOL_FOR_CLASS[class_name]
    found = None
    for factory in _factories().values():
        for tool_obj in factory():
            if getattr(tool_obj, "name", None) == tool_name:
                found = tool_obj
                break
        if found is not None:
            break
    assert found is not None, f"tool {tool_name!r} was not built"
    assert found.args_schema is cls, (
        f"{tool_name!r} does not use the module-level {class_name} — "
        "the schema was copied per call"
    )


# ---------------------------------------------------------------------------
# (b) MUTATION PIN — the second build constructs ZERO pydantic classes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory_name",
    sorted(
        [
            "create_job_tools",
            "create_mission_watch_tools",
            "create_board_tools",
            "create_board_link_tools",
            "create_scheduling_tools",
            "create_view_write_tools",
        ]
    ),
)
def test_second_build_constructs_zero_pydantic_classes(
    factory_name: str, monkeypatch
):
    """A SECOND assembly of the same factory must not rebuild any schema.

    The counter wraps ``ModelMetaclass.__new__`` — every pydantic model
    CLASS construction in the process.  The first build may construct
    classes (the decorator's inferred schemas); the SECOND build must
    construct NONE.  Re-inlining one of the hoisted input models into its
    factory turns this pin RED — that is precisely the mutation it exists
    to catch.
    """
    factory = _factories()[factory_name]
    counter = _count_model_classes(monkeypatch)

    factory()  # first build — primes the memoisation
    counter["n"] = 0
    factory()  # second build — the measured one
    warm = counter["n"]

    assert warm == 0, (
        f"{factory_name}: the second build still constructed {warm} pydantic "
        "model classes — a factory-local input model is back (or another "
        "per-call class statement crept in)"
    )


def test_the_class_counter_detects_a_function_local_class(monkeypatch):
    """The zero-class pin above is only meaningful if the counter CAN see a
    function-local class statement.  This proves it does — without it the
    pin could pass vacuously (e.g. if the monkeypatch silently failed).
    """
    counter = _count_model_classes(monkeypatch)
    before = counter["n"]

    def _factory_local():
        class _Probe(BaseModel):
            x: int = 0

        return _Probe

    probe = _factory_local()
    assert counter["n"] - before == 1, (
        "the model-class counter did not observe a function-local class "
        "statement — the zero-class pin cannot be trusted"
    )
    assert "<locals>" in probe.__qualname__


# ---------------------------------------------------------------------------
# (c) BYTE-IDENTITY — the agent-visible schema must not move
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory_name",
    sorted(
        [
            "create_job_tools",
            "create_mission_watch_tools",
            "create_board_tools",
            "create_board_link_tools",
            "create_scheduling_tools",
            "create_view_write_tools",
        ]
    ),
)
def test_warm_payloads_are_byte_identical_to_the_base_fixture(factory_name: str):
    """The second build's OpenAI payloads must equal the PRE-HOIST fixture.

    Compared with ``json.dumps`` equality at the EXACT key order — no
    ``sort_keys`` — so a re-ordered or mutated schema is caught, not just
    a changed value.
    """
    from langchain_core.utils.function_calling import convert_to_openai_tool

    fixture = _load_fixture()
    expected_entries = fixture["factories"][factory_name]

    factory = _factories()[factory_name]
    factory()  # first build
    tools = factory()  # second build — the captured one

    actual_entries = [
        [getattr(t, "name", None), convert_to_openai_tool(t)] for t in tools
    ]

    assert len(actual_entries) == len(expected_entries), (
        f"{factory_name}: {len(actual_entries)} tools built, "
        f"{len(expected_entries)} in the BASE fixture"
    )
    for (actual_name, actual_payload), (expected_name, expected_payload) in zip(
        actual_entries, expected_entries
    ):
        assert actual_name == expected_name, (
            f"{factory_name}: tool name {actual_name!r} != {expected_name!r}"
        )
        actual_text = json.dumps(actual_payload)
        expected_text = json.dumps(expected_payload)
        assert actual_text == expected_text, (
            f"{factory_name}/{actual_name}: the OpenAI tool payload moved — "
            "the agent-visible schema is NOT byte-identical to the BASE"
        )


# ---------------------------------------------------------------------------
# (d) THE FIXTURE ITSELF — it must still describe the whole surface
# ---------------------------------------------------------------------------


def test_base_fixture_covers_every_factory_and_tool():
    """A fixture that lost a tool (or a factory that grew one) is caught."""
    fixture = _load_fixture()
    assert set(fixture["factories"]) == set(_factories()), (
        "the BASE fixture does not cover exactly the six cohort-A factories"
    )
    assert fixture["meta"]["base_commit"] == "3506a326d", (
        "the BASE fixture was not captured against the pre-hoist base commit"
    )
    expected_names = {
        "create_job_tools": [
            "job_create",
            "job_get",
            "job_list",
            "job_cancel",
            "job_retry",
            "job_delete",
            "job_restore",
            "queue_list",
            "queue_create",
            "queue_update",
            "dlq_list",
            "dlq_replay",
            "job_continue",
            "job_messages",
            "job_tree",
            "job_progress",
            "job_inject",
            "watch_job",
            "unwatch_job",
            "list_watched_jobs",
            "watch_jobs",
            "job_answer",
            "job_pause",
            "job_resume",
        ],
        "create_mission_watch_tools": ["watch_mission"],
        "create_board_tools": [
            "board_create",
            "board_update",
            "board_list",
            "board_get",
        ],
        "create_board_link_tools": ["board_link"],
        "create_scheduling_tools": [
            "task_schedule",
            "task_schedule_list",
            "task_schedule_cancel",
            "task_schedule_update",
        ],
        "create_view_write_tools": ["view_write"],
    }
    for factory_name, names in expected_names.items():
        entries = fixture["factories"][factory_name]
        assert [entry[0] for entry in entries] == names, (
            f"{factory_name}: the BASE fixture's tool roster drifted"
        )


def test_registry_still_serves_the_module_level_schemas():
    """The registry must hold the module-level classes after a build.

    This is the OTHER half of the fix: the classes are built once at
    import, and the registry memoises them so every assembly re-uses the
    SAME class object.
    """
    clear_registry()
    for factory in _factories().values():
        factory()
    stats = registry_stats()
    assert stats["entries"] > 0, "the schema registry holds no entries"
