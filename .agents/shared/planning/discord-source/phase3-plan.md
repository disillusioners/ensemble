# Phase 3: Resilience & Threading

## Objective

Add production-grade resilience to the Discord adapter: Discord-aware rate limiting (concurrency semaphore + SDK delegation), circuit breaker integration on the send path, per-channel ordering locks with LRU eviction, thread lifecycle management with TTL/archive tracking, and comprehensive health checks.

## Files to Fill (from Phase 1 stubs)

| # | File | Change |
|---|------|--------|
| 1 | `daemon/sources/adapters/discord/rate_limiter.py` | Implement `DiscordRateLimiter` — concurrency semaphore + metrics |
| 2 | `daemon/sources/adapters/discord/thread_manager.py` | Implement `DiscordThreadManager` — TTL, LRU, archive tracking |
| 3 | `daemon/sources/adapters/discord/adapter.py` | Wire rate limiter, circuit breaker, ordering locks, thread manager, and TTL eviction task into `send()` and `start()/stop()` |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Implement `DiscordRateLimiter` in `rate_limiter.py`: `__init__(max_concurrent_sends=5)` → `asyncio.Semaphore(max_concurrent_sends)`; methods `async acquire()` (wraps semaphore), `async release()`, context manager `async with rate_limiter:`; metrics: `_active_sends`, `_total_sends`, `_rate_limit_waits` (counts times a caller waited on the semaphore — a "wait", not a rejection); do NOT implement bucket/route logic — discord.py handles dynamic rate limits via HTTP headers internally. Pattern rationale: technical-analysis.md:189-191. | none | Semaphore limits concurrent sends to max_concurrent; metrics track send counts and semaphore waits; no attempt to override SDK 429 handling; `_rate_limit_waits` increments when `acquire()` blocks, not when calls are rejected (the semaphore blocks, it does not reject) |
| 2 | Implement per-channel ordering locks in `adapter.py`: `_channel_locks: OrderedDict[str, asyncio.Lock]` with `_channel_locks_guard`; `_get_channel_lock(channel_id)` with LRU eviction (`MAX_CHANNEL_LOCKS=1000`, `move_to_end`/`popitem(last=False)`). Pattern: Telegram `_get_chat_lock` at `telegram.py:128-142`, Slack at `adapter.py:124-126`. Per-channel locks key on the canonical `external_user_id` (or the resolved channel_id for channel ordering). Matches NFR-9 and Telegram's `MAX_CHAT_LOCKS=1000`. **See `_get_channel_lock()` Atomicity & Eviction Safety contract below** for the must-skip-held-locks semantics. | Phase 1 Task 3 | Lock acquired per channel preserves send ordering; LRU evicts at 1000 entries; concurrent sends to different channels are parallel; eviction skips held locks (active lock is never evicted) |
| 3 | Integrate circuit breaker into `send()` path: before rate limiter and lock acquisition, check `circuit_breaker.can_execute()`; after send attempt, call `record_success()` or `record_failure()`. Move the circuit breaker check to the TOP of send (before any resource acquisition) to avoid wasting rate-limit tokens when circuit is open. Pattern: Telegram `send()` at `telegram.py:290-298`. **See Circuit Breaker Failure Classification contract below** for what counts as a failure vs. a rate-limit signal. | 1,2 | Circuit open → send returns False without acquiring lock/token; send failure → circuit records failure ONLY on transport/5xx/timeout errors (NOT 429); 5 failures → circuit opens for 60s; 429 responses do NOT increment the failure count (rate-limit signal, not transport failure) |
| 4 | Integrate `DiscordRateLimiter` into `send()`: acquire semaphore before discord.py send call; release after (use `try/finally` to ensure release on exception). Position: after circuit breaker check, after lock acquisition, before discord.py `channel.send()`. | 1,3 | Concurrent sends capped at max_concurrent; semaphore released on both success and exception; no deadlock |
| 5 | Implement `DiscordThreadManager` in `thread_manager.py`: `ThreadInstance` dataclass (thread_id, channel_id, guild_id, instance_id, created_at, last_accessed, is_archived, archive_timestamp); `__init__(manager, ttl_seconds=86400, max_threads_per_guild=50)`; storage: `dict[str, OrderedDict[str, ThreadInstance]]` (guild_id → thread map); methods: `register_thread()`, `get_thread()`, `evict_expired()`, `mark_archived()`, `shutdown()`. Track thread archive state. When a thread is archived (`is_archived=True`), outbound messages route to the parent `channel_id` instead of the `thread_id`; the manager should detect archived threads and update routing. **See DiscordThreadManager Lifecycle & Shutdown Contract below** for the complete `shutdown()` API, per-guild lock synchronization, and idempotency semantics. Pattern: Slack `ThreadManager` at `slack/thread_manager.py:29-100`. | none | Thread instances registered and retrieved; TTL expiry evicts + terminates instance; LRU evicts at max_threads_per_guild; archive state tracked; `shutdown()` terminates all tracked instances; shutdown with one failing termination continues for the rest; guild dict access is lock-protected; shutdown is idempotent |
| 6 | Wire `DiscordThreadManager` into `adapter.py`: init in `__init__()` if `manager` is provided (same pattern as Slack: `adapter.py:133-135`); call `register_thread()` in `_normalize_incoming()` when message is in a thread; check `mark_archived()` on outbound send — if thread archived, route to parent channel with warning log; call `shutdown()` in `stop()`. | 2,5 | Thread instances created on first thread message; expired/archived threads evicted; archived thread send falls back to parent channel; `stop()` terminates all thread instances |
| 7 | Enhance `health_check()` to include Gateway state: check status==RUNNING; client exists and `client.is_ready()`; optionally check `client.latency` < 5000ms (Gateway heartbeat latency); log health state with latency. If latency check unavailable, skip gracefully. | Phase 1 Task 6 | Returns True only when Gateway connected + identified + reasonable latency; returns False on disconnect/ERROR; does NOT make REST calls |
| 8 | **Create TTL eviction task in `start()` after Gateway connection is confirmed** — define module-level constant `EVICTION_INTERVAL_SECONDS = 3600` (1 hour) — overridable per-source via `SourceConfig.config["eviction_interval_seconds"]` if operator wants a shorter cycle (e.g., 1800 for half-hourly); after Phase 1 Task 4 transitions STARTING→RUNNING and `_ready_event` is set, schedule the periodic eviction loop by calling `self._ttl_task = asyncio.create_task(self._periodic_eviction_loop())` (either inline in `start()` after the RUNNING transition, or via a `_post_connect()` hook that `start()` invokes). The `_periodic_eviction_loop()` method is an `async def` containing `while True: try: await asyncio.sleep(EVICTION_INTERVAL_SECONDS); await self._thread_manager.evict_expired(); except asyncio.CancelledError: break` — the `except` clause exits cleanly when `stop()` cancels the task. See **TTL Eviction Task Lifecycle Contract** below for the complete specification, including how `stop()` awaits the cancelled task and what happens when `_thread_manager` is `None`. | 6, Phase 1 Task 4 | `_ttl_task` is created during `start()` AFTER Gateway is confirmed (i.e., after RUNNING transition, not before — otherwise the loop would run against an unconnected adapter); `_periodic_eviction_loop()` calls `self._thread_manager.evict_expired()` every `EVICTION_INTERVAL_SECONDS` (default 3600); loop exits cleanly on `asyncio.CancelledError` without logging an error; `stop()` cancels and awaits the task before clearing per-channel locks; task leak is impossible because `stop()` is idempotent and re-entrant |

