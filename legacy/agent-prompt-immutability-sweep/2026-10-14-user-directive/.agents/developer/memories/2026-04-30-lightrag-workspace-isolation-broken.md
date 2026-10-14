# LightRAG Workspace Header Isolation Test — 2026-04-30

## Critical Finding: WORKSPACE ISOLATION IS COMPLETELY BROKEN

The `LIGHTRAG-WORKSPACE` HTTP header does NOT provide data isolation. All endpoints return identical data regardless of the header value.

## Test Method
- Server: `https://kb.mtri.app` (LightRAG server)
- Inserted 3 documents into 3 different workspaces via `POST /documents/text` with `LIGHTRAG-WORKSPACE: ws_test_test1|ws_test_test2|ws_test_default`
- Queried with `POST /query` using `only_need_context: true` (raw retrieval, no LLM interpretation)
- Listed documents via `POST /documents/paginated`

## Results

### Document Listing (`/documents/paginated`) — BROKEN
- ALL 4 queries (3 workspaces + no header) return IDENTICAL 16 documents
- Same document IDs, same summaries, same timestamps
- The 3 test docs (Alpha/PostgreSQL, Beta/MongoDB, Gamma/Redis) appear in ALL workspaces

### Query (`/query` with `only_need_context: true`) — BROKEN
- ALL 4 queries return IDENTICAL context
- Context contains ALL entities: Alpha Project (PostgreSQL), Beta Project (MongoDB), Gamma Project (Redis)
- Zero isolation — querying ws_test_test1 returns MongoDB and Redis entities too

### No Header — Same as with header
- No workspace header returns the same 16 documents / same context
- Default workspace is NOT isolated either

## Root Cause
The LightRAG server COMPLETELY IGNORES the `LIGHTRAG-WORKSPACE` header. This is not a client-side issue — the server treats all data as a single global namespace regardless of the header.

## Impact on agents-ensemble
The KB module (`daemon/rag/`) sends `LIGHTRAG-WORKSPACE` headers expecting isolation per workspace/project. Since isolation doesn't work:
- All projects share the same knowledge base
- Knowledge from one project can contaminate queries for another
- The workspace parameter in the client config is effectively useless

## Fix Strategy Implications
Since the header doesn't provide isolation, we need a different approach:
1. **Option A**: Run separate LightRAG server instances per workspace (heavy)
2. **Option B**: Fix the LightRAG server to actually respect the header (upstream fix)
3. **Option C**: Use separate LightRAG namespaces/databases (if supported)
4. **Option D**: Accept single-namespace and manage isolation at application level

## Test Scripts
- `/tmp/test_rag_workspace_isolation.py` — v1 (LLM-interpreted queries)
- `/tmp/test_rag_isolation_v2.py` — v2 (raw context, definitive)
