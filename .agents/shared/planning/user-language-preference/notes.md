# Working Notes

## Exploration Summary (2026-07-12)

### System Prompt Assembly
- `compose_system_prompt()` at `daemon/loader.py:329` — 11 sections joined with `\n\n---\n\n`
- `load_and_cache_prompt()` at `daemon/loader.py:568` — caches by `agent_id + sorted_mcp_names`
- `PromptCache` at `daemon/loader.py:500` — key = `{agent_id}::{csv_mcp_names}`, mtime-based invalidation
- Post-processing in `daemon/services/instance_lifecycle.py`:
  - `append_context_key()` at line 171
  - `append_current_time()` at line 205
  - Spawn path: lines 535-541 (load → context_key → current_time → build_instance_graph at 570)
  - Restore path: lines 1492-1498 (load → context_key → current_time → build_instance_graph at 1561)

### LangGraph Structure
- `StateGraph(SessionState)` in `daemon/graph.py`
- `SessionState` at line 327 — extends `MessagesState`, adds `compacted_at: str | None`
- `should_continue()` at line 338 — routes: "tools", "agent" (ghost promise), "nudge" (empty after tool), END (line 387)
- `build_instance_graph()` at line 654 — 3 nodes + conditional edges
- `nudge_node()` at line 417 — injects `HumanMessage(NUDGE_MESSAGE)`
- `create_agent_node()` at line 426 — factory closure, bakes `system_prompt` into closure
- `_has_recent_tool_result()` at line 399 — scans `reversed(messages[:-1])` stopping at HumanMessage boundary (PATTERN REUSED for skip detection)
- Graph invoked via `graph.astream()` (streaming, line 1920) and `graph.ainvoke()` (send_message, line 686)
- `stream_mode=["updates"]` — each event is `{"<node_name>": <state_update>}`

### Streaming Pipeline
- `daemon/services/instance_messaging.py:1920-1989` — the streaming loop
- Progressive dispatch: AIMessages from "agent" node dispatched IMMEDIATELY at line 1958 via `dispatch_message()`
- Messages from ALL nodes accumulated at lines 1967-1979
- **C1 ISSUE**: Final AIMessage dispatched before `language_check` runs → user sees wrong answer then correction
- Dedup by message ID at lines 1940-1944

### Tool Registration
- `create_instance_tools()` at `daemon/tools/instance.py:541` — assembles tools in layers
- Pattern: `@register_tool()` + `@tool` or `@register_tool_category()` + `@tool`
- `CATEGORY_MODULES` dict in `daemon/tools/_tool_registry.py`
- Tools added BEFORE help tool creation (line 1067) for `tool_help` visibility
- `scan_tools_for_full_docs(tools)` runs after all tools are added (line 1073)

### Backend API & Storage
- Routers in `daemon/routers/`, registered in `daemon/api.py` via `api_router.include_router()`
- NO existing settings/preferences endpoint
- `ProjectMetadataRecord` table at `daemon/repositories/project/models.py:170`
  - Fields: `id`, `project_id`, `meta_key`, `meta_value` (JSONB), `created_at`, `updated_at`
  - UniqueConstraint on `(project_id, meta_key)`
- `SQLModelProjectRepository` methods (repository.py:788-861):
  - `get_metadata_record(session, project_id, key)` → `ProjectMetadataRecord | None`
  - `set_metadata_record(session, project_id, key, value)` → `ProjectMetadataRecord` (upsert)
  - `set_metadata(project_id, key, value)` → `Project | None` (wrapper with session)
  - `delete_metadata(project_id, key)` → `Project | None`
  - `list_metadata_records(session, project_id)` → list
- `SYSTEM_DEFAULT_PROJECT_ID` in `daemon/constants.py:88` — set at startup by `ensure_system_default_project()` in `daemon/api.py:467`
- Config loaded from `config.yaml` via `load_config()` in `daemon/config.py` — Pydantic `BaseSettings` pattern
- No user/auth system exists — no User model, no Session table, no auth middleware

### Frontend
- Angular standalone components, routes in `frontend/src/app/app.routes.ts`
- Settings gear menu in `frontend/src/app/app.ts:52` — `settingsMenuItems` signal
- Services in `frontend/src/app/services/` — `ApiService`, `ProjectService`, etc.
- Pages: `home/`, `instances/`, `chat/`, `jobs/`, `schedules/`, `skills/`
- No existing settings page