## DiscordRateLimiter Design

```python
class DiscordRateLimiter:
    """Thin concurrency limiter for Discord REST sends.

    Does NOT implement route buckets or global rate limits — discord.py
    handles those internally via HTTP response headers (X-RateLimit-Bucket,
    X-RateLimit-Remaining, Retry-After). This limiter only prevents the
    adapter from overwhelming discord.py's HTTP handler with too many
    concurrent send calls.
    """

    def __init__(self, max_concurrent_sends: int = 5):
        self._semaphore = asyncio.Semaphore(max_concurrent_sends)
        self._active_sends = 0
        self._total_sends = 0
        # Counts times a caller had to wait on the semaphore because all
        # concurrent-send slots were in use. A "wait", not a "rejection" —
        # asyncio.Semaphore blocks instead of raising.
        self._rate_limit_waits = 0

    async def __aenter__(self):
        # If the semaphore is saturated, the caller will block here.
        # We can't directly observe the wait inside __aenter__, but the
        # caller can increment _rate_limit_waits via acquire() if needed.
        await self._semaphore.acquire()
        self._active_sends += 1
        self._total_sends += 1
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._active_sends -= 1
        self._semaphore.release()
```

## DiscordThreadManager Design

```python
@dataclass
class ThreadInstance:
    thread_id: str
    channel_id: str           # Parent channel
    guild_id: str
    instance_id: str | None
    created_at: float
    last_accessed: float
    is_archived: bool = False
    archive_timestamp: float | None = None

class DiscordThreadManager:
    DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24 hours (matches Slack default)
    DEFAULT_MAX_THREADS_PER_GUILD = 50

    # Storage: guild_id -> OrderedDict[thread_id, ThreadInstance]
    # Eviction: LRU via move_to_end + popitem(last=False)
    # Archive policy: route_to_parent (configurable)
```

