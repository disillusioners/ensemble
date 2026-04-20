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
**Decision**: Construct the multimodal `HumanMessage` content array in `manager._process_message_with_tracking()` before passing to the graph.
**Alternatives considered**:
- Do it inside `graph.py:agent_node()` — couples graph to image logic
- Do it in the API layer — too early, images not yet persisted
**Rationale**: Manager is the orchestrator. It already handles message formatting (HumanMessage creation at line 1215). Multimodal construction belongs here alongside that logic.

### DEC-003: Vision model usage scope
**Decision**: Use vision model for the ENTIRE graph execution when images are present (not just the first LLM call).
**Rationale**: The graph may make multiple LLM calls (tool use loops). If the context contains images, all subsequent calls should use the vision model because the multimodal content is in the conversation history. The simplest approach is to pass `model_vision` into `build_instance_graph()` and let it create the LLM with that model when images are present.

### DEC-004: Image size validation location
**Decision**: Validate on BOTH frontend (immediate UX feedback) and backend (security/defense in depth).
**Limits**: Max 3 images, max 10MB per image.

### DEC-005: MessageInput component output type change
**Decision**: Change `@Output() sendMessage` from `EventEmitter<string>` to `EventEmitter<MessagePayload>` where `MessagePayload = { content: string; images: string[] }`.
**Rationale**: The single consumer is `ChatComponent.onSendMessage()`. Since this is a standalone component with one known consumer, changing the output type is clean and type-safe. No other components use this output.
