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

from .config import load_config, warn_deprecated_reasoning_echo_env

# ── Process exit-code contract (Auto-Restart ADR-010/011) ─────────────────
# The launcher (Phase 1) maps exit codes to restart policy:
#
#   0   = clean stop. Supervisor may restart per its own at-boot policy.
#   75  = boot-time temporary failure (EX_TEMPFAIL): PostgreSQL was
#         unreachable at boot within the BOOT_DB_TIMEOUT_S budget. The
#         launcher retries with capped backoff WITHOUT decrementing the
#         burst budget (ADR-011) — a PG outage never permanently downs
#         the daemon. LIVE as of Phase 1 (see _boot_db_preflight).
#   78  = configuration refusal: credentials rejected by PostgreSQL
#         (SQLSTATE 28P01/28000/28P02 — bad POSTGRES_PASSWORD, wrong
#         user, or pg_hba rejection) or, in Phase 5, database newer
#         than the binary can safely run. The supervisor must NOT
#         loop on 78. The auth-refusal half is LIVE as of Phase 1
#         (see _boot_db_preflight); the schema/version guard remains
#         DEFERRED to Phase 5 (migration guard + daemon_meta).
#   1   = crash (unhandled exception). Restart with backoff. The
#         default crash path; no explicit code needed.
# ───────────────────────────────────────────────────────────────────────────

# Exit code for boot-time PG unreachability (EX_TEMPFAIL — see block above).
_EXIT_BOOT_DB_TEMPFAIL = 75

# Exit code for boot-time PG auth refusal (EX_CONFIG — see block above):
# wrong credentials can never be fixed by retrying, so the launcher
# must stop instead of looping on the budget-exempt 75 track.
_EXIT_BOOT_DB_AUTH_REFUSED = 78

# libpq SQLSTATE codes for permanent authentication/authorization
# failure. 28P01 = invalid_password, 28000 =
# invalid_authorization_specification (role missing / pg_hba reject).
# 28P02 is included defensively per code review — not a stock
# PostgreSQL code, but harmless to treat as permanent if a proxy or
# gateway emits it.
_AUTH_FAILURE_SQLSTATES = frozenset({"28P01", "28000", "28P02"})


def _preflight_pg_url(ensemble_config) -> str:
    """Build the boot-preflight engine URL.

    Mirrors the URL construction inside
    ``daemon/repositories/factory.py::create_postgres_engine``
    (``POSTGRES_*`` env overrides over ``ensemble.json`` values) so
    the probe targets EXACTLY the database the daemon would use.
    Duplicated here because the preflight engine needs a
    ``connect_timeout`` the shared factory deliberately does not
    set — keep the two in sync if the factory's URL construction
    changes.
    """
    import os

    pg = ensemble_config.postgres
    host = os.environ.get("POSTGRES_HOST", pg.host)
    port = os.environ.get("POSTGRES_PORT", str(pg.port))
    db = os.environ.get("POSTGRES_DB", pg.db)
    user = os.environ.get("POSTGRES_USER", pg.user)
    password = os.environ.get("POSTGRES_PASSWORD", pg.password)
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


def _pg_sqlstate(exc: BaseException) -> "str | None":
    """Best-effort SQLSTATE extraction from a SQLAlchemy-wrapped DBAPI error.

    SQLAlchemy wraps the driver error as ``exc.orig``; psycopg exposes
    the SQLSTATE as ``orig.sqlstate`` (with ``orig.diag.sqlstate`` as
    an alternate layout). Drivers disagree, so every access is
    defensive — a missing/non-string code returns None, which callers
    classify as transient (fail toward the retryable track).
    """
    orig = getattr(exc, "orig", None)
    if orig is None:
        return None
    code = getattr(orig, "sqlstate", None)
    if isinstance(code, str):
        return code
    diag = getattr(orig, "diag", None)
    code = getattr(diag, "sqlstate", None)
    if isinstance(code, str):
        return code
    return None


