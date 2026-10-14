# MCP Warmup Pool — Logging + Timeout Fix (2026-05-20)

## Problem
Two bugs in `daemon/mcp/warmup_pool.py`:

1. **Misleading success log**: `warmup()` always logged "Warmed up pool for X (N connections)" even when ALL connections failed, because `_warmup_server()` swallowed exceptions via `gather(return_exceptions=True)` and never raised.
2. **MCP STDIO connection timeout**: npx/uvx-based MCP servers (context7, webfetch) need time for package resolution before `session.initialize()` can succeed. The 30s single-shot timeout wasn't resilient enough.

## Fix
- `_warmup_server()` now returns success count (int). Caller logs INFO/WARNING/ERROR based on actual success ratio.
- Added 2s startup delay after subprocess spawn, retry logic (3 attempts, 10s per-attempt, exponential backoff), 60s outer timeout.
- `CancelledError` re-raised immediately (not retried) for proper cancellation propagation.

## Key Pattern
- `asyncio.CancelledError` is `BaseException` subclass in Python 3.9+, not `Exception`. Must catch it separately before `Exception` in retry loops.
