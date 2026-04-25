# Tracking: Innate-Skills Architecture Refactoring

## Iteration 001 — 2026-04-25

**Verdict: APPROVED**

### Council Findings

**Council 1 (Code Completeness & Correctness):**
- Call sites: ✓ Complete — 1 production call site at loader.py:520
- Cache key update: Initially flagged as BLOCKING, but Phase 3 lines 96-122 contain explicit pseudocode. NOT a gap.
- find_skill() sequencing: Initially flagged as BLOCKING, but Phase 3 Task 6 explicitly adds AgentMetadata.innate_skills field. NOT a gap.
- Empty array check: NON-BLOCKING — truthy check is intentional defense-in-depth (Decision 6)
- _baby_template, thread safety: No issues

**Council 2 (Internal Consistency):**
- sorted() redundancy: Intentional defense-in-depth. NOT an issue.
- Truthy check necessity: Valid design choice, documented in Decisions. NOT an issue.
- compose_system_prompt() keys: Verified identical between old and new paths. NOT an issue.
- Baseline capture sequencing: Phase 4 Task 1 says "before any changes" but Phase 4 runs after Phases 1-3. **Documentation clarity issue** — intent is clear, executor should capture baseline before Phase 1.
- Agent count: Plan says "12 agents" but only 10 are discoverable (_inner_soul has no meta.json, _baby_template is in SKIP_DIRS). **Factual error in docs** — doesn't affect implementation since neither has skills.
- SKIP_DIRS safety: Recommended adding innate-skills to SKIP_DIRS as defense-in-depth. NON-BLOCKING.

### Notes (Non-blocking)
1. Phase 4 Task 1 (baseline capture) should be a pre-step before Phase 1, not inside Phase 4
2. Agent count references should be 10, not 12; remove _inner_soul and _baby_template from verification table
3. Consider adding "innate-skills" to SKIP_DIRS as defense-in-depth
