# Phase 2: Graph Integration (System Prompt + Language Check Node + Tool + Streaming Fix)

## Objective
Inject `User prefer language: [Language]` into every agent's system prompt at spawn AND restore time. Add a new `language_check` functional node to the LangGraph that intercepts the agent's final response, checks it against the preferred language, and injects a correction reminder if wrong. Add a `language_skip_check()` tool. Fix the streaming pipeline to defer dispatch of final AIMessages until the language check completes.

This phase merges the original Phase 2 (system prompt injection) and Phase 3 (graph node + tool) because both modify the same files (`instance_lifecycle.py`, `graph.py`) at the same lines, and Phase 2's `user_language` parameter is immediately consumed by Phase 3's graph node.

## Coupling
- **Depends on**: Phase 1 (needs `get_language_preference()` from `daemon/services/language_utils.py`)
- **Coupling type**: loose — depends only on the function interface, not implementation
- **Shared files with other phases**: None (Phase 3/frontend is independent)
- **Shared APIs/interfaces**: `get_language_preference() -> str` (from Phase 1's `language_utils.py`)
- **Why this coupling**: This phase reads the stored language at spawn/restore time and passes it through the graph builder to the language check node.

## Context

### System Prompt Post-Processing Pipeline
- Spawn path: `daemon/services/instance_lifecycle.py` lines 535-541:
  ```
  load_and_cache_prompt() → append_context_key() → append_current_time()
  ```
- Restore path: same sequence at lines 1492-1498
- **Both paths then call `build_instance_graph()`**: spawn at line 570, restore at line 1561
- Language injection goes AFTER `append_current_time()` in both paths

### LangGraph Structure (`daemon/graph.py`)
- `StateGraph(SessionState)` with 3 nodes: "agent", "tools", "nudge"
- `SessionState` at line 327 — extends `MessagesState`, adds `compacted_at: str | None`
- `should_continue()` at line 338 — final `return END` at line 387
- `build_instance_graph()` at line 654 — compiles graph with checkpointer
- `_has_recent_tool_result()` at line 399 — scans `reversed(messages[:-1])` stopping at HumanMessage boundary

### Streaming Pipeline (`daemon/services/instance_messaging.py`)
- `graph.astream(graph_input, config, stream_mode=["updates"])` at line 1920
- Progressive dispatch: AIMessages from "agent" node dispatched IMMEDIATELY at line 1958
- Messages from ALL nodes accumulated at lines 1967-1979
- **Problem**: When `language_check` is active, the agent node's final AIMessage is dispatched before `language_check` runs — user sees wrong-language answer, then corrected answer

### Test Landscape
- `tests/unit/test_nudge_behavior.py` — EXISTS (445 lines). Tests `should_continue()` with 4 assertions at lines 156, 183, 195, 237 asserting `== "__end__"`. **These are NOT broken** because `should_continue()` itself is NOT modified — the graph uses a closure wrapper instead (see Task 10).
- `tests/test_graph.py` — tests `clean_llm_config` only (NOT `should_continue`)
- `tests/conftest.py:27` — sets `END = "__end__"` for mock langgraph
- Tests that mock `build_instance_graph` (test_manager.py, test_progressive_dispatch.py, test_spawn_limit_edge_cases.py) pass kwargs through — adding `user_language` and `language_check_enabled` kwargs with defaults is backward-compatible

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `append_user_language()` function | New function in `daemon/services/instance_lifecycle.py` (near `append_context_key` at line 171). Signature: `append_user_language(system_prompt: str, language: str) -> str`. Appends `\n---\n\n## User Language Preference\n\nUser prefer language: {language}\n` | `daemon/services/instance_lifecycle.py` (MODIFY) |
| 2 | Inject language in spawn path | After `append_current_time(system_prompt)` at line 541: read `user_language = get_language_preference(project_repository)`, then `system_prompt = append_user_language(system_prompt, user_language)` | `daemon/services/instance_lifecycle.py` (MODIFY, ~line 542) |
| 3 | Inject language in restore path | After `append_current_time(system_prompt)` at line 1498: same injection as spawn path. **C3 FIX**: restore path at line 1498 must also inject language — previously missed | `daemon/services/instance_lifecycle.py` (MODIFY, ~line 1499) |
| 4 | Pass `user_language` to `build_instance_graph()` — BOTH call sites | Spawn path (line 570) AND restore path (line 1561) must pass `user_language=user_language`. **C3 FIX**: restore path at line 1561 was missing this parameter | `daemon/services/instance_lifecycle.py` (MODIFY, lines 570 + 1561), `daemon/graph.py` (MODIFY) |
| 5 | Update `build_instance_graph()` signature | Add `user_language: str = "English"` parameter to `build_instance_graph()` in `daemon/graph.py:654` | `daemon/graph.py` (MODIFY) |
| 6 | Extend `SessionState` | Add `language_check_count: int = 0` and `language_check_retry: bool = False` fields. **C4 FIX**: Do NOT add `language_skip_check` — it's dead state (tool can't set it). **C5 FIX**: Add `language_check_retry` for reliable retry routing instead of type-sniffing | `daemon/graph.py` (MODIFY, ~line 327) |
| 7 | Create language detection module | New `daemon/language_detection.py` with `detect_wrong_language(content: str, preferred_language: str) -> bool`. **W1 FIX**: Spanish word list uses only unambiguously Spanish words (no `'no'`, `'a'`, `'en'`, `'con'`, `'sin'`, `'si'`, `'lo'`, `'al'`, `'que'`, `'y'`). Threshold raised to 50%. Minimum 5 absolute Spanish words required. **W4 FIX**: Function normalizes content to string (handles list-type multimodal content) | `daemon/language_detection.py` (NEW) |
| 8 | Create `language_check_node()` | New async node factory in `daemon/graph.py`. **C2 FIX**: Do NOT short-circuit for English — `detect_wrong_language()` handles English detection (CJK chars + Spanish drift). **C4 FIX**: Skip detection via `reversed(messages[:-1])` scan for `ToolMessage` with `name="language_skip_check"` (stopping at HumanMessage boundary). **C5 FIX**: Return `language_check_retry: True` when injecting reminder, `False` otherwise. **W4 FIX**: Wrap `detect_wrong_language()` in try/except — on error, allow response through. **S5 FIX**: Reset `language_check_count` to 0 when a new HumanMessage is detected | `daemon/graph.py` (MODIFY) |
| 9 | Create `should_end_language_check()` | **C5 FIX**: Check `state.get("language_check_retry", False)` — return `"retry"` if True, `END` if False. No type-sniffing | `daemon/graph.py` (MODIFY) |
| 10 | Create `create_should_continue()` closure factory | **ISSUE 2 FIX**: Do NOT modify `should_continue()` directly. Create a factory in `build_instance_graph()` that captures `language_check_enabled` and returns a closure. When enabled: the closure calls the original `should_continue()` logic but replaces the final `return END` with `return "end_candidate"`. When disabled: returns the original `should_continue()` unchanged. This keeps `should_continue()` as a pure module-level function and avoids breaking the 4 existing test assertions in `tests/unit/test_nudge_behavior.py` | `daemon/graph.py` (MODIFY, inside `build_instance_graph`) |
| 11 | Wire graph nodes + edges conditionally | **ISSUE 2 FIX**: In `build_instance_graph()`: when `language_check_enabled=True`, add `graph.add_node("language_check", ...)`, use the closure from Task 10, and include `"end_candidate": "language_check"` in the conditional edges mapping. When `False`, use original `should_continue` directly and include `END: END` in the mapping (no `language_check` node added). The conditional edges mapping is built dynamically — LangGraph requires all keys in the mapping to have corresponding nodes | `daemon/graph.py` (MODIFY, ~line 700-720) |
| 12 | Create `language_skip_check` tool | New `daemon/tools/language_tools.py` with `@register_tool()` + `@tool` decorated `language_skip_check()`. Returns confirmation string. Tool cannot set graph state — node detects it via message scan | `daemon/tools/language_tools.py` (NEW) |
| 13 | Register tool in assembly | Add `language_tools` import + `tools.extend(create_language_tools())` in `create_instance_tools()` before help tool creation (line ~1067). Add `"language": "daemon.tools.language_tools"` to `CATEGORY_MODULES` | `daemon/tools/instance.py` (MODIFY), `daemon/tools/_tool_registry.py` (MODIFY) |
| 14 | Fix streaming deferred dispatch | **C1 FIX (revised)**: In `daemon/services/instance_messaging.py` streaming loop (~line 1929-1958): when `language_check_active` is True (i.e., the graph was built with `language_check_enabled=True`) AND the AIMessage has no `tool_calls` (will route through `language_check`), do NOT dispatch immediately. Buffer the message and dispatch only when the graph reaches END. Pass `language_check_active` into the streaming context (derived from `self._config.language.check_enabled`). **ISSUE 1 FIX**: The predicate is `language_check_active`, NOT `user_language != "English"` — all users with language check enabled get deferred dispatch, including English users who benefit from CJK/Spanish drift detection (C2) | `daemon/services/instance_messaging.py` (MODIFY, ~lines 1929-1965) |
| 15 | Add config flag + wire to graph builder | Add `LanguageConfig` with `check_enabled: bool = True` to `daemon/config.py`. Pass `language_check_enabled=self._config.language.check_enabled` to `build_instance_graph()` at BOTH call sites (spawn line 570 + restore line 1561). The flag controls: (1) whether `language_check` node is added, (2) whether the `should_continue` closure returns `"end_candidate"` or `END`, (3) whether the conditional edges mapping includes `"end_candidate"`. Also pass `language_check_active=self._config.language.check_enabled` into the streaming context for C1 deferred dispatch | `daemon/config.py` (MODIFY), `daemon/services/instance_lifecycle.py` (MODIFY, lines 570 + 1561), `daemon/services/instance_messaging.py` (MODIFY, streaming context) |
| 16 | Write tests | Test: wrong language detected → reminder injected → `language_check_retry=True` → routes to agent. Test: correct language → passes through to END. Test: skip flag detected via message scan → check skipped. Test: counter prevents infinite loop (>2 retries). Test: tool calls not checked. Test: English preference DOES detect CJK/Spanish drift. Test: code blocks stripped. Test: multimodal content doesn't crash. Test: counter resets on new HumanMessage. Test: deferred dispatch (C1). Test: restore path has `user_language` (C3). Test: `should_end_language_check` uses state flag (C5). Test: Spanish false positives (W1) | `tests/test_language_check.py` (NEW) |