## Circuit Breaker Failure Classification

The circuit breaker on the `send()` path MUST classify failures precisely. **429 responses are rate-limit signals, not transport failures. They must not increment the circuit breaker failure count. The rate limiter handles backoff.**

| Signal | Counts as failure? | Action |
|--------|--------------------|--------|
| `aiohttp.ClientError` (connection errors, DNS failure, TLS failure) | ✅ Yes | Call `record_failure()` |
| `ConnectionError` (stdlib, raised by discord.py / aiohttp) | ✅ Yes | Call `record_failure()` |
| 5xx server errors (HTTP 500, 502, 503, 504) | ✅ Yes | Call `record_failure()` |
| `asyncio.TimeoutError` (send timeout) | ✅ Yes | Call `record_failure()` |
| 429 Too Many Requests | ❌ **No** | Do NOT call `record_failure()`. discord.py's internal bucket handler backs off; the rate limiter's `asyncio.Semaphore` waits for an available slot. |

**Reference:** Telegram's `_api_call()` records failures on `aiohttp.ClientError` and `TelegramAPIError`, but rate-limit-equivalent responses (if any) are excluded. Discord follows the same principle but the distinction is sharper because discord.py handles 429 internally via dynamic bucket headers — the adapter sees a successful return after the SDK's internal retry, not a 429 itself.

The classification logic lives in `_send_with_circuit_breaker()` (or equivalent) and runs AFTER the discord.py `channel.send()` call returns. If discord.py itself raises (network drop, connection lost, HTTPException on 5xx), the exception type drives the classification. If discord.py returns successfully after internal backoff, the call is a success and `record_success()` is called.

## `_get_channel_lock()` Atomicity & Eviction Safety

The `_get_channel_lock(channel_key)` method MUST be atomic with respect to the `_channel_locks_guard`. Eviction MUST skip held locks so an in-flight message is never disrupted.

**Atomic lookup/insert under guard:**

```python
async def _get_channel_lock(self, channel_key: str) -> asyncio.Lock:
    async with self._channel_locks_guard:
        if channel_key in self._channel_locks:
            # MRU touch
            self._channel_locks.move_to_end(channel_key)
            return self._channel_locks[channel_key]
        # New entry — capacity check + eviction
        while len(self._channel_locks) >= self.MAX_CHANNEL_LOCKS:
            evicted = self._evict_oldest_unlocked_lock()
            if evicted is None:
                # All locks are held — temporarily exceed cap until one releases.
                # Do NOT block; the caller will await the new lock after release.
                break
        lock = asyncio.Lock()
        self._channel_locks[channel_key] = lock
        return lock
```

**Eviction safety — MUST skip held locks:**

```python
def _evict_oldest_unlocked_lock(self) -> str | None:
    """Evict the oldest UNLOCKED entry. Returns the evicted key, or None
    if every entry is currently held (locked). MUST NOT evict a held
    lock — doing so would break ordering for an in-flight message.
    """
    # OrderedDict preserves insertion order; oldest = first item.
    for key in list(self._channel_locks.keys()):
        lock = self._channel_locks[key]
        if not lock.locked():
            self._channel_locks.popitem(last=False) if key == next(iter(self._channel_locks)) else self._channel_locks.pop(key)
            return key
        # else: skip — held lock is in use, leave it alone
    return None  # all locks held
```

