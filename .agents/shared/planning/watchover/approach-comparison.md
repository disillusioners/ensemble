# Approach Comparison: Watchover Interception Strategy

Date: 2026-08-05T20:52:36Z
Architect Instance: architect (council mode)
Basis: Planner's technical-analysis.md §G (4-option comparison) + resilience council validation

---

## Interception Approach Comparison (Structural)

The planner evaluated 4 structural approaches for where to intercept tool
calls. The architect validates the planner's recommendation (Option A) and
adds the resilience council's confirmation.

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|----------|------------|-------------|-----------------|------|------|----------------|
| **A: New `watchover_check` node** (agent → watchover_check → tools) | Medium | Per-instance; one cheap router eval when OFF | High — matches `create_post_tools_router` + slot pattern | Low after topology tests | One cheap node when OFF; watcher calls when ON | ✅ **RECOMMENDED** — strongest unbypassability, cleanest separation |
| **B: Wrap `should_continue`** (language-check style) | Medium | Same as A | Medium — mixes watchover into router that already composes language check | Medium — routing logic in a function that runs every turn even when OFF | Same LLM cost | Viable but accumulates concerns in `should_continue`; harder to reason about OFF-path |
| **C: Wrap `ToolNode`** (subclass) | Medium-high | Same | Low — couples evaluation to execution; fragile across LangChain versions | High — future code path could call ToolNode directly, bypassing wrapper | Same LLM cost | Rejected — breaks NFR-12 (unbypassable) |
| **D: Generic middleware** | High | Framework overhead | Low — new abstraction, touches every node | High — explicitly rejected by C-1/C-5 | Same + framework maintenance | Rejected — violates project constraint |

**Verdict:** Option A is confirmed. The council found no structural reason to
deviate. The unbypassability guarantee (no `agent → tools` edge, provable via
topology test) is decisive.

---

## Failure-Handling Approach Comparison (Resilience)

The planner mandated fail-closed (Deny) on ALL watcher errors (AD-6). The
resilience council identified this as a self-DoS risk and recommends a
bifurcated approach.

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|----------|------------|-------------|-----------------|------|------|----------------|
| **A: Fail-closed on ALL errors** (planner's AD-6) | Low | Constant-time fallback | High — simple rule | 🔴 High — provider outage → mass termination of all watched instances | Bounded by timeout | Rejected for production — self-DoS cascade |
| **B: Bifurcated — fail-open (infra) / fail-closed (judgment)** (council) | Low-medium | Constant-time fallback | Medium — two error classes | Low — provider outage → degraded safety mode, not mass kill | Bounded by timeout | ✅ **RECOMMENDED** — prevents cascade, preserves safety for real judgment failures |
| **C: Fail-open on ALL errors** | Low | Constant-time | High | 🔴 Critical — dead watcher silently allows everything | Zero | Rejected — defeats the safety feature |

**Verdict:** Approach B (bifurcated). Infrastructure errors (timeout, 5xx,
network) fail-open with a degraded-safety SSE notification. Judgment errors
(malformed response, unparseable verdict, config invalid) fail-closed.

---

## Parallel Tool Call Handling Comparison (Complexity)

The planner's design decision 6 proposed full mixed-batch support. The
resilience council unanimously recommends simplifying to deny-whole-batch.

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|----------|------------|-------------|-----------------|------|------|----------------|
| **A: Deny-whole-batch** (if ANY call denied, deny entire batch) | Low | Linear in batch size | High — simple, no state juggling | Low — no checkpoint/restart surface | Same per-call eval cost | ✅ **RECOMMENDED for phase 1** — strictly safer, eliminates finalize node |
| **B: Mixed-batch** (execute allowed subset, inject denials for rest) | High | Linear, but with message-replacement state | Low — checkpoint/restart at every node boundary | 🔴 High — inconsistent checkpoint state on crash | Same + state management overhead | Phase 2 enhancement — only if per-call execution is a hard product requirement |

**Verdict:** Approach A (deny-whole-batch) for phase 1. This re-scopes AC-EC.9
from "each call independently applied" to "each call independently evaluated;
a denied call blocks its batch."

**Trade-off:** A safe tool call in a mixed batch won't execute. For a safety
feature, false negatives (allowing unsafe) are far worse than false positives
(blocking safe). The agent can retry the safe call separately.

---

## Termination Approach Comparison

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|----------|------------|-------------|-----------------|------|------|----------------|
| **A: Deferred marker + persistent intent + post-graph cascade** (planner AD-3 + council refinement) | Medium | Existing cascade | High — matches question_pause_node | Low — persistent marker closes crash window | No new infra | ✅ **RECOMMENDED** |
| **B: Direct `terminate_instance()` in node** | Low | Existing cascade | Low — violates C-6 pattern | 🔴 High — self-cancels graph task, C2 torn-state | No new infra | Rejected — proven bug class |

**Verdict:** Approach A confirmed. The persistent `watchover_pending_termination`
in `instance_metadata` is the critical addition over a RAM-only marker.

---

## Summary: What Changed from the Planner's Plan

| Planner Decision | Architect Recommendation | Change Type |
|-----------------|--------------------------|-------------|
| AD-1: `watchover_check` node | ✅ Confirmed | No change |
| AD-2: Lightweight LLM call | ✅ Confirmed | No change |
| AD-3: Deferred termination | ✅ Confirmed + add persistent marker | Reinforcement |
| AD-4: instance_metadata JSONB | ✅ Confirmed + add `set_metadata_many` | Minor addition |
| AD-5: Real watcher agent def | ✅ Confirmed | No change |
| AD-6: Fail-closed ALL errors | 🔴 **Changed → bifurcated** (fail-open infra / fail-closed judgment) | **Material change** |
| AD-7: No child inheritance | ✅ Confirmed | No change |
| AD-8: Loop-breaker exclusion | ✅ Confirmed | No change |
| Decision 6: Mixed-batch support | 🔴 **Changed → deny-whole-batch** for phase 1 | **Material simplification** |
| Threshold: 3 denials | 🟡 **Suggest 5** (configurable) | Tuning recommendation |
| In-flight tool safety (NFR-15) | 🟡 **Document as partial** | Honesty adjustment |
| SSE cleanup ordering | 🔴 **Must fix** — reorder after post-commit events | Bug fix prerequisite |
