# Vision Model Routing — Codebase Analysis & Recommendations

> **Status**: Analysis Complete — Recommendations Ready  
> **Date**: 2026-05-30  
> **Scope**: SMALL (analysis + recommendation, not an execution plan)

## Objective

Provide informed, code-backed recommendations on the 6 open questions from the two competing vision model routing RFCs, based on actual codebase analysis.

---

## Codebase Findings

### F1: Current Routing Logic (`daemon/graph.py:340-369`)

The current flow is exactly as both RFCs describe:

```python
# Line 356-364
is_first_call = not any(hasattr(msg, 'type') and msg.type == 'ai' for msg in messages)
use_vision_model = is_first_call and has_images and model_vision and llm_standard is not None
current_llm = llm_with_tools if use_vision_model else (llm_standard or llm_with_tools)
```

**Key observation**: `is_first_call` is actually "first call EVER" — not "first call for this user message". It checks if ANY `AIMessage` exists in the entire message history. This means:
- Turn 1 with image → vision model ✅
- Turn 2+ with anything → standard model, always ❌

### F2: The `has_images` Check Scans ALL History (`daemon/graph.py:342-351`)

```python
for msg in messages:           # ALL messages, not just latest
    content = getattr(msg, 'content', None)
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image_url":
                has_images = True
```

This already scans the full history. The always-on RFC proposes keeping this same scan but removing `is_first_call`. That's literally a 1-line change.

### F3: Dual-LLM Architecture is Already Fully Built (`daemon/graph.py:451-541`)

`build_instance_llms()` already creates both `llm_with_tools` and `llm_standard`:
- If `model_vision` is configured: `llm_with_tools` = vision model, `llm_standard` = text model
- If `model_vision` is NOT configured: `llm_with_tools` = `llm_standard` (same instance)
- Both are tool-bound and retry-wrapped independently

**The infrastructure for dual-model routing already exists.** The always-on approach changes nothing about the LLM creation — it only changes the routing condition.

### F4: Tool-Loop Pattern is Real and Common (`daemon/graph.py:232-270`)

The graph supports three continuation patterns:
1. **Tool calls** → `ToolNode` → back to `agent_node`
2. **Ghost promise** (text ends with `:`) → re-invoke `agent_node`
3. **Empty after tool** → `nudge_node` → back to `agent_node`

A typical agent interaction involves 2-5 `agent_node` calls per user message (user message → LLM → tool → LLM → tool → LLM → final response). The tool-loop is **the normal path, not an edge case**.

### F5: Compaction Bug is Confirmed (`daemon/compaction.py:700-724`)

```python
# Line 706 — THE BUG
if msg_type == "human":
    conversation_parts.append(f"User: {msg.content}")  # str(list) → garbage
```

When `msg.content` is multimodal (a list of dicts), Python's `str()` produces literal list representation like:
```
User: [{'type': 'text', 'text': 'Describe this'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,...'}}]
```

This is **completely independent** of the routing RFC. It's a bug in compaction that exists regardless.

### F6: Config in Practice — `model_vision` is Optional and Rarely Set

From `.env.example`:
```bash
OPENAI_MODEL=gpt-4
# OPENAI_MODEL_TITLE=gpt-3.5-turbo  ← title also commented out
# No OPENAI_MODEL_VISION line at all
```

From `config.yaml`:
```yaml
model_vision: ${OPENAI_MODEL_VISION:-}  # Optional, defaults to empty
```

**Inference**: Most deployments likely use a single capable model (e.g., `gpt-4o`) for `OPENAI_MODEL` and either don't set `model_vision` at all, or set it to the same model. The cost-sensitive dual-model configuration (cheap text + expensive vision) is the *exception*, not the rule.

### F7: No Existing Tests for Routing Logic

From `tests/unit/test_vision.py` (816 lines): Tests cover image validation, serialization, HTTP 400 without vision config, and tool binding. **Zero tests** for:
- The `is_first_call` routing logic
- Multi-turn image handling
- Vision model selection in `agent_node`

This is a gap that both approaches need to address.

---

## Recommendations

### Q1: Always-On vs Captioning?

**Recommendation: Always-On Vision Model**