**Acceptance criteria:**
- `_get_channel_lock()` is atomic under `_channel_locks_guard` — no concurrent reader/writer can observe a torn state.
- `move_to_end` runs on every hit, so frequently-used channels stay resident.
- Eviction skips locked entries — an in-flight message's lock is NEVER evicted.
- When every entry is locked and capacity is reached, capacity temporarily exceeds `MAX_CHANNEL_LOCKS` until at least one lock is released. This is intentional: blocking the caller would risk deadlock with the LRU eviction scan. The cap is a soft memory bound, not a hard concurrency limit.

## DiscordThreadManager Lifecycle & Shutdown Contract

The `DiscordThreadManager.shutdown()` method MUST terminate all tracked thread instances gracefully, continue past individual failures, and be idempotent.

**`shutdown()` method specification:**

```python
async def shutdown(self) -> None:
    """Gracefully terminate all tracked thread instances.

    Iterates every guild's OrderedDict. For each ThreadInstance, calls
    the instance termination API (via manager.spawn_instance() cleanup,
    or equivalent). If one instance fails, LOG the error and CONTINUE.
    Do NOT block shutdown on a single failure. Collect failures and log
    a summary at the end.

    Idempotent: calling shutdown() twice is safe. The second call finds
    no live instances and returns immediately.
    """
    failures: list[tuple[str, str, Exception]] = []
    for guild_id, thread_map in list(self._guild_threads.items()):
        async with self._guild_locks[guild_id]:  # per-guild lock
            for thread_id, instance in list(thread_map.items()):
                try:
                    if instance.instance_id and self._manager:
                        # Terminate the spawned ensemble instance.
                        await self._manager.terminate_instance(
                            instance.instance_id, reason="thread_shutdown"
                        )
                except Exception as exc:
                    logger.error(
                        "thread_shutdown_failed",
                        extra={
                            "guild_id": guild_id,
                            "thread_id": thread_id,
                            "instance_id": instance.instance_id,
                            "error": str(exc),
                        },
                    )
                    failures.append((guild_id, thread_id, exc))
                    continue  # do NOT block on this instance
                finally:
                    thread_map.pop(thread_id, None)
    # Clear all guild state after iteration completes.
    self._guild_threads.clear()
    self._guild_locks.clear()
    if failures:
        logger.warning(
            "thread_shutdown_partial_failure",
            extra={"failed_count": len(failures)},
        )
```

**Per-guild dict synchronization:**

Each guild's `OrderedDict[thread_id, ThreadInstance]` MUST be protected by a per-guild `asyncio.Lock`. The lock is acquired before any read, write, or eviction operation on that guild's map. The lock map itself is created lazily on first access:

```python
self._guild_locks: dict[str, asyncio.Lock] = {}

def _get_guild_lock(self, guild_id: str) -> asyncio.Lock:
    if guild_id not in self._guild_locks:
        self._guild_locks[guild_id] = asyncio.Lock()
    return self._guild_locks[guild_id]
```

**Lazy-init TOCTOU race — benign and accepted:**

The `_guild_locks` dict uses lazy initialization via `_get_guild_lock(guild_id)`. A benign TOCTOU (time-of-check / time-of-use) race exists in the dict lookup-and-insert: two concurrent calls for the same new `guild_id` could each observe `guild_id not in self._guild_locks`, each create a fresh `asyncio.Lock`, and the second write would overwrite the first in the dict. This is **accepted as benign** because:

1. (a) Only one Lock will be retained in the dict (last-write-wins); the other becomes garbage immediately and gets collected.
2. (b) Any code paths that obtained the transient (overwritten) Lock will simply synchronize on it once, then release — the lock object is still a valid `asyncio.Lock`. The next access uses the retained Lock. No caller's synchronization semantics are violated, because `asyncio.Lock` is re-entrant for the owner only and the transient lock is owned by exactly the task that awaited it.
3. (c) No data corruption is possible — the per-guild `OrderedDict` access is always guarded by `async with self._guild_locks[guild_id]:`, which serializes through whichever Lock the caller resolved. The transient Lock and the retained Lock both protect the same dict; losing the race means losing nothing.
4. (d) No deadlock is possible — even if a caller holds the transient (orphaned) Lock, the retained Lock has no waiters, so no circular wait can form.

