# Phase 1: Configuration & Token Estimation

## Objective

Add a new `CompactionConfig` section to the configuration system, create a `MODEL_CONTEXT_LIMITS` registry for model-aware context windows, enhance the existing `estimate_tokens()` to work with LangChain message objects, and wire the new config section into `load_config()`.

## Context

- **Previous phase**: None (foundation phase)
- **Key decisions**: 
  - Use Pydantic `BaseSettings` with `COMPACTION_` env prefix (consistent with existing pattern)
  - Add config to `config.yaml` under new `compaction:` section
  - Extend `estimate_tokens()` rather than replacing it
  - **MUST wire `compaction:` section into `load_config()`** (WARN-6: explicit wiring needed)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Create `CompactionConfig` class** | Pydantic settings class with `COMPACTION_` env prefix for all compaction parameters | `daemon/config.py` |
| 2 | **Add `CompactionConfig` to main `Config` class** | Wire into `Config` as `compaction` field with defaults | `daemon/config.py` |
| 3 | **Wire config loading for `compaction:` section** | Add `if "compaction" in processed_config: config_dict["compaction"] = processed_config["compaction"]` in `load_config()` — following the exact same pattern as all other sections (llm, daemon, limits, etc.) | `daemon/config.py` |
| 4 | **Create model context limits registry** | Dict mapping model name patterns to context window sizes (e.g., `"gpt-4": 8192`, `"gpt-4-turbo": 128000`, `"claude-3": 200000`) | `daemon/compaction.py` (new) |
| 5 | **Add `estimate_messages_tokens()` function** | Estimate tokens for a list of LangChain `BaseMessage` objects, handling `HumanMessage`, `AIMessage`, `ToolMessage`, `SystemMessage` with per-role overhead. Must handle messages where `content` is a list (some models return content as list of blocks). | `daemon/loader.py` |
| 6 | **Add `get_model_context_limit()` helper** | Function that takes model name string, does fuzzy matching against registry, returns context window size. Uses `context_window_override` from config if non-zero. | `daemon/compaction.py` (new) |
| 7 | **Update `config.yaml` with compaction defaults** | Add `compaction:` section with sensible defaults | `config.yaml` |

## Key Files

- `daemon/config.py` — Add `CompactionConfig`, wire into `Config`, update `load_config()`
- `daemon/loader.py` — Add `estimate_messages_tokens()` alongside existing `estimate_tokens()`
- `daemon/compaction.py` — **NEW FILE** — Model context limits registry, `get_model_context_limit()`, and later the full compaction engine
- `config.yaml` — Add `compaction:` section

## Detailed Design

### CompactionConfig Schema

```python
class CompactionConfig(BaseSettings):
    """Context compaction configuration."""
    
    model_config = SettingsConfigDict(env_prefix="COMPACTION_")
    
    # Whether compaction is enabled (default: True)
    enabled: bool = Field(default=True)
    
    # Compaction threshold as fraction of context window (0.0-1.0)
    # Compaction triggers when token usage exceeds this fraction
    threshold: float = Field(
        default=0.80,
        description="Trigger compaction when tokens exceed this fraction of context window"
    )
    
    # Number of recent messages to preserve during compaction
    # These are NEVER summarized — always kept intact
    # NOTE: This counts BOUNDARY GROUPS, not individual messages.
    # A tool call group (AIMessage + N ToolMessages) counts as 1 group.
    # For tool-heavy sessions, each group may be 2-4 messages.
    recent_message_window: int = Field(
        default=10,
        description="Number of most recent boundary GROUPS to keep intact during compaction"
    )
    
    # Minimum recent window — hard floor for progressive reduction
    # Even when reducing window to fit threshold, never go below this
    min_recent_window: int = Field(
        default=3,
        description="Hard minimum for recent window during progressive reduction"
    )
    
    # Context window size override (0 = auto-detect from model name)
    # If set, overrides the model registry lookup
    context_window_override: int = Field(
        default=0,
        description="Override context window size. 0 = auto-detect from model name"
    )
    
    # Maximum tokens to target after compaction (as fraction of context window)
    # After compaction, total tokens should be below this level
    target_ratio: float = Field(
        default=0.40,
        description="Target token usage after compaction as fraction of context window"
    )
    
    # Summarization model override (empty = use same model as session)
    summarization_model: str = Field(
        default="",
        description="Model to use for summarization. Empty = use session's model"
    )
    
    # Minimum messages before compaction is considered
    # Avoids compacting very short conversations
    min_messages_before_compaction: int = Field(
        default=10,
        description="Minimum number of messages before compaction is considered"
    )
    
    # Maximum fraction of context window that compactable messages can occupy
    # before we switch to chunked summarization. If compactable messages exceed
    # this fraction of context window, summarize in batches instead of all at once.
    # Prevents the summarization LLM call itself from overflowing.
    summarization_chunk_threshold: float = Field(
        default=0.60,
        description="Fraction of context window above which summarization uses chunking"
    )
```

