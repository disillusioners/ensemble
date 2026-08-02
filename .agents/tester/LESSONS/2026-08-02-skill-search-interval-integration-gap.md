# LESSON: skill_search_interval — Unit Tests Replicate Gate Logic, Don't Exercise Production Path

**Date:** 2026-08-02  
**Feature:** Configurable Skill Search Frequency (`skill_search_interval`)  
**Branch:** `feature/skill-search-interval`

## Problem

The developer wrote 22 unit tests for `skill_search_interval` that cover the Pydantic field validation, the InstanceManager counter methods, and the gate decision logic. However, the gate tests replicate the production condition in a **helper function** (`_gate_decides_skip()`) rather than calling the real production method `_process_message_with_tracking`.

This means:
- The tests verify the **intended contract** of the gate condition
- But do NOT prove the **real method** actually implements it correctly
- Could mask bugs where the production code wraps the condition with different control flow, resets the counter at the wrong time, or reads the cache incorrectly

This is a common pattern when testing conditional logic: the test author extracts the condition into a pure function for testability, but this decouples the test from the production call site.

## Root Cause

The gate logic lives inside a complex async method (`_process_message_with_tracking`) that has many dependencies (manager, registry, skill injection service, context assembly). Testing it end-to-end requires significant mock setup. The developer chose the simpler path of testing the isolated condition.

## Fix Applied

Created 9 integration tests in `tests/services/test_skill_search_interval_messaging.py` (commit `fe852554`) that exercise the **real** `_process_message_with_tracking` method:
- Uses the capturing-graph pattern from `test_instance_messaging_skill_injection.py`
- Manager mocks backed by **real** `_skill_search_message_counts` and `_context_skill_results` dicts
- Verifies `inject_skills` call counts across multi-message sequences
- Verifies cached values are correctly stored and reused
- Verifies cleanup removes per-instance state

## Pattern to Apply Going Forward

When a feature adds conditional logic to a complex method:
1. **Unit tests on isolated logic are necessary but NOT sufficient** — they verify the contract
2. **Always add integration tests through the real method** — they verify the wiring
3. **The key signal: if test helpers duplicate production conditionals, there's an integration gap**
4. **Look for test setup that explicitly disables the feature** — existing tests that set `skill_injection=False` or `service=None` are NOT regression coverage for the new feature

## What the Integration Tests Caught

No production bugs — the feature implementation is correct. But the integration tests provide confidence that unit tests alone could not:
- The counter increment happens BEFORE the gate check (pre-increment value used)
- The counter reset happens AFTER the search branch completes
- The cached result tuple shape `(injection_text, injected_skill_ids)` is preserved through reuse
- `_cleanup_instance_state` correctly removes both new dicts
- `load_skill` does NOT suppress the auto-search on first message (documents actual behavior)
