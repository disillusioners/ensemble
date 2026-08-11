# Phase 4: Testing & Integration

## Objective

Build a comprehensive test suite for the Discord adapter covering initialization, message normalization, mention-gating, external user ID construction, outbound routing, LLM tag stripping, rate limiting, circuit breaker, thread management, health checks, and registry integration. All tests must pass against both the existing test runner and without a live Discord connection (mocked discord.py).

## Files to Create

| # | File | Purpose | Est. Tests |
|---|------|---------|------------|
| 1 | `tests/test_discord_adapter.py` | Core adapter tests (init, normalization, mention-gating, send, circuit breaker, channel locks, health, shutdown, integration) | ~50-60 test methods |
| 2 | `tests/test_discord_thread_manager.py` | Thread manager tests (register, TTL/LRU eviction, archive tracking, shutdown lifecycle, concurrent guild access) | ~20-25 test methods |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Create `tests/test_discord_adapter.py` test infrastructure: `make_discord_config()` helper (mirror `make_telegram_config()` in `test_telegram_adapter.py:18-30`); fixtures: `mock_on_message`, `discord_config`, `mock_discord_client` (MagicMock for discord.py Client), `mock_source_repo`. | none | All fixtures construct without live Discord; config helper produces valid `SourceConfig` with source_type="discord" |
| 2 | Write initialization tests (`TestDiscordAdapterInit`): missing bot_token raises ValueError; valid config extracts default_agent, channel_require_mention, allowed_guilds; intents config parsed correctly using dict schema (`{"intents": {"guilds": true, "guild_messages": true, "message_content": true, "dm_messages": true}}`) and MESSAGE_CONTENT handling is fail-closed when not enabled; circuit breaker initialized; per-channel locks initialized; thread manager initialized when manager provided. Pattern: `TestTelegramAdapterInit` in `test_telegram_adapter.py:45-80`. | 1 | 6-8 tests covering all init paths; matches Telegram test structure; missing MESSAGE_CONTENT intent does not expose message content |
| 3 | Write inbound normalization tests (`TestInboundNormalization`): DM message → `external_user_id="dm:{user_id}"`; guild channel → `external_user_id="{guild_id}:{channel_id}"`; thread → `external_user_id="{guild_id}:{parent_channel_id}:{thread_id}"`; empty content (attachment) → `[Attachment]`; `/new` command → `message_type="command"`, `metadata["force_new_instance"]=True`; metadata contains all nested `metadata["discord"]` keys and `agent`. | 1 | 8-10 tests covering all normalization paths; external_user_id format matches the canonical DM/channel/thread scheme |
| 4 | Write canonical `DISCORD_ID_PATTERN` tests for `^(dm:\d{17,19}|\d{17,19}:\d{17,19}(:\d{17,19})?)$`: valid DM `dm:123456789012345678` → match; valid channel `123456789012345678:987654321098765432` → match; valid thread `123456789012345678:987654321098765432:111122223333444455` → match; invalid too-short `dm:123` → no match; invalid wrong format `discord:channel:123` → no match. | 1 | Mapper accepts exactly the canonical external user ID scheme |
| 5 | Write `_split_message()` tests (covers all 5 tiers of the priority-ordered fallback chain): (a) **under limit** — content under 2000 chars returns a single-element list; (b) **over limit** — content over 2000 chars returns multiple chunks with each chunk ≤ 2000; (c) **paragraph boundary** — content with paragraph breaks (`\n\n`) near 2000 splits at `\n\n`; (d) **sentence boundary** — content with a sentence boundary (`. `) near the 2000-char mark (no paragraph/line break in range) splits at the sentence boundary, NOT mid-word; (e) **word boundary** — content with no paragraph/line/sentence boundaries near the limit but containing a space splits at the nearest space; (f) **hard cut** — content with NO space within the last 2000 chars (e.g., a 4000-char run of non-space characters) hard-cuts at exactly 2000; (g) **exact limit** — exactly 2000 chars returns one message. | 1 | All 5 split tiers, boundary cases, and hard-cut fallback pass; the priority chain is verified end-to-end (paragraph > line > sentence > word > hard cut) |
| 6 | Write mention-gating tests (`TestMentionGating`): DM → always activated; guild @mention → activated; guild no mention + `channel_require_mention=True` → skipped; guild no mention + `channel_require_mention=False` → activated; bot self-message → skipped; bot_user_id not resolved → fail open (activated). Pattern: mention logic in `slack/adapter.py:666-700`. | 1 | 6-8 tests; mention detection matches Discord `<@id>` and `<@!id>` formats |
| 7 | Write outbound send tests (`TestSendRouting`): valid mapping → message sent to correct channel; missing mapping → return False + log; circuit breaker open → return False; DM routing via `dm:` prefix; thread routing via canonical thread ID in metadata; DB lookup failure → return False + log; LLM tags stripped before send; nested `mapping_metadata={"discord": {"channel_id": "...", "thread_id": "..."}}`. Pattern: Telegram send tests structure. | 1,3 | 8-10 tests; send routing matches canonical external_user_id parsing |
| 8 | Write integration tests (`TestRegistryIntegration`): registry creates `DiscordAdapter` when `source_type="discord"`; adapter receives `manager` kwarg; adapter has `_source_repo` injected; `test_connection()` classmethod returns `(True, ...)` with valid config / `(False, ...)` with invalid; mapper validates canonical Discord external_user_id format (valid snowflake composites pass, malformed fail); router `test_connection` endpoint calls `DiscordAdapter.test_connection()` not the stub. | 1 | 6-8 tests; full registry → adapter → mapper → router chain validated |
| 9 | Write resilience, concurrency, and shutdown tests mapping to the Phase 3 contracts: (a) **Circuit breaker 429-exclusion** — `TestCircuitBreaker.test_429_response_does_not_increment_failure_count`: mock discord.py to raise `discord.HTTPException` with `status=429` after backoff, assert `record_failure()` is NOT called and circuit stays closed. (b) **Circuit breaker 5xx counts as failure** — `test_5xx_response_increments_failure_count`: `status=500` raises and `record_failure()` IS called. (c) **ThreadManager shutdown** — `TestThreadManagerShutdown.test_shutdown_terminates_all_instances`: register N threads, call `shutdown()`, verify `manager.terminate_instance` was called for each `instance_id`. (d) **shutdown with one failing termination** — `test_shutdown_partial_failure_continues`: one terminate raises, others succeed, error logged, no propagation. (e) **shutdown idempotent** — `test_shutdown_idempotent`: call twice, second call no-ops. (f) **Concurrent guild dict access under lock** — `test_concurrent_guild_dict_access_serializes`: spin N coroutines that `register_thread` on the same guild, assert all complete and `len(thread_map) == N`. (g) **Channel lock held during eviction** — `TestChannelLockEviction.test_held_lock_not_evicted`: fill to `MAX_CHANNEL_LOCKS=1000`, hold the oldest lock, request a new lock for a new channel, assert the held lock is still present and a different unlocked entry was evicted. (h) **Eviction skips locked entries** — `test_eviction_skips_locked_entries`: pre-populate 5 entries, lock 3 of them, trigger eviction, verify only the 2 unlocked entries were candidates. (i) **All locks held at capacity → no eviction** — `test_all_locks_held_no_eviction`: at 1000 entries with all locked, new key request creates entry beyond cap (soft limit). (j) **Gateway health latency** — `TestHealthCheck.test_latency_below_5000ms_returns_healthy`: `client.latency = 0.05` → True; `test_latency_above_5000ms_returns_unhealthy`: `client.latency = 6.0` → False; `test_missing_latency_returns_false`: `client.latency = None` → False (no crash). (k) **Adapter stop() idempotent** — `TestStopShutdown.test_stop_called_twice_no_error`: call `stop()` twice, second call returns immediately, status is STOPPED once. (l) **TTL eviction task cancelled on stop** — `test_ttl_task_cancelled_on_stop`: start adapter with TTL task, call `stop()`, assert `_ttl_task` is None and task is done. (m) **ThreadManager.shutdown() failure does not block adapter stop()** — `test_thread_manager_shutdown_failure_does_not_block_stop`: thread_manager.shutdown() raises, adapter.stop() still completes and status == STOPPED. | 1-8 | 12-15 tests covering 429-exclusion, circuit-breaker failure classification, ThreadManager lifecycle (shutdown/partial-failure/idempotency/concurrent-access), channel-lock eviction safety, Gateway latency health check, adapter stop() idempotency, and TTL/task cancellation | 1-8 | 12-15 tests covering 429-exclusion, circuit-breaker failure classification, ThreadManager lifecycle (shutdown/partial-failure/idempotency/concurrent-access), channel-lock eviction safety, Gateway latency health check, adapter stop() idempotency, and TTL/task cancellation |
| 10 | Write NFR-10 token-redaction tests (`TestTokenRedaction`, in `test_discord_adapter.py`): (a) **Bot token not logged in plaintext on error** — `test_bot_token_not_logged_in_plaintext`: configure adapter with a known bot token (e.g., `SECRET_TOKEN_DO_NOT_LEAK_abc123.xyz456`); use `caplog` (pytest's `caplog` fixture) at `logging.DEBUG` level to capture all log output; trigger an error path that would normally include the token in a message string (e.g., mock discord.py `channel.send` to raise `discord.HTTPException` with a message that the adapter might naively include via `f"... {self._bot_token} ..."` or via `repr(self._config)`); assert the bot token string does NOT appear as a substring in ANY captured log record (across all levels), including `record.getMessage()`, the formatted `exc_info`, and `record.args`. (b) **Token not in repr/str of config** — `test_bot_token_not_in_config_repr`: assert `repr(self._config)` and `str(self._config)` do NOT contain the bot token (defense-in-depth: even if logging accidentally serializes the config, the token is masked). (c) **Token not in exception chain** — `test_bot_token_not_in_exception_chain`: trigger a connection error whose `args` or `__cause__` chain could leak config; assert the bot token is not present in any `str(exc)` or `repr(exc)` produced by the adapter or surfaced in logs. (d) **Token not in traceback sent to logs** — `test_bot_token_not_in_traceback`: with `caplog` and `log.exception(...)` invoked, assert the formatted traceback text (`caplog.text`) does not contain the bot token. Pattern: pytest `caplog`/log capture fixtures; reference NFR-10 in requirements.md:209. | 1, 2 | 4 tests covering all common leak vectors (direct log messages, config repr, exception chain, traceback); `caplog.text` is asserted to be free of the secret token at all log levels; satisfies NFR-10 automated grep + error-path verification |

## Test Coverage Matrix

| Component | Test File | Key Test Cases |
|-----------|-----------|----------------|
| `__init__` / config validation | `test_discord_adapter.py::TestDiscordAdapterInit` | Missing token, valid config, intent config, allowed_guilds |
| `_build_external_user_id` | `test_discord_adapter.py::TestInboundNormalization` | DM, guild channel, thread, missing fields |
| `_is_bot_mentioned` | `test_discord_adapter.py::TestMentionGating` | DM, mention, no-mention, self-message, bot_id unresolved |
| `_normalize_incoming` | `test_discord_adapter.py::TestInboundNormalization` | Text, attachment, command, empty, mention cleanup |
| `send()` routing | `test_discord_adapter.py::TestSendRouting` | Valid mapping, missing mapping, circuit open, DM, thread |
| `_strip_llm_artifact_tags` | `test_discord_adapter.py::TestFormatting` | Think block, self-closing, orphan, all tags, nested |
| `_clean_discord_text` | `test_discord_adapter.py::TestFormatting` | User mention, nickname mention, channel mention, role mention, broadcast |
| `_split_message` (5-tier chain) | `test_discord_adapter.py::TestSplitMessage` | Under limit (single chunk), over limit (multi-chunk), paragraph boundary (`\n\n`), sentence boundary (`. `), word boundary (space), hard cut (no space → 2000), exact limit (2000) |
| Bot token redaction (NFR-10) | `test_discord_adapter.py::TestTokenRedaction` | Token not in plaintext log output (`test_bot_token_not_logged_in_plaintext` via `caplog`), token not in `repr/str` of config, token not in exception chain, token not in traceback text |
| `health_check` (Gateway latency) | `test_discord_adapter.py::TestHealthCheck` | RUNNING+ready=True, STOPPED=False, ERROR=False, disconnected=False, latency < 5000ms healthy, latency > 5000ms unhealthy, missing latency (None/NaN) graceful False |
| `test_connection` | `test_discord_adapter.py::TestConnection` | Valid token, invalid token, network error, timeout |
| `DiscordRateLimiter` | `test_discord_adapter.py::TestRateLimiter` | Concurrency limit, release on exception, semaphore metrics (`_rate_limit_waits`, NOT `_rejected_count`) |
| Circuit breaker — failure classification | `test_discord_adapter.py::TestCircuitBreaker` | Open after 5 failures, blocks send, recovers after 60s, **429 does NOT increment failure count** (rate-limit signal), 5xx/timeout/ConnectionError DO increment |
| Channel-lock eviction safety | `test_discord_adapter.py::TestChannelLockEviction` | Held lock NOT evicted, eviction skips locked entries, all-locks-held at capacity → soft cap exceeded, `_get_channel_lock` atomicity under `_channel_locks_guard`, `MAX_CHANNEL_LOCKS=1000` |
| `stop()` lifecycle | `test_discord_adapter.py::TestStopShutdown` | Idempotent (call twice, no error/double-cleanup), TTL task cancelled and awaited, `ThreadManager.shutdown()` failure does not block adapter stop(), status → STOPPED exactly once, channel locks cleared |
| `DiscordThreadManager` core | `test_discord_thread_manager.py` | Register, get, TTL eviction, LRU eviction, archive tracking |
| `DiscordThreadManager.shutdown()` | `test_discord_thread_manager.py::TestThreadManagerShutdown` | Terminates all instances, partial-failure continues (logged, not propagated), idempotent |
| Concurrent guild access | `test_discord_thread_manager.py::TestConcurrentGuildAccess` | Per-guild `asyncio.Lock` serializes concurrent register_thread on the same guild |
| Registry/mapper/router | `test_discord_adapter.py::TestRegistryIntegration` | Dispatch, manager kwarg, source_repo injection, ID validation |

## Mocking Strategy

All tests must run without a live Discord connection. Mock `discord.py` objects:

```python
# Mock discord.py Client
mock_client = AsyncMock(spec=discord.Client)
mock_client.is_ready.return_value = True
mock_client.user.id = 123456789012345678
mock_client.latency = 0.05  # 50ms

# Mock discord Message
mock_message = MagicMock()
mock_message.content = "hello world"
mock_message.author.id = 987654321
mock_message.author.bot = False
mock_message.channel.id = 555444333222
mock_message.channel.type = discord.ChannelType.text
mock_message.guild.id = 111222333
mock_message.mentions = []

# Mock SourceRepository
mock_source_repo = MagicMock()
mock_source_repo.get_instance_mapping.return_value = MagicMock(
    mapping_metadata={"discord": {"channel_id": "555444333222111333"}}
)
```

Use `@patch("daemon.sources.adapters.discord.adapter.discord.Client")` to inject mock client.

## Coupling

- **Tight with:** Phase 1 — tests validate init, lifecycle, registry integration
- **Tight with:** Phase 2 — tests validate normalization, mention-gating, send routing
- **Tight with:** Phase 3 — tests validate rate limiter, circuit breaker, thread manager
- **Depends on:** ALL prior phases being complete (tests cannot be finalized until implementation is done)

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| discord.py mock surface too large / internal API changes | Medium | Mock at the adapter boundary (Client, Message, Channel), not internal discord.py classes. Use `spec=` to catch attribute errors. |
| Thread manager tests require time manipulation | Low | Use `freezegun` or mock `time.monotonic()` / `time.time()` for TTL tests. Check if project already uses time mocking. |
| Integration test needs full registry stack | Medium | Mock the manager, source_repo, and instance_repo at the registry level; don't spin up real instances. |
| Discord snowflake ID format in mapper validation | Low | Discord snowflakes are 17-19 digit integers; validate with regex `^\d{17,19}$` or the composite format pattern. |
| NFR-10 token redaction — accidental leak in third-party library | Medium | Cover all common leak vectors: log messages (`caplog` capture), `repr/str` of config, exception chain (`str(exc)`/`repr(exc)`/`__cause__`), and formatted traceback text. If a third-party library (discord.py, log formatter) leaks the token, the test fails and the leak must be redacted at the call site (do not mute the test). |

## Exit Criterion

- `pytest tests/test_discord_adapter.py` — all tests pass (0 failures)
- `pytest tests/test_discord_thread_manager.py` — all tests pass (0 failures)
- Test coverage covers all adapter methods, all normalization paths, all send routing paths
- Registry integration test confirms adapter is created and wired correctly
- Mapper validation test confirms Discord external_user_id format is accepted/rejected correctly
- Router test_connection test confirms the "not implemented" stub is replaced
- **Circuit-breaker failure classification** — 429s do NOT increment failure count; transport/5xx/timeout errors DO
- **Channel-lock eviction safety** — held locks are never evicted; `MAX_CHANNEL_LOCKS=1000`
- **ThreadManager shutdown** — terminates all instances; partial-failure continues; idempotent; concurrent guild access is lock-serialized
- **Gateway health latency** — `health_check()` returns False on latency ≥ 5000ms or missing latency
- **Adapter `stop()`** — idempotent; cancels TTL task; thread-manager shutdown failure does not block adapter stop
- **`_split_message()` 5-tier chain** — paragraph > line > sentence > word > hard cut; all 5 tiers exercised; hard cut yields exactly `max_length` chunks when no space exists
- **NFR-10 token redaction** — bot token is NEVER present in plaintext in any captured log record, config repr/str, exception chain, or traceback text
- Full test suite (`pytest tests/`) shows no regressions from the new tests
