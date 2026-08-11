# Plan Overview: Discord Source Adapter

Date: 2026-08-11
Author: planner[v2] via plan-creation worker
Status: Ready for Review

## Objective

Deliver a fully functional Discord source adapter for the ensemble daemon — enabling Discord users to chat with ensemble agents via DMs, server text channels, and threads — using `discord.py` for Gateway/WebSocket and REST connectivity, following the established Telegram and Slack adapter patterns.

## Scope

### In Scope

- New adapter package `daemon/sources/adapters/discord/` (multi-file, modeled on `slack/`)
- `DiscordAdapter(MessageSourceAdapter)` implementing the full ABC contract: `start()`, `stop()`, `send()`, `health_check()`, `test_connection()`
- discord.py client lifecycle: Gateway connection, heartbeat, reconnect/resume, intent configuration
- Inbound message normalization: Discord `on_message` events → `IncomingMessage` with external user ID scheme
- Mention-based activation gating in guild channels (configurable, like Slack's `channel_require_mention`)
- Outbound message routing: `OutgoingMessage` → Discord channel/thread/DM via DB-backed mapping lookup (Slack pattern)
- Discord-aware rate limiting: thin adapter semaphore + rely on discord.py's built-in dynamic route buckets and global limiter
- Circuit breaker integration for REST failures
- Per-channel ordering locks (OrderedDict + LRU, Telegram/Slack pattern)
- Thread management: `DiscordThreadManager` with TTL + LRU eviction, archive-state tracking
- LLM artifact tag stripping (reuse Telegram's `_strip_llm_artifact_tags` pattern)
- Source type registration: registry dispatch branch, mapper validation, router `test_connection` wiring
- `discord.py` dependency addition to `pyproject.toml`
- Comprehensive unit + integration test suite

### Out of Scope

- **Sharding / multi-shard Gateway** — single bot client for initial release; sharding deferred until guild scale requires it (open question #1 in technical-analysis.md)
- **Slash commands** — `/new` and other slash commands not in first release; text-based mention activation only
- **Voice / stage channels** — text channels, threads, and DMs only
- **Rich embeds / Block Kit equivalent** — Discord markdown is native; embeds deferred (formatting.py stub only)
- **Image upload to Discord** — receiving image metadata in `IncomingMessage.images` is in scope, but sending images outbound is deferred
- **Outbound idempotency/deduplication key** — documented as a known limitation; not solved in this release (technical-analysis.md open question #3)
- **WhatsApp adapter** — separate feature, not part of this plan
- **FR-18 (reload without restart)** — Adapter reload via stop/start cycle only. The `reload(new_config)` ABC optional override is **deferred to a future release**. Hot-reload of configuration without a full adapter restart is out of scope for v1; operators must call `stop()` + `start()` to pick up new credentials or config changes. The ABC accepts the override but this adapter does not implement it. See `requirements.md:69` (FR-18 spec).
- **FR-19 (per-channel mention configuration)** — Per-channel `require_mention` overrides (`always_active` / `require_mention` / `disabled` per channel) are deferred. The adapter uses a single global `channel_require_mention` config (default `True`) for all guild channels. The `SourceConfig.config["channel_mention_config"]` dict (`requirements.md:562`) is read by the adapter but ignored in v1 — every channel inherits the global setting. Per-channel overrides are a v2 enhancement. See `requirements.md:105-108` (FR-19 spec).

## Architecture Summary

```
Source Registry (daemon/sources/registry.py)
      |
      | source_type == "discord"
      v
DiscordAdapter (MessageSourceAdapter)
   |        |             |              |
   |        |             |              |
Gateway   Discord REST   Correlation    Thread Manager
Client    (discord.py)   / Ordering      (DiscordThreadManager)
(discord.py)              Locks
   |        |             |              |
Discord events   Discord API    DB mapping lookup    TTL/LRU cache
      |
Normalize → IncomingMessage → on_message callback → ensemble pipeline

Outbound: OutgoingMessage → DB mapping → channel/thread ID → REST send → Discord
```

**Design decision (from technical-analysis.md §Trade-offs):** Option A — discord.py-backed adapter. discord.py handles Gateway opcodes, heartbeat, sequence tracking, reconnect/resume, and dynamic REST route buckets. The adapter owns ensemble-specific normalization, routing, resilience, and lifecycle.

### Key Architecture Decisions

| # | Decision | Rationale | Ref |
|---|----------|-----------|-----|
| A1 | Use `discord.py` library (not custom WebSocket) | Delegates protocol-critical Gateway/heartbeat/resume and dynamic rate-limit bucket logic to maintained SDK | technical-analysis.md:111-131 |
| A2 | Multi-file package (not single file like Telegram) | Discord has distinct concerns: Gateway lifecycle, thread archive management, formatting. Slack precedent validates package approach | technical-analysis.md:217-228 |
| A3 | Thin `rate_limiter.py` (not Slack-tier-style table) | Discord rate limits are dynamic (header-based buckets), unlike Slack's static tiers. SDK handles buckets; adapter adds only a concurrency semaphore + metrics | technical-analysis.md:189-191 |
| A4 | External user ID: `{guild_or_dm_scope}:{entity_id}` typed scheme | Stable, unambiguous routing keys mirroring Slack's `{workspace}:{entity}` pattern; retains Discord scope | technical-analysis.md:206-215 |
| A5 | DB-backed outbound routing (Slack pattern) | `send()` resolves channel/thread from `source_repo.get_instance_mapping()` mapping metadata; consistent with Slack | adapter.py:400-449 |
| A6 | Reuse `_strip_llm_artifact_tags` logic | Same LLM artifact tags (`<think>`, `<reasoning>`, etc.) affect Discord; Telegram's regex pattern is directly reusable | telegram.py:37-71 |

### External User ID Scheme

| Context | Format | Example | Notes |
|---------|--------|---------|-------|
| DM | `dm:{user_id}` | `dm:123456789012345678` | One instance per user across DMs |
| Guild channel | `{guild_id}:{channel_id}` | `987654321:555444333222` | One instance per channel (shared conversation) |
| Thread | `{guild_id}:{parent_channel_id}:{thread_id}` | `987654321:555444333:777888999` | Separate instance per thread |

Guild ID and user ID are retained in mapping metadata (`discord.guild_id`, `discord.user_id`, `discord.channel_id`, `discord.channel_type`, `discord.thread_id`) even when the external key is a channel target — consistent with how Slack stores `slack_channel_id` / `slack_thread_ts` in metadata.

## File Structure

```
daemon/sources/adapters/discord/
├── __init__.py            # Exports DiscordAdapter
├── adapter.py             # DiscordAdapter — lifecycle, on_message handler, normalization, send(), health_check(), test_connection()
├── rate_limiter.py        # DiscordRateLimiter — thin concurrency semaphore + metrics (NOT bucket logic; SDK owns that)
├── thread_manager.py      # DiscordThreadManager — thread target cache with TTL + LRU eviction + archive-state tracking
└── formatting.py          # _strip_llm_artifact_tags, _clean_discord_text — mention stripping, markdown normalization
```

### Files to Create (7)

| # | File | Purpose | Est. Lines |
|---|------|---------|------------|
| 1 | `daemon/sources/adapters/discord/__init__.py` | Package export | ~5 |
| 2 | `daemon/sources/adapters/discord/adapter.py` | Core adapter: lifecycle, normalization, send, health | ~500-700 |
| 3 | `daemon/sources/adapters/discord/rate_limiter.py` | Concurrency semaphore + metrics seam | ~80-120 |
| 4 | `daemon/sources/adapters/discord/thread_manager.py` | Thread cache: TTL, LRU, archive tracking | ~250-350 |
| 5 | `daemon/sources/adapters/discord/formatting.py` | LLM tag stripping, mention cleanup | ~80-120 |
| 6 | `tests/test_discord_adapter.py` | Unit tests: init, normalization, mention-gating, send routing, health | ~600-800 |
| 7 | `tests/test_discord_thread_manager.py` | Unit tests: TTL, LRU eviction, archive handling | ~200-300 |

### Files to Modify (4)

| # | File | Change | Lines Changed |
|---|------|--------|---------------|
| 1 | `daemon/sources/registry.py` (~line 423) | Add `elif source_type == "discord"` branch; pass `manager=self._manager`, inject `_source_repo` | +8 |
| 2 | `daemon/sources/mapper.py` (lines 27-31, 89-106) | Add `SOURCE_TYPE_DISCORD` constant, add to `VALID_SOURCE_TYPES`, add Discord ID validation branch | +20 |
| 3 | `daemon/routers/sources.py` (lines 207-209) | Replace "not implemented" stub with `DiscordAdapter.test_connection()` call | +5 |
| 4 | `pyproject.toml` (dependencies) | Add `discord.py>=2.4.0` (or `py-cord>=2.6.0` — see Phase 1 decision) | +1 |

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Core Adapter Scaffold | DiscordAdapter class with discord.py client, credential validation, lifecycle, registry registration | 10 | independent (no other phase depends on Discord protocol details) | pending |
| 2 | Message Flow | Bidirectional message normalization: inbound Discord→IncomingMessage, outbound OutgoingMessage→Discord | 7 | tight with Phase 1 (shares adapter class); loose with Phase 3 (send uses ordering locks) | pending |
| 3 | Resilience & Threading | Rate limiting, circuit breaker, per-channel locks, DiscordThreadManager, TTL eviction task, health check | 8 | tight with Phase 2 (send path); loose with Phase 1 (lifecycle hooks) | pending |
| 4 | Testing & Integration | Full test suite + registry/mapper/router integration tests | 8 | tight with all phases (tests validate all) | pending |

## Coupling Map

| | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| Phase 1 | — | tight (shared adapter class) | loose (lifecycle hooks) | tight (tests validate scaffold) |
| Phase 2 | tight | — | tight (send path uses locks + circuit breaker) | tight (tests validate normalization) |
| Phase 3 | loose | tight | — | tight (tests validate resilience) |
| Phase 4 | tight | tight | tight | — |

**Phase ordering is strictly sequential**: 1 → 2 → 3 → 4. Each phase's exit criterion gates the next.

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | discord.py API/version drift breaks adapter | High | Medium | Pin version in pyproject.toml; isolate discord.py imports behind adapter package; add version-gated CI test |
| 2 | Missing MESSAGE_CONTENT intent yields empty message bodies | High | Medium | Validate intent config at startup; fail with clear error if MESSAGE_CONTENT requested but unavailable; document in config guide |
| 3 | Duplicate outbound sends after timeout/reconnect | Medium | Medium | Document as known limitation; rely on discord.py's request dedup where available; avoid blind retries in send(); Phase 3 circuit breaker limits damage |
| 4 | Gateway reconnect storm under network instability | Medium | Low | discord.py handles jittered backoff resume; adapter classifies fatal vs retryable auth failures; add reconnect metrics logging |
| 5 | discord.py asyncio event loop conflicts with ensemble's existing event loop | High | Low | discord.py is fully async; ensure client starts within ensemble's running event loop (not a new thread); test with ensemble's async lifecycle |
| 6 | Thread auto-archive creates routing dead-ends | Medium | Medium | DiscordThreadManager tracks archive state; policy: route to parent channel on archived thread send (configurable); log warning |
| 7 | Lock/cache memory growth under high channel volume | Low | Low | OrderedDict + LRU eviction with configurable `MAX_CHANNEL_LOCKS = 1000` (matches NFR-9 and Telegram's `MAX_CHAT_LOCKS = 1000`); metrics on eviction count |
| 8 | Source type validation regression in mapper.py | Medium | Low | Add Discord ID pattern validation + unit tests in Phase 4; validate against real Discord snowflake ID format |

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | Discord source can be created and started via API | POST /sources with source_type="discord", start source, GET /sources/{id} shows "running" | Status="running" within 10s of start |
| 2 | DM messages route to ensemble agent and receive responses | Send DM to Discord bot → agent processes → response delivered back to DM channel | End-to-end response received within 30s |
| 3 | Guild channel messages with mention activate agent | @mention bot in server channel → agent responds in same channel | Response mentions/addresses the original user |
| 4 | Guild channel messages without mention are ignored | Send message without @mention in server channel → no agent response | Zero agent response, debug log confirms skip |
| 5 | Thread messages get separate agent instances | Message in thread A vs thread B → different instance IDs | Unique external_user_id per thread; mapping persists |
| 6 | Adapter survives Discord Gateway disconnect and resume | Kill WebSocket connection → discord.py resumes → messages flow again | No data loss; resume within 60s |
| 7 | Health check returns true when connected, false when disconnected | GET /sources/{id}/health at various states | Correct boolean for RUNNING/STOPPED/ERROR |
| 8 | All unit tests pass | `pytest tests/test_discord_adapter.py tests/test_discord_thread_manager.py` | 100% pass rate, 0 failures |
| 9 | Rate limiting prevents API errors under load | Send 100 rapid messages → no 429 errors from Discord | discord.py bucket handling + adapter semaphore prevent 429s |
| 10 | test_connection API endpoint works for Discord | POST /sources/test with Discord config + valid token | Returns (true, "Connected as BotName#1234") |

## Research Insights

Key findings from the technical analysis (`.agents/shared/planning/discord-source/technical-analysis.md`) and codebase exploration that shaped this plan:

- **discord.py is the right library choice** (technical-analysis.md:129-131): delegates Gateway/heartbeat/resume and dynamic rate-limit bucket logic; matches Slack SDK-backed precedent
- **Slack-tier-style rate limiter is wrong for Discord** (technical-analysis.md:189-191): Discord buckets are dynamic (HTTP header-based), unlike Slack's static tiers. Thin semaphore + SDK delegation only.
- **DB-backed send routing is the proven pattern** (`daemon/sources/adapters/slack/adapter.py:416-449`): `source_repo.get_instance_mapping(source_id, external_user_id)` → `mapping_metadata` → channel/thread IDs
- **Per-channel LRU locks are established** (`telegram.py:114-142`, `slack/adapter.py:124-126`): OrderedDict with configurable cap + `move_to_end`/`popitem(last=False)` eviction
- **Mention-gating pattern** (`slack/adapter.py:666-700`): DM always True; guild channels check `<@bot_id>` in text; configurable `channel_require_mention` flag
- **Mapper validation gap** (`mapper.py:27-31`): `VALID_SOURCE_TYPES` and `SOURCE_TYPE_*` constants do NOT include Discord — must be added
- **Router stub exists** (`routers/sources.py:207-209`): Discord test_connection returns "not implemented" — replace with real implementation
- **Existing SourceType enum includes discord** (`models/source.py:23`): `discord = "discord"` already in the enum; no model change needed

## Dependencies

| Dependency | Type | Version | Purpose |
|------------|------|---------|---------|
| discord.py | new (pip) | >=2.4.0 | Discord Gateway WebSocket + REST API client |
| aiohttp | existing | >=3.9.0 | Already used by Telegram adapter; discord.py also depends on it |
| CircuitBreaker | existing (internal) | n/a | `daemon/sources/circuit_breaker.py` — shared resilience utility |
| TokenBucketLimiter | existing (internal) | n/a | `daemon/sources/rate_limiter.py` — available if needed, but Discord uses SDK rate limiting |
| SourceRepository | existing (internal) | n/a | DB-backed instance mapping lookup for send routing |

## Open Questions

All four open questions have been **RESOLVED** by the Leader. They are tracked here for reference; none remain blocking.

1. **Library choice: discord.py vs py-cord?** — ✅ **RESOLVED.** Use `discord.py` for ecosystem maturity, with `py-cord` as a documented fallback if the Python 3.13 compatibility gate (Phase 1 Task 0) fails. Pin in `pyproject.toml`.
2. **Should missing MESSAGE_CONTENT intent be a startup error or degrade gracefully?** — ✅ **RESOLVED.** **FAIL-CLOSED with clear error message.** If the operator configures `intents.message_content = true` but the bot does not have the intent granted/verified at `start()`, `start()` logs a clear, actionable error and raises. No silent empty-message processing under any circumstance.
3. **Archived thread behavior: reopen, route to parent, or fail?** — ✅ **RESOLVED.** Route to parent channel. `DiscordThreadManager` tracks archive state; on archived-thread send, route the message to the parent channel with a warning log. (Documented in Risk #6.)
4. **Max channel lock count.** — ✅ **RESOLVED.** `MAX_CHANNEL_LOCKS = 1000`, matching NFR-9 and Telegram's `MAX_CHAT_LOCKS = 1000`. (Documented in Risk #7.)
