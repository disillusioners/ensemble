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
MAX_CREDENTIALS_SIZE: int = 4096  # Max bytes for credentials JSON
MAX_ERROR_LEN: int = 500  # Max length for error messages (prevents HTML flooding)
MAX_CHAT_LOCKS: int = 1000  # LRU eviction limit for per-chat locks in Telegram adapter

# ── SSE & Streaming ───────────────────────────────────────────────────────────────
SSE_TIMEOUT_S: int = 30  # SSE event timeout (seconds)
SSE_PING_INTERVAL: int = 30  # SSE keepalive ping interval (seconds)
SSE_QUEUE_MAXSIZE: int = 50  # Max size for SSE event queue
EVENT_STREAM_POLL_INTERVAL: int = 2  # Job SSE poll interval (seconds)

# ── Mid-flight QA channel (2026-09-21, feature/midflight-qa-channel) ─────────────
# Single source of truth for the wedge-guard one-shot chain timing.
# Each link of the chain is a single future-dated Task row
# (``next_retry_at``) — NOT a periodic sweep. The chain is finite
# (3 emissions): #1 at pause time (t=0), #2 at t=+1800s, #3 at
# t=+3600s; at emission_index=3 the processor escalates (terminate +
# NotificationBroadcaster fan-out) and mints NO successor. Total
# wedge-detection window: ~60 minutes.
STUCK_HEARTBEAT_AFTER_SECONDS: int = 1800

# Wedge-guard escalation threshold: the processor terminates the asker
# and fans out to NotificationBroadcaster when the derived
# ``emission_index`` reaches this value. No successor one-shot is
# minted at or above this index.
STUCK_HEARTBEAT_ESCALATION_INDEX: int = 3

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
WORKER_POOL_SIZE: int = 5  # Default number of worker threads
WORKER_WAIT_TIMEOUT: float = 3.0  # Worker wait timeout (seconds)
WORKER_STALE_CHECK_INTERVAL: int = 60  # Stale task recovery check interval (seconds)
STALE_TASK_CANCEL_GRACE_S: int = 10  # Grace period before cancelling stale tasks
ACTIVITY_UPDATE_INTERVAL: float = 5.0  # Activity callback update interval (seconds)

# Chat-source worker lane (chat-source-worker-lane, D7 — hardcoded, NO
# ENSEMBLE_* flags; activation = rebuild+restart, same as WORKER_POOL_SIZE).
CHAT_WORKER_POOL_SIZE: int = 2  # Dedicated chat-lane worker threads (telegram/slack/discord)

# ── LCA unverified-completion surface (7d4a3bd9 fix, Fix 1) ─────────────────
#
# DISTINCT user-facing status string rendered INSTEAD of plain
# ``completed`` on every read surface (job events, job_get, get_mission,
# FE label) when the linked instance carries
# ``completion_gate_escalated=True`` — i.e. the attestation gate ended
# the mission via ``terminal_after_bound`` WITHOUT an attested
# completion. Incident 7d4a3bd9 Episode B: the escalated completion was
# INVISIBLE at the user surface (plain ``completed``); this string makes
# the unverified shape loud. Single source of truth — the FE derives its
# badge from the same literal shape via suffix match.
COMPLETION_GATE_ESCALATED_DISPLAY: str = "completed (gate escalated — unverified)"

# Interactive-chat source prefixes (chat-source-worker-lane, D1/D10.1).
#
# Provenance (Pin 1 of the D10.1 5-pin pattern): the LEGITIMATE mint
# site for every value starting with one of these prefixes is
# ``daemon/sources/registry.py:857`` —
# ``source = f"{source_id}:{msg.external_user_id}"`` — which formats
# the adapter's registered ``source_id`` (from ``SourceCreate``) plus
# the external user id. Minting a ``telegram:`` / ``slack:`` /
# ``discord:``-prefixed source via ANY other path is a bug.
#
# Match semantics: PREFIX match (``startswith``), NOT exact-match —
# mirrors the mint shape ``telegram:<user>`` so concrete rows
# (e.g. ``telegram:alice:1``) are caught. Case-SENSITIVE — see the
# ``is_chat_source`` docstring below.
#
# Interactive-chat prefixes ONLY. ``webhook:`` / ``whatsapp:`` are
# explicitly EXCLUDED from this lane (deliberate scope decision:
# webhook ≈ CI/automation, not interactive chat) — see
# ``USER_ORIGIN_CHAT_SOURCE_TYPES`` in ``daemon/tools/upgrade_journal.py``
# for the broader FOUR-member (registry source_type) user-origin gate set;
# this tuple is the THREE-member interactive-chat lane subset (prefix-matched
# ids, a different mechanism from the registry classification). The
# asymmetry is pinned by
# ``tests/unit/routers/test_source_reservation.py::
# TestChatSourcePrefixesConstant`` and the ``is_chat_source`` helper
# pins (``webhook:gh-hook`` / ``whatsapp:1234`` → False).
CHAT_SOURCE_PREFIXES: tuple[str, ...] = ("telegram:", "slack:", "discord:")

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

