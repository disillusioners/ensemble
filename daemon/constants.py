"""Named constants for the agents-ensemble daemon.

All magic numbers in the codebase are consolidated here for discoverability
and maintainability. Constants are organized by category.
"""

# ── Projects ────────────────────────────────────────────────────────────────────

# ── API Limits ──────────────────────────────────────────────────────────────────
DEFAULT_PAGE_LIMIT: int = 20  # Default pagination limit for instance/message listing
DEFAULT_JOB_LIST_LIMIT: int = 50  # Default limit for job listing
DEFAULT_SCHEDULE_EXECUTIONS_LIMIT: int = 100  # Default limit for schedule execution history
MAX_PAGE_LIMIT: int = 100  # Maximum allowed pagination limit
MAX_JOB_LIST_LIMIT: int = 100  # Maximum job list limit
MAX_SCHEDULE_EXECUTION_LIMIT: int = 1000  # Maximum schedule execution history limit
MAX_INSTANCES: int = 100  # Max concurrent agent instances
MAX_CHILDREN_PER_INSTANCE: int = 10  # Max child instances per parent
MAX_CREDENTIALS_SIZE: int = 4096  # Max bytes for credentials JSON
MAX_ERROR_LEN: int = 500  # Max length for error messages (prevents HTML flooding)
MAX_CHAT_LOCKS: int = 1000  # LRU eviction limit for per-chat locks in Telegram adapter

# ── SSE & Streaming ───────────────────────────────────────────────────────────────
SSE_TIMEOUT_S: int = 30  # SSE event timeout (seconds)
SSE_PING_INTERVAL: int = 30  # SSE keepalive ping interval (seconds)
SSE_QUEUE_MAXSIZE: int = 50  # Max size for SSE event queue
EVENT_STREAM_POLL_INTERVAL: int = 2  # Job SSE poll interval (seconds)

# ── Timeouts (seconds) ───────────────────────────────────────────────────────────
REQUEST_TIMEOUT_S: int = 610  # LLM request timeout (11 minutes)
INSTANCE_TIMEOUT_S: int = 60  # Instance timeout (minutes converted to seconds)
GRAPH_TIMEOUT_S: int = 300  # MainLoopBridge default timeout (5 minutes)
TASK_TIMEOUT_S: int = 300  # Default task timeout (5 minutes)
SHUTDOWN_TIMEOUT_S: int = 300  # Graceful shutdown timeout
CIRCUIT_BREAKER_RECOVERY_S: int = 60  # Circuit breaker recovery timeout
SSE_LOCK_RELEASE_TIMEOUT: int = 5  # Timeout for releasing locks from sync context

# ── Retry & Backoff ─────────────────────────────────────────────────────────────
DEFAULT_RETRY_COUNT: int = 3  # Default max retry attempts
MAX_RETRY_COUNT: int = 3  # Max task retries (from config)
LLM_TRANSIENT_RETRIES: int = 10  # LLM transient error retry attempts
LLM_TIMEOUT_RETRIES: int = 3  # LLM timeout error retry attempts
BACKOFF_BASE_S: int = 60  # Exponential backoff base (seconds)
BACKOFF_MAX_S: int = 3600  # Exponential backoff max (seconds)
BACKOFF_MULTIPLIER: float = 2.0  # Exponential backoff multiplier
CIRCUIT_BREAKER_THRESHOLD: int = 5  # Failure threshold before circuit opens

# ── Worker Pool ──────────────────────────────────────────────────────────────────
WORKER_POOL_SIZE: int = 4  # Default number of worker threads
WORKER_WAIT_TIMEOUT: float = 3.0  # Worker wait timeout (seconds)
WORKER_STALE_CHECK_INTERVAL: int = 60  # Stale task recovery check interval (seconds)
STALE_TASK_CANCEL_GRACE_S: int = 10  # Grace period before cancelling stale tasks
ACTIVITY_UPDATE_INTERVAL: float = 5.0  # Activity callback update interval (seconds)

# ── Rate Limits (messages_per_second, burst_size) ─────────────────────────────────
TELEGRAM_RATE_LIMIT: tuple[int, int] = (30, 30)  # Telegram: 30 msg/sec
WEBHOOK_RATE_LIMIT: tuple[int, int] = (100, 100)  # Webhook: 100 req/sec
WHATSAPP_RATE_LIMIT: tuple[int, int] = (10, 20)  # WhatsApp: 10 msg/sec burst 20

# ── Database ─────────────────────────────────────────────────────────────────────
DB_POOL_SIZE: int = 5  # Database connection pool size
DB_MAX_OVERFLOW: int = 10  # Database connection pool max overflow
DB_BUSY_TIMEOUT_S: int = 30  # SQLite busy timeout
CHECKPOINT_INTERVAL: int = 1  # Checkpoint interval (messages)
CHECKPOINT_TTL_HOURS: int = 168  # Checkpoint TTL (7 days)
CHECKPOINT_CLEANUP_INTERVAL_HOURS: int = 24  # Checkpoint cleanup interval
CHECKPOINT_MAX_PER_THREAD: int = 50  # Max checkpoints per thread (preserves parent chain)
MAX_INSTANCE_HISTORY: int = 300  # Max terminal instances to keep checkpoint data for
MAINTENANCE_CHECK_INTERVAL_MINUTES: int = 15  # Maintenance service check interval
IDEMPOTENCY_KEY_TTL_HOURS: int = 24  # Idempotency key deduplication TTL

# ── Graph & LLM ──────────────────────────────────────────────────────────────────
GRAPH_RECURSION_LIMIT: int = 100  # LangGraph recursion limit
LLM_CONCURRENCY: int = 10  # Max concurrent LLM calls
RECENT_WINDOW_SIZE: int = 10  # Recent message window for compaction
MIN_RECENT_WINDOW: int = 3  # Minimum recent window size

# ── Compaction ───────────────────────────────────────────────────────────────────
COMPACTION_THRESHOLD: float = 0.80  # Trigger compaction at 80% context
COMPACTION_TARGET_RATIO: float = 0.40  # Target 40% context after compaction
MIN_MESSAGES_BEFORE_COMPACTION: int = 10  # Minimum messages before compaction
SUMMARIZATION_CHUNK_THRESHOLD: float = 0.60  # Chunk threshold for summarization

# ── Health & Monitoring ───────────────────────────────────────────────────────────
OBSERVER_HEALTH_CHECK_INTERVAL_S: int = 300  # Observer health check interval (5 min)

# ── System Default Project ───────────────────────────────────────────────────────
SYSTEM_DEFAULT_PROJECT_NAME = "__system_default__"
SYSTEM_DEFAULT_PROJECT_ID: str | None = None  # Set at startup by ensure_system_default_project()

# ============================================================
# Scheduler
# ============================================================
# Tradeoff: wider window for skipped triggers when max_concurrent is reached.
# Monitor "skipped" callbacks to tune this value.
SCHEDULER_SEMAPHORE_TIMEOUT_S = 1.0        # Raised from 0.1s for reliability.
SCHEDULER_MANUAL_SEMAPHORE_TIMEOUT_S = 10.0  # Manual trigger semaphore timeout
SCHEDULER_GRACE_PERIOD_S = 30.0             # Grace period for running executions on stop
SCHEDULER_ERROR_RETRY_S = 5.0               # Brief pause before retry on errors
SCHEDULER_DRAIN_CHECK_S = 0.5               # Polling interval for drain check
SCHEDULER_DEFAULT_MAX_CONCURRENT = 1         # Default max concurrent executions
SCHEDULER_DEFAULT_PRIORITY = 5              # Default execution priority
