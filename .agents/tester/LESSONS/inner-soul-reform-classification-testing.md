# Inner Soul Reform — Testing Lessons

## Key Insight: Persona Prefix Does NOT Bypass Project Check

Phase 1's implementation uses a more sophisticated 3-stage flow than the original plan described:

**Plan description**: Persona prefix match → skip project check entirely → normal classification
**Actual implementation**: Persona prefix match → STILL run project check → if both match, require a persona CATEGORY match in Stage 3

This means:
- `"I should improve my deployment strategy"` → persona prefix "i should" matches AND "deployment" matches project → but no persona category (identity/personality/workflow) matches → **REJECTED**
- `"I should be more careful with deployments"` → "deployments" (plural) does NOT match `\bdeployment\b` (singular) → falls through → **ACCEPTED** (as event fallback)

**Lesson**: When writing tests for classification, always verify against the ACTUAL implementation, not the plan description. The plan described the intended design, but the implementation is more conservative (and more correct — it prevents project content from hiding behind persona prefixes).

## Plural vs Singular Patterns

`\bdeployment\b` matches "deployment" but NOT "deployments". This is actually beneficial:
- "I should be more careful with deployments" → ACCEPTED (legitimate persona reflection)
- "Deployed to production" → REJECTED (compound verb pattern)

But it means tests must be precise about pluralization.

## Compound Request Per-Part Classification

Each part of a compound request (split on " AND ") is classified independently. This means:
- `"Be more concise AND I deployed the new build to k8s"` → Part 1: personality (accepted), Part 2: project_knowledge (rejected)
- The rejection appears inline in the compound response, not as an error

## "knowledge" Category Removal Impact

Only 9 tests actually broke (plan predicted 16). The tests that survived:
- `test_empty_targets_does_not_redirect` — used `{"type": "knowledge"}` but still passes because `_should_redirect_to_rag()` checks `_KNOWLEDGE_CLASSIFICATIONS` which no longer has "knowledge", so returns False regardless
- `test_rag_disabled_never_redirects` — same, RAG disabled guard returns False before checking type
- `test_knowledge_request_redirects_to_experience` — input "I learned that early testing catches bugs" still redirects (via event/pattern fallback when RAG enabled), assertion `"experience()" in result` still holds

## Integration Tests vs Unit Tests

The integration tests (`test_inner_soul.py`, `test_inner_soul_standalone.py`) require a running LLM server and real OPENAI_API_KEY. They fail in CI/dev without these. Always verify with `git stash` that failures are pre-existing before investigating.