# ── Checkpoint Blob Prune (Phase 1 C3 — reference-aware checkpoint_blobs prune) ───
# Conservative ladder: the maintenance blob prune starts DRY-RUN ONLY (reports
# what would be deleted, deletes nothing). Destructive execution requires BOTH
# env flags set together — CHECKPOINT_BLOB_PRUNE_DRY_RUN=0 AND
# CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1 — and even then the destructive code
# path is structurally unreachable unless that conjunction holds at call time
# (daemon/services/checkpoint_prune.py::blob_prune_destructive_enabled).
CHECKPOINT_BLOB_PRUNE_DRY_RUN: bool = True  # default dry-run; set false only via env (CHECKPOINT_BLOB_PRUNE_DRY_RUN=0)
CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE: bool = False  # destructive kill-switch; env-overridden (CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1); default OFF
CHECKPOINT_BLOB_PRUNE_MAX_REFS_PER_THREAD: int = 100_000  # safety cap — skip pairs with more refs than this
# Max RETRY attempts for the destructive anti-join DELETE after a PostgreSQL
# serialization failure (SQLSTATE 40001) or deadlock (SQLSTATE 40P01); total
# attempts = 1 + this. Exhaustion logs ERROR and skips the pair with zero rows
# deleted (never raises into the maintenance loop). See
# daemon/checkpoint_adapter.py::PostgresCheckpointerAdapter.delete_blobs_anti_join.
CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES: int = 3

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
# Agent Snapshot — R15 settings toggle (write side ONLY)
# ---------------------------------------------------------------------------
# Stored under the SYSTEM_DEFAULT_PROJECT metadata record (mirrors the
# EDITOR_METADATA_KEY shape). The ``is_snapshot_create_enabled`` seam in
# ``daemon/tools/snapshot_tools.py`` reads it; absent/missing → OFF
# (fail-closed opt-in rollout). The toggle gates ONLY ``snapshot_create``;
# ``snapshot_search`` (read) and ``spawn_hot_instance`` (consumption) are
# always-on (R15 rider (i) isolation).
SNAPSHOT_CREATE_METADATA_KEY = "snapshot_create_enabled"
# Tracked string values stored in the metadata record. Anything not in this
# set is treated as OFF (defense-in-depth — corrupt or legacy values fail
# closed).
SNAPSHOT_CREATE_ENABLED_VALUES = frozenset({"on", "true", "1", "yes"})

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
#   - plane_sync_state: "linked" | "syncing" | "drift" | "error"
#                       ("synced" kept as a back-compat alias for "linked"
#                        — pre-Phase-3 writers wrote "synced"; new code
#                        writes "linked". See PLANE_SYNC_STATES below.)
#   - plane_synced_at:   ISO8601 timestamp of the most recent SUCCESSFUL sync
#   - plane_last_attempt: ISO8601 timestamp of the most recent attempt
#                         (success OR failure) — backing the watchdog's
#                         "next eligible retry" math.
#   - plane_attempt_count: int — consecutive-failure counter that backs
#                               the per-project exponential backoff and
#                               the watchdog's max-attempts circuit.
#   - plane_last_error: short string — last error reason (advisory; used
#                       by the drift-classifier for diagnostics).
PLANE_PROJECT_ID_METADATA_KEY = "plane_project_id"
PLANE_SYNC_STATE_METADATA_KEY = "plane_sync_state"
PLANE_SYNCED_AT_METADATA_KEY = "plane_synced_at"
PLANE_LAST_ATTEMPT_METADATA_KEY = "plane_last_attempt"
PLANE_ATTEMPT_COUNT_METADATA_KEY = "plane_attempt_count"
PLANE_LAST_ERROR_METADATA_KEY = "plane_last_error"

# Canonical sync state vocabulary (Phase 3 expansion).
#
# - "linked"  — Plane project is paired with the Ensemble project and the
#               identity fields agree. Steady-state success.
# - "syncing" — a sync is in flight right now. Used as a re-entrancy guard
#               for the manual endpoint and the watchdog sweep.
# - "drift"   — the most recent sync SUCCEEDED at the API level, but
#               identity-field comparison flagged divergence between the
#               Ensemble side (name/description/status) and the Plane
#               side. Next successful corrective sync flips to "linked".
# - "error"   — the most recent attempt failed (API error, auth, network,
#               circuit-open, etc.). Watchdog re-drives with exponential
#               backoff.
#
# "synced" is retained as a back-compat alias that pre-Phase-3 writes
# may carry (the v1 vocabulary). Reads treat "synced" as equivalent to
# "linked"; new writes always use the canonical "linked"/"syncing"/
# "drift"/"error" form. Pre-existing rows do NOT need migration.
PLANE_SYNC_STATE_LINKED = "linked"
PLANE_SYNC_STATE_SYNCING = "syncing"
PLANE_SYNC_STATE_DRIFT = "drift"
PLANE_SYNC_STATE_ERROR = "error"
PLANE_SYNC_STATE_SYNCED_ALIAS = "synced"  # legacy v1 success marker

PLANE_SYNC_STATES: frozenset[str] = frozenset({
    PLANE_SYNC_STATE_LINKED,
    PLANE_SYNC_STATE_SYNCING,
    PLANE_SYNC_STATE_DRIFT,
    PLANE_SYNC_STATE_ERROR,
    PLANE_SYNC_STATE_SYNCED_ALIAS,  # accepted but never written
})

# States eligible for the periodic watchdog to re-drive. "linked" is the
# steady-state success marker and excluded — there is nothing to retry.
# "syncing" is also excluded because a sweep must NEVER overlap an
# in-flight sync (the manual endpoint's 409 idempotent return enforces
# the same rule at the HTTP layer).
PLANE_SYNC_STATES_RETRYABLE: frozenset[str] = frozenset({
    PLANE_SYNC_STATE_ERROR,
    PLANE_SYNC_STATE_DRIFT,
})

