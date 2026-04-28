# Explorer/Experiencer RAG Upgrade Testing

## Key Learnings
- The `explore()` tool now has a fire-and-forget pattern for enqueueing experiencer jobs — tests need `asyncio.sleep(0.1)` to let async tasks complete before assertions
- Use a separate `mock_manager_with_job_queue` fixture rather than modifying the shared `mock_manager` — keeps existing tests isolated
- The `_parse_should_update_kb` uses case-insensitive matching and defaults to False
- `_generate_idempotency_key` is deterministic based on query + project_id inputs

## Gotchas
- When testing "no job enqueued" scenarios, the shared `mock_manager` fixture may already have `_job_queue_service` attribute — use the dedicated fixture and assert `enqueue.assert_not_called()` instead of checking attribute existence
