# Coverage gap: no committed ToolNode happy-path test for ask_questions

**Date:** 2026-09-06 · **Found by:** W5 mock audit (`f6a59836`) during the ask-questions format-validation gate
**Branch:** `feature/ask-questions-format-validation` @ `5e4e33b9` · **Severity:** 🟢 nice-to-have (not drift, not a defect)

## Gap

`tests/test_question_tools_validation.py` pins the executor boundary ONLY for the schema-REJECTION branch
(`TestExecutorLayerSchemaRejects::test_args_schema_rejects_before_body_runs`, lines 669-759 — real ToolNode via
the sys.modules unmock pattern + real args schema). There is **no sibling test driving a VALID payload through
`ToolNode.ainvoke`**, so the executor happy-path seam (ToolNode's try/except wrapping, `ToolMessage(status="success")`
rendering of the tool's string result, pause-flag flip under the executor) is unpinned in committed tests. The other
47 tests call `tool.coroutine(...)` directly, skipping that seam.

Live re-execution during the audit and the functional close-out (W4) both confirm the happy path WORKS today
(`ToolMessage(status='success', content='Asked the user: …')`, pause flag flipped, SSE awaited exactly once) —
this is a pin-the-behavior gap, not a bug.

## Repro recipe (turn into a committed test)

Mirror the existing rejection test's unmock pattern (lines 697-704: pop `langgraph.*` from `sys.modules`, reimport
`CONF`/`CONFIG_KEY_RUNTIME`/`ToolNode`/`Runtime`), then:

```python
tool_node = ToolNode([tool], handle_tool_errors=True)
out = await tool_node.ainvoke(
    {"messages": [AIMessage(tool_calls=[{"name": "ask_questions",
        "args": {"questions": [{"text": "Pick", "options": ["A", "B"]}]},
        "id": "x", "type": "tool_call"}])]},
    config={CONF: {CONFIG_KEY_RUNTIME: Runtime()}})
assert out["messages"][0].status == "success"
assert out["messages"][0].content.startswith("Asked the user:")
# + pause flag flipped + stream_question_pack awaited once
```

Restore conftest langgraph mocks in `finally` (same as lines 747-750).

## Why it matters

The schema-rejection pin proves the args schema is a real defense layer; the happy-path pin would prove the tool's
SUCCESS string contract (`"Asked the user: …"`) and side-effect ordering survive executor-layer changes (e.g.
ToolNode upgrades, handle_tool_errors semantics). Cheap to add, prevents silent contract drift.

**Precedent:** same recipe family as `tests/unit/test_mcp_tool_timeout.py::TestToolNodeIntegration` (only other
repo site invoking ToolNode directly).
