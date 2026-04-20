# Architecture Decisions: Vision Support

## Pre-decided (from requirements)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Image transport | Base64 inline in JSON | No separate upload endpoint, no file storage, no CDN needed. Simpler for a self-hosted tool. |
| API payload shape | `{ content: str, images?: string[] }` | Backward compatible — existing `content` field unchanged, `images` is optional. |
| DB storage | `images` JSON column in `message_queue` | Co-located with message content, no joins needed, simple to query. |
| LLM routing | If `images` non-empty → vision model | Clean separation, no need to inspect image content. |
| Frontend upload | Base64 conversion client-side | Matches transport format, no multipart/form-data needed. |

## Implementation Decisions

### DEC-001: Vision model config placement
**Decision**: Add `model_vision` field to existing `LLMConfig` class with env var `OPENAI_MODEL_VISION`.
**Alternatives considered**:
- Separate `VisionConfig` class — overkill for one field
- Put in `config.yaml` under a new section — inconsistent with existing LLM config pattern
**Rationale**: `LLMConfig` already has `model` and `model_title` (a similar "variant model" pattern). Adding `model_vision` follows the same convention.

### DEC-002: Where to construct multimodal content
**Decision**: Construct multimodal `HumanMessage` in `manager._process_message_with_tracking()` BEFORE passing to graph. The vision model is used for the FIRST LLM call only (the agent_node's initial invocation). Tool calls, retries, and checkpointing all use the text model. This is the scope boundary.
**Alternatives considered**:
- Do it inside `graph.py:agent_node()` — couples graph to image logic
- Do it in the API layer — too early, images not yet persisted
**Rationale**: Manager is the orchestrator. It already handles message formatting (HumanMessage creation). Multimodal construction belongs here alongside that logic. Keeping vision to the first LLM call only avoids complex model swapping mid-graph.

### DEC-003: Vision model usage scope
**Decision**: Vision model applies to the FIRST LLM call only. After the first LLM response, the graph continues with the standard text model for all subsequent steps (tool execution, reasoning loops, etc.). This keeps the architecture simple — no dynamic model swapping mid-graph.
**Rationale**: The simplest approach is to pass `model_vision` into `build_instance_graph()` and let it create the LLM with that model so the first LLM call uses it. Subsequent graph steps (tool execution, reasoning loops) continue with the text model.

### DEC-004: Image size validation location
**Decision**: Validate on BOTH frontend (immediate UX feedback) and backend (security/defense in depth).
**Limits**: Max 3 images, max 10MB per image.

### DEC-005: MessageInput component output type change
**Decision**: Change `@Output() sendMessage` from `EventEmitter<string>` to `EventEmitter<MessagePayload>` where `MessagePayload = { content: string; images: string[] }`.
**Rationale**: The single consumer is `ChatComponent.onSendMessage()`. Since this is a standalone component with one known consumer, changing the output type is clean and type-safe. No other components use this output.

### DEC-006: Image serialization in checkpoints
**Decision**: Images in `HumanMessage.content` must survive checkpoint serialization. `serialize_message()` in `daemon/utils.py` must be updated to extract and preserve `image_url` content blocks. Images are NOT stripped from checkpoints — the checkpointer stores the full multimodal message as-is.
**Rationale**: Checkpoints are the state of the graph at each step. If we strip images from the serialized messages stored in checkpoints, message history will lose images on page refresh. The checkpointer must store the complete multimodal content array (text + image_url blocks) so that when the graph state is restored, images are still present.
