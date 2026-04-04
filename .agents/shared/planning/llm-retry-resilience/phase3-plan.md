# Phase 3: Fallback Provider Support

## Objective
Add configurable fallback LLM provider support so agents can automatically switch to a backup provider when the primary fails all retry attempts. This eliminates the single point of failure for LLM calls.

## Coupling
- **Depends on**: Phase 1 (config structure, retry behavior)
- **Coupling type**: loose
- **Shared files with other phases**: 
  - `daemon/config.py` (shared with Phase 1 for config, Phase 4 for observability)
  - `daemon/graph.py` (shared with Phase 1 for retry wrapper, Phase 2 for validation)
- **Shared APIs/interfaces**: `ThinkingChatOpenAI` constructor (Phase 3 adds fallback awareness), config schema
- **Why this coupling**: Phase 3 adds new config fields and new code paths in graph.py, but doesn't modify Phase 1's retry logic. The fallback wraps around the retry layer.

## Context
- Previous phase completed: Phase 1 delivered expanded retry, wired config, reduced timeout
- Key decisions: See `decisions.md`
- Current state: No fallback support. If primary provider is down, all requests fail.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add fallback provider config schema | Add `fallback_model`, `fallback_base_url`, `fallback_api_key`, `fallback_api_key_env`, `fallback_request_timeout` fields to `LLMConfig` or `QueueConfig`. All optional — if not set, fallback is disabled. | `daemon/config.py` |
| 2 | Create fallback LLM wrapper | New module `daemon/llm_fallback.py` that wraps primary + fallback LLMs. Implements: try primary → if all retries exhausted → try fallback → if fallback also fails → raise original error. Returns fallback info in metadata for observability. | `daemon/llm_fallback.py` (new) |
| 3 | Integrate fallback into graph construction | In `graph.py` where `ThinkingChatOpenAI` is instantiated, optionally create a fallback instance and wrap both in the fallback wrapper. The fallback should also get `with_retry()` applied (from Phase 1 config). | `daemon/graph.py` |
| 4 | Add fallback-aware streaming with chunk tracking | Extend the fallback wrapper to handle streaming. **Critical**: track whether any chunks have been yielded. If primary fails AFTER yielding chunks, do NOT fall back (would corrupt output with interleaved chunks from two providers). Instead, raise the error for queue-level retry + checkpoint resume. Fallback ONLY activates if primary fails BEFORE yielding any content. | `daemon/llm_fallback.py`, `daemon/manager.py` |
| 5 | Add `fallback_enabled` config flag | Explicit opt-in flag for fallback. Default `False` to maintain backward compatibility. | `daemon/config.py` |
| 6 | Add YAML config documentation | Document the fallback config schema with examples. | `docs/configuration.md` or config comments |
| 7 | Add tests for fallback behavior | Test: primary succeeds (no fallback), primary fails + fallback succeeds, both fail, fallback not configured, streaming with primary failure. | `tests/test_llm_fallback.py` (new) |

## Key Files
- `daemon/llm_fallback.py` — New: fallback wrapper implementation
- `daemon/config.py` — Fallback config fields
- `daemon/graph.py` — Integration of fallback into graph construction
- `daemon/manager.py` — Streaming fallback awareness
- `tests/test_llm_fallback.py` — New: fallback tests

## Constraints
- **Opt-in by default**: Fallback is disabled unless explicitly configured. No behavior change for existing deployments.
- **Fallback uses same agent config**: The fallback provider uses the same tools, temperature, and other settings — only the provider URL/model/key changes.
- **No streaming mid-failover**: Cannot switch providers mid-stream. Fallback only activates when primary fails BEFORE yielding any chunks. After partial delivery, the error propagates to queue-level retry for checkpoint resume.
- **No cascading fallbacks**: Only one level of fallback (primary → secondary). No tertiary providers.
- **API key security**: Fallback API key should support env var reference like primary does.

## Implementation Notes

### Task 1: Config Schema

```python
# In config.py, add to QueueConfig or create FallbackConfig:
class FallbackConfig(BaseSettings):
    enabled: bool = Field(default=False, description="Enable fallback LLM provider")
    model: str = Field(default="", description="Fallback model name")
    base_url: str = Field(default="", description="Fallback API base URL")
    api_key: str = Field(default="", description="Fallback API key (or use api_key_env)")
    api_key_env: str = Field(default="", description="Env var name for fallback API key")
    request_timeout: int = Field(default=120, description="Fallback request timeout (seconds)")
```

### Task 2: Fallback Wrapper Design

