# Phase 3 RAG Knowledge Toolset — Implementation Notes

## What Was Built

Phase 3 of the RAG Knowledge Toolset plan — the Tools layer connecting agents to LightRAG.

### Files Created
- `daemon/tools/rag_tools.py` — 15 RAG tools wrapping AsyncLightRAGClient (rag category)
- `daemon/tools/knowledge_tools.py` — explore() + experience() tools (knowledge category)
- `tests/unit/tools/test_rag_tools.py` — 24 RAG tool tests
- `tests/unit/tools/test_knowledge_tools.py` — 13 knowledge tool tests

### Files Modified
- `daemon/tools/_tool_registry.py` — Added "rag" and "knowledge" to CATEGORY_MODULES
- `daemon/tools/instance.py` — Wired create_rag_tools() and create_knowledge_tools() into factory

### Key Patterns
- **RAG tools**: Factory function `create_rag_tools(manager, instance_id)` with closure pattern, singleton `_rag_client`, `is_rag_enabled()` guard, `RAGError` → string error
- **Knowledge tools**: `explore()` uses `invoke_agent_and_wait()` (blocking 300s timeout), `experience()` uses `spawn_instance` + `enqueue_message` (fire-and-forget)
- **Tool filtering**: Both added BEFORE `_apply_tool_filter()` — agent meta.json allow/deny lists control actual availability

### Review Findings
- All passed — one non-blocking warning about `updated_name` param in `rag_update_entity`
- All 724 tests pass
- Commit: `5e88553` on `feature/rag-knowledge-toolset`

### Architecture Notes
- RAG tools (rag category) → only explorer/experiencer agents (via allow list)
- Knowledge tools (knowledge category) → all agents
- This creates "Agent-as-Tool" pattern where regular agents call explore/experience which spawn specialized agents
