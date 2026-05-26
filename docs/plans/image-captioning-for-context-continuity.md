# Vision Model Routing & Image Captioning

> **Status**: RFC — needs team discussion  
> **Created**: 2026-05-26  
> **Related**: DEC-003 (vision model scope — to be revised), `.agents/shared/planning/vision-support/`

## Background

The current implementation (DEC-003) routes to the vision model only on the **first** LLM call when images are present. All subsequent calls — even if the user sends new images — use the standard text model. This was a simplification that no longer matches requirements.

## Requirements

### R1: Vision model on every image message

**Any message that contains images must be routed to the vision model**, regardless of position in the conversation. This means:

- Turn 1: User sends image → vision model
- Turn 2: User sends text → standard model
- Turn 3: User sends another image → vision model again
- Turn 4: User sends text → standard model

The `is_first_call` check in `daemon/graph.py:356` must be replaced with a per-message-level check: does the **latest user message** (the one being processed) contain images?

### R2: Image captioning for text-only continuity

After the vision model processes an image, the `image_url` blocks must be replaced with a **text caption** in the stored message. This ensures:

- Subsequent standard model calls never receive `image_url` blocks they cannot process
- The conversation retains image context in plain text form
- Compaction/summarization works correctly (no raw multimodal blocks)

### R3: Compaction must handle multimodal content defensively

Even with captioning in place, `daemon/compaction.py:_summarize_single_batch()` must correctly handle multimodal `msg.content` — extract text, skip `image_url` blocks — as a safety net for any edge cases where captioning hasn't run yet.

## Current Behavior (to be changed)

```
graph.py agent_node():
  has_images = any message in history has image_url blocks
  is_first_call = no AIMessage exists in state yet
  use_vision_model = is_first_call AND has_images   ← PROBLEM
```

**What breaks today:**

| Scenario | Current behavior | Expected behavior |
|----------|-----------------|-------------------|
| User sends image on turn 1 | Vision model ✅ | Vision model ✅ |
| User sends text follow-up on turn 2 | Standard model, but receives old `image_url` blocks from history ❌ | Standard model, old image replaced by caption ✅ |
| User sends image on turn 3+ | Standard model (is_first_call=False) ❌ | Vision model ✅ |
| Compaction summarizes image messages | `str(list)` garbage in prompt ❌ | Extract text only / caption available ✅ |

## Proposed Design

### Part A: Per-Message Vision Routing

Instead of `is_first_call`, check whether the **current message being added to the graph** contains images:

```
agent_node(state):
  latest_human_msg = last HumanMessage in state
  current_msg_has_images = image_url blocks in latest_human_msg.content
  use_vision_model = current_msg_has_images AND model_vision is configured
```

Key detail: within a single graph execution (tool call loops, retries), the vision model should only be used on the **first agent_node invocation** for that user message. Subsequent agent_node calls within the same graph run (after tool execution) should use the standard model — the image has already been seen.

This means we need to distinguish:
- **New user message arrives** → check for images, pick model
- **Agent loop continues** (tool result → next LLM call) → always standard model

**Open question for team**: How to detect "new user message" vs "tool loop continuation"? Options:
- Check if the last message is a HumanMessage (new input) vs ToolMessage (tool loop)
- Use a flag in graph state (e.g., `_vision_used: bool`)
- Check if any AIMessage exists after the latest HumanMessage

### Part B: Post-Response Image Captioning

After the vision model produces its first response for an image message:

```
1. Vision model responds to image → AI response streamed to user
2. New step (after response, before next user turn):
   a. Find the HumanMessage with image_url blocks in state
   b. Generate caption for images (see Q1 below)
   c. Replace multimodal content with: "[User attached N image(s). Description: <caption>]\n<original text>"
   d. Update graph state — message content is now plain text
3. All subsequent calls (standard model, compaction) see text only
```

### Part C: Compaction Defensive Fix

In `compaction.py:_summarize_single_batch()`, when `msg.content` is a list:

```python
# Current (broken):
conversation_parts.append(f"User: {msg.content}")  # str(list) → garbage

# Fixed:
text = extract_text_from_content(msg.content)  # skip image_url blocks
conversation_parts.append(f"User: {text}")
```

This is a small, independent fix that should ship regardless of the captioning approach.

## Open Questions for Team Discussion

### Q1: Caption generation strategy

| Option | Pros | Cons |
|--------|------|------|
| **A. Separate captioning call** | Explicit, controlled quality; can use cheaper model | Extra LLM call = latency + cost |
| **B. Extract from AI response** | No extra call | Response may not be a clean image description |
| **C. Dedicated captioning prompt** | Clean separation | Extra call + complexity |

### Q2: Caption timing

| Option | Pros | Cons |
|--------|------|------|
| **A. Immediately after vision response** | Consistent state from that point | Latency on critical path (~1-2s) |
| **B. Async background task** | No user-visible latency | Race condition if user sends follow-up before replacement |
| **C. Lazy — replace before standard model call** | No upfront cost | Complex mid-graph detection |

### Q3: Caption format

- **Per-image**: `[Image 1: A sunset over mountains]`
- **Combined**: `[User attached 2 images showing a landscape scene]`

### Q4: Original image preservation

- Raw images stay in `message_queue` DB (`images` JSON column) — always queryable
- Should we also keep originals in graph state metadata, or is the DB copy sufficient?
- Trade-off: state size (checkpoints) vs ability to re-process with different model later

### Q5: Vision model for tool-loop continuation?

When a user sends an image and the agent calls a tool, then loops back to LLM:
- First invocation: vision model (sees image) ✅
- Second invocation (after tool result): standard model? Or vision model again?

**Recommendation**: Standard model for tool-loop calls. The image was already processed on the first pass. This avoids unnecessary vision model cost.

### Q6: Config

- Captioning always-on when `model_vision` is configured?
- Separate flag `llm.image_captioning: true`?
- Fallback if captioning model not specified: use `model_vision`?

## Scope Estimate

| Area | Files | Effort |
|------|-------|--------|
| Per-message vision routing | `daemon/graph.py` | S |
| Caption generation logic | `daemon/graph.py` or new module | M |
| State replacement after vision response | `daemon/graph.py` | S |
| Compaction multimodal fix | `daemon/compaction.py` | XS |
| Config | `daemon/config.py` | XS |
| Tests | `tests/unit/`, `tests/integration/` | M |

**Estimated total**: 5-8 hours after design decisions are made.

## Non-Goals

- Image understanding in tool calls (tools receiving images)
- Streaming the captioning step to the frontend
- Image storage / CDN migration (keeping base64)
- Changing the frontend image upload flow

## Success Criteria

- [ ] Vision model used on ANY message with images, not just the first
- [ ] Standard model never receives `image_url` blocks
- [ ] Text follow-up messages can reference image content via caption
- [ ] Compaction correctly handles multimodal message content
- [ ] Raw images still queryable from `message_queue` DB
- [ ] No regression in text-only message flows
- [ ] Vision model NOT used for tool-loop continuation calls (cost control)
