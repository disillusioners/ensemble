# Explorer Context-First Testing — 2026-05-31

## Finding: Minor naming inconsistency between rule.md and Python code
- **rule.md** Rule 1 references `ENSEMBLE_SHARED_CONTEXT_DIR` as the variable name
- **knowledge_tools.py** sends `Shared context dir: {context_dir}` (no `ENSEMBLE_` prefix)
- **Impact**: Non-functional — the agent receives the actual path string, so it works correctly
- **Recommendation**: Could align naming for clarity, but not required

## Edge case analysis confirmed all paths are safe
- None context_key → guard skips hint line
- Empty context dir → agent falls through to RAG
- Missing context dir → same as empty, graceful fallback
