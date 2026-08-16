import logging
import os
import signal
import sys
import warnings

logger = logging.getLogger(__name__)

# Suppress langchain Pydantic V1 compatibility warning on Python 3.14+
# This is safe: langchain still works, just uses deprecated Pydantic V1 shim
warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality isn't compatible with Python 3.14",
    category=UserWarning,
)

import uvicorn
from pathlib import Path

from .config import load_config

# ── Process exit-code contract (Auto-Restart ADR-010/011) ─────────────────
# The launcher (Phase 1) maps exit codes to restart policy:
#
#   0   = clean stop. Supervisor may restart per its own at-boot policy.
#   75  = boot-time temporary failure (EX_TEMPFAIL): PostgreSQL was
#         unreachable at boot within the BOOT_DB_TIMEOUT_S budget. The
#         launcher retries with capped backoff WITHOUT decrementing the
#         burst budget (ADR-011) — a PG outage never permanently downs
#         the daemon. LIVE as of Phase 1 (see _boot_db_preflight).
#   78  = configuration/schema refusal: boot found the database newer
#         than the binary can safely run, or fatal config. The
#         supervisor must NOT loop on 78. DEFERRED to Phase 5
#         (migration guard + daemon_meta) — deliberately NOT live yet.
#   1   = crash (unhandled exception). Restart with backoff. The
#         default crash path; no explicit code needed.
# ───────────────────────────────────────────────────────────────────────────

# Exit code for boot-time PG unreachability (EX_TEMPFAIL — see block above).
_EXIT_BOOT_DB_TEMPFAIL = 75


def _boot_db_preflight() -> None:
    """Boot preflight: PostgreSQL reachability check (Phase 1, ADR-011).

    Replicates the lifespan's minimal ensemble.json read
    (ENSEMBLE_DATA_DIR > DATA_DIR > ./data precedence — see
    ``daemon/api.py`` lifespan) to decide the database backend BEFORE
    uvicorn starts. When the resolved backend is PostgreSQL, runs
    ``SELECT 1`` with a ``BOOT_DB_TIMEOUT_S`` budget:

    * Connectivity failure → log an operator-facing message and
      ``sys.exit(75)``. The launcher retries with capped backoff
      without consuming the burst budget. No in-process retry loop
      (plan m5) — one check, one verdict.
    * SQLite backend → check skipped entirely.
    * Any UNEXPECTED exception during the check itself (config read
      failure, import error, …) → fail-open: log and proceed. Only
      definite PG unreachability blocks boot (fail-closed); a broken
      check must not take the daemon down.
    """
    try:
        from .ensemble_config import EnsembleConfig

        data_dir = Path(
            os.environ.get("ENSEMBLE_DATA_DIR")
            or os.environ.get("DATA_DIR")
            or "./data"
        )
        ensemble_config = EnsembleConfig.load_or_create(data_dir)
        if not ensemble_config.is_postgres:
            logger.debug("Boot preflight: SQLite backend — PG check skipped")
            return

        from .repositories.factory import create_postgres_engine
        from .constants import BOOT_DB_TIMEOUT_S
        from sqlalchemy import text

        logger.info(
            f"Boot preflight: PostgreSQL backend selected — probing "
            f"connectivity (timeout {BOOT_DB_TIMEOUT_S}s)"
        )
        engine = create_postgres_engine(ensemble_config)
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        finally:
            engine.dispose()
        logger.info("Boot preflight: PostgreSQL reachable — proceeding")
    except SystemExit:
        raise
    except Exception as exc:
        # Fail-closed ONLY on definite PG unreachability. SQLAlchemy
        # wraps connection refusals/timeouts in OperationalError /
        # DBAPIError — anything else here means the check itself is
        # broken (bad config file, import failure) and must not block
        # boot: log and proceed.
        from sqlalchemy.exc import OperationalError, DBAPIError

        if isinstance(exc, (OperationalError, DBAPIError)):
            logger.error(
                "PostgreSQL unreachable at boot (timeout/conn refused): %s\n"
                "  → exiting 75 EX_TEMPFAIL; launcher will retry with "
                "capped backoff (burst budget untouched, ADR-011)",
                exc,
            )
            sys.exit(_EXIT_BOOT_DB_TEMPFAIL)
        logger.warning(
            "Boot preflight: unexpected error during PG check "
            "(fail-open, proceeding): %s",
            exc,
            exc_info=True,
        )


def main():
    """Main entry point."""
    # Load config to get host/port
    config = load_config()

    # Apply LLM-specific class-level config that must be set before any
    # ThinkingChatOpenAI instance is created.
    from .graph import ThinkingChatOpenAI
    ThinkingChatOpenAI.reasoning_echo_models = list(
        config.llm.reasoning_echo_models or []
    )
    logger.info(
        f"[Config] reasoning_echo_models={ThinkingChatOpenAI.reasoning_echo_models} "
        f"(echo reasoning_content back in multi-turn for matching model names)"
    )

    # Log version for debugging
    from . import __version__
    logger.info(f"Starting Ensemble v{__version__}")

    # Boot DB preflight (Auto-Restart Phase 1, ADR-011): exit 75 on
    # definite PG unreachability BEFORE binding the port or starting
    # uvicorn. Cheap (single SELECT 1) and fail-open on check errors.
    _boot_db_preflight()

    # Note: uvicorn handles SIGTERM and SIGINT automatically.
    # The FastAPI lifespan shutdown (via @asynccontextmanager) will be
    # triggered when uvicorn shuts down, which calls manager.shutdown()
    # for graceful cleanup.

    # Run server (access_log=False: custom SelectiveAccessLogMiddleware handles selective logging)
    #
    # timeout_graceful_shutdown (Auto-Restart Phase 1, fixes C1): bounds
    # SIGTERM shutdown so a hung lifespan teardown can no longer hang
    # forever. The dead SHUTDOWN_TIMEOUT_S constants ceiling (300s) is
    # superseded by this configurable field (default 60s; env
    # DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS). Raising it trades
    # shutdown latency for drain completeness.
    uvicorn.run(
        "daemon.api:app",
        host=config.daemon.host,
        port=config.daemon.port,
        reload=False,
        access_log=False,
        timeout_graceful_shutdown=config.daemon.graceful_shutdown_timeout_seconds,
    )


if __name__ == "__main__":
    main()