def _boot_db_preflight() -> None:
    """Boot preflight: PostgreSQL reachability check (Phase 1, ADR-011).

    Replicates the lifespan's minimal ensemble.json read
    (ENSEMBLE_DATA_DIR > DATA_DIR > ./data precedence — see
    ``daemon/api.py`` lifespan) to decide the database backend BEFORE
    uvicorn starts. When the resolved backend is PostgreSQL, runs
    ``SELECT 1`` on a LOCALLY constructed engine whose libpq
    ``connect_timeout`` is pinned to ``BOOT_DB_TIMEOUT_S`` — the
    shared factory engine carries no driver timeout and would hang
    ~75-130s on a firewall-DROP host:

    * Auth refusal (SQLSTATE 28P01/28000/28P02) → log an
      operator-facing credential hint and ``sys.exit(78)``:
      retrying cannot fix wrong credentials, so the launcher must
      stop rather than burn the budget-exempt 75 track forever.
    * Other definite unreachability (refused/timeout/unreachable,
      including a raw ``ConnectionError`` escaping SQLAlchemy's
      wrapper) → ``sys.exit(75)``. The launcher retries with capped
      backoff without consuming the burst budget. No in-process
      retry loop (plan m5) — one check, one verdict.
    * SQLite backend → check skipped entirely.
    * Any UNEXPECTED exception during the check itself (config read
      failure, import error, …) → fail-open: log and proceed. Only
      definite PG unreachability/auth refusal blocks boot
      (fail-closed); a broken check must not take the daemon down.
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

        from .constants import BOOT_DB_TIMEOUT_S
        from sqlalchemy import create_engine, text

        logger.info(
            f"Boot preflight: PostgreSQL backend selected — probing "
            f"connectivity (timeout {BOOT_DB_TIMEOUT_S}s)"
        )
        # The preflight engine is constructed LOCALLY (not via the
        # shared create_postgres_engine factory) for exactly one
        # reason: it carries a libpq ``connect_timeout`` so a
        # firewall-DROP host fails inside the BOOT_DB_TIMEOUT_S
        # budget instead of at the OS TCP timeout (~75-130s). The
        # driver bound is the real enforcement — an asyncio.wait_for
        # wrapper CANNOT bound a sync connect (measured on Py3.13:
        # the executor shutdown joins the hung worker thread, so the
        # timeout surfaces only after the thread finally completes).
        # One-shot probe: no pool tuning, dispose immediately after.
        engine = create_engine(
            _preflight_pg_url(ensemble_config),
            connect_args={"connect_timeout": BOOT_DB_TIMEOUT_S},
        )
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        finally:
            engine.dispose()
        logger.info("Boot preflight: PostgreSQL reachable — proceeding")
    except SystemExit:
        raise
    except Exception as exc:
        # Fail-closed ONLY on definite PG unreachability or auth
        # refusal. SQLAlchemy wraps connection refusals/timeouts in
        # OperationalError / DBAPIError (a raw ConnectionError gets
        # the same verdict) — anything else here means the check
        # itself is broken (bad config file, import failure) and must
        # not block boot: log and proceed.
        from sqlalchemy.exc import OperationalError, DBAPIError

        if isinstance(exc, (OperationalError, DBAPIError, ConnectionError)):
            sqlstate = _pg_sqlstate(exc)
            if sqlstate in _AUTH_FAILURE_SQLSTATES:
                logger.error(
                    "PostgreSQL authentication refused at boot "
                    "(SQLSTATE %s): %s\n"
                    "  → permanent auth failure — check POSTGRES_* "
                    "credentials (POSTGRES_USER / POSTGRES_PASSWORD / "
                    "POSTGRES_DB / pg_hba.conf)\n"
                    "  → exiting 78; launcher must NOT retry — "
                    "retrying cannot fix credentials",
                    sqlstate,
                    exc,
                )
                sys.exit(_EXIT_BOOT_DB_AUTH_REFUSED)
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


def main(run_preflight: bool = True):
    """Main entry point.

    ``run_preflight=False`` is used ONLY by the frozen entry
    (``run_app.py``), which runs ``_boot_db_preflight()`` itself —
    earlier, before this function's config steps — so the probe fires
    exactly once per boot on every entry (F-DR1-1, P2.3 B5.6).
    """
    # Load config to get host/port
    config = load_config()

    # Apply LLM-specific class-level config that must be set before any
    # ThinkingChatOpenAI instance is created.
    from .graph import ThinkingChatOpenAI
    ThinkingChatOpenAI.reasoning_echo_disabled_models = list(
        config.llm.reasoning_echo_disabled_models or []
    )
    logger.info(
        f"[Config] reasoning_echo_disabled_models={ThinkingChatOpenAI.reasoning_echo_disabled_models} "
        f"(models matching these patterns will NOT echo reasoning_content; all others echo)"
    )

    # Warn-once if the removed allowlist env var is still set (no-op when
    # load_config already emitted it)
    warn_deprecated_reasoning_echo_env()

    # Log version for debugging
    from . import __version__
    logger.info(f"Starting Ensemble v{__version__}")

    # Boot DB preflight (Auto-Restart Phase 1, ADR-011): exit 75 on
    # definite PG unreachability BEFORE binding the port or starting
    # uvicorn. Cheap (single SELECT 1) and fail-open on check errors.
    # Skipped when the caller (run_app.py frozen entry) already ran it.
    if run_preflight:
        _boot_db_preflight()

    # Note: uvicorn handles SIGTERM and SIGINT automatically.
    # The FastAPI lifespan shutdown (via @asynccontextmanager) will be
    # triggered when uvicorn shuts down, which calls manager.shutdown()
    # for graceful cleanup.

    # Run server (access_log=False: custom SelectiveAccessLogMiddleware handles selective logging)
    #
    # timeout_graceful_shutdown (Auto-Restart Phase 1, fixes C1): bounds
    # the TASK-DRAIN phase of SIGTERM shutdown only. SCOPE (uvicorn
    # 0.41.0): Server._serve() wraps timeout_graceful_shutdown around
    # _wait_tasks_to_complete() — i.e. in-flight connections/requests.
    # The lifespan shutdown (lifespan.shutdown(), all 9 steps of
    # manager.shutdown() in daemon/api.py) is invoked AFTER that and is
    # NOT bounded by this value. The real hard bound on TOTAL shutdown
    # time is therefore the launcher's SIGKILL (launcher.sh
    # CHILD_STOP_WAIT_S, default 70s = 60s graceful + 10s margin;
    # scripts/stop-ensemble.sh WAIT_S mirrors it). Per-step
    # asyncio.wait_for budgets inside manager.shutdown() are deferred
    # hardening (pre-Phase-3). Raising the value trades task-drain
    # latency for drain completeness.
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
