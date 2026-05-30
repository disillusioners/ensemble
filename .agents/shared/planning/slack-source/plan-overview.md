# Plan Overview: Slack Source Integration

## Objective

Add Slack as a pluggable message source to Ensemble using Socket Mode (persistent WebSocket), supporting DMs, channels, and threads with proper instance isolation and routing. The integration follows the established 7-step source integration checklist and reuses all existing infrastructure (SourceRegistry, InstanceMapper, ResponseDispatcher, CircuitBreaker, RateLimiter).

## Scope Assessment

**LARGE** — Multi-module, multi-day effort spanning adapter package (5+ files), registry changes, mapper changes, API model changes, test suite (4+ test files), and documentation. The adapter is complex enough to warrant a package (not a single file) due to Socket Mode's async lifecycle, DM resolution API, thread TTL management, and Slack Blocks handling.

## Context

- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Reference Implementation**: `daemon/sources/adapters/telegram.py` (626 lines, single-file adapter)
- **Source System Files**: `daemon/sources/` — base.py, registry.py, mapper.py, dispatcher.py, rate_limiter.py, circuit_breaker.py
- **API Models**: `daemon/models/source.py` — SourceType enum, SourceCreate/Update/Info models
- **API Routes**: `daemon/routers/sources.py` — CRUD endpoints, `daemon/routers/webhooks.py` — webhook receiver

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Core Adapter + Integration Points | SlackAdapter package with Socket Mode, registry wiring, enum + mapper + API model changes | None | — | 8-10h |
| 2 | Instance Routing + Thread Lifecycle | DM resolution, composite external_user_id, thread TTL, channel routing, response dispatch | Phase 1 | tight | 6-8h |
| 3 | Testing + Polish + Documentation | Full test suite, error handling hardening, Slack Blocks support, slash commands, setup docs | Phase 2 | loose | 6-8h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| Phase 1 → Phase 2 | **tight** | Phase 2 depends on the adapter package structure, InstanceMapper changes, and composite ID format from Phase 1. They share files in `daemon/sources/adapters/slack/` and `daemon/sources/mapper.py`. |
| Phase 2 → Phase 3 | **loose** | Phase 3 tests against the interfaces defined in Phase 2. Tests can be written independently and only need the adapter API surface, not implementation details. |

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Response routing: metadata is empty at dispatch time** | HIGH — `ResponseDispatcher.dispatch_completed()` constructs `OutgoingMessage(metadata={})` — Slack's `slack_channel_id` is NOT passed through. Telegram works because `external_user_id` IS the chat_id. Slack's composite `external_user_id` (e.g. `TWS:U1`) is NOT routable. | **DB lookup in `send()`**: SlackAdapter's `send()` looks up `mapping_metadata` from the DB using `source_id` + `external_user_id` to retrieve `slack_channel_id` and `slack_thread_ts`. This is the canonical routing strategy for Slack. Keeps changes contained to the adapter — no dispatcher modifications needed. Also fixes `/new` confirmation routing. |
| **JobQueue path dispatches responses** | ~~HIGH~~ ✅ **Resolved** (commit `5468a76`) — Both WorkerPool and JobQueue paths now call `dispatch_completed()`. No action needed for Slack. | No action needed. Kept as historical reference. |
| **Slack Socket Mode connection instability** | MEDIUM — WebSocket connections can drop, requiring reconnection logic. | Leverage SourceRegistry's supervisor loop with exponential backoff (already built). slack-bolt's SocketModeHandler has built-in reconnection. |
| **Thread TTL adds complexity** | MEDIUM — Thread instances with 24h TTL need cleanup tracking. Evicted instances must also be terminated. | Use SourceCleanup's periodic cleanup job. ThreadManager eviction calls `manager.terminate_instance()`. |
| **Rate limiting differs by API tier** | MEDIUM — Slack has 4 tiers with different limits (1/min to 100+/min). | Create SlackRateLimiter with per-tier token buckets, separate from the global adapter rate limiter. |
| **DM resolution requires conversations.open() API call** | LOW — Extra API call per new DM user to get channel_id. | Cache DM channel IDs per user. TTL-based cache eviction. |
| **slack-bolt dependency conflicts** | LOW — New dependency could conflict with existing packages. | Pin versions in requirements.txt. slack-bolt depends on slack-sdk which is well-maintained. |
| **Mapping metadata size limits** | LOW — Storing slack_channel_id in mapping_metadata may need schema awareness. | mapping_metadata is JSON column — no size limit concern. |