```python
# daemon/llm_fallback.py
class FallbackLLM:
    """Wraps primary and fallback LLMs with automatic failover."""
    
    def __init__(self, primary_llm, fallback_llm=None, config=None):
        self.primary = primary_llm
        self.fallback = fallback_llm
        self.config = config
    
    async def ainvoke(self, input, config=None, **kwargs):
        """Try primary, fall back on exhaustion."""
        try:
            return await self.primary.ainvoke(input, config=config, **kwargs)
        except Exception as primary_error:
            if self.fallback is None:
                raise
            
            logger.warning(
                f"Primary LLM failed, attempting fallback: {primary_error}"
            )
            try:
                result = await self.fallback.ainvoke(input, config=config, **kwargs)
                # Tag result with fallback metadata
                result.response_metadata["fallback_used"] = True
                result.response_metadata["primary_error"] = str(primary_error)
                return result
            except Exception as fallback_error:
                logger.error(f"Fallback LLM also failed: {fallback_error}")
                raise primary_error  # Raise original error
    
    # Similar for ainvoke, invoke, stream
    
    async def astream(self, input, config=None, **kwargs):
        """Stream with fallback ONLY if primary fails before yielding any chunks.
        
        CRITICAL: If the primary stream fails AFTER yielding one or more chunks,
        we must NOT fall back to the secondary. Doing so would interleave chunks
        from two different LLM calls, producing corrupted output. Instead, we 
        raise the error and let the queue-level retry + checkpoint resume handle
        recovery from the last completed node.
        """
        chunks_yielded = False
        try:
            async for chunk in self.primary.astream(input, config=config, **kwargs):
                chunks_yielded = True
                yield chunk
        except Exception as primary_error:
            if self.fallback is None:
                raise
            
            if chunks_yielded:
                # PRIMARY ALREADY DELIVERED PARTIAL OUTPUT
                # Do NOT fall back — the caller has already received chunks.
                # Interleaving a second provider's stream would corrupt the response.
                # Let queue-level retry + checkpoint resume handle this.
                logger.warning(
                    f"Primary stream failed after {chunks_yielded} chunks delivered. "
                    f"Cannot fall back mid-stream. Propagating error for queue retry. "
                    f"Error: {primary_error}"
                )
                raise
            
            # PRIMARY FAILED BEFORE ANY OUTPUT — safe to fall back
            logger.warning(
                f"Primary stream failed before any output, attempting fallback: "
                f"{primary_error}"
            )
            async for chunk in self.fallback.astream(input, config=config, **kwargs):
                # Tag chunk with fallback metadata for observability
                if hasattr(chunk, 'response_metadata'):
                    chunk.response_metadata["fallback_used"] = True
                    chunk.response_metadata["primary_error"] = str(primary_error)[:200]
                yield chunk
```

### Task 3: Integration Pattern

```python
# In graph.py, where LLM is created:
def _create_llm_with_retry(config):
    primary = ThinkingChatOpenAI(
        model=config.model,
        base_url=config.base_url,
        request_timeout=config.request_timeout,
        ...
    )
    
    if config.fallback and config.fallback.enabled:
        fallback = ThinkingChatOpenAI(
            model=config.fallback.model,
            base_url=config.fallback.base_url,
            api_key=_resolve_api_key(config.fallback),
            request_timeout=config.fallback.request_timeout,
            ...
        )
        # Apply retry to both
        primary = primary.with_retry(...)
        fallback = fallback.with_retry(...)
        return FallbackLLM(primary, fallback, config)
    
    # No fallback — just primary with retry
    return primary.with_retry(...)
```

### Task 4: Streaming Fallback with Chunk Tracking

**Critical bug prevention**: If the primary stream fails AFTER yielding chunks to the caller, the fallback must NOT be activated. Doing so would produce corrupted output — chunks from provider A followed by chunks from provider B, with no coherent message.

**Safe fallback boundary**: Fallback is only safe when the primary fails BEFORE yielding any chunks (connection refused, auth error at connect, model not found, etc.). Once a single chunk has been yielded, the error must propagate to the queue-level retry, which uses LangGraph checkpoint resume to restart from the last completed node.

**Recovery path for mid-stream failures**:
1. Primary yields N chunks → fails
2. `FallbackLLM.astream()` detects `chunks_yielded > 0` → raises error (no fallback)
3. `_process_message_with_tracking()` catches error → re-raises to `_process_queue()`
4. `_process_queue()` schedules queue-level retry
5. On retry, LangGraph resumes from checkpoint (last completed node, NOT the beginning)
6. The failed LLM call re-executes from scratch with full retry budget

**Additional tests needed for this task**:
- Test: primary succeeds entirely (no fallback) ← baseline
- Test: primary fails before any chunks → fallback activates
- Test: primary fails after 3 chunks → fallback does NOT activate, error propagates
- Test: fallback stream also fails after primary pre-chunk failure → original error raised
- Test: verify queue-level retry + checkpoint resume recovers from mid-stream failure

## Deliverables
- [ ] `FallbackConfig` in config.py with all necessary fields
- [ ] `daemon/llm_fallback.py` module with `FallbackLLM` wrapper
- [ ] Fallback integrated into graph construction
- [ ] Streaming-aware fallback with chunk tracking (prevents mid-stream corruption)
- [ ] Tests covering: pre-chunk fallback, post-chunk error propagation, mid-stream recovery via checkpoint
- [ ] Fallback is opt-in (disabled by default)
- [ ] Config documentation with examples
- [ ] Tests for all fallback paths