## Key Files

### NEW Files
- `daemon/language_detection.py` — Language detection logic (CJK + Spanish with cleaned word list)
- `daemon/tools/language_tools.py` — `language_skip_check()` tool
- `tests/test_language_check.py` — Graph node + detection + streaming tests

### MODIFIED Files
- `daemon/services/instance_lifecycle.py` — `append_user_language()` + injection in BOTH spawn (line 542) and restore (line 1499) paths + `user_language` passed to `build_instance_graph()` at BOTH call sites (lines 570 + 1561)
- `daemon/graph.py` — SessionState extension, `language_check_node()`, `should_end_language_check()`, `create_should_continue()` closure factory, modified `build_instance_graph()` with conditional node/edge construction. **`should_continue()` itself is NOT modified** — the closure wraps it.
- `daemon/services/instance_messaging.py` — Deferred dispatch logic for final AIMessages when language check is active
- `daemon/tools/instance.py` — Add language tools to assembly
- `daemon/tools/_tool_registry.py` — Add category mapping
- `daemon/config.py` — Add `language_check_enabled` flag

## Implementation Details

### `append_user_language()` Function

```python
def append_user_language(system_prompt: str, language: str) -> str:
    """Append user language preference to the system prompt.
    
    Post-processing step (like append_context_key and append_current_time)
    — runs AFTER cached prompt is loaded, so language changes do NOT
    invalidate the prompt cache.
    """
    if not language:
        language = "English"
    language_section = f"\n---\n\n## User Language Preference\n\nUser prefer language: {language}\n"
    return system_prompt + language_section
```

