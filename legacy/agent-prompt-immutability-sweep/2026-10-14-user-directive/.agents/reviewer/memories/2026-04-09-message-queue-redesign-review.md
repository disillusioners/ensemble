# Review: Message Queue Redesign - Plan Review Insights

## Date: 2026-04-09
## Reviewer: Reviewer Agent
## Scope: 7 plan documents validated against existing codebase

## Key Findings

### Most Critical Issue: Async/Thread Boundary (Phase 2)
The entire worker pool design (Phase 2) proposes sync threads but the core processing
method `_process_message_with_tracking` is async. LangGraph execution, LLM semaphore,
event broadcasting — all async. The plan acknowledges this risk but doesn't resolve it.

### Most Critical Issue: Multi-Client SSE (Phase 4)
The `delivered` field on events is a single boolean. If two SSE clients connect to the
same instance, the first to read marks events as delivered, and the second misses them.

### Good News
- SQLite 3.51.3 installed — RETURNING is supported (3.35+ required)
- Migration runner handles idempotent column additions
- Atomic UPDATE-RETURNING pattern is sound for SQLite
- Existing codebase already has async/thread bridge patterns to follow