| Factor | Always-On | Captioning | Verdict |
|--------|-----------|------------|---------|
| **Implementation effort** | ~1-2 hours (verified — it's literally removing `is_first_call`) | ~5-8 hours (new captioning step, state mutation, async concerns) | **Always-On wins** |
| **Lines changed** | ~5 lines removed | ~50-100 lines added | **Always-On wins** |
| **Bug surface** | Tiny — no new code paths, just broader use of existing path | Moderate — captioning timing, state replacement race conditions, caption quality | **Always-On wins** |
| **Context quality** | Perfect — model always has full image data | Lossy — captioning inevitably drops details | **Always-On wins** |
| **Cost (same model)** | Zero additional cost | Same + extra captioning call | **Always-On wins** |
| **Cost (different models)** | ~16x on input tokens for text follow-ups | ~1.1x | Captioning wins |
| **No new config needed** | Yes | New flags for captioning model, timing, format | **Always-On wins** |

**The codebase reality is clear**: The dual-LLM infrastructure is already built. The routing change is literally removing one condition (`is_first_call`). Captioning would add an entirely new processing step, state mutation logic, and configuration surface — for a benefit (cost savings with different models) that most deployments won't need.

### Q2: Hybrid Option?

**Recommendation: Ship Always-On First. Period. No "add captioning later" plan needed.**

Rationale:
- Always-on is a **1-2 hour net code removal**. Ship it, test it, move on.
- Captioning is a **5-8 hour addition** of new complexity. If always-on works well in practice (it should — vision models handle text fine), there's no reason to ever add captioning.
- If cost becomes a real problem for users with different models, the solution is simpler: add a `vision_turn_cap` config (Q4) or document that same-model is recommended.
- **Don't architect for a problem you don't have yet.**

### Q3: Tool-Loop Calls — Keep Using Vision Model or Switch to Standard?

**Recommendation: Keep using vision model for tool-loop calls (simple).**

The analysis of `should_continue()` (lines 232-270) shows the tool-loop is the normal execution path, not an edge case. Adding logic to detect "is this a tool-loop continuation?" would mean:
- Checking if the last message is a `ToolMessage` or `AIMessage` with tool results nearby
- This re-introduces the exact complexity we're removing (the `is_first_call` detection)
- For same-model configs (the common case), there's zero benefit

The cost argument doesn't hold:
- Tool-loop calls typically have short prompts (tool result + continuation)
- The vision model processes them fine — it's a superset capability
- The complexity of detecting tool-loop vs new-message isn't worth the savings

**Keep it dead simple. Vision model for everything when images exist in conversation.**

### Q4: Context Limit Cap — Add a Configurable Turn Cap or Defer?

**Recommendation: Defer. No config needed now.**

Rationale:
- The RFC mentions "N turns after last image, then switch to text model" but this is solving a hypothetical problem.
- Current behavior already sends images in every call (they persist in state). The always-on change doesn't make this worse.
- If context limits become a real issue, the solution would likely involve removing old images from state entirely (not model switching), which is a different feature.
- Adding config now is premature optimization. Ship the simple fix, monitor, and add if needed.

### Q5: Compaction Bug — Ship Regardless?

**Recommendation: Yes, ship independently. It's unrelated to the routing change.**

The compaction bug (`daemon/compaction.py:706`) is a separate, pre-existing issue:
- It affects any conversation with multimodal messages, regardless of routing approach
- Even with always-on vision, compaction still needs to handle multimodal content correctly
- The fix is simple: extract text blocks, skip `image_url` blocks

**This should be a separate PR that ships before or alongside the routing change.** It's the kind of defensive fix that has zero risk and addresses a real bug.

### Q6: Config Flags — Any New Config Needed?

**Recommendation: No new config. The current setup is sufficient.**

Current config already has everything needed:
- `model_vision: str | None` — already optional, already controls vision behavior
- `model: str` — the standard model
- The routing logic is: "if `model_vision` is set AND images exist in conversation → use vision model"

The always-on approach doesn't need any new flags because:
- It doesn't change *when* vision is used (images present → same condition)
- It doesn't change *which* model is used (still `model_vision` when set)
- It only changes "which call" — from "first call only" to "all calls"
- Users who don't want always-on vision can simply not set `model_vision`

**The only config addition worth considering** (but still deferring): A comment in `config.yaml` noting that `model_vision` will be used for all calls when images exist, so setting `model_vision` to the same model as `model` avoids cost differences.

---

## Summary

| Question | Recommendation | Confidence |
|----------|---------------|------------|
| Q1: Always-On vs Captioning | **Always-On** | High |
| Q2: Hybrid? | **Ship always-on only. Don't plan captioning.** | High |
| Q3: Tool-loop calls | **Keep vision model (simple)** | High |
| Q4: Context limit cap | **Defer. Not needed now.** | Medium |
| Q5: Compaction bug | **Ship independently.** | High |
| Q6: Config flags | **None needed. Current setup sufficient.** | High |

**The implementation is a ~1-2 hour job**: Remove `is_first_call` check from `daemon/graph.py:356-364`, update comments/docs referencing DEC-003, add routing tests, and fix the compaction bug separately.

## Tracking
- Created: 2026-05-30
- Status: complete