### Spawn Path Injection (instance_lifecycle.py ~line 538-542)

```python
# Existing:
system_prompt = append_context_key(system_prompt, instance_id, instance_repository, parent_id=parent_id)
system_prompt = append_current_time(system_prompt)

# NEW (add after append_current_time):
from daemon.services.language_utils import get_language_preference
user_language = get_language_preference(project_repository)
system_prompt = append_user_language(system_prompt, user_language)
```

### Restore Path Injection (instance_lifecycle.py ~line 1495-1499) — C3 FIX

```python
# Existing:
system_prompt = append_context_key(system_prompt, instance_id, instance_repository, parent_id=meta.parent_id)
system_prompt = append_current_time(system_prompt)

# NEW (add after append_current_time):
from daemon.services.language_utils import get_language_preference
user_language = get_language_preference(project_repository)
system_prompt = append_user_language(system_prompt, user_language)
```

### Graph Builder Call — BOTH Sites (instance_lifecycle.py lines 570 + 1561) — C3 FIX

```python
# Spawn path (line 570):
graph = build_instance_graph(
    tools=tools,
    checkpointer=self._checkpointer,
    llm_config=llm_config,
    system_prompt=system_prompt,
    retry_config=retry_config,
    compactor=self._compactor,
    graph_config=config,
    user_language=user_language,  # NEW — C3: was missing on restore path
)

# Restore path (line 1561) — IDENTICAL user_language kwarg must be added
graph = build_instance_graph(
    tools=tools,
    checkpointer=self._checkpointer,
    llm_config=llm_config,
    system_prompt=system_prompt,
    retry_config=retry_config,
    compactor=self._compactor,
    graph_config=config,
    user_language=user_language,  # NEW — C3 FIX
)
```

### Language Detection (`daemon/language_detection.py`) — W1 + W4 FIXES

