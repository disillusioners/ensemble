"""Unit pin for the `_full_doc_` extended tool_help channel of the scheduling tools.

WHAT THIS PINS
--------------
The OpenAI-payload fixture (``tests/unit/tools/fixtures/module_level_inputs_a_base.json``)
pins the agent-visible schema that reaches ``convert_to_openai_tool``.  It CANNOT
see the extended ``_full_doc_`` documentation that tools expose through the
``_full_docs`` registry channel in ``daemon/tools/_tool_registry.py`` and that
``tool_help`` consumes — that channel is a SEPARATE registry, populated by
``scan_tools_for_full_docs``.  The Phase-1 scheduling work rewrote listing-field
wording (``window``, ``project_id``, ``last_execution_status``) and the
live-preference resolution wording that live ONLY in that channel, so a pin on
the OpenAI payload is blind to them.  This file closes that gap: the warm build's
``_full_doc_`` strings must be byte-identical to the BASE fixture captured here.

Capture path (AC1): build ``create_scheduling_tools`` TWICE (first primes, second
is the captured warm build), run ``scan_tools_for_full_docs`` on the warm tool
list to populate ``_full_docs``, then read each string back via ``get_full_doc``
— the exact path ``tool_help`` uses.  Nothing is hand-copied from
``daemon/tools/scheduling.py``.

SAFETY FENCE: pure unit tests — mocks only.  No DB, no network, no live state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from daemon.tools._tool_registry import (
    clear_registry,
    get_full_doc,
    scan_tools_for_full_docs,
)

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "tool_full_doc_scheduling_base.json"
)


def _factories() -> dict[str, Any]:
    from daemon.tools.scheduling import create_scheduling_tools

    return {
        "create_scheduling_tools": lambda: create_scheduling_tools(
            MagicMock(),
            source_repo=MagicMock(),
            current_instance_id="inst-full-doc-pin",
            agent_id="ari",
        ),
    }


def _load_fixture() -> dict[str, Any]:
    with FIXTURE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# (a) BYTE-IDENTITY — the _full_doc_ channel must not move
# ---------------------------------------------------------------------------


def test_warm_full_docs_are_byte_identical_to_base_fixture():
    """The second build's `_full_doc_` strings must equal the BASE fixture.

    Compared as plain string equality (the captured payload IS the string),
    which is the strongest possible byte-identity check — no tolerance, no
    substring, no normalisation.
    """
    fixture = _load_fixture()
    expected_entries = fixture["factories"]["create_scheduling_tools"]

    factory = _factories()["create_scheduling_tools"]
    factory()  # first build — primes
    tools = factory()  # second build — the captured one

    clear_registry()
    scan_tools_for_full_docs(tools)

    actual_entries = [
        [getattr(t, "name", None), get_full_doc(getattr(t, "name", None))]
        for t in tools
    ]

    assert len(actual_entries) == len(expected_entries), (
        f"create_scheduling_tools: {len(actual_entries)} tools built, "
        f"{len(expected_entries)} in the BASE fixture"
    )
    for (actual_name, actual_doc), (expected_name, expected_doc) in zip(
        actual_entries, expected_entries
    ):
        assert actual_name == expected_name, (
            f"create_scheduling_tools: tool name {actual_name!r} != "
            f"{expected_name!r}"
        )
        assert actual_doc == expected_doc, (
            f"create_scheduling_tools/{actual_name}: the _full_doc_ text moved "
            "— the extended tool_help channel is NOT byte-identical to the BASE"
        )


# ---------------------------------------------------------------------------
# (b) THE FIXTURE ITSELF — it must still describe the whole surface
# ---------------------------------------------------------------------------


def test_base_fixture_covers_the_scheduling_factory_and_roster():
    """A fixture that lost a tool (or grew one) is caught."""
    fixture = _load_fixture()
    assert set(fixture["factories"]) == set(_factories()), (
        "the BASE fixture does not cover exactly the scheduling factory"
    )
    entries = fixture["factories"]["create_scheduling_tools"]
    assert [e[0] for e in entries] == [
        "task_schedule",
        "task_schedule_list",
        "task_schedule_cancel",
        "task_schedule_update",
    ], "the BASE fixture's scheduling tool roster drifted"

    # meta provenance must be present and match the captured base commit
    meta = fixture["meta"]
    assert meta["base_commit"] == "1a8310eb8", (
        "the BASE fixture was not captured against the expected base commit"
    )
    assert meta.get("entry_shape"), "meta.entry_shape must be recorded"


def test_registry_serves_the_captured_full_docs():
    """After a warm build + scan, get_full_doc returns the captured text."""
    factory = _factories()["create_scheduling_tools"]
    factory()
    tools = factory()
    clear_registry()
    scan_tools_for_full_docs(tools)
    fixture = _load_fixture()
    expected = {
        e[0]: e[1] for e in fixture["factories"]["create_scheduling_tools"]
    }
    for t in tools:
        name = getattr(t, "name", None)
        assert get_full_doc(name) == expected[name], (
            f"{name}: get_full_doc does not match the captured BASE"
        )
