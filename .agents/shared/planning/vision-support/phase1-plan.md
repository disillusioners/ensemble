# Phase 1: Backend Vision Pipeline

## Objective
Add vision model configuration, extend API/DB models for image attachments, store images in DB, and route multimodal content to the vision-capable LLM when images are present.

## Coupling
- **Depends on**: None
- **Coupling type**: independent (no other phases touch these files)
- **Shared files with other phases**: None (Phase 2 only reads the API contract)
- **Shared APIs/interfaces**: `MessageCreate.images` field added to API
- **Why this coupling**: Root phase — no prior phases exist. Phase 2 will consume the new API schema but doesn't need its implementation.

## Context
- Previous phase completed: N/A (root phase)
- Key decisions (from architecture spec):
  - Image transport: Base64 inline in JSON payload
  - API payload: `{ content: str, images?: string[] }` — backward compatible
  - DB storage: new `images` JSON column in message_queue table
  - LLM routing: if `images` array non-empty → construct multimodal content + use vision model

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `model_vision` field to `LLMConfig` | Add `model_vision: Optional[str]` with env `OPENAI_MODEL_VISION`. Fall back to main model if not set. | `daemon/config.py` |
| 2 | Update `config.yaml` with `model_vision` | Add `model_vision: ${OPENAI_MODEL_VISION:-}` line under `llm:` section. Document in comments. | `config.yaml` |
| 3 | Add `images` field to `MessageCreate` Pydantic model | Add `images: Optional[list[str]] = Field(default=None)` to align with API payload. Add field validation: if images present, max 3, each must be base64 data URI `data:image/...;base64,...` format. | `daemon/models.py:117` |
| 4 | Add `images` column to `MessageQueue` DB model | Add `images: dict[str, Any] = Field(default_factory=dict, sa_column=Column("images", JSON))` to the SQLModel table. Update `to_dict()` to include `images`. | `daemon/repositories/message_queue/models.py:47` |
| 5 | Add `images` parameter to `enqueue_message` and `dequeue` repository methods | Update `SQLModelMessageQueueRepository.enqueue()` to accept and store `images`. Update `dequeue()` retrieval to include images. | `daemon/repositories/message_queue/repository.py:30` |
| 6 | Create DB migration for `images` column | Create `daemon/migrations/versions/YYYYMMDD_000001_add_images_to_message_queue.sql` with `ALTER TABLE message_queue ADD COLUMN images JSON DEFAULT '[]'`. | `daemon/migrations/versions/` |
| 7 | Update `send_message` API endpoint to pass images | Modify `daemon/api.py:852` to read `message.images` and pass to `manager.enqueue_message()`. Update `MessageResponse` to include images (optional). | `daemon/api.py:852` |
| 8 | Update `manager.enqueue_message()` to accept and store images | Add `images: Optional[list[str]]` parameter. Pass to `_queue_repository.enqueue()`. Store in message_metadata if needed for tracking. | `daemon/manager.py:907` |
| 9 | Update `_process_message_with_tracking()` to extract images | Read `images` from the MessageQueue record (via message_metadata or new column). Pass images to `_run_with_vision_model()`. | `daemon/manager.py:1026` |
| 10 | Add `_run_with_vision_model()` helper in manager.py | Takes `message: str, images: list[str]`. Constructs `HumanMessage` with multimodal content array: `[{type: "text", text: message}, {type: "image_url", image_url: {"url": img}}]`. Swaps model to `model_vision` for this call only. Falls back to main model if `model_vision` not set. Returns `AIMessage` response. | `daemon/manager.py` (new method) |
| 11 | Update `agent_node()` in graph.py to handle multimodal messages | Detect if last HumanMessage has `content` that is a list (multimodal). If so, pass to vision model. If text-only, use existing path. This requires passing message content type info or detecting in graph. | `daemon/graph.py:278` |
| 12 | Update graph building to support vision model swap | `build_instance_graph()` already takes `llm_config`. When constructing the LLM in `create_agent_node()`, check if the first message is multimodal. If so, temporarily swap to vision model for that call. OR: construct the HumanMessage with multimodal content and let ChatOpenAI handle it (most providers accept multimodal input directly). | `daemon/graph.py:357` |
| 13 | Handle multimodal responses in `serialize_message()` | Ensure `serialize_message()` utility can serialize list content (already handles it per manager.py:1263-1271 pattern). | `daemon/utils.py` |
| 14 | Add logging for vision model usage | Log when vision model is selected vs. text model. Log image count per request. | `daemon/graph.py`, `daemon/manager.py` |

## Key Files

### Modified Files
| File | Change |
|------|--------|
| `daemon/config.py` | Add `model_vision: Optional[str]` to `LLMConfig` |
| `config.yaml` | Add `model_vision: ${OPENAI_MODEL_VISION:-}` under `llm:` |
| `daemon/models.py:117` | Add `images` field to `MessageCreate` |
| `daemon/repositories/message_queue/models.py` | Add `images` JSON column to `MessageQueue` SQLModel |
| `daemon/repositories/message_queue/repository.py` | Add `images` param to `enqueue()`, include in `dequeue()` |
| `daemon/migrations/versions/` | New migration file for `images` column |
| `daemon/api.py:852` | Pass `images` from request to `enqueue_message()` |
| `daemon/manager.py:907` | Add `images` param to `enqueue_message()`, `_process_message_with_tracking()` |
| `daemon/graph.py` | Handle multimodal HumanMessage content in `agent_node()` |

### New Files
| File | Purpose |
|------|---------|
| `daemon/migrations/versions/YYYYMMDD_000001_add_images_to_message_queue.sql` | DB migration to add `images` JSON column |

## Constraints
- Must remain backward compatible: text-only messages use existing code path unchanged
- Image size validation: max 10MB per image (reject with 400 error if exceeded)
- Max 3 images per message (reject with 400 error if exceeded)
- Images must be valid base64 data URIs (`data:image/<format>;base64,<data>`)
- Fall back to main model if `model_vision` is not configured (with warning log)
- Do NOT break existing unit tests

## Deliverables
- [ ] `LLMConfig` has `model_vision` field with env var `OPENAI_MODEL_VISION`
- [ ] `config.yaml` updated with `model_vision` env var interpolation
- [ ] `MessageCreate` Pydantic model accepts `images?: string[]`
- [ ] `MessageQueue` DB model has `images` JSON column
- [ ] DB migration creates `images` column
- [ ] `send_message` API endpoint passes images to queue
- [ ] `enqueue_message()` stores images in DB
- [ ] `_process_message_with_tracking()` retrieves and passes images
- [ ] Multimodal `HumanMessage` content array constructed when images present
- [ ] Vision model selected when images present, main model otherwise
- [ ] Logging distinguishes vision vs text model requests
- [ ] Existing text-only path untouched and functional
