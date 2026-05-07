# KB Disable Feature Testing Lessons

## Feature: Conditional KB/RAG Tool Disabling (feature/kb-disable-when-no-lightrag)

### Key Findings
1. **Whitespace-only LIGHTRAG_HOST** is treated as ENABLED — `bool("   ")` returns `True`. Unlikely in real deployments but worth noting. Fix would be adding `.strip()` in `RAGConfig.from_env()`.
2. **H1 stripping pattern** — `compose_system_prompt()` uses `re.sub(r'^#\s+.*\n*', '', content, count=1)` to strip leading H1 from `knowledge.md` to prevent double-heading. Works correctly.
3. **Cache invalidation on toggle** — Cache correctly invalidates when `is_rag_enabled()` returns different values across calls. The cache key doesn't include RAG state; instead, the `load_shared_knowledge()` function conditionally returns empty or content based on RAG state, which naturally causes cache differences.
4. **Template agents** (`_baby_template`, `_mother`) still reference explore/experience in their knowledge.md. This is intentional — they are parent/child templates that may spawn agents needing KB tools.

### Test Coverage
- 110 original feature tests + ~15 gap tests added = comprehensive coverage
- All scenarios tested: tool availability, prompt assembly, cache behavior, per-agent files, edge cases, backward compat
