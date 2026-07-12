# Architecture Decisions

## D1: Storage Mechanism — ProjectMetadataRecord vs New Table

**Decision**: Use existing `ProjectMetadataRecord` table on the system default project.

**Rationale**:
- A dedicated key-value metadata table already exists (`daemon/repositories/project/models.py:170`)
- CRUD methods already implemented: `set_metadata()`, `get_metadata_record()`, `list_metadata_records()`
- Dialect-aware upsert (SQLite + PostgreSQL) already handled
- `SYSTEM_DEFAULT_PROJECT_ID` is bootstrapped at startup — provides a natural "global" scope
- No migration needed — table already exists
- Avoids schema changes and `_ensure_postgres_columns()` additions

**Alternative considered**: New `UserPreferences` table with columns like `user_id`, `language`, `timezone`, etc.
- Rejected: No user/auth system exists. Over-engineering for a single preference. Adds migration burden.

---

## D2: Language Injection — Post-Processing vs compose_system_prompt

**Decision**: Post-processing via `append_user_language()` (same pattern as `append_context_key` and `append_current_time`).

**Rationale**:
- System prompt is cached by `PromptCache` keyed on `agent_id + mcp_tool_names` with mtime invalidation
- If we inject language INTO `compose_system_prompt()`, we'd need to add language to the cache key — invalidating ALL cached prompts when language changes
- Post-processing runs per-spawn, AFTER cache lookup — language changes only affect NEW instances, not cached prompts
- This matches the established pattern for per-instance dynamic content (Context Key, Current Time)

**Alternative considered**: Add language as a 12th section in `compose_system_prompt()`
- Rejected: Would require cache key changes and invalidate all prompts on language change.

---

## D3: Graph Node Placement — Between should_continue and END

**Decision**: Use a closure factory `create_should_continue(language_check_enabled)` inside `build_instance_graph()` that conditionally replaces `END → "end_candidate"` when enabled. `should_continue()` itself is NOT modified.

**Rationale** (revised for ISSUE 2):
- We can't insert a node between `should_continue()` and `END` without changing the return value
- The `language_check` node is the ONLY new routing target — all other branches (tools, agent, nudge) remain unchanged
- The node only activates for final responses (no tool calls) — intermediate turns bypass it entirely
- A second conditional (`should_end_language_check`) handles the retry-vs-end decision

**Alternative considered**: Check language inside `should_continue()` itself
- Rejected: `should_continue()` is a pure routing function — adding message modification (injecting reminder) violates separation of concerns. Also, `should_continue` returns a string, not state updates.

**Alternative considered** (ISSUE 2): Modify `should_continue()` to return `"end_candidate"` directly
- Rejected: `should_continue()` is a module-level function at `daemon/graph.py:338` with no config parameter, no closure access, no `RunnableConfig`. It cannot check `language_check_enabled`. Additionally, `graph.add_conditional_edges("agent", should_continue, {...})` takes the function reference at compile time — the mapping must match. Modifying the function would break the 4 existing test assertions in `tests/unit/test_nudge_behavior.py` (lines 156, 183, 195, 237). The closure wrapper avoids both problems.

---

## D4: Skip Flag Propagation — Message History Scanning Only

**Decision**: Message history scanning only. `SessionState.language_skip_check` is NOT used.

**Rationale** (revised after reviewer feedback C4):
- The `language_skip_check` tool returns a string — it cannot directly set graph state
- `SessionState.language_skip_check` would be dead state: always `False`, never set to `True` by any code path
- Instead, the `language_check_node` scans `reversed(messages[:-1])` for a `ToolMessage` with `name="language_skip_check"`, stopping at HumanMessage boundary
- This is the same pattern as `_has_recent_tool_result()` at `graph.py:399-414`
- No state field needed — the scan runs fresh every time the node executes

**Previous approach** (rejected): Dual approach with `SessionState.language_skip_check` boolean + message scanning
- Rejected: The state field is dead code. It exists only as checkpoint serialization tax with no writer.