# Per-project cooldown (seconds) for the ``plane_sync_project`` agent tool.
# Prevents a tight LLM loop from hammering the Plane API. ``force=True``
# bypasses this gate.
PLANE_SYNC_COOLDOWN_S: float = 30.0

# Phase 3 retry machinery — default knobs. Tuned to be sane for an
# external SaaS API: not so tight that a flap hammers the server, not
# so loose that 21 stuck projects stay stuck for hours. All overrides
# live on ``ServicesConfig`` (see ``daemon/config.py``) and FAIL FAST
# when out-of-range.
#
# PLANE_SYNC_WATCHDOG_INTERVAL_SECONDS:
#   Steady-state cadence of the periodic sweep. 300s = 5min — Plane is
#   a SaaS API, hammering every minute is wasteful. Bounded by
#   ``ServicesConfig.plane_sync_watchdog_interval_seconds`` (ge=5).
PLANE_SYNC_WATCHDOG_INTERVAL_SECONDS: int = 300

# PLANE_SYNC_WATCHDOG_BACKOFF_BASE_SECONDS:
#   Base of the per-project exponential backoff. attempt_count=0/1 → no
#   extra wait; attempt_count=2 → 60s; attempt_count=3 → 120s; ...
#   Formula: min(MAX, BASE * 2 ** (attempt_count - 2)) — capped by
#   PLANE_SYNC_WATCHDOG_BACKOFF_MAX_SECONDS so a long-running outage
#   does not push retry delay into "hours" territory.
PLANE_SYNC_WATCHDOG_BACKOFF_BASE_SECONDS: int = 60
PLANE_SYNC_WATCHDOG_BACKOFF_MAX_SECONDS: int = 1800  # 30min ceiling

# PLANE_SYNC_WATCHDOG_MAX_ATTEMPTS:
#   Consecutive-failure threshold above which the watchdog STOPS
#   re-driving a project (writes a terminal "dead" hint to the logs and
#   to ``plane_last_error`` but does NOT touch ``plane_sync_state`` —
#   that field keeps the operator-visible "error" marker so the manual
#   endpoint can still be used to force a re-sync after the operator
#   has investigated). 5 attempts × 5min cadence ≈ 25min before
#   quarantine; the operator can call POST /api/plane/sync/{id} to
#   reset attempt_count and re-drive.
PLANE_SYNC_WATCHDOG_MAX_ATTEMPTS: int = 5

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
#   4. ``daemon/sources/registry.py`` (chat-source ``_handle_message``
#      — ``feature/chat-source-live-injection``, 2026-09-19; live-turn
#      injection for ``telegram:/slack:/discord:`` ingest mirroring
#      the web branch's status + graph-task guards).
#
# Test invariant (tests/unit/tools/test_instance_tools.py::test_k_…):
#   ``grep -n "_INJECTION_ELIGIBLE_STATUSES\s*=\s*{" daemon/`` must
#   return exactly ONE hit — this module. The router's local frozenset
#   and ``job_inject``'s inline tuple are GONE.
#
# wc-wake-report-integrity (T2, 2026-08-30): ``\"waiting_children\"`` was
# REMOVED from this set. A parked ``WAITING_CHILDREN`` parent has no
# live turn to absorb a mid-turn injection — only ``enqueue_message``
# (durable wake, first-class turn) can wake it. The legacy FIFO
# injection route is preserved behind the ``ENSEMBLE_WC_WAKE_ENQUEUE``
# kill-switch (C1-Q2 RESOLVED 2026-08-30; see
# ``daemon/services/instance_messaging.py::_resolve_wc_wake_enqueue_enabled``)
# via an EXPLICIT ``status == \"waiting_children\" and not <flag>``
# branch at each of the three call sites — the constant stays
# config-free per single-home convention. The transient flag-off
# window accepts the legacy semantics as the documented revert path.
INJECTION_ELIGIBLE_STATUSES: frozenset[str] = frozenset({
    "running",
})

