# LLM Retry Resilience Plan Review

**Date**: 2026-04-02
**Reviewer**: Reviewer Agent
**Verdict**: 🟡 NEEDS WORK — 3 Critical, 5 Warnings, 6 Suggestions

## Key Findings

### Critical Issues
1. **C1**: FallbackLLM.astream() doesn't track yielded chunks — will produce corrupted interleaved output on mid-stream failure
2. **C2**: Streaming chunk timeout uses asyncio.timeout() around yield — architecturally wrong, need asyncio.wait_for on __anext__()
3. **C3**: "Never fail mid-task" claim doesn't account for multi-turn graph execution (agent→tool→agent loops)

### Warnings
1. **W1**: Phase 1 assumes with_retry() supports custom wait params — unverified
2. **W2**: Permanent errors at queue level waste 3 LLM retries first
3. **W3**: FallbackLLM + with_retry() = 6 attempts, 12+ min latency — no fallback_timeout
4. **W4**: Circuit breaker unification ignores async/sync incompatibility
5. **W5**: LLMResponseValidationError used for both transient and permanent failures

### Codebase Bugs Found During Review
- **manager.py:1365-1374**: Streaming buffer content lost on failure (not flushed before re-raise)
- **manager.py:1306-1321**: Content flush bug — only runs when reasoning chunks present
- **queue.py:29-30**: Hardcoded circuit breaker thresholds ignore config values
- **queue.py:24**: Hardcoded MESSAGE_TIMEOUT_SECONDS ignores QueueConfig

## Verdict
Plan is well-structured with sound phase ordering and good ADRs. After fixing C1-C3 and addressing W3/W5, ready for implementation.
