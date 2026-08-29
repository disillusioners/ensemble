"""Named constants for the agents-ensemble daemon.

All magic numbers in the codebase are consolidated here for discoverability
and maintainability. Constants are organized by category.

Leaf-module invariant: this module imports NOTHING. Constants are raw
Python literals (strings, ints, floats, frozensets, dicts) so consumers
can ``from daemon.constants import …`` without pulling in a dependency
chain. If you find yourself reaching for an import here, hoist the value
into a non-constants module and reference it from there.
"""

# ── Projects ────────────────────────────────────────────────────────────────────

# ── API Limits ──────────────────────────────────────────────────────────────────
DEFAULT_PAGE_LIMIT: int = 10  # Default pagination limit for instance/message listing
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
SHUTDOWN_TIMEOUT_S: int = 300  # Graceful shutdown internal ceiling (superseded by DaemonConfig.graceful_shutdown_timeout_seconds, which uvicorn enforces)
BOOT_DB_TIMEOUT_S: int = 10  # Boot preflight budget for the PostgreSQL SELECT 1 connectivity probe (exit-75 path)
CIRCUIT_BREAKER_RECOVERY_S: int = 60  # Circuit breaker recovery timeout
SSE_LOCK_RELEASE_TIMEOUT: int = 5  # Timeout for releasing locks from sync context
GIT_TIMEOUT_S: int = 10  # Git operation timeout (seconds) — used by workspace git diff tracking

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
MAX_INSTANCE_HISTORY: int = 500  # Max terminal instances to keep checkpoint data for
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

# ── Per-Project Blueprint Opt-In ────────────────────────────────────────────────
# Two-tier model: ``auto_rebuild_enabled`` (config.yaml) gates the system-wide
# feature; ``BLUEPRINT_ACTIVE_METADATA_KEY`` (``project_metadata_records``) is
# the per-project opt-in defaulting to False — a project must explicitly enable
# the blueprint system. Both gates must be true for any automated activity.
BLUEPRINT_ACTIVE_METADATA_KEY = "blueprint_active"

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

# ---------------------------------------------------------------------------
# VS Code Server
# ---------------------------------------------------------------------------
VSCODE_STARTUP_TIMEOUT_S: int = 30            # Max seconds to wait for code-server port detection
VSCODE_HEALTH_CHECK_INTERVAL_S: int = 2       # How often to poll health endpoint
VSCODE_LOG_BUFFER_LIMIT: int = 4 * 1024 * 1024  # 4 MB in-memory log buffer (mirror proc_tools.py)
VSCODE_STOP_GRACE_S: int = 5                  # SIGTERM grace period before SIGKILL escalation
VSCODE_DEFAULT_USER_DATA_DIR: str = "vscode-user-data"  # Subdir under data/ if not configured
VSCODE_PID_FILENAME: str = "vscode-server.pid"  # PID file for crash recovery
VSCODE_PORT_DETECTION_POLL_S: float = 0.2       # How often to poll stdout for port line
VSCODE_HEALTH_TIMEOUT_S: int = 10            # Max seconds for a single health check HTTP request

# ---------------------------------------------------------------------------
# Editor Preference (VS Code Server)
# ---------------------------------------------------------------------------
EDITOR_METADATA_KEY = "editor_preference"     # metadata key in project_metadata_records
EDITOR_DEFAULT = "builtin"                     # default when no preference set
EDITOR_OPTIONS = ["builtin", "vscode"]         # valid editor values

# ---------------------------------------------------------------------------
# Default Agent Versions
# ---------------------------------------------------------------------------
DEFAULT_AGENT_VERSIONS_METADATA_KEY = "default_agent_versions"  # metadata key in project_metadata_records

