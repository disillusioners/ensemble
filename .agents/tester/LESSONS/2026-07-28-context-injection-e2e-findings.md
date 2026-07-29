# LESSON: E2E Context Injection — Test Architecture Findings

**Date:** 2026-07-28
**Ref:** Hybrid context injection verification

## Finding 1: Combined E2E Pack Exceeds 5-Min Cap (Structural)

**Root cause:** Two sequential real-LLM-turn scenarios (~270s + ~67s = ~337s) in one pack script with a 300s timeout wrapper.

**Impact:** The pack-level timeout kills Scenario 2 exactly 4 seconds after its turn 1 completes. Scenario 2 was verified individually (67s, PASS).

**Fix:** Split `e2e_context_injection_test.sh` into:
- `e2e_context_injection_project.sh` → Scenario 1 only (~270s, fits under cap)
- `e2e_context_injection_skills.sh` → Scenario 2 only (~67s, comfortable)

**Status:** Recommended but not yet applied — the combined pack is currently the only registered pack for this test. A worker can split it via a Test Architecture Fix.

## Finding 2: E2E Prompts Must Be Read-Only

**Root cause:** Scenario 2 turn 2 originally prompted "Run the unit tests now" — the tester agent obeyed literally, spawned an Explorer child, entered real test-planning workflow, blew the 5-min budget.

**Rule:** When writing E2E tests that only need LLM responses (not actual task execution), prompts must be read-only/informational. Avoid action-triggering verbs like "run", "execute", "fix", "create".

**Good prompts:** "What do you know about...", "Tell me more about...", "Which one is most relevant to..."
**Bad prompts:** "Run...", "Execute...", "Fix...", "Create..."

## Finding 3: Dynamic Project ID Lookup

**Root cause:** E2E tests hardcoded `PROJECT_ID = "83da04de-..."`, but the daemon's live DB had the project under a different UUID after a re-seed.

**Rule:** E2E tests should never hardcode project UUIDs. Use a dynamic lookup:
```python
def _resolve_project_id() -> str:
    """Look up project_id by name from the live daemon DB."""
    resp = requests.get(f"{API_BASE}/projects", timeout=10)
    ...
    # Find by name or shortname
```

## Finding 4: Daemon Log Confirmation of Hybrid Behavior

The daemon emits explicit log lines confirming the hybrid architecture:

**Persistent (project context):**
```
[Hybrid] Prepended 1 persistent context message(s) to graph_input for {iid} (project_injected=False)
```

**Ephemeral (skills):**
```
[ContextSlot] Injected 1 ephemeral context message(s) for {iid} before LLM call
```

These are useful for debugging and can be grepped from daemon logs to verify context injection without needing to inspect message lists.
