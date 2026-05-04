# Quick Fix: Strip whitespace from workspace in _request()

**Date:** 2026-05-04
**Branch:** fix/rag-search-workspace-mismatch
**Commit:** fe1e826
**Session:** rag-testing

## Issue
Whitespace-only workspace strings (`"  "`, `"\t"`) passed to `_request()` were truthy in Python, causing `LIGHTRAG-WORKSPACE` headers to be added with whitespace-only values.

## Root Cause
`_request()` checked `if workspace:` which is truthy for whitespace-only strings. The env var handling already stripped whitespace in `from_env()`, but the runtime workspace parameter did not.

## Fix
Added `.strip()` before truthiness check in `_request()` for workspace parameter, consistent with env var handling.

## File Changed
- `daemon/rag/client.py` — `_request()` method

## Verification
- All 68 RAG tests pass
- Edge case: `workspace="  "` → treated as empty (no header) ✅
- Edge case: `workspace="\t"` → treated as empty (no header) ✅
- Edge case: `workspace="  my-ws  "` → trimmed before sanitization ✅