# ---------------------------------------------------------------------------
# Plane Project Sync
# ---------------------------------------------------------------------------
# Metadata keys for the Plane project sync subsystem. Each Ensemble project
# that has been mirrored to Plane stores:
#   - plane_project_id: Plane's internal UUID — the primary mapping handle
#   - plane_sync_state: "synced" | "error" | "pending"
#   - plane_synced_at:   ISO8601 timestamp of the most recent sync attempt
PLANE_PROJECT_ID_METADATA_KEY = "plane_project_id"
PLANE_SYNC_STATE_METADATA_KEY = "plane_sync_state"
PLANE_SYNCED_AT_METADATA_KEY = "plane_synced_at"

# Per-project cooldown (seconds) for the ``plane_sync_project`` agent tool.
# Prevents a tight LLM loop from hammering the Plane API. ``force=True``
# bypasses this gate.
PLANE_SYNC_COOLDOWN_S: float = 30.0

# Mapping from Ensemble ``ProjectStatus`` → Plane project state. Best-effort
# — Plane's state vocabulary differs from ours and we default to "active"
# for unknown values.
PLANE_STATUS_MAP: dict[str, str] = {
    "active": "active",
    "paused": "hold",
    "archived": "cancelled",
    "completed": "completed",
}

# ── Pause-report-recovery Phase 1 (DEFERRED marker reasons) ────────────────────
# Storage-layer contract (C1): the literal values below MUST match the
# storage enum / DDL predicate literals verbatim (UPPERCASE). The
# ``report_injections`` partial unique index
# (``uq_report_injections_oblig_triple``) uses the
# ``state IN ('PENDING','DEFERRED')`` predicate — the
# ``DEFERRED_REASON_*`` values below are the only values written to
# ``report_injections.deferred_reason`` by the pause drop-site writers
# (Site 1 — message_processing_pipeline.py:472-, Variant B live site —
# child_reports.py:2106-, Variant B idempotency guard —
# child_reports.py:1626-). Any new reason value is added to BOTH this
# list AND any DDL / docs in the same change.
DEFERRED_REASON_PAUSE_TOCTOU: str = "PAUSE_TOCTOU"
DEFERRED_REASON_PENDING_MESSAGES: str = "PENDING_MESSAGES"
DEFERRED_REASON_IDEMPOTENCY_SKIP: str = "IDEMPOTENCY_SKIP"
DEFERRED_REASON_RESUME_ROUTER: str = "RESUME_ROUTER"

# ── Injection routing (agent-instance-tools Phase 1) ────────────────────────────
# Phase 1 (agent-instance-tools) hoists the eligibility set that governs
# ``set_injection`` routing to ONE named constant. Previously the value
# was forked in two places with subtly different forms:
#   * ``daemon/routers/messages.py:39-42`` — a local frozenset (named).
#   * ``daemon/tools/job_queue.py:1787-1790`` — an INLINE TUPLE in
#     ``job_inject``'s status gate (NOT a named constant).
# The fork was a delta-fix target: any new caller (the agent-tool layer,
# or a future ``graph.py`` injection source) would have risked minting a
# THIRD copy. The hoist to ``daemon.constants`` (LOCKED choice — no
# Manager-attr alternative per delta-fix #4) eliminates the hazard.
#
# Semantics:
#   * ``RUNNING`` — the agent is in an active LLM turn; ``set_injection``
#     will be drained by the next ``agent_node`` pass.
#   * ``WAITING_CHILDREN`` — the parent is parked waiting for child
#     completion reports; the injection sits in the FIFO until the next
#     dispatch (typically a child report waking the instance). The drain
#     at ``daemon/graph.py:2871`` runs BEFORE the report injection at
#     ``:3021``, so user/agent FIFO entries land BEFORE child reports
#     in the same wake-up turn (W5 ordering — documented in
#     ``send_message``'s docstring + ``_full_doc_``).
#
# Consumers (must all import from here; do NOT introduce a third fork):
#   1. ``daemon/routers/messages.py`` (HTTP ``POST /messages``)
#   2. ``daemon/tools/job_queue.py`` (``job_inject`` tool)
#   3. ``daemon/tools/instance.py`` (agent-tool ``send_message``)
#
# Test invariant (tests/unit/tools/test_instance_tools.py::test_k_…):
#   ``grep -n "_INJECTION_ELIGIBLE_STATUSES\s*=\s*{" daemon/`` must
#   return exactly ONE hit — this module. The router's local frozenset
#   and ``job_inject``'s inline tuple are GONE.
INJECTION_ELIGIBLE_STATUSES: frozenset[str] = frozenset({
    "running",
    "waiting_children",
})

