# RAG Knowledge Toolset Testing Notes

## Feature Overview
- CompletionRegistry: Thread-safe async event-based completion tracking with buffered completions
- RAG Client: Full async HTTP client for LightRAG API with retry, error handling, and schema validation
- 15 RAG Tools: Factory pattern with manager injection, graceful RAG-disable, defensive attribute access
- Knowledge Tools: explore() (synchronous via invoke_agent_and_wait), experience() (fire-and-forget)
- inner_soul Redirect: Semantic classification routes knowledge → RAG instead of file-based memory
- Explorer/Experiencer Agents: Dedicated agents with rag tools (not knowledge tools)

## Testing Patterns Used
- CompletionRegistry tests use asyncio event loop directly for async wait_for testing
- RAG client tests mock httpx transport to avoid needing real LightRAG server
- Tool tests mock the RAG client singleton (_rag_client) and verify output formatting
- Knowledge tools mock invoke_agent_and_wait and manager.spawn_instance
- inner_soul redirect tests use environment variable patching for is_rag_enabled()

## Key Gotchas
- RAG tools use `getattr(obj, 'attr', default)` for defensive attribute access on API responses
- experience() spawns instance then enqueues message — if enqueue fails, must terminate orphan
- CompletionRegistry handles cross-thread notification via call_soon_threadsafe
- invoke_agent_and_wait uses semaphore (WORKER_POOL_SIZE - 1) to prevent deadlock
- inner_soul redirect only activates when ALL targets are RAG targets AND classification is knowledge-oriented
