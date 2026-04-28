# RAG Workspace Scoping — project_id to LightRAG

## What Was Built
Per-request workspace header override so each project's RAG data is isolated. Project IDs flow from instance metadata → RAG tools → RAG client → `LIGHTRAG-WORKSPACE` HTTP header.

## Architecture (4 Layers)
1. **daemon/rag/client.py** — `_sanitize_workspace()` + `workspace` param on `_request()` and all 17 public methods
2. **daemon/tools/rag_tools.py** — `_get_project_workspace()` helper extracts from instance metadata, all 15 tools pass it
3. **daemon/tools/knowledge_tools.py** — Already passes project_id to spawned agents (no changes needed)
4. **Sanitization** — `re.sub(r'[^a-zA-Z0-9_]', '_', workspace)` matches LightRAG's format (hyphens → underscores)

## Key Implementation Details
- Header merge pattern: `{**self._build_headers(), **kwargs.pop("headers", {})}` to preserve auth headers
- `_get_project_workspace()` uses closure over `manager` and `current_instance_id` from tool factory
- All workspace params default to `None` for backward compatibility
- When None, no `LIGHTRAG-WORKSPACE` header is sent (uses server default)

## Bugs Found During Review
- `update_relation` was initially missed — review caught it
- Initial header merge was broken (overwrote default headers) — test session caught and fixed

## Files Changed
- `daemon/rag/client.py` — +99 lines
- `daemon/tools/rag_tools.py` — +43 lines
- `tests/unit/rag/test_workspace_scoping.py` — 22 new tests
- `tests/unit/tools/test_rag_tools.py` — updated fixtures

## Commit
`7152f42` on `feature/rag-knowledge-toolset`