---

## D5: Retry Routing — Explicit State Flag (NOT Type-Sniffing)

**Decision**: `language_check_node` returns `language_check_retry: True` when injecting a reminder. `should_end_language_check()` checks `state.get("language_check_retry", False)`.

**Rationale** (revised after reviewer feedback C5):
- Previous approach checked `last_message.type == 'human'` — fragile, assumes the ONLY HumanMessage arriving at `language_check` is the injected reminder
- Also had dead code: a secondary check by content that was redundant
- Explicit state flag is deterministic, testable, and doesn't depend on message type
- `SessionState.language_check_retry: bool = False` is added to the state schema

**Previous approach** (rejected): Type-sniffing on `last_message.type == 'human'`
- Rejected: Fragile. Any HumanMessage from any source would trigger a retry. Dead code in the secondary check.

---

## D6: Language Detection — Heuristic Only (No LLM)

**Decision**: Pure Python regex + set-based detection. No LLM calls for language detection.

**Rationale**:
- CJK character detection is 100% accurate via Unicode range matching
- Spanish detection uses a curated set of unambiguously Spanish words (W1 fix) with a 50% threshold and ≥5 absolute count minimum
- Fast, deterministic, no API cost, no latency
- Code blocks are stripped before detection to avoid false positives
- Multimodal content (list-type) is normalized to string before detection (W4 fix)

**Limitations**:
- Only English, Chinese (CJK), and Spanish are detected
- Other languages (French, German, etc.) skip the check entirely
- The system prompt instruction (`User prefer language: X`) is the primary enforcement mechanism — the check node is a backstop, not the primary gate

**Spanish Word List** (W1 fix): Excluded ambiguous words that are valid English: `'no'`, `'a'`, `'en'`, `'con'`, `'sin'`, `'si'`, `'lo'`, `'al'`, `'que'`, `'y'`. Kept only unambiguously Spanish words like `'porque'`, `'cuando'`, `'donde'`, `'hacer'`, `'tener'`, etc.

**Alternative considered**: Use an LLM to classify the language
- Rejected: Adds latency, cost, and non-determinism to every final response. Overkill for a heuristic check.

---

## D7: English Preference — Detection IS Active

**Decision**: English preference triggers language detection (does NOT short-circuit).

**Rationale** (revised after reviewer feedback C2):
- The feature must work for English users — agents can drift into Chinese or Spanish even when the user prefers English
- `detect_wrong_language()` has a fully-implemented English branch: CJK char detection + Spanish word ratio
- The `language_check_node` does NOT have a `if user_language.lower() == "english": return` short-circuit
- This means an agent responding in Chinese when the user prefers English WILL be caught and corrected

**Previous approach** (rejected): Short-circuit for English (`if user_language.lower() == "english": return {}`)
- Rejected: Makes the feature dead code for the majority of users (English is the default). The `detect_wrong_language()` English branch would never be reached.

---

## D8: Streaming Deferred Dispatch — Buffer Final AIMessages

**Decision**: When language check is active (`language_check_enabled=True`), the streaming pipeline buffers final AIMessages (content, no tool_calls) instead of dispatching immediately. The buffered message is dispatched only when the graph reaches END.

**Rationale** (reviewer feedback C1, revised for ISSUE 1):
- The streaming pipeline at `instance_messaging.py:1929-1958` dispatches AIMessages from the "agent" node immediately via `dispatch_message()`
- Without buffering, the user sees the wrong-language answer first, then gets a corrected answer — confusing UX
- By buffering, the user only sees the final, language-checked response
- Trade-off: adds latency (one graph step) for final messages when language check is active
- Acceptable because the feature is opt-in via config flag
- If `language_check` injects a retry, the buffered message is discarded — the agent produces a new response