### Test Landscape (Verified)
- `tests/unit/test_nudge_behavior.py` — EXISTS (445 lines, 18,904 bytes). Tests `should_continue()`, `_is_empty_content`, `_has_recent_tool_result`, `nudge_node`, and `build_instance_graph`. Contains 4 assertions at lines 156, 183, 195, 237 asserting `should_continue(state) == "__end__"`. **NOT broken** — `should_continue()` is NOT modified (closure wrapper handles routing).
  - Line 156: `test_empty_content_no_tool_result_returns_end` — `assert should_continue(state) == "__end__"`
  - Line 183: `test_normal_content_returns_end` — `assert should_continue(state) == "__end__"`
  - Line 195: `test_tool_result_separated_by_human_message_returns_end` — `assert should_continue(state) == "__end__"`
  - Line 237: `test_response_with_reasoning_and_content_returns_end` — `assert should_continue(state) == "__end__"`
  - **Action**: None needed — `should_continue()` is NOT modified, closure wrapper handles routing
- `tests/test_graph.py` — tests `clean_llm_config` only (48 lines). Does NOT test `should_continue()`
- `tests/conftest.py:27` — sets `END = "__end__"` for mock langgraph
- `tests/test_progressive_dispatch.py` — mocks `build_instance_graph`, tests streaming pipeline (18 test cases)
- `tests/test_manager.py` — mocks `build_instance_graph`, tests spawn/restore flows (many test cases)
- `tests/test_spawn_limit_edge_cases.py` — mocks `build_instance_graph` (8 test cases)
- `test_nudge_in_conditional_edges_mapping` (line 390) checks `"nudge"` is in routing dict — NOT broken (nudge still present)
- Tests that mock `build_instance_graph` use `patch('daemon.manager.build_instance_graph', return_value=mock_graph)` — adding `user_language` kwarg with default `"English"` is backward-compatible (mocks ignore extra kwargs)

### Graph Modification Impact
- `should_continue()` is NOT modified — a closure wrapper `create_should_continue(language_check_enabled)` inside `build_instance_graph()` handles the `END → "end_candidate"` routing change
- The 4 existing test assertions in `tests/unit/test_nudge_behavior.py` (lines 156, 183, 195, 237) test the original `should_continue()` directly and remain valid
- Adding `user_language: str = "English"` and `language_check_enabled: bool = True` to `build_instance_graph()` is backward-compatible with all existing mock patches
- The conftest mock `END = "__end__"` is a string — `should_end_language_check()` returning `END` (which is `"__end__"`) will work correctly in both real and test environments

### Import Structure
- `daemon/services/language_utils.py` — new shared utility module (W3 fix)
  - Imported by `daemon/routers/settings.py` (router layer) ✓
  - Imported by `daemon/services/instance_lifecycle.py` (service layer) ✓
  - No circular dependency — `language_utils.py` only imports from `daemon.constants` and `sqlmodel`
- `daemon/language_detection.py` — new detection module
  - Imported by `daemon/graph.py` (late import inside `language_check_node`)
  - No circular dependency — standalone module with no daemon imports

## Reviewer Feedback (v2 — 2026-07-12)

### Critical Fixes Applied
- **C1**: Streaming deferred dispatch — buffer final AIMessages when `language_check_active` (config flag, NOT `user_language != "English"` — revised in v4 for ISSUE 1), dispatch only at END
- **C2**: Removed English short-circuit — `detect_wrong_language()` handles English detection (CJK + Spanish drift)
- **C3**: Both spawn (line 570) AND restore (line 1561) call sites pass `user_language` to `build_instance_graph()`
- **C4**: Dropped `SessionState.language_skip_check` — dead state. Use message history scan only
- **C5**: `should_end_language_check()` uses `state.get("language_check_retry")` — NOT type-sniffing

### Warnings Applied
- **W1**: Cleaned Spanish word list (removed ambiguous English-valid words). Threshold raised to 50%. Min 5 absolute words
- **W2**: `tests/unit/test_nudge_behavior.py` EXISTS (445 lines) with 4 assertions at lines 156, 183, 195, 237 asserting `should_continue(state) == "__end__"`. **NOT broken** — `should_continue()` is NOT modified. A closure wrapper `create_should_continue(language_check_enabled)` inside `build_instance_graph()` handles the `END → "end_candidate"` routing change. The tests exercise the original `should_continue()` directly and remain valid.
- **W3**: Created `daemon/services/language_utils.py` — shared utility in service layer
- **W4**: `detect_wrong_language()` normalizes content (handles list/multimodal). Wrapped in try/except in node

