"""Unit pins for the process-level tool-schema registry (ROW2-DEEP-DIVE §3).

The registry (``daemon/tools/_schema_registry.py``) memoises the pydantic
``args_schema`` class that langchain's ``@tool`` decorator builds, plus the
derived OpenAI tool payload.  Without it, EVERY graph assembly rebuilds
~640 pydantic model classes (the ``@tool`` decorations) and derives the
OpenAI payload three times over (the three ``bind_tools`` sites) — the
flagship 23.16 s event-loop stall.

What is pinned here:

* **memoisation** — a second invocation of the same factory derives NO
  fresh pydantic model classes (the per-assembly rebuild is gone);
* **byte-identity** — the memoised schema's ``model_json_schema()`` and the
  OpenAI payload are byte-identical to a freshly-built / freshly-derived
  one, across the WHOLE tool surface (not a sample);
* **bind-once** — all THREE ``bind_tools`` sites in
  ``daemon.graph.build_instance_llms`` (the vision branch included) consume
  ONE payload derivation;
* **closure isolation** — the memoised schema is shared, the tool object
  and its closure are NOT;
* **MCP dynamism** — a re-discovered MCP schema yields a fresh payload.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from daemon.tools._schema_registry import (
    clear_payload_cache,
    clear_registry,
    openai_tool_payloads,
    registry_stats,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _build_surface() -> list:
    """Build the daemon's tool surface the way a graph assembly does."""
    from daemon.tools.instance import create_instance_tools

    manager = MagicMock(name="InstanceManager")
    manager.config.llm.allowed_models = []
    manager.config.daemon.port = 0
    return create_instance_tools(manager, "schema-registry-unit", "")


def _by_name(tools: list) -> dict[str, Any]:
    return {getattr(t, "name", ""): t for t in tools}


def _payload_json(tool_obj: Any) -> str:
    from langchain_core.utils.function_calling import convert_to_openai_tool

    return json.dumps(convert_to_openai_tool(tool_obj))


# The residual: the factories that declare their input model INSIDE the
# factory body (``class JobCreateInput(BaseModel): ...``) rebuild that one
# class per assembly.  It is NOT reachable from the ``@tool`` decorator (the
# caller supplies the schema), so the registry cannot memoise it.
_RESIDUAL_CLASS_BUDGET = 96


# ---------------------------------------------------------------------------
# 1. Memoisation — the per-assembly rebuild is gone
# ---------------------------------------------------------------------------


def test_second_assembly_derives_almost_no_fresh_model_classes(monkeypatch):
    """A SECOND assembly of the same surface must not rebuild the schemas."""
    counter = _count_model_classes(monkeypatch)
    clear_registry()

    cold_tools = _build_surface()
    cold = counter["n"]
    assert cold > 100, (
        f"the cold build only constructed {cold} pydantic model classes — "
        "the census is not measuring what this pin claims to measure"
    )

    counter["n"] = 0
    warm_tools = _build_surface()
    warm = counter["n"]
    assert warm <= _RESIDUAL_CLASS_BUDGET, (
        f"the second assembly still constructed {warm} pydantic model "
        f"classes (budget {_RESIDUAL_CLASS_BUDGET}) — the per-assembly "
        "rebuild is back"
    )

    assert len(cold_tools) == len(warm_tools)

    # The OpenAI payload derivation is memoised too: the FIRST derivation may
    # build models, every later one must build NONE.
    openai_tool_payloads(warm_tools)
    counter["n"] = 0
    payloads = openai_tool_payloads(warm_tools)
    assert counter["n"] == 0, (
        f"the payload pass constructed {counter['n']} pydantic model "
        "classes — the payload is not memoised"
    )
    assert len(payloads) == len(warm_tools)

    # ... and it survives a FURTHER assembly (the memoised schema class is
    # the cache key, so the next assembly hits the same entries).
    third_tools = _build_surface()
    counter["n"] = 0
    openai_tool_payloads(third_tools)
    assert counter["n"] == 0, (
        "the payload was re-derived after a further assembly — the "
        "per-assembly rebuild is back"
    )


def test_registry_reports_the_memoised_entries():
    stats = registry_stats()
    assert stats["entries"] > 0
    assert stats["schema_hits"] >= 0


# ---------------------------------------------------------------------------
# 2. Byte-identity — the provider payload must not move
# ---------------------------------------------------------------------------


def test_memoised_schema_is_byte_identical_to_the_freshly_built_one():
    """The cached ``model_json_schema()`` == the freshly-built one, tool for tool.

    The census is the WHOLE surface: surface A is built with a CLEARED
    registry (every schema freshly built by langchain), surface B is built
    with the warm registry (every schema served from the cache).
    """
    clear_registry()
    fresh_tools = _by_name(_build_surface())
    cached_tools = _by_name(_build_surface())

    assert set(fresh_tools) == set(cached_tools)
    assert len(fresh_tools) > 50, "the census surface is implausibly small"

    mismatched: list[str] = []
    for name, fresh_tool in fresh_tools.items():
        cached_tool = cached_tools[name]
        fresh_schema = getattr(fresh_tool, "args_schema", None)
        cached_schema = getattr(cached_tool, "args_schema", None)
        if fresh_schema is None or cached_schema is None:
            continue
        if isinstance(fresh_schema, dict) or isinstance(cached_schema, dict):
            fresh_json = fresh_schema
            cached_json = cached_schema
        else:
            fresh_json = fresh_schema.model_json_schema()
            cached_json = cached_schema.model_json_schema()
        if fresh_json != cached_json:
            mismatched.append(name)
    assert not mismatched, f"schema drift on: {mismatched[:10]}"


