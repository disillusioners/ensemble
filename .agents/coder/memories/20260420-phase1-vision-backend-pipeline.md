# Phase 1 Vision Pipeline — Backend Implementation

## What was implemented
Backend vision pipeline for image support: config, API models, DB column, multimodal message construction, vision model routing for first LLM call, serialization preservation, and 33 unit tests.

## Key Architecture
- `model_vision` field in `LLMConfig` with env var `OPENAI_MODEL_VISION` (follows `model_title` pattern)
- Images stored in `images` JSON column in `message_queue` table (type: `list[str] | None`)
- Multimodal `HumanMessage` constructed in `manager._process_message_with_tracking()` BEFORE passing to graph
- Vision model for FIRST LLM call only — graph creates two LLM instances (vision + standard), checks `is_first_call`
- `serialize_message()` in utils.py preserves image data in checkpoints (extracts from content array)
- `MessageResponse` includes `images` field for `getMessages()`
- HTTP 400 if images sent but `model_vision` not configured

## Key Lessons
1. **DB model type hint matters**: SQLite JSON column stores lists, not dicts. Use `list[str] | None` not `dict[str, Any]`.
2. **Dual-LLM architecture for vision**: Created two LLM instances in graph.py — one with vision model, one standard. First call uses vision if images present, subsequent calls use standard.
3. **First-call detection**: Used `not any(msg.type == 'ai' for msg in messages)` to detect first LLM call.
4. **Empty list normalization**: Convert `images=[]` to `None` in validator for cleaner downstream logic.
5. **Existing test adjustments**: Dual-LLM architecture may affect existing tests that mock LLM creation — test for `classify_llm_errors` expected 1 call but now gets 2.

## Commit
- Hash: 8ec692c
- Message: `feat(vision): backend vision pipeline — config, models, multimodal routing, tests`
- Files: 19 changed (+861/-74)

## Review Issues Found & Fixed
1. Type mismatch in DB model (`dict` → `list`)
2. Vision model scope (first call only, not entire graph)
3. Missing image count logging
4. Empty images list normalization
5. Existing test assertion adjustment for dual-LLM

## Post-Commit Review Fixes (Commit 650eef5)

### Critical Fix: llm_standard not bound to tools when vision NOT configured
- **Bug**: The `else` branch binding tools to `llm_standard` only ran when vision WAS configured
- **Impact**: When `model_vision=None` (default for most users), agent couldn't call any tools
- **Fix**: Always bind tools to `llm_standard` unconditionally

### Security: SVG MIME type rejection (XSS defense-in-depth)
- Changed regex from allow-any-image to allowlist: `png|jpeg|jpg|gif|webp|bmp|tiff`
- Rejects `image/svg+xml` which could contain XSS payloads

### DRY: Extracted helper functions
- `_build_message_content()` in manager.py — replaced 3 duplicate multimodal content construction blocks
- `build_instance_llms()` in graph.py — extracted LLM creation logic from `build_instance_graph()`

### Config filtering: Remove model_vision from non-vision LLM configs
- Filter `model_vision` from summarization, title gen, and standard LLM configs
- Avoids noisy LangChain warnings about unknown model parameter

### Size calculation: Use only base64 portion
- Changed from `len(img)` to `len(img.split(",", 1)[1])` for accurate size estimation
- Previously over-counted by including the `data:image/...;base64,` prefix

### Test additions: 4 new tests
- SVG rejection, boundary size (under/over 10MB), tool binding without vision config
- Fixed 3 existing tests in `test_graph_retry_integration.py` that broke due to `build_instance_llms` extraction

### Total tests: 332 passing (37 vision-specific)
