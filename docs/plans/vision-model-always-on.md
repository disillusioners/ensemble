# Vision Model Always-On for Image Contexts

> **Status**: RFC — needs team discussion  
> **Created**: 2026-05-26  
> **Approach**: Simple — use vision model for all LLM calls when images exist in conversation  
> **Related**: DEC-003 (vision model scope — to be revised), `.agents/shared/planning/vision-support/`  
> **Alternative**: [Image Captioning approach](./image-captioning-for-context-continuity.md)

## Core Idea

**Don't switch models at all.** Once any image enters the conversation, use the vision model for every subsequent LLM call in that instance. No captioning, no state replacement, no post-processing.

Vision models (e.g., `gpt-4o`) can process both text and images. Sending text-only messages through a vision model works fine — it's just slightly more expensive per token. We accept that cost for simplicity.

## Why This Approach

| Factor | Assessment |
|--------|-----------|
| Implementation complexity | **Very low** — remove `is_first_call` check, that's most of the change |
| Risk of bugs | **Low** — no new graph steps, no state mutation, no async captioning |
| Cost impact | **Medium** — vision model on every call (see cost analysis below) |
| Context quality | **Best** — model always has full image data, no information loss from captioning |

## Current Behavior → Proposed Behavior

```
# Current (DEC-003):
is_first_call = no AIMessage in state
use_vision_model = is_first_call AND has_images

# Proposed:
has_images = any message in state contains image_url blocks
use_vision_model = has_images AND model_vision is configured
```

The entire `is_first_call` detection and the dual-LLM switching logic goes away. One check: are there images in the conversation? If yes, vision model. If no, standard model.

## What Changes

### `daemon/graph.py` — `agent_node()`

Remove:

- `is_first_call` detection (lines 356-359)
- `use_vision_model` compound condition (line 364)

Replace with:

```
has_images = any message in state has image_url blocks
use_vision_model = has_images AND model_vision is configured AND llm_standard is not None
current_llm = llm_with_tools if use_vision_model else (llm_standard or llm_with_tools)
```

### `daemon/graph.py` — `build_instance_llms()`

No structural change needed. Both `llm_with_tools` (vision) and `llm_standard` are still created — the routing logic just changes in `agent_node()`.

### `daemon/compaction.py` — `_summarize_single_batch()`

**Still needs a fix.** Line 706 does `f"User: {msg.content}"` which produces garbage for multimodal content. Fix: extract text blocks, skip `image_url` blocks.

This is a separate bug that exists regardless of which approach we choose.

## Cost Analysis

Assumptions (OpenAI pricing, May 2026):

| Model | Input (per 1M tokens) | Output (per 1M tokens) |
|-------|----------------------|------------------------|
| `gpt-4o` (vision) | $2.50 | $10.00 |
| `gpt-4o-mini` (text) | $0.15 | $0.60 |

**Scenario**: A conversation with 1 image message, then 10 text-only follow-ups.

| Approach | Vision model calls | Text model calls | Estimated extra cost |
|----------|-------------------|------------------|---------------------|
| Current (DEC-003) | 1 | 10 | Baseline |
| Always-on vision | 11 | 0 | ~16x more expensive on input tokens |
| Captioning approach | 1 + 1 caption | 10 | ~1.1x |

**Key insight**: The cost multiplier depends heavily on which models are paired. If both `model` and `model_vision` point to the same model (e.g., both `gpt-4o`), there is **zero additional cost**. The cost penalty only exists when `model` is a cheaper text-only model.

### When Always-On Is Cost-Neutral

If the team configures `model: gpt-4o` and `model_vision: gpt-4o`, the always-on approach has no cost downside at all. The "standard" and "vision" LLM instances are the same model.

This is likely the common case for self-hosted setups using a single capable model.

## Trade-Offs vs Captioning Approach

| Dimension | Always-On Vision | Image Captioning |
|-----------|-----------------|-----------------|
| Implementation effort | **~1-2 hours** | ~5-8 hours |
| New code paths | Minimal (remove code) | New captioning step, state mutation |
| Cost (different models) | Higher | Lower |
| Cost (same model) | **Same** | Same + extra captioning call |
| Context quality | **Perfect** — full image data always available | Lossy — caption may miss details |
| Bug surface area | **Tiny** | Moderate (timing, state replacement, race conditions) |
| Checkpoint size | Larger (images stay in state) | Smaller (text replaces images) |
| Compaction fix needed | Yes (independent bug) | Yes (independent bug) |

## Open Questions for Team Discussion

### Q1: Is the cost acceptable?

If the standard deployment uses the same model for both `model` and `model_vision`, cost is a non-issue. But if users configure a cheap text model + expensive vision model, always-on could significantly increase cost.

Should we:
- **A.** Accept the cost and document it (simplest)
- **B.** Add a config flag `llm.vision_always_on: true` (default true) to let users opt out
- **C.** Auto-detect: if `model == model_vision`, always-on; if different, use captioning approach

### Q2: What about tool-loop calls?

With always-on, even tool-loop continuation calls (after tool execution) use the vision model. This is harmless but wasteful — the image was already seen.

Should we still use standard model for tool-loop calls?
- **Yes** — adds back some complexity but saves cost
- **No** — keep it dead simple, vision model handles text fine

### Q3: Image-heavy conversations and context limits

Images consume significant tokens. With always-on vision, the model processes all images on every call. In a conversation with many images, this could:
- Hit context limits faster
- Increase per-call latency

Should we add a configurable cap (e.g., "use vision model for N turns after last image, then switch to text model")?

### Q4: Checkpoint / state size

Images stay in graph state indefinitely. With base64 images (up to ~13MB each, max 3), a single checkpoint could be ~40MB larger. Should we care?

- Current behavior already stores images in checkpoints (DEC-006), so this is not a new problem
- Always-on actually makes this *less* of a concern than captioning — no risk of state inconsistency from replacement

## Scope Estimate

| Area | Files | Effort |
|------|-------|--------|
| Remove `is_first_call`, simplify routing | `daemon/graph.py` | XS |
| Compaction multimodal fix | `daemon/compaction.py` | XS |
| Config flag (if needed) | `daemon/config.py` | XS |
| Tests | `tests/unit/` | S |

**Estimated total**: 1-2 hours. This is a net code *removal* — we're deleting complexity.

## Non-Goals

- Image understanding in tool calls
- Image storage / CDN migration
- Reducing checkpoint size (images already stored per DEC-006)
- Changing frontend image upload flow

## Success Criteria

- [ ] Vision model used for ALL LLM calls when images exist in conversation
- [ ] Standard model used when no images in conversation
- [ ] No `is_first_call` logic remaining
- [ ] Compaction handles multimodal content correctly
- [ ] No regression in text-only message flows
- [ ] No regression in conversations without `model_vision` configured