## Success Criteria

- [ ] Slack adapter connects via Socket Mode and receives messages in real-time
- [ ] DMs create 1:1 user-to-instance mappings (same as Telegram private chats)
- [ ] Channel messages route to shared instances (same as Telegram groups)
- [ ] Thread replies create separate instances with 24h TTL
- [ ] Agent responses dispatch back to correct Slack channel/thread
- [ ] Reaction indicator (✅) shown while agent processes
- [ ] Slack files/images received and forwarded to agent as images
- [ ] `/new` slash command resets conversation (same UX as Telegram)
- [ ] Circuit breaker and rate limiting protect against Slack API failures
- [ ] Health check verifies Socket Mode connection is alive
- [ ] Source test endpoint validates bot token and connection
- [ ] Full test suite with >90% coverage on adapter code
- [ ] Both WorkerPool and JobQueue processing paths deliver responses

## 7-Step Source Integration Checklist

This is the canonical checklist for adding a new source type, derived from reading the codebase:

| # | Step | File | Current State | Change Needed |
|---|------|------|---------------|---------------|
| 1 | Add `slack` to SourceType enum | `daemon/models/source.py` | Has telegram, webhook, whatsapp, discord, scheduler | Add `slack = "slack"` |
| 2 | Create adapter class(es) | `daemon/sources/adapters/slack/` | N/A | Create package with `__init__.py`, `adapter.py`, `rate_limiter.py`, `thread_manager.py` |
| 3 | Import adapter in `__init__.py` | `daemon/sources/adapters/__init__.py` | Imports TelegramAdapter, SchedulerAdapter | Add `from .slack import SlackAdapter` |
| 4 | Add elif in `_create_adapter_from_config()` | `daemon/sources/registry.py` L270-348 | Has telegram + scheduler branches | Add `elif source_type == "slack":` branch |
| 5 | Add to `supported_types` set | `daemon/routers/sources.py` L113 | `{"telegram", "webhook", "whatsapp", "discord", "scheduler"}` | Add `"slack"` |
| 6 | Update webhook compatibility check (if needed) | `daemon/routers/webhooks.py` L52 | Checks `"webhook", "telegram"` | Slack uses Socket Mode — NOT webhooks. No change needed. |
| 7 | Update mapper validation | `daemon/sources/mapper.py` L26-28 | `VALID_SOURCE_TYPES = {"telegram", "webhook"}` | Add `"slack"` to set + add Slack-specific user ID validation |

**Additional changes beyond the 7 steps:**
- `daemon/routers/sources.py` test_source(): Add `elif test_request.source_type == SourceType.slack:` branch
- `daemon/sources/rate_limiter.py`: Add Slack to `DEFAULT_RATE_LIMITS`
- `daemon/sources/registry.py`: Inject `_source_repo` into SlackAdapter after construction (for DB lookup routing)
- `daemon/sources/registry.py` `_handle_message()`: Extract Slack metadata and pass as `extra_mapping_metadata` to mapper
- `daemon/sources/__init__.py`: No change needed (adapter imported via registry)

## Tracking

- Created: 2026-05-25
- Last Updated: 2026-05-25
- Status: draft
- Knowledge Base Entries: slack-source-integration-plan, slack-source-plan-7-file-comprehensive-p