def test_openai_payload_is_byte_identical_to_a_fresh_derivation():
    """The memoised payload == a fresh ``convert_to_openai_tool`` derivation."""
    tools = _build_surface()
    memoised = [json.dumps(p) for p in openai_tool_payloads(tools)]
    fresh = [_payload_json(t) for t in tools]
    assert memoised == fresh, "the memoised OpenAI payload drifted"


def test_payload_key_order_is_preserved():
    """Key ORDER is part of the byte-identity contract, not just the content."""
    tools = _build_surface()
    for payload, tool_obj in zip(openai_tool_payloads(tools), tools):
        assert list(payload.keys()) == ["type", "function"]
        assert list(payload["function"].keys())[:3] == [
            "name",
            "description",
            "parameters",
        ]
        assert payload["function"]["name"] == getattr(tool_obj, "name", None)


# ---------------------------------------------------------------------------
# 3. Bind-once — all three ``bind_tools`` sites, the vision branch included
# ---------------------------------------------------------------------------


def test_all_three_bind_sites_derive_the_payload_once(monkeypatch):
    """``build_instance_llms`` must derive the OpenAI payload EXACTLY once.

    Regression pin for the tester defect D4: the vision branch
    (``llm_vision_chat.bind_tools``) is a THIRD bind site, so the payload
    multiplier was 3 x N_tools whenever a vision model was configured.
    """
    import langchain_core.utils.function_calling as function_calling
    from langchain_core.runnables import RunnableLambda

    from daemon import graph

    tools = _build_surface()
    clear_payload_cache()

    counter = {"n": 0}
    original = function_calling.convert_to_openai_tool

    def _counting(tool_obj, **kwargs):  # noqa: ANN001
        counter["n"] += 1
        return original(tool_obj, **kwargs)

    monkeypatch.setattr(function_calling, "convert_to_openai_tool", _counting)

    bound: list[list] = []

    class _FakeChat:
        # The class-level knobs ``clean_llm_config`` reads off the real
        # ``ThinkingChatOpenAI`` — mirrored so the config path is unchanged.
        default_streaming = True
        default_request_timeout = None
        default_request_gzip = False

        def __init__(self, **kwargs):  # noqa: ANN003, ANN002
            self.kwargs = kwargs

        def bind_tools(self, tools_arg, **kwargs):  # noqa: ANN001, ANN003
            bound.append(list(tools_arg))
            return RunnableLambda(lambda payload: payload)

    monkeypatch.setattr(graph, "ThinkingChatOpenAI", _FakeChat)

    graph.build_instance_llms(
        {"base_url": "http://127.0.0.1:9/v1", "api_key": "unit-test-key"},
        "standard-model",
        "vision-model",
        tools,
        retry_config=None,
    )

    # THREE bind SITES exist in the source (the vision branch + the two
    # standard binds) but only TWO of them RUN per assembly: the vision
    # branch and the "standard-with-tools" bind are mutually exclusive
    # (``if llm_with_tools is None``).  Either way the payload must be
    # derived ONCE — a re-derivation at either site is the regression.
    assert len(bound) == 2, f"expected two bind calls, saw {len(bound)}"
    assert counter["n"] == len(tools), (
        f"the OpenAI payload was derived {counter['n']} times for "
        f"{len(tools)} tools — it must be derived ONCE per tool"
    )
    # Every site received the SAME payload objects.
    for site in bound[1:]:
        assert site == bound[0]
        assert all(a is b for a, b in zip(site, bound[0]))


def test_no_vision_model_still_binds_once(monkeypatch):
    """The standard-only path derives the payload once too."""
    import langchain_core.utils.function_calling as function_calling
    from langchain_core.runnables import RunnableLambda

    from daemon import graph

    tools = _build_surface()
    clear_payload_cache()

    counter = {"n": 0}
    original = function_calling.convert_to_openai_tool

    def _counting(tool_obj, **kwargs):  # noqa: ANN001
        counter["n"] += 1
        return original(tool_obj, **kwargs)

    monkeypatch.setattr(function_calling, "convert_to_openai_tool", _counting)

    bound: list[list] = []

    class _FakeChat:
        # The class-level knobs ``clean_llm_config`` reads off the real
        # ``ThinkingChatOpenAI`` — mirrored so the config path is unchanged.
        default_streaming = True
        default_request_timeout = None
        default_request_gzip = False

        def __init__(self, **kwargs):  # noqa: ANN003, ANN002
            self.kwargs = kwargs

        def bind_tools(self, tools_arg, **kwargs):  # noqa: ANN001, ANN003
            bound.append(list(tools_arg))
            return RunnableLambda(lambda payload: payload)

    monkeypatch.setattr(graph, "ThinkingChatOpenAI", _FakeChat)

    graph.build_instance_llms(
        {"base_url": "http://127.0.0.1:9/v1", "api_key": "unit-test-key"},
        "standard-model",
        None,
        tools,
        retry_config=None,
    )

    assert len(bound) == 2, f"expected two bind sites, saw {len(bound)}"
    assert counter["n"] == len(tools)


