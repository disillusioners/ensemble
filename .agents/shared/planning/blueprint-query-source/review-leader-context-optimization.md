# Leader Review: Blueprint Query Source — Context Optimization Perspective

> **Reviewer**: Leader (instance 7a0c990c)  
> **Date**: 2026-08-02  
> **Perspective**: Context load optimization session (Tier 1–3A, same day)  
> **Target**: `technical-analysis.md` (453 lines)  
> **Verdict**: ✅ **APPROVED with enhancements** — Option B is correct, but 3 gaps need closing before implementation  

---

## Overall Assessment

This is a **well-structured technical analysis**. The Option A–D comparison is thorough, the integration points are correctly identified with file:line references, and the recommendation (Option B — task message + context param) aligns perfectly with our Tier 2A work. The plan correctly leverages the `context` parameter we just shipped.

The analysis correctly identifies that:
- The `project_injected` gate is the right hook location
- Parent blueprint inheritance (C/D) requires plumbing that doesn't exist
- Graceful degradation to Option A when `context` is absent is essential
- One-shot persistent matching makes wrong-query decisions costly

**No blocking issues found.** The 3 enhancements below improve robustness and align the plan with validated findings from our optimization investigation.

---

## Enhancement 1: Add `load_skill` Content to the Query

### Gap
The analysis considers `task_message` and `context` param as the two query sources. But there's a **third signal**: the `load_skill` meta tag.

When a parent dispatches via `send_message(..., load_skill="code-analysis")`, the skill content is injected as a `[SYSTEM CONTEXT: Skills]` HumanMessage. This skill content contains rich domain-specific vocabulary (analysis patterns, quality criteria, code review checklists) that is **highly relevant** to blueprint matching.

### Evidence
From our session investigation:
- `daemon/tools/instance.py:1509-1516` — `load_skill` appends `` to message
- `daemon/services/instance_messaging.py:2560-2636` — skill content injected via `inject_explicit_skill()`
- Skills are injected at position 0 of `persistent_context_msgs` alongside task context

### Recommendation
Extend the query construction to include matched skill content:

```python
def build_blueprint_query(task_message: str, task_context: str | None, skill_content: str | None) -> str:
    parts = [task_message]
    if task_context and task_context.strip():
        parts.append(task_context)
    if skill_content and skill_content.strip():
        parts.append(skill_content[:2000])  # cap to avoid query bloat
    return "\n\n".join(parts)
```