```python
"""Language detection heuristics for the language check node."""
import re
import logging

logger = logging.getLogger(__name__)

# CJK Unicode ranges (Chinese, Japanese, Korean)
CJK_PATTERN = re.compile(
    r'[\u4e00-\u9fff'   # CJK Unified Ideographs
    r'\u3400-\u4dbf'     # CJK Extension A
    r'\u3040-\u309f'     # Hiragana
    r'\u30a0-\u30ff'     # Katakana
    r'\uac00-\ud7af'     # Hangul
    r']+'
)

# W1 FIX: Only unambiguously Spanish words.
# EXCLUDED: 'no', 'a', 'en', 'con', 'sin', 'si', 'lo', 'al', 'que', 'y'
# — all valid English words that caused false positives.
SPANISH_INDICATORS = {
    'porque', 'cuando', 'donde', 'quién', 'cómo', 'qué',
    'bueno', 'malo', 'hacer', 'tener', 'decir', 'poder',
    'querer', 'saber', 'venir', 'pasar', 'deber', 'poner',
    'parecer', 'quedar', 'creer', 'hablar', 'llevar', 'dejar',
    'seguir', 'encontrar', 'llamar', 'entonces', 'también',
    'ahora', 'después', 'antes', 'aquí', 'allí', 'muy',
    'mucho', 'poco', 'todo', 'otro', 'mismo', 'tanto',
    'nuestro', 'vuestro', 'suyo', 'mía', 'tuya', 'suya',
    'está', 'están', 'era', 'fueron', 'sea', 'ser',
    'ha', 'han', 'había', 'tendrá', 'podría', 'querría',
}

# W1 FIX: Raised from 30% to 50%
SPANISH_RATIO_THRESHOLD = 0.50
# W1 FIX: Minimum absolute count for short responses
SPANISH_MIN_ABSOLUTE_COUNT = 5


def _normalize_content(content) -> str:
    """Normalize message content to a string.
    
    Handles:
    - str → returned as-is
    - list (multimodal) → extracts text blocks and joins them
    - None → empty string
    - Other → str(content)
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Multimodal content: [{"type": "text", "text": "..."}, {"type": "image_url", ...}]
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("text"):
                text_parts.append(block["text"])
            elif isinstance(block, str):
                text_parts.append(block)
        return " ".join(text_parts)
    return str(content)


def strip_code_blocks(content: str) -> str:
    """Remove fenced code blocks (```...```) from content before language detection."""
    return re.sub(r'```[\s\S]*?```', '', content)


def has_cjk_characters(content: str) -> bool:
    """Check if content contains any CJK characters."""
    return bool(CJK_PATTERN.search(content))


def spanish_word_count(content: str) -> tuple[int, int]:
    """Count Spanish indicator words and total words.
    
    Returns:
        Tuple of (spanish_count, total_word_count).
    """
    words = re.findall(r'\b[a-zA-ZñáéíóúüÁÉÍÓÚÜ]+\b', content.lower())
    if not words:
        return (0, 0)
    spanish_count = sum(1 for w in words if w in SPANISH_INDICATORS)
    return (spanish_count, len(words))


def detect_wrong_language(content, preferred_language: str) -> bool:
    """Check if content is in a language different from the preferred language.
    
    Args:
        content: The assistant message content (str or list for multimodal).
        preferred_language: The user's preferred language (e.g., "English").
    
    Returns:
        True if the content appears to be in the wrong language.
    
    Detection rules:
    - C2 FIX: English preference IS checked (detects CJK/Spanish drift)
    - W1 FIX: Spanish detection uses cleaned word list, 50% threshold, ≥5 absolute words
    - W4 FIX: Content is normalized to string (handles multimodal list content)
    - Code blocks are stripped before detection
    - Empty content → not wrong (let other logic handle empty)
    """
    # W4 FIX: Normalize content to string
    text = _normalize_content(content)
    
    if not text or not text.strip():
        return False
    
    # Strip code blocks before detection
    clean_content = strip_code_blocks(text)
    if not clean_content.strip():
        return False  # Content was entirely code blocks
    
    preferred = preferred_language.lower().strip()
    
    # C2 FIX: English preference IS checked — detects CJK and Spanish drift
    if preferred == "english":
        if has_cjk_characters(clean_content):
            return True
        # W1 FIX: 50% threshold + ≥5 absolute count
        spanish_count, total_words = spanish_word_count(clean_content)
        if total_words > 0:
            ratio = spanish_count / total_words
            if ratio >= SPANISH_RATIO_THRESHOLD and spanish_count >= SPANISH_MIN_ABSOLUTE_COUNT:
                return True
        return False
    
    # For non-English preferences: check if content lacks the preferred language
    if preferred in ("chinese", "中文", "mandarin"):
        if not has_cjk_characters(clean_content):
            return True
        return False
    
    if preferred in ("spanish", "español"):
        spanish_count, total_words = spanish_word_count(clean_content)
        if total_words > 0 and spanish_count < SPANISH_MIN_ABSOLUTE_COUNT:
            return True
        return False
    
    # For other languages, we don't have detection heuristics — skip check
    return False
```

### SessionState Extension (`daemon/graph.py:327`) — C4 + C5 FIXES

```python
class SessionState(MessagesState):
    """Extended state schema for agent sessions."""
    compacted_at: str | None = None
    # C5 FIX: Retry flag — set by language_check_node when injecting reminder
    language_check_retry: bool = False
    # Language check retry counter (prevents infinite correction loop)
    language_check_count: int = 0
    # C4 FIX: language_skip_check REMOVED — dead state (tool can't set it)
    #         Skip detection uses message history scan instead
```

### Language Check Node (`daemon/graph.py`) — C2 + C4 + C5 + W4 + S5 FIXES

