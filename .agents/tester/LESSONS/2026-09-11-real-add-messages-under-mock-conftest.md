# Testing REAL langgraph add_messages under the root-conftest mock-langgraph eviction pattern

**Date:** 2026-09-11 (LCA orphan+marker final merge gate)
**Files:** `tests/unit/test_attestation_marker_supersede_lca.py` (commit `5ac046ea`)

## Problem

The repo's root `tests/conftest.py` installs mock `langgraph` modules for hermetic unit tests. Any test that must verify behavior of the REAL LangGraph primitives — e.g. the marker-hint supersede contract (same-id `add_messages` upsert collapses two hints into one block, langgraph 1.0.9 `graph/message.py:225` `merged[existing_idx] = m`) — silently tests a mirrored dict helper instead of the real reducer if it just imports `langgraph` normally.

## Pattern (reusable)

1. `setup_module()` evicts the root-conftest mock-langgraph entries from `sys.modules` and imports the REAL packages (`langgraph.graph.message.add_messages`, `langgraph.checkpoint.memory.MemorySaver`).
2. **Guard test first:** assert `add_messages` is a real `function`, NOT a `MagicMock` — a mock sneaking past the eviction otherwise passes vacuously.
3. Two-layer verification:
   - Direct reducer call: `add_messages([hint_1], [hint_2])` with shared stable id → exactly 1 block, second content wins.
   - Real checkpoint round-trip: `StateGraph` with `Annotated[list, add_messages]` reducer + real `MemorySaver()`; two nodes each emit one same-id hint; read back via `checkpointer.get(config)` → persisted state has exactly 1 block. (Gotcha: langgraph 1.0.9 `MemorySaver.get()` returns a plain dict — `.checkpoint` attribute access is wrong.)

## Why it matters

Review W1+W2 acceptance required supersede verified "against real add_messages upsert behavior, not just the mirrored helper" — this pattern is the cheapest honest way to do that inside a mocked-conftest repo, with zero production impact.