# Terminal instance statuses — companion to ``INJECTION_ELIGIBLE_STATUSES``
# above. The four instance statuses that ``send_message``'s routing helper
# (``daemon/tools/instance.py::_route_send_message``) maps to the
# terminal-revive branch: ``enqueue_message`` dispatches via the shared
# ``_prepare_enqueued_message`` path, which reactivates the instance.
# Previously this set lived as a module-local ``_TERMINAL_STATUSES``
# frozenset in ``daemon/tools/instance.py``; it is hoisted here for the
# same fork-prevention reason as ``INJECTION_ELIGIBLE_STATUSES`` — one
# canonical home for routing-relevant status sets, so future consumers
# (routers, tools, lifecycle) import instead of re-declaring.
#
# Values mirror ``InstanceStatus`` (daemon/repositories/instance/models.py):
# COMPLETED, TERMINATED, ERROR, FAILED. Naming convention: inline docstrings
# above status-set constants list the enum NAMES in UPPERCASE for readability
# (matching the enum definition), while the constant VALUES are lowercase
# strings — the runtime vocabulary ``send_message``'s routing helper
# compares against. Kept as raw strings so ``daemon.constants`` stays
# dependency-free.
TERMINAL_INSTANCE_STATUSES: frozenset[str] = frozenset({
    "completed",
    "terminated",
    "error",
    "failed",
})

# Alive instance statuses — companion to ``TERMINAL_INSTANCE_STATUSES``
# above. The five instance statuses that gate liveness checks across the
# reconciler/drift-cancel code path:
#   * ``daemon/manager.py::_is_parent_alive`` (parent-status guard for
#     cascade resume + sub-shape (c) carrier-revival).
#   * ``daemon/services/job_recovery_service.py::_is_instance_alive``
#     (used by drift sweep + ``reconcile_drift_states`` Pattern d Fix 2
#     — the alive-instance guard that prevents the wedge-fix class from
#     re-opening).
#
# Previously this set had TWO homes (fork hazard — review minor (a) from
# the wedge-fix batch):
#   * ``daemon/services/job_recovery_service.py:52-58`` — local
#     ``_ALIVE_INSTANCE_STATUSES`` set (``InstanceStatus`` enum refs).
#   * ``daemon/manager.py:_is_parent_alive`` — inline literal set
#     (same five members, hard-coded).
# Two copies on a set that gates drift-cancels is a silent-divergence
# risk: if a future status is added to one copy and not the other, the
# safety net silently de-syncs and the wedge class re-opens. This
# constant is the single definition; both consumers import it.
#
# Values mirror ``InstanceStatus`` (daemon/repositories/instance/models.py):
# IDLE, RUNNING, PAUSED, QUEUED, WAITING_CHILDREN. Naming convention:
# inline docstrings above status-set constants list the enum NAMES in
# UPPERCASE for readability (matching the enum definition), while the
# constant VALUES are lowercase strings — the runtime vocabulary both
# consumers compare against. Kept as raw strings so ``daemon.constants``
# stays dependency-free.
#
# Test invariant (tests/unit/test_reconciler_wedge_fix.py::T2b — the
# alive-instance guard pinning test): the five members above are
# byte-identical to the pre-hoist local definition at
# ``daemon/services/job_recovery_service.py:52-58``. Any new member
# must be added here AND in any DDL / docs in the same change.
ALIVE_INSTANCE_STATUSES: frozenset[str] = frozenset({
    "idle",
    "running",
    "paused",
    "queued",
    "waiting_children",
})
