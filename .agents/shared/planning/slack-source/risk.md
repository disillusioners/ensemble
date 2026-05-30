# Risk Assessment: Slack Source Integration

## HIGH RISK

### R1: Response Routing via DB Lookup

**Description**: Slack's composite `external_user_id` (e.g. `TWS:U1`) is NOT a routable Slack channel ID. The dispatcher constructs `OutgoingMessage(metadata={})` — always empty metadata. Unlike Telegram where `external_user_id` IS the `chat_id`, Slack must resolve routing differently.

**Evidence**:
- `ResponseDispatcher.dispatch_completed()` creates `OutgoingMessage(metadata=metadata or {})` — always `{}`
- Neither `task_processor.py` nor `message_job_handler.py` pass Slack routing data
- Telegram works because `message.external_user_id` = numeric chat_id, directly usable
- Slack's `TWS:U1` format is meaningless as a Slack API channel parameter

**Mitigation (ADOPTED)**: DB lookup in `send()`.
- `SlackAdapter.send()` calls `self._source_repo.get_instance_mapping(source_id, external_user_id)`
- Reads `slack_channel_id` and `slack_thread_ts` from `mapping_metadata` JSON column
- `_source_repo` is injected by `registry._create_adapter_from_config()` after adapter construction
- Works for ALL callers: dispatcher, `/new` confirmation, progressive delivery

**Trade-offs**:
- ✅ No dispatcher changes needed
- ✅ No base interface changes
- ✅ Works for all send() callers uniformly
- ⚠️ One extra DB read per response (indexed, fast)
- ⚠️ Adapter needs `_source_repo` reference

**Likelihood**: N/A (design choice, not a failure mode)
**Impact**: Critical if not addressed (all responses lost)

---

### R2: ~~JobQueue Path Doesn't Dispatch Responses~~ — RESOLVED

**Status**: ✅ **Already mitigated** in commit `5468a76`. Both WorkerPool and JobQueue paths now call `dispatch_completed()`.

**Historical context** (kept for reference):
- The `enqueue_message_via_jq()` path (via JobQueueService → MessageJobHandler) previously did NOT call `dispatch_completed()`. If Slack messages were routed through the JobQueue path, agent responses would never reach Slack.
- `SourceRegistry._handle_message()` (line 688) calls `self._manager.enqueue_message()` — the WorkerPool path — which has always dispatched correctly.
- The JobQueue path was fixed to also call `dispatch_completed()`, so both paths now deliver responses reliably.

**No action needed** for Slack integration. Both processing paths work correctly.

---

### R3: Socket Mode Connection Stability

**Description**: WebSocket connections can drop due to network issues, Slack maintenance, or token expiration. The adapter must handle reconnection gracefully.

**Evidence**: 
- Slack Socket Mode connections are long-lived WebSockets
- Slack may disconnect for maintenance or rate limiting
- Network interruptions are common in production

**Mitigation**:
- Leverage SourceRegistry's supervisor loop (exponential backoff, auto-restart)
- slack-bolt's `AsyncSocketModeHandler` has built-in reconnection logic
- Circuit breaker prevents cascading failures during outages
- Health check verifies connection is alive (periodic auth.test call)

**Likelihood**: Medium
**Impact**: High (adapter stops receiving/sending until reconnected)

---

## MEDIUM RISK

### R4: Thread Instance Memory Growth

**Description**: High-traffic Slack workspaces could create many thread instances, consuming memory in the ThreadManager's in-memory tracking. Evicted instances must also be terminated.

**Evidence**: 
- Each thread instance stores external_user_id, agent_instance_id, timestamps
- No automatic cleanup for expired threads (relies on next access to check TTL)
- Very active channels could create 100+ threads per day

**Mitigation**:
- 50-thread cap per workspace with LRU eviction
- 24-hour TTL auto-expires stale threads
- ThreadManager eviction calls `manager.terminate_instance()` to free agent resources
- SourceCleanup periodic job can scan and clean expired thread mappings
- Consider adding periodic TTL scan (every hour) to proactively clean up

**Likelihood**: Low (cap prevents unbounded growth)
**Impact**: Medium (memory usage, potential stale instances)

---

### R5: Slack Rate Limit Tier Complexity

**Description**: Slack has 4 different rate limit tiers for different API methods. Incorrect tier mapping or insufficient rate limiting could cause API errors.

**Evidence**: 
- Tier 1: 1 req/min (conversations.create)
- Tier 2: 5 req/min (conversations.open, conversations.members) 
- Tier 3: 50 req/min (chat.postEphemeral, reactions.add)
- Tier 4: 100+ req/min (chat.postMessage, chat.update)
- Burst traffic (many simultaneous conversations) could hit limits

**Mitigation**:
- Per-tier token bucket rate limiter with conservative defaults
- Map every Slack API method to its tier
- Handle rate_limited responses with Retry-After header
- Circuit breaker prevents API flood during failures

**Likelihood**: Medium
**Impact**: Medium (temporary API errors, messages delayed)

---

### R6: DM Resolution Latency

**Description**: First DM to a user requires `conversations.open()` API call (Tier 2: 5/min). High concurrent DM volume could hit rate limits.

**Evidence**: 
- Each new DM user requires one conversations.open() call
- Tier 2 allows only 5 requests per minute
- Bulk DM scenarios could exhaust limit quickly

**Mitigation**:
- Cache DM channel IDs per user (memory cache, no TTL needed — IDs are stable)
- Only call conversations.open() on cache miss
- Rate-limited DM resolution queues and retries

**Likelihood**: Low (typical usage is well under 5/min)
**Impact**: Medium (delayed first DM, but cached afterwards)

---

## LOW RISK

### R7: Slack Blocks Formatting Compatibility

**Description**: Agent responses are Markdown; Slack uses mrkdwn (subset of Markdown). Some formatting may not render correctly.

**Evidence**: 
- Slack mrkdwn doesn't support: headers (#), tables, HTML, nested lists well
- Long messages need to be split into blocks (3000 char limit per block)
- Code blocks may not render identically

**Mitigation**: 
- MVP-quality best-effort conversion in `blocks.py` — handles basic formatting only
- Full production Block Kit conversion is a **future improvement**, not a Phase 3 deliverable
- Fallback to plain text for unsupported formatting
- Slack's mrkdwn handles most common cases (bold, italic, code, links)

**Likelihood**: Medium
**Impact**: Low (cosmetic only, doesn't affect functionality)

---

### R8: slack-bolt Dependency Conflicts

**Description**: Adding slack-bolt and slack-sdk could conflict with existing Python dependencies.

**Evidence**: 
- slack-bolt depends on slack-sdk, which is well-maintained
- Both are pure-Python packages with minimal dependencies
- No known conflicts with aiohttp, fastapi, or other Ensemble dependencies

**Mitigation**: 
- Pin versions in requirements.txt
- Test in clean virtual environment before merging

**Likelihood**: Very Low
**Impact**: Low (dependency resolution)

---

### R9: Mapping Metadata Size

**Description**: Storing extra fields (slack_channel_id, slack_thread_ts) in mapping_metadata JSON column.

**Evidence**: 
- mapping_metadata is a JSON column with no explicit size limit
- Extra fields add ~50-100 bytes per mapping
- Minimal compared to existing metadata

**Mitigation**: None needed — JSON column handles this fine.

**Likelihood**: N/A
**Impact**: Negligible
