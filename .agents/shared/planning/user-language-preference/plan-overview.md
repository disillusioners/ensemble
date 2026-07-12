# Plan Overview: User Language Preference

## Objective
Allow users to set a preferred language (default English) via a backend API + frontend UI. The setting is injected into every agent's system prompt at spawn time, and a new LangGraph functional node checks each LLM assistant message against the preferred language — injecting a correction reminder if the agent responds in the wrong language. A `language_skip_check()` tool lets agents opt out for specific messages (e.g., translation files, multilingual READMEs).

## Scope Assessment
**LARGE** — Touches 4 subsystems: backend API + storage, system prompt assembly + LangGraph graph structure (merged), and frontend UI. Requires new API router, new graph node, new tool, new language detection module, and new Angular page. Estimated 1-2 days of developer work.

## Context
- Project: agents-ensemble
- Working Directory: /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble
- Branch: feature/user-language-preference (new branch from latest)

## Key Architecture Findings

### System Prompt Assembly
- `compose_system_prompt()` in `daemon/loader.py:329` — assembles 11 sections from markdown files, joined with `\n\n---\n\n`
- `load_and_cache_prompt()` in `daemon/loader.py:568` — caches by `agent_id + sorted_mcp_names` with mtime invalidation
- `PromptCache` at `daemon/loader.py:500` — in-memory dict, key = `{agent_id}::{csv_mcp_names}`
- Post-processing in `daemon/services/instance_lifecycle.py`:
  - `append_context_key()` at line 171 — appends Context Key section (NOT cached)
  - `append_current_time()` at line 205 — appends Current Time section (NOT cached)
  - **Injection point for user language**: add `append_user_language()` in the same post-processing pipeline
  - Spawn path: lines 535-541 (prompt load → append_context_key → append_current_time)
  - Restore path: lines 1492-1498 (same sequence)
- System prompt is baked into `agent_node` closure at graph-build time — immutable for instance lifetime
- **Both spawn (line 570) AND restore (line 1561) call `build_instance_graph()`** — both must receive `user_language`

### LangGraph Structure (`daemon/graph.py`)
- `StateGraph(SessionState)` with 3 nodes: "agent", "tools", "nudge"
- `SessionState` at line 327 — extends `MessagesState`, adds `compacted_at: str | None`
- `should_continue()` at line 338 — routes to "tools"/"agent"/"nudge"/END. **NOT modified** — a closure wrapper `create_should_continue()` in `build_instance_graph()` handles the `END → "end_candidate"` routing change when language check is enabled
- `build_instance_graph()` at line 654 — compiles graph with checkpointer
- Flow: START → agent → (conditional) → tools/nudge → agent → ... → END
- **New node**: `language_check` inserted between "agent" output and END routing
- `should_continue()` final `return END` — **NOT modified**. A closure wrapper `create_should_continue()` in `build_instance_graph()` replaces `END → "end_candidate"` when language check is enabled

### Streaming Pipeline (`daemon/services/instance_messaging.py`)
- `graph.astream(graph_input, config, stream_mode=["updates"])` at line 1920
- Progressive dispatch: AIMessages from "agent" node dispatched IMMEDIATELY at line 1958 via `dispatch_message()`
- Messages from ALL nodes accumulated at lines 1967-1979
- **Critical**: When `language_check` is active, final AIMessages must NOT be dispatched until the graph reaches END — otherwise user sees wrong-language answer, then corrected answer

### Tool Registration (`daemon/tools/instance.py`)
- `create_instance_tools()` at line 541 — assembles all tools in layers
- Pattern: `@register_tool()` + `@tool` decorator, or `@register_tool_category()` + `@tool`
- Tools added to list BEFORE help tool creation (line 1067) so they show in `tool_help`
- `CATEGORY_MODULES` dict in `daemon/tools/_tool_registry.py` maps category names to modules

### Backend API & Storage
- Routers in `daemon/routers/`, registered in `daemon/api.py` via `api_router.include_router()`
- No existing settings/preferences endpoint
- `ProjectMetadataRecord` table at `daemon/repositories/project/models.py:170` — key-value store per project
- `SQLModelProjectRepository` has `set_metadata()`, `get_metadata_record()`, `list_metadata_records()` methods
- `SYSTEM_DEFAULT_PROJECT_ID` in `daemon/constants.py:88` — bootstrapped at startup in `daemon/api.py:467`
- Config loaded from `config.yaml` via `load_config()` in `daemon/config.py`
- **No user/auth system** — no User model, no Session table, no auth middleware

### Frontend
- Angular standalone components, routes in `frontend/src/app/app.routes.ts`
- Settings menu in `frontend/src/app/app.ts:52` — `settingsMenuItems` signal
- Services in `frontend/src/app/services/` — `ApiService` for HTTP calls
- No existing settings page

### Test Landscape
- `tests/unit/test_nudge_behavior.py` — EXISTS (445 lines). Tests `should_continue()` with 4 assertions at lines 156, 183, 195, 237 asserting `== "__end__"`. **NOT broken** — `should_continue()` is NOT modified; a closure wrapper in `build_instance_graph()` handles the routing change.
- `tests/test_graph.py` — tests `clean_llm_config` only (NOT `should_continue`)
- `tests/conftest.py:27` — sets `END = "__end__"` for mock langgraph
- `tests/test_progressive_dispatch.py` — mocks `build_instance_graph`, tests streaming pipeline
- `tests/test_manager.py` — mocks `build_instance_graph`, tests spawn/restore flows
- Tests that mock `build_instance_graph` pass kwargs through — adding `user_language` and `language_check_enabled` kwargs with defaults is backward-compatible

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Backend API + Storage | Create `/api/settings/language` endpoint with GET/PUT, stored via `ProjectMetadataRecord` on system default project. Create `daemon/services/language_utils.py` for shared `get_language_preference()` | None | — | 3h |
| 2 | Graph Integration | System prompt injection (`append_user_language()`) + `language_check` graph node + `language_skip_check` tool + deferred dispatch in streaming pipeline + language detection module | Phase 1 | tight (merged from original Phases 2+3) | 7h |
| 3 | Frontend UI | Language selector dropdown in settings page, saves to backend | Phase 1 | loose | 3h |

