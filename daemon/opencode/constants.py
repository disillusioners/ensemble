"""OpenCode integration configuration constants.

Ported from ``.inspiration-projects/opencode_skill_src/internal/config/config.go``.

This module defines runtime configuration values for the OpenCode API client
and the in-process session manager state machine. Values mirror the Go
binary's defaults; override per-process via environment variables when needed.
"""

from __future__ import annotations

from datetime import datetime, timezone

# ── OpenCode HTTP API ──────────────────────────────────────────────────────────
OPENCODE_URL: str = "http://127.0.0.1:4095"  # Go: config.OpenCodeURL
"""Base URL of the local OpenCode HTTP server. The wrapper always talks to the
loopback address — the server is expected to run on the same host as the
ensemble daemon."""

OPENCODE_HTTP_TIMEOUT_S: int = 3600  # Go: httpClient.Timeout = 1 * time.Hour
"""HTTP request timeout in seconds. The Go binary hardcodes 1 hour because
long-running prompts stream for tens of minutes."""

# ── Defaults for new sessions / agents ─────────────────────────────────────────
DEFAULT_AGENT: str = "orchestrator"  # Go: config.DefaultAgent
"""Default agent name when callers do not specify one."""

DEFAULT_MODEL_PROVIDER_ID: str = "litellm"  # Go: types.ModelDetails{ProviderID:"litellm"}
DEFAULT_MODEL_ID: str = "coding"  # Go: types.ModelDetails{ModelID:"coding"}
"""``litellm/coding`` is the default model used by ``resume`` and ``/start-work``."""

DEFAULT_API_USER: str = "opencode"  # Go: config.DefaultAPIUser
DEFAULT_API_KEY: str = "opencode"  # Go: config.DefaultAPIKey
"""Basic Auth credentials. The Go binary falls back to ``opencode:opencode``
when ``~/.opencode_skill/config.json`` is missing."""

# ── State machine ──────────────────────────────────────────────────────────────
POLL_INTERVAL_S: int = 30  # Go: config.PollInterval = 30 * time.Second
"""Interval for the question-polling background task. Detects interactive
prompts that are not surfaced through the worker completion path."""

IDLE_HEARTBEAT_S: int = 300  # 5 minutes — used when session is IDLE+not busy
"""Long heartbeat interval for idle sessions. Idle sessions don't need
aggressive 30s polling; this longer interval detects unexpected state
changes (e.g. external session activity) without burning CPU/IO. Matches
the spirit of the Go binary's ticker but skips the wake-up cost when the
session has no work to do. See ``session_manager._run_loop``."""

# ── Session manager queues ─────────────────────────────────────────────────────
INPUT_QUEUE_SIZE: int = 10  # Go: make(chan Request, 10)
"""Buffered size of the manager's input request queue. Mirrors the Go binary
exactly so back-pressure semantics are preserved."""

WORKER_DONE_QUEUE_SIZE: int = 1  # Go: make(chan workerResult, 1)
"""Bounded to 1. A new worker that completes while a previous result is
sitting on the channel will replace the older one — the manager only needs
the *latest* result."""

# ── Server-side special behavior ──────────────────────────────────────────────
SPECIAL_PROMPTS: frozenset[str] = frozenset({"start-work", "continue", "abort", "retry"})
"""Prompts that bypass the BUSY-state rejection. The full set comes from
``server.go`` lines 453-457:

    normalizedText == "start-work" || "continue" || "abort" || "retry"
"""

START_WORK_AGENT: str = "atlas"
"""When ``/start-work`` is sent, the agent is locked to this name. Mirrors
``registry.UpdateAgentState(project, sessionName, "atlas", true)`` in
``server.go`` lines 437-444."""

# ── Agent override on resume / continue / retry ────────────────────────────────
RESUME_AGENT: str = DEFAULT_AGENT
RESUME_TEXT: str = "continue"
"""Hardcoded resume prompt body — agent=orchestrator, model=litellm/coding,
text="resume". See ``manager.go`` lines 465-470."""

# ── Abort timing ───────────────────────────────────────────────────────────────
ABORT_REMOTE_SETTLE_S: float = 3.0  # Go: server.go:359 time.Sleep(3 * time.Second)
"""Delay between a successful remote abort and the local state reset. Gives
the OpenCode server time to mark the session as aborted before we clobber
local state."""

# ── Path conventions (parity with Go) ──────────────────────────────────────────
LEGACY_WRAPPER_DIR: str = "~/.opencode_skill/"
"""Matches ``config.WrapperDir`` in the Go binary. Used only for fallback
credential resolution; ensemble's primary storage lives under
``<data_dir>/opencode_sessions``."""

# ── Prompt injection hints ────────────────────────────────────────────────────
COUNCIL_HINT: str = (
    "\n\nNeed to use @council subagent-tool when investigating/reviewing "
    "critical paths (e.g., high-complexity logic, important decisions, "
    "breaking changes, architecture-related work)."
)
"""Trailer appended to outbound prompts when ``council=True`` is passed to
``external_opencode_send_message``. Mirrors ``config.CouncilHint`` in the
Go binary (main.go:460-462)."""


# ── Helpers ────────────────────────────────────────────────────────────────────


def _now_rfc3339() -> str:
    """Return current UTC time in RFC3339 — matches ``time.RFC3339`` in Go.

    Used for ``last_activity`` columns. The Go binary writes
    ``sm.lastActivity.Format(time.RFC3339)`` (manager.go:125).
    """
    return datetime.now(timezone.utc).isoformat()