```python
LANGUAGE_REMINDER_TEMPLATE = (
    "You are using wrong language, prefer user language is {language}. "
    "Please respond again with the correct language: {language}."
)

LANGUAGE_CHECK_MAX_RETRIES = 2


def create_language_check_node(user_language: str):
    """Create the language check node function.
    
    Intercepts the agent's final response and checks if the content is
    in the preferred language. If wrong language detected, injects a
    correction reminder and routes back to "agent".
    """
    
    async def language_check_node(state):
        messages = state["messages"]
        last_message = messages[-1]
        
        # Only check AIMessage content (not tool calls, not tool results)
        if not hasattr(last_message, 'content') or getattr(last_message, 'tool_calls', None):
            return {"language_check_retry": False, "language_check_count": 0}
        
        count = state.get("language_check_count", 0)
        
        # S5 FIX: Reset counter when a new HumanMessage is detected
        # (new user message = new turn, stale counter should not persist)
        # W-C FIX: Use additional_kwargs marker instead of content-prefix string matching
        for msg in reversed(messages[:-1]):
            msg_type = getattr(msg, 'type', None)
            if msg_type == 'human':
                # Check if this HumanMessage is our injected reminder or a new user message
                # Reminders are tagged with additional_kwargs={"language_check_reminder": True}
                if not getattr(msg, 'additional_kwargs', {}).get('language_check_reminder', False):
                    count = 0  # New user message, reset counter
                break
        
        # C4 FIX: Detect skip via message history scan (NOT SessionState flag)
        # Same pattern as _has_recent_tool_result at graph.py:399
        skip = False
        for msg in reversed(messages[:-1]):
            msg_type = getattr(msg, 'type', None)
            if msg_type == 'tool':
                tool_name = getattr(msg, 'name', None)
                if tool_name == 'language_skip_check':
                    skip = True
                    break
            elif msg_type == 'human':
                break  # Don't look past the last user message
        
        # Max retries — prevent infinite loop
        if count >= LANGUAGE_CHECK_MAX_RETRIES:
            logger.warning(f"[LanguageCheck] Max retries ({LANGUAGE_CHECK_MAX_RETRIES}) reached, allowing response")
            return {"language_check_retry": False, "language_check_count": 0}
        
        # Skip if language_skip_check tool was called
        if skip:
            return {"language_check_retry": False, "language_check_count": 0}
        
        # Get content
        content = getattr(last_message, 'content', '') or ''
        
        # W4 FIX: Wrap detection in try/except — never crash the graph
        try:
            from .language_detection import detect_wrong_language
            if detect_wrong_language(content, user_language):
                reminder = HumanMessage(
                    content=LANGUAGE_REMINDER_TEMPLATE.format(language=user_language),
                    additional_kwargs={"language_check_reminder": True},  # W-C: marker for counter reset
                )
                # C5 FIX: Set language_check_retry=True for reliable routing
                logger.info(f"[LanguageCheck] Wrong language detected (attempt {count + 1}/{LANGUAGE_CHECK_MAX_RETRIES}), injecting reminder")
                return {
                    "messages": [reminder],
                    "language_check_retry": True,
                    "language_check_count": count + 1,
                }
        except Exception as e:
            # W4 FIX: On any error, allow response through
            logger.warning(f"[LanguageCheck] Detection error, allowing response: {e}")
            return {"language_check_retry": False, "language_check_count": 0}
        
        # Correct language — reset counter, no retry
        return {"language_check_retry": False, "language_check_count": 0}
    
    return language_check_node


def should_end_language_check(state) -> str:
    """Determine if language check should retry or end.
    
    C5 FIX: Uses state flag, NOT type-sniffing on last message.
    """
    if state.get("language_check_retry", False):
        return "retry"
    return END
```

### `create_should_continue()` Closure Factory — ISSUE 2 FIX

**Do NOT modify `should_continue()` directly.** Instead, `build_instance_graph()` creates a closure that captures the `language_check_enabled` flag and conditionally overrides the final return value.

```python
def create_should_continue(language_check_enabled: bool):
    """Create a should_continue wrapper that routes to language_check when enabled.
    
    When language_check_enabled=True:
        - Routes final responses (would-be END) to "end_candidate" → language_check node
        - All other branches (tools, agent, nudge) unchanged
    
    When language_check_enabled=False:
        - Returns the original should_continue() unchanged (END → END)
        - No language_check node exists in the graph
    """
    if not language_check_enabled:
        return should_continue  # Use original function directly
    
    def should_continue_with_language_check(state: MessagesState) -> str:
        result = should_continue(state)
        if result == END:
            return "end_candidate"
        return result
    
    return should_continue_with_language_check
```

**Why this approach**:
1. `should_continue()` at `daemon/graph.py:338` is a module-level function with signature `(state: MessagesState) -> str` — no config parameter, no closure access, no `RunnableConfig`
2. `graph.add_conditional_edges("agent", should_continue, {...})` at line 709 takes the function reference at compile time — it must be the right function before the graph is compiled
3. The closure captures the flag at `build_instance_graph()` call time, then `add_conditional_edges` receives the closure
4. The original `should_continue()` is unchanged — the 4 existing test assertions in `tests/unit/test_nudge_behavior.py` (lines 156, 183, 195, 237) still pass because they test the original function directly