# ── Chat-source routing-envelope allowlist ─────────────────────────────
# Per-provider allowlist of top-level ``IncomingMessage.metadata`` keys
# that a chat adapter is contractually required to populate. Used by
# the chat-source live-injection gate at
# ``daemon/sources/registry.py:_is_text_only_payload_for_chat_injection``
# to decide whether a message can safely take the RAM-FIFO injection
# lane (``manager.set_injection``) or must fall through to the durable
# ``enqueue_message_job`` path.
#
# Iteration 2 (2026-09-19, ``feature/chat-source-live-injection`` fix
# cycle): the prior blocklist (``not msg.metadata``) was dead-code in
# production — every chat adapter always populates non-empty provider
# metadata. A narrow allowlist degrades to durable fallthrough, which
# is the SAFE direction (an unknown key never strands an injection).
#
# Per-provider reply-path verdicts (verified that the agent's outbound
# send path does NOT depend on per-message metadata that only the
# durable path carries):
#
#   * ``slack``  — slack/adapter.py:813-825 (mint); reply path at
#     slack/adapter.py:380-449 routes via ``external_user_id`` format
#     ``{workspace}:{channel_or_user_id}[:{thread_ts}]`` (:401) +
#     mapping-side ``mapping.mapping_metadata.get("slack_thread_ts")``
#     fallback (:439-440). Per-message metadata is NOT required for
#     reply routing. ✓
#   * ``telegram`` — telegram.py:558-573 (mint); reply path at
#     telegram.py:262-326 reads ``message.metadata.get("reply_chat_id")``
#     with ``external_user_id`` fallback (:280) — and ``external_user_id``
#     is always a valid Telegram chat_id (``user_id`` for private chats,
#     ``chat_id`` for groups), so the fallback always routes correctly.
#     Per-message metadata is NOT required. ✓
#   * ``discord`` — discord/adapter.py:1024-1038 / :1170-1196 (mint); reply
#     path at discord/adapter.py:1589-1627 routes via ``_resolve_send_target``
#     (:1363-1437) which reads mapping.metadata (Discord does NOT
#     populate ``reply_chat_id`` in message metadata — channel/thread
#     routing lives on the mapping, set at first-message time via
#     ``extra_mapping_metadata`` in registry.py:820-824). Per-message
#     metadata is NOT required. ✓
#
# Sources (each entry cited from the adapter's metadata construction
# site):
#   * slack      — slack/adapter.py:813-825 (+ /new at :829-830)
#   * discord    — discord/adapter.py:1092-1099 (text) and :1204-1209 (slash)
#   * telegram   — telegram.py:558-573 (+ /new at :579-580)
ROUTING_ENVELOPE_KEYS: dict[str, frozenset[str]] = {
    "slack": frozenset({
        "slack",
        "agent",
        "reply_chat_id",
        "force_new_instance",
        "command",
    }),
    "discord": frozenset({
        "discord",
        "agent",
        "force_new_instance",
        "command",
    }),
    "telegram": frozenset({
        "telegram",
        "agent",
        "reply_chat_id",
        "force_new_instance",
        "command",
    }),
}

# DEFECT A (dispatch-lane stranding fix, feature/fix-question-resume-stuck,
# 2026-09-14): membership in this set is NECESSARY but NOT SUFFICIENT for
# the RAM-FIFO injection lane. Every consumer MUST additionally verify a
# live graph consumer exists (``InstanceManager.has_live_graph_task``)
# before calling ``set_injection`` — a spawn-created child that was
# cascade-paused and cascade-resumed WITHOUT ever being dispatched reads
# ``running`` while having no graph (zero task/message/checkpoint rows),
# and an injection into it is stranded in memory forever (the graph that
# would drain ``_pending_injections`` never runs). Graphless ``running``
# targets route through the durable enqueue pipeline instead. The
# constant itself stays a pure status set (single-home, config-free).

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

# Alive-instance status literal — companion to ``TERMINAL_INSTANCE_STATUSES``
# above. Used by the long-tool-nudge parent-status gate (``deliver_long_tool_nudge``
# in ``daemon/services/long_tool_nudge.py``) and any other caller that needs
# to branch on the PAUSED state without depending on the ``InstanceStatus``
# enum (which would create a ``daemon.constants → daemon.repositories``
# cycle). Mirrors ``InstanceStatus.PAUSED.value`` exactly. Adding a new
# "pause-equivalent" status means adding the literal here AND updating the
# ``InstanceStatus`` enum in the same change.
INSTANCE_STATUS_PAUSED: str = "paused"

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
# Test invariant (``tests/unit/test_reconciler_wedge_fix.py::
# TestAliveInstanceStatusesMembership.test_alive_instance_statuses_membership``
# — the membership pinning test added during the wedge-fix
# post-merge cleanup): the five members above are byte-identical
# to the pre-hoist local definition at
# ``daemon/services/job_recovery_service.py:52-58``. Companion
# behavioral test (T2b) lives at
# ``tests/job_queue/test_seam_invariants.py:3413``
# (``test_reconciler_pattern_d_skips_alive_instance_with_terminal_job``)
# — it pins the behavior; this test pins the membership. Any new
# member must be added here AND in any DDL / docs in the same
# change.
ALIVE_INSTANCE_STATUSES: frozenset[str] = frozenset({
    "idle",
    "running",
    "paused",
    "queued",
    "waiting_children",
})


# ── Source-Validation Boundary (stability-backlog item 7, F2 pre-close) ────────

