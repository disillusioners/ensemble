# Phase 1: Backend Vision Pipeline

## Objective
Add vision model configuration, extend API/DB models for image attachments, store images in DB, and route multimodal content to the vision-capable LLM when images are present. The vision model applies to the FIRST LLM call only; subsequent graph steps (tool execution, reasoning loops) use the standard text model.

## Coupling
- **Depends on**: None
- **Coupling type**: independent (no other phases touch these files)
- **Shared files with other phases**: None (Phase 2 only reads the API contract)
- **Shared APIs/interfaces**: `MessageCreate.images` field added to API, `MessageResponse.images` field added for `getMessages()`
- **Why this coupling**: Root phase — no prior phases exist. Phase 2 will consume the new API schema but doesn't need its implementation.

## Context
- Previous phase completed: N/A (root phase)
- Key decisions (from architecture spec):
  - Image transport: Base64 inline in JSON payload
  - API payload: `{ content: str, images?: string[] }` — backward compatible
  - DB storage: new `images` JSON column in message_queue table
  - LLM routing: if `images` array non-empty → construct multimodal content + use vision model for first LLM call only

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `model_vision` field to `LLMConfig` | Add `model_vision: Optional[str]` with env `OPENAI_MODEL_VISION`. If `model_vision` is not set and images are sent, return HTTP 400 with clear error message. | `daemon/config.py` |
| 2 | Update `config.yaml` with `model_vision` | Add `model_vision: ${OPENAI_MODEL_VISION:-}` line under `llm:` section. Document in comments. | `config.yaml` |
| 3 | Add `images` field to `MessageCreate` Pydantic model | Add `images: Optional[list[str]] = Field(default=None)` to align with API payload. Add field validation: if images present, max 3, each must be base64 data URI `data:image/...;base64,...` format. | `daemon/models.py:117` |
| 4 | Add `images` column to `MessageQueue` DB model | Add `images: list[str] = Field(default_factory=list, sa_column=Column("images", JSON))` to the SQLModel table. Update `to_dict()` to include `images`. | `daemon/repositories/message_queue/models.py:47` |
| 5 | Add `images` parameter to `enqueue_message` and `dequeue` repository methods | Update `SQLModelMessageQueueRepository.enqueue()` to accept and store `images`. Update `dequeue()` retrieval to include images. | `daemon/repositories/message_queue/repository.py:30` |
| 6 | Create DB migration for `images` column | Create `daemon/migrations/versions/YYYYMMDD_000001_add_images_to_message_queue.sql` with `ALTER TABLE message_queue ADD COLUMN images JSON DEFAULT NULL`. Migration must be additive only (default null) to preserve existing data. | `daemon/migrations/versions/` |
| 7 | Update `send_message` API endpoint to pass images | Modify `daemon/api.py:852` to read `message.images` and pass to `manager.enqueue_message()`. | `daemon/api.py:852` |
| 8 | Update `manager.enqueue_message()` to accept and store images | Add `images: Optional[list[str]]` parameter. Pass to `_queue_repository.enqueue()`. Store in message_metadata if needed for tracking. | `daemon/manager.py:907` |
| 8b | Pass `model_vision` into `llm_config` construction in `build_instance_graph()` | When `model_vision` is set, include it in `llm_config` so the graph uses the vision model from the start (first LLM call). | `daemon/graph.py` |
| 9 | Update `_process_message_with_tracking()` to extract images and construct multimodal content | Read `images` from the MessageQueue record. Construct multimodal `HumanMessage` with content array: `[{type: "text", text: message}, {type: "image_url", image_url: {"url": img}}]`. Pass to graph. | `daemon/manager.py:1026` |
| 11-12 | Update `build_instance_graph()` and `agent_node()` for vision model | Conditionally use `model_vision` in `llm_config` when `model_vision` is set. The graph is built with the vision model so the first LLM call uses it. Update `agent_node()` to log when vision model is in use. | `daemon/graph.py:278`, `daemon/graph.py:357` |
| 13b | Update `serialize_message()` to preserve image data in checkpoints | Update `serialize_message()` in `daemon/utils.py` to detect multimodal HumanMessage content (list of content blocks) and extract both text and `image_url` blocks. Images must survive checkpoint serialization. | `daemon/utils.py` |
| 15 | Add `images` field to `MessageResponse` Pydantic model | Add `images?: string[]` field to `MessageResponse` so the API returns images in `getMessages()`. | `daemon/models.py` |
| 16 | Add unit tests | Add tests for: (a) `MessageCreate` validation (max 3 images, max 10MB each, base64 data URI format), (b) multimodal `HumanMessage` construction with mixed text+image and image-only, (c) text-only path unchanged regression test. | `tests/` |

## Key Files

### Modified Files
| File | Change |
|------|--------|
| `daemon/config.py` | Add `model_vision: Optional[str]` to `LLMConfig` |
| `config.yaml` | Add `model_vision: ${OPENAI_MODEL_VISION:-}` under `llm:` |
| `daemon/models.py:117` | Add `images` field to `MessageCreate`; add `images` to `MessageResponse` |
| `daemon/repositories/message_queue/models.py` | Add `images` JSON column to `MessageQueue` SQLModel |
| `daemon/repositories/message_queue/repository.py` | Add `images` param to `enqueue()`, include in `dequeue()` |
| `daemon/migrations/versions/` | New migration file for `images` column |
| `daemon/api.py:852` | Pass `images` from request to `enqueue_message()` |
| `daemon/manager.py:907` | Add `images` param to `enqueue_message()`, `_process_message_with_tracking()` constructs multimodal HumanMessage |
| `daemon/graph.py` | Conditionally use vision model in `build_instance_graph()`; `agent_node()` logs vision model usage |
| `daemon/utils.py` | Update `serialize_message()` to handle multimodal content blocks |

### New Files
| File | Purpose |
|------|---------|
| `daemon/migrations/versions/YYYYMMDD_000001_add_images_to_message_queue.sql` | DB migration for `images` column |
| `tests/test_vision*.py` | Unit tests for vision support |

## Constraints
- Must remain backward compatible: text-only messages use existing code path unchanged
- Image size validation: max 10MB per image (reject with 400 error if exceeded)
- Max 3 images per message (reject with 400 error if exceeded)
- Images must be valid base64 data URIs (`data:image/<format>;base64,<data>`)
- If `model_vision` is not set and images are sent, return HTTP 400 with clear error message
- Vision model used for FIRST LLM call only; subsequent steps use text model
- DB migration must be additive only (default null) to preserve existing data
- Do NOT break existing unit tests

## Deliverables
- [ ] `LLMConfig` has `model_vision` field with env var `OPENAI_MODEL_VISION`
- [ ] `config.yaml` updated with `model_vision` env var interpolation
- [ ] `MessageCreate` Pydantic model accepts `images?: string[]`
- [ ] `MessageResponse` Pydantic model returns `images?: string[]` in `getMessages()`
- [ ] `MessageQueue` DB model has `images` JSON column
- [ ] DB migration creates `images` column (additive, default null)
- [ ] `send_message` API endpoint passes images to queue
- [ ] `enqueue_message()` stores images in DB
- [ ] `_process_message_with_tracking()` constructs multimodal `HumanMessage`
- [ ] Vision model used for first LLM call when images present, text model otherwise
- [ ] `serialize_message()` preserves image data in checkpoints
- [ ] Logging distinguishes vision vs text model requests
- [ ] Existing text-only path untouched and functional
- [ ] Unit tests for validation, multimodal construction, and regression
