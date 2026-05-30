# Compaction Multimodal Quick Fix

**Date**: 2026-05-30
**Branch**: `feature/vision-always-on`
**Commit**: `0244021`

## Issue
Tests revealed the original `_extract_text_from_content()` fix was incomplete. While the helper was added, several compaction paths still passed raw multimodal content (list with `image_url` blocks) to functions expecting strings, producing garbage output.

## Root Cause
The `_extract_text_from_content()` helper was applied to 7 call sites, but additional code paths in `emergency_truncate`, `_truncate_batch_to_fit`, `_build_replacement_messages`, and `_truncate_fallback` still operated on raw multimodal content without conversion.

## Fix
Added multimodal-to-string conversion at 4 additional locations:
1. `emergency_truncate` Pass 0 — converts all multimodal content before any truncation checks
2. `_truncate_batch_to_fit` — initial conversion loop for all message types
3. `_build_replacement_messages` — converts preserved message content to strings
4. `_truncate_fallback` — same conversion pattern

## Verification
- 30 new tests in `tests/unit/test_compaction_multimodal.py` — all pass
- 54 existing compaction tests — all pass
- Full regression suite — 0 new failures
