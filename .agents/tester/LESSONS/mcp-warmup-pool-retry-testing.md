# MCP Warmup Pool Retry Testing Lessons

## Date: 2026-05-20
## Branch: fix/mcp-warmup-pool-logging-logic

### Retry Logic Testing Pattern
- The `_create_pooled_connection` method has retry with exponential backoff
- Key mock pattern: mock `asyncio.sleep` but chain to original to avoid recursion
- Recursion issue: tests that mock `asyncio.sleep` need to properly track calls without creating infinite loops
- Fix: Use a wrapper that tracks calls then delegates to original `asyncio.sleep`

### Testing Async Retry Logic
- Mock `session.initialize` to fail N times then succeed
- Track `asyncio.sleep` calls to verify backoff timing (2s, 4s)
- `CancelledError` must propagate immediately — no retry
- Per-attempt timeout can be tested by mocking `asyncio.wait_for` to raise `asyncio.TimeoutError`

### Log Level Verification in Retry
- Retry attempts should log WARNING
- Final failure should log ERROR
- Success should log INFO
- Use `caplog` fixture with `logging.getLogger()` to capture and assert log levels

### Minor: Log Format Inconsistency
- Success/partial: `({count}/{size} connections)`
- Failure: `(0/{size} connections created)`
- Cosmetic issue, "connections created" appears only in failure message
