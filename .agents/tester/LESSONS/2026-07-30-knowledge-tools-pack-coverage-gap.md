# LESSON: test_knowledge_tools.py had no pack coverage

**Date:** 2026-07-30
**Branch:** `feature/explore-caller-model-switch`
**Commit:** `a4c6a32e` (pack creation)

## Problem
PACKS.md listed `tests/unit/tools/test_knowledge_tools.py` under the `context_tools_unit_test` row (line 84), claiming it was covered alongside `test_context_tools.py` and others. However, **no `test/packs/context_tools_unit_test.sh` script exists** — it was a stale documentation entry. The file had 117 tests (now 120) running without any pack script or dual-layer timeout protection.

## Discovery
During blast-radius analysis of the explore caller-model override feature, opencode was dispatched to verify which packs cover `test_knowledge_tools.py` and `test_registry.py`. The grep for `tests/unit/tools/test_knowledge_tools` across all `*.sh` pack scripts returned 0 matches. This meant the 7 new feature tests (class `TestExploreCallerModelOverrides`) would have been invisible to the pack system.

## Fix
1. Created `test/packs/knowledge_tools_unit_test.sh` — standalone pack with dual-layer timeout (Layer 1: `timeout 120s`, Layer 2: internal `timeout 110s`)
2. Registered in PACKS.md as a new entry with full scope description
3. Updated the stale `context_tools_unit_test` row to note the extraction and remove `test_knowledge_tools.py` from its file list
4. Updated Summary counts (Total 221→222, Unit 173→174)

## Lesson
PACKS.md entries should be validated against actual script existence. The `context_tools_unit_test` entry was likely created when the files were grouped conceptually but no script was ever written. When a pack lists multiple test files in a row, verify that a corresponding `.sh` script exists in `test/packs/` — the PACKS.md is documentation, not enforcement.
