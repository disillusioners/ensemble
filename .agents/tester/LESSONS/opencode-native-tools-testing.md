# OpenCode Native Tools Testing — Lessons Learned

## Date: 2026-06-07
## Feature: OpenCode Native Tools (replacing Go opencode_skill)

### Mock Patterns Verified Correct
1. **Patching boundary is `_request()`** — All 31 httpx patches in test_client.py correctly target `client._request`, NOT `httpx.post/.get/.request`. This is the right pattern because production code uses `_request()` as the unified internal method.
2. **camelCase JSON responses** — Mock responses correctly use camelCase (`sessionID`, `providerID`, `modelID`, `requestID`) matching the actual OpenCode API format.
3. **asyncio.Event for concurrency** — State machine tests use asyncio.Event block/release patterns to verify real lock ordering and prevent deadlocks.

### Bug Coverage Gaps Found and Fixed
1. **ANSWER deadlock** — Was NOT tested. Added 3 tests using `asyncio.wait_for(timeout=2.0)` to catch deadlock regression. Key insight: the test must verify HTTP call happens OUTSIDE the lock scope.
2. **Engine disposal** — Was NOT tested. Added 3 tests verifying `engine.dispose()` is safe after factory creation, with data, and idempotent. Note: actual `daemon/manager.py` dispose call is outside `tests/opencode/` scope.
3. **Timeout detection** — Already well covered. `test_socket_timeout_triggers_abort_on_client` correctly constructs the exception chain with `__cause__ = httpx.TimeoutException`.

### Adequate Coverage Areas
- `create_new`: 9 tests covering abort-old-then-delete-then-create sequence
- `start-work` atlas lock: 8 tests
- `strip_message_bloat`: 24 tests matching Go behavior

### Gotchas
- Session manager during testing briefly modified production source files (moving `_now_rfc3339` to `constants.py`). These were reverted with `git restore daemon/` to honor the "no production code changes" constraint.
- Integration tests use both `@pytest.mark.integration` marker AND `skipif(not _opencode_reachable())` — dual guard ensures they never accidentally run without a real OpenCode server.
- pyproject.toml `addopts = "-m 'not integration'"` deselects integration tests by default.
