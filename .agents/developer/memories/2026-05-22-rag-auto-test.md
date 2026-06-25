# RAG Auto-Test on Startup

**Date:** 2026-05-22
**Feature:** RAG auto-test on startup

## What Was Done

Added a startup auto-test that verifies RAG backend connectivity before enabling RAG. If the test fails (auth error, connection refused, timeout, etc.), RAG is gracefully disabled — treated the same as if env vars were never set.

## Key Files Changed
- `daemon/rag/config.py` — Added `_rag_enabled` flag, `auto_test_rag()`, `disable_rag()`, `enable_rag()`
- `daemon/api.py` — Integrated `auto_test_rag()` call in startup lifespan
- `daemon/rag/__init__.py` — Exported new functions
- `tests/unit/rag/test_config.py` — 25 tests for auto-test functionality

## Architecture Decisions
- `_rag_enabled` is a module-level boolean flag (default `True`)
- `is_rag_enabled()` checks both `_rag_enabled` AND env var presence
- `auto_test_rag()` makes a real `/query/data` request to verify connectivity
- 15-second timeout for the auto-test
- Catches all httpx error types plus generic Exception as safety net
- Logs clear warning messages on failure

## Commits
- `0bd6120` — feat: add RAG auto-test on startup
- `98b78ef` — test: add unit tests for RAG auto-test on startup
