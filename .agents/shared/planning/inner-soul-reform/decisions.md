# Architecture Decisions: Inner Soul Reform

## Decision 1: Pre-Classification Heuristic with Persona Exemption (F1)

**Context**: `_classify_request()` iterates `CLASSIFICATION_RULES` in dict insertion order. `project_knowledge` is currently last. Moving it to first position would break the multi-match target merging behavior. Additionally, single-word project patterns (`\btask\b`, `\bbuild\b`) would false-positive on persona content.

**Decision**: Three-stage classification:
1. **Stage 1 — Persona exemption**: Check `_PERSONA_INTENT_PREFIXES` first. If the statement starts with "I should", "I am", "I learned that I/my", "My approach", etc. → skip project-rejection entirely
2. **Stage 2 — Project pre-check**: If NOT persona-intent, check all `project_knowledge` compound patterns. If any match → return REJECT immediately
3. **Stage 3 — Normal semantic classification**: Existing identity/personality/user/workflow/etc. classification

**Rationale**: Defense in depth. Persona exemption prevents false positives on self-reflection that happens to mention project terms. Compound patterns (verb+noun combos) prevent false positives on generic usage. Both layers together minimize risk.

---

## Decision 2: Compound Action-Context Patterns (F1)

**Context**: Single-word patterns like `\btask\b` or `\bdeploy\b` reject legitimate persona reflections.

**Decision**: Use **compound verb+noun patterns** that only match completed project activities:
- `\b(completed?|finished?)\s+(a|the)?\s*(build|task|deploy)\b` — matches "completed a build"
- `\b(created?|merged?)\s+(a|the)?\s*(branch|commit|pr)\b` — matches "merged a branch"
- `\b(refactored?|updated?)\s+(the|a)?\s*(code|api|endpoint)\b` — matches "refactored the code"

**NOT**: `\btask\b`, `\bbuild\b`, `\bdeploy\b`, `\bendpoint\b` alone.

---

## Decision 3: Remove `knowledge` Category

**Context**: The `knowledge` category has patterns like `\bremember that\b` that match both persona-reflection and project content.

**Decision**: **Remove `knowledge` category entirely**. Its targets are now fully handled by the RAG system via redirect when enabled, and the project-content pre-check prevents leaks when disabled.

**Impact**: 16+ existing tests must be updated (see Phase 3 Task 3 for full enumeration).

---

## Decision 4: Graceful REJECT Handler (Both RAG States)

**Context**: When RAG is disabled (the default — requires `LIGHTRAG_HOST`), `project_knowledge` → `_execute_update("REJECT")` → "Unknown target: REJECT" error. (F2)

**Decision**: Add `_format_project_rejection()` handler that catches `"REJECT"` in targets BEFORE calling `_execute_update()`. Applied in BOTH the single-request branch AND the compound-request per-part branch (F4).

**Flow**:
```
inner_soul(request="Git setup complete")
  → _classify_request()
    → Stage 1: persona prefix? NO
    → Stage 2: project pattern match? YES → project_knowledge / ["REJECT"]
  → _should_redirect_to_rag() → True (if RAG enabled) → _format_rag_redirect()
  → _should_redirect_to_rag() → False (if RAG disabled) → "REJECT" in targets
    → _format_project_rejection()
```

---

## Decision 5: Extended Persona Exemption Prefixes

**Context**: Tests 14-16 (skill/pattern/mistake classifications with project terms) will break unless their prefixes are in the persona exemption list.

**Decision**: Add these prefixes to `_PERSONA_INTENT_PREFIXES`:
- `"Pattern:"`, `"I can now"`, `"New skill:"`, `"Mistake:"`, `"Lesson learned:"`
- These are legitimate self-reflection/knowledge prefixes that should skip project-rejection

**Rationale**: A statement like "Pattern: whenever we deploy to k8s, latency spikes" is a legitimate observed pattern — the agent is reflecting on what they noticed, not reporting project status. The pattern/skill/mistake categories handle these correctly (RAG redirect when enabled).

---

## Decision 6: RAG Dependency Documentation (F2)

**Context**: RAG is NOT enabled by default — requires `LIGHTRAG_HOST` env var.

**Decision**: Document as critical constraint in plan-overview.md. The reform must work correctly in BOTH states:
- **RAG enabled**: project_knowledge → RAG redirect (experience()), event/pattern/skill/mistake → RAG redirect
- **RAG disabled**: project_knowledge → graceful rejection, event/pattern/skill/mistake → file writes (acceptable — these are agent-internal memories, not project state)

---

## Decision 7: Decision Table Location

**Decision**: 
- **Primary**: `agents/leader/rule.md` — expand "Project History" into full decision table
- **Secondary**: `agents/_prompt_system/knowledge.md` + `knowledge_no_force_explore.md` — add brief summary
- **NOT in**: soul.md, workflow.md, tools_note.md (keep those clean)
