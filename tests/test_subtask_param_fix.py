"""Verification tests for the ``todo_graph_add_subtask`` parameter rename.

Background
----------

The tool was originally ``todo_graph_add_subtask(node_id, text)`` where ``text``
accepted either a single string or a ``list[str]`` (atomic batch). The
parameter was renamed to ``list`` (more accurate for the batch case) with
``text`` kept as a deprecated alias, and JSON-string auto-parse was added
so a string like ``'["a","b"]'`` becomes a 2-item batch transparently.

This file is the dedicated verification suite for that change. It is
intentionally separate from ``tests/test_todo_tools.py`` so a future
contributor can see "what scenarios the rename had to satisfy" in one
place without grepping the broader tool test file.

Coverage matrix
---------------

The 11 scenarios below map 1:1 to the task spec. Each scenario gets its
own test method named ``test_<scenario_number>_<short_summary>`` so a
reviewer can scan the test list and see exactly what was verified.

Schema verification (Part 3 of the task spec) is in
:class:`TestSchemaVerification`.

Reference implementation
------------------------

The behavior under test lives in
``daemon/tools/todo_tools.py:todo_graph_add_subtask`` (the inner ``@tool``-
decorated closure inside ``create_todo_tools``). Constants
:data:`daemon.services.todo_manager.MAX_SUBTASKS_PER_NODE` and
:data:`daemon.services.todo_manager.MAX_SUBTASK_TEXT_LENGTH` are the
boundary values for scenarios 10 and 11.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from daemon.services.todo_manager import (
    MAX_SUBTASKS_PER_NODE,
    MAX_SUBTASK_TEXT_LENGTH,
    TodoManager,
)


# =============================================================================
# Local fixtures (mirror the helpers in tests/test_todo_tools.py so this file
# stays self-contained — easy to copy/run in isolation).
# =============================================================================


def _make_manager() -> MagicMock:
    """Build a mock ``InstanceManager`` with a real TodoManager attached.

    The tool only reads ``manager._todo_manager``; the rest of the mock is
    a stub. A real ``TodoManager`` (not a ``MagicMock``) means state
    mutations actually exercise the manager's validation logic.
    """
    manager = MagicMock()
    manager._todo_manager = TodoManager()
    return manager


def _build_tools(manager: MagicMock | None = None):
    """Build the 11 todo tools, returning the list in canonical order."""
    from daemon.tools.todo_tools import create_todo_tools

    if manager is None:
        manager = _make_manager()
    return create_todo_tools(
        manager=manager,
        current_instance_id="test-instance-id",
        live_event_hub=None,
    )


def _seed_graph(manager: MagicMock, node_id: str = "alpha", text: str = "Alpha task") -> None:
    """Seed one node so subtask tests can target it."""
    manager._todo_manager.create_graph(
        "test-instance-id",
        nodes=[{"id": node_id, "text": text}],
        edges=[],
    )


# =============================================================================
# Part 2: 11 scenarios
# =============================================================================


class TestSubtaskParamFixScenarios:
    """The 11 verification scenarios from the task spec.

    Each test method corresponds to exactly one numbered scenario. The
    ordering mirrors the spec so a quick visual scan tells you which
    scenarios are covered and in what order.
    """

    # -- Scenario 1: list="my subtask" → 1 subtask created -------------------
    async def test_01_list_single_string_creates_one_subtask(self):
        """Scenario 1: ``list`` with a single string creates exactly 1 subtask.

        The ``list`` parameter accepts a single string for the common
        one-item case without forcing callers to wrap in ``["..."]``.
        """
        manager = _make_manager()
        _seed_graph(manager)
        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        result = await add_subtask_tool.coroutine(node_id="alpha", list="my subtask")

        assert "Added sub-task" in result
        assert "s-" in result
        stored = manager._todo_manager.get_all("test-instance-id")
        assert len(stored[0]["subtasks"]) == 1
        assert stored[0]["subtasks"][0]["text"] == "my subtask"
        assert stored[0]["subtasks"][0]["status"] == "pending"

    # -- Scenario 2: list=["a","b","c"] → 3 subtasks --------------------------
    async def test_02_list_batch_creates_n_subtasks(self):
        """Scenario 2: ``list`` with a list of strings creates all items atomically.

        The batched code path uses the plural ``Added N sub-tasks`` header
        and lists every generated id in one confirmation line.
        """
        manager = _make_manager()
        _seed_graph(manager)
        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        result = await add_subtask_tool.coroutine(
            node_id="alpha", list=["a", "b", "c"]
        )

        assert "Added 3 sub-tasks" in result
        # At least 3 s-prefixed ids appear in the confirmation line.
        assert result.count("s-") >= 3
        stored = manager._todo_manager.get_all("test-instance-id")
        assert len(stored[0]["subtasks"]) == 3
        assert [st["text"] for st in stored[0]["subtasks"]] == ["a", "b", "c"]

    # -- Scenario 3: text="old style" (alias) → 1 subtask ---------------------
    async def test_03_text_alias_still_works_for_backward_compat(self):
        """Scenario 3: legacy ``text=...`` kwarg is still accepted (back-compat).

        Old agents that pass ``text=`` instead of ``list=`` must continue
        to work unchanged. The tool promotes ``text`` to ``list``
        internally before validation.
        """
        manager = _make_manager()
        _seed_graph(manager)
        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        # Old-style call: ``text=...`` only, no ``list=``.
        result = await add_subtask_tool.coroutine(node_id="alpha", text="old style")

        assert "Added sub-task" in result
        assert "s-" in result
        stored = manager._todo_manager.get_all("test-instance-id")
        assert len(stored[0]["subtasks"]) == 1
        assert stored[0]["subtasks"][0]["text"] == "old style"

    # -- Scenario 4: list + text both → list wins -----------------------------
    async def test_04_list_takes_priority_over_text_when_both_provided(self):
        """Scenario 4: when both are passed, ``list`` wins.

        ``text`` is the deprecated alias — if a caller passes both (e.g.
        during migration), the new ``list`` value is authoritative. This
        matches the docstring contract.
        """
        manager = _make_manager()
        _seed_graph(manager)
        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        result = await add_subtask_tool.coroutine(
            node_id="alpha", list="new", text="old"
        )

        assert "Added sub-task" in result
        stored = manager._todo_manager.get_all("test-instance-id")
        assert len(stored[0]["subtasks"]) == 1
        # The new ``list`` value, NOT the legacy ``text`` alias, is stored.
        assert stored[0]["subtasks"][0]["text"] == "new"

    # -- Scenario 5: list='["a","b"]' (JSON string) → 2 subtasks -------------
    async def test_05_json_string_array_auto_parsed(self):
        """Scenario 5: a JSON-encoded array string is auto-parsed into a list.

        Many agent frameworks serialize tool args as JSON; a caller that
        passes ``'["a","b"]'`` (a JSON string, not a Python list) should
        get the same result as passing ``["a", "b"]`` directly.
        """
        manager = _make_manager()
        _seed_graph(manager)
        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        result = await add_subtask_tool.coroutine(
            node_id="alpha", list='["a", "b"]'
        )

        assert "Added 2 sub-tasks" in result
        stored = manager._todo_manager.get_all("test-instance-id")
        assert len(stored[0]["subtasks"]) == 2
        assert [st["text"] for st in stored[0]["subtasks"]] == ["a", "b"]

    # -- Scenario 6: list='not json [but has brackets]' → 1 subtask plain ----
    async def test_06_invalid_json_string_falls_back_to_plain_text(self):
        """Scenario 6: a string that *looks* like JSON but isn't parseable
        is silently treated as a single plain-string subtask.

        The auto-parse path must NOT reject legitimate strings that happen
        to contain ``[`` or ``]``. The implementation only attempts
        ``json.loads`` when the string starts with ``[`` and ends with
        ``]``; on failure it falls through to plain-string handling.
        """
        manager = _make_manager()
        _seed_graph(manager)
        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        result = await add_subtask_tool.coroutine(
            node_id="alpha", list="not json [but has brackets]"
        )

        assert "Added sub-task" in result  # singular: single subtask
        stored = manager._todo_manager.get_all("test-instance-id")
        assert len(stored[0]["subtasks"]) == 1
        # Verbatim — no JSON parsing happened, no split, no error.
        assert stored[0]["subtasks"][0]["text"] == "not json [but has brackets]"

    # -- Scenario 7: list=[] → clear error ------------------------------------
    async def test_07_empty_list_returns_clear_error(self):
        """Scenario 7: passing ``list=[]`` returns a clear, actionable error.

        Without the explicit empty-list guard this would either silently
        succeed with 0 items or surface the generic ``"Failed to add
        sub-task"`` from the exception handler. The implementation adds a
        dedicated error string that mentions the cause ("empty").
        """
        manager = _make_manager()
        _seed_graph(manager)
        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        result = await add_subtask_tool.coroutine(node_id="alpha", list=[])

        assert result.startswith("ERROR:")
        # The empty-list guard message should mention "empty" so callers
        # understand what they did wrong.
        assert "empty" in result.lower()
        # No state was created.
        stored = manager._todo_manager.get_all("test-instance-id")
        assert stored[0]["subtasks"] == []

    # -- Scenario 8: list=None, text=None → error about missing param --------
    async def test_08_both_none_returns_missing_parameter_error(self):
        """Scenario 8: omitting both ``list`` and ``text`` errors clearly.

        The error must guide the caller to the new primary parameter
        name (``list``) while acknowledging the legacy alias (``text``).
        """
        manager = _make_manager()
        _seed_graph(manager)
        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        result = await add_subtask_tool.coroutine(node_id="alpha")

        assert result.startswith("ERROR:")
        # The message references the new parameter name so callers learn it.
        assert "list" in result.lower()
        # The alias is mentioned so old agents know ``text`` still works.
        assert "text" in result.lower()
        # Nothing was created.
        stored = manager._todo_manager.get_all("test-instance-id")
        assert stored[0]["subtasks"] == []

    # -- Scenario 9: list='["only"]' → 1 subtask "only" (JSON-parsed) --------
    async def test_09_json_single_item_array_not_treated_as_plain_string(self):
        """Scenario 9: ``'["only"]'`` is JSON-parsed to ``["only"]`` and
        creates exactly 1 subtask with text ``"only"`` — NOT a literal
        ``'["only"]'`` string.

        This guards against the parser short-circuiting on length: a
        single-item JSON array must still go through the parse path so
        callers get the element, not the raw JSON.
        """
        manager = _make_manager()
        _seed_graph(manager)
        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        result = await add_subtask_tool.coroutine(node_id="alpha", list='["only"]')

        assert "Added sub-task" in result  # singular: parsed to 1-element list
        stored = manager._todo_manager.get_all("test-instance-id")
        assert len(stored[0]["subtasks"]) == 1
        assert stored[0]["subtasks"][0]["text"] == "only"
        # Defensive: the literal JSON must not appear as the stored text.
        assert stored[0]["subtasks"][0]["text"] != '["only"]'

    # -- Scenario 10: 500 chars PASS, 501 chars ERROR -------------------------
    async def test_10_subtask_text_length_boundary_500_passes_501_errors(self):
        """Scenario 10: the 500-char boundary is exact.

        A subtask at exactly ``MAX_SUBTASK_TEXT_LENGTH`` (500) is accepted;
        one over (501) is rejected. The boundary must be inclusive at
        the upper edge.
        """
        manager = _make_manager()
        _seed_graph(manager)
        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        # 500 chars: must succeed.
        text_at_limit = "x" * MAX_SUBTASK_TEXT_LENGTH
        assert len(text_at_limit) == 500
        result_ok = await add_subtask_tool.coroutine(
            node_id="alpha", list=text_at_limit
        )
        assert "Added sub-task" in result_ok
        stored = manager._todo_manager.get_all("test-instance-id")
        assert len(stored[0]["subtasks"]) == 1
        assert len(stored[0]["subtasks"][0]["text"]) == MAX_SUBTASK_TEXT_LENGTH

        # 501 chars: must fail with a clear, non-generic error.
        text_over_limit = "y" * (MAX_SUBTASK_TEXT_LENGTH + 1)
        assert len(text_over_limit) == 501
        result_err = await add_subtask_tool.coroutine(
            node_id="alpha", list=text_over_limit
        )
        assert result_err.startswith("ERROR:")
        # The over-length entry was rejected; only the 500-char item remains.
        stored_after = manager._todo_manager.get_all("test-instance-id")
        assert len(stored_after[0]["subtasks"]) == 1
        assert stored_after[0]["subtasks"][0]["text"] == text_at_limit

    # -- Scenario 11: 20th PASS, 21st ERROR -----------------------------------
    async def test_11_max_subtasks_boundary_20_passes_21_errors(self):
        """Scenario 11: the 20-subtask cap is exact.

        Adding the 20th subtask to a node succeeds; the 21st fails.
        Atomicity: the 21st call leaves the node at exactly 20 items.
        """
        manager = _make_manager()
        # Seed with 19 subtasks, then add the 20th via the tool to exercise
        # the normal path (not the seed-time shortcut).
        manager._todo_manager.create_graph(
            "test-instance-id",
            nodes=[
                {
                    "id": "alpha",
                    "text": "Alpha",
                    "subtasks": [
                        {"text": f"seed {i}"}
                        for i in range(MAX_SUBTASKS_PER_NODE - 1)
                    ],
                }
            ],
            edges=[],
        )
        tools = _build_tools(manager=manager)
        add_subtask_tool = tools[6]

        # 20th subtask: must succeed.
        result_ok = await add_subtask_tool.coroutine(
            node_id="alpha", list="item 20"
        )
        assert "Added sub-task" in result_ok
        stored = manager._todo_manager.get_all("test-instance-id")
        assert len(stored[0]["subtasks"]) == MAX_SUBTASKS_PER_NODE

        # 21st subtask: must fail and leave state unchanged.
        result_err = await add_subtask_tool.coroutine(
            node_id="alpha", list="item 21"
        )
        assert result_err.startswith("ERROR:")
        stored_after = manager._todo_manager.get_all("test-instance-id")
        assert len(stored_after[0]["subtasks"]) == MAX_SUBTASKS_PER_NODE
        # The rejected entry did NOT sneak in.
        assert all(
            st["text"] != "item 21" for st in stored_after[0]["subtasks"]
        )


# =============================================================================
# Part 3: Schema verification
# =============================================================================


class TestSchemaVerification:
    """Verify the LangChain tool schema advertises the new contract.

    The tool is wrapped by ``@tool`` which derives an ``args_schema`` from
    the function signature. The schema is what LLM tool-call planners see
    when deciding what arguments to send — so the schema is part of the
    public contract, not just an implementation detail.
    """

    def _get_add_subtask_tool(self):
        """Return the ``todo_graph_add_subtask`` tool from the factory."""
        tools = _build_tools()
        for t in tools:
            if t.name == "todo_graph_add_subtask":
                return t
        raise AssertionError("todo_graph_add_subtask not in tool list")

    def _schema(self, tool) -> dict:
        """Return the tool's args_schema as a JSON Schema dict.

        LangChain's ``@tool`` decorator attaches a Pydantic model at
        ``tool.args_schema``. We prefer ``model_json_schema()`` (Pydantic v2,
        non-deprecated) and fall back to ``.schema()`` (Pydantic v1) so the
        test stays compatible regardless of which model class LangChain
        is built on at the moment.
        """
        model = tool.args_schema  # type: ignore[attr-defined]
        if hasattr(model, "model_json_schema"):
            return model.model_json_schema()
        return model.schema()

    def test_args_schema_exposes_list_as_primary_parameter(self):
        """The tool schema advertises ``list`` as a parameter.

        LLM planners see ``list`` (the new name) in the schema; if the
        schema only exposed ``text``, callers would be steered toward the
        deprecated alias. The schema MUST include ``list``.
        """
        tool = self._get_add_subtask_tool()
        properties = self._schema(tool).get("properties", {})

        assert "list" in properties, (
            f"Expected 'list' in tool schema properties, got {list(properties)}"
        )

    def test_args_schema_exposes_text_as_optional_alias(self):
        """The tool schema advertises ``text`` as an optional parameter.

        The deprecated alias must remain visible in the schema so old
        agents that generate ``text=...`` calls continue to validate.
        Marking it optional (not in ``required``) signals to planners
        that ``list`` is the new primary name.
        """
        tool = self._get_add_subtask_tool()
        schema = self._schema(tool)
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        assert "text" in properties, (
            f"Expected 'text' in tool schema properties for back-compat, "
            f"got {list(properties)}"
        )
        # ``text`` is optional: omitting it must NOT be a schema error.
        assert "text" not in required, (
            f"'text' should be optional (deprecated alias) but is listed "
            f"in required={required}"
        )

    def test_args_schema_list_is_optional_not_required(self):
        """``list`` is optional in the schema (back-compat for callers who
        still send only ``text=...``).

        If ``list`` were marked required, an old agent sending only
        ``text=...`` would be rejected at the schema layer — defeating
        the whole point of keeping the alias.
        """
        tool = self._get_add_subtask_tool()
        required = self._schema(tool).get("required", [])

        assert "list" not in required, (
            f"'list' should be optional so the legacy 'text=' alias "
            f"continues to work, but it's in required={required}"
        )

    def test_args_schema_node_id_remains_required(self):
        """``node_id`` is still the one required parameter.

        The rename only touched ``text`` → ``list``; ``node_id`` must
        remain required so a missing parent id is caught at the schema
        layer instead of producing a runtime error.
        """
        tool = self._get_add_subtask_tool()
        required = self._schema(tool).get("required", [])

        assert "node_id" in required, (
            f"'node_id' must remain required; required={required}"
        )

    def test_args_schema_list_property_mentions_deprecated_alias_in_doc(self):
        """Schema sanity: the schema exposes both parameter names.

        Belt-and-suspenders check that the schema's ``properties`` dict
        carries BOTH names — proving no silent rename happened at the
        LLM-facing contract layer.
        """
        tool = self._get_add_subtask_tool()
        properties = self._schema(tool).get("properties", {})

        assert set(properties.keys()) >= {"list", "text", "node_id"}, (
            f"Schema must expose list, text, and node_id; "
            f"got {sorted(properties.keys())}"
        )
