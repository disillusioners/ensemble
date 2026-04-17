# Quick Fix: test_persistence.py Invalid Type Assertions

**Date:** 2026-04-17
**Branch:** feature/job-system-improvements
**Commit:** a23ca2c

## Issue
Three tests in `tests/test_persistence.py` were asserting `messages[0]["type"]` on dictionaries returned by `serialize_message()`. The function maps LangChain message types to REST API format using a `"role"` key (e.g., "human"→"user", "ai"→"assistant"), not `"type"`.

## Root Cause
The tests were checking for a non-existent key. The `serialize_message()` function in `daemon/utils.py` correctly returns `role`, not `type`.

## Fix
Removed the 3 invalid `["type"]` assertions. The existing `["role"]` assertions in the same tests already verified the correct message type mapping.

## Lesson
When testing serialization functions, verify the actual return structure matches assertions. The `"role"` key is the correct REST API convention; `"type"` is a LangChain internal concept.