**ISSUE 1 FIX**: The buffering predicate is `language_check_active` (derived from `self._config.language.check_enabled`), NOT `user_language != "English"`. This resolves the C1/C2 contradiction:
- C2 fix (D7) made detection active for ALL languages including English (agent drifting into Chinese when user prefers English IS caught)
- If C1 used `user_language != "English"`, English users would get immediate dispatch but still be language-checked → double-dispatch for English users
- By tying the predicate to the config flag, ALL users with language check enabled get deferred dispatch — consistent behavior

**Implementation**: The streaming loop checks `language_check_active AND not has_tool_calls` — if both true, the message is stored in `_deferred_final_message` instead of dispatched. After the streaming loop completes (graph reached END), the deferred message is dispatched.

---

## D9: Shared Utility Module — Service Layer

**Decision**: `get_language_preference()` lives in `daemon/services/language_utils.py` (service layer), imported by both the router and the lifecycle service.

**Rationale** (reviewer feedback W3):
- Previous approach had `instance_lifecycle.py` (service layer) importing from `daemon.routers.settings` (router layer) — import inversion
- Codebase layering: routers depend on services, not the other way around
- `daemon/services/language_utils.py` is the shared dependency that both layers import
- The router's `GET /settings/language` endpoint calls `get_language_preference(_project_repo)`
- The lifecycle service's spawn/restore paths call `get_language_preference(project_repository)`

---

## D10: Config Flag — `language_check_enabled` + Closure Wiring

**Decision**: Add `language_check_enabled: bool = True` to config (new `LanguageConfig` section). Wire it via a closure factory in `build_instance_graph()`, NOT by modifying `should_continue()`.

**Rationale** (reviewer feedback S2 + ISSUE 2):
- Each language check retry = one extra LLM call (3× worst case per turn)
- Some deployments may not want the overhead
- `should_continue()` at `daemon/graph.py:338` is a module-level function with signature `(state: MessagesState) -> str` — no config parameter, no closure access, no `RunnableConfig`
- `graph.add_conditional_edges("agent", should_continue, {...})` at line 709 takes the function reference at compile time — it must be the right function before the graph is compiled
- **Solution**: `create_should_continue(language_check_enabled)` closure factory inside `build_instance_graph()`. When enabled, wraps `should_continue()` and replaces `END → "end_candidate"`. When disabled, passes the original `should_continue` directly.
- The conditional edges mapping is also built conditionally — `"end_candidate": "language_check"` only included when the node exists (LangGraph requires all mapping keys to have corresponding target nodes)
- The `language_check` node is only added when enabled

**When disabled**: the graph is identical to the current (pre-feature) graph — no `language_check` node, `should_continue` returns `END` directly, no deferred dispatch. Zero overhead. The system prompt injection (`append_user_language`) still happens — the instruction is always present, only the enforcement check is toggleable.

**W2 consequence**: Since `should_continue()` is NOT modified, the 4 existing test assertions in `tests/unit/test_nudge_behavior.py` (lines 156, 183, 195, 237) still pass without modification. The tests exercise the original `should_continue()` function directly — the closure wrapper is only used inside `build_instance_graph()`.

**Environment variable**: `LANGUAGE_CHECK_ENABLED=false`

---

## D11: Counter Reset on New User Message

**Decision**: `language_check_count` resets to 0 when a new HumanMessage is detected in the message history. Reminder messages are identified via `additional_kwargs={"language_check_reminder": True}` marker (W-C fix).

**Rationale** (reviewer feedback S5 + W-C):
- If the graph is interrupted mid-retry (e.g., daemon restart, crash), the counter persists at a non-zero value in the checkpoint
- On resume with a new user message, the next turn would start with a stale counter — potentially hitting the max-retries limit prematurely
- The `language_check_node` scans `reversed(messages[:-1])` for HumanMessage. The injected reminder is tagged with `additional_kwargs={"language_check_reminder": True}`. If the last HumanMessage does NOT have this marker, it's a new user message → reset counter to 0
- **W-C**: More robust than content-prefix string matching — no false positives from user messages that happen to contain the reminder template text
