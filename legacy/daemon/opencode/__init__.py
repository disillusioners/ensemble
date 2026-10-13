"""OpenCode integration for the ensemble daemon.

Direct Python port of the ``opencode_skill`` Go binary — a stateful
session manager that talks to a local OpenCode HTTP server and exposes
PROMPT/COMMAND/ANSWER/RESUME/INIT_SESSION/ABORT_SESSION actions.

Public API:

- ``OpenCodeClient`` (from ``.client``) — async HTTP transport + Pydantic
  DTOs with camelCase aliases.
- ``OpenCodeSessionManager`` (from ``.session_manager``) — per-session
  state machine with optimistic BUSY handling and message-based state
  derivation.
- ``OpenCodeSessionRegistry`` (from ``.registry``) — owns the live
  managers and the SQLite-backed repository.
- ``OpenCodeSessionRepository`` (from ``.repository``) — CRUD for the
  ``opencode_sessions`` table.
- ``external_opencode_send_message`` (from ``.server``) — the dispatch
  function used by the ensemble HTTP router.
"""

from __future__ import annotations

from .client import (
    AnswerRequest,
    CommandRequest,
    ModelDetails,
    OpenCodeAPIError,
    OpenCodeClient,
    Part,
    PromptRequest,
    Question,
    QuestionItem,
    Option,
    SessionResponse,
)
from .constants import (
    ABORT_REMOTE_SETTLE_S,
    DEFAULT_AGENT,
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_PROVIDER_ID,
    OPENCODE_URL,
    POLL_INTERVAL_S,
    RESUME_AGENT,
    RESUME_TEXT,
    SPECIAL_PROMPTS,
    START_WORK_AGENT,
)
from .registry import OpenCodeSessionRegistry
from .repository import (
    OpenCodeSessionRecord,
    OpenCodeSessionRepository,
    create_opencode_session_repository,
)
from .server import (
    OpenCodeRequest,
    OpenCodeResponse,
    external_opencode_send_message,
)
from .session_manager import (
    IDLE,
    BUSY,
    WAITING_FOR_INPUT,
    OpenCodeSessionManager,
    PersistedState,
    Request as ManagerRequest,
    SessionState,
    STATE_IDLE,
    STATE_BUSY,
    STATE_WAITING_FOR_INPUT,
)
from .state import (
    _derive_state_from_finish,
    get_message_finish,
    has_message_error,
    strip_message_bloat,
)

__all__ = [
    # Client / DTOs
    "OpenCodeClient",
    "OpenCodeAPIError",
    "PromptRequest",
    "CommandRequest",
    "AnswerRequest",
    "Part",
    "ModelDetails",
    "Question",
    "QuestionItem",
    "Option",
    "SessionResponse",
    # State
    "SessionState",
    "STATE_IDLE",
    "STATE_BUSY",
    "STATE_WAITING_FOR_INPUT",
    "IDLE",
    "BUSY",
    "WAITING_FOR_INPUT",
    "has_message_error",
    "strip_message_bloat",
    "get_message_finish",
    "_derive_state_from_finish",
    # Session manager
    "OpenCodeSessionManager",
    "PersistedState",
    "ManagerRequest",
    # Registry
    "OpenCodeSessionRegistry",
    # Repository
    "OpenCodeSessionRepository",
    "OpenCodeSessionRecord",
    "create_opencode_session_repository",
    # Server entry
    "OpenCodeRequest",
    "OpenCodeResponse",
    "external_opencode_send_message",
    # Constants
    "OPENCODE_URL",
    "POLL_INTERVAL_S",
    "RESUME_AGENT",
    "RESUME_TEXT",
    "DEFAULT_AGENT",
    "DEFAULT_MODEL_ID",
    "DEFAULT_MODEL_PROVIDER_ID",
    "SPECIAL_PROMPTS",
    "START_WORK_AGENT",
    "ABORT_REMOTE_SETTLE_S",
]