# Reserved source values for the ``source`` field of ``JobItem`` /
# ``MessageQueue``. These originate inside the daemon and are NOT
# forgeable by user-supplied HTTP bodies. Internal callers stamp them
# directly via ``manager.enqueue_message(source=...)`` /
# ``service.enqueue(source=...)``; the HTTP boundary rejects them with
# 422 so a frontend bug or hostile body cannot impersonate an internal
# dispatch lane (which would subvert the dispatch-source guard at
# ``daemon/services/instance_messaging.py:2280-2339`` — e.g. forging
# ``internal_report:<child>`` would route a user message through the
# original-source lookup path used for completion reports).
#
# Match semantics (see :func:`is_reserved_source` below):
#   * Colon-terminated members are matched by ``str.startswith`` so
#     ``internal_report:abc:msg`` is caught by ``"internal_report:"``.
#   * Non-colon members (``cascade_resume``, ``api_resume_fallback``)
#     are matched by exact equality — a custom user source
#     ``"cascade_resume_v2"`` must NOT be collateral-blocked because
#     it merely starts with the reserved string.
#
# Membership pinned by
# ``tests/unit/routers/test_source_reservation.py::
# TestReservedSourcePrefixesConstant`` — same fork-prevention shape as
# ``INJECTION_ELIGIBLE_STATUSES`` above. Single-home check:
# ``grep -rn --include="*.py" "RESERVED_SOURCE_PREFIXES" daemon/``
# MUST show exactly ONE assignment — the annotated declaration below
# (the ``: frozenset[str]`` annotation sits between the name and
# ``=``, so ``name\s*=\s*{`` patterns cannot match it). No
# per-consumer fork allowed.
#
# Provenance (where each value is stamped):
#   * ``"system:"``                       — infrastructure notices,
#                                          e.g. ``system:watchdog``
#                                          hang-notice (instance
#                                          messaging dispatch-source
#                                          guard:2284-2308).
#   * ``"internal_agent:"``               — agent-to-agent message lane
#                                          (``daemon/graph.py:2942``,
#                                          ``daemon/services/work_notifier.py:293``
#                                          job-event ping,
#                                          ``daemon/services/message_processing_pipeline.py:706``
#                                          for the special job-event
#                                          prefix).
#   * ``"internal_report:"``              — completion-report drain
#                                          (``daemon/graph.py:3169``,
#                                          ``daemon/services/child_reports.py:2744``,
#                                          ``daemon/repositories/report_injection/repository.py:633``).
#   * ``"internal_error_report:"``        — error-report drain
#                                          (``daemon/services/error_reporting.py:745``,
#                                          dedup key at :419).
#   * ``"internal_invoke_and_wait:"``     — invoke_agent_and_wait tool
#                                          (``daemon/utils.py:645``,
#                                          documented as non-user
#                                          origin in
#                                          ``daemon/tools/upgrade_journal.py:1070``).
#   * ``"internal_chart_reuse:"``         — generate_chart charter-reuse
#                                          enqueue (``daemon/tools/chart_tools.py``).
#   * ``"cascade_resume"``                — answer-gate cascade resume
#                                          (``daemon/manager.py:9285``,
#                                          ``daemon/services/watchover_service.py:676,722``).
#   * ``"api_resume_fallback"``           — messages router fallback
#                                          enqueue
#                                          (``daemon/routers/messages.py:282``).
#   * ``"explore:"``                      — kb-importer hand-off from the
#                                          explore tool
#                                          (``daemon/tools/knowledge_tools.py:323``).
#   * ``"experience:"``                   — kb-writer hand-off from the
#                                          experience tool
#                                          (``daemon/tools/knowledge_tools.py:389``).
#   * ``"blueprint-sidecar:"``            — blueprinter drift-signal
#                                          sidecar enqueue
#                                          (``daemon/tools/knowledge_tools.py:470``).
#   * ``"agent:"``                        — server-derived agent-caller
#                                          origin on the job tools
#                                          (``daemon/tools/job_queue.py:550``
#                                          and ``:1062``; the
#                                          empty-caller fallback is
#                                          ``internal_agent:unknown``,
#                                          itself covered above).
#   * ``"watchover_next_command"``        — terminal-activation
#                                          follow-up enqueue
#                                          (``daemon/services/watchover_service.py:1382``).
#   * ``"skill_metric_scan"``             — periodic skill-evolution
#                                          scan direct enqueue
#                                          (``daemon/manager.py:4024``).
#   * ``"skill_evolution"``               — skill_job_dispatcher stamp
#                                          (``SOURCE_TAG`` at
#                                          ``daemon/services/skill_job_dispatcher.py:76``,
#                                          stamped at ``:257``).
#   * ``"admin-endpoint"``                — blueprinter REST trigger
#                                          (helper default at
#                                          ``daemon/services/blueprint_job_helper.py:42``,
#                                          stamped by
#                                          ``daemon/routers/blueprints.py:300``).
#   * ``"auto-scan"``                     — blueprinter scan-service
#                                          trigger
#                                          (``daemon/services/blueprint_scan_service.py:316``).
#   * ``"scheduler"``                     — scheduler adapter trigger
#                                          enqueue — userless pure-daemon
#                                          identity, same trust class as
#                                          ``admin-endpoint`` /
#                                          ``auto-scan``
#                                          (``daemon/sources/adapters/scheduler.py:765``).
#
# Deliberately NOT in the set (legitimate user origins — an end-user
# identity rides the request; userless pure-daemon identities such as
# ``admin-endpoint`` / ``auto-scan`` / ``scheduler`` mint no user
# identity, are daemon-minted trust anchors, and are reserved above):
#   * ``"api"``                           — default + bare-api origin;
#                                          the full F2 P2.3 user-origin
#                                          question stays gated/out of
#                                          scope per the stability
#                                          backlog (item 7 preamble).
#   * ``"telegram:"`` / ``"webhook:"`` /
#     ``"whatsapp:"`` / ``"discord:"`` /
#     ``"slack:"``                        — channel adapters, all
#                                          stamped on the inbound
#                                          adapter side and reflected
#                                          back into ``JobItem.source``
#                                          by the source dispatcher.
#   * ``"dependency_bus"``                — FollowUp.source field default
#                                          (``daemon/services/dependency_bus.py:159``),
#                                          kept only as the legacy-payload
#                                          deserialization fallback
#                                          (``from_payload`` :210) — no active
#                                          mint site today; the only mint
#                                          (``daemon/tools/instance.py:538``)
#                                          stamps ``internal_agent:``, which IS
#                                          reserved above.
#   * Arbitrary custom strings from
#     integrated frontends / hooks. A user source value is a
#     free-form identifier — pinning the F2 user-origin whitelist is
#     a separate, deferred decision; this constant only pins the
#     reserved INTERNAL half so the boundary is enforceable today.
RESERVED_SOURCE_PREFIXES: frozenset[str] = frozenset({
    # Colon-terminated families (matched by ``startswith``).
    "system:",
    "internal_agent:",
    "internal_report:",
    "internal_error_report:",
    "internal_invoke_and_wait:",
    "internal_chart_reuse:",
    "explore:",
    "experience:",
    "agent:",
    "blueprint-sidecar:",
    # Non-colon exact values (matched by exact equality).
    "cascade_resume",
    "api_resume_fallback",
    "watchover_next_command",
    "skill_metric_scan",
    "skill_evolution",
    "admin-endpoint",
    "auto-scan",
    "scheduler",
})