An outer guard lock around the lazy-init could eliminate this race but adds contention for no practical benefit (the race is one-time per guild, then stable). The pattern matches Telegram's `_get_chat_lock` (which has the same TOCTOU shape) and Slack's adapter-level lock map. Do NOT wrap `_get_guild_lock` in a `threading.Lock` or `asyncio.Lock` "fix" — it would slow every lock lookup by an unnecessary acquisition.

**Acceptance criteria:**
- `ThreadManager.shutdown()` terminates all tracked instances.
- If one instance termination raises, others still terminate. Failure is logged, not propagated.
- Guild dict access is lock-protected — concurrent register/get/evict on the same guild serialize through the per-guild lock.
- `shutdown()` is idempotent — calling twice produces no error and no double-cleanup.
- After `shutdown()`, `register_thread()` on a new thread creates a fresh guild entry (lazy re-init).
- `_get_guild_lock()` may exhibit a benign lazy-init TOCTOU race for never-before-seen `guild_id`s; the race cannot cause data corruption, deadlock, or lost synchronization, and is documented as accepted.

## Adapter `stop()` Idempotency & Resource Release Contract

The adapter's `stop()` method MUST be idempotent and MUST release all owned resources even if individual cleanup steps fail. This is the contract between the adapter lifecycle and the framework's source-registry shutdown.

**`stop()` resource release order:**

```python
async def stop(self) -> None:
    """Idempotent shutdown. Safe to call multiple times."""
    # 1. Idempotency guard
    if self._status == SourceStatus.STOPPED:
        return

    # 2. Cancel TTL eviction task (best-effort). The task is created
    #    during start() AFTER Gateway is confirmed — see Task 8 and the
    #    TTL Eviction Task Lifecycle Contract below. If `self._ttl_task`
    #    exists, cancel it and await completion before proceeding to the
    #    rest of stop(). The `is not None` guard covers two cases:
    #    (a) start() never reached the RUNNING transition (e.g., failed
    #    Gateway connect, raised before _ready_event) — task is None;
    #    (b) stop() called twice — second call sees `self._ttl_task is None`
    #    because step 2 cleared it on the first call.
    if self._ttl_task is not None:
        self._ttl_task.cancel()
        try:
            await asyncio.gather(self._ttl_task, return_exceptions=True)
        except Exception as exc:
            logger.warning("ttl_task_cancel_error", extra={"error": str(exc)})
        self._ttl_task = None

    # 3. Release semaphore — pending waiters are unblocked because
    #    the client task is cancelled in step 4, which cascades.
    #    asyncio.Semaphore has no explicit close; pending acquires
    #    propagate CancelledError when their awaiting task is cancelled.

    # 4. Stop the discord.py client (cancels its background task)
    if self._client is not None:
        try:
            await self._client.close()
        except Exception as exc:
            logger.warning("client_close_error", extra={"error": str(exc)})

    # 5. Clear per-channel locks. Held locks release when their
    #    owning task completes or is cancelled — clear is safe.
    self._channel_locks.clear()

    # 6. ThreadManager shutdown — wrapped in try/except so a failure
    #    here does NOT block adapter stop().
    if self._thread_manager is not None:
        try:
            await self._thread_manager.shutdown()
        except Exception as exc:
            logger.error(
                "thread_manager_shutdown_failed",
                extra={"error": str(exc)},
            )

    # 7. Final state — only reached once, even if upper steps raised
    self._status = SourceStatus.STOPPED
```

**Acceptance criteria:**
- `stop()` called twice produces no error and no double-cleanup (idempotent via status guard).
- TTL eviction task is cancelled and awaited — no leaked asyncio task.
- `ThreadManager.shutdown()` failure does NOT block adapter `stop()` (wrapped in try/except with logging).
- Per-channel locks are cleared at the end so the adapter can be re-`start()`-ed fresh.
- Status transitions to `STOPPED` exactly once, even if intermediate steps raise.

