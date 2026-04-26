# RAG Knowledge Toolset Review — Critical Findings

**Date**: 2026-04-26
**Branch**: `feature/rag-knowledge-toolset`
**Scope**: 71 files, ~7,951 lines across 6 phases

## Top-Line
🔴 **Blocking** — 6 Critical, 7 Warnings, 8 Suggestions

## Critical Issues (must fix before merge)
1. `CompletionRegistry.wait_for()` is sync but calls `run_until_complete()` inside a running event loop — always raises RuntimeError, silently caught as None. Fix: make it `async def` with `await asyncio.wait_for()`.
2. Tests mock `wait_for()` entirely, hiding the bug.
3. `experience()` can orphan instances if `enqueue_message` fails after `spawn_instance`.
4. `explore()` has no try/except around `invoke_agent_and_wait`.
5. Migration script not idempotent — duplicates on re-run.
6. Migration docs don't warn about duplicate risk.

## What Works Well
- Recursion prevention (Explorer/Experiencer exclude "knowledge" category)
- Semaphore deadlock prevention design
- CompletionRegistry `complete()` signaling (thread-safe, buffered)
- inner_soul redirect protects core memory (soul/user/workflow)
- RAG exception hierarchy
- Experiencer agent definition quality