def is_reserved_source(source: str | None) -> bool:
    """Return True if ``source`` matches a reserved internal origin.

    Match semantics:
      * Colon-terminated members of :data:`RESERVED_SOURCE_PREFIXES`
        are matched by ``str.startswith``.
      * Non-colon members are matched by exact equality.

    ``None`` and the empty string return False — the HTTP-boundary
    caller treats them as "no user-supplied value" and lets the
    underlying Pydantic default (``"api"``) fill in. Internal
    callers that pass ``None`` directly to
    ``manager.enqueue_message`` are unaffected (the helper is a
    boundary check, not a guard on the enqueue path itself).
    """
    if not isinstance(source, str) or not source:
        return False
    # Colon-terminated families — prefix-match.
    for reserved in RESERVED_SOURCE_PREFIXES:
        if reserved.endswith(":") and source.startswith(reserved):
            return True
    # Non-colon exact values — exact-match.
    if source in RESERVED_SOURCE_PREFIXES:
        return True
    return False


# Case-sensitivity invariant (deliberate — do NOT casefold this helper).
#   * Every stamp site mints the exact lowercase literals in
#     ``RESERVED_SOURCE_PREFIXES`` — no daemon path ever produces a
#     case-variant.
#   * The internal dispatch sinks match case-SENSITIVELY (bare
#     ``startswith`` / exact equality at
#     ``daemon/services/instance_messaging.py:2290-2299`` and
#     ``daemon/services/message_processing_pipeline.py:702-708``), so a
#     case-variant body value (e.g. ``"SYSTEM:watchdog"``) would NOT be
#     system-recognized as internal even if it reached a queue — it
#     flows as an inert free-form external source. Casefolding here
#     would claim a contract the sinks do not honor AND over-block
#     free-form user identifiers (e.g. ``"Agent:my-app"``) that are
#     harmless today.
#   * If any sink is ever changed to casefolded matching, update the
#     sink AND this helper together — they must agree exactly. The
#     case-sensitive behavior is pinned by
#     ``tests/unit/routers/test_source_reservation.py::
#     TestReservedSourcePrefixesConstant::
#     test_helper_is_deliberately_case_sensitive``.


def is_chat_source(source: str | None) -> bool:
    """Return True if ``source`` matches an interactive-chat origin.

    Match semantics: PREFIX match against
    :data:`CHAT_SOURCE_PREFIXES` (``"telegram:"``, ``"slack:"``,
    ``"discord:"``) — NOT exact-match. The mint site
    (``daemon/sources/registry.py:857``) always appends
    ``:<external_user_id>``, so concrete rows look like
    ``telegram:alice:1``; prefix semantics catch every concrete row.
    This mirrors the ``is_reserved_source`` colon-family shape and
    differs from it in one way: ``CHAT_SOURCE_PREFIXES`` carries NO
    non-colon exact members, so there is no exact-equality arm here.

    ``None`` and the empty string return False — same boundary
    contract as :func:`is_reserved_source` (the HTTP-boundary caller
    treats them as "no user-supplied value").

    Case-SENSITIVE (deliberate — do NOT casefold). Every mint site
    stamps the exact lowercase literals in
    :data:`CHAT_SOURCE_PREFIXES`; a case-variant body value (e.g.
    ``"TELEGRAM:foo"``) is NOT system-recognized as a chat origin —
    it flows as an inert free-form external source, same contract as
    the reserved-source helper (pinned by
    ``tests/unit/routers/test_source_reservation.py::
    TestChatSourcePrefixesConstant::
    test_helper_is_chat_source_deliberately_case_sensitive``).

    Asymmetry pin: ``webhook:`` / ``whatsapp:`` are deliberately
    EXCLUDED from the chat lane (CI/automation vs interactive chat) —
    ``is_chat_source("webhook:gh-hook")`` and
    ``is_chat_source("whatsapp:1234")`` return False. See the
    :data:`CHAT_SOURCE_PREFIXES` provenance comment above and
    ``USER_ORIGIN_CHAT_SOURCE_TYPES`` (``daemon/tools/upgrade_journal.py``,
    the registry-backed live-upgrade gate set) for the broader
    user-origin classification.
    """
    if not isinstance(source, str) or not source:
        return False
    return any(source.startswith(prefix) for prefix in CHAT_SOURCE_PREFIXES)