## TTL Eviction Task Lifecycle Contract

The adapter owns a long-running periodic task (`self._ttl_task`) that wakes every `EVICTION_INTERVAL_SECONDS` and calls `self._thread_manager.evict_expired()`. The task MUST be created during `start()` AFTER Gateway confirmation, MUST exit cleanly on cancellation, and MUST be torn down by `stop()` before the adapter reaches `STOPPED`. This is the missing counterpart to the `_ttl_task` reference in the `stop()` contract above — fixing the orphaned reference is the explicit purpose of Phase 3 Task 8.

**Module-level constant:**

```python
# daemon/sources/adapters/discord/adapter.py
EVICTION_INTERVAL_SECONDS: int = 3600  # 1 hour; tunable via SourceConfig.config["eviction_interval_seconds"]
```

**`_periodic_eviction_loop()` method specification:**

```python
async def _periodic_eviction_loop(self) -> None:
    """Periodic eviction driver. Calls thread_manager.evict_expired()
    every EVICTION_INTERVAL_SECONDS. Exits cleanly on CancelledError.
    """
    try:
        while True:
            await asyncio.sleep(EVICTION_INTERVAL_SECONDS)
            if self._thread_manager is None:
                # No thread manager wired — nothing to evict. Loop is a no-op
                # but stays alive so stop() has a task to cancel.
                continue
            try:
                await self._thread_manager.evict_expired()
            except Exception as exc:
                # Eviction failures are NEVER fatal to the loop. Log and
                # continue — the next tick will retry. A persistent failure
                # surfaces via log volume, not by killing the eviction
                # driver (which would silently leak TTL-expired threads).
                logger.error(
                    "ttl_eviction_failed",
                    extra={"error": str(exc)},
                    exc_info=True,
                )
    except asyncio.CancelledError:
        # Cancellation arrives from stop() — exit cleanly without re-raising.
        # The task is considered done; stop() awaits this task via
        # asyncio.gather(return_exceptions=True).
        return
```

**`start()` integration — create the task AFTER Gateway confirmation:**

```python
async def start(self) -> None:
    # ... Phase 1 Task 4 logic: configure intents, build client,
    # create _client_task, await _ready_event with 30s timeout,
    # transition STARTING → RUNNING ...
    self._status = SourceStatus.RUNNING

    # Phase 3 Task 8: schedule TTL eviction task AFTER RUNNING is set.
    # Schedule BEFORE _ready_event.set() is unnecessary — the loop sleeps
    # for EVICTION_INTERVAL_SECONDS on its first iteration anyway, so the
    # exact moment of creation within start() does not affect correctness.
    self._ttl_task = asyncio.create_task(self._periodic_eviction_loop())
```

**`stop()` integration — already in place via the contract above:**

The `stop()` method cancels and awaits `self._ttl_task` in step 2 (see the updated comment block in the **Adapter `stop()` Idempotency & Resource Release Contract** section). With Task 8 wiring creation in `start()`, the `is not None` guard correctly handles three lifecycle scenarios:

| Scenario | `self._ttl_task` at `stop()` time | Behavior |
|----------|-----------------------------------|----------|
| `start()` raised before RUNNING transition (e.g., Gateway timeout) | `None` (never created) | Guard skips; no-op |
| `start()` succeeded → RUNNING → `_ttl_task` created → `stop()` called normally | Live task | Cancel + `await asyncio.gather(...)` + set to `None` |
| `stop()` called twice | `None` (cleared by first call) | Guard skips; no-op on second call |