### Modified `build_instance_graph()` (`daemon/graph.py:654-723`) — ISSUE 2 FIX

```python
def build_instance_graph(
    tools: list,
    checkpointer,
    llm_config: dict,
    system_prompt: str,
    retry_config: dict | None = None,
    compactor=None,
    graph_config=None,
    user_language: str = "English",           # NEW
    language_check_enabled: bool = True,       # NEW
):
    # ... existing LLM setup ...
    
    graph = StateGraph(SessionState)
    
    # Add nodes
    graph.add_node("agent", create_agent_node(...))
    graph.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    graph.add_node("nudge", nudge_node)
    
    # ISSUE 2 FIX: Conditionally add language_check node + build routing
    if language_check_enabled:
        graph.add_node("language_check", create_language_check_node(user_language))
        
        # Closure wrapper: routes END → "end_candidate"
        routing_fn = create_should_continue(language_check_enabled=True)
        
        # Conditional edges: "end_candidate" routes to language_check
        graph.add_conditional_edges("agent", routing_fn, {
            "tools": "tools",
            "agent": "agent",
            "nudge": "nudge",
            "end_candidate": "language_check",
        })
        
        # Language check → retry or END
        graph.add_conditional_edges("language_check", should_end_language_check, {
            "retry": "agent",
            END: END,
        })
    else:
        # Language check disabled: use original should_continue, no language_check node
        graph.add_conditional_edges("agent", should_continue, {
            "tools": "tools",
            "agent": "agent",
            "nudge": "nudge",
            END: END,
        })
    
    graph.add_edge(START, "agent")
    graph.add_edge("tools", "agent")
    graph.add_edge("nudge", "agent")
    
    compiled = graph.compile(checkpointer=checkpointer)
    graph_ref[0] = compiled
    return compiled
```

**Key points**:
- When `language_check_enabled=True`: the `language_check` node is added, the closure routes `END → "end_candidate"`, and the conditional edges mapping includes `"end_candidate": "language_check"`
- When `language_check_enabled=False`: no `language_check` node, original `should_continue` used directly, mapping includes `END: END` — identical to current behavior
- LangGraph requires all keys in the conditional edges mapping to have corresponding target nodes — this is why the mapping must be built conditionally

### Streaming Deferred Dispatch (instance_messaging.py ~lines 1929-1965) — C1 FIX

> **W-A NOTE**: This fix applies ONLY to the `astream` path (lines 1920+). The `ainvoke` path at line 688 (`result = await graph.ainvoke(...)`) does NOT do progressive dispatch — it returns the full final state. No changes needed on the `ainvoke` path.

```python
# In the streaming loop, when processing "agent" node messages:

# C1 FIX (revised): When language check is active, defer dispatch of final AIMessages
# (those without tool_calls) until the graph reaches END.
# 
# ISSUE 1 FIX: The predicate is `language_check_active`, NOT `user_language != "English"`.
# All users with language check enabled get deferred dispatch — including English users
# who benefit from CJK/Spanish drift detection (C2 fix). Tying the predicate to the
# config flag rather than the language value avoids the C1/C2 contradiction where
# English users would get immediate dispatch but still get language-checked.

# Pass language_check_active into the processing function (derived from config):
# language_check_active = self._config.language.check_enabled

# Pseudocode for the fix:
if dispatch_source and self._manager.source_dispatcher:
    for node_name, node_data in data.items():
        if node_name == "agent":
            node_messages = node_data.get("messages", [])
            for msg in node_messages:
                if not (hasattr(msg, 'type') and msg.type == 'ai'):
                    continue
                
                msg_id = getattr(msg, 'id', None)
                if msg_id and msg_id in _dispatched_msg_ids:
                    continue
                if msg_id:
                    _dispatched_msg_ids.add(msg_id)
                
                content = getattr(msg, 'content', None)
                if isinstance(content, list):
                    text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("text")]
                    content = " ".join(text_parts)
                
                has_tool_calls = bool(getattr(msg, 'tool_calls', None))
                
                # C1 FIX: If language check is active AND this is a final
                # message (no tool_calls), buffer it — don't dispatch yet.
                # It will be dispatched after language_check completes.
                # 
                # W-B NOTE: _deferred_final_message is OVERWRITTEN on each
                # new agent AIMessage. This means retries naturally replace
                # the wrong-language buffer: agent produces wrong-language
                # response → buffered → language_check injects reminder →
                # agent produces corrected response → overwrites buffer →
                # language_check passes → graph reaches END → correct
                # response dispatched.
                if language_check_active and content and content.strip() and not has_tool_calls:
                    # Buffer for deferred dispatch (overwrites any previous buffer)
                    _deferred_final_message = msg
                    continue  # Skip immediate dispatch
                
                if content and content.strip():
                    try:
                        await self._manager.source_dispatcher.dispatch_message(
                            source=dispatch_source,
                            content=content
                        )
                    except Exception as e:
                        logger.warning(f"Progressive dispatch failed: {e}")

# After the streaming loop completes (graph reached END):
# Dispatch the deferred message if it wasn't consumed by a retry
if _deferred_final_message:
    content = getattr(_deferred_final_message, 'content', '')
    if isinstance(content, list):
        content = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("text"))
    if content and content.strip():
        await self._manager.source_dispatcher.dispatch_message(
            source=dispatch_source,
            content=content
        )
```