**Risk**: Query length growth. Mitigation: cap skill content contribution at ~2K chars (skills are typically 1-5K chars; the first 2K usually contains the skill's purpose and scope).

---

## Enhancement 2: Explicitly Handle the `task_context` Truncation

### Gap
The plan flags this as an edge case (#2: "Very long task_context") and mentions a 4K cap, but doesn't specify HOW or WHERE truncation happens.

### Critical Detail
We implemented `_TASK_CONTEXT_MAX_CHARS = 4000` in the Tier 2A hardening (`daemon/tools/instance.py`). The formatted task context is **already capped at 4000 chars** before it reaches the blueprint query. The plan should reference this existing cap rather than proposing a new one.

### Recommendation
- State that `task_context` is already truncated to 4000 chars by `_format_task_context()` before it reaches the MessageQueue row
- The blueprint query builder should NOT re-truncate — it receives already-capped content
- Remove "Truncate task_context to 4K chars" from the technical debt table — it's already paid

---

## Enhancement 3: Add Query Logging for Empirical Evaluation

### Gap
The plan mentions empirical evaluation in open question #3 ("Should the match service emit a debug log?") but defers it. Given that blueprint matching is a one-shot persistent decision with no recovery, we should build observability in from day one.

### Recommendation
Add structured logging at the blueprint match hook:

```python
logger.info(
    "blueprint_match",
    extra={
        "instance_id": instance_id,
        "query_source": "task_only" if not task_context else "task+context",
        "query_length": len(query),
        "matched_count": len(matched),
        "matched_ids": [b["blueprint_id"] for b in matched[:5]],
        "top_score": matched[0]["score"] if matched else 0.0,
    }
)
```

This enables:
- A/B comparison: Option A (task-only) vs Option B (task+context) match quality
- Detection of degenerate queries (empty results, very low scores)
- Telemetry on how often `context` is present vs absent in real dispatches

**Priority**: Do this in v1, not as a follow-up. The empirical evaluation depends on having this data from the start.

---

## Validation Against Session Findings

I'm validating this plan against what we learned during the context optimization investigation (instance 0111b410 + code ground truth):

| Plan Assumption | Session Finding | Verdict |
|---|---|---|
| `task_context` is stored on MessageQueue row metadata | ✅ Confirmed — `task_processor.py:342` extracts from metadata | Correct |
| `project_injected` flag is the natural gate | ✅ Confirmed — `instance_messaging.py:1920-1942` | Correct |
| Context param degrades gracefully when absent | ✅ Confirmed — Tier 2A backward compat verified | Correct |
| Parent blueprint matches are NOT persisted | ✅ Confirmed — `assemble_context_messages()` re-derives each turn | Correct |
| `core.md` should NOT be hardcoded | ✅ Agree — trust the matching algorithm | Correct |
| First message ALWAYS needs a search (no cache for first) | ✅ Consistent with our skill_search_interval design | Correct |
| Match runs once, results persist for instance lifetime | ✅ Consistent with `project_injected` flag semantics | Correct |

**All assumptions validated.** The plan is grounded in correct understanding of the codebase.

---

## Alignment with Optimization Work

This plan directly benefits from three things we shipped today:

1. **`context` parameter (Tier 2A)** — the plan's Option B recommendation depends on this feature. It's shipped, tested, and hardened.
2. **Worker `heuristic_match` flag (Tier 1A)** — workers now receive shared context, so blueprint matching adds a second context layer without conflicting.
3. **`skill_search_interval` (Tier 3A)** — the interval caching pattern we built for skill search could be referenced if blueprint matching ever needs a re-match mechanism (though the plan correctly keeps it one-shot).

---

## Additional Considerations

### Token Budget Interaction
The plan caps at 5 blueprints. Combined with our existing context layers, a child's first-turn persistent block will contain:

| Layer | Source | Est. Tokens |
|---|---|---|
| Task context | Tier 2A `context` param | ~500-2000 (capped 4K chars) |
| Skills | Skill injection | ~500-2000 |
| Shared context RAG | Heuristic match | ~500-2000 |
| **Blueprints (NEW)** | Blueprint match | ~500-2500 (5 × ~500 each) |
| Project metadata | Project JSON + notes + history | ~500-1000 |

**Total estimated**: 2.5K-9.5K tokens of injected context on first turn. This is within acceptable bounds but worth monitoring. If blueprints are verbose, consider a per-blueprint char cap (e.g., 2K chars each → max 10K chars = ~2.5K tokens).

### Query Length Sensitivity
The plan assumes BM25 + vector similarity handles variable-length queries well. This needs empirical validation. Our skill search uses a 3-stage pipeline (BM25 → embedding → LLM) specifically because single-stage matching has quality issues. Consider:
- Is BM25 + vector sufficient for blueprint matching?
- Or does blueprint matching also need an LLM re-rank step?
- The plan correctly rejects LLM re-rank for latency reasons — but flag this as an empirical risk

### Concurrency
Edge case #8 assumes `project_injected` is set under a per-instance lock. Our investigation confirmed this — the `project_injected` flag write happens within the same DB transaction as the first-message processing, protected by the turn-reconciler infrastructure. This is correct.

---

## Summary

| Aspect | Rating | Notes |
|---|---|---|
| **Architecture** | ⭐⭐⭐⭐⭐ | Correct integration points, clean data flow |
| **Option analysis** | ⭐⭐⭐⭐⭐ | Thorough A-D comparison, correct recommendation |
| **Implementation readiness** | ⭐⭐⭐⭐ | Enhancement 1 (skill content) should be added before implementation |
| **Risk coverage** | ⭐⭐⭐⭐ | Good edge cases, needs query observability |
| **Alignment with session work** | ⭐⭐⭐⭐⭐ | Directly leverages Tier 2A, validated against real code |

**Recommendation**: Proceed with implementation after incorporating the 3 enhancements. The plan is architecturally sound and well-researched.

---