**Acceptance criteria:**
- `EVICTION_INTERVAL_SECONDS = 3600` is defined as a module-level constant in `adapter.py`; overridable per-source via `SourceConfig.config["eviction_interval_seconds"]`.
- `start()` creates `self._ttl_task` via `asyncio.create_task(self._periodic_eviction_loop())` AFTER the RUNNING transition. Creation is the last statement of `start()` (or lives in a `_post_connect()` hook called at the end of `start()`).
- `_periodic_eviction_loop()` runs `while True` with `await asyncio.sleep(EVICTION_INTERVAL_SECONDS)` then `await self._thread_manager.evict_expired()`.
- `_periodic_eviction_loop()` exits cleanly on `asyncio.CancelledError` — the `except` block swallows the cancellation and returns without re-raising.
- `_periodic_eviction_loop()` catches and logs exceptions from `evict_expired()` (transient eviction failures must not kill the loop driver).
- `stop()` cancels and awaits `_ttl_task` via `asyncio.gather(self._ttl_task, return_exceptions=True)` and sets the field to `None`.
- The `is not None` guard in `stop()` makes the cleanup safe under all three lifecycle scenarios in the table above.
- No asyncio task leak: after `stop()` returns, `_ttl_task` is `None` and the underlying task is done.

## Coupling

- **Tight with:** Phase 2 — `send()` path from Phase 2 is extended with circuit breaker, rate limiter, and locks
- **Loose with:** Phase 1 — lifecycle hooks (`stop()`) extended to call `thread_manager.shutdown()`
- **Tight with:** Phase 4 — all resilience behaviors are tested

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Semaphore deadlock if release fails on exception | High | Use `try/finally` or async context manager (`__aenter__/__aexit__`) to guarantee release |
| Circuit breaker opens too aggressively during discord.py 429 handling | Medium | Only record failures on actual transport errors (ConnectionError, 5xx), NOT on 429 (which discord.py handles via Retry-After wait). Document the distinction. |
| Thread manager evicts active conversation thread | Medium | TTL default 24h (generous); LRU touches `last_accessed` on every access; configurable per-source |
| Archived thread send fails silently | Medium | `mark_archived()` logs warning; policy routes to parent channel; caller knows fallback occurred |
| MAX_CHANNEL_LOCKS=1000 too low for large servers | Low | Configurable via `SourceConfig.config["max_channel_locks"]`; default 1000 matches NFR-9 and Telegram's MAX_CHAT_LOCKS=1000 |
| TTL eviction task leaks if `start()` raises before RUNNING transition | Medium | Guard `_ttl_task` creation behind the RUNNING transition in `start()` (Task 8); `stop()`'s `is not None` check skips cleanup when the task was never created |
| `_periodic_eviction_loop()` dies on transient `evict_expired()` exception, silently leaking TTL-expired threads | Medium | Catch and log exceptions inside the loop (Task 8 / TTL Eviction Task Lifecycle Contract); the loop survives transient failures and retries on the next tick |

## Exit Criterion

- `send()` checks circuit breaker BEFORE acquiring locks/tokens (prevents waste)
- Concurrent sends are bounded by `DiscordRateLimiter` semaphore
- Per-channel ordering is preserved via LRU locks with `MAX_CHANNEL_LOCKS=1000`
- Eviction under capacity pressure MUST skip held locks — never evict a lock that's currently in use
- Circuit breaker opens after 5 consecutive **transport/5xx/timeout** failures and blocks sends for 60s; 429 responses do NOT increment the failure count
- `DiscordThreadManager` registers thread instances on first thread message
- Expired threads (TTL > 24h) are evicted and instances terminated
- Archived thread sends route to parent channel with warning
- `health_check()` includes Gateway latency check (< 5000ms = healthy)
- `stop()` is idempotent — calling twice produces no error or double-cleanup
- `ThreadManager.shutdown()` terminates all tracked instances and continues past individual failures
- `_rate_limit_waits` (not `_rejected_count`) tracks semaphore waits
- `_ttl_task` is created during `start()` AFTER the RUNNING transition, and runs `_periodic_eviction_loop()` on a configurable `EVICTION_INTERVAL_SECONDS` (default 3600) interval
- `_periodic_eviction_loop()` exits cleanly on `asyncio.CancelledError` (no re-raise) and survives transient `evict_expired()` failures (logged, retried on next tick)
- `stop()` cancels and awaits `_ttl_task` via `asyncio.gather(..., return_exceptions=True)` and sets the field to `None` — no asyncio task leak across stop()/restart cycles
- The `is not None` guard in `stop()` correctly handles all three lifecycle scenarios: start-raised-before-RUNNING, normal stop, double-stop