### Suggestions Applied
- **S1**: Merged Phase 2 + Phase 3 → "Phase 2: Graph Integration" (3 phases total)
- **S2**: Added `language_check_enabled` config flag (default True). Documented LLM cost impact (3× worst case)
- **S3**: Documented recursion budget (+5 nodes worst case vs 100-step limit = 5% budget, safe)
- **S5**: Counter resets when new HumanMessage detected in message history

## Open Questions
1. Should the `language_check` node's injected `HumanMessage` (reminder) be visible to the user in the frontend? (Currently, nudge messages are not displayed — the language reminder should follow the same pattern. The deferred dispatch fix means the user never sees the wrong-language response OR the reminder — only the final corrected response.)
2. Should the `language_check_enabled` flag be per-project or global? (Currently global in config. Could be extended to per-project via `ProjectMetadataRecord` in the future.)
3. What happens if `language_check` node adds a reminder but the graph hits `recursion_limit` before the agent can respond? (The graph will raise a recursion error. The counter cap at 2 retries makes this unlikely — max 5 extra nodes per turn vs 100-step limit.)

## Blocking Fixes Applied (v4 — ISSUE 1 + ISSUE 2)

- **ISSUE 1 (C1/C2 contradiction)**: C1 streaming deferred dispatch predicate changed from `user_language != "English"` to `language_check_active` (config flag `language.check_enabled`). All users with language check enabled get deferred dispatch — including English users who benefit from CJK/Spanish drift detection (C2). Resolves the contradiction where English users would get immediate dispatch but still be language-checked.
- **ISSUE 2 (should_continue + config flag wiring)**: `should_continue()` is NOT modified. Instead, `create_should_continue(language_check_enabled)` closure factory inside `build_instance_graph()` captures the flag. When enabled, wraps `should_continue()` and replaces `END → "end_candidate"`. When disabled, uses original function. Conditional edges mapping also built conditionally. The 4 existing test assertions in `tests/unit/test_nudge_behavior.py` remain valid — they test the original function directly.
- **W2 consequence**: Since `should_continue()` is unchanged, no test assertions need updating. The previous W2 fix (updating 4 assertions) is no longer needed.

## Minor Clarifications (W-A / W-B / W-C)

### W-A: `ainvoke` Path Unaffected by C1 Fix
The `ainvoke` path at `instance_messaging.py:688` (`result = await graph.ainvoke({"messages": [message]}, config)`) does NOT do progressive dispatch — it returns the full final state. The C1 deferred dispatch fix applies ONLY to the `astream` path (lines 1920+). No changes needed on the `ainvoke` path.

### W-B: Deferred Message Buffer Overwrite
In the C1 streaming fix pseudocode, `_deferred_final_message` is overwritten on each new agent AIMessage. This means retries naturally replace the wrong-language buffer:
1. Agent produces wrong-language response → buffered in `_deferred_final_message`
2. `language_check` detects wrong language → injects reminder → `language_check_retry=True`
3. Graph routes back to "agent" → agent produces new (corrected) response
4. New AIMessage overwrites `_deferred_final_message` (replacing the wrong-language version)
5. `language_check` passes → graph reaches END → `_deferred_final_message` (now the correct response) is dispatched

### W-C: Counter Reset via `additional_kwargs` Marker (Replaces Content-Prefix Matching)
Instead of string-matching the `LANGUAGE_REMINDER_TEMPLATE` prefix to detect injected reminders, the reminder HumanMessage is tagged with `additional_kwargs={"language_check_reminder": True}`:

```python
reminder = HumanMessage(
    content=LANGUAGE_REMINDER_TEMPLATE.format(language=user_language),
    additional_kwargs={"language_check_reminder": True},
)
```

The counter reset logic in `language_check_node` checks for this marker:
```python
for msg in reversed(messages[:-1]):
    msg_type = getattr(msg, 'type', None)
    if msg_type == 'human':
        # Check if this HumanMessage is our injected reminder or a new user message
        if not getattr(msg, 'additional_kwargs', {}).get('language_check_reminder', False):
            count = 0  # New user message, reset counter
        break
```

This is more robust than content-prefix string matching — no false positives from user messages that happen to contain the reminder template text.