# ---------------------------------------------------------------------------
# 4. Closure isolation — the SCHEMA is shared, the tool is NOT
# ---------------------------------------------------------------------------


def test_schema_is_shared_but_the_closure_is_not():
    """Two builds of the same factory share the schema, not the tool."""
    from daemon.tools._schema_registry import tool as registry_tool

    def _factory(tag: str):
        @registry_tool
        def probe(a: int, b: str = "x") -> str:
            """Probe tool.

            Args:
                a: an int
            """
            return f"{tag}:{a}"

        return probe

    first = _factory("one")
    second = _factory("two")

    assert first is not second
    assert first.args_schema is second.args_schema, (
        "the schema class must be shared — that is the whole point"
    )
    assert first.invoke({"a": 5}) == "one:5"
    assert second.invoke({"a": 5}) == "two:5"


def test_registry_does_not_mutate_the_shared_schema():
    """The cached class is handed out as-is; nothing mutates it."""
    from daemon.tools._schema_registry import tool as registry_tool

    def _factory():
        @registry_tool
        def probe(a: int) -> str:
            """Probe tool.

            Args:
                a: an int
            """
            return "x"

        return probe

    first = _factory()
    schema = first.args_schema
    before = schema.model_json_schema()
    _factory()
    assert schema.model_json_schema() == before
    assert first.args_schema is schema


# ---------------------------------------------------------------------------
# 5. MCP dynamism — a re-discovered schema yields a fresh payload
# ---------------------------------------------------------------------------


def test_a_changed_schema_yields_a_fresh_payload():
    """The payload cache is keyed on the schema IDENTITY, not the name."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel

    from daemon.tools._schema_registry import openai_tool_payloads as payloads_of

    def _make():
        class _Input(BaseModel):
            value: int

        return StructuredTool.from_function(
            func=lambda value: value,
            name="mcp_dynamic_probe",
            description="A dynamically discovered tool.",
            args_schema=_Input,
        )

    first = _make()
    second = _make()
    assert first.args_schema is not second.args_schema

    payload_first = payloads_of([first])[0]
    payload_second = payloads_of([second])[0]
    # Same name + description, different schema CLASSES → two cache entries.
    assert payload_first is not payload_second
    assert payload_first == payload_second  # same content, different entries


# ---------------------------------------------------------------------------
# 6. The boot warm
# ---------------------------------------------------------------------------


def test_boot_warm_memoises_the_schemas():
    """``warm_schema_registry`` must memoise the surface without a DB."""
    from daemon.tools._schema_registry import warm_schema_registry

    clear_registry()
    warmed = warm_schema_registry()
    assert warmed > 100, f"the warm only memoised {warmed} schemas"

    stats = registry_stats()
    assert stats["entries"] >= warmed


def test_boot_warm_uses_no_database():
    """The warm must not touch the DB or any live manager."""
    from daemon.tools._schema_registry import _InertManager

    from daemon.tools._schema_registry import _INERT

    manager = _InertManager()
    # No MCP service (so no MCP discovery), and no database engine, session
    # or pool anywhere on the stand-in — every unknown attribute resolves to
    # the inert sentinel, which is falsy and never a live handle.
    assert manager._mcp_service is None
    for forbidden in ("engine", "session", "_engine", "db", "_db", "_pool"):
        assert getattr(manager, forbidden, None) is _INERT
    assert not _INERT


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_boot_warm_can_be_disabled(monkeypatch, value):
    """``ENSEMBLE_SCHEMA_REGISTRY_WARM=0`` turns the warm off."""
    import threading

    from daemon import __main__ as daemon_main

    started: list[str] = []
    monkeypatch.setattr(
        threading.Thread,
        "start",
        lambda self: started.append(self.name),
    )
    monkeypatch.setenv("ENSEMBLE_SCHEMA_REGISTRY_WARM", value)
    daemon_main._start_schema_registry_warm()
    assert started == [], "the warm thread started despite the kill switch"


def test_boot_warm_starts_a_background_thread(monkeypatch):
    """The warm runs on a DAEMON thread so it cannot hold the process open."""
    import threading

    from daemon import __main__ as daemon_main

    started: list[threading.Thread] = []
    original_start = threading.Thread.start

    def _capture(self):  # noqa: ANN001
        started.append(self)
        return original_start(self)

    monkeypatch.setattr(threading.Thread, "start", _capture)
    monkeypatch.delenv("ENSEMBLE_SCHEMA_REGISTRY_WARM", raising=False)
    daemon_main._start_schema_registry_warm()
    assert len(started) == 1
    assert started[0].name == "schema-registry-warm"
    assert started[0].daemon is True