### `language_skip_check` Tool (`daemon/tools/language_tools.py`)

```python
"""Language-related tools for the agent."""
import logging
from langchain_core.tools import tool
from ._tool_registry import register_tool

logger = logging.getLogger(__name__)


@register_tool(
    "language_skip_check",
    category="language",
    short_doc="Skip the language check for the next message.",
    full_doc="""Skip the language preference check for your next response.

Use this when you intentionally need to respond in a different language
(e.g., translating a file, writing a multilingual README, or outputting
code with non-English comments).

The skip applies to ONE message only — the next response will be checked
again normally.

Returns:
    Confirmation message.
""",
)
@tool
def language_skip_check() -> str:
    """Skip the language check for the next message."""
    return "Language check skipped for the next message. The system will not enforce the preferred language on your next response."


def create_language_tools():
    """Create language-related tools for an instance."""
    return [language_skip_check]
```

### Tool Registration (`daemon/tools/instance.py` ~line 1067)

```python
# Add before help tool creation:
from .language_tools import create_language_tools
language_tool_list = create_language_tools()
tools.extend(language_tool_list)
```

### Category Mapping (`daemon/tools/_tool_registry.py`)

```python
CATEGORY_MODULES = {
    ...,
    "language": "daemon.tools.language_tools",
}
```

### Config Flag (`daemon/config.py`) — S2 + ISSUE 2

```python
class LanguageConfig(BaseSettings):
    """Language check configuration."""
    model_config = SettingsConfigDict(env_prefix="LANGUAGE_")
    
    check_enabled: bool = Field(default=True, description="Enable language preference checking on agent responses")

# Add to Config class:
class Config(BaseSettings):
    ...
    language: LanguageConfig = Field(default_factory=LanguageConfig)
```

**How the flag is wired through the system**:

1. **Graph builder** (`build_instance_graph`): receives `language_check_enabled` parameter. When `True`, adds the `language_check` node and uses `create_should_continue(True)` closure. When `False`, uses original `should_continue` and no `language_check` node.

2. **Call sites** (`instance_lifecycle.py` lines 570 + 1561): pass `language_check_enabled=self._config.language.check_enabled` to `build_instance_graph()`.

3. **Streaming pipeline** (`instance_messaging.py`): reads `language_check_active = self._config.language.check_enabled` and uses it as the C1 deferred-dispatch predicate.

4. **When disabled**: the graph is identical to the current (pre-feature) graph — no `language_check` node, `should_continue` returns `END` directly, no deferred dispatch. Zero overhead.

## Edge Cases

### What if language is English (default)? — C2 FIX
- `language_check_node` does NOT short-circuit for English
- `detect_wrong_language()` handles English: detects CJK chars and Spanish drift (≥50% ratio, ≥5 words)
- This means an agent drifting into Chinese when the user prefers English WILL be caught

### How does the skip flag work? — C4 FIX
- `SessionState.language_skip_check` does NOT exist (removed — dead state)
- The `language_check_node` scans `reversed(messages[:-1])` for a `ToolMessage` with `name="language_skip_check"`, stopping at HumanMessage boundary
- This is the same pattern as `_has_recent_tool_result()` at graph.py:399-414
- The scan runs every time the node executes — no state to consume

### How does retry routing work? — C5 FIX
- `language_check_node` returns `language_check_retry: True` when it injects a reminder
- `should_end_language_check(state)` checks `state.get("language_check_retry", False)` — returns `"retry"` if True
- No type-sniffing on `last_message.type == 'human'` — uses explicit state flag

### Streaming double-dispatch — C1 FIX (revised)
- When `language_check_active` is True (config flag `language.check_enabled`) and the AIMessage has content but no `tool_calls`, the streaming pipeline buffers the message instead of dispatching immediately
- **ISSUE 1 FIX**: The predicate is `language_check_active`, NOT `user_language != "English"`. All users with language check enabled get deferred dispatch — including English users who benefit from CJK/Spanish drift detection (C2). This resolves the C1/C2 contradiction where English users would get immediate dispatch but still be language-checked.
- If `language_check` injects a reminder (retry), the buffered message is discarded — the agent produces a new response
- Only when the graph reaches END is the final (correct) message dispatched
- Trade-off: adds latency (one graph step) for final messages when language check is active. Acceptable because the feature is opt-in via config flag.
- **W-A**: This fix applies ONLY to the `astream` path (line 1920+). The `ainvoke` path (line 688) does NOT do progressive dispatch — no changes needed there
- **W-B**: `_deferred_final_message` is overwritten on each new agent AIMessage — retries naturally replace the wrong-language buffer with the corrected response

