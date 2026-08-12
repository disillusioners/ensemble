# Approach Comparison: PM Agent Design Decisions

**Date:** 2026-08-12
**Source:** 3-worker competitive fan-out (security-design, structural-design, trade-off-analysis)
**Parent:** `architecture-recommendation.md`

---

## Decision 1: Tool Boundary Strategy

The plan's current approach (allow categories, deny a few tool names) was compared against the corrected approach (allow categories for reads, deny all write tools by exact name, remove write-only categories entirely).

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| **A: Current plan** — allow categories wholesale, deny 6 tool names | Low | Poor — deny list doesn't track category growth | 🟡 Misleading (3 dead-code denies signal false safety) | 🔴 16 write paths open | Low | **Reject** |
| **B: Corrected** — keep read categories, deny all write tools by name, remove write-only categories | Low | Good — individual denies are stable across category growth | 🟢 Clear: category kept for reads, writes denied by exact name | 🟢 Machine-enforced read-only | Low (config change only) | **Adopt** |
| **C: Allow-list only individual read tools** (no categories) | Med (long allow list) | Good | 🟡 Brittle — new read tools added to a category won't auto-appear | 🟢 Safest (default-deny) | Med (must enumerate every read tool) | **Reject for v1** — over-engineered; B achieves the same safety with less maintenance |

**Winner: B.** Keeps the category-based allow list for readability and auto-inclusion of new read tools, while denying every write tool by exact name. The deny list is the safety layer; the allow list is the convenience layer.

---

## Decision 2: PM ↔ Leader Boundary Mechanism

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| **A: Prose-only hand-back** (current plan) — PM emits "hand to leader" in its reply; user acts on it | Low | Good (no coupling) | 🟢 Simple, no mechanism to break | 🟡 No machine path; relies on user reading the tail | Low | **Accept for v1** — document explicitly |
| **B: Parent-of-spawn signal** — PM's completion report reaches its spawner (leader) automatically | Low | Good | 🟢 Built into ensemble | 🟢 Machine path exists | Low | **Investigate** (O3) — if the mechanism exists, document it; if not, A stands |
| **C: Add `instance` to allow + `leader` to team_members** — PM can `send_message` to leader | Med | Poor (couples PM to leader) | 🔴 Breaks stand-alone guarantee | 🔴 Reopens dispatch boundary | Med | **Reject** — violates Cardinal #2 and the stand-alone design |

**Winner: A for v1, B if it exists.** The stand-alone constraint means the PM cannot signal the leader programmatically. Prose-only hand-back is the correct v1 design — it just needs to be documented as intentional, not as a gap.

---

## Decision 3: Workflow Flow Completeness

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| **A: 4 flows as specified** (risk, progress, scope, decision) | Med | Good (independent) | 🟡 F2/F4 have broken steps; flows don't chain | 🟡 Cardinal #7 has no flow entry point | Low | **Fix and accept** — patch F2/F4 step 1, add chain rule |
| **B: 4 flows + F5 Contract Sweep** | Med-High | Good | 🟢 Cardinal #7 operationalized | 🟢 Complete coverage | Low | **Optional** — fold into F1 step 4 instead (Decision D4) |
| **C: Single unified "oversight" flow** with sub-modes | High | Poor (monolithic) | 🔴 Hard to evolve | 🟡 Over-coupled | High | **Reject** — loses the clarity of distinct flows |

**Winner: A (fixed).** The 4-flow structure is sound. The fixes (rephrase F2/F4 step 1, add flow-chain rule, fold contract-sweep into F1) are low-cost and preserve the clean structure.

---

## Decision 4: Future Integration Path

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| **A: Document a "Future Integration Contract"** — id/name stable, allow-list additive, `instance` permanently denied | Low | Good | 🟢 Clear seam for v2 | 🟢 Future PR has a checklist | Low | **Adopt** |
| **B: Build integration hooks now** (e.g., add `team_members: ["leader"]` but keep `instance` denied) | Med | Poor (premature coupling) | 🔴 Coupling before the integration is designed | 🟡 YAGNI risk | Med | **Reject** — premature; v1 is standalone |
| **C: No future planning** — cross that bridge when we get there | Low | Poor | 🔴 Contributor has no guidance | 🟡 `instance` could be accidentally re-added | Low | **Reject** — S1 + I4 findings show the risk |

**Winner: A.** A one-paragraph "Future Integration Contract" in the plan Summary gives the v2 contributor a clear checklist without adding any v1 complexity.
