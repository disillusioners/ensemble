# Test Report: Vision Always-On + Compaction Multimodal Fix
Date: 2026-05-30
Branch: `feature/vision-always-on`
Commits: `20c55ce` (initial), `4a714dd` (review fixes), `9a0b2b0` (vision routing tests), `0244021` (compaction quick fixes)

## Summary
- **Total**: 142 tests (across affected files) | **Passed**: 142 | **Failed**: 0 | **Errors**: 0
- **Unit Tests**: 142 tests | **Mock Tests**: N/A (behavior tested via mocked LLMs)
- **ensure.md**: ✅ PASS (dev.sh stable 30s)
- **Quick Fixes Applied**: 1 fix in compaction.py (incomplete multimodal conversion)
- **New Test Files**: 2 (`test_vision_routing.py`, `test_compaction_multimodal.py`)

## What Was Changed
1. **`daemon/graph.py`** — Removed `is_first_call` condition from vision model routing. Vision model now used whenever images present (`has_images AND model_vision AND llm_standard`), regardless of conversation turn.
2. **`daemon/compaction.py`** — Added `_extract_text_from_content()` helper + applied it to 7 call sites. Additional fix (commit `0244021`): added multimodal-to-string conversion in `emergency_truncate`, `_truncate_batch_to_fit`, `_build_replacement_messages`, `_truncate_fallback`.

## Unit Test Results

### Existing Tests (Regression)
| Suite | Total | Passed | Status |
|-------|-------|--------|--------|
| `tests/unit/test_compaction.py` | 54 | 54 | ✅ PASS |
| `tests/unit/test_vision.py` | 45 | 45 | ✅ PASS |

### New: Vision Routing Tests (`tests/unit/test_vision_routing.py`)
13 tests covering:
- Vision model selected when images present (core behavior)
- Standard model for text-only messages
- Fallback when `model_vision` not configured
- Fallback when `llm_standard` is None
- **Images on turn 3+ still use vision model** (the core fix)
- Multiple images, mixed content, AIMessage with images
- Tool binding verification

All 13/13 PASS | Commit: `9a0b2b0`

### New: Compaction Multimodal Tests (`tests/unit/test_compaction_multimodal.py`)
30 tests covering:
- `emergency_truncate` with multimodal content (6 tests)
- `_truncate_batch_to_fit` with multimodal content (5 tests)
- `ContextCompactor` summarization with multimodal content (5 tests)
- Garbage output prevention (6 tests)
- Edge cases: empty lists, only images, malformed blocks, unicode (5 tests)
- Integration: full cycles, round-trip, end-to-end (3 tests)

All 30/30 PASS | Commit: `0244021`

## Quick Fixes Applied
- **Commit `0244021`**: Tests revealed original fix was incomplete. Added multimodal-to-string conversion in 4 additional locations in `daemon/compaction.py`:
  - `emergency_truncate` Pass 0: converts all multimodal content before truncation checks
  - `_truncate_batch_to_fit`: initial conversion loop for all message types
  - `_build_replacement_messages`: converts preserved message content to strings
  - `_truncate_fallback`: same fix

## ensure.md Validation
- ✅ dev.sh ran stable for 30s (exit code 124 = timeout killed = good)
- Server started, RAG auto-test passed, MCP warmup complete, WorkerPool started
- Clean shutdown after timeout

## Code Changes Summary
| File | Change | Commit |
|------|--------|--------|
| `tests/unit/test_vision_routing.py` | New: 13 vision routing tests | `9a0b2b0` |
| `tests/unit/test_compaction_multimodal.py` | New: 30 compaction multimodal tests | `0244021` |
| `daemon/compaction.py` | Fix: additional multimodal string conversion | `0244021` |

## Overall Status
- Unit Tests: ✅ PASS (142/142)
- Regression: ✅ PASS (0 new failures)
- ensure.md: ✅ PASS (dev.sh stable 30s)
- **Testing Complete**: ✅ READY