### Restore path — C3 FIX
- Both spawn (line 570) and restore (line 1561) call `build_instance_graph()` with `user_language=user_language`
- After daemon restart, restored instances have language check properly configured

### Multimodal content — W4 FIX
- `detect_wrong_language()` calls `_normalize_content()` which handles `content` as str, list (multimodal), or None
- The entire detection is wrapped in try/except — on any error, the response is allowed through

### Counter reset on new user message — S5 + W-C FIX
- `language_check_node` scans `reversed(messages[:-1])` for HumanMessage
- The injected reminder is tagged with `additional_kwargs={"language_check_reminder": True}`
- If the last HumanMessage does NOT have this marker, it's a new user message → reset `language_check_count` to 0
- This prevents stale counter values from persisting across turns
- More robust than content-prefix string matching (W-C) — no false positives from user messages containing reminder text

### Recursion budget — S3
- Worst case: +5 nodes per turn (agent → language_check → agent → language_check → agent → language_check → END)
- `graph_recursion_limit` defaults to 100 — 5% budget, safe
- The `LANGUAGE_CHECK_MAX_RETRIES = 2` cap ensures max 3 agent invocations per turn

### LLM cost impact — S2
- Each retry = one extra LLM call
- Max 2 retries = 3× worst case per turn (original + 2 retries)
- `language_check_enabled` config flag (default True) allows disabling entirely
- When disabled, the graph is identical to pre-feature behavior — no `language_check` node, original `should_continue` used directly, no deferred dispatch. Zero overhead.

### Config flag wiring — ISSUE 2 FIX
- `should_continue()` is a module-level function with no config access — it cannot check the flag itself
- Instead, `build_instance_graph()` creates a `create_should_continue(language_check_enabled)` closure that captures the flag
- When enabled, the closure wraps `should_continue()` and replaces `END → "end_candidate"`
- When disabled, the original `should_continue` is passed directly to `add_conditional_edges`
- The conditional edges mapping is also built conditionally — `"end_candidate": "language_check"` only included when the node exists
- LangGraph requires all keys in the mapping to have corresponding target nodes — conditional mapping construction prevents runtime errors

## Constraints
- `SessionState` fields must be JSON-serializable for checkpoint persistence (bool and int are fine)
- Language detection must be fast (no API calls, pure Python regex + set lookup)
- The node must not modify the original AIMessage — it only adds a new HumanMessage
- Code blocks are stripped before detection to avoid false positives
- Only English, Chinese (CJK), and Spanish detection are implemented — other languages skip the check (detect_wrong_language returns False)
- `build_instance_graph()` has `user_language: str = "English"` and `language_check_enabled: bool = True` defaults — backward-compatible with existing test mocks that don't pass these kwargs
- **`should_continue()` is NOT modified** — the closure wrapper `create_should_continue()` captures the flag at graph-build time. The 4 existing test assertions in `tests/unit/test_nudge_behavior.py` remain valid.

## Deliverables
- [ ] `append_user_language()` function in `daemon/services/instance_lifecycle.py`
- [ ] Both spawn (line 542) AND restore (line 1499) paths inject language — C3 FIX
- [ ] Both `build_instance_graph()` call sites (lines 570 + 1561) pass `user_language` AND `language_check_enabled` — C3 + ISSUE 2 FIX
- [ ] `build_instance_graph()` accepts `user_language` and `language_check_enabled` parameters with defaults
- [ ] `daemon/language_detection.py` with cleaned Spanish word list (W1), 50% threshold, ≥5 min count, multimodal normalization (W4)
- [ ] `SessionState` has `language_check_retry` + `language_check_count` (NOT `language_skip_check` — C4 FIX)
- [ ] `language_check_node` does NOT short-circuit for English (C2 FIX), wraps detection in try/except (W4 FIX), resets counter on new user message (S5 FIX)
- [ ] `should_end_language_check()` uses `state.get("language_check_retry")` — NOT type-sniffing (C5 FIX)
- [ ] `create_should_continue()` closure factory in `build_instance_graph()` — does NOT modify `should_continue()` (ISSUE 2 FIX)
- [ ] `build_instance_graph()` conditionally adds `language_check` node + builds conditional edges mapping (ISSUE 2 FIX)
- [ ] **W2**: `should_continue()` unchanged — 4 assertions in `tests/unit/test_nudge_behavior.py` (lines 156, 183, 195, 237) still pass without modification
- [ ] Streaming pipeline defers dispatch of final AIMessages when `language_check_active` (config flag, NOT `user_language != "English"`) — C1 + ISSUE 1 FIX
- [ ] `daemon/tools/language_tools.py` with `language_skip_check()` tool
- [ ] Tool registered in `create_instance_tools()` and `CATEGORY_MODULES`
- [ ] `LanguageConfig` with `check_enabled` flag in `daemon/config.py` (S2)
- [ ] Tests in `tests/test_language_check.py` covering all fixes
- [ ] All existing tests pass (including unmodified `tests/unit/test_nudge_behavior.py`)