# Exhaustion-severity marker (monitoring-followups). Stamped as an
# exception attribute by the agent_node loud-ERROR handler
# (daemon/graph.py) on validation-family exceptions the moment they
# reach that handler — which for this family can only happen after the
# retry budget is burned (both classes are unconditional
# TRANSIENT_EXCEPTIONS members, the sole raise site
# (``validate_llm_response``) lives INSIDE the retry scope, and the
# ``Retrying(reraise=True)`` wrapper hands the original exception over
# only when the predicate refuses further attempts). Read by
# ``_classify_error_type`` (daemon/services/message_processing_errors.py)
# to mint the ``validation_error_exhausted`` lane, which
# ``CRITICAL_ERROR_TYPES`` (daemon/services/error_reporting.py)
# severity-maps to critical. Plain validation errors keep the legacy
# ``validation_error`` lane (severity warning).
RETRY_BUDGET_EXHAUSTED_MARKER: str = "retry_budget_exhausted"

# ── Report Integrity (wc-wake-report-integrity Wave 1) ────────────────────────
#
# S3 scoping note (council follow-up, 2026-08-30): the Wave-1
# observability instruments below — ``REPORT_SANITY_MARKER`` (the (c)
# passive marker), ``REPORT_INTEGRITY_JUNK_REPORT_TOTAL`` (the NR-3 junk
# counter), and the marker-append gate ``SANITY_FLAG_VERSION`` — are
# ALWAYS-ON observability. Their rollback seam is ``SANITY_FLAG_VERSION``
# alone; they are NOT gated by the Wave-2 B-guard kill-switch
# (``WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED``, defined in
# the next section). The B-guard flag controls ONLY (b) declared-waiting
# ENFORCEMENT (notice injection, B.S.1-iii / B.S.2). Operators reading
# the env table should not assume the B-guard flag suppresses (c)
# emission or NR-3 counting — it does not. Pinned by the
# TestMarkerCitationGateS1 class + the registry-completeness tests.

# (c) Passive report-sanity marker (C2-D2.9 LOCKED, DESCRIPTIVE-ONLY).
# Appended to TERMINAL completion reports whose source history shows zero
# tool-call evidence in a short history (see
# ``ChildReportsService._is_zero_tool_short_history``). Byte-for-byte
# pinned by ``tests/unit/services/test_child_reports.py::
# TestSanityConstantsRegistry`` — the directive half ("treat as interim,
# not completion") deliberately lives in (d) prompt guidance, NEVER here.
REPORT_SANITY_MARKER: str = "[REPORT SANITY: zero tool-call evidence in source history]"

# Separately-versioned rollback seam for the (c) marker (ruling S8; mirror
# ``LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED`` precedent). The marker appends
# only while this equals 1: bumping to 0 (or any future value, e.g. 2)
# SUPPRESSES the marker while leaving the code paths live — no revert
# needed if the marker ever breaks downstream consumers. Bump rationale
# belongs in CHANGELOG.md next to the code change.
SANITY_FLAG_VERSION: int = 1

# NR-3 junk-rate metric name (Prometheus-style). Counter is observability
# only — it never changes report content. Increment site:
# ``ChildReportsService._get_last_assistant_message_raw`` (BEFORE the
# skip_repair and report_repair.enabled short-circuits so ALL terminal
# completions count); sink seam: ``daemon/services/report_integrity_metrics``.
REPORT_INTEGRITY_JUNK_REPORT_TOTAL: str = "report_integrity_junk_report_total"

# NR-2 (C2-D2.15 LOCKED): the ONE source of truth for agent IDs whose
# completion reports are exempt from report repair AND from the (c) sanity
# marker. ``ReportRepairConfig.repair_excluded_agents`` derives its default
# from this frozenset; the documented env override
# (``REPORT_REPAIR_EXCLUDED_AGENTS``, comma-separated — REPLACES the set)
# remains the per-deployment escape hatch.
#
# Members are text-only-by-design agents whose short, zero-tool-call
# reports are legitimate work products, NOT silent-death evidence:
#   * ``wanderer`` / ``explorer`` — exploration agents; naturally concise
#     final reports (original 2026-08-11 spec).
#   * ``watcher`` — included at NR-2 landing (evidence-based call):
#     ``agents/watcher/meta.json`` has ``tools.allow: []`` — it STRUCTURALLY
#     cannot emit tool_calls, so the zero-evidence predicate would match
#     100% of its reports; its verdict output is text-only by design.
#     (Today no daemon code path spawns watcher instances — the watchover
#     flow invokes it as an inline single-call LLM evaluator — so this
#     entry is defensive; it becomes load-bearing the moment a watcher
#     instance rides the report path.) Third text-only-by-design member ⇒
#     D2.15/OQ-1 re-open trigger (generic per-agent opt-out) is now MET —
#     flagged to the component owner, NOT built here.
REPORT_REPAIR_EXCLUDED_AGENTS: frozenset[str] = frozenset(
    {"wanderer", "explorer", "watcher"}
)