### `load_config()` Wiring (WARN-6)

In `daemon/config.py` `load_config()`, add after the existing `if "queue" in processed_config:` block:

```python
if "compaction" in processed_config:
    config_dict["compaction"] = processed_config["compaction"]
```

This follows the **exact same pattern** as `llm`, `daemon`, `limits`, `persistence`, `agents`, and `queue` — no special handling needed.

### config.yaml Addition

```yaml
# Context Compaction Settings
compaction:
  enabled: true
  threshold: 0.80              # Trigger at 80% of context window
  recent_message_window: 10    # Keep last 10 boundary groups intact (not individual messages)
  min_recent_window: 3         # Never reduce below 3 groups, even under pressure
  target_ratio: 0.40           # Target 40% of context window after compaction
  min_messages_before_compaction: 10
  summarization_chunk_threshold: 0.60  # Chunk summarization if old messages > 60% of context
  # context_window_override: 0  # 0 = auto-detect from model name
  # summarization_model: ""     # Empty = use session's model
```

### Model Context Limits Registry

```python
# In daemon/compaction.py

MODEL_CONTEXT_LIMITS: dict[str, int] = {
    # OpenAI models
    "gpt-4": 8192,
    "gpt-4-turbo": 128000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4.5": 128000,
    "gpt-3.5-turbo": 16385,
    "o1": 200000,
    "o1-mini": 128000,
    "o3-mini": 200000,
    
    # Anthropic models (via OpenAI-compatible API)
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "claude-3.5-sonnet": 200000,
    "claude-3.5-haiku": 200000,
    "claude-4": 200000,
    
    # Open-source models
    "llama-3": 8192,
    "llama-3.1": 128000,
    "mistral": 32000,
    "mixtral": 32000,
    "deepseek": 128000,
    "qwen": 32768,
}

DEFAULT_CONTEXT_LIMIT = 128000  # Safe default for modern models
```

### `estimate_messages_tokens()` Function

```python
def estimate_messages_tokens(messages: list[BaseMessage]) -> int:
    """Estimate total token count for a list of LangChain messages.
    
    Accounts for per-message overhead (role tokens, formatting) that LLMs add.
    Uses rough overhead estimates based on OpenAI's token accounting:
    - Each message: +4 tokens (role markers, separators)
    - Tool calls: additional tokens for function call formatting
    
    Args:
        messages: List of LangChain BaseMessage objects.
        
    Returns:
        Estimated total token count including overhead.
    """
    if not messages:
        return 0
        
    total = 0
    for msg in messages:
        # Content tokens
        content = getattr(msg, 'content', '') or ''
        if isinstance(content, list):
            # Some models return content as list of blocks
            for block in content:
                if isinstance(block, dict):
                    total += estimate_tokens(block.get('text', ''))
                else:
                    total += estimate_tokens(str(block))
        else:
            total += estimate_tokens(str(content))
        
        # Per-message overhead (~4 tokens for role markers, separators)
        total += 4
        
        # Tool calls overhead
        if hasattr(msg, 'tool_calls') and msg.tool_calls:
            for tc in msg.tool_calls:
                if isinstance(tc, dict):
                    total += estimate_tokens(str(tc.get('args', {})))
                    total += estimate_tokens(tc.get('name', ''))
                else:
                    total += estimate_tokens(str(getattr(tc, 'args', {})))
                    total += estimate_tokens(getattr(tc, 'name', ''))
                total += 3  # function call formatting overhead
        
        # Tool response metadata
        if hasattr(msg, 'name') and msg.name:
            total += estimate_tokens(msg.name) + 2
        
        # Additional kwargs (thinking, reasoning)
        if hasattr(msg, 'additional_kwargs') and msg.additional_kwargs:
            for key, val in msg.additional_kwargs.items():
                if key in ('reasoning_content', 'thinking'):
                    total += estimate_tokens(str(val))
    
    return total
```

## Constraints

- Must be backward compatible — existing configs without `compaction:` section must work (all fields have defaults)
- `estimate_messages_tokens()` must handle all message types: `HumanMessage`, `AIMessage`, `ToolMessage`, `SystemMessage`
- `estimate_messages_tokens()` must handle `content` as both `str` and `list` (some models return list of blocks)
- Token estimation is approximate by design — this is acceptable
- `load_config()` wiring follows exact same pattern as existing sections (llm, daemon, etc.)

## Deliverables

- [ ] `CompactionConfig` class in `daemon/config.py`
- [ ] `Config` class updated with `compaction` field
- [ ] `load_config()` updated to parse `compaction:` YAML section
- [ ] `estimate_messages_tokens()` in `daemon/loader.py`
- [ ] `MODEL_CONTEXT_LIMITS` registry and `get_model_context_limit()` in `daemon/compaction.py`
- [ ] `config.yaml` updated with `compaction:` section and defaults
- [ ] Unit tests for `estimate_messages_tokens()` and `get_model_context_limit()`