### Coupling Assessment

| Phase Pair | Coupling | Reasoning |
|------------|----------|-----------|
| 1 → 2 | loose | Phase 2 needs `get_language_preference()` from `daemon/services/language_utils.py` (created in Phase 1). Interface only, not implementation |
| 1 → 3 | loose | Phase 3 calls the API from Phase 1 (GET/PUT `/api/settings/language`). Different codebase entirely (Angular vs Python) |
| 2 → 3 | independent | Frontend doesn't interact with the graph node or tool at all |

**Parallelization**: Phase 3 (frontend) can start as soon as Phase 1 API contract is defined. Phase 2 must wait for Phase 1.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Language detection false positives (e.g., code snippets with Chinese comments) | medium | Only check the final assistant message CONTENT (not tool calls). Strip code blocks before detection. CJK = any char match; Spanish = unambiguous words only, ≥50% ratio, ≥5 absolute count |
| Graph recursion limit hit by language correction loop | low | `language_check_count` in SessionState caps retries at 2. Worst case: +5 nodes per turn vs 100-step `graph_recursion_limit` — safe (5% budget) |
| `language_skip_check` tool cannot set graph state | medium | Detect tool call by scanning `reversed(messages[:-1])` for `ToolMessage` with `name="language_skip_check"`, stopping at HumanMessage boundary (same pattern as `_has_recent_tool_result` at graph.py:399) |
| Prompt cache not invalidating when language changes | non-issue | Language injection is in post-processing (NOT cached) — same as Context Key and Current Time. Cache is NOT affected |
| System default project not existing in older deployments | low | `ensure_system_default_project()` runs at startup (api.py:467). If it fails, language defaults to English |
| Language check node interfering with tool-call flow | high | Only check when `should_continue()` would return `"end_candidate"` (final message, no tool calls). If tool_calls present, skip language check entirely |
| Postgres JSONB vs SQLite JSON for metadata value | low | `ProjectMetadataRecord` already handles this via `JSONBType` and `_get_dialect_insert()` |
| **LLM cost impact** — each retry = one extra LLM call | medium | Max 2 retries = 3× worst case per turn. Add `language_check_enabled: bool` config flag (default True) to allow disabling. Document in config.yaml |
| **Streaming double-dispatch** — user sees wrong answer then corrected answer | high | Defer progressive dispatch of final AIMessages when `language_check_active` (config flag) until graph reaches END. Predicate is `language_check_active`, NOT `user_language != "English"` — resolves C1/C2 contradiction where English users would get immediate dispatch but still be language-checked |
| **`should_continue()` has no config access** — module-level function can't check flag | high | Use closure factory `create_should_continue(language_check_enabled)` inside `build_instance_graph()`. When enabled, wraps `should_continue()` and replaces `END → "end_candidate"`. When disabled, uses original function. `should_continue()` itself is NOT modified — existing tests pass unchanged |
| **Multimodal content crash** — `content` can be a list | medium | Wrap `detect_wrong_language()` in try/except. Normalize content to string before detection. On error, allow response through |
| **Counter persistence across turns** — stale `language_check_count` on resume | low | Reset `language_check_count` to 0 when a new HumanMessage is detected in the message history (new user message = new turn) |
| **Service → router import inversion** | medium | `get_language_preference()` lives in `daemon/services/language_utils.py` (service layer), imported by both router and lifecycle service |

## Success Criteria
- [ ] User can set language preference via `PUT /api/settings/language` with `{"language": "Spanish"}`
- [ ] User can retrieve language preference via `GET /api/settings/language`
- [ ] All newly spawned agents have `User prefer language: [Language]` in their system prompt (both spawn AND restore paths)
- [ ] Agent responding in Chinese when preference is English gets a correction reminder injected
- [ ] Agent responding in Spanish (≥50% ratio, ≥5 words) when preference is English gets a correction reminder
- [ ] English preference DOES trigger checks (detects CJK/Spanish drift into non-English)
- [ ] `language_skip_check()` tool skips the next language check only
- [ ] Language check only applies to final assistant messages (not tool-call turns)
- [ ] Language check counter prevents infinite correction loops (max 2 retries)
- [ ] Language check counter resets on new user message
- [ ] Streaming pipeline does NOT dispatch wrong-language final AIMessage before language_check runs (predicate: `language_check_active` config flag, NOT `user_language != "English"`)
- [ ] Multimodal content (list-type) does not crash the language check node
- [ ] `should_continue()` is NOT modified — closure wrapper `create_should_continue()` handles routing change
- [ ] When `language_check_enabled=False`, graph is identical to pre-feature behavior (no `language_check` node, original `should_continue`, no deferred dispatch)
- [ ] Frontend settings page has a language dropdown that saves to backend
- [ ] `language_check_enabled` config flag can disable the feature
- [ ] All existing tests pass (no regressions — `tests/unit/test_nudge_behavior.py` unchanged, `should_continue()` unmodified)
- [ ] New tests cover: API CRUD, prompt injection (spawn + restore), graph node routing, language detection, skip flag, deferred dispatch, counter reset

## Tracking
- Created: 2026-07-12
- Last Updated: 2026-07-12 (reviewer feedback v4 — ISSUE 1 C1/C2 contradiction + ISSUE 2 should_continue closure)
- Status: draft (v4 — post-blocking-fix)