# ── wc-wake-report-integrity (Wave 2 — B.S.8 PARTIAL) ────────────────────────
#
# Kill-switch registry entries for the Wave-2 report-integrity gates.
# Both env names are RESERVED at this commit (B.S.1-i PREDICATE-FUNCTION-ONLY).
# The env bindings (config.py field + ``_resolve_*_enabled()`` helper,
# mirroring the ``LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED`` precedent at
# ``daemon/repositories/instance/repository.py:65`` and the
# ``ENSEMBLE_WC_WAKE_ENQUEUE`` precedent for C1-Q2) land at stage iii
# (B.S.1-iii / B.S.8 registry test) — that commit also asserts the
# registry test for BOTH env names, including the reserved-unused (a).
#
# Decisions: C2-D2.5 (b) ships in this component; C2-D2.5-FLIP
# (leader-confirmed 2026-08-30) commits to the enforcement flip
# after ≤2-week soak or immediately on any silent-death incident;
# C2-D2.2 ((a) does NOT land initially; WITHDRAWN — subsumed by (c)).
#
# ────────────────────────────────────────────────────────────────────────────

# (b) Terminal-child-aware waiting guard (decisions.md C2-D2.5 LOCKED,
# C2-D2.6 LOCKED, C2-D2.7 LOCKED, C2-D2.8 LOCKED, C2-D2.18 LOCKED).
# Wave-2 stage-iii (B.S.1-iii) wires the config.py env binding; the
# default-0 semantics here match the ``LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED``
# precedent (kill-switch default OFF — the revert path; pre-committed
# enforcement flip per D2.5-FLIP).
#
# S3 scoping note (council follow-up, 2026-08-30): this flag gates ONLY
# (b) ENFORCEMENT (notice injection via B.S.1-iii / B.S.2). It does NOT
# gate (c) marker emission, the NR-3 junk counter, or any other Wave-1
# observability instrument — their rollback seam is ``SANITY_FLAG_VERSION``
# (defined above), not this env. Operators flipping this flag should
# expect zero change in (c) marker / NR-3 counter behavior.
WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED: str = (
    "WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED"
)

# (a) Premature-first-turn guard — RESERVED-UNUSED (decisions.md C2-D2.2
# LOCKED — (a) does NOT land initially; (a2) WITHDRAWN — subsumed by (c);
# C2-D2.3 LOCKED — default OFF behind kill-switch; C2-D2.4 LOCKED —
# first-turn only if ever activated). The env name is reserved so
# future escalation has a single canonical home and the B.S.8 registry
# test at stage iii covers BOTH names. NO config binding lands at this
# commit — the name is a forward declaration only.
WC_REPORT_INTEGRITY_A_PREMATURE_TURN_GUARD_ENABLED: str = (
    "WC_REPORT_INTEGRITY_A_PREMATURE_TURN_GUARD_ENABLED"
)


# ── kv-ambient-awareness-fix (C0 prereq / C2 binding — B.S.8) ────────────────
#
# Kill-switch registry entries for the ambient shared_meta_kv fixes.
# Binding state per name:
#
#   * ``ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED`` — BOUND at C2
#     (fix(D3): standalone KV host + system-default un-suppression).
#     Shape A binding (pydantic ``ContextMessagesConfig`` field +
#     ``_resolve_kv_ambient_from_sources`` in ``config.py`` +
#     resolved-once cache behind ``_resolve_kv_ambient_system_default_enabled``,
#     mirroring the ``ENSEMBLE_PROACTIVE_COMPACTION`` precedent); the
#     runtime gate consumes the name via this constant — no literal
#     env-name fork (B.S.8 registry discipline; the wiring pins live
#     beside the flag's tests). Default ON; ``=0`` + restart restores
#     the legacy suppression (decisions.md D8).
#   * ``ENSEMBLE_AMBIENT_KV_FRESH`` — BOUND at C3
#     fix(D1): split block + per-turn refresh). Shape B (service-module
#     cached resolver + boot INFO, mirroring the
#     ``ENSEMBLE_WC_WAKE_ENQUEUE`` precedent). The literal must not
#     appear anywhere else in ``daemon/`` until that commit lands.
#
# A third historical name, ``ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT``,
# is deliberately NOT reserved: its defect (spawned-child mispartition)
# is FIXED AT BASE by 80bb61dd and the flag-wrap was adjudicated
# negative-value (decisions.md D12) — a reserved-unused entry would
# contradict B.S.8's own rationale.
#
# ────────────────────────────────────────────────────────────────────────────

ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED: str = (
    "ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED"
)

# RESERVED at C2 — the C3 binding (Shape B, per-turn refresh resolver)
# lands with fix(D1). Until then this name is bound to nothing: no
# config field, no resolver, no read site outside this registry block.
ENSEMBLE_AMBIENT_KV_FRESH: str = "ENSEMBLE_AMBIENT_KV_FRESH"

# ENSEMBLE_VSCODE_WEBVIEW_CSP_FIX — BOUND at fix-vscode-image-preview
# (Step 1 of the two-step plan: make webview resources — image
# preview — load through the daemon's /vscode proxy). Shape A binding
# (config.py resolver + resolved-once cache behind
# ``_resolve_vscode_webview_csp_fix``, mirroring the
# ``ENSEMBLE_PROACTIVE_COMPACTION`` / ``ENSEMBLE_KV_AMBIENT_*`` family).
# Default ON; ``=0`` + restart restores the exact pre-fix blocking
# behavior (the meta-CSP stays strict, virtual-host assets are blocked
# at the browser level — useful as an incident-revert path).
# The literal must not appear anywhere else in ``daemon/`` until this
# binding lands — B.S.8 registry discipline, same as the KV-ambient
# names above.
ENSEMBLE_VSCODE_WEBVIEW_CSP_FIX: str = "ENSEMBLE_VSCODE_WEBVIEW_CSP_FIX"
