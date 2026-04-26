# Review: RAG Knowledge Toolset Plan

## Date: 2026-04-26
## Verdict: ⚠️ One Fix Needed (then ✅ Ready to Implement)

### C1 Fix (exit points): ✅ SOLID
### C2 Fix (error recovery): ✅ SOLID  
### C3 Fix (classification redirect): ⚠️ One remaining issue — target merge produces "REJECT"

## Remaining Issue

### Multi-match target merging in _classify_request()
- _classify_request() MERGES targets across all matching types
- "I learned that the project uses postgresql" → knowledge + project_knowledge → targets=["memory","memories","REJECT"]
- _should_redirect_to_rag() checks all(t in _RAG_TARGETS) → fails on "REJECT"
- Falls through to _execute_update("REJECT") → unknown target error

### Fix: Filter "REJECT" from targets in _should_redirect_to_rag()
```python
actual_targets = [t for t in targets if t != "REJECT"]
# Then check all(t in _RAG_TARGETS for t in actual_targets)
```

### Doc error: Plan says "15 types" but CLASSIFICATION_RULES has 11 types
